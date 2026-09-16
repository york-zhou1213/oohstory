import 'dart:convert';
import 'dart:math';

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

  AccountUser copyWith({String? displayName}) => AccountUser(
    id: id,
    email: email,
    displayName: displayName ?? this.displayName,
    emailVerified: emailVerified,
    googleLinked: googleLinked,
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

  final http.Client _client = OohHttpClient();
  String? _token;
  AccountUser? user;
  bool initialized = false;
  Map<String, dynamic> cloudState = const {
    'history': <dynamic>[],
    'favorites': <dynamic>[],
    'bookshelf': <dynamic>[],
  };

  bool get isSignedIn => user != null && _token != null;
  Map<String, String> get authHeaders => _token == null
      ? const {}
      : <String, String>{'Authorization': 'Bearer $_token'};
  bool get googleAvailable =>
      _webClientId.isNotEmpty &&
      (defaultTargetPlatform != TargetPlatform.iOS || _iosClientId.isNotEmpty);

  String get platformClient {
    if (kIsWeb) return 'web';
    return switch (defaultTargetPlatform) {
      TargetPlatform.android => 'android',
      TargetPlatform.iOS => 'ios',
      TargetPlatform.macOS => 'macos',
      TargetPlatform.windows => 'windows',
      TargetPlatform.linux => 'linux',
      TargetPlatform.fuchsia => 'fuchsia',
    };
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
      clientId:
          defaultTargetPlatform == TargetPlatform.iOS && _iosClientId.isNotEmpty
          ? _iosClientId
          : null,
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

  Future<Map<String, dynamic>> profile() async {
    if (!isSignedIn) throw const AccountException('请先登录');
    return _request('/api/v1/me/profile');
  }

  Future<Map<String, dynamic>> updateProfile({
    required String displayName,
    required String bio,
    required String gender,
    String? birthday,
    required String location,
  }) async {
    if (!isSignedIn) throw const AccountException('请先登录');
    final data = await _request(
      '/api/v1/me/profile',
      method: 'PUT',
      body: {
        'display_name': displayName.trim(),
        'bio': bio.trim(),
        'gender': gender,
        'birthday': birthday == null || birthday.isEmpty ? null : birthday,
        'location': location.trim(),
      },
    );
    final profile = Map<String, dynamic>.from(data['profile'] as Map);
    user = user?.copyWith(displayName: profile['display_name'] as String?);
    notifyListeners();
    return data;
  }

  Future<Map<String, dynamic>> changePassword({
    required String currentPassword,
    required String newPassword,
  }) {
    if (!isSignedIn) throw const AccountException('请先登录');
    return _request(
      '/api/v1/me/password',
      method: 'POST',
      body: {'current_password': currentPassword, 'new_password': newPassword},
    );
  }

  String avatarUrl(String relative) =>
      relative.startsWith('http') ? relative : '${ApiService.baseUrl}$relative';

  Future<Map<String, dynamic>> uploadAvatar(String path) async {
    if (!isSignedIn) throw const AccountException('请先登录');
    final request = http.MultipartRequest(
      'POST',
      Uri.parse('${ApiService.baseUrl}/api/v1/me/avatar'),
    );
    request.headers.addAll({'Accept': 'application/json', ...authHeaders});
    request.files.add(await http.MultipartFile.fromPath('file', path));
    return _sendMultipart(request, timeout: _uploadTimeout);
  }

  Future<void> removeAvatar() async {
    if (!isSignedIn) throw const AccountException('请先登录');
    await _request('/api/v1/me/avatar', method: 'DELETE');
  }

  Future<Map<String, dynamic>> readingLevel() async {
    if (!isSignedIn) throw const AccountException('请先登录');
    return _request('/api/v1/me/reading-level');
  }

  Future<Map<String, dynamic>> sendReadingHeartbeat({
    required String bookId,
    int activeSeconds = 30,
  }) async {
    if (!isSignedIn) return const {};
    return _request(
      '/api/v1/me/reading-heartbeat',
      method: 'POST',
      body: {
        'event_id': _uuidV4(),
        'book_id': bookId,
        'active_seconds': activeSeconds.clamp(1, 60),
      },
    );
  }

  Future<void> mergeLocalState(LocalStorageService storage) async {
    if (!isSignedIn) return;
    final localHistory = storage.getHistory();
    final history = localHistory.map(_readingState).toList();
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
        'bookshelf': localHistory.map(_automaticShelfState).toList(),
      },
    );
    storage.mergeCloudState(cloudState);
    notifyListeners();
  }

  /// Persist one authoritative reading checkpoint and automatically promote
  /// the work into the account bookshelf. The API performs partial upserts, so
  /// this does not replace unrelated account records.
  Future<void> syncReadingEntry(HistoryEntry entry) async {
    if (!isSignedIn) return;
    cloudState = await _request(
      '/api/v1/me/state',
      method: 'PUT',
      body: {
        'history': [_readingState(entry)],
        'favorites': <dynamic>[],
        'bookshelf': [_automaticShelfState(entry)],
      },
    );
    notifyListeners();
  }

  bool contains(String kind, String bookId) =>
      (cloudState[kind] as List? ?? const []).any(
        (item) => item is Map && item['book_id'] == bookId,
      );

  Future<void> setBookCollection(
    String kind,
    Book book,
    bool enabled, {
    String note = '',
  }) async {
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
          'bookshelf': kind == 'bookshelf'
              ? [
                  {..._bookState(book), 'note': note.trim()},
                ]
              : <dynamic>[],
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

  Future<void> updateBookshelfNote(
    Map<String, dynamic> item,
    String note,
  ) async {
    if (!isSignedIn) throw const AccountException('请先登录');
    cloudState = await _request(
      '/api/v1/me/state',
      method: 'PUT',
      body: {
        'history': <dynamic>[],
        'favorites': <dynamic>[],
        'bookshelf': [
          {
            'book_id': item['book_id'],
            'title': item['title'] ?? '',
            'author': item['author'] ?? '',
            'cover_url': item['cover_url'] ?? '',
            'note': note.trim(),
            'updated_at': DateTime.now().toUtc().toIso8601String(),
          },
        ],
      },
    );
    notifyListeners();
  }

  Future<void> removeState(String kind, String bookId) async {
    if (!isSignedIn) throw const AccountException('请先登录');
    await _request('/api/v1/me/state/$kind/$bookId', method: 'DELETE');
    cloudState = {
      ...cloudState,
      kind: (cloudState[kind] as List? ?? const [])
          .where((item) => item is! Map || item['book_id'] != bookId)
          .toList(),
    };
    notifyListeners();
  }

  Map<String, dynamic> _bookState(Book book) => {
    'book_id': book.id,
    'title': book.title,
    'author': book.author,
    'cover_url': book.coverUrl ?? '',
    'updated_at': DateTime.now().toUtc().toIso8601String(),
  };

  Map<String, dynamic> _readingState(HistoryEntry entry) => {
    'book_id': entry.book.id,
    'title': entry.book.title,
    'author': entry.book.author,
    'cover_url': entry.book.coverUrl ?? '',
    'chapter_id': entry.lastChapterPosition,
    'progress': entry.chapterProgress,
    'updated_at': DateTime.fromMillisecondsSinceEpoch(
      entry.lastReadAt,
    ).toUtc().toIso8601String(),
  };

  Map<String, dynamic> _automaticShelfState(HistoryEntry entry) => {
    'book_id': entry.book.id,
    'title': entry.book.title,
    'author': entry.book.author,
    'cover_url': entry.book.coverUrl ?? '',
    'note': _existingBookshelfNote(entry.book.id),
    'updated_at': DateTime.fromMillisecondsSinceEpoch(
      entry.lastReadAt,
    ).toUtc().toIso8601String(),
  };

  String _existingBookshelfNote(String bookId) {
    for (final item in cloudState['bookshelf'] as List? ?? const []) {
      if (item is Map && item['book_id'] == bookId) {
        return item['note'] as String? ?? '';
      }
    }
    return '';
  }

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
    return _sendMultipart(request, timeout: _uploadTimeout);
  }

  Future<List<Map<String, dynamic>>> novelSubmissions() async {
    final data = await _request('/api/v1/me/novel-submissions');
    return (data['items'] as List? ?? const [])
        .map((item) => Map<String, dynamic>.from(item as Map))
        .toList();
  }

  Future<List<Map<String, dynamic>>> categories() async {
    final data = await _request('/api/v1/categories');
    return (data['items'] as List? ?? const [])
        .map((item) => Map<String, dynamic>.from(item as Map))
        .toList();
  }

  Future<Map<String, dynamic>> uploadNovel({
    required Map<String, String> metadata,
    required String manuscriptPath,
    required String coverPath,
  }) async {
    if (!isSignedIn) throw const AccountException('请先登录');
    final request = http.MultipartRequest(
      'POST',
      Uri.parse('${ApiService.baseUrl}/api/v1/me/novel-submissions'),
    );
    request.headers.addAll({'Accept': 'application/json', ...authHeaders});
    request.fields['metadata'] = jsonEncode(metadata);
    request.files.add(
      await http.MultipartFile.fromPath('manuscript', manuscriptPath),
    );
    request.files.add(await http.MultipartFile.fromPath('cover', coverPath));
    return _sendMultipart(request, timeout: _uploadTimeout);
  }

  Future<Map<String, dynamic>> notifications({int limit = 100}) async {
    if (!isSignedIn) throw const AccountException('请先登录');
    return _request('/api/v1/me/notifications?limit=${limit.clamp(1, 200)}');
  }

  Future<void> markNotificationRead([String? notificationId]) async {
    if (!isSignedIn) throw const AccountException('请先登录');
    final path = notificationId == null
        ? '/api/v1/me/notifications/read'
        : '/api/v1/me/notifications/$notificationId/read';
    await _request(path, method: 'POST');
  }

  Future<Map<String, dynamic>> chapterComments(
    String bookId,
    String chapterId,
  ) => _request('/api/v1/books/$bookId/chapters/$chapterId/comments');

  Future<Map<String, dynamic>> bookComments(String bookId) =>
      _request('/api/v1/books/$bookId/comments');

  Future<Map<String, dynamic>> createBookComment({
    required String bookId,
    required String content,
  }) {
    if (!isSignedIn) throw const AccountException('请先登录后再评论');
    return _request(
      '/api/v1/books/$bookId/comments',
      method: 'POST',
      body: {'content': content.trim()},
    );
  }

  Future<Map<String, dynamic>> addBookCommentLike(String commentId) {
    if (!isSignedIn) throw const AccountException('请先登录后再点赞');
    return _request('/api/v1/comments/$commentId/likes', method: 'POST');
  }

  Future<Map<String, dynamic>> createParagraphComment({
    required String bookId,
    required String chapterId,
    required int paragraphIndex,
    required String content,
  }) {
    if (!isSignedIn) throw const AccountException('请先登录后再评论');
    return _request(
      '/api/v1/books/$bookId/chapters/$chapterId/comments',
      method: 'POST',
      body: {'paragraph_index': paragraphIndex, 'content': content.trim()},
    );
  }

  Future<Map<String, dynamic>> addParagraphCommentLike(String commentId) {
    if (!isSignedIn) throw const AccountException('请先登录后再点赞');
    return _request(
      '/api/v1/paragraph-comments/$commentId/likes',
      method: 'POST',
    );
  }

  Future<Map<String, dynamic>> recommendationStatus(String bookId) {
    if (!isSignedIn) throw const AccountException('请先登录');
    return _request('/api/v1/books/$bookId/recommendation');
  }

  Future<Map<String, dynamic>> recommendBook(String bookId, {String? eventId}) {
    if (!isSignedIn) throw const AccountException('请先登录后推荐作品');
    return _request(
      '/api/v1/books/$bookId/recommend',
      method: 'POST',
      body: {'event_id': eventId ?? _uuidV4()},
    );
  }

  Future<Map<String, dynamic>> _sendMultipart(
    http.MultipartRequest request, {
    required Duration timeout,
  }) async {
    final streamed = await _client.send(request).timeout(timeout);
    final response = await http.Response.fromStream(streamed).timeout(timeout);
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

  String _uuidV4() {
    final random = Random.secure();
    final bytes = List<int>.generate(16, (_) => random.nextInt(256));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    final hex = bytes
        .map((value) => value.toRadixString(16).padLeft(2, '0'))
        .join();
    return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-'
        '${hex.substring(12, 16)}-${hex.substring(16, 20)}-${hex.substring(20)}';
  }
}
