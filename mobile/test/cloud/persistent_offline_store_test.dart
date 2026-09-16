import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/cloud/cloud.dart';
import 'package:oohstory/adapters/contracts/adapter_contracts.dart';
import 'package:oohstory/core/core.dart';

import 'cloud_test_support.dart';

void main() {
  late Directory temporary;

  setUp(() async {
    temporary = await Directory.systemTemp.createTemp('oohstory-cloud-queue-');
  });

  tearDown(() async {
    if (await temporary.exists()) await temporary.delete(recursive: true);
  });

  test(
    'encrypted queue survives store recreation and replays in order',
    () async {
      final credentials = MemoryCredentialStore();
      final firstStore = await createPersistentOfflineMutationStore(
        credentialStore: credentials,
        rootPath: temporary.path,
      );
      final firstAdapter = _RecordingAdapter();
      final first = OfflineCloudSynchronizer(
        adapter: firstAdapter,
        accountId: 'account-a',
        store: firstStore,
        clock: () => DateTime.utc(2026, 9, 17, 1),
      );
      await first.enqueueWrite(
        'books/private-title.epub',
        Stream<List<int>>.value(utf8.encode('secret-payload')),
        etag: 'v1',
      );
      await first.enqueueDelete('books/old.epub', etag: 'v2');

      final files = await temporary
          .list(recursive: true, followLinks: false)
          .where((entity) => entity is File && entity.path.endsWith('.queue'))
          .cast<File>()
          .toList();
      expect(files, hasLength(2));
      for (final file in files) {
        final stored = await file.readAsBytes();
        final binaryText = latin1.decode(stored);
        expect(binaryText, isNot(contains('secret-payload')));
        expect(binaryText, isNot(contains('private-title')));
      }

      final restoredStore = await createPersistentOfflineMutationStore(
        credentialStore: credentials,
        rootPath: temporary.path,
      );
      final restoredAdapter = _RecordingAdapter();
      final restored = OfflineCloudSynchronizer(
        adapter: restoredAdapter,
        accountId: 'account-a',
        store: restoredStore,
      );
      final pending = await restoredStore.pending(restored.scope);
      expect(pending.map((item) => item.type), <CloudMutationType>[
        CloudMutationType.write,
        CloudMutationType.delete,
      ]);

      expect(await restored.replay(), 2);
      expect(restoredAdapter.writes, <String>[
        'books/private-title.epub:v1:14',
      ]);
      expect(restoredAdapter.deletes, <String>['books/old.epub:v2']);
      expect(await restoredStore.pending(restored.scope), isEmpty);
    },
  );

  test('authenticated queue rejects ciphertext tampering', () async {
    final store = await createPersistentOfflineMutationStore(
      credentialStore: MemoryCredentialStore(),
      rootPath: temporary.path,
    );
    final sync = OfflineCloudSynchronizer(
      adapter: _RecordingAdapter(),
      accountId: 'account-a',
      store: store,
    );
    await sync.enqueueDelete('books/old.epub', etag: 'v1');
    final file = await temporary
        .list(recursive: true, followLinks: false)
        .where((entity) => entity is File && entity.path.endsWith('.queue'))
        .cast<File>()
        .single;
    final bytes = await file.readAsBytes();
    bytes[bytes.length - 1] ^= 0x01;
    await file.writeAsBytes(bytes, flush: true);

    await expectLater(
      store.pending(sync.scope),
      throwsA(
        isA<CoreException>().having(
          (error) => error.code,
          'code',
          CoreErrorCode.validationError,
        ),
      ),
    );
  });

  test('durable partitions isolate accounts', () async {
    final store = await createPersistentOfflineMutationStore(
      credentialStore: MemoryCredentialStore(),
      rootPath: temporary.path,
    );
    final adapter = _RecordingAdapter();
    final accountA = OfflineCloudSynchronizer(
      adapter: adapter,
      accountId: 'account-a',
      store: store,
    );
    final accountB = OfflineCloudSynchronizer(
      adapter: adapter,
      accountId: 'account-b',
      store: store,
    );
    await accountA.enqueueDelete('books/a.epub', etag: 'v1');
    await accountB.enqueueDelete('books/b.epub', etag: 'v1');

    expect(await store.pending(accountA.scope), hasLength(1));
    expect(await store.pending(accountB.scope), hasLength(1));
    expect(await accountB.replay(), 1);
    expect(adapter.deletes, <String>['books/b.epub:v1']);
    expect(await store.pending(accountA.scope), hasLength(1));
  });
}

final class _RecordingAdapter implements CloudLibraryAdapter {
  final List<String> writes = <String>[];
  final List<String> deletes = <String>[];

  @override
  String get providerId => 'fixture';

  @override
  ProviderCapabilities get capabilities => ProviderCapabilities(
    providerId: providerId,
    supported: const <AdapterCapability>[AdapterCapability.cloudLibrary],
  );

  @override
  Future<void> delete(String path, {String? etag}) async {
    deletes.add('$path:$etag');
  }

  @override
  Future<SyncPage<CloudEntry>> list(String path, {String? cursor}) async =>
      SyncPage<CloudEntry>(
        items: const <CloudEntry>[],
        nextCursor: null,
        serverTime: DateTime.utc(2026, 9, 17),
      );

  @override
  Stream<List<int>> read(String path) => const Stream<List<int>>.empty();

  @override
  Future<CloudEntry> stat(String path) async =>
      CloudEntry(path: path, isDirectory: false, etag: 'v2');

  @override
  Future<CloudEntry> write(
    String path,
    Stream<List<int>> bytes, {
    String? etag,
  }) async {
    final payload = await bytes.expand((chunk) => chunk).toList();
    writes.add('$path:$etag:${payload.length}');
    return CloudEntry(path: path, isDirectory: false, etag: 'v2');
  }
}
