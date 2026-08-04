import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:http/http.dart' as http;

import 'api_service.dart';
import 'local_storage_service.dart';
import '../models/book.dart';

class AccountUser {
  final String id;
  final String email;
  final String displayName;
  final bool emailVerified;
  final bool googleLinked;

  const AccountUser({
    required this.id,
    required this.email,
    required this.displayName,
    required this.emailVerified,
    required this.googleLinked,
  });

  factory AccountUser.fromJson(Map<String, dynamic> json) => AccountUser(
    id: json['id'] as String? ?? '',
    email: json['email'] as String? ?? '',
    displayName: json['display_name'] as String? ?? '读者',
    emailVerified: json['email_verified'] as bool? ?? false,
    googleLinked: json['google_linked'] as bool? ?? false,
  );
}

class AccountException implements Exception {
  final String message;
  const AccountException(this.message);
  @override
  String toString() => message;
}

class AccountService extends ChangeNotifier {
  AccountService._();
  static final instance = AccountService._();

  static const _tokenKey = 'oohstory_account_token';
  static const _storage = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
    iOptions: IOSOptions(
      accessibility: KeychainAccessibility.first_unlock_this_device,
    ),
  );
  // OAuth client IDs are public identifiers. Keep the production Web client
  // as a safe default so release builds cannot silently lose Google Sign-In;
  // CI or alternate environments may still override it with --dart-define.
  static const _webClientId = String.fromEnvironment(
    'GOOGLE_WEB_CLIENT_ID',
    defaultValue:
        '1046473401516-7iohcfjigv2ufopiimo3a2ihiepr0dbh.apps.googleusercontent.com',
  );
  static const _iosClientId = String.fromEnvironment('GOOGLE_IOS_CLIENT_ID');
  static const _requestTimeout = Duration(seconds: 20);
  static const _uploadTimeout = Duration(minutes: 4);

  final http.Client _client = http.Client();
  String? _token;
  AccountUser? user;
  bool initialized = false;
  Map<String, dynamic> cloudState = const {
    'history': <dynamic>[],
    'favorites': <dynamic>[],
    'bookshelf': <dynamic>[],
  };

  bool get isSignedIn => user != null && _token != null;
  bool get googleAvailable =>
      _webClientId.isNotEmpty && (!Platform.isIOS || _iosClientId.isNotEmpty);

  String get platformClient {
    if (Platform.isIOS) return 'ios';
    return 'android';
  }

  Future<Map<String, dynamic>> _request(
    String path, {
    String method = 'GET',
    Map<String, dynamic>? body,
  }) async {
    final uri = Uri.parse('${ApiService.baseUrl}$path');
    final headers = <String, String>{'Accept': 'application/json'};
    if (_token != null) headers['Authorization'] = 'Bearer $_token';
    if (body != null) headers['Content-Type'] = 'application/json';
    late final http.Response response;
    final encoded = body == null ? null : jsonEncode(body);
    if (method == 'POST') {
      response = await _client
          .post(uri, headers: headers, body: encoded)
          .timeout(_requestTimeout);
    } else if (method == 'PUT') {
      response = await _client
          .put(uri, headers: headers, body: encoded)
          .timeout(_requestTimeout);
    } else if (method == 'DELETE') {
      response = await _client
          .delete(uri, headers: headers, body: encoded)
          .timeout(_requestTimeout);
    } else {
      response = await _client
          .get(uri, headers: headers)
          .timeout(_requestTimeout);
    }
    final data = response.body.isEmpty
        ? <String, dynamic>{}
        : jsonDecode(response.body) as Map<String, dynamic>;
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw AccountException(
        data['detail'] as String? ?? '请求失败（${response.statusCode}）',
      );
    }
    return data;
  }

  Future<void> initialize() async {
    if (initialized) return;
    _token = await _storage.read(key: _tokenKey);
    if (_token != null) {
      try {
        final session = await _request('/api/v1/auth/session');
        user = AccountUser.fromJson(session['user'] as Map<String, dynamic>);
        await refreshCloudState();
      } catch (_) {
        _token = null;
        await _storage.delete(key: _tokenKey);
      }
    }
    initialized = true;
    notifyListeners();
  }

  Future<void> _acceptSession(Map<String, dynamic> data) async {
    final token = data['access_token'] as String?;
    if (token == null || token.length < 32) {
      throw const AccountException('服务器未返回安全会话');
    }
    _token = token;
    user = AccountUser.fromJson(data['user'] as Map<String, dynamic>);
    await _storage.write(key: _tokenKey, value: token);
    await refreshCloudState();
    notifyListeners();
  }

  Future<void> register({
    required String email,
    required String password,
    required String displayName,
    required String invitationCode,
  }) async {
    final data = await _request(
      '/api/v1/auth/register',
      method: 'POST',
      body: {
        'email': email.trim(),
        'password': password,
        'display_name': displayName.trim(),
        'invite_code': invitationCode.trim(),
        'client': platformClient,
      },
    );
    await _acceptSession(data);
  }

  Future<void> login({required String email, required String password}) async {
    final data = await _request(
      '/api/v1/auth/login',
      method: 'POST',
      body: {
        'email': email.trim(),
        'password': password,
        'client': platformClient,
      },
    );
    await _acceptSession(data);
  }

  Future<String> _googleIdToken() async {
    if (!googleAvailable) throw const AccountException('Google 登录尚未配置');
    final google = GoogleSignIn(
      scopes: const ['email', 'profile'],
      clientId: Platform.isIOS && _iosClientId.isNotEmpty ? _iosClientId : null,
      serverClientId: _webClientId,
    );
    final account = await google.signIn();
    if (account == null) throw const AccountException('已取消 Google 登录');
    final auth = await account.authentication;
    if (auth.idToken == null) throw const AccountException('Google 未返回身份凭据');
    return auth.idToken!;
  }

  Future<void> signInWithGoogle() async {
    final idToken = await _googleIdToken();
    final data = await _request(
      '/api/v1/auth/google',
      method: 'POST',
      body: {'id_token': idToken, 'client': platformClient},
    );
    await _acceptSession(data);
  }

  Future<void> linkGoogle() async {
    if (!isSignedIn) throw const AccountException('请先登录注册账户');
    final idToken = await _googleIdToken();
    final data = await _request(
      '/api/v1/auth/google/link',
      method: 'POST',
      body: {'id_token': idToken, 'client': platformClient},
    );
    user = AccountUser.fromJson(data['user'] as Map<String, dynamic>);
    notifyListeners();
  }

  Future<void> logout() async {
    try {
      await _request('/api/v1/auth/logout', method: 'POST');
    } finally {
      _token = null;
      user = null;
      cloudState = const {'history': [], 'favorites': [], 'bookshelf': []};
      await _storage.delete(key: _tokenKey);
      notifyListeners();
    }
  }

  Future<void> refreshCloudState() async {
    if (!isSignedIn) return;
    cloudState = await _request('/api/v1/me/state');
    notifyListeners();
  }

  Future<void> mergeLocalState(LocalStorageService storage) async {
    if (!isSignedIn) return;
    final history = storage
        .getHistory()
        .map(
          (entry) => {
            'book_id': entry.book.id,
            'title': entry.book.title,
            'author': entry.book.author,
            'cover_url': entry.book.coverUrl ?? '',
            'chapter_id': int.tryParse(entry.lastChapterId) ?? 1,
            'progress': 0,
            'updated_at': DateTime.fromMillisecondsSinceEpoch(
              entry.lastReadAt,
            ).toUtc().toIso8601String(),
          },
        )
        .toList();
    final favorites = storage
        .getFavorites()
        .map(
          (book) => {
            'book_id': book.id,
            'title': book.title,
            'author': book.author,
            'cover_url': book.coverUrl ?? '',
            'updated_at': DateTime.fromMillisecondsSinceEpoch(
              book.timestamp,
            ).toUtc().toIso8601String(),
          },
        )
        .toList();
    cloudState = await _request(
      '/api/v1/me/state',
      method: 'PUT',
      body: {
        'history': history,
        'favorites': favorites,
        'bookshelf': <dynamic>[],
      },
    );
    storage.mergeCloudState(cloudState);
    notifyListeners();
  }

  bool contains(String kind, String bookId) =>
      (cloudState[kind] as List? ?? const []).any(
        (item) => item is Map && item['book_id'] == bookId,
      );

  Future<void> setBookCollection(String kind, Book book, bool enabled) async {
    if (!isSignedIn) throw const AccountException('请先登录');
    if (kind != 'favorites' && kind != 'bookshelf') {
      throw const AccountException('记录类型无效');
    }
    if (enabled) {
      cloudState = await _request(
        '/api/v1/me/state',
        method: 'PUT',
        body: {
          'history': <dynamic>[],
          'favorites': kind == 'favorites' ? [_bookState(book)] : <dynamic>[],
          'bookshelf': kind == 'bookshelf' ? [_bookState(book)] : <dynamic>[],
        },
      );
    } else {
      await _request('/api/v1/me/state/$kind/${book.id}', method: 'DELETE');
      cloudState = {
        ...cloudState,
        kind: (cloudState[kind] as List? ?? const [])
            .where((item) => item is! Map || item['book_id'] != book.id)
            .toList(),
      };
    }
    notifyListeners();
  }

  Map<String, dynamic> _bookState(Book book) => {
    'book_id': book.id,
    'title': book.title,
    'author': book.author,
    'cover_url': book.coverUrl ?? '',
    'updated_at': DateTime.now().toUtc().toIso8601String(),
  };

  Future<List<Map<String, dynamic>>> uploads() async {
    final data = await _request('/api/v1/me/uploads');
    return (data['items'] as List? ?? const [])
        .map((item) => Map<String, dynamic>.from(item as Map))
        .toList();
  }

  Future<Map<String, dynamic>> uploadSource(String path) async {
    if (!isSignedIn) throw const AccountException('请先登录');
    final request = http.MultipartRequest(
      'POST',
      Uri.parse('${ApiService.baseUrl}/api/v1/me/uploads'),
    );
    request.headers['Accept'] = 'application/json';
    request.headers['Authorization'] = 'Bearer $_token';
    request.files.add(await http.MultipartFile.fromPath('file', path));
    final streamed = await _client.send(request).timeout(_uploadTimeout);
    final response = await http.Response.fromStream(
      streamed,
    ).timeout(_uploadTimeout);
    final data = jsonDecode(response.body) as Map<String, dynamic>;
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw AccountException(data['detail'] as String? ?? '上传失败');
    }
    return data;
  }
}
