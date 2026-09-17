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
    this.author = '',
    this.language = '',
  });

  final String title;
  final String format;
  final int textEncoding;
  final int uniqueId;
  final String author;
  final String language;
}

class KindleDecodedBook {
  const KindleDecodedBook({required this.metadata, required this.document});

  final KindleMetadata metadata;
  final DecodedDocument document;
}

/// Decoder for DRM-free PalmDB/MOBI6, PalmDOC, HUFF/CDIC, and KF8/AZW3.
///
/// The implementation intentionally rejects encrypted content. It also keeps
/// all record, recursion, and expansion work bounded by [FormatLimits].
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
    } on FormatException catch (error) {
      throw CoreException(
        CoreErrorCode.validationError,
        'Kindle file is malformed: ${error.message}',
      );
    } on Object {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Kindle file is malformed',
      );
    }
  }

  KindleDecodedBook _decode(Uint8List bytes) {
    final database = _PalmDatabase.parse(bytes);
    var header = _KindleHeader.parse(database, 0, _decodeText);

    // A combo file contains a legacy MOBI section followed by a BOUNDARY
    // record and a KF8 section. Prefer the KF8 rendition when it is present.
    final boundary = header.exthUint(121);
    if (header.mobiVersion < 8 && boundary != null) {
      for (final candidate in <int>[boundary + 1, boundary]) {
        if (candidate <= 0 || candidate >= database.recordCount) continue;
        try {
          final next = _KindleHeader.parse(database, candidate, _decodeText);
          if (next.mobiVersion >= 8) {
            header = next;
            break;
          }
        } on Object {
          // Some producers point at the BOUNDARY record and others at the
          // following record. Try both before retaining the MOBI6 rendition.
        }
      }
    }

    if (header.encryptionType != 0 || header.hasDrm) {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'DRM-protected Kindle files are not supported',
      );
    }
    if (header.textRecordCount == 0 ||
        header.baseRecord + header.textRecordCount >= database.recordCount) {
      throw const FormatException('Invalid Kindle text record count');
    }
    if (header.textLength > limits.maxExpandedBytes) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Kindle text exceeds the configured expansion limit',
      );
    }
    if (bytes.isNotEmpty &&
        header.textLength > bytes.length * limits.maxExpansionRatio) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Kindle text exceeds the configured expansion ratio',
      );
    }

    final huff = header.compression == 17480
        ? _HuffCdicDecoder.parse(database, header, limits)
        : null;
    final output = BytesBuilder(copy: false);
    for (var index = 0; index < header.textRecordCount; index++) {
      var record = database.record(header.baseRecord + index + 1);
      record = _stripTrailingData(record, header.extraDataFlags);
      switch (header.compression) {
        case 1:
          output.add(record);
        case 2:
          output.add(_decompressPalmDoc(record));
        case 17480:
          output.add(huff!.decompress(record));
        default:
          throw CoreException(
            CoreErrorCode.unsupported,
            'Unsupported Kindle compression: ${header.compression}',
          );
      }
      if (output.length > limits.maxExpandedBytes) {
        throw const CoreException(
          CoreErrorCode.payloadTooLarge,
          'Kindle text exceeds the configured expansion limit',
        );
      }
    }
    var textBytes = output.takeBytes();
    if (textBytes.length < header.textLength) {
      throw const FormatException('Kindle text is truncated');
    }
    if (textBytes.length > header.textLength) {
      textBytes = Uint8List.sublistView(textBytes, 0, header.textLength);
    }

    final htmlSections = header.isKf8
        ? _decodeKf8Sections(database, header, textBytes)
        : <String>[_decodeText(textBytes, header.textEncoding)];
    final sections = htmlSections.expand(_sections).toList(growable: false);
    if (sections.isEmpty) throw const FormatException('Kindle text is empty');

    final format = header.isKf8 ? 'azw3' : 'mobi';
    final title = header.title.trim();
    if (title.isEmpty) throw const FormatException('Kindle title is empty');
    return KindleDecodedBook(
      metadata: KindleMetadata(
        title: title,
        format: format,
        textEncoding: header.textEncoding,
        uniqueId: header.uniqueId,
        author: header.exthStrings(100, _decodeText).join(', '),
        language: header.exthStrings(524, _decodeText).firstOrNull ?? '',
      ),
      document: DecodedDocument(
        version: '$format:${header.uniqueId}:${header.mobiVersion}',
        sections: sections,
      ),
    );
  }

  List<String> _decodeKf8Sections(
    _PalmDatabase database,
    _KindleHeader header,
    Uint8List raw,
  ) {
    final skeletonIndex = header.kf8SkeletonIndex;
    final fragmentIndex = header.kf8FragmentIndex;
    if (skeletonIndex == null || fragmentIndex == null) {
      return <String>[_decodeText(raw, header.textEncoding)];
    }
    final skeletons = _IndexData.parse(database, header, skeletonIndex).entries
        .map((entry) {
          final values = entry.tags[6];
          final fragments = entry.tags[1];
          if (values == null || values.length < 2 || fragments == null) {
            throw const FormatException('KF8 skeleton index is incomplete');
          }
          return _Kf8Skeleton(
            fragmentCount: fragments.first,
            offset: values[0],
            length: values[1],
          );
        })
        .toList(growable: false);
    final fragmentData = _IndexData.parse(database, header, fragmentIndex);
    final fragments = fragmentData.entries
        .map((entry) {
          final values = entry.tags[6];
          final id = entry.tags[4];
          if (values == null || values.length < 2 || id == null) {
            throw const FormatException('KF8 fragment index is incomplete');
          }
          return _Kf8Fragment(
            insertOffset: int.parse(entry.name),
            id: id.first,
            offset: values[0],
            length: values[1],
          );
        })
        .toList(growable: false);

    final result = <String>[];
    var fragmentStart = 0;
    for (final skeleton in skeletons) {
      final fragmentEnd = fragmentStart + skeleton.fragmentCount;
      if (fragmentEnd > fragments.length) {
        throw const FormatException('KF8 fragment range is invalid');
      }
      final selected = fragments.sublist(fragmentStart, fragmentEnd);
      final contentLength =
          skeleton.length +
          selected.fold<int>(0, (sum, item) => sum + item.length);
      requireRange(raw, skeleton.offset, contentLength);
      final sectionRaw = Uint8List.sublistView(
        raw,
        skeleton.offset,
        skeleton.offset + contentLength,
      );
      var rebuilt = Uint8List.fromList(sectionRaw.sublist(0, skeleton.length));
      for (final fragment in selected) {
        final sourceOffset = skeleton.length + fragment.offset;
        requireRange(sectionRaw, sourceOffset, fragment.length);
        final insertion = fragment.insertOffset - skeleton.offset;
        if (insertion < 0 || insertion > rebuilt.length) {
          throw const FormatException('KF8 fragment insertion is invalid');
        }
        final builder = BytesBuilder(copy: false)
          ..add(Uint8List.sublistView(rebuilt, 0, insertion))
          ..add(
            Uint8List.sublistView(
              sectionRaw,
              sourceOffset,
              sourceOffset + fragment.length,
            ),
          )
          ..add(Uint8List.sublistView(rebuilt, insertion));
        rebuilt = builder.takeBytes();
        if (rebuilt.length > limits.maxExpandedBytes) {
          throw const CoreException(
            CoreErrorCode.payloadTooLarge,
            'KF8 section exceeds the configured expansion limit',
          );
        }
      }
      result.add(_decodeText(rebuilt, header.textEncoding));
      fragmentStart = fragmentEnd;
    }
    return result.isEmpty
        ? <String>[_decodeText(raw, header.textEncoding)]
        : result;
  }

  Uint8List _stripTrailingData(Uint8List input, int flags) {
    var end = input.length;
    var trailers = flags >> 1;
    while (trailers != 0) {
      if ((trailers & 1) != 0) {
        final length = _trailingEntryLength(input, end);
        if (length <= 0 || length > end) {
          throw const FormatException('Kindle trailing data is invalid');
        }
        end -= length;
      }
      trailers >>= 1;
    }
    if ((flags & 1) != 0) {
      if (end == 0) throw const FormatException('Kindle trailer is missing');
      final length = (input[end - 1] & 3) + 1;
      if (length > end) {
        throw const FormatException('Kindle multibyte trailer is invalid');
      }
      end -= length;
    }
    return Uint8List.sublistView(input, 0, end);
  }

  int _trailingEntryLength(List<int> bytes, int end) {
    var value = 0;
    final start = end > 4 ? end - 4 : 0;
    for (var index = start; index < end; index++) {
      final byte = bytes[index];
      if ((byte & 0x80) != 0) value = 0;
      value = (value << 7) | (byte & 0x7f);
    }
    return value;
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
      r'<\s*(?:br\s*/?|/p|/div|/h[1-6]|/li|/section|/article|(?:mbp:)?pagebreak)\s*>',
      caseSensitive: false,
    );
    final withoutMarkup = text
        .replaceAll(
          RegExp(r'<(script|style)[^>]*>[\s\S]*?</\1>', caseSensitive: false),
          '',
        )
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

