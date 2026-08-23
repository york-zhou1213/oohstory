import '_zlib_decoder_portable.dart' as platform;

List<int> decodeZlib(List<int> bytes, {required int maxOutputBytes}) =>
    platform.decodeZlib(bytes, maxOutputBytes: maxOutputBytes);

Future<List<int>> decodeZlibAsync(
  List<int> bytes, {
  required int maxOutputBytes,
  required void Function() checkCancelled,
}) => platform.decodeZlibAsync(
  bytes,
  maxOutputBytes: maxOutputBytes,
  checkCancelled: checkCancelled,
);
