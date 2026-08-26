import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/foundation.dart';

import '../../adapters/dictionary/mdx_dictionary_adapter.dart';
import '../../adapters/formats/comic_archive_decoder.dart';
import '../../adapters/formats/kindle_format_decoder.dart';
import '../../adapters/ocr/local_ocr_adapter.dart';
import '../../core/errors.dart';
import '../../core/models.dart';
import 'comic_page_extractor.dart';

enum LocalContentKind { text, comic }

class LocalContentBook {
  const LocalContentBook.text({
    required this.title,
    required this.format,
    required this.version,
    required this.sections,
  }) : kind = LocalContentKind.text,
       pages = const <ExtractedComicPage>[];

  const LocalContentBook.comic({
    required this.title,
    required this.format,
    required this.version,
    required this.pages,
  }) : kind = LocalContentKind.comic,
       sections = const <String>[];

  final LocalContentKind kind;
  final String title;
  final String format;
  final String version;
  final List<String> sections;
  final List<ExtractedComicPage> pages;

  int get pageCount =>
      kind == LocalContentKind.text ? sections.length : pages.length;
}

class LocalPickedFile {
  LocalPickedFile({
    required this.name,
    required this.size,
    required Stream<List<int>> stream,
  }) : _stream = stream;

  factory LocalPickedFile.fromBytes(String name, List<int> bytes) {
    final copy = Uint8List.fromList(bytes);
    return LocalPickedFile(
      name: name,
      size: copy.length,
      stream: Stream<List<int>>.value(copy),
    );
  }

  final String name;
  final int size;
  final Stream<List<int>> _stream;

  Future<Uint8List> read({required int maxBytes}) async {
    if (size < 0 || size > maxBytes) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Selected file exceeds the configured size limit',
      );
    }
    final builder = BytesBuilder(copy: false);
    var total = 0;
    await for (final chunk in _stream) {
      total += chunk.length;
      if (total > maxBytes) {
        throw const CoreException(
          CoreErrorCode.payloadTooLarge,
          'Selected file exceeds the configured size limit',
        );
      }
      builder.add(chunk);
    }
    return builder.takeBytes();
  }
}

Future<LocalPickedFile?> pickLocalContentFile({
  required List<String> extensions,
}) async {
  final result = await FilePicker.platform.pickFiles(
    type: FileType.custom,
    allowedExtensions: extensions,
    allowMultiple: false,
    withData: false,
    withReadStream: true,
  );
  final file = result?.files.singleOrNull;
  if (file == null) return null;
  final stream = file.readStream;
  if (stream == null) {
    throw const LocalContentException('无法读取所选文件，请重新选择');
  }
  return LocalPickedFile(name: file.name, size: file.size, stream: stream);
}

class LocalDictionary {
  const LocalDictionary({required this.name, required this.adapter});

  final String name;
  final MdxDictionaryAdapter adapter;

  int get entryCount => adapter.entryCount;
}

class LocalContentException implements Exception {
  const LocalContentException(this.message);

  final String message;

  @override
  String toString() => message;
}

class LocalContentService {
  LocalContentService({
    required this.ocrAdapter,
    this.kindleDecoder = const KindleFormatDecoder(),
    this.comicDecoder = const ComicArchiveFormatDecoder(),
    this.comicPageExtractor = const ComicPageExtractor(),
  });

  factory LocalContentService.forCurrentPlatform() {
    final platform = kIsWeb ? 'web' : defaultTargetPlatform.name.toLowerCase();
    final supported =
        !kIsWeb && defaultTargetPlatform != TargetPlatform.fuchsia;
    return LocalContentService(
      ocrAdapter: supported
          ? LocalOcrAdapter.portable(platform: platform)
          : LocalOcrAdapter.unavailable(platform: platform),
    );
  }

  static const bookExtensions = <String>[
    'mobi',
    'azw',
    'azw3',
    'cbr',
    'cbt',
    'cb7',
  ];
  static const dictionaryExtensions = <String>['mdx'];
  static const imageExtensions = <String>['png', 'jpg', 'jpeg'];

  static const _maxBookBytes = 64 * 1024 * 1024;
  static const _maxDictionaryBytes = 32 * 1024 * 1024;
  static const _maxImageBytes = 12 * 1024 * 1024;