typedef _TextDecoder = String Function(List<int> bytes, int encoding);

class _PalmDatabase {
  const _PalmDatabase(this.bytes, this.offsets);

  factory _PalmDatabase.parse(Uint8List bytes) {
    if (bytes.length < 68 ||
        ascii.decode(bytes.sublist(60, 68)) != 'BOOKMOBI') {
      throw const FormatException('Missing BOOKMOBI signature');
    }
    requireRange(bytes, 76, 2);
    final count = uint16Be(bytes, 76);
    if (count < 2) {
      throw const FormatException('Invalid Palm database record count');
    }
    requireRange(bytes, 78, count * 8);
    final offsets = <int>[
      for (var index = 0; index < count; index++)
        uint32Be(bytes, 78 + index * 8),
      bytes.length,
    ];
    if (offsets.first < 78 + count * 8) {
      throw const FormatException('Palm database record overlaps its header');
    }
    for (var index = 0; index < count; index++) {
      if (offsets[index] < 0 || offsets[index] >= offsets[index + 1]) {
        throw const FormatException('Invalid Palm database record offsets');
      }
    }
    return _PalmDatabase(bytes, offsets);
  }

  final Uint8List bytes;
  final List<int> offsets;

  int get recordCount => offsets.length - 1;

  Uint8List record(int index) {
    if (index < 0 || index >= recordCount) {
      throw const FormatException('Palm database record is out of range');
    }
    return Uint8List.sublistView(bytes, offsets[index], offsets[index + 1]);
  }
}

