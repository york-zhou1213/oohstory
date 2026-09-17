import 'dart:convert';
import 'dart:typed_data';

Uint8List kindleFixture({
  String title = 'Fixture Book',
  String body = '<p>Chapter 1</p><p>Hello reader.</p>',
  int compression = 1,
  int encryptionType = 0,
  int mobiVersion = 8,
}) {
  final titleBytes = utf8.encode(title);
  final bodyBytes = utf8.encode(body);
  const record0Offset = 96;
  const titleOffset = 200;
  final record1Offset = record0Offset + titleOffset + titleBytes.length;
  final bytes = Uint8List(record1Offset + bodyBytes.length);
  _writeAscii(bytes, 0, 'Fixture');
  _writeAscii(bytes, 60, 'BOOKMOBI');
  _write16Be(bytes, 76, 2);
  _write32Be(bytes, 78, record0Offset);
  _write32Be(bytes, 86, record1Offset);

  _write16Be(bytes, record0Offset, compression);
  _write32Be(bytes, record0Offset + 4, bodyBytes.length);
  _write16Be(bytes, record0Offset + 8, 1);
  _write16Be(bytes, record0Offset + 10, 4096);
  _write16Be(bytes, record0Offset + 12, encryptionType);
  final mobi = record0Offset + 16;
  _writeAscii(bytes, mobi, 'MOBI');
  _write32Be(bytes, mobi + 4, 184);
  _write32Be(bytes, mobi + 8, 2);
  _write32Be(bytes, mobi + 12, 65001);
  _write32Be(bytes, mobi + 16, 42);
  _write32Be(bytes, mobi + 20, mobiVersion);
  _write32Be(bytes, mobi + 68, titleOffset);
  _write32Be(bytes, mobi + 72, titleBytes.length);
  _write32Be(bytes, mobi + 152, 0xffffffff);
  bytes.setRange(record0Offset + titleOffset, record1Offset, titleBytes);
  bytes.setRange(record1Offset, bytes.length, bodyBytes);
  return bytes;
}

Uint8List huffCdicKindleFixture({
  String title = 'HUFF Fixture',
  String body = '<p>HUFF chapter</p><p>Dictionary text.</p>',
}) {
  final titleBytes = utf8.encode(title);
  final bodyBytes = utf8.encode(body);
  if (bodyBytes.length > 256) {
    throw ArgumentError.value(
      body,
      'body',
      'fixture body must fit 256 symbols',
    );
  }

  final huff = Uint8List(24 + 256 * 4 + 32 * 8);
  _writeAscii(huff, 0, 'HUFF');
  _write32Be(huff, 4, 24);
  _write32Be(huff, 8, 24);
  _write32Be(huff, 12, 24 + 256 * 4);
  for (var index = 0; index < 256; index++) {
    // Eight-bit terminal codes. Every prefix shares max code 255, so byte
    // 255 maps to dictionary entry 0, byte 254 to entry 1, and so on.
    _write32Be(huff, 24 + index * 4, (255 << 8) | 0x80 | 8);
  }

  const cdicHeaderLength = 16;
  const dictionaryEntries = 256;
  final offsets = <int>[];
  var dataLength = dictionaryEntries * 2;
  for (var index = 0; index < dictionaryEntries; index++) {
    offsets.add(dataLength);
    dataLength += 2 + (index < bodyBytes.length ? 1 : 0);
  }
  final cdic = Uint8List(cdicHeaderLength + dataLength);
  _writeAscii(cdic, 0, 'CDIC');
  _write32Be(cdic, 4, cdicHeaderLength);
  _write32Be(cdic, 8, dictionaryEntries);
  _write32Be(cdic, 12, 8);
  for (var index = 0; index < dictionaryEntries; index++) {
    _write16Be(cdic, cdicHeaderLength + index * 2, offsets[index]);
    final phrase = cdicHeaderLength + offsets[index];
    final length = index < bodyBytes.length ? 1 : 0;
    _write16Be(cdic, phrase, 0x8000 | length);
    if (length == 1) cdic[phrase + 2] = bodyBytes[index];
  }

  final compressed = Uint8List.fromList(
    List<int>.generate(bodyBytes.length, (index) => 255 - index),
  );
  const record0Offset = 112;
  const titleOffset = 280;
  final record0Length = titleOffset + titleBytes.length;
  final record1Offset = record0Offset + record0Length;
  final record2Offset = record1Offset + compressed.length;
  final record3Offset = record2Offset + huff.length;
  final bytes = Uint8List(record3Offset + cdic.length);
  _writeAscii(bytes, 0, 'HUFF Fixture');
  _writeAscii(bytes, 60, 'BOOKMOBI');
  _write16Be(bytes, 76, 4);
  for (final item in <(int, int)>[
    (0, record0Offset),
    (1, record1Offset),
    (2, record2Offset),
    (3, record3Offset),
  ]) {
    _write32Be(bytes, 78 + item.$1 * 8, item.$2);
  }
  _write16Be(bytes, record0Offset, 17480);
  _write32Be(bytes, record0Offset + 4, bodyBytes.length);
  _write16Be(bytes, record0Offset + 8, 1);
  _write16Be(bytes, record0Offset + 10, 4096);
  final mobi = record0Offset + 16;
  _writeAscii(bytes, mobi, 'MOBI');
  _write32Be(bytes, mobi + 4, 228);
  _write32Be(bytes, mobi + 8, 2);
  _write32Be(bytes, mobi + 12, 65001);
  _write32Be(bytes, mobi + 16, 17480);
  _write32Be(bytes, mobi + 20, 6);
  _write32Be(bytes, mobi + 68, titleOffset);
  _write32Be(bytes, mobi + 72, titleBytes.length);
  _write32Be(bytes, mobi + 96, 2);
  _write32Be(bytes, mobi + 100, 2);
  _write32Be(bytes, mobi + 152, 0xffffffff);
  bytes.setRange(record0Offset + titleOffset, record1Offset, titleBytes);
  bytes.setRange(record1Offset, record2Offset, compressed);
  bytes.setRange(record2Offset, record3Offset, huff);
  bytes.setRange(record3Offset, bytes.length, cdic);
  return bytes;
}

