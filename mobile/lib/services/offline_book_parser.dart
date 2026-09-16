import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:path/path.dart' as path;
import 'package:xml/xml.dart';

class ParsedOfflineBook {
  final String title;
  final String author;
  final String format;
  final String content;
  final int sourceSize;
  final Uint8List? assetBytes;
  final String storageExtension;
  final int pageCount;

  const ParsedOfflineBook({
    required this.title,
    required this.author,
    required this.format,
    required this.content,
    required this.sourceSize,
    this.assetBytes,
    this.storageExtension = 'txt',
    this.pageCount = 0,
  });

  int get wordCount => content.replaceAll(RegExp(r'\s+'), '').length;
}

class OfflineBookParser {
  static const supportedExtensions = <String>{
    'txt',
    'md',
    'markdown',
    'htm',
    'html',
    'xhtml',
    'xml',
    'fb2',
    'docx',
    'epub',
    'pdf',
    'cbz',
  };

  static const _maxSourceBytes = 256 * 1024 * 1024;
  static const _maxExpandedBytes = 512 * 1024 * 1024;

  const OfflineBookParser();

  Future<ParsedOfflineBook> parse(String filePath, String fileName) async {
    final source = File(filePath);
    final size = await source.length();
    if (size <= 0) throw const FormatException('文件内容为空');
    if (size > _maxSourceBytes) {
      throw const FormatException('单个离线文件不能超过 256 MB');
    }
    final extension = path
        .extension(fileName)
        .replaceFirst('.', '')
        .toLowerCase();
    if (!supportedExtensions.contains(extension)) {
      throw FormatException('暂不支持 .$extension 文件');
    }
    final bytes = await source.readAsBytes();
    final fallbackTitle = path.basenameWithoutExtension(fileName).trim();
    return switch (extension) {
      'pdf' => _parsePdf(bytes, fallbackTitle, size),
      'cbz' => _parseCbz(bytes, fallbackTitle, size),
      'epub' => _parseEpub(bytes, fallbackTitle, size),
      'docx' => _parseDocx(bytes, fallbackTitle, size),
      'fb2' => _parseFb2(_decodeText(bytes), fallbackTitle, size),
      'html' || 'htm' || 'xhtml' || 'xml' => ParsedOfflineBook(
        title: fallbackTitle,
        author: '',
        format: extension,
        content: _htmlToText(_decodeText(bytes)),
        sourceSize: size,
      ),
      'md' || 'markdown' => ParsedOfflineBook(
        title: fallbackTitle,
        author: '',
        format: 'md',
        content: _markdownToText(_decodeText(bytes)),
        sourceSize: size,
      ),
      _ => ParsedOfflineBook(
        title: fallbackTitle,
        author: '',
        format: 'txt',
        content: _decodeText(bytes).trim(),
        sourceSize: size,
      ),
    };
  }

  ParsedOfflineBook _parsePdf(Uint8List bytes, String fallbackTitle, int size) {
    if (bytes.length < 8 || utf8.decode(bytes.take(5).toList()) != '%PDF-') {
      throw const FormatException('PDF 文件头无效');
    }
    return ParsedOfflineBook(
      title: fallbackTitle,
      author: '',
      format: 'pdf',
      content: '',
      sourceSize: size,
      assetBytes: bytes,
      storageExtension: 'pdf',
    );
  }

  ParsedOfflineBook _parseCbz(Uint8List bytes, String fallbackTitle, int size) {
    final archive = _safeArchive(bytes);
    final pages = archive.files.where((file) {
      if (!file.isFile) return false;
      final extension = path.extension(file.name).toLowerCase();
      return const {
        '.jpg',
        '.jpeg',
        '.png',
        '.webp',
        '.gif',
      }.contains(extension);
    }).toList()..sort((left, right) => _naturalCompare(left.name, right.name));
    if (pages.isEmpty) throw const FormatException('CBZ 中没有可读图片页');
    if (pages.length > 5000) throw const FormatException('CBZ 图片页不能超过 5000 页');
    for (final page in pages) {
      if (page.size > 64 * 1024 * 1024) {
        throw FormatException('CBZ 图片页过大：${path.basename(page.name)}');
      }
    }
    return ParsedOfflineBook(
      title: fallbackTitle,
      author: '',
      format: 'cbz',
      content: '',
      sourceSize: size,
      assetBytes: bytes,
      storageExtension: 'cbz',
      pageCount: pages.length,
    );
  }