class _KindleHeader {
  const _KindleHeader({
    required this.baseRecord,
    required this.compression,
    required this.textLength,
    required this.textRecordCount,
    required this.encryptionType,
    required this.textEncoding,
    required this.uniqueId,
    required this.mobiVersion,
    required this.title,
    required this.mobiHeaderLength,
    required this.huffRecordIndex,
    required this.huffRecordCount,
    required this.extraDataFlags,
    required this.drmOffset,
    required this.drmCount,
    required this.exth,
    required this.kf8FdstIndex,
    required this.kf8FragmentIndex,
    required this.kf8SkeletonIndex,
  });

  factory _KindleHeader.parse(
    _PalmDatabase database,
    int baseRecord,
    _TextDecoder decodeText,
  ) {
    final record = database.record(baseRecord);
    requireRange(record, 0, 32);
    final compression = uint16Be(record, 0);
    final textLength = uint32Be(record, 4);
    final textRecordCount = uint16Be(record, 8);
    final encryptionType = uint16Be(record, 12);
    const mobi = 16;
    if (ascii.decode(record.sublist(mobi, mobi + 4)) != 'MOBI') {
      throw const FormatException('Missing MOBI header');
    }
    final headerLength = uint32Be(record, mobi + 4);
    if (headerLength < 92) {
      throw const FormatException('MOBI header is too short');
    }
    requireRange(record, mobi, headerLength);
    final encoding = uint32Be(record, mobi + 12);
    final uniqueId = uint32Be(record, mobi + 16);
    final version = uint32Be(record, mobi + 20);
    final titleOffset = uint32Be(record, mobi + 68);
    final titleLength = uint32Be(record, mobi + 72);
    requireRange(record, titleOffset, titleLength);
    var title = decodeText(
      record.sublist(titleOffset, titleOffset + titleLength),
      encoding,
    );

    final exth = <int, List<Uint8List>>{};
    if (headerLength >= 116 && (uint32Be(record, mobi + 112) & 0x40) != 0) {
      final start = mobi + headerLength;
      requireRange(record, start, 12);
      if (ascii.decode(record.sublist(start, start + 4)) != 'EXTH') {
        throw const FormatException('Invalid EXTH header');
      }
      final length = uint32Be(record, start + 4);
      final count = uint32Be(record, start + 8);
      requireRange(record, start, length);
      var cursor = start + 12;
      for (var index = 0; index < count; index++) {
        requireRange(record, cursor, 8);
        final type = uint32Be(record, cursor);
        final entryLength = uint32Be(record, cursor + 4);
        if (entryLength < 8 || cursor + entryLength > start + length) {
          throw const FormatException('Invalid EXTH record');
        }
        exth
            .putIfAbsent(type, () => <Uint8List>[])
            .add(
              Uint8List.sublistView(record, cursor + 8, cursor + entryLength),
            );
        cursor += entryLength;
      }
      final exthTitle = exth[503];
      if (exthTitle != null && exthTitle.isNotEmpty) {
        title = decodeText(exthTitle.first, encoding);
      }
    }

    int? optional32(int mobiOffset) => headerLength >= mobiOffset + 4
        ? uint32Be(record, mobi + mobiOffset)
        : null;
    final drmOffset = optional32(152) ?? 0xffffffff;
    final drmCount = optional32(156) ?? 0;
    final extraFlags = optional32(224) ?? 0;
    return _KindleHeader(
      baseRecord: baseRecord,
      compression: compression,
      textLength: textLength,
      textRecordCount: textRecordCount,
      encryptionType: encryptionType,
      textEncoding: encoding,
      uniqueId: uniqueId,
      mobiVersion: version,
      title: title,
      mobiHeaderLength: headerLength,
      huffRecordIndex: optional32(96),
      huffRecordCount: optional32(100),
      extraDataFlags: extraFlags,
      drmOffset: drmOffset,
      drmCount: drmCount,
      exth: exth,
      kf8FdstIndex: version >= 8 ? optional32(176) : null,
      kf8FragmentIndex: version >= 8 ? optional32(232) : null,
      kf8SkeletonIndex: version >= 8 ? optional32(236) : null,
    );
  }

