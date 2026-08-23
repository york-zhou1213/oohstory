import 'dart:typed_data';
import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/cloud/cloud.dart';
import 'package:oohstory/adapters/contracts/adapter_contracts.dart';
import 'package:oohstory/core/core.dart';

import 'cloud_test_support.dart';

void main() {
  test('cloud roots reject traversal and alternate separators', () {
    final root = CloudRoot('OOHStory');
    expect(root.slashPath('books/a.epub'), '/OOHStory/books/a.epub');
    for (final path in <String>[
      '../secret',
      'books/../../secret',
      r'..\secret',
    ]) {
      expect(
        () => root.resolve(path),
        throwsA(
          isA<CoreException>().having(
            (error) => error.code,
            'code',
            anyOf(CoreErrorCode.forbidden, CoreErrorCode.validationError),
          ),
        ),
      );
    }
    expect(
      () => root.relativeFromSegments(<String>['Other', 'secret']),
      throwsA(isA<CoreException>()),
    );
    expect(() => root.requireDescendant(''), throwsA(isA<CoreException>()));
  });

  test('runtime policy requires HTTPS, explicit Web CORS, and no userinfo', () {
    expect(
      () => const CloudRuntimePolicy(
        isWeb: false,
      ).validate(Uri.parse('http://cloud.example.test')),
      throwsA(isA<CoreException>()),
    );
    expect(
      () => const CloudRuntimePolicy(
        isWeb: true,
      ).validate(Uri.parse('https://cloud.example.test')),
      throwsA(
        isA<CoreException>().having(
          (error) => error.message,
          'message',
          contains('CORS'),
        ),
      ),
    );
    expect(
      () => const CloudRuntimePolicy(
        isWeb: true,
        corsVerified: true,
      ).validate(Uri.parse('https://user:password@cloud.example.test')),
      throwsA(isA<CoreException>()),
    );
  });

  test('safe logger removes credential fields and URI query data', () {
    late Map<String, Object?> logged;
    final logger = SafeCloudLogger((_, fields) => logged = fields);
    logger.event('request', <String, Object?>{
      'authorization': 'Bearer should-not-leak',
      'uri': Uri.parse('https://user:pass@example.test/file?token=secret'),
      'provider': 'fixture',
    });
    expect(logged['authorization'], '[REDACTED]');
    expect(logged['uri'], 'https://example.test/file');
    expect(logged.toString(), isNot(contains('should-not-leak')));
    expect(logged.toString(), isNot(contains('secret')));
  });

  test(
    'invalid provider JSON is normalized without payload disclosure',
    () async {
      Object? captured;
      try {
        await decodeJsonObject(
          CloudHttpResponse.bytes(
            statusCode: 200,
            body: utf8.encode('{"access_token":"must-not-leak"'),
          ),
        );
      } on Object catch (error) {
        captured = error;
      }
      expect(captured, isA<CoreException>());
      expect(captured.toString(), isNot(contains('must-not-leak')));
    },
  );

  test(
    'OAuth refresh is single-flight and persists rotated token first',
    () async {
      final scope = CredentialScope('oauth-single-flight');
      final credentials = MemoryCredentialStore(<String, String>{
        scope.key('refresh_token'): 'old-refresh',
        scope.key('client_id'): 'client',
      });
      var requests = 0;
      final transport = FixtureTransport((_, _) async {
        requests++;
        await Future<void>.delayed(Duration.zero);
        return jsonResponse(
          200,
          '{"access_token":"access","refresh_token":"rotated"}',
        );
      });
      final tokens = OAuthAccessTokenProvider(
        store: credentials,
        scope: scope,
        tokenEndpoint: Uri.parse('https://oauth.fixture/token'),
        transport: transport,
        runtimePolicy: const CloudRuntimePolicy(isWeb: false),
      );
      expect(
        await Future.wait(<Future<bool>>[tokens.refresh(), tokens.refresh()]),
        <bool>[true, true],
      );
      expect(requests, 1);
      expect(credentials.writes, <String>[
        scope.key('refresh_token'),
        scope.key('access_token'),
      ]);
      expect(credentials.values[scope.key('refresh_token')], 'rotated');
      expect(credentials.values[scope.key('access_token')], 'access');
    },
  );

  test(
    'retry policy retries transient failures but honors cancellation',
    () async {
      var attempts = 0;
      final transport = FixtureTransport((_, _) async {
        attempts++;
        return CloudHttpResponse.bytes(statusCode: attempts < 3 ? 503 : 200);
      });
      final response = await const RetryPolicy(initialDelay: Duration.zero)
          .send(
            transport,
            CloudHttpRequest(
              method: 'GET',
              uri: Uri.parse('https://fixture.test'),
            ),
            sleep: (_) async {},
          );
      expect(response.statusCode, 200);
      expect(attempts, 3);

      final cancelDuringBackoff = CancellationToken();
      final enteredBackoff = Completer<void>();
      final backoff = const RetryPolicy().send(
        FixtureTransport((_, _) async {
          enteredBackoff.complete();
          return CloudHttpResponse.bytes(statusCode: 503);
        }),
        CloudHttpRequest(method: 'GET', uri: Uri.parse('https://fixture.test')),
        cancellationToken: cancelDuringBackoff,
        sleep: (_) => Completer<void>().future,
      );
      await enteredBackoff.future;
      cancelDuringBackoff.cancel();
      await expectLater(backoff, throwsA(isA<CloudOperationCancelled>()));

      final cancelled = CancellationToken()..cancel();
      await expectLater(
        const RetryPolicy().send(
          transport,
          CloudHttpRequest(
            method: 'GET',
            uri: Uri.parse('https://fixture.test'),
          ),
          cancellationToken: cancelled,
        ),
        throwsA(isA<CloudOperationCancelled>()),
      );
      expect(attempts, 3);
    },
  );

  test(
    'offline replay deduplicates exact operations and retains failures',
    () async {
      final adapter = _RecordingAdapter()..failWrites = true;
      final store = InMemoryOfflineMutationStore();
      final sync = OfflineCloudSynchronizer(
        adapter: adapter,
        accountId: 'account-a',
        store: store,
        clock: () => DateTime.utc(2026, 8, 23),
      );
      final first = await sync.enqueueWrite(
        'books/a.epub',
        Stream<List<int>>.value(<int>[1, 2, 3]),
        etag: 'v1',
      );
      final duplicate = await sync.enqueueWrite(
        'books/a.epub',
        Stream<List<int>>.value(<int>[1, 2, 3]),
        etag: 'v1',
      );
      expect(duplicate, first);
      await sync.enqueueDelete('books/a.epub', etag: 'v2');
      expect(
        (await store.pending(sync.scope)).map((mutation) => mutation.type),
        <CloudMutationType>[CloudMutationType.write, CloudMutationType.delete],
      );

      await expectLater(sync.replay(), throwsA(isA<CoreException>()));
      expect(await store.pending(sync.scope), hasLength(2));
      adapter.failWrites = false;
      expect(await sync.replay(), 2);
      expect(await store.pending(sync.scope), isEmpty);
      expect(adapter.writes.single, 'books/a.epub:v1:3');
      expect(adapter.deletes.single, 'books/a.epub:v2');
    },
  );

  test('queued mutations round-trip without exposing credentials', () {
    final scope = CloudMutationScope(
      providerId: 'fixture',
      accountId: 'account-a',
    );
    final mutation = QueuedCloudMutation(
      scope: scope,
      idempotencyKey: 'idempotent',
      type: CloudMutationType.write,
      path: 'book.epub',
      etag: 'etag',
      bytes: Uint8List.fromList(<int>[1, 2]),
      enqueuedAt: DateTime.utc(2026, 8, 23),
    );
    final restored = QueuedCloudMutation.fromJson(mutation.toJson());
    expect(restored.scope, scope);
    expect(restored.idempotencyKey, mutation.idempotencyKey);
    expect(restored.bytes, mutation.bytes);
  });

  test('offline replay rejects tampered persisted mutations', () async {
    final adapter = _RecordingAdapter();
    final store = InMemoryOfflineMutationStore();
    final sync = OfflineCloudSynchronizer(
      adapter: adapter,
      accountId: 'account-a',
      store: store,
    );
    await store.put(
      QueuedCloudMutation(
        scope: sync.scope,
        idempotencyKey: 'tampered-key',
        type: CloudMutationType.write,
        path: 'book.epub',
        etag: null,
        bytes: Uint8List.fromList(<int>[1]),
        enqueuedAt: DateTime.utc(2026, 8, 23),
      ),
    );
    await expectLater(
      sync.replay(),
      throwsA(
        isA<CoreException>().having(
          (error) => error.code,
          'code',
          CoreErrorCode.validationError,
        ),
      ),
    );
    expect(adapter.writes, isEmpty);
    expect(await store.pending(sync.scope), hasLength(1));
  });

  test('offline queues are isolated by provider and account', () async {
    final store = InMemoryOfflineMutationStore();
    final dropboxA = _RecordingAdapter('dropbox');
    final dropboxB = _RecordingAdapter('dropbox');
    final s3A = _RecordingAdapter('s3');
    final syncDropboxA = OfflineCloudSynchronizer(
      adapter: dropboxA,
      accountId: 'account-a',
      store: store,
    );
    final syncDropboxB = OfflineCloudSynchronizer(
      adapter: dropboxB,
      accountId: 'account-b',
      store: store,
    );
    final syncS3A = OfflineCloudSynchronizer(
      adapter: s3A,
      accountId: 'account-a',
      store: store,
    );

    final keys = <String>{
      await syncDropboxA.enqueueDelete('book.epub'),
      await syncDropboxB.enqueueDelete('book.epub'),
      await syncS3A.enqueueDelete('book.epub'),
    };
    expect(keys, hasLength(3));
    expect(await syncDropboxB.replay(), 1);
    expect(dropboxA.deletes, isEmpty);
    expect(dropboxB.deletes, <String>['book.epub:null']);
    expect(s3A.deletes, isEmpty);
    expect(await store.pending(syncDropboxA.scope), hasLength(1));
    expect(await store.pending(syncS3A.scope), hasLength(1));
  });

  test('offline replay rejects a store returning another account', () async {
    final adapter = _RecordingAdapter('dropbox');
    final foreignScope = CloudMutationScope(
      providerId: 'dropbox',
      accountId: 'account-b',
    );
    final mutation = QueuedCloudMutation(
      scope: foreignScope,
      idempotencyKey: 'foreign',
      type: CloudMutationType.delete,
      path: 'book.epub',
      etag: null,
      bytes: Uint8List(0),
      enqueuedAt: DateTime.utc(2026, 8, 23),
    );
    final sync = OfflineCloudSynchronizer(
      adapter: adapter,
      accountId: 'account-a',
      store: _MisdirectedStore(mutation),
    );
    await expectLater(
      sync.replay(),
      throwsA(
        isA<CoreException>().having(
          (error) => error.code,
          'code',
          CoreErrorCode.validationError,
        ),
      ),
    );
    expect(adapter.deletes, isEmpty);
  });
}

