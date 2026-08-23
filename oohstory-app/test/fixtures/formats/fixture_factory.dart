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
  _write32Be(bytes, mobi + 84, titleOffset);
  _write32Be(bytes, mobi + 88, titleBytes.length);
  _write32Be(bytes, mobi + 168, 0xffffffff);
  bytes.setRange(record0Offset + titleOffset, record1Offset, titleBytes);
  bytes.setRange(record1Offset, bytes.length, bodyBytes);
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
    header[24] = 20;
    header[25] = 0x30;
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