  final int baseRecord;
  final int compression;
  final int textLength;
  final int textRecordCount;
  final int encryptionType;
  final int textEncoding;
  final int uniqueId;
  final int mobiVersion;
  final String title;
  final int mobiHeaderLength;
  final int? huffRecordIndex;
  final int? huffRecordCount;
  final int extraDataFlags;
  final int drmOffset;
  final int drmCount;
  final Map<int, List<Uint8List>> exth;
  final int? kf8FdstIndex;
  final int? kf8FragmentIndex;
  final int? kf8SkeletonIndex;

  bool get isKf8 => mobiVersion >= 8;
  bool get hasDrm => drmOffset != 0xffffffff || drmCount != 0;

  int? exthUint(int type) {
    final value = exth[type]?.firstOrNull;
    if (value == null || value.length != 4) return null;
    return uint32Be(value, 0);
  }

  List<String> exthStrings(int type, _TextDecoder decodeText) =>
      exth[type]
          ?.map((value) => decodeText(value, textEncoding).trim())
          .where((value) => value.isNotEmpty)
          .toList(growable: false) ??
      const <String>[];
}

class _HuffCdicDecoder {
  _HuffCdicDecoder({
    required this.table1,
    required this.minCodes,
    required this.maxCodes,
    required this.dictionary,
    required this.limits,
  });

