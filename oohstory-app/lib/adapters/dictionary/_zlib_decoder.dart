import '_zlib_decoder_stub.dart'
    if (dart.library.io) '_zlib_decoder_io.dart'
    as platform;

List<int> decodeZlib(List<int> bytes, {required int maxOutputBytes}) =>
    platform.decodeZlib(bytes, maxOutputBytes: maxOutputBytes);
