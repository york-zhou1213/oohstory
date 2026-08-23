import '../../core/errors.dart';

List<int> decodeZlib(List<int> bytes, {required int maxOutputBytes}) {
  if (maxOutputBytes < 0 || bytes.length < 6) {
    throw const FormatException('MDX zlib block is malformed');
  }
  final cmf = bytes[0];
  final flags = bytes[1];
  if ((cmf & 0x0f) != 8 ||
      (cmf >> 4) > 7 ||
      ((cmf << 8) | flags) % 31 != 0 ||
      (flags & 0x20) != 0) {
    throw const FormatException('MDX zlib header is invalid');
  }

  final expectedAdler =
      (bytes[bytes.length - 4] << 24) |
      (bytes[bytes.length - 3] << 16) |
      (bytes[bytes.length - 2] << 8) |
      bytes[bytes.length - 1];
  final reader = _DeflateReader(bytes, 2, bytes.length - 4, maxOutputBytes);
  final output = reader.inflate();
  if (_adler32(output) != expectedAdler) {
    throw const FormatException('MDX zlib checksum mismatch');
  }
  return output;
}

class _DeflateReader {
  _DeflateReader(this.bytes, this.offset, this.end, this.maxOutputBytes);

  static const List<int> _codeLengthOrder = <int>[
    16,
    17,
    18,
    0,
    8,
    7,
    9,
    6,
    10,
    5,
    11,
    4,
    12,
    3,
    13,
    2,
    14,
    1,
    15,
  ];
  static const List<int> _lengthBases = <int>[
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    13,
    15,
    17,
    19,
    23,
    27,
    31,
    35,
    43,
    51,
    59,
    67,
    83,
    99,
    115,
    131,
    163,
    195,
    227,
    258,
  ];
  static const List<int> _lengthExtra = <int>[
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    1,
    1,
    1,
    1,
    2,
    2,
    2,
    2,
    3,
    3,
    3,
    3,
    4,
    4,
    4,
    4,
    5,
    5,
    5,
    5,
    0,
  ];
  static const List<int> _distanceBases = <int>[
    1,
    2,
    3,
    4,
    5,
    7,
    9,
    13,
    17,
    25,
    33,
    49,
    65,
    97,
    129,
    193,
    257,
    385,
    513,
    769,
    1025,
    1537,
    2049,
    3073,
    4097,
    6145,
    8193,
    12289,
    16385,
    24577,
  ];
  static const List<int> _distanceExtra = <int>[
    0,
    0,
    0,
    0,
    1,
    1,
    2,
    2,
    3,
    3,
    4,
    4,
    5,
    5,
    6,
    6,
    7,
    7,
    8,
    8,
    9,
    9,
    10,
    10,
    11,
    11,
    12,
    12,
    13,
    13,
  ];

  final List<int> bytes;
  int offset;
  final int end;
  final int maxOutputBytes;
  int _bits = 0;
  int _bitCount = 0;
  final List<int> _output = <int>[];

  List<int> inflate() {
    try {
      var isFinal = false;
      while (!isFinal) {
        isFinal = _readBits(1) != 0;
        switch (_readBits(2)) {
          case 0:
            _storedBlock();
          case 1:
            _compressedBlock(_fixedLiteralTable(), _fixedDistanceTable());
          case 2:
            final tables = _dynamicTables();
            _compressedBlock(tables.$1, tables.$2);
          default:
            throw const FormatException('Reserved DEFLATE block type');
        }
      }
      if (offset != end) {
        throw const FormatException('DEFLATE stream has trailing data');
      }
      return List<int>.unmodifiable(_output);
    } on CoreException {
      rethrow;
    } on FormatException {
      rethrow;
    } on Object {
      throw const FormatException('MDX zlib block is malformed');
    }
  }

  void _storedBlock() {
    _bits = 0;
    _bitCount = 0;
    if (offset + 4 > end) throw const FormatException('Truncated block');
    final length = bytes[offset] | (bytes[offset + 1] << 8);
    final inverse = bytes[offset + 2] | (bytes[offset + 3] << 8);
    offset += 4;
    if ((length ^ 0xffff) != inverse || offset + length > end) {
      throw const FormatException('Invalid stored DEFLATE block');
    }
    _ensureCapacity(length);
    _output.addAll(bytes.sublist(offset, offset + length));
    offset += length;
  }

  void _compressedBlock(_Huffman literals, _Huffman distances) {
    while (true) {
      final symbol = literals.read(this);
      if (symbol < 256) {
        _ensureCapacity(1);
        _output.add(symbol);
        continue;
      }
      if (symbol == 256) return;
      if (symbol < 257 || symbol > 285) {
        throw const FormatException('Invalid DEFLATE length symbol');
      }
      final lengthIndex = symbol - 257;
      final length =
          _lengthBases[lengthIndex] + _readBits(_lengthExtra[lengthIndex]);
      final distanceSymbol = distances.read(this);
      if (distanceSymbol >= _distanceBases.length) {
        throw const FormatException('Invalid DEFLATE distance symbol');
      }
      final distance =
          _distanceBases[distanceSymbol] +
          _readBits(_distanceExtra[distanceSymbol]);
      if (distance <= 0 || distance > _output.length) {
        throw const FormatException('Invalid DEFLATE back-reference');
      }
      _ensureCapacity(length);
      for (var index = 0; index < length; index++) {
        _output.add(_output[_output.length - distance]);
      }
    }
  }

