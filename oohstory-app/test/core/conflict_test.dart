import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/core/core.dart';

void main() {
  const resolver = ProgressConflictResolver();

  ProgressRecord record({
    int revision = 1,
    double percentage = 0.2,
    String version = 'v1',
    bool tombstone = false,
    int minute = 0,
  }) => ProgressRecord(
    bookId: 'book',
    documentVersion: version,
    location: 'chapter:1',
    percentage: percentage,
    deviceId: '123e4567-e89b-12d3-a456-426614174000',
    updatedAt: DateTime.utc(2026, 8, 23, 0, minute),
    revision: revision,
    tombstone: tombstone,
  );

  test('higher monotonic revision wins', () {
    final result = resolver.merge(record(revision: 2), record(revision: 3));
    expect(result.record.revision, 3);
    expect(result.decision, MergeDecision.keepRemote);
  });

  test('older offline progress cannot resurrect newer tombstone', () {
    final result = resolver.merge(
      record(revision: 4, percentage: 0.9),
      record(revision: 5, tombstone: true),
    );
    expect(result.record.tombstone, isTrue);
  });

  test('equal revision with document mismatch is an explicit conflict', () {
    final result = resolver.merge(
      record(version: 'old'),
      record(version: 'new'),
    );
    expect(result.decision, MergeDecision.documentVersionConflict);
    expect(result.record.documentVersion, 'new');
  });

  test('equal revision and document version chooses greater percentage', () {
    final result = resolver.merge(
      record(percentage: 0.6),
      record(percentage: 0.4),
    );
    expect(result.record.percentage, 0.6);
    expect(result.decision, MergeDecision.equalRevisionTie);
  });

  test('equal-revision tombstone wins over live record', () {
    final result = resolver.merge(record(), record(tombstone: true));
    expect(result.record.tombstone, isTrue);
  });

  test('exact equal-revision tie is independent of merge direction', () {
    final first = record();
    final second = ProgressRecord(
      bookId: 'book',
      documentVersion: 'v1',
      location: 'chapter:2',
      percentage: 0.2,
      deviceId: '123e4567-e89b-12d3-a456-426614174001',
      updatedAt: DateTime.utc(2026, 8, 23),
      revision: 1,
    );
    expect(
      resolver.merge(first, second).record.deviceId,
      resolver.merge(second, first).record.deviceId,
    );
  });

  test('offline replay never rolls the remote revision backward', () {
    final result = resolver.replayOffline(<ProgressRecord>[
      record(revision: 2, percentage: 0.8),
      record(revision: 4, percentage: 0.9),
    ], record(revision: 3, percentage: 0.3));
    expect(result.revision, 4);
    expect(result.percentage, 0.9);
  });
}
