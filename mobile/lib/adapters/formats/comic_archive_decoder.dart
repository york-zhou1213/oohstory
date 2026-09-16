import 'dart:convert';
import 'dart:typed_data';

import 'package:archive/archive.dart';

import '../../core/capabilities.dart';
import '../../core/errors.dart';
import '../../core/models.dart';
import '../contracts/adapter_contracts.dart';
import '_binary.dart';
import '_seven_zip_lzma.dart';
import 'format_limits.dart';

class DecodedComicPage {
  const DecodedComicPage({required this.name, required this.bytes});

  final String name;
  final Uint8List bytes;
}

class DecodedComicArchive {
  const DecodedComicArchive({required this.document, required this.pages});

  final DecodedDocument document;
  final List<DecodedComicPage> pages;
}

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
  Future<DecodedDocument> decode(Stream<List<int>> bytes) async =>
      (await decodeArchive(bytes)).document;

  Future<DecodedComicArchive> decodeArchive(Stream<List<int>> bytes) async {
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
          .toList();
      pages.sort((left, right) => _naturalCompare(left.name, right.name));
      if (pages.any((entry) => entry.bytes == null)) {
        throw const CoreException(
          CoreErrorCode.unsupported,
          'Comic archive pages could not be extracted',
        );
      }
      final pageNames = pages.map((entry) => entry.name).toList();
      if (pageNames.isEmpty) {
        throw const CoreException(
          CoreErrorCode.validationError,
          'Comic archive contains no supported image pages',
        );
      }
      if (pageNames.length > limits.maxPages) {
        throw const CoreException(
          CoreErrorCode.payloadTooLarge,
          'Comic archive exceeds the configured page limit',
        );
      }
      return DecodedComicArchive(
        document: DecodedDocument(version: result.version, sections: pageNames),
        pages: <DecodedComicPage>[
          for (final page in pages)
            DecodedComicPage(name: page.name, bytes: page.bytes!),
        ],
      );
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
          expanded = _addEntry(
            entries,
            safePath,
            size,
            expanded,
            bytes: Uint8List.fromList(
              bytes.sublist(dataOffset, dataOffset + size),
            ),
          );
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
          _validateEntrySize(unpackedSize);
          final method = bytes[offset + 25];
          if (method != 0x30 || dataSize != unpackedSize) {
            throw const CoreException(
              CoreErrorCode.unsupported,
              'Compressed RAR4 entries are not supported; use stored method',
            );
          }
          final dataOffset = offset + headerSize;
          requireRange(bytes, dataOffset, dataSize);
          final entryBytes = Uint8List.fromList(
            bytes.sublist(dataOffset, dataOffset + dataSize),
          );
          final expectedCrc = uint32Le(bytes, offset + 16);
          if (crc32(entryBytes) != expectedCrc) {
            throw const FormatException('RAR4 file checksum mismatch');
          }
          expanded = _addEntry(
            entries,
            name,
            unpackedSize,
            expanded,
            bytes: entryBytes,
          );
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
        final expectedCrc = (fileFlags & 0x0004) != 0
            ? uint32Le(fileFields.readBytes(4), 0)
            : null;
        final compressionInfo = fileFields.readRarUint64();
        fileFields.readRarUint64();
        final nameSize = fileFields.readRarUint64();
        final name = _validatePath(utf8.decode(fileFields.readBytes(nameSize)));
        if (extraSize > 0) {
          _rejectEncryptedRar5Extra(bytes, headerEnd - extraSize, headerEnd);
        }
        if ((fileFlags & 0x0001) == 0) {
          _validateEntrySize(unpackedSize);
          final method = (compressionInfo >> 7) & 0x07;
          if (method != 0 || dataSize != unpackedSize) {
            throw const CoreException(
              CoreErrorCode.unsupported,
              'Compressed RAR5 entries are not supported; use stored method',
            );
          }
          requireRange(bytes, headerEnd, dataSize);
          final entryBytes = Uint8List.fromList(
            bytes.sublist(headerEnd, headerEnd + dataSize),
          );
          if (expectedCrc != null && crc32(entryBytes) != expectedCrc) {
            throw const FormatException('RAR5 file checksum mismatch');
          }
          expanded = _addEntry(
            entries,
            name,
            unpackedSize,
            expanded,
            bytes: entryBytes,
          );
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
    var header = ByteCursor(
      bytes,
      offset: nextStart,
      end: nextStart + nextSize,
    );
    final marker = header.readByte();
    if (marker == 0x17) {
      final decoded = _decode7zEncodedHeader(bytes, header);
      header = ByteCursor(decoded);
      if (header.readByte() != 0x01) {
        throw const FormatException('Decoded 7z header marker is invalid');
      }
    } else if (marker != 0x01) {
      throw const FormatException('7z header marker is invalid');
    }
    return _parse7zHeader(bytes, header);
  }

  Uint8List _decode7zEncodedHeader(Uint8List bytes, ByteCursor header) {
    final streams = _parse7zStreamsInfo(header);
    final pack = streams.packInfo;
    final unpack = streams.unpackInfo;
    if (pack == null || unpack == null || pack.sizes.length != 1) {
      throw const FormatException('Encoded 7z header stream is invalid');
    }
    if (unpack.folders.length != 1) {
      throw const FormatException('Encoded 7z header must use one folder');
    }
    final folder = unpack.folders.single;
    if (folder.coders.length != 1 ||
        folder.coders.single.inputs != 1 ||
        folder.coders.single.outputs != 1) {
      throw const FormatException('Encoded 7z header coder chain is invalid');
    }
    final packedStart = 32 + pack.position;
    final packedSize = pack.sizes.single;
    requireRange(bytes, packedStart, packedSize);
    final packed = bytes.sublist(packedStart, packedStart + packedSize);
    if (pack.crcs.isDefined(0) && crc32(packed) != pack.crcs.values[0]) {
      throw const FormatException('Encoded 7z header checksum mismatch');
    }
    final coder = folder.coders.single;
    final unpackedSize = folder.finalOutputSize;
    _validateExpansion(unpackedSize, packedSize);
    final decoded = _sameBytes(coder.method, const <int>[0x00])
        ? Uint8List.fromList(packed)
        : _sameBytes(coder.method, const <int>[0x03, 0x01, 0x01])
        ? decodeSevenZipLzma(packed, unpackedSize, coder.properties)
        : _sameBytes(coder.method, const <int>[0x21])
        ? _decodeSevenZipLzma2(packed, unpackedSize, coder.properties)
        : throw const FormatException('Unknown encoded 7z header codec');
    if (decoded.length != unpackedSize) {
      throw const FormatException('Encoded 7z header length mismatch');
    }
    if (folder.crc != null && crc32(decoded) != folder.crc) {
      throw const FormatException('Decoded 7z header checksum mismatch');
    }
    return decoded;
  }

  _ArchiveResult _parse7zHeader(Uint8List archive, ByteCursor header) {
    _SevenZipStreamsInfo? streams;
    _SevenZipFilesInfo? files;
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
          streams = _parse7zStreamsInfo(header);
        case 0x05:
          files = _parse7zFilesInfo(header);
        default:
          throw const FormatException('Unknown 7z header section');
      }
    }
    if (files == null) throw const FormatException('7z file list is missing');
    final streamBytes = streams == null
        ? const <Uint8List>[]
        : _decode7zStreams(archive, streams);
    final streamSizes = streams?.substreams.sizes ?? const <int>[];
    final entries = <_ArchiveEntry>[];
    var streamIndex = 0;
    var expanded = 0;
    for (var index = 0; index < files.names.length; index++) {
      final safePath = _validatePath(files.names[index]);
      if (files.emptyStreams[index]) {
        if (!files.emptyFiles[index]) continue;
        expanded = _addEntry(
          entries,
          safePath,
          0,
          expanded,
          bytes: Uint8List(0),
        );
      } else {
        if (streamIndex >= streamSizes.length ||
            streamIndex >= streamBytes.length) {
          throw const FormatException('7z file stream table is truncated');
        }
        expanded = _addEntry(
          entries,
          safePath,
          streamSizes[streamIndex],
          expanded,
          bytes: streamBytes[streamIndex++],
        );
      }
    }
    if (streamIndex != streamSizes.length) {
      throw const FormatException('7z file stream table has extra entries');
    }
    return _ArchiveResult('cb7:7z:${entries.length}', entries, expanded);
  }

  List<Uint8List> _decode7zStreams(
    Uint8List archive,
    _SevenZipStreamsInfo streams,
  ) {
    final pack = streams.packInfo;
    final unpack = streams.unpackInfo;
    if (pack == null || unpack == null) {
      throw const FormatException('7z stream metadata is incomplete');
    }
    if (pack.sizes.length != unpack.folders.length ||
        streams.substreams.counts.length != unpack.folders.length) {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'This 7z folder layout is not supported by the local reader',
      );
    }
    final decodedStreams = <Uint8List>[];
    var packOffset = 32 + pack.position;
    var substreamIndex = 0;
    for (
      var folderIndex = 0;
      folderIndex < unpack.folders.length;
      folderIndex++
    ) {
      final packedSize = pack.sizes[folderIndex];
      requireRange(archive, packOffset, packedSize);
      final packed = Uint8List.fromList(
        archive.sublist(packOffset, packOffset + packedSize),
      );
      if (pack.crcs.isDefined(folderIndex) &&
          crc32(packed) != pack.crcs.values[folderIndex]) {
        throw const FormatException('7z packed stream checksum mismatch');
      }
      final folder = unpack.folders[folderIndex];
      if (folder.coders.length != 1 ||
          folder.coders.single.inputs != 1 ||
          folder.coders.single.outputs != 1) {
        throw const CoreException(
          CoreErrorCode.unsupported,
          'This 7z coder chain is not supported by the local reader',
        );
      }
      final coder = folder.coders.single;
      final outputSize = folder.finalOutputSize;
      final decoded = _sameBytes(coder.method, const <int>[0x00])
          ? packed
          : _sameBytes(coder.method, const <int>[0x03, 0x01, 0x01])
          ? decodeSevenZipLzma(packed, outputSize, coder.properties)
          : _sameBytes(coder.method, const <int>[0x21])
          ? _decodeSevenZipLzma2(packed, outputSize, coder.properties)
          : throw const CoreException(
              CoreErrorCode.unsupported,
              'This 7z compression method is not supported by the local reader',
            );
      if (decoded.length != outputSize) {
        throw const FormatException('7z folder output length mismatch');
      }
      if (folder.crc != null && crc32(decoded) != folder.crc) {
        throw const FormatException('7z folder checksum mismatch');
      }
      var folderOffset = 0;
      final count = streams.substreams.counts[folderIndex];
      for (var index = 0; index < count; index++) {
        if (substreamIndex >= streams.substreams.sizes.length ||
            substreamIndex >= streams.substreams.crcs.defined.length) {
          throw const FormatException('7z substream table is truncated');
        }
        final size = streams.substreams.sizes[substreamIndex];
        requireRange(decoded, folderOffset, size);
        final bytes = Uint8List.fromList(
          decoded.sublist(folderOffset, folderOffset + size),
        );
        if (streams.substreams.crcs.isDefined(substreamIndex) &&
            crc32(bytes) != streams.substreams.crcs.values[substreamIndex]) {
          throw const FormatException('7z substream checksum mismatch');
        }
        decodedStreams.add(bytes);
        folderOffset += size;
        substreamIndex++;
      }
      if (folderOffset != decoded.length) {
        throw const FormatException('7z folder substreams are misaligned');
      }
      packOffset += packedSize;
    }
    if (substreamIndex != streams.substreams.sizes.length) {
      throw const FormatException('7z substream table has extra entries');
    }
    return decodedStreams;
  }

  Uint8List _decodeSevenZipLzma2(
    List<int> packed,
    int outputSize,
    List<int> properties,
  ) {
    if (properties.length != 1 || properties.single > 40 || outputSize < 0) {
      throw const FormatException('Invalid LZMA2 properties');
    }
    final input = InputMemoryStream(packed);
    final decoder = LzmaDecoder();
    final output = BytesBuilder(copy: false);
    var produced = 0;
    var ended = false;
    while (!input.isEOS) {
      final control = input.readByte();
      if (control == 0) {
        ended = true;
        break;
      }
      Uint8List chunk;
      if ((control & 0x80) == 0) {
        if (control != 1 && control != 2) {
          throw const FormatException('Invalid LZMA2 control byte');
        }
        final length = ((input.readByte() << 8) | input.readByte()) + 1;
        if (control == 1) decoder.reset(resetDictionary: true);
        chunk = decoder.decodeUncompressed(input, length);
      } else {
        final reset = (control >> 5) & 0x03;
        final unpackedLength =
            (((control & 0x1f) << 16) |
                (input.readByte() << 8) |
                input.readByte()) +
            1;
        final compressedLength =
            ((input.readByte() << 8) | input.readByte()) + 1;
        int? literalContextBits;
        int? literalPositionBits;
        int? positionBits;
        if (reset >= 2) {
          var value = input.readByte();
          positionBits = value ~/ 45;
          value -= positionBits * 45;
          literalPositionBits = value ~/ 9;
          literalContextBits = value - literalPositionBits * 9;
        }
        if (reset > 0) {
          decoder.reset(
            positionBits: positionBits,
            literalPositionBits: literalPositionBits,
            literalContextBits: literalContextBits,
            resetDictionary: reset == 3,
          );
        }
        chunk = decoder.decode(
          input.readBytes(compressedLength),
          unpackedLength,
        );
      }
      produced += chunk.length;
      if (produced > outputSize) {
        throw const FormatException('LZMA2 output exceeds declared size');
      }
      output.add(chunk);
    }
    if (!ended || produced != outputSize) {
      throw const FormatException('LZMA2 output length mismatch');
    }
    return output.takeBytes();
  }

  void _skip7zArchiveProperties(ByteCursor header) {
    while (true) {
      final id = header.readByte();
      if (id == 0x00) return;
      header.skip(header.read7zUint64());
    }
  }

  _SevenZipStreamsInfo _parse7zStreamsInfo(ByteCursor header) {
    _SevenZipPackInfo? packInfo;
    _SevenZipUnpackInfo? unpackInfo;
    _SevenZipSubStreamsInfo? substreams;
    while (true) {
      final id = header.readByte();
      if (id == 0x00) {
        if (unpackInfo != null) {
          substreams ??= _SevenZipSubStreamsInfo.inferred(unpackInfo.folders);
        }
        return _SevenZipStreamsInfo(
          packInfo,
          unpackInfo,
          substreams ?? const _SevenZipSubStreamsInfo.empty(),
        );
      }
      switch (id) {
        case 0x06:
          packInfo = _parse7zPackInfo(header);
        case 0x07:
          unpackInfo = _parse7zUnpackInfo(header);
        case 0x08:
          if (unpackInfo == null) {
            throw const FormatException('7z substreams precede folders');
          }
          substreams = _parse7zSubStreamsInfo(header, unpackInfo);
        default:
          throw const FormatException('Unknown 7z streams section');
      }
    }
  }

  _SevenZipPackInfo _parse7zPackInfo(ByteCursor header) {
    final position = header.read7zUint64();
    final streams = header.read7zUint64();
    if (streams > limits.maxEntries) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        '7z archive exceeds the configured stream limit',
      );
    }
    List<int>? sizes;
    var crcs = _SevenZipDigests.undefined(streams);
    while (true) {
      final id = header.readByte();
      if (id == 0x00) {
        if (sizes == null || sizes.length != streams) {
          throw const FormatException('7z pack sizes are missing');
        }
        return _SevenZipPackInfo(position, sizes, crcs);
      }
      if (id == 0x09) {
        sizes = <int>[];
        for (var index = 0; index < streams; index++) {
          sizes.add(header.read7zUint64());
        }
      } else if (id == 0x0a) {
        crcs = _read7zDigests(header, streams);
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
    if (folders > limits.maxEntries) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        '7z archive exceeds the configured folder limit',
      );
    }
    if (header.readByte() != 0) {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'External 7z folder data is not supported',
      );
    }
    final outputCounts = <int>[];
    final parsedFolders = <_SevenZipFolder>[];
    for (var index = 0; index < folders; index++) {
      final folder = _parse7zFolder(header);
      parsedFolders.add(folder);
      outputCounts.add(folder.outputCount);
    }
    if (header.readByte() != 0x0c) {
      throw const FormatException('7z unpack sizes are missing');
    }
    for (
      var folderIndex = 0;
      folderIndex < outputCounts.length;
      folderIndex++
    ) {
      final sizes = <int>[];
      final outputs = outputCounts[folderIndex];
      for (var index = 0; index < outputs; index++) {
        sizes.add(header.read7zUint64());
      }
      parsedFolders[folderIndex].outputSizes = sizes;
    }
    var folderCrc = _SevenZipDigests.undefined(folders);
    final next = header.readByte();
    if (next == 0x0a) {
      folderCrc = _read7zDigests(header, folders);
      if (header.readByte() != 0x00) {
        throw const FormatException('7z unpack section is not terminated');
      }
    } else if (next != 0x00) {
      throw const FormatException('Unknown 7z unpack property');
    }
    var expanded = 0;
    for (var index = 0; index < parsedFolders.length; index++) {
      final folder = parsedFolders[index];
      if (folderCrc.isDefined(index)) folder.crc = folderCrc.values[index];
      expanded += folder.finalOutputSize;
      if (expanded > limits.maxExpandedBytes) {
        throw const CoreException(
          CoreErrorCode.payloadTooLarge,
          'Comic archive exceeds the configured expansion limit',
        );
      }
    }
    return _SevenZipUnpackInfo(parsedFolders);
  }

  _SevenZipFolder _parse7zFolder(ByteCursor header) {
    final coders = header.read7zUint64();
    if (coders < 1 || coders > limits.maxEntries) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        '7z folder exceeds the configured coder limit',
      );
    }
    var totalInputs = 0;
    var totalOutputs = 0;
    final parsedCoders = <_SevenZipCoder>[];
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
      if (inputs < 1 || outputs < 1) {
        throw const FormatException('Invalid 7z coder stream count');
      }
      final properties = (flags & 0x20) != 0
          ? header.readBytes(header.read7zUint64())
          : const <int>[];
      if ((flags & 0x80) != 0) {
        throw const CoreException(
          CoreErrorCode.unsupported,
          'Alternative 7z coder methods are not supported',
        );
      }
      totalInputs += inputs;
      totalOutputs += outputs;
      if (totalInputs > limits.maxEntries || totalOutputs > limits.maxEntries) {
        throw const CoreException(
          CoreErrorCode.payloadTooLarge,
          '7z folder exceeds the configured stream limit',
        );
      }
      parsedCoders.add(_SevenZipCoder(codec, properties, inputs, outputs));
    }
    final bindPairs = totalOutputs - 1;
    final boundOutputs = <int>{};
    for (var index = 0; index < bindPairs; index++) {
      final output = header.read7zUint64();
      final input = header.read7zUint64();
      if (output >= totalOutputs ||
          input >= totalInputs ||
          !boundOutputs.add(output)) {
        throw const FormatException('Invalid 7z folder bind pair');
      }
    }
    final packedStreams = totalInputs - bindPairs;
    if (packedStreams > 1) {
      for (var index = 0; index < packedStreams; index++) {
        header.read7zUint64();
      }
    }
    final finalOutputs = <int>[
      for (var index = 0; index < totalOutputs; index++)
        if (!boundOutputs.contains(index)) index,
    ];
    if (finalOutputs.length != 1) {
      throw const FormatException('7z folder has no final output stream');
    }
    return _SevenZipFolder(parsedCoders, totalOutputs, finalOutputs.single);
  }

  _SevenZipSubStreamsInfo _parse7zSubStreamsInfo(
    ByteCursor header,
    _SevenZipUnpackInfo unpackInfo,
  ) {
    final streams = List<int>.filled(unpackInfo.folders.length, 1);
    var id = header.readByte();
    if (id == 0x0d) {
      var total = 0;
      for (var index = 0; index < streams.length; index++) {
        streams[index] = header.read7zUint64();
        total += streams[index];
        if (total > limits.maxEntries) {
          throw const CoreException(
            CoreErrorCode.payloadTooLarge,
            '7z archive exceeds the configured stream limit',
          );
        }
      }
      id = header.readByte();
    }
    final sizes = <int>[];
    if (id == 0x09) {
      for (var folderIndex = 0; folderIndex < streams.length; folderIndex++) {
        final count = streams[folderIndex];
        var remaining = unpackInfo.folders[folderIndex].finalOutputSize;
        for (var index = 1; index < count; index++) {
          final size = header.read7zUint64();
          if (size > remaining) {
            throw const FormatException('Invalid 7z substream size');
          }
          sizes.add(size);
          remaining -= size;
        }
        if (count > 0) sizes.add(remaining);
      }
      id = header.readByte();
    } else {
      if (streams.any((count) => count > 1)) {
        throw const FormatException('7z substream sizes are missing');
      }
      for (var index = 0; index < streams.length; index++) {
        if (streams[index] == 1) {
          sizes.add(unpackInfo.folders[index].finalOutputSize);
        }
      }
    }
    var digests = _SevenZipDigests.undefined(
      streams.fold<int>(0, (sum, value) => sum + value),
    );
    if (id == 0x0a) {
      var digestCount = 0;
      for (var index = 0; index < streams.length; index++) {
        if (streams[index] != 1 || unpackInfo.folders[index].crc == null) {
          digestCount += streams[index];
        }
      }
      final encoded = _read7zDigests(header, digestCount);
      final definitions = <bool>[];
      final values = <int>[];
      var encodedIndex = 0;
      for (var folderIndex = 0; folderIndex < streams.length; folderIndex++) {
        final count = streams[folderIndex];
        final folder = unpackInfo.folders[folderIndex];
        if (count == 1 && folder.crc != null) {
          definitions.add(true);
          values.add(folder.crc!);
        } else {
          for (var index = 0; index < count; index++) {
            definitions.add(encoded.isDefined(encodedIndex));
            values.add(encoded.values[encodedIndex++]);
          }
        }
      }
      if (encodedIndex != encoded.defined.length) {
        throw const FormatException('7z substream checksums are misaligned');
      }
      digests = _SevenZipDigests(definitions, values);
      id = header.readByte();
    } else {
      final definitions = <bool>[];
      final values = <int>[];
      for (var folderIndex = 0; folderIndex < streams.length; folderIndex++) {
        final count = streams[folderIndex];
        final folder = unpackInfo.folders[folderIndex];
        for (var index = 0; index < count; index++) {
          definitions.add(count == 1 && folder.crc != null);
          values.add(count == 1 ? folder.crc ?? 0 : 0);
        }
      }
      digests = _SevenZipDigests(definitions, values);
    }
    if (id != 0x00) {
      throw const FormatException('Unknown 7z substreams property');
    }
    return _SevenZipSubStreamsInfo(sizes, streams, digests);
  }

  _SevenZipDigests _read7zDigests(ByteCursor header, int count) {
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
    final values = List<int>.filled(count, 0);
    for (var index = 0; index < count; index++) {
      if (definitions[index]) {
        final bytes = header.readBytes(4);
        values[index] = uint32Le(bytes, 0);
      }
    }
    return _SevenZipDigests(definitions, values);
  }

  _SevenZipFilesInfo _parse7zFilesInfo(ByteCursor header) {
    final count = header.read7zUint64();
    if (count > limits.maxEntries) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Comic archive exceeds the configured entry limit',
      );
    }
    List<String>? names;
    var emptyStreams = List<bool>.filled(count, false);
    var emptyFiles = List<bool>.filled(count, false);
    while (true) {
      final property = header.readByte();
      if (property == 0x00) break;
      final size = header.read7zUint64();
      final end = header.offset + size;
      if (end > header.end) {
        throw const FormatException('7z file property is truncated');
      }
      final propertyHeader = ByteCursor(
        header.bytes,
        offset: header.offset,
        end: end,
      );
      if (property == 0x11) {
        if (propertyHeader.readByte() != 0) {
          throw const CoreException(
            CoreErrorCode.unsupported,
            'External 7z filenames are not supported',
          );
        }
        final units = <int>[];
        final decoded = <String>[];
        while (propertyHeader.offset < end) {
          final low = propertyHeader.readByte();
          final high = propertyHeader.readByte();
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
      } else if (property == 0x0e) {
        emptyStreams = _read7zBoolVector(propertyHeader, count);
      } else if (property == 0x0f) {
        final emptyCount = emptyStreams.where((empty) => empty).length;
        final compact = _read7zBoolVector(propertyHeader, emptyCount);
        var compactIndex = 0;
        for (var index = 0; index < count; index++) {
          if (emptyStreams[index]) emptyFiles[index] = compact[compactIndex++];
        }
      }
      header.offset = end;
    }
    if (names == null) throw const FormatException('7z filenames are missing');
    return _SevenZipFilesInfo(names, emptyStreams, emptyFiles);
  }

  List<bool> _read7zBoolVector(ByteCursor header, int count) {
    final values = List<bool>.filled(count, false);
    for (var index = 0; index < (count + 7) ~/ 8; index++) {
      final bits = header.readByte();
      for (var bit = 0; bit < 8 && index * 8 + bit < count; bit++) {
        values[index * 8 + bit] = (bits & (0x80 >> bit)) != 0;
      }
    }
    return values;
  }

  int _addEntry(
    List<_ArchiveEntry> entries,
    String name,
    int size,
    int expanded, {
    bool enforceExpanded = true,
    Uint8List? bytes,
  }) {
    if (entries.length >= limits.maxEntries) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Comic archive exceeds the configured entry limit',
      );
    }
    _validateEntrySize(size);
    final total = expanded + size;
    if (enforceExpanded && total > limits.maxExpandedBytes) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Comic archive exceeds the configured expansion limit',
      );
    }
    entries.add(_ArchiveEntry(name, size, bytes));
    return total;
  }

  void _validateEntrySize(int size) {
    if (size < 0 || size > limits.maxEntryBytes) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Comic archive entry exceeds the configured size limit',
      );
    }
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
      bytes.length >= 512 &&
      _sameBytes(bytes.sublist(257, 262), const <int>[
        0x75,
        0x73,
        0x74,
        0x61,
        0x72,
      ]);

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
  const _ArchiveEntry(this.name, this.expandedSize, this.bytes);

  final String name;
  final int expandedSize;
  final Uint8List? bytes;
}

