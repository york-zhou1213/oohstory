import 'dart:async';
import 'dart:typed_data';

Future<Uint8List> collectBoundedBytes(
  Stream<List<int>> stream, {
  required int maxBytes,
  required Object Function() tooLarge,
}) async {
  final bytes = BytesBuilder(copy: false);
  final iterator = StreamIterator<List<int>>(stream);
  try {
    while (await iterator.moveNext()) {
      final chunk = iterator.current;
      if (chunk.length > maxBytes - bytes.length) throw tooLarge();
      bytes.add(chunk);
    }
    return bytes.takeBytes();
  } catch (error, stackTrace) {
    try {
      await iterator.cancel();
    } on Object {
      // Preserve the response/limit error that caused cancellation.
    }
    Error.throwWithStackTrace(error, stackTrace);
  }
}

Future<void> cancelByteStream(Stream<List<int>> stream) async {
  final subscription = stream.listen((_) {});
  await subscription.cancel();
}