  (_Huffman, _Huffman) _dynamicTables() {
    final literalCount = _readBits(5) + 257;
    final distanceCount = _readBits(5) + 1;
    final codeLengthCount = _readBits(4) + 4;
    final codeLengths = List<int>.filled(19, 0);
    for (var index = 0; index < codeLengthCount; index++) {
      codeLengths[_codeLengthOrder[index]] = _readBits(3);
    }
    final codeLengthTable = _Huffman(codeLengths);
    final lengths = <int>[];
    final total = literalCount + distanceCount;
    while (lengths.length < total) {
      final symbol = codeLengthTable.read(this);
      if (symbol <= 15) {
        lengths.add(symbol);
        continue;
      }
      final (value, repeat) = switch (symbol) {
        16 => (
          lengths.isEmpty
              ? throw const FormatException('Invalid code-length repeat')
              : lengths.last,
          _readBits(2) + 3,
        ),
        17 => (0, _readBits(3) + 3),
        18 => (0, _readBits(7) + 11),
        _ => throw const FormatException('Invalid code-length symbol'),
      };
      if (repeat > total - lengths.length) {
        throw const FormatException('Code-length repeat overflows table');
      }
      lengths.addAll(List<int>.filled(repeat, value));
    }
    final literalLengths = lengths.sublist(0, literalCount);
    if (literalLengths[256] == 0) {
      throw const FormatException('DEFLATE end marker is missing');
    }
    return (
      _Huffman(literalLengths),
      _Huffman(lengths.sublist(literalCount), allowEmpty: true),
    );
  }

  int _readBits(int count) {
    if (count == 0) return 0;
    while (_bitCount < count) {
      if (offset >= end) throw const FormatException('Truncated DEFLATE data');
      _bits |= bytes[offset++] << _bitCount;
      _bitCount += 8;
    }
    final value = _bits & ((1 << count) - 1);
    _bits >>= count;
    _bitCount -= count;
    return value;
  }

  void _ensureCapacity(int additional) {
    if (additional < 0 || additional > maxOutputBytes - _output.length) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'MDX expanded data exceeds the configured size limit',
      );
    }
  }

  static _Huffman _fixedLiteralTable() => _Huffman(<int>[
    ...List<int>.filled(144, 8),
    ...List<int>.filled(112, 9),
    ...List<int>.filled(24, 7),
    ...List<int>.filled(8, 8),
  ]);

  static _Huffman _fixedDistanceTable() => _Huffman(List<int>.filled(32, 5));
}

class _Huffman {
  _Huffman(List<int> lengths, {bool allowEmpty = false}) {
    final counts = List<int>.filled(16, 0);
    for (final length in lengths) {
      if (length < 0 || length > 15) {
        throw const FormatException('Invalid Huffman code length');
      }
      if (length > 0) counts[length]++;
    }
    if (counts.skip(1).every((count) => count == 0)) {
      if (allowEmpty) return;
      throw const FormatException('Empty Huffman table');
    }
    var available = 1;
    for (var length = 1; length <= 15; length++) {
      available = (available << 1) - counts[length];
      if (available < 0) throw const FormatException('Oversubscribed table');
    }
    final nextCode = List<int>.filled(16, 0);
    var code = 0;
    for (var length = 1; length <= 15; length++) {
      code = (code + counts[length - 1]) << 1;
      nextCode[length] = code;
    }
    for (var symbol = 0; symbol < lengths.length; symbol++) {
      final length = lengths[symbol];
      if (length == 0) continue;
      final reversed = _reverseBits(nextCode[length]++, length);
      _symbols[(length << 16) | reversed] = symbol;
      if (length > maxLength) maxLength = length;
    }
  }

  final Map<int, int> _symbols = <int, int>{};
  int maxLength = 0;

  int read(_DeflateReader reader) {
    if (maxLength == 0) throw const FormatException('Empty distance table');
    var code = 0;
    for (var length = 1; length <= maxLength; length++) {
      code |= reader._readBits(1) << (length - 1);
      final symbol = _symbols[(length << 16) | code];
      if (symbol != null) return symbol;
    }
    throw const FormatException('Invalid Huffman symbol');
  }
}

int _reverseBits(int value, int length) {
  var reversed = 0;
  for (var index = 0; index < length; index++) {
    reversed = (reversed << 1) | ((value >> index) & 1);
  }
  return reversed;
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