  ParsedOfflineBook _parseFb2(String raw, String fallbackTitle, int size) {
    final document = XmlDocument.parse(raw);
    final titleInfo = document.descendants
        .whereType<XmlElement>()
        .where((node) => node.name.local == 'title-info')
        .firstOrNull;
    final title = _firstDescendantText(titleInfo, 'book-title');
    final authorNode = titleInfo?.descendants
        .whereType<XmlElement>()
        .where((node) => node.name.local == 'author')
        .firstOrNull;
    final author = [
      _firstDescendantText(authorNode, 'first-name'),
      _firstDescendantText(authorNode, 'middle-name'),
      _firstDescendantText(authorNode, 'last-name'),
    ].where((part) => part.isNotEmpty).join(' ');
    final paragraphs = document.descendants
        .whereType<XmlElement>()
        .where(
          (node) => const {'title', 'subtitle', 'p'}.contains(node.name.local),
        )
        .map((node) => node.innerText.trim())
        .where((value) => value.isNotEmpty)
        .toList();
    return ParsedOfflineBook(
      title: title.isEmpty ? fallbackTitle : title,
      author: author,
      format: 'fb2',
      content: paragraphs.join('\n\n'),
      sourceSize: size,
    );
  }

  ParsedOfflineBook _parseDocx(
    Uint8List bytes,
    String fallbackTitle,
    int size,
  ) {
    final archive = _safeArchive(bytes);
    final documentFile = archive.findFile('word/document.xml');
    if (documentFile == null) throw const FormatException('DOCX 缺少正文');
    final document = XmlDocument.parse(_decodeText(_fileBytes(documentFile)));
    final paragraphs = document.descendants
        .whereType<XmlElement>()
        .where((node) => node.name.local == 'p')
        .map(
          (paragraph) => paragraph.descendants
              .whereType<XmlElement>()
              .where((node) => node.name.local == 't')
              .map((node) => node.innerText)
              .join(),
        )
        .map((value) => value.trim())
        .where((value) => value.isNotEmpty)
        .toList();
    var title = fallbackTitle;
    var author = '';
    final core = archive.findFile('docProps/core.xml');
    if (core != null) {
      final metadata = XmlDocument.parse(_decodeText(_fileBytes(core)));
      title = _firstElementText(metadata, 'title').isEmpty
          ? fallbackTitle
          : _firstElementText(metadata, 'title');
      author = _firstElementText(metadata, 'creator');
    }
    return ParsedOfflineBook(
      title: title,
      author: author,
      format: 'docx',
      content: paragraphs.join('\n\n'),
      sourceSize: size,
    );
  }

  ParsedOfflineBook _parseEpub(
    Uint8List bytes,
    String fallbackTitle,
    int size,
  ) {
    final archive = _safeArchive(bytes);
    final container = archive.findFile('META-INF/container.xml');
    if (container == null) throw const FormatException('EPUB 缺少容器描述');
    final containerXml = XmlDocument.parse(_decodeText(_fileBytes(container)));
    final rootFile = containerXml.descendants
        .whereType<XmlElement>()
        .where((node) => node.name.local == 'rootfile')
        .map((node) => node.getAttribute('full-path') ?? '')
        .firstWhere((value) => value.isNotEmpty, orElse: () => '');
    if (rootFile.isEmpty || rootFile.contains('..')) {
      throw const FormatException('EPUB 目录路径无效');
    }
    final packageFile = archive.findFile(rootFile);
    if (packageFile == null) throw const FormatException('EPUB 缺少内容清单');
    final packageXml = XmlDocument.parse(_decodeText(_fileBytes(packageFile)));
    final title = _firstElementText(packageXml, 'title');
    final author = _firstElementText(packageXml, 'creator');
    final manifest = <String, String>{};
    for (final item in packageXml.descendants.whereType<XmlElement>().where(
      (node) => node.name.local == 'item',
    )) {
      final id = item.getAttribute('id') ?? '';
      final href = item.getAttribute('href') ?? '';
      if (id.isNotEmpty && href.isNotEmpty && !href.contains('..')) {
        manifest[id] = href;
      }
    }
    final base = path.posix.dirname(rootFile);
    final sections = <String>[];
    for (final itemRef in packageXml.descendants.whereType<XmlElement>().where(
      (node) => node.name.local == 'itemref',
    )) {
      final href = manifest[itemRef.getAttribute('idref') ?? ''];
      if (href == null) continue;
      final normalized = path.posix.normalize(path.posix.join(base, href));
      if (normalized.startsWith('../') || normalized.contains('/../')) continue;
      final entry = archive.findFile(normalized);
      if (entry == null) continue;
      final text = _htmlToText(_decodeText(_fileBytes(entry)));
      if (text.isNotEmpty) sections.add(text);
    }
    if (sections.isEmpty) throw const FormatException('EPUB 中没有可读正文');
    return ParsedOfflineBook(
      title: title.isEmpty ? fallbackTitle : title,
      author: author,
      format: 'epub',
      content: sections.join('\n\n'),
      sourceSize: size,
    );
  }

