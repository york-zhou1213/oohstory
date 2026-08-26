import 'dart:convert';
import 'dart:typed_data';

import 'package:archive/archive.dart';

import '../../adapters/formats/_binary.dart';
import '../../adapters/formats/_seven_zip_lzma.dart';
import '../../core/errors.dart';

class ExtractedComicPage {
  const ExtractedComicPage({required this.name, required this.bytes});

  final String name;
  final Uint8List bytes;
}

class ComicPageExtractor {
  const ComicPageExtractor();

  static const _rar4 = <int>[0x52, 0x61, 0x72, 0x21, 0x1a, 0x07, 0x00];
  static const _rar5 = <int>[0x52, 0x61, 0x72, 0x21, 0x1a, 0x07, 0x01, 0x00];
  static const _sevenZip = <int>[0x37, 0x7a, 0xbc, 0xaf, 0x27, 0x1c];

  List<ExtractedComicPage> extract(
    Uint8List archive,
    List<String> orderedNames,
  ) {
    final wanted = orderedNames.toSet();
    final extracted = _startsWith(archive, _rar4)
        ? _extractRar4(archive, wanted)
        : _startsWith(archive, _rar5)
        ? _extractRar5(archive, wanted)
        : _startsWith(archive, _sevenZip)
        ? _extractSevenZip(archive, orderedNames)
        : _extractTar(archive, wanted);
    final byName = <String, Uint8List>{
      for (final page in extracted) page.name: page.bytes,
    };
    if (orderedNames.any((name) => !byName.containsKey(name))) {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'This comic uses a compression layout that cannot be rendered locally',
      );
    }
    return <ExtractedComicPage>[
      for (final name in orderedNames)
        ExtractedComicPage(name: name, bytes: byName[name]!),
    ];
  }

  List<ExtractedComicPage> _extractTar(Uint8List bytes, Set<String> wanted) {
    final pages = <ExtractedComicPage>[];
    var offset = 0;
    while (offset + 512 <= bytes.length) {
      final header = bytes.sublist(offset, offset + 512);
      if (header.every((byte) => byte == 0)) break;
      final type = header[156];
      final name = _tarText(header, 0, 100);
      final prefix = _tarText(header, 345, 155);
      final path = (prefix.isEmpty ? name : '$prefix/$name').replaceAll(
        '\\',
        '/',
      );
      final sizeText = _tarText(header, 124, 12).trim();
      final size = sizeText.isEmpty ? 0 : int.parse(sizeText, radix: 8);
      final dataOffset = offset + 512;
      final paddedSize = ((size + 511) ~/ 512) * 512;
      requireRange(bytes, dataOffset, paddedSize);
      if ((type == 0 || type == 0x30) && wanted.contains(path)) {
        pages.add(
          ExtractedComicPage(
            name: path,
            bytes: Uint8List.fromList(
              bytes.sublist(dataOffset, dataOffset + size),
            ),
          ),
        );
      }
      offset = dataOffset + paddedSize;
    }
    return pages;
  }

  List<ExtractedComicPage> _extractRar4(Uint8List bytes, Set<String> wanted) {
    final pages = <ExtractedComicPage>[];
    var offset = _rar4.length;
    while (offset < bytes.length) {
      requireRange(bytes, offset, 7);
      final type = bytes[offset + 2];
      final flags = uint16Le(bytes, offset + 3);
      final headerSize = uint16Le(bytes, offset + 5);
      requireRange(bytes, offset, headerSize);
      final packedSize = (flags & 0x8000) != 0
          ? uint32Le(bytes, offset + 7)
          : 0;
      if (type == 0x74) {
        var unpackedSize = uint32Le(bytes, offset + 11);
        var nameOffset = offset + 32;
        if ((flags & 0x0100) != 0) {
          final highPacked = uint32Le(bytes, nameOffset);
          final highUnpacked = uint32Le(bytes, nameOffset + 4);
          if (highPacked != 0) _unsupportedCompression();
          unpackedSize |= highUnpacked << 32;
          nameOffset += 8;
        }
        final nameSize = uint16Le(bytes, offset + 26);
        requireRange(bytes, nameOffset, nameSize);
        final rawName = bytes.sublist(nameOffset, nameOffset + nameSize);
        final zero = rawName.indexOf(0);
        final name = utf8
            .decode(
              zero < 0 ? rawName : rawName.sublist(0, zero),
              allowMalformed: true,
            )
            .replaceAll('\\', '/');
        if (wanted.contains(name)) {
          if (bytes[offset + 25] != 0x30 || packedSize != unpackedSize) {
            _unsupportedCompression();
          }
          final start = offset + headerSize;
          requireRange(bytes, start, packedSize);
          pages.add(
            ExtractedComicPage(
              name: name,
              bytes: Uint8List.fromList(
                bytes.sublist(start, start + packedSize),
              ),
            ),
          );
        }
      }
      final next = offset + headerSize + packedSize;
      if (next <= offset || next > bytes.length) {
        throw const FormatException('RAR block is truncated');
      }
      offset = next;
      if (type == 0x7b) break;
    }
    return pages;
  }

  List<ExtractedComicPage> _extractRar5(Uint8List bytes, Set<String> wanted) {
    final pages = <ExtractedComicPage>[];
    var offset = _rar5.length;
    while (offset < bytes.length) {
      requireRange(bytes, offset, 4);
      final header = ByteCursor(bytes, offset: offset + 4);
      final headerSize = header.readRarUint64();
      final headerStart = header.offset;
      final headerEnd = headerStart + headerSize;
      requireRange(bytes, headerStart, headerSize);
      final fields = ByteCursor(bytes, offset: headerStart, end: headerEnd);
      final type = fields.readRarUint64();
      final flags = fields.readRarUint64();
      final extraSize = (flags & 0x0001) != 0 ? fields.readRarUint64() : 0;
      final packedSize = (flags & 0x0002) != 0 ? fields.readRarUint64() : 0;
      if (type == 2) {
        final file = ByteCursor(
          bytes,
          offset: fields.offset,
          end: headerEnd - extraSize,
        );
        final fileFlags = file.readRarUint64();
        final unpackedSize = file.readRarUint64();
        file.readRarUint64();
        if ((fileFlags & 0x0002) != 0) file.skip(4);
        if ((fileFlags & 0x0004) != 0) file.skip(4);
        file.readRarUint64();
        file.readRarUint64();
        final nameSize = file.readRarUint64();
        final name = utf8
            .decode(file.readBytes(nameSize))
            .replaceAll('\\', '/');
        if (wanted.contains(name)) {
          if (packedSize != unpackedSize) _unsupportedCompression();
          requireRange(bytes, headerEnd, packedSize);
          pages.add(
            ExtractedComicPage(
              name: name,
              bytes: Uint8List.fromList(
                bytes.sublist(headerEnd, headerEnd + packedSize),
              ),
            ),
          );
        }
      }
      final next = headerEnd + packedSize;
      if (next <= offset || next > bytes.length) {
        throw const FormatException('RAR5 block is truncated');
      }
      offset = next;
      if (type == 5) break;
    }
    return pages;
  }

  List<ExtractedComicPage> _extractSevenZip(
    Uint8List bytes,
    List<String> names,
  ) {
    requireRange(bytes, 0, 32);
    final nextOffset = uint64Le(bytes, 12);
    final nextSize = uint64Le(bytes, 20);
    final nextStart = 32 + nextOffset;
    requireRange(bytes, nextStart, nextSize);
    var header = ByteCursor(
      bytes,
      offset: nextStart,
      end: nextStart + nextSize,
    );
    var marker = header.readByte();
    if (marker == 0x17) {
      final encoded = _parseSevenZipStreams(header);
      if (encoded.folders.length != 1 || encoded.packSizes.length != 1) {
        _unsupportedCompression();
      }
      final decoded = _decodeSevenZipFolder(bytes, encoded, 0);
      header = ByteCursor(decoded);
      marker = header.readByte();
    }
    if (marker != 0x01) throw const FormatException('Invalid 7z header');

    _SevenZipStreams? streams;
    _SevenZipFiles? files;
    while (header.offset < header.end) {
      final id = header.readByte();
      if (id == 0x00) break;
      if (id == 0x02) {
        _skipArchiveProperties(header);
      } else if (id == 0x04) {
        streams = _parseSevenZipStreams(header);
      } else if (id == 0x05) {
        files = _parseFilesInfo(header);
      } else {
        throw const FormatException('Unsupported 7z header section');
      }
    }
    final streamNames = files?.streamNames ?? const <String>[];
    if (streams == null ||
        streams.packSizes.length != streams.folders.length ||
        streams.substreamCounts.length != streams.folders.length ||
        streams.substreamSizes.length != streamNames.length) {
      _unsupportedCompression();
    }
    final decoded = <String, Uint8List>{};
    var streamIndex = 0;
    for (
      var folderIndex = 0;
      folderIndex < streams.folders.length;
      folderIndex++
    ) {
      final folderBytes = _decodeSevenZipFolder(bytes, streams, folderIndex);
      var folderOffset = 0;
      final count = streams.substreamCounts[folderIndex];
      for (var index = 0; index < count; index++) {
        if (streamIndex >= streamNames.length) _unsupportedCompression();
        final size = streams.substreamSizes[streamIndex];
        requireRange(folderBytes, folderOffset, size);
        decoded[streamNames[streamIndex]] = Uint8List.fromList(
          folderBytes.sublist(folderOffset, folderOffset + size),
        );
        folderOffset += size;
        streamIndex++;
      }
      if (folderOffset != folderBytes.length) _unsupportedCompression();
    }
    if (streamIndex != streamNames.length) _unsupportedCompression();
    if (names.any((name) => !decoded.containsKey(name))) {
      _unsupportedCompression();
    }
    return <ExtractedComicPage>[
      for (final name in names)
        ExtractedComicPage(name: name, bytes: decoded[name]!),
    ];
  }

  Uint8List _decodeSevenZipFolder(
    Uint8List bytes,
    _SevenZipStreams streams,
    int index,
  ) {
    var offset = 32 + streams.packPosition;
    for (var i = 0; i < index; i++) {
      offset += streams.packSizes[i];
    }
    final packedSize = streams.packSizes[index];
    requireRange(bytes, offset, packedSize);
    final packed = bytes.sublist(offset, offset + packedSize);
    final folder = streams.folders[index];
    final decoded = _sameBytes(folder.method, const <int>[0x00])
        ? Uint8List.fromList(packed)
        : _sameBytes(folder.method, const <int>[0x03, 0x01, 0x01])
        ? decodeSevenZipLzma(packed, folder.outputSize, folder.properties)
        : _sameBytes(folder.method, const <int>[0x21])
        ? _decodeSevenZipLzma2(packed, folder.outputSize, folder.properties)
        : _unsupportedCompression<Uint8List>();
    if (decoded.length != folder.outputSize) {
      throw const FormatException('7z output length mismatch');
    }
    return decoded;
  }

  _SevenZipStreams _parseSevenZipStreams(ByteCursor header) {
    var packPosition = 0;
    var packSizes = <int>[];
    var folders = <_SevenZipFolder>[];
    var substreamSizes = <int>[];
    var substreamCounts = <int>[];
    while (true) {
      final id = header.readByte();
      if (id == 0x00) {
        if (substreamSizes.isEmpty) {
          substreamSizes = folders.map((folder) => folder.outputSize).toList();
        }
        if (substreamCounts.isEmpty) {
          substreamCounts = List<int>.filled(folders.length, 1);
        }
        return _SevenZipStreams(
          packPosition: packPosition,
          packSizes: packSizes,
          folders: folders,
          substreamSizes: substreamSizes,
          substreamCounts: substreamCounts,
        );
      }
      if (id == 0x06) {
        packPosition = header.read7zUint64();
        final count = header.read7zUint64();
        while (true) {
          final property = header.readByte();
          if (property == 0x00) break;
          if (property == 0x09) {
            packSizes = <int>[
              for (var i = 0; i < count; i++) header.read7zUint64(),
            ];
          } else if (property == 0x0a) {
            _skipDigests(header, count);
          } else {
            throw const FormatException('Unsupported 7z pack property');
          }
        }
      } else if (id == 0x07) {
        if (header.readByte() != 0x0b) {
          throw const FormatException('Missing 7z folders');
        }
        final count = header.read7zUint64();
        if (header.readByte() != 0) _unsupportedCompression();
        folders = <_SevenZipFolder>[];
        for (var i = 0; i < count; i++) {
          if (header.read7zUint64() != 1) _unsupportedCompression();
          final flags = header.readByte();
          final method = header.readBytes(flags & 0x0f);
          if ((flags & 0x10) != 0 || (flags & 0x80) != 0) {
            _unsupportedCompression();
          }
          final properties = (flags & 0x20) != 0
              ? header.readBytes(header.read7zUint64())
              : const <int>[];
          folders.add(_SevenZipFolder(method: method, properties: properties));
        }
        if (header.readByte() != 0x0c) {
          throw const FormatException('Missing 7z unpack sizes');
        }
        for (final folder in folders) {
          folder.outputSize = header.read7zUint64();
        }
        final next = header.readByte();
        if (next == 0x0a) {
          _skipDigests(header, count);
          if (header.readByte() != 0x00) {
            throw const FormatException('Invalid 7z unpack terminator');
          }
        } else if (next != 0x00) {
          throw const FormatException('Unsupported 7z unpack property');
        }
      } else if (id == 0x08) {
        final counts = List<int>.filled(folders.length, 1);
        var property = header.readByte();
        if (property == 0x0d) {
          for (var i = 0; i < counts.length; i++) {
            counts[i] = header.read7zUint64();
          }
          property = header.readByte();
        }
        substreamCounts = counts;
        if (property == 0x09) {
          substreamSizes = <int>[];
          for (
            var folderIndex = 0;
            folderIndex < folders.length;
            folderIndex++
          ) {
            var remaining = folders[folderIndex].outputSize;
            for (var index = 1; index < counts[folderIndex]; index++) {
              final size = header.read7zUint64();
              if (size > remaining) {
                throw const FormatException('Invalid 7z substream size');
              }
              substreamSizes.add(size);
              remaining -= size;
            }
            if (counts[folderIndex] > 0) substreamSizes.add(remaining);
          }
          property = header.readByte();
        } else {
          if (counts.any((count) => count > 1)) _unsupportedCompression();
          substreamSizes = <int>[
            for (var index = 0; index < folders.length; index++)
              if (counts[index] == 1) folders[index].outputSize,
          ];
        }
        if (property == 0x0a) {
          _skipDigests(header, counts.fold(0, (sum, value) => sum + value));
          property = header.readByte();
        }
        if (property != 0x00) {
          throw const FormatException('Unsupported 7z substream property');
        }
      } else {
        throw const FormatException('Unsupported 7z streams section');
      }
    }
  }

  void _skipArchiveProperties(ByteCursor header) {
    while (true) {
      final id = header.readByte();
      if (id == 0x00) return;
      header.skip(header.read7zUint64());
    }
  }

  _SevenZipFiles _parseFilesInfo(ByteCursor header) {
    final count = header.read7zUint64();
    List<String>? names;
    var emptyStreams = List<bool>.filled(count, false);
    while (true) {
      final id = header.readByte();
      if (id == 0x00) {
        if (names == null || names.length != count) {
          throw const FormatException('7z filenames are missing');
        }
        return _SevenZipFiles(<String>[
          for (var index = 0; index < names.length; index++)
            if (!emptyStreams[index]) names[index].replaceAll('\\', '/'),
        ]);
      }
      final size = header.read7zUint64();
      final end = header.offset + size;
      if (end > header.end) {
        throw const FormatException('7z file property is truncated');
      }
      final property = ByteCursor(
        header.bytes,
        offset: header.offset,
        end: end,
      );
      if (id == 0x11) {
        if (property.readByte() != 0) _unsupportedCompression();
        final decoded = <String>[];
        final units = <int>[];
        while (property.offset < property.end) {
          final unit = property.readByte() | (property.readByte() << 8);
          if (unit == 0) {
            decoded.add(String.fromCharCodes(units));
            units.clear();
          } else {
            units.add(unit);
          }
        }
        if (units.isNotEmpty) {
          throw const FormatException('7z filename table is invalid');
        }
        names = decoded;
      } else if (id == 0x0e) {
        emptyStreams = _readBoolVector(property, count);
      }
      header.offset = end;
    }
  }

  List<bool> _readBoolVector(ByteCursor header, int count) {
    final values = List<bool>.filled(count, false);
    for (var index = 0; index < (count + 7) ~/ 8; index++) {
      final bits = header.readByte();
      for (var bit = 0; bit < 8 && index * 8 + bit < count; bit++) {
        values[index * 8 + bit] = (bits & (0x80 >> bit)) != 0;
      }
    }
    return values;
  }

  void _skipDigests(ByteCursor header, int count) {
    final allDefined = header.readByte();
    final defined = List<bool>.filled(count, allDefined != 0);
    if (allDefined == 0) {
      for (var index = 0; index < (count + 7) ~/ 8; index++) {
        final bits = header.readByte();
        for (var bit = 0; bit < 8 && index * 8 + bit < count; bit++) {
          defined[index * 8 + bit] = (bits & (0x80 >> bit)) != 0;
        }
      }
    }
    header.skip(defined.where((value) => value).length * 4);
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

  String _tarText(List<int> bytes, int offset, int length) {
    final field = bytes.sublist(offset, offset + length);
    final zero = field.indexOf(0);
    return utf8.decode(zero < 0 ? field : field.sublist(0, zero));
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

  Never _unsupportedCompression<T>() => throw const CoreException(
    CoreErrorCode.unsupported,
    'This comic uses compression that is unavailable in the local reader',
  );
}

class _SevenZipStreams {
  const _SevenZipStreams({
    required this.packPosition,
    required this.packSizes,
    required this.folders,
    required this.substreamSizes,
    required this.substreamCounts,
  });

  final int packPosition;
  final List<int> packSizes;
  final List<_SevenZipFolder> folders;
  final List<int> substreamSizes;
  final List<int> substreamCounts;
}

class _SevenZipFolder {
  _SevenZipFolder({required this.method, required this.properties});

  final List<int> method;
  final List<int> properties;
  int outputSize = 0;
}

class _SevenZipFiles {
  const _SevenZipFiles(this.streamNames);

  final List<String> streamNames;
}