Uint8List indexedKf8KindleFixture({
  String title = 'Indexed KF8 Fixture',
  String text = 'Reconstructed KF8 text.',
}) {
  final titleBytes = utf8.encode(title);
  final fragment = utf8.encode(text);
  final prefix = utf8.encode('<html><body><p>');
  final suffix = utf8.encode('</p></body></html>');
  final skeleton = Uint8List.fromList(<int>[...prefix, ...suffix]);
  final raw = Uint8List.fromList(<int>[...skeleton, ...fragment]);

  final skeletonRoot = _kf8IndexRoot(const <List<int>>[
    <int>[1, 1, 1, 0],
    <int>[6, 2, 2, 0],
  ]);
  final skeletonEntry = _kf8IndexEntry('', <int>[
    3,
    0x81,
    0x80,
    0x80 | skeleton.length,
  ]);
  final fragmentRoot = _kf8IndexRoot(const <List<int>>[
    <int>[4, 1, 1, 0],
    <int>[6, 2, 2, 0],
  ]);
  final fragmentEntry = _kf8IndexEntry(prefix.length.toString(), <int>[
    3,
    0x80,
    0x80,
    0x80 | fragment.length,
  ]);

  const record0Offset = 128;
  const titleOffset = 300;
  final records = <Uint8List>[
    Uint8List(titleOffset + titleBytes.length),
    raw,
    skeletonRoot,
    skeletonEntry,
    fragmentRoot,
    fragmentEntry,
  ];
  final offsets = <int>[];
  var cursor = record0Offset;
  for (final record in records) {
    offsets.add(cursor);
    cursor += record.length;
  }
  final bytes = Uint8List(cursor);
  _writeAscii(bytes, 0, 'KF8 Fixture');
  _writeAscii(bytes, 60, 'BOOKMOBI');
  _write16Be(bytes, 76, records.length);
  for (var index = 0; index < offsets.length; index++) {
    _write32Be(bytes, 78 + index * 8, offsets[index]);
  }

  final record0 = records.first;
  _write16Be(record0, 0, 1);
  _write32Be(record0, 4, raw.length);
  _write16Be(record0, 8, 1);
  _write16Be(record0, 10, 4096);
  const mobi = 16;
  _writeAscii(record0, mobi, 'MOBI');
  _write32Be(record0, mobi + 4, 264);
  _write32Be(record0, mobi + 8, 2);
  _write32Be(record0, mobi + 12, 65001);
  _write32Be(record0, mobi + 16, 8080);
  _write32Be(record0, mobi + 20, 8);
  _write32Be(record0, mobi + 68, titleOffset);
  _write32Be(record0, mobi + 72, titleBytes.length);
  _write32Be(record0, mobi + 152, 0xffffffff);
  _write32Be(record0, mobi + 176, 0xffffffff);
  _write32Be(record0, mobi + 232, 4);
  _write32Be(record0, mobi + 236, 2);
  record0.setRange(titleOffset, titleOffset + titleBytes.length, titleBytes);
  for (var index = 0; index < records.length; index++) {
    bytes.setRange(
      offsets[index],
      offsets[index] + records[index].length,
      records[index],
    );
  }
  return bytes;
}

