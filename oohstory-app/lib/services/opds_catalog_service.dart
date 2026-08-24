import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:path/path.dart' as path;
import 'package:path_provider/path_provider.dart';
import 'package:xml/xml.dart';

import 'local_storage_service.dart';

class OpdsBookEntry {
  final String title;
  final String author;
  final Uri downloadUri;
  final String format;

  const OpdsBookEntry({
    required this.title,
    required this.author,
    required this.downloadUri,
    required this.format,
  });

  String get fileName {
    final fromPath = path.basename(downloadUri.path).trim();
    if (fromPath.isNotEmpty &&
        path.extension(fromPath).toLowerCase() == '.$format') {
      return fromPath;
    }
    final safeTitle = title.replaceAll(RegExp(r'[\\/:*?"<>|]'), '_').trim();
    return '${safeTitle.isEmpty ? 'OPDS-book' : safeTitle}.$format';
  }
}

class OpdsCatalogService {
  static const _maxCatalogBytes = 4 * 1024 * 1024;
  static const _maxBookBytes = 256 * 1024 * 1024;
  final http.Client _client;

  OpdsCatalogService({http.Client? client}) : _client = client ?? http.Client();

  Future<List<OpdsBookEntry>> fetch(Uri catalogUri) async {
    _validateUri(catalogUri);
    final response = await _client
        .get(
          catalogUri,
          headers: const {
            'Accept':
                'application/opds+json, application/atom+xml, application/xml;q=0.9',
          },
        )
        .timeout(const Duration(seconds: 20));
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw HttpException('OPDS 目录返回 HTTP ${response.statusCode}');
    }
    if (response.bodyBytes.length > _maxCatalogBytes) {
      throw const FormatException('OPDS 目录不能超过 4 MB');
    }
    final body = utf8
        .decode(response.bodyBytes, allowMalformed: true)
        .trimLeft();
    return body.startsWith('{')
        ? _parseOpds2(body, catalogUri)
        : _parseOpds1(body, catalogUri);
  }

  Future<LocalBookInfo> downloadAndImport(
    OpdsBookEntry entry,
    LocalStorageService storage,
  ) async {
    _validateUri(entry.downloadUri);
    final request = http.Request('GET', entry.downloadUri)
      ..headers['Accept'] = _mimeFor(entry.format);
    final response = await _client
        .send(request)
        .timeout(const Duration(seconds: 25));
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw HttpException('书籍下载返回 HTTP ${response.statusCode}');
    }
    final declaredLength = response.contentLength;
    if (declaredLength != null && declaredLength > _maxBookBytes) {
      throw const FormatException('单本 OPDS 书籍不能超过 256 MB');
    }
    final bytes = <int>[];
    await for (final chunk in response.stream.timeout(
      const Duration(seconds: 30),
    )) {
      bytes.addAll(chunk);
      if (bytes.length > _maxBookBytes) {
        throw const FormatException('单本 OPDS 书籍不能超过 256 MB');
      }
    }
    final temp = await getTemporaryDirectory();
    final file = File(
      '${temp.path}/opds-${DateTime.now().microsecondsSinceEpoch}.${entry.format}',
    );
    try {
      await file.writeAsBytes(Uint8List.fromList(bytes), flush: true);
      return await storage.importLocalBook(file.path, entry.fileName);
    } finally {
      if (await file.exists()) await file.delete();
    }
  }

  List<OpdsBookEntry> _parseOpds1(String body, Uri baseUri) {
    final document = XmlDocument.parse(body);
    final books = <OpdsBookEntry>[];
    for (final entry in document.descendants.whereType<XmlElement>().where(
      (node) => node.name.local == 'entry',
    )) {
      final title = _firstText(entry, 'title');
      final authorElement = entry.descendants
          .whereType<XmlElement>()
          .where((node) => node.name.local == 'author')
          .firstOrNull;
      final author = authorElement == null
          ? ''
          : _firstText(authorElement, 'name');
      for (final link in entry.descendants.whereType<XmlElement>().where(
        (node) => node.name.local == 'link',
      )) {
        final rel = link.getAttribute('rel') ?? '';
        final href = link.getAttribute('href') ?? '';
        final format = _formatFor(link.getAttribute('type') ?? '', href);
        if (!rel.contains('acquisition') || href.isEmpty || format == null) {
          continue;
        }
        final uri = baseUri.resolve(href);
        _validateUri(uri);
        books.add(
          OpdsBookEntry(
            title: title.isEmpty
                ? path.basenameWithoutExtension(uri.path)
                : title,
            author: author,
            downloadUri: uri,
            format: format,
          ),
        );
        break;
      }
    }
    return books;
  }

  List<OpdsBookEntry> _parseOpds2(String body, Uri baseUri) {
    final root = Map<String, dynamic>.from(jsonDecode(body) as Map);
    final publications = root['publications'] as List? ?? const [];
    final books = <OpdsBookEntry>[];
    for (final raw in publications) {
      final publication = Map<String, dynamic>.from(raw as Map);
      final metadata = Map<String, dynamic>.from(
        publication['metadata'] as Map? ?? const {},
      );
      final title = metadata['title']?.toString().trim() ?? '';
      final author = _opds2Author(metadata['author']);
      final links = publication['links'] as List? ?? const [];
      for (final rawLink in links) {
        final link = Map<String, dynamic>.from(rawLink as Map);
        final href = link['href']?.toString() ?? '';
        final rel = link['rel'];
        final relText = rel is List ? rel.join(' ') : rel?.toString() ?? '';
        final format = _formatFor(link['type']?.toString() ?? '', href);
        if (href.isEmpty ||
            format == null ||
            (!relText.contains('acquisition') &&
                relText != 'http://opds-spec.org/acquisition')) {
          continue;
        }
        final uri = baseUri.resolve(href);
        _validateUri(uri);
        books.add(
          OpdsBookEntry(
            title: title.isEmpty
                ? path.basenameWithoutExtension(uri.path)
                : title,
            author: author,
            downloadUri: uri,
            format: format,
          ),
        );
        break;
      }
    }
    return books;
  }

  String _opds2Author(Object? raw) {
    if (raw is String) return raw;
    if (raw is Map) return raw['name']?.toString() ?? '';
    if (raw is List) {
      return raw
          .map(
            (item) =>
                item is Map ? item['name']?.toString() ?? '' : item.toString(),
          )
          .where((name) => name.isNotEmpty)
          .join('、');
    }
    return '';
  }

  String? _formatFor(String mime, String href) {
    final normalized = mime.toLowerCase().split(';').first.trim();
    if (normalized == 'application/epub+zip') return 'epub';
    if (normalized == 'application/pdf') return 'pdf';
    if (normalized == 'application/vnd.comicbook+zip' ||
        normalized == 'application/x-cbz') {
      return 'cbz';
    }
    final extension = path
        .extension(Uri.tryParse(href)?.path ?? href)
        .replaceFirst('.', '')
        .toLowerCase();
    return const {'epub', 'pdf', 'cbz'}.contains(extension) ? extension : null;
  }

  String _mimeFor(String format) => switch (format) {
    'epub' => 'application/epub+zip',
    'pdf' => 'application/pdf',
    'cbz' => 'application/vnd.comicbook+zip',
    _ => 'application/octet-stream',
  };

  String _firstText(XmlNode node, String localName) => node.descendants
      .whereType<XmlElement>()
      .where((element) => element.name.local == localName)
      .map((element) => element.innerText.trim())
      .firstWhere((text) => text.isNotEmpty, orElse: () => '');

  void _validateUri(Uri uri) {
    if (!uri.hasAuthority || (uri.scheme != 'http' && uri.scheme != 'https')) {
      throw const FormatException('OPDS 地址必须是完整的 HTTP 或 HTTPS 地址');
    }
  }

  void close() => _client.close();
}
