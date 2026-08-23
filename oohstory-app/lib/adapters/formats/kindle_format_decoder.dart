import 'dart:convert';
import 'dart:typed_data';

import '../../core/capabilities.dart';
import '../../core/errors.dart';
import '../../core/models.dart';
import '../contracts/adapter_contracts.dart';
import '_binary.dart';
import 'format_limits.dart';

class KindleMetadata {
  const KindleMetadata({
    required this.title,
    required this.format,
    required this.textEncoding,
    required this.uniqueId,
  });

  final String title;
  final String format;
  final int textEncoding;
  final int uniqueId;
}

class KindleDecodedBook {
  const KindleDecodedBook({required this.metadata, required this.document});

  final KindleMetadata metadata;
  final DecodedDocument document;
}

class KindleFormatDecoder implements FormatDecoder {
  const KindleFormatDecoder({
    this.limits = const FormatLimits(maxExpandedBytes: 64 * 1024 * 1024),
  });

  static const Set<String> _mediaTypes = <String>{
    'application/x-mobipocket-ebook',
    'application/vnd.amazon.ebook',
    'application/x-mobi8-ebook',
  };

  final FormatLimits limits;

  @override
  String get providerId => 'kindle-drm-free';

  @override
  ProviderCapabilities get capabilities => ProviderCapabilities(
    providerId: providerId,
    supported: const <AdapterCapability>[AdapterCapability.textDecoding],
  );

  @override
  Future<bool> probe(String mediaType, List<int> header) async {
    final normalized = mediaType.toLowerCase().split(';').first.trim();
    return _mediaTypes.contains(normalized) || _hasMobiSignature(header);
  }

  @override
  Future<DecodedDocument> decode(Stream<List<int>> bytes) async =>
      (await decodeBook(bytes)).document;