  Archive _safeArchive(Uint8List bytes) {
    final archive = ZipDecoder().decodeBytes(bytes, verify: true);
    var expanded = 0;
    for (final file in archive.files) {
      final normalized = path.posix.normalize(file.name);
      if (normalized.startsWith('../') || normalized.contains('/../')) {
        throw const FormatException('压缩包包含不安全路径');
      }
      expanded += file.size;
      if (expanded > _maxExpandedBytes) {
        throw const FormatException('压缩包解压后超过 512 MB');
      }
    }
    return archive;
  }

  static Uint8List _fileBytes(ArchiveFile file) =>
      Uint8List.fromList(file.content as List<int>);

  static String _decodeText(List<int> bytes) {
    if (bytes.length >= 2 && bytes[0] == 0xff && bytes[1] == 0xfe) {
      final units = <int>[];
      for (var index = 2; index + 1 < bytes.length; index += 2) {
        units.add(bytes[index] | (bytes[index + 1] << 8));
      }
      return String.fromCharCodes(units);
    }
    return utf8.decode(bytes, allowMalformed: true).replaceFirst('\ufeff', '');
  }

  static String _firstElementText(XmlNode document, String localName) =>
      document.descendants
          .whereType<XmlElement>()
          .where((node) => node.name.local == localName)
          .map((node) => node.innerText.trim())
          .firstWhere((value) => value.isNotEmpty, orElse: () => '');

  static String _firstDescendantText(XmlNode? node, String localName) {
    if (node == null) return '';
    return _firstElementText(node, localName);
  }

  static String _htmlToText(String html) {
    final cleaned = html
        .replaceAll(
          RegExp(r'<(script|style)[^>]*>[\s\S]*?</\1>', caseSensitive: false),
          '',
        )
        .replaceAll(RegExp(r'<br\s*/?>', caseSensitive: false), '\n')
        .replaceAll(
          RegExp(r'</(p|div|h[1-6]|li|section|article)>', caseSensitive: false),
          '\n\n',
        )
        .replaceAll(RegExp(r'<[^>]+>'), ' ');
    return _decodeEntities(cleaned)
        .replaceAll(RegExp(r'[ \t]+'), ' ')
        .replaceAll(RegExp(r'\n[ \t]+'), '\n')
        .replaceAll(RegExp(r'\n{3,}'), '\n\n')
        .trim();
  }

  static String _markdownToText(String markdown) => _decodeEntities(
    markdown
        .replaceAll(RegExp(r'^#{1,6}\s*', multiLine: true), '')
        .replaceAll(RegExp(r'!\[[^\]]*\]\([^)]*\)'), '')
        .replaceAllMapped(
          RegExp(r'\[([^\]]+)\]\([^)]*\)'),
          (match) => match.group(1) ?? '',
        )
        .replaceAll(RegExp(r'(```[\s\S]*?```|`([^`]*)`)'), r'$2')
        .replaceAll(RegExp(r'\*\*|__|~~'), '')
        .replaceAll(RegExp(r'^\s*[-*+]\s+', multiLine: true), '• ')
        .trim(),
  );

  static String _decodeEntities(String value) => value
      .replaceAll('&nbsp;', ' ')
      .replaceAll('&lt;', '<')
      .replaceAll('&gt;', '>')
      .replaceAll('&quot;', '"')
      .replaceAll('&#39;', "'")
      .replaceAll('&amp;', '&');

  static int _naturalCompare(String left, String right) {
    final pattern = RegExp(r'(\d+)|(\D+)');
    final leftParts = pattern
        .allMatches(left.toLowerCase())
        .map((match) => match.group(0)!)
        .toList();
    final rightParts = pattern
        .allMatches(right.toLowerCase())
        .map((match) => match.group(0)!)
        .toList();
    final length = leftParts.length < rightParts.length
        ? leftParts.length
        : rightParts.length;
    for (var index = 0; index < length; index++) {
      final leftNumber = int.tryParse(leftParts[index]);
      final rightNumber = int.tryParse(rightParts[index]);
      final comparison = leftNumber != null && rightNumber != null
          ? leftNumber.compareTo(rightNumber)
          : leftParts[index].compareTo(rightParts[index]);
      if (comparison != 0) return comparison;
    }
    return leftParts.length.compareTo(rightParts.length);
  }
}

extension _FirstOrNull<T> on Iterable<T> {
  T? get firstOrNull => isEmpty ? null : first;
}
