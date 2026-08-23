import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/contracts/reference_adapters.dart';
import 'package:oohstory/core/core.dart';

void main() {
  ProgressRecord record({int revision = 0, bool tombstone = false}) =>
      ProgressRecord(
        bookId: 'book',
        documentVersion: 'v1',
        location: 'page:1',
        percentage: 0.5,
        deviceId: '123e4567-e89b-12d3-a456-426614174000',
        updatedAt: DateTime.utc(2026, 8, 23),
        revision: revision,
        tombstone: tombstone,
      );

  test('reference store preserves pending progress and tombstones', () async {
    final store = InMemoryProgressStore();
    await store.put(record());
    await store.delete('book', record(revision: 1, tombstone: true));
    expect((await store.get('book'))!.tombstone, isTrue);
    expect(await store.pending().toList(), hasLength(2));
  });

  test(
    'reference transport pages deterministically and creates tombstone',
    () async {
      final transport = InMemoryProgressTransport(
        clock: () => DateTime.utc(2026, 8, 23),
      );
      await transport.put(record());
      final page = await transport.pull(limit: 1);
      expect(page.items.single.bookId, 'book');
      final deleted = await transport.delete('book', ifMatch: 0);
      expect(deleted.tombstone, isTrue);
      expect(deleted.revision, 1);
    },
  );

  test(
    'reference transport reports stale If-Match as revision conflict',
    () async {
      final transport = InMemoryProgressTransport();
      await transport.put(record());
      await expectLater(
        transport.put(record(revision: 1), ifMatch: 2),
        throwsA(
          isA<CoreException>().having(
            (error) => error.code,
            'code',
            CoreErrorCode.revisionConflict,
          ),
        ),
      );
    },
  );

  test('reference transport retries identical put idempotently', () async {
    final transport = InMemoryProgressTransport();
    final progress = record();

    final first = await transport.put(progress);
    final retry = await transport.put(progress);

    expect(retry, same(first));
  });

  test('reference transport validates exact update retry If-Match', () async {
    final transport = InMemoryProgressTransport();
    await transport.put(record());
    final updated = await transport.put(record(revision: 1), ifMatch: 0);

    expect(await transport.put(record(revision: 1), ifMatch: 0), same(updated));
    for (final request in <Future<ProgressRecord> Function()>[
      () => transport.put(record(revision: 1)),
      () => transport.put(record(revision: 1), ifMatch: 999),
    ]) {
      await expectLater(
        request(),
        throwsA(
          isA<CoreException>().having(
            (error) => error.code,
            'code',
            CoreErrorCode.revisionConflict,
          ),
        ),
      );
    }
  });

  test('reference transport retries delete idempotently', () async {
    final transport = InMemoryProgressTransport();
    await transport.put(record());

    final first = await transport.delete('book', ifMatch: 0);
    final retry = await transport.delete('book', ifMatch: 0);

    expect(retry, same(first));
    expect(retry.tombstone, isTrue);
    expect(retry.revision, 1);
  });

  test('reference transport enforces create revision preconditions', () async {
    for (final request in <Future<ProgressRecord> Function()>[
      () => InMemoryProgressTransport().put(record(revision: 1)),
      () => InMemoryProgressTransport().put(record(), ifMatch: 0),
    ]) {
      await expectLater(
        request(),
        throwsA(
          isA<CoreException>().having(
            (error) => error.code,
            'code',
            CoreErrorCode.revisionConflict,
          ),
        ),
      );
    }
  });

  test('reference transport enforces update revision preconditions', () async {
    final transport = InMemoryProgressTransport();
    await transport.put(record());

    for (final request in <Future<ProgressRecord> Function()>[
      () => transport.put(record(revision: 1)),
      () => transport.put(record(revision: 1), ifMatch: 2),
      () => transport.put(record(revision: 2), ifMatch: 0),
    ]) {
      await expectLater(
        request(),
        throwsA(
          isA<CoreException>().having(
            (error) => error.code,
            'code',
            CoreErrorCode.revisionConflict,
          ),
        ),
      );
    }
  });

  test('reference transport normalizes malformed pagination errors', () async {
    final transport = InMemoryProgressTransport();
    await transport.put(record());

    for (final request in <Future<SyncPage<ProgressRecord>> Function()>[
      () => transport.pull(cursor: 'not-a-number'),
      () => transport.pull(limit: -1),
      () => transport.pull(cursor: '2', limit: 1),
    ]) {
      await expectLater(
        request(),
        throwsA(
          isA<CoreException>().having(
            (error) => error.code,
            'code',
            CoreErrorCode.validationError,
          ),
        ),
      );
    }
  });
}
