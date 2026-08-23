import '../../core/errors.dart';

List<int> decodeZlib(List<int> bytes, {required int maxOutputBytes}) {
  throw const CoreException(
    CoreErrorCode.unsupported,
    'Compressed MDX blocks are unavailable on this platform',
  );
}
