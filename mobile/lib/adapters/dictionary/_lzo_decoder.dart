import 'dart:typed_data';

/// Decodes one raw LZO1X block used by MDX/MDD compression type 1.
///
/// The caller supplies the exact decoded length from the MDX block table.
/// Every input read, output write, and back-reference is bounded.
Uint8List decodeLzo1x(
  List<int> input, {
  required int expectedSize,
  required int maxOutputBytes,
}) {
  if (expectedSize < 0 || expectedSize > maxOutputBytes) {
    throw const FormatException('LZO output exceeds the configured limit');
  }
  if (input.isEmpty) throw const FormatException('LZO stream is empty');
  final source = input is Uint8List ? input : Uint8List.fromList(input);
  final output = Uint8List(expectedSize);
  var inputOffset = 0;
  var outputOffset = 0;

  int readByte() {
    if (inputOffset >= source.length) {
      throw const FormatException('LZO stream is truncated');
    }
    return source[inputOffset++];
  }

  void copyLiterals(int count) {
    if (count < 0 ||
        inputOffset + count > source.length ||
        outputOffset + count > output.length) {
      throw const FormatException('LZO literal run is invalid');
    }
    output.setRange(outputOffset, outputOffset + count, source, inputOffset);
    inputOffset += count;
    outputOffset += count;
  }

  void copyMatch(int position, int count) {
    if (position < 0 ||
        position >= outputOffset ||
        count < 0 ||
        outputOffset + count > output.length) {
      throw const FormatException('LZO match is invalid');
    }
    for (var index = 0; index < count; index++) {
      output[outputOffset++] = output[position++];
    }
  }

  int extendedCount(int base) {
    var count = 0;
    while (true) {
      final value = readByte();
      if (value != 0) return count + base + value;
      count += 255;
      if (count > expectedSize) {
        throw const FormatException('LZO length is invalid');
      }
    }
  }

  var state = _LzoState.literal;
  if (source.first > 17) {
    final count = readByte() - 17;
    copyLiterals(count);
    state = count < 4 ? _LzoState.nextMatch : _LzoState.firstMatch;
  }

  while (true) {
    var token = readByte();
    if (state == _LzoState.literal) {
      if (token < 16) {
        if (token == 0) token = extendedCount(15);
        copyLiterals(token + 3);
        state = _LzoState.firstMatch;
        continue;
      }
    } else if (token < 16) {
      final tokenOffset = inputOffset - 1;
      if (state == _LzoState.firstMatch) {
        final position =
            outputOffset - (1 + 0x800) - (token >> 2) - (readByte() << 2);
        copyMatch(position, 3);
      } else {
        final position = outputOffset - 1 - (token >> 2) - (readByte() << 2);
        copyMatch(position, 2);
      }
      final trailing = source[tokenOffset] & 3;
      if (trailing > 0) {
        copyLiterals(trailing);
        state = _LzoState.nextMatch;
      } else {
        state = _LzoState.literal;
      }
      continue;
    }

    late int position;
    late int length;
    late int trailingSource;
    if (token >= 64) {
      position = outputOffset - 1 - ((token >> 2) & 7) - (readByte() << 3);
      length = (token >> 5) + 1;
      trailingSource = inputOffset - 2;
    } else if (token >= 32) {
      length = (token & 31) == 0 ? extendedCount(31) + 2 : (token & 31) + 2;
      final first = readByte();
      final second = readByte();
      position = outputOffset - 1 - ((first >> 2) + (second << 6));
      trailingSource = inputOffset - 2;
    } else {
      position = outputOffset - ((token & 8) << 11);
      length = (token & 7) == 0 ? extendedCount(7) + 2 : (token & 7) + 2;
      final first = readByte();
      final second = readByte();
      position -= (first >> 2) + (second << 6);
      trailingSource = inputOffset - 2;
      if (position == outputOffset) {
        if (outputOffset != expectedSize || inputOffset != source.length) {
          throw const FormatException('LZO stream size mismatch');
        }
        return output;
      }
      position -= 0x4000;
    }
    copyMatch(position, length);
    final trailing = source[trailingSource] & 3;
    if (trailing > 0) {
      copyLiterals(trailing);
      state = _LzoState.nextMatch;
    } else {
      state = _LzoState.literal;
    }
  }
}

enum _LzoState { literal, firstMatch, nextMatch }
