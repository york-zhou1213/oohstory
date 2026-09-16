import 'dart:typed_data';

import '../../core/errors.dart';

Future<Uint8List> collectFormatBytes(
  Stream<List<int>> stream, {
  required int maxBytes,
}) async {
  final builder = BytesBuilder(copy: false);
  var length = 0;
  await for (final chunk in stream) {
    length += chunk.length;
    if (length > maxBytes) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Format input exceeds the configured size limit',
      );
    }
    try {
      builder.add(chunk);
    } on RangeError {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Format input contains invalid bytes',
      );
    }
  }
  return builder.takeBytes();
}

int uint16Be(List<int> bytes, int offset) {
  requireRange(bytes, offset, 2);
  return (bytes[offset] << 8) | bytes[offset + 1];
}

int uint16Le(List<int> bytes, int offset) {
  requireRange(bytes, offset, 2);
  return bytes[offset] | (bytes[offset + 1] << 8);
}

int uint32Be(List<int> bytes, int offset) {
  requireRange(bytes, offset, 4);
  return (bytes[offset] << 24) |
      (bytes[offset + 1] << 16) |
      (bytes[offset + 2] << 8) |
      bytes[offset + 3];
}

int uint32Le(List<int> bytes, int offset) {
  requireRange(bytes, offset, 4);
  return bytes[offset] |
      (bytes[offset + 1] << 8) |
      (bytes[offset + 2] << 16) |
      (bytes[offset + 3] << 24);
}

int uint64Le(List<int> bytes, int offset) {
  requireRange(bytes, offset, 8);
  var value = 0;
  for (var index = 7; index >= 0; index--) {
    value = (value << 8) | bytes[offset + index];
  }
  return value;
}

void requireRange(List<int> bytes, int offset, int length) {
  if (offset < 0 || length < 0 || offset + length > bytes.length) {
    throw const FormatException('Unexpected end of format input');
  }
}

int crc32(List<int> bytes, [int start = 0, int? end]) {
  var crc = 0xffffffff;
  final stop = end ?? bytes.length;
  requireRange(bytes, start, stop - start);
  for (var index = start; index < stop; index++) {
    crc ^= bytes[index];
    for (var bit = 0; bit < 8; bit++) {
      crc = (crc & 1) == 0 ? crc >> 1 : (crc >> 1) ^ 0xedb88320;
    }
  }
  return (crc ^ 0xffffffff) & 0xffffffff;
}

class ByteCursor {
  ByteCursor(this.bytes, {this.offset = 0, int? end})
    : end = end ?? bytes.length;

  final List<int> bytes;
  int offset;
  final int end;

  int readByte() {
    requireRange(bytes, offset, 1);
    if (offset >= end) throw const FormatException('Unexpected end of header');
    return bytes[offset++];
  }

  List<int> readBytes(int length) {
    if (offset + length > end) {
      throw const FormatException('Unexpected end of header');
    }
    final value = bytes.sublist(offset, offset + length);
    offset += length;
    return value;
  }

  void skip(int length) {
    readBytes(length);
  }

  int read7zUint64() {
    final first = readByte();
    var mask = 0x80;
    var value = 0;
    for (var index = 0; index < 8; index++) {
      if ((first & mask) == 0) {
        return value | ((first & (mask - 1)) << (index * 8));
      }
      value |= readByte() << (index * 8);
      mask >>= 1;
    }
    return value;
  }

  int readRarUint64() {
    var value = 0;
    var shift = 0;
    for (var index = 0; index < 10; index++) {
      final byte = readByte();
      value |= (byte & 0x7f) << shift;
      if ((byte & 0x80) == 0) return value;
      shift += 7;
    }
    throw const FormatException('RAR integer is too large');
  }
}