  factory _HuffCdicDecoder.parse(
    _PalmDatabase database,
    _KindleHeader header,
    FormatLimits limits,
  ) {
    final huffIndex = header.huffRecordIndex;
    final huffCount = header.huffRecordCount;
    if (huffIndex == null || huffCount == null || huffCount < 2) {
      throw const FormatException('HUFF/CDIC record metadata is missing');
    }
    final absoluteHuff = header.baseRecord + huffIndex;
    if (absoluteHuff < 0 || absoluteHuff + huffCount > database.recordCount) {
      throw const FormatException('HUFF/CDIC record range is invalid');
    }
    final huff = database.record(absoluteHuff);
    requireRange(huff, 0, 24);
    if (ascii.decode(huff.sublist(0, 4)) != 'HUFF' || uint32Be(huff, 4) < 24) {
      throw const FormatException('Invalid HUFF header');
    }
    final table1Offset = uint32Be(huff, 8);
    final table2Offset = uint32Be(huff, 12);
    requireRange(huff, table1Offset, 256 * 4);
    requireRange(huff, table2Offset, 32 * 8);
    final table1 = <_HuffPrefix>[];
    for (var index = 0; index < 256; index++) {
      final value = uint32Be(huff, table1Offset + index * 4);
      final length = value & 0x1f;
      if (length < 1 || length > 32) {
        throw const FormatException('Invalid HUFF code length');
      }
      table1.add(
        _HuffPrefix(
          terminal: (value & 0x80) != 0,
          length: length,
          maxCode: value >> 8,
        ),
      );
    }
    final minCodes = List<int>.filled(33, 0);
    final maxCodes = List<int>.filled(33, 0);
    for (var length = 1; length <= 32; length++) {
      minCodes[length] = uint32Be(huff, table2Offset + (length - 1) * 8);
      maxCodes[length] = uint32Be(huff, table2Offset + (length - 1) * 8 + 4);
    }

    final dictionary = <_HuffEntry>[];
    int? codeLength;
    int? expectedEntries;
    for (var recordIndex = 1; recordIndex < huffCount; recordIndex++) {
      final cdic = database.record(absoluteHuff + recordIndex);
      requireRange(cdic, 0, 16);
      if (ascii.decode(cdic.sublist(0, 4)) != 'CDIC') {
        throw const FormatException('Invalid CDIC header');
      }
      final headerLength = uint32Be(cdic, 4);
      final entries = uint32Be(cdic, 8);
      final bits = uint32Be(cdic, 12);
      if (headerLength < 16 || bits < 1 || bits > 16 || entries < 1) {
        throw const FormatException('Invalid CDIC table metadata');
      }
      codeLength ??= bits;
      expectedEntries ??= entries;
      if (codeLength != bits || expectedEntries != entries) {
        throw const FormatException('Inconsistent CDIC table metadata');
      }
      final count = (1 << bits).clamp(0, entries - dictionary.length);
      final bufferLength = cdic.length - headerLength;
      requireRange(cdic, headerLength, count * 2);
      for (var index = 0; index < count; index++) {
        final offset = uint16Be(cdic, headerLength + index * 2);
        if (offset + 2 > bufferLength) {
          throw const FormatException('CDIC phrase offset is invalid');
        }
        final phraseOffset = headerLength + offset;
        final descriptor = uint16Be(cdic, phraseOffset);
        final length = descriptor & 0x7fff;
        requireRange(cdic, phraseOffset + 2, length);
        dictionary.add(
          _HuffEntry(
            Uint8List.sublistView(
              cdic,
              phraseOffset + 2,
              phraseOffset + 2 + length,
            ),
            expanded: (descriptor & 0x8000) != 0,
          ),
        );
      }
    }
    if (dictionary.length != expectedEntries || codeLength == null) {
      throw const FormatException('CDIC dictionary is incomplete');
    }
    return _HuffCdicDecoder(
      table1: table1,
      minCodes: minCodes,
      maxCodes: maxCodes,
      dictionary: dictionary,
      limits: limits,
    );
  }

  final List<_HuffPrefix> table1;
  final List<int> minCodes;
  final List<int> maxCodes;
  final List<_HuffEntry> dictionary;
  final FormatLimits limits;

  Uint8List decompress(Uint8List input) => _decompress(input, 0);

  Uint8List _decompress(Uint8List input, int depth) {
    if (depth > 32) {
      throw const FormatException('HUFF/CDIC recursion is too deep');
    }
    final output = BytesBuilder(copy: false);
    final bitLength = input.length * 8;
    var bitOffset = 0;
    while (bitOffset < bitLength) {
      final bits = _read32Bits(input, bitOffset);
      final prefix = table1[bits >> 24];
      var codeLength = prefix.length;
      var maximum = prefix.maxCode;
      if (!prefix.terminal) {
        while (codeLength <= 32 &&
            (bits >> (32 - codeLength)) < minCodes[codeLength]) {
          codeLength++;
        }
        if (codeLength > 32) throw const FormatException('Invalid HUFF code');
        maximum = maxCodes[codeLength];
      }
      bitOffset += codeLength;
      if (bitOffset > bitLength) break;
      final code = maximum - (bits >> (32 - codeLength));
      if (code < 0 || code >= dictionary.length) {
        throw const FormatException('HUFF dictionary index is invalid');
      }
      final entry = dictionary[code];
      if (!entry.expanded) {
        entry.bytes = _decompress(entry.bytes, depth + 1);
        entry.expanded = true;
      }
      output.add(entry.bytes);
      if (output.length > limits.maxExpandedBytes) {
        throw const CoreException(
          CoreErrorCode.payloadTooLarge,
          'HUFF/CDIC output exceeds the configured expansion limit',
        );
      }
    }
    return output.takeBytes();
  }

