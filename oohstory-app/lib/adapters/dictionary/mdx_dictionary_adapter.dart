import 'dart:convert';
import 'dart:typed_data';

import '../../core/capabilities.dart';
import '../../core/errors.dart';
import '../../core/models.dart';
import '../contracts/adapter_contracts.dart';
import '_zlib_decoder.dart';

class MdxLimits {
  const MdxLimits({
    this.maxInputBytes = 32 * 1024 * 1024,
    this.maxHeaderBytes = 1024 * 1024,
    this.maxEntries = 200000,
    this.maxBlocks = 4096,
    this.maxKeyBytes = 4096,
    this.maxDefinitionBytes = 4 * 1024 * 1024,
    this.maxExpandedBytes = 64 * 1024 * 1024,
  });

  final int maxInputBytes;
  final int maxHeaderBytes;
  final int maxEntries;
  final int maxBlocks;
  final int maxKeyBytes;
  final int maxDefinitionBytes;
  final int maxExpandedBytes;
}

class MdxDictionaryAdapter implements DictionaryAdapter {
  MdxDictionaryAdapter._(this._entries);

  factory MdxDictionaryAdapter.fromBytes(
    Uint8List bytes, {
    MdxLimits limits = const MdxLimits(),
  }) {
    try {
      _validateLimits(limits);
      if (bytes.length > limits.maxInputBytes) {
        throw const CoreException(
          CoreErrorCode.payloadTooLarge,
          'MDX input exceeds the configured size limit',
        );
      }
      final parsed = _MdxParser(bytes, limits).parse();
      return MdxDictionaryAdapter._(parsed);
    } on CoreException {
      rethrow;
    } on Object {
      throw const CoreException(
        CoreErrorCode.validationError,
        'MDX input is malformed',
      );
    }
  }

  final Map<String, List<DictionaryEntry>> _entries;

  @override
  String get providerId => 'local-mdx';

  @override
  ProviderCapabilities get capabilities => ProviderCapabilities(
    providerId: providerId,
    supported: const <AdapterCapability>[AdapterCapability.dictionary],
  );

  int get entryCount =>
      _entries.values.fold<int>(0, (count, entries) => count + entries.length);

  @override
  Future<List<DictionaryEntry>> lookup(String term, {String? locale}) async {
    final normalized = _normalizeTerm(term);
    if (normalized.isEmpty) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Dictionary lookup term must not be blank',
      );
    }
    if (locale != null && locale.trim().isEmpty) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Dictionary locale must not be blank',
      );
    }
    return _entries[normalized] ?? const <DictionaryEntry>[];
  }
}

void _validateLimits(MdxLimits limits) {
  if (limits.maxInputBytes <= 0 ||
      limits.maxHeaderBytes <= 0 ||
      limits.maxEntries <= 0 ||
      limits.maxBlocks <= 0 ||
      limits.maxKeyBytes <= 0 ||
      limits.maxDefinitionBytes <= 0 ||
      limits.maxExpandedBytes <= 0) {
    throw const CoreException(
      CoreErrorCode.validationError,
      'MDX limits must be positive',
    );
  }
}

String _normalizeTerm(String value) => value.trim().toLowerCase();

class _MdxParser {
  _MdxParser(this.bytes, this.limits) : cursor = _Reader(bytes);

  final Uint8List bytes;
  final MdxLimits limits;
  final _Reader cursor;
  late final _TextCodec textCodec;
  var expandedBytes = 0;

  Map<String, List<DictionaryEntry>> parse() {
    _readHeader();
    final keys = _readKeys();
    final records = _readRecords(keys.length);
    if (!cursor.isDone) throw const FormatException('Trailing MDX data');
    if (keys.isEmpty || records.isEmpty) {
      throw const FormatException('MDX dictionary is empty');
    }

    final result = <String, List<DictionaryEntry>>{};
    for (var index = 0; index < keys.length; index++) {
      final start = keys[index].recordOffset;
      final end = index + 1 < keys.length
          ? keys[index + 1].recordOffset
          : records.length;
      if (start < 0 || end < start || end > records.length) {
        throw const FormatException('MDX record offset is invalid');
      }
      if (end - start > limits.maxDefinitionBytes) {
        throw const CoreException(
          CoreErrorCode.payloadTooLarge,
          'MDX definition exceeds the configured size limit',
        );
      }
      final definition = textCodec.decode(records.sublist(start, end));
      final entry = DictionaryEntry(
        term: keys[index].term,
        definition: definition,
      );
      result
          .putIfAbsent(_normalizeTerm(entry.term), () => <DictionaryEntry>[])
          .add(entry);
    }
    return Map<String, List<DictionaryEntry>>.unmodifiable(
      <String, List<DictionaryEntry>>{
        for (final item in result.entries)
          item.key: List<DictionaryEntry>.unmodifiable(item.value),
      },
    );
  }

