import 'dart:convert';
import 'dart:typed_data';

import '../../core/capabilities.dart';
import '../../core/errors.dart';
import '../../core/models.dart';
import '../contracts/adapter_contracts.dart';
import '_binary.dart';
import 'format_limits.dart';

class ComicArchiveFormatDecoder implements FormatDecoder {
  const ComicArchiveFormatDecoder({this.limits = const FormatLimits()});

  static const List<int> _rar4Signature = <int>[
    0x52,
    0x61,
    0x72,
    0x21,
    0x1a,
    0x07,
    0x00,
  ];
  static const List<int> _rar5Signature = <int>[
    0x52,
    0x61,
    0x72,
    0x21,
    0x1a,
    0x07,
    0x01,
    0x00,
  ];
  static const List<int> _sevenZipSignature = <int>[
    0x37,
    0x7a,
    0xbc,
    0xaf,
    0x27,
    0x1c,
  ];
  static const Set<String> _mediaTypes = <String>{
    'application/vnd.comicbook-rar',
    'application/x-cbr',
    'application/vnd.comicbook+tar',
    'application/x-cbt',
    'application/vnd.comicbook+7z',
    'application/x-cb7',
    'application/x-7z-compressed',
  };
  static const Set<String> _imageExtensions = <String>{
    'avif',
    'bmp',
    'gif',
    'jpeg',
    'jpg',
    'png',
    'webp',
  };

  final FormatLimits limits;

  @override
  String get providerId => 'comic-archive-safe';

  @override
  ProviderCapabilities get capabilities => ProviderCapabilities(
    providerId: providerId,
    supported: const <AdapterCapability>[AdapterCapability.comicDecoding],
  );

  @override
  Future<bool> probe(String mediaType, List<int> header) async {
    final normalized = mediaType.toLowerCase().split(';').first.trim();
    return _mediaTypes.contains(normalized) ||
        _startsWith(header, _rar4Signature) ||
        _startsWith(header, _rar5Signature) ||
        _startsWith(header, _sevenZipSignature) ||
        _isTar(header);
  }

