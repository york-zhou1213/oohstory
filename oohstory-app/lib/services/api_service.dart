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
    return (data['categories'] as List).cast<String>();
  }

  Future<Map<String, dynamic>> getBooks({
    String? category,
    String? status,
    String? query,
    String sort = 'title',
    int page = 1,
    int pageSize = 24,
  }) async {
    final params = <String, String>{
      'sort': sort,
      'page': page.toString(),
      'page_size': pageSize.toString(),
    };
    if (category != null && category.isNotEmpty) params['category'] = category;
    if (status != null && status.isNotEmpty) params['status'] = status;
    if (query != null && query.isNotEmpty) params['q'] = query;
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

  Future<List<Chapter>> getChapters(String bookId) async {
    final data = await _getJson('/api/v1/books/$bookId/chapters');
    return (data['chapters'] as List)
        .map((e) => Chapter.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<Chapter> getChapter(String bookId, String chapterId) async {
    final data = await _getJson('/api/v1/books/$bookId/chapters/$chapterId');
    return Chapter.fromJson(data);
  }

  Future<List<Deconstruction>> getDeconstructions() async {
    final data = await _getJson('/api/v1/deconstructions');
    return (data['items'] as List)
        .map((e) => Deconstruction.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<Map<String, dynamic>> getDeconstruction(String slug) =>
      _getJson('/api/v1/deconstructions/$slug');

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
