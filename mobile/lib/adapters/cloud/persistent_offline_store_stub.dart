import '../../core/errors.dart';
import 'offline_sync.dart';
import 'secure_credentials.dart';

Future<OfflineMutationStore> createPersistentOfflineMutationStore({
  required SecureCredentialStore credentialStore,
  String? rootPath,
}) async {
  throw const CoreException(
    CoreErrorCode.unsupported,
    'Persistent offline cloud mutations are unavailable on this platform',
  );
}