Uint8List _kf8IndexRoot(List<List<int>> definitions) {
  const headerLength = 56;
  final tagxLength = 12 + definitions.length * 4;
  final bytes = Uint8List(headerLength + tagxLength);
  _writeAscii(bytes, 0, 'INDX');
  _write32Be(bytes, 4, headerLength);
  _write32Be(bytes, 20, headerLength + tagxLength);
  _write32Be(bytes, 24, 1);
  _write32Be(bytes, 28, 65001);
  _writeAscii(bytes, headerLength, 'TAGX');
  _write32Be(bytes, headerLength + 4, tagxLength);
  _write32Be(bytes, headerLength + 8, 1);
  var cursor = headerLength + 12;
  for (final definition in definitions) {
    bytes.setRange(cursor, cursor + 4, definition);
    cursor += 4;
  }
  return bytes;
}

Uint8List _kf8IndexEntry(String name, List<int> payload) {
  const headerLength = 56;
  final nameBytes = utf8.encode(name);
  final entryOffset = headerLength;
  final entryLength = 1 + nameBytes.length + payload.length;
  final idxtOffset = entryOffset + entryLength;
  final bytes = Uint8List(idxtOffset + 6);
  _writeAscii(bytes, 0, 'INDX');
  _write32Be(bytes, 4, headerLength);
  _write32Be(bytes, 20, idxtOffset);
  _write32Be(bytes, 24, 1);
  _write32Be(bytes, 28, 65001);
  bytes[entryOffset] = nameBytes.length;
  bytes.setRange(
    entryOffset + 1,
    entryOffset + 1 + nameBytes.length,
    nameBytes,
  );
  bytes.setRange(entryOffset + 1 + nameBytes.length, idxtOffset, payload);
  _writeAscii(bytes, idxtOffset, 'IDXT');
  _write16Be(bytes, idxtOffset + 4, entryOffset);
  return bytes;
}

Uint8List tarFixture(Map<String, int> entries) {
  final output = BytesBuilder(copy: false);
  for (final entry in entries.entries) {
    final header = Uint8List(512);
    _writeAscii(header, 0, entry.key);
    _writeTarOctal(header, 100, 8, 0x1a4);
    _writeTarOctal(header, 108, 8, 0);
    _writeTarOctal(header, 116, 8, 0);
    _writeTarOctal(header, 124, 12, entry.value);
    _writeTarOctal(header, 136, 12, 0);
    header.fillRange(148, 156, 0x20);
    header[156] = 0x30;
    _writeAscii(header, 257, 'ustar');
    header[262] = 0;
    _writeAscii(header, 263, '00');
    final checksum = header.fold<int>(0, (sum, byte) => sum + byte);
    _writeTarOctal(header, 148, 8, checksum);
    output.add(header);
    output.add(Uint8List(entry.value));
    final padding = (512 - entry.value % 512) % 512;
    if (padding > 0) output.add(Uint8List(padding));
  }
  output.add(Uint8List(1024));
  return output.takeBytes();
}