  int _read32Bits(Uint8List input, int bitOffset) {
    final start = bitOffset >> 3;
    final shift = bitOffset & 7;
    var value = 0;
    for (var index = 0; index < 5; index++) {
      value =
          (value << 8) |
          (start + index < input.length ? input[start + index] : 0);
    }
    return (value >> (8 - shift)) & 0xffffffff;
  }
}

class _HuffPrefix {
  const _HuffPrefix({
    required this.terminal,
    required this.length,
    required this.maxCode,
  });

  final bool terminal;
  final int length;
  final int maxCode;
}

class _HuffEntry {
  _HuffEntry(this.bytes, {required this.expanded});

  Uint8List bytes;
  bool expanded;
}

class _IndexData {
  const _IndexData(this.entries, this.cncx);

  factory _IndexData.parse(
    _PalmDatabase database,
    _KindleHeader header,
    int relativeIndex,
  ) {
    if (relativeIndex == 0xffffffff || relativeIndex < 0) {
      throw const FormatException('KF8 index is not present');
    }
    final absoluteIndex = header.baseRecord + relativeIndex;
    final root = database.record(absoluteIndex);
    final rootHeader = _IndxHeader.parse(root);
    requireRange(root, rootHeader.length, 12);
    if (ascii.decode(root.sublist(rootHeader.length, rootHeader.length + 4)) !=
        'TAGX') {
      throw const FormatException('Missing KF8 TAGX section');
    }
    final tagxLength = uint32Be(root, rootHeader.length + 4);
    final controlBytes = uint32Be(root, rootHeader.length + 8);
    if (tagxLength < 12 || (tagxLength - 12) % 4 != 0 || controlBytes < 1) {
      throw const FormatException('Invalid KF8 TAGX section');
    }
    requireRange(root, rootHeader.length, tagxLength);
    final tagTable = <_TagDefinition>[];
    for (
      var cursor = rootHeader.length + 12;
      cursor < rootHeader.length + tagxLength;
      cursor += 4
    ) {
      tagTable.add(
        _TagDefinition(
          tag: root[cursor],
          valuesPerEntry: root[cursor + 1],
          mask: root[cursor + 2],
          end: root[cursor + 3],
        ),
      );
    }

    final cncx = <int, String>{};
    var cncxOffset = 0;
    for (var index = 0; index < rootHeader.cncxCount; index++) {
      final record = database.record(
        absoluteIndex + rootHeader.recordCount + index + 1,
      );
      var cursor = 0;
      while (cursor < record.length) {
        if (record[cursor] == 0 &&
            record.sublist(cursor).every((byte) => byte == 0)) {
          break;
        }
        final key = cursor;
        final length = _readVarInt(record, cursor);
        cursor += length.bytesRead;
        requireRange(record, cursor, length.value);
        cncx[cncxOffset + key] = utf8.decode(
          record.sublist(cursor, cursor + length.value),
          allowMalformed: true,
        );
        cursor += length.value;
      }
      cncxOffset += 0x10000;
    }

    final entries = <_IndexEntry>[];
    for (
      var recordIndex = 0;
      recordIndex < rootHeader.recordCount;
      recordIndex++
    ) {
      final record = database.record(absoluteIndex + recordIndex + 1);
      final indexHeader = _IndxHeader.parse(record);
      for (
        var entryIndex = 0;
        entryIndex < indexHeader.recordCount;
        entryIndex++
      ) {
        final offsetPosition = indexHeader.idxtOffset + 4 + entryIndex * 2;
        final offset = uint16Be(record, offsetPosition);
        requireRange(record, offset, 1);
        final nameLength = record[offset];
        requireRange(record, offset + 1, nameLength);
        final name = utf8.decode(
          record.sublist(offset + 1, offset + 1 + nameLength),
          allowMalformed: true,
        );
        final controlStart = offset + 1 + nameLength;
        requireRange(record, controlStart, controlBytes);
        var valueCursor = controlStart + controlBytes;
        var controlIndex = 0;
        final tags = <int, List<int>>{};
        for (final definition in tagTable) {
          if ((definition.end & 1) != 0) {
            controlIndex++;
            continue;
          }
          if (controlIndex >= controlBytes) {
            throw const FormatException('KF8 tag control byte is missing');
          }
          final masked = record[controlStart + controlIndex] & definition.mask;
          int? valueCount;
          int? encodedBytes;
          if (masked == definition.mask) {
            if (_bitCount(definition.mask) > 1) {
              final encoded = _readVarInt(record, valueCursor);
              valueCursor += encoded.bytesRead;
              encodedBytes = encoded.value;
            } else {
              valueCount = 1;
            }
          } else {
            valueCount = masked >> _trailingZeroBits(definition.mask);
          }
          final values = <int>[];
          if (valueCount != null) {
            final count = valueCount * definition.valuesPerEntry;
            for (var index = 0; index < count; index++) {
              final encoded = _readVarInt(record, valueCursor);
              valueCursor += encoded.bytesRead;
              values.add(encoded.value);
            }
          } else if (encodedBytes != null) {
            var consumed = 0;
            while (consumed < encodedBytes) {
              final encoded = _readVarInt(record, valueCursor);
              valueCursor += encoded.bytesRead;
              consumed += encoded.bytesRead;
              values.add(encoded.value);
            }
            if (consumed != encodedBytes) {
              throw const FormatException('KF8 tag value length is invalid');
            }
          }
          if (values.isNotEmpty) tags[definition.tag] = values;
        }
        entries.add(_IndexEntry(name: name, tags: tags));
      }
    }
    return _IndexData(entries, cncx);
  }

