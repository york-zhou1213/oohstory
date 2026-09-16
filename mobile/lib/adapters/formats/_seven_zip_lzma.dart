import 'dart:typed_data';

// The raw LZMA decoder follows the range-coding structure used by
// package:archive's MIT-licensed LzmaDecoder.
// Copyright (c) 2013-2021 Brendan Duncan.
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to
// deal in the Software without restriction, including without limitation the
// rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
// sell copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in
// all copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
// FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
// IN THE SOFTWARE.

Uint8List decodeSevenZipLzma(
  List<int> input,
  int outputLength,
  List<int> properties,
) {
  if (properties.length != 5 || outputLength < 0) {
    throw const FormatException('Invalid LZMA header properties');
  }
  final decoder = _LzmaDecoder(outputLength);
  decoder.reset(properties[0], resetDictionary: true);
  decoder.decodeChunk(input, outputLength);
  return decoder.output;
}

class _LzmaDecoder {
  _LzmaDecoder(int outputLength) : output = Uint8List(outputLength) {
    for (var index = 0; index < _State.values.length; index++) {
      _nonLiteral.add(_ProbabilityTable(1 << 4));
      _longRepeat0.add(_ProbabilityTable(1 << 4));
    }
    _matchLength = _LengthDecoder(_range, 4);
    _repeatLength = _LengthDecoder(_range, 4);
    _distance = _DistanceDecoder(_range);
  }

  final Uint8List output;
  final _RangeDecoder _range = _RangeDecoder();
  final List<_ProbabilityTable> _nonLiteral = <_ProbabilityTable>[];
  final _ProbabilityTable _repeat = _ProbabilityTable(_State.values.length);
  final _ProbabilityTable _repeat0 = _ProbabilityTable(_State.values.length);
  final List<_ProbabilityTable> _longRepeat0 = <_ProbabilityTable>[];
  final _ProbabilityTable _repeat1 = _ProbabilityTable(_State.values.length);
  final _ProbabilityTable _repeat2 = _ProbabilityTable(_State.values.length);
  final List<_ProbabilityTable> _literal = <_ProbabilityTable>[];
  final List<_ProbabilityTable> _matchLiteral0 = <_ProbabilityTable>[];
  final List<_ProbabilityTable> _matchLiteral1 = <_ProbabilityTable>[];
  late final _LengthDecoder _matchLength;
  late final _LengthDecoder _repeatLength;
  late final _DistanceDecoder _distance;

  int position = 0;
  int _dictionaryStart = 0;
  int _positionBits = 2;
  int _literalPositionBits = 0;
  int _literalContextBits = 3;
  int _distance0 = 0;
  int _distance1 = 0;
  int _distance2 = 0;
  int _distance3 = 0;
  _State _state = _State.literal;

  void reset(int? property, {required bool resetDictionary}) {
    if (property != null) {
      if (property >= 225) {
        throw const FormatException('Invalid LZMA properties');
      }
      var value = property;
      _literalContextBits = value % 9;
      value ~/= 9;
      _literalPositionBits = value % 5;
      _positionBits = value ~/ 5;
    }
    if (_positionBits > 4 || _literalPositionBits > 4) {
      throw const FormatException('Unsupported LZMA properties');
    }
    if (resetDictionary) resetDictionaryState();
    _state = _State.literal;
    _distance0 = 0;
    _distance1 = 0;
    _distance2 = 0;
    _distance3 = 0;
    for (final table in _nonLiteral) {
      table.reset();
    }
    _repeat.reset();
    _repeat0.reset();
    for (final table in _longRepeat0) {
      table.reset();
    }
    _repeat1.reset();
    _repeat2.reset();
    final literalStates = 1 << (_literalPositionBits + _literalContextBits);
    while (_literal.length < literalStates) {
      _literal.add(_ProbabilityTable(0x100));
      _matchLiteral0.add(_ProbabilityTable(0x100));
      _matchLiteral1.add(_ProbabilityTable(0x100));
    }
    for (var index = 0; index < literalStates; index++) {
      _literal[index].reset();
      _matchLiteral0[index].reset();
      _matchLiteral1[index].reset();
    }
    final positions = 1 << _positionBits;
    _matchLength.reset(positions);
    _repeatLength.reset(positions);
    _distance.reset();
  }

  void resetDictionaryState() {
    _dictionaryStart = position;
  }

  void decodeChunk(List<int> input, int length) {
    final end = position + length;
    if (end > output.length) {
      throw const FormatException('LZMA output exceeds declared size');
    }
    _range.initialize(input);
    while (position < end) {
      final positionState = position & ((1 << _positionBits) - 1);
      if (_range.readBit(_nonLiteral[_state.index], positionState) == 0) {
        _decodeLiteral();
      } else if (_range.readBit(_repeat, _state.index) == 0) {
        _decodeMatch(positionState, end);
      } else {
        _decodeRepeat(positionState, end);
      }
    }
  }