Uint8List rar4Fixture(
  Map<String, int> entries, {
  int? declaredSize,
  bool encrypted = false,
  int method = 0x30,
}) {
  final output = BytesBuilder(copy: false)
    ..add(const <int>[0x52, 0x61, 0x72, 0x21, 0x1a, 0x07, 0x00])
    ..add(_rarBlock(type: 0x73, headerSize: 13));
  for (final entry in entries.entries) {
    final name = utf8.encode(entry.key);
    final data = Uint8List(entry.value);
    final headerSize = 32 + name.length;
    final header = Uint8List(headerSize);
    header[2] = 0x74;
    _write16Le(header, 3, 0x8000 | (encrypted ? 0x0004 : 0));
    _write16Le(header, 5, headerSize);
    _write32Le(header, 7, data.length);
    _write32Le(header, 11, declaredSize ?? data.length);
    _write32Le(header, 16, _crc32(data));
    header[24] = 20;
    header[25] = method;
    _write16Le(header, 26, name.length);
    header.setRange(32, header.length, name);
    _write16Le(header, 0, _crc32(header.sublist(2)) & 0xffff);
    output
      ..add(header)
      ..add(data);
  }
  output.add(_rarBlock(type: 0x7b, headerSize: 7));
  return output.takeBytes();
}

// Produced by RAR 7.00 with: rar a -m0 stored-small.rar small.jpg
Uint8List realRar5StoredFixture() => base64Decode(
  'UmFyIRoHAQAzkrXlCgEFBgAFAQGAgACfmtxMJwIDC5oABJoApIMCEW98ooAAAQlz'
  'bWFsbC5qcGcKAxMUk45qpgWiNXJlYWwgUkFSIHByb2R1Y2VyIGZpeHR1cmUKHXdW'
  'UQMFBAA=',
);

// Produced by RAR 7.00 with: rar a -m5 compressed-real.rar page.jpg
Uint8List realRar5CompressedFixture() => base64Decode(
  'UmFyIRoHAQDz4YLrCwEFBwAGAQGAgIAAA30WBiYCAwusAAShEKSDAuwJSfGABQEI'
  'cGFnZS5qcGcKAxPvko5qzfT9JcW2KSVVMvpS/2L+hGFyCCCnRwN/jDF3QTnLOO2U'
  'MLZq0VcV8d5HmX+syGzQHXdWUQMFBAA=',
);

// Produced by RAR 6.24 with: rar a -ep -ma4 -m5 pages.cbr 10.jpg 2.jpg
Uint8List realRar4CompressedComicFixture() => base64Decode(
  'UmFyIRoHAM+QcwAADQAAAAAAAAB7u3QgkCsAowAAAB4BAAADuTQEz7ReMV0dNQYApIEAADEwLmpwZwDwCiQWDBTYzL2YDRyykUkstv0tDQ4oIwEqkCO0GBwUSPxicTglKEPxErARiZm7waaabafJvYe8l7F+9g8W+CO3u5gtV2m4GMPgdyGgHmeyM95Oid6lCMSFJWuGpS2VsXRVR4NLaaqyJpeSstsK3BZSwNK0FOFS9GoL/g7YGpE3aHM3uNifCdtGMffV9VJ0n4HmQxvx5HubX84hh+sjLGQZxm3a8ruHRD0mdCCQKgCiAAAAHgEAAAMbkOBztF4xXR01BQCkgQAAMi5qcGcA8Fi7DwwU2My9mA0cspCSWW36Wl4yYmAlUgSOzmBwg/GJxOCUoQ/ESsQjEzu7waaabafJvYe8l7F+9g8W+CO4u6gtt+y8GMPod0GsHmeyM+BOid6lCMyFJWuGpS2VsXRVR6NLaaqyJpeystsK3FZSwNK0FONS+GoLLB28NSJvEOZvkbE+E7gNA/esKmTpPwPLjG/ns6pTa/nMMP3kZYyDUNJ7tzuXZMQ9ewBABwA=',
);

