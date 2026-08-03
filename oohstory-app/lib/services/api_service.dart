import 'dart:convert';
import 'package:http/http.dart' as http;
import '../models/book.dart';

class ApiService {
  static const String baseUrl = 'https://oohstory.com';

  final http.Client _client = http.Client();

  Future<Map<String, dynamic>> _getJson(String path) async {
    final response = await _client.get(Uri.parse('$baseUrl$path'));
    if (response.statusCode != 200) {
      throw ApiException('请求失败: ${response.statusCode}', response.statusCode);
    }
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> getHome() => _getJson('/api/v1/home');

  Future<List<String>> getCategories() async {
    final data = await _getJson('/api/v1/categories');
    final list = (data['items'] ?? data['categories'] ?? []) as List;
    return list.map((e) {
      if (e is String) return e;
      if (e is Map) return (e['name'] ?? '') as String;
      return e.toString();
    }).where((s) => s.isNotEmpty).toList();
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
    final uri = Uri.parse('$baseUrl/api/v1/books').replace(queryParameters: params);
    final response = await _client.get(uri);
    if (response.statusCode != 200) throw ApiException('请求失败', response.statusCode);
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<Book> getBook(String bookId) async {
    final data = await _getJson('/api/v1/books/$bookId');
    return Book.fromJson(data);
  }

  Future<Map<String, dynamic>> getBookMetrics(String bookId) =>
      _getJson('/api/v1/books/$bookId/metrics');

  String coverUrl(String bookId) => '$baseUrl/api/v1/books/$bookId/cover';

  Future<ChapterCatalog> getChapterCatalog(String bookId) async {
    final data = await _getJson('/api/v1/books/$bookId/chapters');
    final list = (data['chapters'] ?? data['items'] ?? []) as List;
    final chapters = list.map((e) => Chapter.fromJson(e as Map<String, dynamic>)).toList();
    final volList = (data['volumes'] as List?) ?? [];
    final volumes = volList.map((e) => Volume.fromJson(e as Map<String, dynamic>)).toList();
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

  Future<Map<String, dynamic>> getDeconstructionFile(String slug, String subpath) =>
      _getJson('/api/v1/deconstructions/$slug/file/$subpath');

  Future<List<TtsVoice>> getTtsVoices() async {
    final data = await _getJson('/api/v1/tts/voices');
    return (data['voices'] as List)
        .map((e) => TtsVoice.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Uri ttsUrl(String text, String voice, {String rate = '+0%', String pitch = '+0Hz'}) {
    final params = <String, String>{
      'text': text,
      'voice': voice,
      'rate': rate,
      'pitch': pitch,
    };
    return Uri.parse('$baseUrl/api/v1/tts/speak').replace(queryParameters: params);
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
