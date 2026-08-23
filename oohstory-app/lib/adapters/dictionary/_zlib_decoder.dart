import '_zlib_decoder_portable.dart' as platform;

List<int> decodeZlib(List<int> bytes, {required int maxOutputBytes}) =>
    platform.decodeZlib(bytes, maxOutputBytes: maxOutputBytes);