class _ArchiveResult {
  const _ArchiveResult(this.version, this.entries, this.expandedBytes);

  final String version;
  final List<_ArchiveEntry> entries;
  final int expandedBytes;
}

class _SevenZipUnpackInfo {
  const _SevenZipUnpackInfo(this.folders);

  final List<_SevenZipFolder> folders;
}

class _SevenZipStreamsInfo {
  const _SevenZipStreamsInfo(this.packInfo, this.unpackInfo, this.substreams);

  final _SevenZipPackInfo? packInfo;
  final _SevenZipUnpackInfo? unpackInfo;
  final _SevenZipSubStreamsInfo substreams;
}

class _SevenZipSubStreamsInfo {
  const _SevenZipSubStreamsInfo(this.sizes, this.counts, this.crcs);

  const _SevenZipSubStreamsInfo.empty()
    : sizes = const <int>[],
      counts = const <int>[],
      crcs = const _SevenZipDigests(<bool>[], <int>[]);

  factory _SevenZipSubStreamsInfo.inferred(List<_SevenZipFolder> folders) {
    final definitions = <bool>[];
    final values = <int>[];
    for (final folder in folders) {
      definitions.add(folder.crc != null);
      values.add(folder.crc ?? 0);
    }
    return _SevenZipSubStreamsInfo(
      folders.map((folder) => folder.finalOutputSize).toList(),
      List<int>.filled(folders.length, 1),
      _SevenZipDigests(definitions, values),
    );
  }