  void _readHeader() {
    final headerSize = cursor.uint32Be();
    if (headerSize <= 2 || headerSize > limits.maxHeaderBytes) {
      throw const FormatException('MDX header size is invalid');
    }
    final headerBytes = cursor.take(headerSize);
    final checksum = cursor.uint32Be();
    if (_adler32(headerBytes) != checksum) {
      throw const FormatException('MDX header checksum mismatch');
    }
    final header = _decodeUtf16Header(headerBytes);
    final versionText = _attribute(header, 'GeneratedByEngineVersion');
    final version = double.tryParse(versionText);
    if (version == null || version < 2 || version >= 3) {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'Only MDX engine version 2 is supported',
      );
    }
    final encrypted = _attribute(header, 'Encrypted').toLowerCase();
    if (encrypted != 'no' && encrypted != '0' && encrypted != 'false') {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'Encrypted MDX dictionaries are unsupported',
      );
    }
    textCodec = _TextCodec(_attribute(header, 'Encoding'));
  }

  List<_KeyEntry> _readKeys() {
    final headerStart = cursor.offset;
    final keyBlockCount = cursor.uint64Be();
    final entryCount = cursor.uint64Be();
    final keyInfoExpandedSize = cursor.uint64Be();
    final keyInfoSize = cursor.uint64Be();
    final keyBlocksSize = cursor.uint64Be();
    final headerEnd = cursor.offset;
    final headerChecksum = cursor.uint32Be();
    if (_adler32(bytes.sublist(headerStart, headerEnd)) != headerChecksum) {
      throw const FormatException('MDX key header checksum mismatch');
    }
    _validateCount(keyBlockCount, limits.maxBlocks, 'key block');
    _validateCount(entryCount, limits.maxEntries, 'entry');
    _validateBound(keyInfoExpandedSize, limits.maxExpandedBytes);
    _validateBound(keyInfoSize, bytes.length);
    _validateBound(keyBlocksSize, bytes.length);

    final keyInfo = _decodeBlock(
      cursor.take(keyInfoSize),
      expectedSize: keyInfoExpandedSize,
    );
    final blocks = _parseKeyBlockInfo(keyInfo, keyBlockCount, entryCount);
    final keyData = _Reader(cursor.take(keyBlocksSize));
    final entries = <_KeyEntry>[];
    for (final block in blocks) {
      final decoded = _decodeBlock(
        keyData.take(block.compressedSize),
        expectedSize: block.expandedSize,
      );
      final blockCursor = _Reader(decoded);
      for (var index = 0; index < block.entryCount; index++) {
        final offset = blockCursor.uint64Be();
        final termBytes = blockCursor.takeUntil(
          textCodec.terminator,
          maxBytes: limits.maxKeyBytes,
        );
        final term = textCodec.decode(termBytes);
        if (term.trim().isEmpty) throw const FormatException('Blank MDX key');
        entries.add(_KeyEntry(term, offset));
      }
      if (!blockCursor.isDone) {
        throw const FormatException('MDX key block has trailing data');
      }
    }
    if (!keyData.isDone || entries.length != entryCount) {
      throw const FormatException('MDX key count mismatch');
    }
    for (var index = 1; index < entries.length; index++) {
      if (entries[index].recordOffset < entries[index - 1].recordOffset) {
        throw const FormatException('MDX record offsets are not ordered');
      }
    }
    return entries;
  }

  List<_KeyBlock> _parseKeyBlockInfo(
    List<int> data,
    int blockCount,
    int totalEntries,
  ) {
    final reader = _Reader(data);
    final result = <_KeyBlock>[];
    var entries = 0;
    for (var index = 0; index < blockCount; index++) {
      final count = reader.uint64Be();
      _validateCount(count, limits.maxEntries, 'key block entry');
      _skipKeySummary(reader);
      _skipKeySummary(reader);
      final compressed = reader.uint64Be();
      final expanded = reader.uint64Be();
      _validateBound(compressed, bytes.length);
      _validateBound(expanded, limits.maxExpandedBytes);
      entries += count;
      if (entries > totalEntries) {
        throw const FormatException('MDX key block entry count overflow');
      }
      result.add(_KeyBlock(count, compressed, expanded));
    }
    if (!reader.isDone || entries != totalEntries) {
      throw const FormatException('MDX key block metadata mismatch');
    }
    return result;
  }

  void _skipKeySummary(_Reader reader) {
    final units = reader.uint16Be();
    final byteLength = units * textCodec.unitWidth;
    if (byteLength > limits.maxKeyBytes) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'MDX key exceeds the configured size limit',
      );
    }
    reader.take(byteLength);
    reader.take(textCodec.terminator.length);
  }

  List<int> _readRecords(int expectedEntries) {
    final blockCount = cursor.uint64Be();
    final entryCount = cursor.uint64Be();
    final infoSize = cursor.uint64Be();
    final blocksSize = cursor.uint64Be();
    _validateCount(blockCount, limits.maxBlocks, 'record block');
    if (entryCount != expectedEntries) {
      throw const FormatException('MDX record entry count mismatch');
    }
    if (infoSize != blockCount * 16) {
      throw const FormatException('MDX record block metadata size is invalid');
    }
    _validateBound(blocksSize, bytes.length);
    final blocks = <_RecordBlock>[];
    var totalCompressed = 0;
    for (var index = 0; index < blockCount; index++) {
      final compressed = cursor.uint64Be();
      final expanded = cursor.uint64Be();
      _validateBound(compressed, bytes.length);
      _validateBound(expanded, limits.maxExpandedBytes);
      totalCompressed += compressed;
      if (totalCompressed > blocksSize) {
        throw const FormatException('MDX record block size overflow');
      }
      blocks.add(_RecordBlock(compressed, expanded));
    }
    if (totalCompressed != blocksSize) {
      throw const FormatException('MDX record block size mismatch');
    }
    final data = <int>[];
    for (final block in blocks) {
      final decoded = _decodeBlock(
        cursor.take(block.compressedSize),
        expectedSize: block.expandedSize,
      );
      if (decoded.length > limits.maxExpandedBytes - data.length) {
        throw const CoreException(
          CoreErrorCode.payloadTooLarge,
          'MDX expanded data exceeds the configured size limit',
        );
      }
      data.addAll(decoded);
    }
    return data;
  }

  List<int> _decodeBlock(List<int> block, {required int expectedSize}) {
    if (block.length < 8) throw const FormatException('MDX block is truncated');
    if (expectedSize > limits.maxExpandedBytes - expandedBytes) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'MDX expanded data exceeds the configured size limit',
      );
    }
    if (block[1] != 0 || block[2] != 0 || block[3] != 0) {
      throw const FormatException('MDX compression marker is invalid');
    }
    final compression = block[0];
    final checksum = _uint32Be(block, 4);
    final payload = block.sublist(8);
    final decoded = switch (compression) {
      0 => payload,
      2 => decodeZlib(payload, maxOutputBytes: expectedSize),
      1 => throw const CoreException(
        CoreErrorCode.unsupported,
        'LZO-compressed MDX blocks are unsupported',
      ),
      _ => throw const CoreException(
        CoreErrorCode.unsupported,
        'Unknown MDX block compression is unsupported',
      ),
    };
    if (decoded.length != expectedSize || _adler32(decoded) != checksum) {
      throw const FormatException('MDX block size or checksum mismatch');
    }
    expandedBytes += decoded.length;
    return decoded;
  }
}

