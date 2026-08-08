import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'package:http/http.dart' as http;
import 'package:http/io_client.dart' as io_client;
import 'package:shared_preferences/shared_preferences.dart';
import '../models/book.dart';

const _appUserAgent = 'OOHStoryApp/1.18 (Flutter; official)';

class _OOHClient extends http.BaseClient {
  late final http.Client _inner;

  _OOHClient() {
    final httpClient = HttpClient()
      ..badCertificateCallback = _rejectBadCert
      ..connectionTimeout = const Duration(seconds: 15)
      ..idleTimeout = const Duration(seconds: 30);
    _inner = io_client.IOClient(httpClient);
  }

  static bool _rejectBadCert(X509Certificate cert, String host, int port) {
    return false;
  }

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) {
    if (request.url.host.endsWith('oohstory.com') &&
        request.url.scheme != 'https') {
      throw ApiException('不安全的连接已被阻止', 0);
    }
    request.headers.putIfAbsent('User-Agent', () => _appUserAgent);
    return _inner.send(request);
  }

  @override
  void close() => _inner.close();
}

class ApiService {
  static const String baseUrl = 'https://oohstory.com';

  final http.Client _client = _OOHClient();

  Future<Map<String, dynamic>> _getJson(String path) async {
    final response = await _client.get(Uri.parse('$baseUrl$path'));
    if (response.statusCode == 429) {
      throw ApiException('请求过于频繁，请稍后再试', response.statusCode);
    }
    if (response.statusCode != 200) {
      throw ApiException('请求失败: ${response.statusCode}', response.statusCode);
    }
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> getHome() => _getJson('/api/v1/home');

  Future<List<String>> getCategories() async {
    final data = await _getJson('/api/v1/categories');
    final list = (data['items'] ?? data['categories'] ?? []) as List;
    return list
        .map((e) {
          if (e is String) return e;
          if (e is Map) return (e['name'] ?? '') as String;
          return e.toString();
        })
        .where((s) => s.isNotEmpty)
        .toList();
  }

  Future<Map<String, dynamic>> getBooks({
    String? category,
    String? status,
    String? query,
    String? words,
    String sort = 'recent',
    int page = 1,
    int pageSize = 48,
  }) async {
    final params = <String, String>{
      'sort': sort,
      'page': page.toString(),
      'page_size': pageSize.toString(),
    };
    if (category != null && category.isNotEmpty) params['category'] = category;
    if (status != null && status.isNotEmpty) params['serialization'] = status;
    if (query != null && query.isNotEmpty) params['q'] = query;
    if (words != null && words.isNotEmpty) params['words'] = words;
    final uri = Uri.parse(
      '$baseUrl/api/v1/books',
    ).replace(queryParameters: params);
    final response = await _client.get(uri);
    if (response.statusCode != 200) {
      throw ApiException('请求失败', response.statusCode);
    }
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<Book> getBook(String bookId) async {
    final data = await _getJson('/api/v1/books/$bookId');
    return Book.fromJson(data);
  }

  Future<Map<String, dynamic>> getBookMetrics(String bookId) =>
      _getJson('/api/v1/books/$bookId/metrics');

  Future<Map<String, dynamic>> recordBookMetric(
    String bookId,
    String event,
  ) async {
    final preferences = await SharedPreferences.getInstance();
    const key = 'oohstory_public_metric_visitor_id';
    var visitorId = preferences.getString(key) ?? '';
    if (!RegExp(
      r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    ).hasMatch(visitorId)) {
      final bytes = List<int>.generate(16, (_) => Random.secure().nextInt(256));
      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      bytes[8] = (bytes[8] & 0x3f) | 0x80;
      final hex = bytes
          .map((value) => value.toRadixString(16).padLeft(2, '0'))
          .join();
      visitorId =
          '${hex.substring(0, 8)}-${hex.substring(8, 12)}-'
          '${hex.substring(12, 16)}-${hex.substring(16, 20)}-${hex.substring(20)}';
      await preferences.setString(key, visitorId);
    }
    final response = await _client.post(
      Uri.parse('$baseUrl/api/v1/books/$bookId/metrics/$event'),
      headers: const {'Content-Type': 'application/json'},
      body: jsonEncode({'visitor_id': visitorId}),
    );
    if (response.statusCode != 200) {
      throw ApiException('请求失败: ${response.statusCode}', response.statusCode);
    }
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  String coverUrl(String bookId) => '$baseUrl/api/v1/books/$bookId/cover';

  Future<ChapterCatalog> getChapterCatalog(String bookId) async {
    final data = await _getJson('/api/v1/books/$bookId/chapters');
    final list = (data['chapters'] ?? data['items'] ?? []) as List;
    final chapters = list
        .map((e) => Chapter.fromJson(e as Map<String, dynamic>))
        .toList();
    final volList = (data['volumes'] as List?) ?? [];
    final volumes = volList
        .map((e) => Volume.fromJson(e as Map<String, dynamic>))
        .toList();
    return ChapterCatalog(chapters: chapters, volumes: volumes);
  }

  Future<List<Chapter>> getChapters(String bookId) async {
    final catalog = await getChapterCatalog(bookId);
    return catalog.chapters;
  }

  String illustrationUrl(String bookId, String subpath) =>
      '$baseUrl/api/v1/books/$bookId/illustrations/${Uri.encodeFull(subpath)}';

  Future<Chapter> getChapter(String bookId, String chapterId) async {
    final data = await _getJson('/api/v1/books/$bookId/chapters/$chapterId');
    return Chapter.fromJson(data);
  }

  String fullCoverUrl(String? relativePath) {
    if (relativePath == null || relativePath.isEmpty) return '';
    if (relativePath.startsWith('http')) return relativePath;
    return '$baseUrl$relativePath';
  }

  Future<List<Deconstruction>> getDeconstructions() async {
    final data = await _getJson('/api/v1/deconstructions');
    return (data['items'] as List)
        .map((e) => Deconstruction.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<Map<String, dynamic>> getDeconstruction(String slug) =>
      _getJson('/api/v1/deconstructions/$slug');

  Future<Map<String, dynamic>> getDeconstructionFile(
    String slug,
    String subpath,
  ) => _getJson('/api/v1/deconstructions/$slug/file/$subpath');

  Future<List<TtsVoice>> getTtsVoices() async {
    final data = await _getJson('/api/v1/tts/voices');
    return (data['voices'] as List)
        .map((e) => TtsVoice.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<String> audiobookClientId() async {
    final preferences = await SharedPreferences.getInstance();
    const key = 'oohstory_audiobook_client_id';
    var value = preferences.getString(key) ?? '';
    if (!RegExp(r'^[A-Za-z0-9_-]{16,96}$').hasMatch(value)) {
      value = List<int>.generate(
        24,
        (_) => Random.secure().nextInt(36),
      ).map((number) => number.toRadixString(36)).join();
      await preferences.setString(key, value);
    }
    return value;
  }

  Future<Map<String, dynamic>> createAudiobookSession({
    required String bookId,
    required String chapterId,
    required String mode,
    required String narrator,
    required String voice,
    required double rate,
    String emotion = 'auto',
  }) async {
    final clientId = await audiobookClientId();
    final response = await _client.post(
      Uri.parse('$baseUrl/api/v1/audiobook/sessions'),
      headers: {
        'Content-Type': 'application/json',
        'X-Audiobook-Client': clientId,
      },
      body: jsonEncode({
        'book_id': bookId,
        'chapter_id': int.parse(chapterId),
        'client_id': clientId,
        'mode': mode,
        'narrator': narrator,
        'voice': voice,
        'emotion': emotion,
        'rate': rate,
      }),
    );
    if (response.statusCode != 201) {
      throw ApiException('听书章节分析失败', response.statusCode);
    }
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>?> prefetchAudiobookNext(String sessionId) async {
    final clientId = await audiobookClientId();
    final response = await _client.post(
      Uri.parse('$baseUrl/api/v1/audiobook/sessions/$sessionId/next'),
      headers: {'X-Audiobook-Client': clientId},
    );
    if (response.statusCode != 200) return null;
    return (jsonDecode(response.body) as Map<String, dynamic>)['next']
        as Map<String, dynamic>?;
  }

  Future<http.Response> fetchAudiobookSegment(String endpoint) async {
    final clientId = await audiobookClientId();
    return _client.post(
      Uri.parse('$baseUrl$endpoint'),
      headers: {'X-Audiobook-Client': clientId},
    );
  }

  Future<void> cancelAudiobookSession(String sessionId) async {
    if (sessionId.isEmpty) return;
    final clientId = await audiobookClientId();
    await _client.delete(
      Uri.parse('$baseUrl/api/v1/audiobook/sessions/$sessionId'),
      headers: {'X-Audiobook-Client': clientId},
    );
  }

  void dispose() => _client.close();
}

class ApiException implements Exception {
  final String message;
  final int statusCode;
  ApiException(this.message, this.statusCode);

  @override
  String toString() => 'ApiException($statusCode): $message';
}