final class _RecordingAdapter implements CloudLibraryAdapter {
  _RecordingAdapter([this.providerId = 'recording']);

  bool failWrites = false;
  final List<String> writes = <String>[];
  final List<String> deletes = <String>[];

  @override
  final String providerId;

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
  Future<SyncPage<CloudEntry>> list(String path, {String? cursor}) =>
      throw UnimplementedError();

  @override
  Stream<List<int>> read(String path) => const Stream<List<int>>.empty();

  @override
  Future<CloudEntry> stat(String path) => throw UnimplementedError();

  @override
  Future<CloudEntry> write(
    String path,
    Stream<List<int>> bytes, {
    String? etag,
  }) async {
    final body = await collectBytes(bytes);
    if (failWrites) {
      throw const CoreException(CoreErrorCode.upstreamError, 'fixture offline');
    }
    writes.add('$path:$etag:${body.length}');
    return CloudEntry(path: path, isDirectory: false, etag: 'v2');
  }
}

final class _MisdirectedStore implements OfflineMutationStore {
  _MisdirectedStore(this.mutation);

  final QueuedCloudMutation mutation;

  @override
  Future<List<QueuedCloudMutation>> pending(CloudMutationScope scope) async =>
      <QueuedCloudMutation>[mutation];

  @override
  Future<void> put(QueuedCloudMutation mutation) async {}

  @override
  Future<void> remove(CloudMutationScope scope, String idempotencyKey) async {}
}