  Future<KindleDecodedBook> decodeBook(Stream<List<int>> bytes) async {
    final data = await collectFormatBytes(
      bytes,
      maxBytes: limits.maxInputBytes,
    );
    try {
      return _decode(data);
    } on CoreException {
      rethrow;
    } on Object {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Kindle file is malformed',
      );
    }
  }

  KindleDecodedBook _decode(Uint8List bytes) {
    if (!_hasMobiSignature(bytes)) {
      throw const FormatException('Missing BOOKMOBI signature');
    }
    requireRange(bytes, 76, 2);
    final recordCount = uint16Be(bytes, 76);
    if (recordCount < 2 || recordCount > 65535) {
      throw const FormatException('Invalid Palm database record count');
    }
    requireRange(bytes, 78, recordCount * 8);
    final offsets = <int>[
      for (var index = 0; index < recordCount; index++)
        uint32Be(bytes, 78 + index * 8),
      bytes.length,
    ];
    if (offsets.first < 78 + recordCount * 8) {
      throw const FormatException('Palm database record overlaps its header');
    }
    for (var index = 0; index < offsets.length - 1; index++) {
      if (offsets[index] < 0 || offsets[index] >= offsets[index + 1]) {
        throw const FormatException('Invalid Palm database record offsets');
      }
    }

    final record0 = offsets.first;
    requireRange(bytes, record0, 32);
    final compression = uint16Be(bytes, record0);
    final textLength = uint32Be(bytes, record0 + 4);
    final textRecordCount = uint16Be(bytes, record0 + 8);
    final encryptionType = uint16Be(bytes, record0 + 12);
    if (encryptionType != 0) {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'DRM-protected Kindle files are not supported',
      );
    }
    if (textRecordCount == 0 || textRecordCount >= recordCount) {
      throw const FormatException('Invalid Kindle text record count');
    }
    if (textLength > limits.maxExpandedBytes) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Kindle text exceeds the configured expansion limit',
      );
    }
    if (bytes.isNotEmpty &&
        textLength > bytes.length * limits.maxExpansionRatio) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Kindle text exceeds the configured expansion ratio',
      );
    }

    final mobi = record0 + 16;
    if (ascii.decode(bytes.sublist(mobi, mobi + 4)) != 'MOBI') {
      throw const FormatException('Missing MOBI header');
    }
    final mobiHeaderLength = uint32Be(bytes, mobi + 4);
    if (mobiHeaderLength < 92) {
      throw const FormatException('MOBI header is too short');
    }
    requireRange(bytes, mobi, mobiHeaderLength);
    final textEncoding = uint32Be(bytes, mobi + 12);
    final uniqueId = uint32Be(bytes, mobi + 16);
    final mobiVersion = uint32Be(bytes, mobi + 20);
    if (mobiHeaderLength >= 184) {
      final drmOffset = uint32Be(bytes, mobi + 168);
      final drmCount = uint32Be(bytes, mobi + 172);
      if (drmOffset != 0xffffffff || drmCount != 0) {
        throw const CoreException(
          CoreErrorCode.unsupported,
          'DRM-protected Kindle files are not supported',
        );
      }
    }

    final titleOffset = uint32Be(bytes, mobi + 84);
    final titleLength = uint32Be(bytes, mobi + 88);
    requireRange(bytes, record0 + titleOffset, titleLength);
    final title = _decodeText(
      bytes.sublist(record0 + titleOffset, record0 + titleOffset + titleLength),
      textEncoding,
    ).trim();
    if (title.isEmpty) throw const FormatException('Kindle title is empty');

    final output = BytesBuilder(copy: false);
    for (var index = 1; index <= textRecordCount; index++) {
      final record = bytes.sublist(offsets[index], offsets[index + 1]);
      switch (compression) {
        case 1:
          output.add(record);
        case 2:
          output.add(_decompressPalmDoc(record));
        default:
          throw CoreException(
            CoreErrorCode.unsupported,
            'Unsupported Kindle compression: $compression',
          );
      }
      if (output.length > limits.maxExpandedBytes) {
        throw const CoreException(
          CoreErrorCode.payloadTooLarge,
          'Kindle text exceeds the configured expansion limit',
        );
      }
    }
    final textBytes = output.takeBytes();
    if (textBytes.length < textLength) {
      throw const FormatException('Kindle text is truncated');
    }
    final text = _decodeText(textBytes.sublist(0, textLength), textEncoding);
    final sections = _sections(text);
    if (sections.isEmpty) throw const FormatException('Kindle text is empty');
    final format = mobiVersion >= 8 ? 'azw3' : 'mobi';
    return KindleDecodedBook(
      metadata: KindleMetadata(
        title: title,
        format: format,
        textEncoding: textEncoding,
        uniqueId: uniqueId,
      ),
      document: DecodedDocument(
        version: '$format:$uniqueId:$mobiVersion',
        sections: sections,
      ),
    );
  }

  Uint8List _decompressPalmDoc(List<int> input) {
    final output = <int>[];
    var index = 0;
    while (index < input.length) {
      final byte = input[index++];
      if (byte == 0 || (byte >= 9 && byte <= 0x7f)) {
        output.add(byte);
      } else if (byte <= 8) {
        if (index + byte > input.length) {
          throw const FormatException('PalmDOC literal is truncated');
        }
        output.addAll(input.sublist(index, index + byte));
        index += byte;
      } else if (byte <= 0xbf) {
        if (index >= input.length) {
          throw const FormatException('PalmDOC back-reference is truncated');
        }
        final pair = (byte << 8) | input[index++];
        final distance = (pair >> 3) & 0x7ff;
        final length = (pair & 7) + 3;
        if (distance == 0 || distance > output.length) {
          throw const FormatException('PalmDOC back-reference is invalid');
        }
        for (var copied = 0; copied < length; copied++) {
          output.add(output[output.length - distance]);
          if (output.length > limits.maxExpandedBytes) {
            throw const CoreException(
              CoreErrorCode.payloadTooLarge,
              'Kindle text exceeds the configured expansion limit',
            );
          }
        }
      } else {
        output
          ..add(0x20)
          ..add(byte ^ 0x80);
      }
    }
    return Uint8List.fromList(output);
  }

  String _decodeText(List<int> bytes, int encoding) {
    switch (encoding) {
      case 65001:
        return utf8.decode(bytes);
      case 1252:
        return String.fromCharCodes(bytes.map(_windows1252));
      default:
        throw CoreException(
          CoreErrorCode.unsupported,
          'Unsupported Kindle text encoding: $encoding',
        );
    }
  }

  int _windows1252(int byte) => switch (byte) {
    0x80 => 0x20ac,
    0x82 => 0x201a,
    0x83 => 0x0192,
    0x84 => 0x201e,
    0x85 => 0x2026,
    0x86 => 0x2020,
    0x87 => 0x2021,
    0x88 => 0x02c6,
    0x89 => 0x2030,
    0x8a => 0x0160,
    0x8b => 0x2039,
    0x8c => 0x0152,
    0x8e => 0x017d,
    0x91 => 0x2018,
    0x92 => 0x2019,
    0x93 => 0x201c,
    0x94 => 0x201d,
    0x95 => 0x2022,
    0x96 => 0x2013,
    0x97 => 0x2014,
    0x98 => 0x02dc,
    0x99 => 0x2122,
    0x9a => 0x0161,
    0x9b => 0x203a,
    0x9c => 0x0153,
    0x9e => 0x017e,
    0x9f => 0x0178,
    _ => byte,
  };

  List<String> _sections(String text) {
    final blockBreaks = RegExp(
      r'<\s*(?:br\s*/?|/p|/div|/h[1-6])\s*>',
      caseSensitive: false,
    );
    final withoutMarkup = text
        .replaceAll(blockBreaks, '\n')
        .replaceAll(RegExp(r'<[^>]*>'), '')
        .replaceAll('&nbsp;', ' ')
        .replaceAll('&amp;', '&')
        .replaceAll('&lt;', '<')
        .replaceAll('&gt;', '>')
        .replaceAll('&quot;', '"')
        .replaceAll('&#39;', "'");
    return withoutMarkup
        .split(RegExp(r'\r?\n+'))
        .map((section) => section.trim())
        .where((section) => section.isNotEmpty)
        .toList(growable: false);
  }

  bool _hasMobiSignature(List<int> bytes) =>
      bytes.length >= 68 &&
      bytes[60] == 0x42 &&
      bytes[61] == 0x4f &&
      bytes[62] == 0x4f &&
      bytes[63] == 0x4b &&
      bytes[64] == 0x4d &&
      bytes[65] == 0x4f &&
      bytes[66] == 0x42 &&
      bytes[67] == 0x49;
}