// Produced by RAR 6.24 with: rar a -ep -ma5 -m5 pages.cbr 10.jpg 2.jpg
Uint8List realRar5CompressedComicFixture() => base64Decode(
  'UmFyIRoHAQDz4YLrCwEFBwAGAQGAgIAA6szECyQCAwulAQSeAqSDArk0BM+ABQEGMTAuanBnCgMTRWSrakgUpgjEPKIwU0My9lBEcspFJLLbktDYcUEaBKpAjtBoOCiR8aTieJShlgI0Gu8PzMWLE0/jOVNtPyTSb97Q9LfFD6n5BcMN+AUofaPwNwXRJnteJSymS1jNDHNe9zXOfC2D6rKk5rjXZaRRJArr7ivy7KbJU0JbclSQyos/R8hFoo9xaopqKN0w+Y0j98Y10xSlgkalK/xmfDoy53EF+okNIQ2jZ046z7DVutyBIwIDC6MBBJ4CpIMCG5Dgc4AFAQUyLmpwZwoDE0Vkq2qNLiUGwzmgMFM0MvZQRHlymGfNtmWl4yaTQJVIEjs5oOEHxpOJxLKEuIRoNZw+SKKJp/E5U20/JNJv3tD0t8UPqfkFxy4ZBSh8o/A3hdU2m15lbK5rWM1Mc173Nc58bovhbBObJWW3EUiQK/C8sNOyoyVRCW3RUkMgX/0fMSaKvcWqSakjdMPoNg+/GdbQUp4JmKlfo2t1DpT53EV+okNIQ3DavHkfYB13VlEDBQQA',
);

Uint8List defaultCb7Fixture() => base64Decode(
  'N3q8ryccAARnVtd+hAAAAAAAAAAhAAAAAAAAANH44b3gASsACF0AAG/998ENIAAAAACBMweuD87ysgwHyEN/QbH6/kwKdmInG6P1wv8pPEqy3uo7dlwGglre6yYoxpWW3uO6eDElgiAATttV2cMbew0eC041NaBBw3yOdi4nFhC2ZdKTJY6XkA9Jmv0XZSNHZtfG2uif6HpAlgOa2jg+4lPnIAAXBhABCXQABwsBAAEjAwEBBV0AEAAADICaCgFhoQrHAAA=',
);

Uint8List encryptedHeaderCb7Fixture() => base64Decode(
  'N3q8ryccAASB5C93gAAAAAAAAAA7AAAAAAAAAB7nUOVsvVYSyVWAsOn2y43+ZR72L4KRQLZz7h2n6lpVSonBWsfbqlsNKqxWidOS7IV5cqYrj0X8RHz7hl+bIvEG2CHmQ1QcWcEmgv1qqOWn7HYS6qCzY9qWYV/fr7NEyfFyrFGr0WvscnDY/65os0SgzNBeefo/wsXhERyV+a0+K9NwmxcGEAEJcAAHCwEAAiQG8QcBElMPoYipN0PXFFQL3HEPg6VDJyMDAQEFXQAQAAABAAxtegoB39PNwgAA',
);

Uint8List sevenZipFixture(List<String> names, {bool encodedHeader = false}) {
  final nextHeader = <int>[];
  final packed = encodedHeader ? Uint8List(0) : Uint8List(names.length);
  if (encodedHeader) {
    nextHeader.add(0x17);
  } else {
    final nameBytes = <int>[0];
    for (final name in names) {
      for (final unit in name.codeUnits) {
        nameBytes
          ..add(unit & 0xff)
          ..add(unit >> 8);
      }
      nameBytes.addAll(const <int>[0, 0]);
    }
    nextHeader
      ..add(0x01)
      ..add(0x04)
      ..add(0x06)
      ..add(0)
      ..addAll(_sevenZipInt(names.length))
      ..add(0x09);
    for (var index = 0; index < names.length; index++) {
      nextHeader.add(1);
    }
    nextHeader
      ..add(0x00)
      ..add(0x07)
      ..add(0x0b)
      ..addAll(_sevenZipInt(names.length))
      ..add(0);
    for (var index = 0; index < names.length; index++) {
      nextHeader.addAll(const <int>[1, 1, 0]);
    }
    nextHeader.add(0x0c);
    for (var index = 0; index < names.length; index++) {
      nextHeader.add(1);
    }
    nextHeader
      ..add(0x00)
      ..add(0x08)
      ..add(0x0a)
      ..add(1);
    for (var index = 0; index < names.length; index++) {
      nextHeader.addAll(_little32(_crc32(const <int>[0])));
    }
    nextHeader
      ..add(0x00)
      ..add(0x00)
      ..add(0x05)
      ..addAll(_sevenZipInt(names.length))
      ..add(0x11)
      ..addAll(_sevenZipInt(nameBytes.length))
      ..addAll(nameBytes)
      ..add(0x00)
      ..add(0x00);
  }
  final bytes = Uint8List(32 + packed.length + nextHeader.length);
  bytes.setRange(0, 6, const <int>[0x37, 0x7a, 0xbc, 0xaf, 0x27, 0x1c]);
  bytes[7] = 4;
  _write64Le(bytes, 12, packed.length);
  _write64Le(bytes, 20, nextHeader.length);
  _write32Le(bytes, 28, _crc32(nextHeader));
  _write32Le(bytes, 8, _crc32(bytes.sublist(12, 32)));
  bytes.setRange(32, 32 + packed.length, packed);
  bytes.setRange(32 + packed.length, bytes.length, nextHeader);
  return bytes;
}