  final LocalOcrAdapter ocrAdapter;
  final KindleFormatDecoder kindleDecoder;
  final ComicArchiveFormatDecoder comicDecoder;
  final ComicPageExtractor comicPageExtractor;

  bool get isOcrAvailable => ocrAdapter.isAvailable;
  List<String> get ocrLanguages => ocrAdapter.supportedLanguages;

  Future<LocalContentBook> importBook(LocalPickedFile file) async {
    final extension = _extension(file.name);
    if (!bookExtensions.contains(extension)) {
      throw const LocalContentException('请选择 MOBI、AZW、AZW3、CBR、CBT 或 CB7 文件');
    }
    try {
      final bytes = await file.read(maxBytes: _maxBookBytes);
      if (const <String>{'mobi', 'azw', 'azw3'}.contains(extension)) {
        final decoded = await kindleDecoder.decodeBook(
          Stream<List<int>>.value(bytes),
        );
        return LocalContentBook.text(
          title: decoded.metadata.title,
          format: decoded.metadata.format.toUpperCase(),
          version: decoded.document.version,
          sections: decoded.document.sections,
        );
      }
      final decoded = await comicDecoder.decode(Stream<List<int>>.value(bytes));
      final pages = comicPageExtractor.extract(bytes, decoded.sections);
      return LocalContentBook.comic(
        title: _displayName(file.name),
        format: extension.toUpperCase(),
        version: decoded.version,
        pages: pages,
      );
    } on Object catch (error) {
      throw LocalContentException(_messageFor(error));
    }
  }

  Future<LocalDictionary> attachDictionary(LocalPickedFile file) async {
    if (_extension(file.name) != 'mdx') {
      throw const LocalContentException('请选择本地 MDX 词典文件');
    }
    try {
      final bytes = await file.read(maxBytes: _maxDictionaryBytes);
      return LocalDictionary(
        name: file.name,
        adapter: MdxDictionaryAdapter.fromBytes(bytes),
      );
    } on Object catch (error) {
      throw LocalContentException(_messageFor(error));
    }
  }

  Future<List<DictionaryEntry>> lookup(
    LocalDictionary dictionary,
    String term,
  ) async {
    try {
      return dictionary.adapter.lookup(term);
    } on Object catch (error) {
      throw LocalContentException(_messageFor(error));
    }
  }

  Future<Uint8List> readOcrImage(LocalPickedFile file) async {
    if (!imageExtensions.contains(_extension(file.name))) {
      throw const LocalContentException('本地 OCR 仅支持 PNG 或 JPEG 图片');
    }
    try {
      return await file.read(maxBytes: _maxImageBytes);
    } on Object catch (error) {
      throw LocalContentException(_messageFor(error));
    }
  }

  OcrJob startOcr(Uint8List imageBytes) {
    try {
      return ocrAdapter.start(
        imageBytes,
        locale: ocrLanguages.contains('en') ? 'en' : null,
      );
    } on Object catch (error) {
      throw LocalContentException(_messageFor(error));
    }
  }

  String describeError(Object error) => _messageFor(error);

  static String _messageFor(Object error) {
    if (error is LocalContentException) return error.message;
    if (error is CoreException) {
      if (error.code == CoreErrorCode.payloadTooLarge) {
        return '文件或解压内容超出安全上限，请选择更小的文件';
      }
      if (error.code == CoreErrorCode.unsupported) {
        final lower = error.message.toLowerCase();
        if (lower.contains('drm') || lower.contains('encrypted')) {
          return '此文件受 DRM 或加密保护，无法导入；请选择无 DRM 版本';
        }
        if (lower.contains('ocr') || lower.contains('platform')) {
          return '此平台暂不支持本地 OCR，且不会上传到远程服务';
        }
        return '此文件使用当前本地阅读器不支持的编码或压缩方式';
      }
      if (error.message.toLowerCase().contains('cancel')) {
        return '已取消本地 OCR';
      }
    }
    return '文件已损坏或格式不完整，请检查后重试';
  }

  static String _extension(String name) {
    final dot = name.lastIndexOf('.');
    return dot < 0 ? '' : name.substring(dot + 1).toLowerCase();
  }

  static String _displayName(String name) {
    final normalized = name.replaceAll('\\', '/').split('/').last;
    final dot = normalized.lastIndexOf('.');
    return dot <= 0 ? normalized : normalized.substring(0, dot);
  }
}