  final List<int> sizes;
  final List<int> counts;
  final _SevenZipDigests crcs;
}

class _SevenZipPackInfo {
  const _SevenZipPackInfo(this.position, this.sizes, this.crcs);

  final int position;
  final List<int> sizes;
  final _SevenZipDigests crcs;
}

class _SevenZipDigests {
  const _SevenZipDigests(this.defined, this.values);

  factory _SevenZipDigests.undefined(int count) => _SevenZipDigests(
    List<bool>.filled(count, false),
    List<int>.filled(count, 0),
  );

  final List<bool> defined;
  final List<int> values;

  bool isDefined(int index) => defined[index];
}

class _SevenZipCoder {
  const _SevenZipCoder(this.method, this.properties, this.inputs, this.outputs);

  final List<int> method;
  final List<int> properties;
  final int inputs;
  final int outputs;
}

class _SevenZipFolder {
  _SevenZipFolder(this.coders, this.outputCount, this.finalOutputIndex);

  final List<_SevenZipCoder> coders;
  final int outputCount;
  final int finalOutputIndex;
  List<int> outputSizes = const <int>[];
  int? crc;

  int get finalOutputSize {
    if (finalOutputIndex >= outputSizes.length) {
      throw const FormatException('7z folder output size is missing');
    }
    return outputSizes[finalOutputIndex];
  }
}

class _SevenZipFilesInfo {
  const _SevenZipFilesInfo(this.names, this.emptyStreams, this.emptyFiles);

  final List<String> names;
  final List<bool> emptyStreams;
  final List<bool> emptyFiles;
}