class _TextCodec {
  _TextCodec(String name) : name = name.toUpperCase() {
    if (this.name != 'UTF-8' &&
        this.name != 'UTF-16' &&
        this.name != 'UTF-16LE') {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'MDX text encoding is unsupported',
      );
    }
  }

  final String name;
  int get unitWidth => name == 'UTF-8' ? 1 : 2;
  List<int> get terminator =>
      name == 'UTF-8' ? const <int>[0] : const <int>[0, 0];

  String decode(List<int> bytes) {
    if (name == 'UTF-8') return utf8.decode(bytes, allowMalformed: false);
    if (bytes.length.isOdd) {
      throw const FormatException('Invalid UTF-16LE text');
    }
    final units = <int>[];
    for (var index = 0; index < bytes.length; index += 2) {
      units.add(bytes[index] | (bytes[index + 1] << 8));
    }
    return String.fromCharCodes(units);
  }
}

class _Reader {
  _Reader(this.bytes);

  final List<int> bytes;
  int offset = 0;
  bool get isDone => offset == bytes.length;

  int uint16Be() {
    final data = take(2);
    return (data[0] << 8) | data[1];
  }

  int uint32Be() {
    final value = _uint32Be(bytes, offset);
    offset += 4;
    return value;
  }

  int uint64Be() {
    final data = take(8);
    var value = 0;
    for (final byte in data) {
      value = (value << 8) | byte;
    }
    return value;
  }

