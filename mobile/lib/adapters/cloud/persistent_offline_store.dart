import 'offline_sync.dart';
import 'persistent_offline_store_stub.dart'
    if (dart.library.io) 'persistent_offline_store_io.dart'
    as platform;
import 'secure_credentials.dart';

/// Creates the platform's durable, encrypted cloud-mutation store.
///
/// [rootPath] is intended for isolated tests. Production callers leave it
/// unset so native platforms use their application-support directory.
Future<OfflineMutationStore> createPersistentOfflineMutationStore({
  required SecureCredentialStore credentialStore,
  String? rootPath,
}) => platform.createPersistentOfflineMutationStore(
  credentialStore: credentialStore,
  rootPath: rootPath,
);
