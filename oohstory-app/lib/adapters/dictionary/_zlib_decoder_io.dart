import 'dart:io';

import '../../core/errors.dart';

List<int> decodeZlib(List<int> bytes, {required int maxOutputBytes}) {
  final sink = _BoundedByteSink(maxOutputBytes);
  try {
    final decoder = ZLibCodec().decoder.startChunkedConversion(sink);
    decoder.add(bytes);
    decoder.close();
    return sink.bytes;
  } on CoreException {
    rethrow;
  } on Object {
    throw const FormatException('MDX zlib block is malformed');
  }
}

class _BoundedByteSink implements Sink<List<int>> {
  _BoundedByteSink(this.maxBytes);

  final int maxBytes;
  final List<int> _bytes = <int>[];
  bool _closed = false;

  List<int> get bytes => List<int>.unmodifiable(_bytes);

  @override
  void add(List<int> data) {
    if (_closed) throw StateError('MDX decoder sink is closed');
    if (data.length > maxBytes - _bytes.length) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'MDX expanded data exceeds the configured size limit',
      );
    }
    _bytes.addAll(data);
  }

  @override
  void close() {
    _closed = true;
  }
}