  List<int> take(int length) {
    if (length < 0 || length > bytes.length - offset) {
      throw const FormatException('Unexpected end of MDX input');
    }
    final result = bytes.sublist(offset, offset + length);
    offset += length;
    return result;
  }

  List<int> takeUntil(List<int> delimiter, {required int maxBytes}) {
    final start = offset;
    while (offset + delimiter.length <= bytes.length) {
      var matches = true;
      for (var index = 0; index < delimiter.length; index++) {
        if (bytes[offset + index] != delimiter[index]) {
          matches = false;
          break;
        }
      }
      if (matches) {
        final result = bytes.sublist(start, offset);
        offset += delimiter.length;
        return result;
      }
      offset += delimiter.length;
      if (offset - start > maxBytes) {
        throw const CoreException(
          CoreErrorCode.payloadTooLarge,
          'MDX key exceeds the configured size limit',
        );
      }
    }
    throw const FormatException('MDX key terminator is missing');
  }
}

class _KeyEntry {
  const _KeyEntry(this.term, this.recordOffset);
  final String term;
  final int recordOffset;
}

class _KeyBlock {
  const _KeyBlock(this.entryCount, this.compressedSize, this.expandedSize);
  final int entryCount;
  final int compressedSize;
  final int expandedSize;
}

class _RecordBlock {
  const _RecordBlock(this.compressedSize, this.expandedSize);
  final int compressedSize;
  final int expandedSize;
}

String _decodeUtf16Header(List<int> bytes) {
  if (bytes.length.isOdd) throw const FormatException('Invalid MDX header');
  var littleEndian = true;
  var start = 0;
  if (bytes.length >= 2 && bytes[0] == 0xfe && bytes[1] == 0xff) {
    littleEndian = false;
    start = 2;
  } else if (bytes.length >= 2 && bytes[0] == 0xff && bytes[1] == 0xfe) {
    start = 2;
  }
  final units = <int>[];
  for (var index = start; index < bytes.length; index += 2) {
    final unit = littleEndian
        ? bytes[index] | (bytes[index + 1] << 8)
        : (bytes[index] << 8) | bytes[index + 1];
    if (unit != 0) units.add(unit);
  }
  return String.fromCharCodes(units);
}

String _attribute(String header, String name) {
  if (!header.trimLeft().startsWith('<Dictionary') ||
      !header.trimRight().endsWith('/>')) {
    throw const FormatException('MDX XML header is invalid');
  }
  final matches = RegExp(
    '$name\\s*=\\s*["\\\']([^"\\\']*)["\\\']',
    caseSensitive: false,
  ).allMatches(header).toList(growable: false);
  if (matches.length != 1) {
    throw FormatException('Invalid MDX header attribute: $name');
  }
  return matches.single.group(1)!;
}

void _validateCount(int value, int max, String label) {
  if (value <= 0 || value > max) {
    throw FormatException('MDX $label count is invalid');
  }
}

void _validateBound(int value, int max) {
  if (value < 0 || value > max) {
    throw const CoreException(
      CoreErrorCode.payloadTooLarge,
      'MDX declared size exceeds the configured limit',
    );
  }
}

int _uint32Be(List<int> bytes, int offset) {
  if (offset < 0 || offset + 4 > bytes.length) {
    throw const FormatException('Unexpected end of MDX input');
  }
  return ((bytes[offset] << 24) |
          (bytes[offset + 1] << 16) |
          (bytes[offset + 2] << 8) |
          bytes[offset + 3]) &
      0xffffffff;
}

int _adler32(List<int> bytes) {
  const modulus = 65521;
  var first = 1;
  var second = 0;
  for (final byte in bytes) {
    first = (first + byte) % modulus;
    second = (second + first) % modulus;
  }
  return ((second << 16) | first) & 0xffffffff;
}