Uint8List rar5Fixture(Map<String, int> entries) {
  final output = BytesBuilder(copy: false)
    ..add(const <int>[0x52, 0x61, 0x72, 0x21, 0x1a, 0x07, 0x01, 0x00])
    ..add(_rar5Block(<int>[1, 0]));
  for (final entry in entries.entries) {
    final name = utf8.encode(entry.key);
    final header = <int>[
      2,
      2,
      entry.value,
      0,
      entry.value,
      0,
      0,
      0,
      name.length,
      ...name,
    ];
    output
      ..add(_rar5Block(header))
      ..add(Uint8List(entry.value));
  }
  output.add(_rar5Block(<int>[5, 0]));
  return output.takeBytes();
}

Uint8List _rarBlock({required int type, required int headerSize}) {
  final bytes = Uint8List(headerSize);
  bytes[2] = type;
  _write16Le(bytes, 5, headerSize);
  _write16Le(bytes, 0, _crc32(bytes.sublist(2)) & 0xffff);
  return bytes;
}

Uint8List _rar5Block(List<int> header) {
  final size = _sevenZipInt(header.length);
  final crcInput = <int>[...size, ...header];
  final bytes = Uint8List(4 + crcInput.length);
  _write32Le(bytes, 0, _crc32(crcInput));
  bytes.setRange(4, bytes.length, crcInput);
  return bytes;
}

List<int> _little32(int value) => <int>[
  value & 0xff,
  (value >> 8) & 0xff,
  (value >> 16) & 0xff,
  (value >> 24) & 0xff,
];

List<int> _sevenZipInt(int value) {
  if (value < 0x80) return <int>[value];
  throw ArgumentError.value(value, 'value', 'Fixture integer is too large');
}

void _writeAscii(Uint8List bytes, int offset, String value) {
  final encoded = ascii.encode(value);
  bytes.setRange(offset, offset + encoded.length, encoded);
}

void _writeTarOctal(Uint8List bytes, int offset, int length, int value) {
  final encoded = value.toRadixString(8).padLeft(length - 2, '0');
  _writeAscii(bytes, offset, '$encoded\u0000 ');
}

void _write16Be(Uint8List bytes, int offset, int value) {
  bytes[offset] = value >> 8;
  bytes[offset + 1] = value;
}

void _write16Le(Uint8List bytes, int offset, int value) {
  bytes[offset] = value;
  bytes[offset + 1] = value >> 8;
}

void _write32Be(Uint8List bytes, int offset, int value) {
  for (var index = 0; index < 4; index++) {
    bytes[offset + index] = value >> ((3 - index) * 8);
  }
}

void _write32Le(Uint8List bytes, int offset, int value) {
  for (var index = 0; index < 4; index++) {
    bytes[offset + index] = value >> (index * 8);
  }
}

void _write64Le(Uint8List bytes, int offset, int value) {
  for (var index = 0; index < 8; index++) {
    bytes[offset + index] = value >> (index * 8);
  }
}

int _crc32(List<int> bytes) {
  var crc = 0xffffffff;
  for (final byte in bytes) {
    crc ^= byte;
    for (var bit = 0; bit < 8; bit++) {
      crc = (crc & 1) == 0 ? crc >> 1 : (crc >> 1) ^ 0xedb88320;
    }
  }
  return (crc ^ 0xffffffff) & 0xffffffff;
}