  bool get _previousWasLiteral => _state.index < 7;

  void _decodeLiteral() {
    var previous = position > _dictionaryStart ? output[position - 1] : 0;
    final low = previous >> (8 - _literalContextBits);
    final high =
        (position & ((1 << _literalPositionBits) - 1)) << _literalContextBits;
    final tableIndex = low + high;
    final table = _literal[tableIndex];
    int value;
    if (_previousWasLiteral) {
      value = _range.readBitTree(table, 8);
    } else {
      final matchPosition = position - _distance0 - 1;
      if (matchPosition < _dictionaryStart) {
        throw const FormatException('Invalid LZMA match distance');
      }
      previous = output[matchPosition];
      value = 0;
      var prefix = 1;
      var matched = true;
      for (var index = 0; index < 8; index++) {
        int bit;
        if (matched) {
          final matchBit = (previous >> 7) & 1;
          previous = (previous << 1) & 0xff;
          bit = _range.readBit(
            matchBit == 0
                ? _matchLiteral0[tableIndex]
                : _matchLiteral1[tableIndex],
            prefix | value,
          );
          matched = bit == matchBit;
        } else {
          bit = _range.readBit(table, prefix | value);
        }
        value = (value << 1) | bit;
        prefix <<= 1;
      }
    }
    output[position++] = value;
    _state = switch (_state) {
      _State.literal ||
      _State.matchLiteralLiteral ||
      _State.repeatLiteralLiteral ||
      _State.shortRepeatLiteralLiteral => _State.literal,
      _State.matchLiteral => _State.matchLiteralLiteral,
      _State.repeatLiteral => _State.repeatLiteralLiteral,
      _State.shortRepeatLiteral => _State.shortRepeatLiteralLiteral,
      _State.literalMatch || _State.nonLiteralMatch => _State.matchLiteral,
      _State.literalLongRepeat ||
      _State.nonLiteralRepeat => _State.repeatLiteral,
      _State.literalShortRepeat => _State.shortRepeatLiteral,
    };
  }

  void _decodeMatch(int positionState, int end) {
    final length = _matchLength.read(positionState);
    final distance = _distance.read(length);
    _repeatBytes(distance, length, end);
    _distance3 = _distance2;
    _distance2 = _distance1;
    _distance1 = _distance0;
    _distance0 = distance;
    _state = _previousWasLiteral ? _State.literalMatch : _State.nonLiteralMatch;
  }

  void _decodeRepeat(int positionState, int end) {
    int distance;
    if (_range.readBit(_repeat0, _state.index) == 0) {
      if (_range.readBit(_longRepeat0[_state.index], positionState) == 0) {
        _repeatBytes(_distance0, 1, end);
        _state = _previousWasLiteral
            ? _State.literalShortRepeat
            : _State.nonLiteralRepeat;
        return;
      }
      distance = _distance0;
    } else if (_range.readBit(_repeat1, _state.index) == 0) {
      distance = _distance1;
      _distance1 = _distance0;
      _distance0 = distance;
    } else if (_range.readBit(_repeat2, _state.index) == 0) {
      distance = _distance2;
      _distance2 = _distance1;
      _distance1 = _distance0;
      _distance0 = distance;
    } else {
      distance = _distance3;
      _distance3 = _distance2;
      _distance2 = _distance1;
      _distance1 = _distance0;
      _distance0 = distance;
    }
    _repeatBytes(distance, _repeatLength.read(positionState), end);
    _state = _previousWasLiteral
        ? _State.literalLongRepeat
        : _State.nonLiteralRepeat;
  }

  void _repeatBytes(int distance, int length, int end) {
    if (distance < 0 || position - distance - 1 < _dictionaryStart) {
      throw const FormatException('Invalid LZMA match distance');
    }
    if (position + length > end) {
      throw const FormatException('LZMA match exceeds declared size');
    }
    for (var index = 0; index < length; index++) {
      output[position] = output[position - distance - 1];
      position++;
    }
  }
}

enum _State {
  literal,
  matchLiteralLiteral,
  repeatLiteralLiteral,
  shortRepeatLiteralLiteral,
  matchLiteral,
  repeatLiteral,
  shortRepeatLiteral,
  literalMatch,
  literalLongRepeat,
  literalShortRepeat,
  nonLiteralMatch,
  nonLiteralRepeat,
}

class _LengthDecoder {
  _LengthDecoder(this._range, int positions) {
    reset(positions);
  }