  @override
  Future<DecodedDocument> decode(Stream<List<int>> bytes) async {
    final data = await collectFormatBytes(
      bytes,
      maxBytes: limits.maxInputBytes,
    );
    try {
      final result = _startsWith(data, _rar4Signature)
          ? _parseRar4(data)
          : _startsWith(data, _rar5Signature)
          ? _parseRar5(data)
          : _startsWith(data, _sevenZipSignature)
          ? _parseSevenZip(data)
          : _isTar(data)
          ? _parseTar(data)
          : throw const FormatException('Unknown comic archive format');
      _validateExpansion(result.expandedBytes, data.length);
      final pages = result.entries
          .where((entry) => _isImage(entry.name))
          .map((entry) => entry.name)
          .toList();
      pages.sort(_naturalCompare);
      if (pages.isEmpty) {
        throw const CoreException(
          CoreErrorCode.validationError,
          'Comic archive contains no supported image pages',
        );
      }
      if (pages.length > limits.maxPages) {
        throw const CoreException(
          CoreErrorCode.payloadTooLarge,
          'Comic archive exceeds the configured page limit',
        );
      }
      return DecodedDocument(version: result.version, sections: pages);
    } on CoreException {
      rethrow;
    } on Object {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Comic archive is malformed',
      );
    }
  }

  _ArchiveResult _parseTar(Uint8List bytes) {
    final entries = <_ArchiveEntry>[];
    var offset = 0;
    var expanded = 0;
    var archiveEntries = 0;
    while (offset + 512 <= bytes.length) {
      final header = bytes.sublist(offset, offset + 512);
      if (header.every((byte) => byte == 0)) break;
      _verifyTarChecksum(header);
      final type = header[156];
      if (const <int>{0x4c, 0x4b, 0x78, 0x67}.contains(type)) {
        throw const CoreException(
          CoreErrorCode.unsupported,
          'Extended TAR path records are not supported',
        );
      }
      final name = _tarText(header, 0, 100);
      final prefix = _tarText(header, 345, 155);
      final path = prefix.isEmpty ? name : '$prefix/$name';
      final sizeText = _tarText(header, 124, 12).trim();
      final size = sizeText.isEmpty ? 0 : int.parse(sizeText, radix: 8);
      final dataOffset = offset + 512;
      final paddedSize = ((size + 511) ~/ 512) * 512;
      requireRange(bytes, dataOffset, paddedSize);
      final isFile = type == 0 || type == 0x30;
      if (path.isNotEmpty) {
        archiveEntries++;
        _checkEntryCount(archiveEntries);
        final safePath = _validatePath(path);
        if (isFile) {
          expanded = _addEntry(entries, safePath, size, expanded);
        }
      }
      offset = dataOffset + paddedSize;
    }
    if (offset > bytes.length) throw const FormatException('TAR is truncated');
    return _ArchiveResult('cbt:tar:${entries.length}', entries, expanded);
  }

  _ArchiveResult _parseRar4(Uint8List bytes) {
    final entries = <_ArchiveEntry>[];
    var expanded = 0;
    var offset = _rar4Signature.length;
    var archiveEntries = 0;
    while (offset < bytes.length) {
      requireRange(bytes, offset, 7);
      final type = bytes[offset + 2];
      final flags = uint16Le(bytes, offset + 3);
      final headerSize = uint16Le(bytes, offset + 5);
      if (headerSize < 7) {
        throw const FormatException('RAR header is too short');
      }
      requireRange(bytes, offset, headerSize);
      if ((crc32(bytes, offset + 2, offset + headerSize) & 0xffff) !=
          uint16Le(bytes, offset)) {
        throw const FormatException('RAR header checksum mismatch');
      }
      if ((flags & 0x8000) != 0 && headerSize < 11) {
        throw const FormatException('RAR data header is too short');
      }
      final dataSize = (flags & 0x8000) != 0 ? uint32Le(bytes, offset + 7) : 0;
      if (type == 0x73 && (flags & 0x0080) != 0) {
        throw const CoreException(
          CoreErrorCode.unsupported,
          'Encrypted RAR archives are not supported',
        );
      }
      if (type == 0x74) {
        archiveEntries++;
        _checkEntryCount(archiveEntries);
        if (headerSize < 32) {
          throw const FormatException('RAR file header is too short');
        }
        if ((flags & 0x0004) != 0) {
          throw const CoreException(
            CoreErrorCode.unsupported,
            'Encrypted RAR entries are not supported',
          );
        }
        final nameSize = uint16Le(bytes, offset + 26);
        var unpackedSize = uint32Le(bytes, offset + 11);
        var nameOffset = offset + 32;
        if ((flags & 0x0100) != 0) {
          if (headerSize < 40) {
            throw const FormatException('Large RAR file header is too short');
          }
          final highPacked = uint32Le(bytes, nameOffset);
          final highUnpacked = uint32Le(bytes, nameOffset + 4);
          if (highPacked != 0) {
            throw const CoreException(
              CoreErrorCode.payloadTooLarge,
              'RAR packed entry exceeds supported size',
            );
          }
          unpackedSize |= highUnpacked << 32;
          nameOffset += 8;
        }
        if (nameOffset + nameSize > offset + headerSize) {
          throw const FormatException('RAR filename exceeds its header');
        }
        final rawName = bytes.sublist(nameOffset, nameOffset + nameSize);
        final zero = rawName.indexOf(0);
        final nameBytes = zero < 0 ? rawName : rawName.sublist(0, zero);
        final name = _validatePath(
          utf8.decode(nameBytes, allowMalformed: true),
        );
        final directory = (flags & 0x00e0) == 0x00e0;
        if (!directory) {
          expanded = _addEntry(entries, name, unpackedSize, expanded);
        }
      }
      final next = offset + headerSize + dataSize;
      if (next <= offset || next > bytes.length) {
        throw const FormatException('RAR block is truncated');
      }
      offset = next;
      if (type == 0x7b) break;
    }
    return _ArchiveResult('cbr:rar4:${entries.length}', entries, expanded);
  }

  _ArchiveResult _parseRar5(Uint8List bytes) {
    final entries = <_ArchiveEntry>[];
    var expanded = 0;
    var offset = _rar5Signature.length;
    var archiveEntries = 0;
    while (offset < bytes.length) {
      requireRange(bytes, offset, 4);
      final header = ByteCursor(bytes, offset: offset + 4);
      final headerSize = header.readRarUint64();
      final headerStart = header.offset;
      final headerEnd = headerStart + headerSize;
      if (headerEnd > bytes.length) {
        throw const FormatException('RAR5 header is truncated');
      }
      if (crc32(bytes, offset + 4, headerEnd) != uint32Le(bytes, offset)) {
        throw const FormatException('RAR5 header checksum mismatch');
      }
      final fields = ByteCursor(bytes, offset: headerStart, end: headerEnd);
      final type = fields.readRarUint64();
      final flags = fields.readRarUint64();
      final extraSize = (flags & 0x0001) != 0 ? fields.readRarUint64() : 0;
      final dataSize = (flags & 0x0002) != 0 ? fields.readRarUint64() : 0;
      if (extraSize > headerEnd - fields.offset) {
        throw const FormatException('RAR5 extra area is invalid');
      }
      if (type == 4) {
        throw const CoreException(
          CoreErrorCode.unsupported,
          'Encrypted RAR archives are not supported',
        );
      }
      if (type == 2) {
        archiveEntries++;
        _checkEntryCount(archiveEntries);
        final fileFields = ByteCursor(
          bytes,
          offset: fields.offset,
          end: headerEnd - extraSize,
        );
        final fileFlags = fileFields.readRarUint64();
        final unpackedSize = fileFields.readRarUint64();
        fileFields.readRarUint64();
        if ((fileFlags & 0x0002) != 0) fileFields.skip(4);
        if ((fileFlags & 0x0004) != 0) fileFields.skip(4);
        fileFields.readRarUint64();
        fileFields.readRarUint64();
        final nameSize = fileFields.readRarUint64();
        final name = _validatePath(utf8.decode(fileFields.readBytes(nameSize)));
        if (extraSize > 0) {
          _rejectEncryptedRar5Extra(bytes, headerEnd - extraSize, headerEnd);
        }
        if ((fileFlags & 0x0001) == 0) {
          expanded = _addEntry(entries, name, unpackedSize, expanded);
        }
      }
      final next = headerEnd + dataSize;
      if (next <= offset || next > bytes.length) {
        throw const FormatException('RAR5 block is truncated');
      }
      offset = next;
      if (type == 5) break;
    }
    return _ArchiveResult('cbr:rar5:${entries.length}', entries, expanded);
  }

  void _rejectEncryptedRar5Extra(List<int> bytes, int start, int end) {
    final extras = ByteCursor(bytes, offset: start, end: end);
    while (extras.offset < extras.end) {
      final recordSize = extras.readRarUint64();
      final recordEnd = extras.offset + recordSize;
      if (recordEnd <= extras.offset || recordEnd > end) {
        throw const FormatException('RAR5 extra record is invalid');
      }
      final type = extras.readRarUint64();
      if (extras.offset > recordEnd) {
        throw const FormatException('RAR5 extra record type is invalid');
      }
      if (type == 1) {
        throw const CoreException(
          CoreErrorCode.unsupported,
          'Encrypted RAR entries are not supported',
        );
      }
      extras.offset = recordEnd;
    }
  }

  _ArchiveResult _parseSevenZip(Uint8List bytes) {
    requireRange(bytes, 0, 32);
    final startHeaderCrc = uint32Le(bytes, 8);
    if (crc32(bytes, 12, 32) != startHeaderCrc) {
      throw const FormatException('7z start header checksum mismatch');
    }
    final nextOffset = uint64Le(bytes, 12);
    final nextSize = uint64Le(bytes, 20);
    final nextCrc = uint32Le(bytes, 28);
    final nextStart = 32 + nextOffset;
    if (nextOffset > bytes.length || nextSize > bytes.length) {
      throw const FormatException('7z next header is out of range');
    }
    requireRange(bytes, nextStart, nextSize);
    if (crc32(bytes, nextStart, nextStart + nextSize) != nextCrc) {
      throw const FormatException('7z next header checksum mismatch');
    }
    final header = ByteCursor(
      bytes,
      offset: nextStart,
      end: nextStart + nextSize,
    );
    final marker = header.readByte();
    if (marker == 0x17) {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'Encoded or encrypted 7z headers are not supported',
      );
    }
    if (marker != 0x01) {
      throw const FormatException('7z header marker is invalid');
    }
    List<String>? names;
    var expanded = 0;
    while (header.offset < header.end) {
      final id = header.readByte();
      if (id == 0x00) break;
      switch (id) {
        case 0x02:
          _skip7zArchiveProperties(header);
        case 0x03:
          throw const CoreException(
            CoreErrorCode.unsupported,
            'Additional 7z streams are not supported',
          );
        case 0x04:
          expanded = _parse7zStreamsInfo(header);
        case 0x05:
          names = _parse7zFilesInfo(header);
        default:
          throw const FormatException('Unknown 7z header section');
      }
    }
    if (names == null) throw const FormatException('7z file list is missing');
    final entries = <_ArchiveEntry>[];
    for (final name in names) {
      final safePath = _validatePath(name);
      if (!safePath.endsWith('/')) {
        _addEntry(entries, safePath, 0, 0, enforceExpanded: false);
      }
    }
    return _ArchiveResult('cb7:7z:${entries.length}', entries, expanded);
  }

  void _skip7zArchiveProperties(ByteCursor header) {
    while (true) {
      final id = header.readByte();
      if (id == 0x00) return;
      header.skip(header.read7zUint64());
    }
  }

  int _parse7zStreamsInfo(ByteCursor header) {
    var expanded = 0;
    _SevenZipUnpackInfo? unpackInfo;
    while (true) {
      final id = header.readByte();
      if (id == 0x00) return expanded;
      switch (id) {
        case 0x06:
          _skip7zPackInfo(header);
        case 0x07:
          unpackInfo = _parse7zUnpackInfo(header);
          expanded = unpackInfo.expandedBytes;
        case 0x08:
          if (unpackInfo == null) {
            throw const FormatException('7z substreams precede folders');
          }
          _skip7zSubStreamsInfo(header, unpackInfo);
        default:
          throw const FormatException('Unknown 7z streams section');
      }
    }
  }

  void _skip7zPackInfo(ByteCursor header) {
    header.read7zUint64();
    final streams = header.read7zUint64();
    while (true) {
      final id = header.readByte();
      if (id == 0x00) return;
      if (id == 0x09) {
        for (var index = 0; index < streams; index++) {
          header.read7zUint64();
        }
      } else if (id == 0x0a) {
        _skip7zDigests(header, streams);
      } else {
        throw const FormatException('Unknown 7z pack property');
      }
    }
  }

  _SevenZipUnpackInfo _parse7zUnpackInfo(ByteCursor header) {
    if (header.readByte() != 0x0b) {
      throw const FormatException('7z folder section is missing');
    }
    final folders = header.read7zUint64();
    if (header.readByte() != 0) {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'External 7z folder data is not supported',
      );
    }
    final outputCounts = <int>[];
    for (var index = 0; index < folders; index++) {
      outputCounts.add(_parse7zFolder(header));
    }
    if (header.readByte() != 0x0c) {
      throw const FormatException('7z unpack sizes are missing');
    }
    var expanded = 0;
    for (final outputs in outputCounts) {
      for (var index = 0; index < outputs; index++) {
        expanded += header.read7zUint64();
        if (expanded > limits.maxExpandedBytes) {
          throw const CoreException(
            CoreErrorCode.payloadTooLarge,
            'Comic archive exceeds the configured expansion limit',
          );
        }
      }
    }
    var folderCrcDefined = List<bool>.filled(folders, false);
    final next = header.readByte();
    if (next == 0x0a) {
      folderCrcDefined = _skip7zDigests(header, folders);
      if (header.readByte() != 0x00) {
        throw const FormatException('7z unpack section is not terminated');
      }
    } else if (next != 0x00) {
      throw const FormatException('Unknown 7z unpack property');
    }
    return _SevenZipUnpackInfo(expanded, folderCrcDefined);
  }

  int _parse7zFolder(ByteCursor header) {
    final coders = header.read7zUint64();
    var totalInputs = 0;
    var totalOutputs = 0;
    for (var index = 0; index < coders; index++) {
      final flags = header.readByte();
      final codec = header.readBytes(flags & 0x0f);
      if (_sameBytes(codec, const <int>[0x06, 0xf1, 0x07, 0x01])) {
        throw const CoreException(
          CoreErrorCode.unsupported,
          'Encrypted 7z archives are not supported',
        );
      }
      var inputs = 1;
      var outputs = 1;
      if ((flags & 0x10) != 0) {
        inputs = header.read7zUint64();
        outputs = header.read7zUint64();
      }
      if ((flags & 0x20) != 0) header.skip(header.read7zUint64());
      if ((flags & 0x80) != 0) {
        throw const CoreException(
          CoreErrorCode.unsupported,
          'Alternative 7z coder methods are not supported',
        );
      }
      totalInputs += inputs;
      totalOutputs += outputs;
    }
    final bindPairs = totalOutputs - 1;
    for (var index = 0; index < bindPairs; index++) {
      header
        ..read7zUint64()
        ..read7zUint64();
    }
    final packedStreams = totalInputs - bindPairs;
    if (packedStreams > 1) {
      for (var index = 0; index < packedStreams; index++) {
        header.read7zUint64();
      }
    }
    return totalOutputs;
  }

  void _skip7zSubStreamsInfo(
    ByteCursor header,
    _SevenZipUnpackInfo unpackInfo,
  ) {
    final streams = List<int>.filled(unpackInfo.folderCrcDefined.length, 1);
    var id = header.readByte();
    if (id == 0x0d) {
      for (var index = 0; index < streams.length; index++) {
        streams[index] = header.read7zUint64();
      }
      id = header.readByte();
    }
    if (id == 0x09) {
      for (final count in streams) {
        for (var index = 1; index < count; index++) {
          header.read7zUint64();
        }
      }
      id = header.readByte();
    }
    if (id == 0x0a) {
      var digests = 0;
      for (var index = 0; index < streams.length; index++) {
        if (streams[index] != 1 || !unpackInfo.folderCrcDefined[index]) {
          digests += streams[index];
        }
      }
      _skip7zDigests(header, digests);
      id = header.readByte();
    }
    if (id != 0x00) {
      throw const FormatException('Unknown 7z substreams property');
    }
  }

  List<bool> _skip7zDigests(ByteCursor header, int count) {
    final allDefined = header.readByte();
    final definitions = List<bool>.filled(count, allDefined != 0);
    if (allDefined == 0) {
      for (var index = 0; index < (count + 7) ~/ 8; index++) {
        final bits = header.readByte();
        for (var bit = 0; bit < 8 && index * 8 + bit < count; bit++) {
          definitions[index * 8 + bit] = (bits & (0x80 >> bit)) != 0;
        }
      }
    }
    header.skip(definitions.where((defined) => defined).length * 4);
    return definitions;
  }

  List<String> _parse7zFilesInfo(ByteCursor header) {
    final count = header.read7zUint64();
    if (count > limits.maxEntries) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Comic archive exceeds the configured entry limit',
      );
    }
    List<String>? names;
    while (true) {
      final property = header.readByte();
      if (property == 0x00) break;
      final size = header.read7zUint64();
      final end = header.offset + size;
      if (end > header.end) {
        throw const FormatException('7z file property is truncated');
      }
      if (property == 0x11) {
        if (header.readByte() != 0) {
          throw const CoreException(
            CoreErrorCode.unsupported,
            'External 7z filenames are not supported',
          );
        }
        final units = <int>[];
        final decoded = <String>[];
        while (header.offset < end) {
          final low = header.readByte();
          final high = header.readByte();
          final unit = low | (high << 8);
          if (unit == 0) {
            decoded.add(String.fromCharCodes(units));
            units.clear();
          } else {
            units.add(unit);
          }
        }
        if (units.isNotEmpty || decoded.length != count) {
          throw const FormatException('7z filename table is invalid');
        }
        names = decoded;
      }
      header.offset = end;
    }
    if (names == null) throw const FormatException('7z filenames are missing');
    return names;
  }

  int _addEntry(
    List<_ArchiveEntry> entries,
    String name,
    int size,
    int expanded, {
    bool enforceExpanded = true,
  }) {
    if (entries.length >= limits.maxEntries) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Comic archive exceeds the configured entry limit',
      );
    }
    if (size < 0 || size > limits.maxEntryBytes) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Comic archive entry exceeds the configured size limit',
      );
    }
    final total = expanded + size;
    if (enforceExpanded && total > limits.maxExpandedBytes) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Comic archive exceeds the configured expansion limit',
      );
    }
    entries.add(_ArchiveEntry(name, size));
    return total;
  }

  void _checkEntryCount(int count) {
    if (count > limits.maxEntries) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Comic archive exceeds the configured entry limit',
      );
    }
  }

  void _validateExpansion(int expanded, int compressed) {
    if (expanded > limits.maxExpandedBytes) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Comic archive exceeds the configured expansion limit',
      );
    }
    if (compressed > 0 && expanded > compressed * limits.maxExpansionRatio) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Comic archive exceeds the configured expansion ratio',
      );
    }
  }

  String _validatePath(String path) {
    if (path.isEmpty || path.contains('\u0000')) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Comic archive contains an invalid path',
      );
    }
    final normalized = path.replaceAll('\\', '/');
    final segments = normalized.split('/');
    if (normalized.startsWith('/') ||
        RegExp(r'^[A-Za-z]:').hasMatch(normalized) ||
        segments.any((segment) => segment == '..')) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Comic archive path traversal is not allowed',
      );
    }
    return normalized;
  }

  bool _isImage(String path) {
    final normalized = path.replaceAll('\\', '/');
    final name = normalized.split('/').last;
    final dot = name.lastIndexOf('.');
    return dot > 0 &&
        _imageExtensions.contains(name.substring(dot + 1).toLowerCase());
  }

  int _naturalCompare(String left, String right) {
    final leftLower = left.toLowerCase();
    final rightLower = right.toLowerCase();
    var leftIndex = 0;
    var rightIndex = 0;
    while (leftIndex < leftLower.length && rightIndex < rightLower.length) {
      final leftDigit = _digit(leftLower.codeUnitAt(leftIndex));
      final rightDigit = _digit(rightLower.codeUnitAt(rightIndex));
      if (leftDigit && rightDigit) {
        final leftEnd = _digitEnd(leftLower, leftIndex);
        final rightEnd = _digitEnd(rightLower, rightIndex);
        final compared = _compareDigits(
          leftLower.substring(leftIndex, leftEnd),
          rightLower.substring(rightIndex, rightEnd),
        );
        if (compared != 0) return compared;
        leftIndex = leftEnd;
        rightIndex = rightEnd;
      } else {
        final compared = leftLower
            .codeUnitAt(leftIndex)
            .compareTo(rightLower.codeUnitAt(rightIndex));
        if (compared != 0) return compared;
        leftIndex++;
        rightIndex++;
      }
    }
    final lengthOrder = leftLower.length.compareTo(rightLower.length);
    return lengthOrder != 0 ? lengthOrder : left.compareTo(right);
  }

  int _compareDigits(String left, String right) {
    final leftValue = left.replaceFirst(RegExp(r'^0+'), '');
    final rightValue = right.replaceFirst(RegExp(r'^0+'), '');
    final leftNormalized = leftValue.isEmpty ? '0' : leftValue;
    final rightNormalized = rightValue.isEmpty ? '0' : rightValue;
    final lengthOrder = leftNormalized.length.compareTo(rightNormalized.length);
    if (lengthOrder != 0) return lengthOrder;
    final valueOrder = leftNormalized.compareTo(rightNormalized);
    if (valueOrder != 0) return valueOrder;
    return left.length.compareTo(right.length);
  }

  int _digitEnd(String value, int start) {
    var end = start;
    while (end < value.length && _digit(value.codeUnitAt(end))) {
      end++;
    }
    return end;
  }

  bool _digit(int codeUnit) => codeUnit >= 0x30 && codeUnit <= 0x39;

  bool _isTar(List<int> bytes) =>
      bytes.length >= 512 && ascii.decode(bytes.sublist(257, 262)) == 'ustar';

  String _tarText(List<int> bytes, int offset, int length) {
    final field = bytes.sublist(offset, offset + length);
    final zero = field.indexOf(0);
    return utf8.decode(zero < 0 ? field : field.sublist(0, zero));
  }

  void _verifyTarChecksum(List<int> header) {
    final expectedText = _tarText(header, 148, 8).trim();
    final expected = int.parse(expectedText, radix: 8);
    var actual = 0;
    for (var index = 0; index < header.length; index++) {
      actual += index >= 148 && index < 156 ? 0x20 : header[index];
    }
    if (actual != expected) {
      throw const FormatException('TAR checksum mismatch');
    }
  }

  bool _startsWith(List<int> bytes, List<int> signature) =>
      bytes.length >= signature.length &&
      _sameBytes(bytes.sublist(0, signature.length), signature);

  bool _sameBytes(List<int> left, List<int> right) {
    if (left.length != right.length) return false;
    for (var index = 0; index < left.length; index++) {
      if (left[index] != right[index]) return false;
    }
    return true;
  }
}

class _ArchiveEntry {
  const _ArchiveEntry(this.name, this.expandedSize);

  final String name;
  final int expandedSize;
}

class _ArchiveResult {
  const _ArchiveResult(this.version, this.entries, this.expandedBytes);

  final String version;
  final List<_ArchiveEntry> entries;
  final int expandedBytes;
}

class _SevenZipUnpackInfo {
  const _SevenZipUnpackInfo(this.expandedBytes, this.folderCrcDefined);

  final int expandedBytes;
  final List<bool> folderCrcDefined;
}
