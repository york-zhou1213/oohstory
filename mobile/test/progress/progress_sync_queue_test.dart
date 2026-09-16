import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/contracts/reference_adapters.dart';
import 'package:oohstory/core/core.dart';
import 'package:oohstory/services/progress_sync_queue.dart';
import 'package:oohstory/services/progress_sync_runtime.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  const deviceA = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const deviceB = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';

  ProgressRecord record({
    double percentage = .25,
    int revision = 0,
    String deviceId = deviceA,
    DateTime? updatedAt,
  }) => ProgressRecord(
    bookId: 'oohstory:42',
    documentVersion: 'catalog-v1',
    location: 'chapter:3',
    percentage: percentage,
    deviceId: deviceId,
    updatedAt: updatedAt ?? DateTime.utc(2026, 9, 15),
    revision: revision,
  );

  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('persists and replays an account-partitioned create', () async {
    final preferences = await SharedPreferences.getInstance();
    final transport = InMemoryProgressTransport();
    final first = ProgressSyncQueue(
      preferences: preferences,
      accountId: 'account-a',
      transport: transport,
    );
    await first.enqueue(record());

    final restored = ProgressSyncQueue(
      preferences: preferences,
      accountId: 'account-a',
      transport: transport,
    );
    expect(restored.pendingCount, 1);
    final result = await restored.synchronize();

    expect(result.uploaded, 1);
    expect(result.pending, 0);
    expect((await transport.pull()).items.single.revision, 0);
  });

  test(
    'keeps queues isolated by account without storing auth material',
    () async {
      final preferences = await SharedPreferences.getInstance();
      final transport = InMemoryProgressTransport();
      final accountA = ProgressSyncQueue(
        preferences: preferences,
        accountId: 'account-a',
        transport: transport,
      );
      final accountB = ProgressSyncQueue(
        preferences: preferences,
        accountId: 'account-b',
        transport: transport,
      );
      await accountA.enqueue(record());

      expect(accountA.pendingCount, 1);
      expect(accountB.pendingCount, 0);
      expect(
        preferences.getKeys().any((key) => key.contains('account-a')),
        isFalse,
      );
      expect(
        preferences.getKeys().any((key) => key.contains('token')),
        isFalse,
      );
    },
  );

  test(
    'preserves a concurrent remote update as an explicit conflict',
    () async {
      final preferences = await SharedPreferences.getInstance();
      final transport = InMemoryProgressTransport();
      final queue = ProgressSyncQueue(
        preferences: preferences,
        accountId: 'account-a',
        transport: transport,
      );
      await queue.enqueue(record());
      await transport.put(record(percentage: .8, deviceId: deviceB));

      final result = await queue.synchronize();

      expect(result.uploaded, 0);
      expect(result.pending, 1);
      expect(result.conflicts, hasLength(1));
      expect(result.conflicts.single.remote?.percentage, .8);
    },
  );

  test('can explicitly keep remote or retry local after conflict', () async {
    final preferences = await SharedPreferences.getInstance();
    final transport = InMemoryProgressTransport();
    final queue = ProgressSyncQueue(
      preferences: preferences,
      accountId: 'account-a',
      transport: transport,
    );
    await transport.put(record(percentage: .6, deviceId: deviceB));
    await queue.enqueue(record(percentage: .9));
    var conflict = (await queue.synchronize()).conflicts.single;

    await queue.retryLocal('oohstory:42', conflict.remote!);
    var result = await queue.synchronize();
    expect(result.uploaded, 1);
    expect(result.pending, 0);
    expect((await transport.pull()).items.last.percentage, .9);

    await queue.enqueue(
      record(
        percentage: .2,
        revision: 2,
        updatedAt: DateTime.utc(2026, 9, 15, 1),
      ),
    );
    await transport.put(
      record(
        percentage: 1,
        revision: 2,
        deviceId: deviceB,
        updatedAt: DateTime.utc(2026, 9, 15, 2),
      ),
      ifMatch: 1,
    );
    conflict = (await queue.synchronize()).conflicts.single;
    await queue.acceptRemote('oohstory:42', conflict.remote!);
    expect(queue.pendingCount, 0);
  });

  test('canonicalizes server book identities without leaking raw long IDs', () {
    expect(ProgressSyncRuntime.canonicalBookId('42'), 'oohstory:42');
    final hashed = ProgressSyncRuntime.canonicalBookId('书名/本地路径');
    expect(hashed, matches(RegExp(r'^sha256:[0-9a-f]{64}$')));
    expect(hashed, isNot(contains('书名')));
  });
}