  final _RangeDecoder _range;
  final _ProbabilityTable _choice = _ProbabilityTable(2);
  final _ProbabilityTable _long = _ProbabilityTable(0x100);
  final List<_ProbabilityTable> _short = <_ProbabilityTable>[];
  final List<_ProbabilityTable> _medium = <_ProbabilityTable>[];

  void reset(int positions) {
    _choice.reset();
    _long.reset();
    while (_short.length < positions) {
      _short.add(_ProbabilityTable(8));
      _medium.add(_ProbabilityTable(8));
    }
    for (var index = 0; index < positions; index++) {
      _short[index].reset();
      _medium[index].reset();
    }
  }

  int read(int positionState) {
    if (_range.readBit(_choice, 0) == 0) {
      return 2 + _range.readBitTree(_short[positionState], 3);
    }
    if (_range.readBit(_choice, 1) == 0) {
      return 10 + _range.readBitTree(_medium[positionState], 3);
    }
    return 18 + _range.readBitTree(_long, 8);
  }
}

class _DistanceDecoder {
  _DistanceDecoder(this._range) {
    for (var index = 0; index < 4; index++) {
      _slots.add(_ProbabilityTable(64));
    }
    for (var slot = 4; slot < 14; slot++) {
      _short.add(_ProbabilityTable(1 << (slot ~/ 2 - 1)));
    }
  }

  final _RangeDecoder _range;
  final List<_ProbabilityTable> _slots = <_ProbabilityTable>[];
  final List<_ProbabilityTable> _short = <_ProbabilityTable>[];
  final _ProbabilityTable _align = _ProbabilityTable(16);

  void reset() {
    for (final table in _slots) {
      table.reset();
    }
    for (final table in _short) {
      table.reset();
    }
    _align.reset();
  }

  int read(int length) {
    final state = (length - 2).clamp(0, 3);
    final slot = _range.readBitTree(_slots[state], 6);
    if (slot < 4) return slot;
    final bits = slot ~/ 2 - 1;
    final prefix = 2 | (slot & 1);
    if (slot < 14) {
      return (prefix << bits) |
          _range.readReverseBitTree(_short[slot - 4], bits);
    }
    return (prefix << bits) |
        (_range.readDirect(bits - 4) << 4) |
        _range.readReverseBitTree(_align, 4);
  }
}

class _ProbabilityTable {
  _ProbabilityTable(int length) : values = Uint16List(length) {
    reset();
  }

  final Uint16List values;

  void reset() => values.fillRange(0, values.length, 1 << 10);
}

class _RangeDecoder {
  List<int> _input = const <int>[];
  int _offset = 0;
  int _range = 0xffffffff;
  int _code = 0;

  void initialize(List<int> input) {
    if (input.length < 5 || input[0] != 0) {
      throw const FormatException('Invalid LZMA range header');
    }
    _input = input;
    _offset = 1;
    _range = 0xffffffff;
    _code = 0;
    for (var index = 0; index < 4; index++) {
      _code = ((_code << 8) | _readByte()) & 0xffffffff;
    }
  }

  int readBit(_ProbabilityTable table, int index) {
    _normalize();
    final probability = table.values[index];
    final bound = (_range >> 11) * probability;
    if (_code < bound) {
      _range = bound;
      table.values[index] += (2048 - probability) >> 5;
      return 0;
    }
    _range = (_range - bound) & 0xffffffff;
    _code = (_code - bound) & 0xffffffff;
    table.values[index] -= probability >> 5;
    return 1;
  }

  int readBitTree(_ProbabilityTable table, int count) {
    var value = 0;
    var prefix = 1;
    for (var index = 0; index < count; index++) {
      final bit = readBit(table, prefix | value);
      value = (value << 1) | bit;
      prefix <<= 1;
    }
    return value;
  }

  int readReverseBitTree(_ProbabilityTable table, int count) {
    var value = 0;
    var prefix = 1;
    for (var index = 0; index < count; index++) {
      final bit = readBit(table, prefix | value);
      value |= bit << index;
      prefix <<= 1;
    }
    return value;
  }

  int readDirect(int count) {
    var value = 0;
    for (var index = 0; index < count; index++) {
      _normalize();
      _range >>= 1;
      final difference = _code - _range;
      if (difference < 0) {
        value <<= 1;
      } else {
        _code = difference;
        value = (value << 1) | 1;
      }
    }
    return value;
  }

  void _normalize() {
    if (_range < 1 << 24) {
      _range = (_range << 8) & 0xffffffff;
      _code = ((_code << 8) | _readByte()) & 0xffffffff;
    }
  }

  int _readByte() {
    if (_offset >= _input.length) {
      throw const FormatException('Truncated LZMA stream');
    }
    return _input[_offset++];
  }
}