  final List<_IndexEntry> entries;
  final Map<int, String> cncx;
}

class _IndxHeader {
  const _IndxHeader({
    required this.length,
    required this.idxtOffset,
    required this.recordCount,
    required this.cncxCount,
  });

  factory _IndxHeader.parse(Uint8List record) {
    requireRange(record, 0, 56);
    if (ascii.decode(record.sublist(0, 4)) != 'INDX') {
      throw const FormatException('Invalid KF8 INDX record');
    }
    final length = uint32Be(record, 4);
    final idxtOffset = uint32Be(record, 20);
    final recordCount = uint32Be(record, 24);
    final cncxCount = uint32Be(record, 52);
    if (length < 56 || length > record.length) {
      throw const FormatException('Invalid KF8 INDX header length');
    }
    return _IndxHeader(
      length: length,
      idxtOffset: idxtOffset,
      recordCount: recordCount,
      cncxCount: cncxCount,
    );
  }

  final int length;
  final int idxtOffset;
  final int recordCount;
  final int cncxCount;
}

class _TagDefinition {
  const _TagDefinition({
    required this.tag,
    required this.valuesPerEntry,
    required this.mask,
    required this.end,
  });

  final int tag;
  final int valuesPerEntry;
  final int mask;
  final int end;
}

class _IndexEntry {
  const _IndexEntry({required this.name, required this.tags});

  final String name;
  final Map<int, List<int>> tags;
}

class _VarInt {
  const _VarInt(this.value, this.bytesRead);

  final int value;
  final int bytesRead;
}

_VarInt _readVarInt(List<int> bytes, int offset) {
  var value = 0;
  for (var index = 0; index < 4; index++) {
    requireRange(bytes, offset + index, 1);
    final byte = bytes[offset + index];
    value = (value << 7) | (byte & 0x7f);
    if ((byte & 0x80) != 0) return _VarInt(value, index + 1);
  }
  throw const FormatException('KF8 variable integer is too long');
}

int _bitCount(int value) {
  var count = 0;
  while (value != 0) {
    count += value & 1;
    value >>= 1;
  }
  return count;
}

int _trailingZeroBits(int value) {
  if (value == 0) throw const FormatException('KF8 tag mask is empty');
  var count = 0;
  while ((value & 1) == 0) {
    count++;
    value >>= 1;
  }
  return count;
}

class _Kf8Skeleton {
  const _Kf8Skeleton({
    required this.fragmentCount,
    required this.offset,
    required this.length,
  });

  final int fragmentCount;
  final int offset;
  final int length;
}

class _Kf8Fragment {
  const _Kf8Fragment({
    required this.insertOffset,
    required this.id,
    required this.offset,
    required this.length,
  });

  final int insertOffset;
  final int id;
  final int offset;
  final int length;
}
