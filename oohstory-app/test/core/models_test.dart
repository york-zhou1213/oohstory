import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/core/core.dart';

void main() {
  final timestamp = DateTime.utc(2026, 8, 23, 1, 2, 3);

  test('progress serialization matches frozen contract deterministically', () {
    final record = ProgressRecord(
      bookId: 'sha256:abc',
      documentVersion: 'v1',
      location: 'chapter:4',
      percentage: 0.25,
      deviceId: 'device-1',
      updatedAt: timestamp,
      revision: 7,
    );

    expect(
      jsonEncode(record.toJson()),
      '{"book_id":"sha256:abc","document_version":"v1",'
      '"location":"chapter:4","percentage":0.25,"device_id":"device-1",'
      '"updated_at":"2026-08-23T01:02:03.000Z","revision":7,'
      '"tombstone":false}',
    );
    expect(ProgressRecord.fromJson(record.toJson()).toJson(), record.toJson());
  });

  test('progress validates contract ranges and UTC clock', () {
    expect(
      () => ProgressRecord(
        bookId: 'book',
        documentVersion: 'v1',
        location: 'page:1',
        percentage: 1.1,
        deviceId: 'device',
        updatedAt: timestamp,
        revision: 0,
      ),
      throwsRangeError,
    );
  });

  test('error envelope uses contract wire code and stable key order', () {
    const error = CoreException(
      CoreErrorCode.revisionConflict,
      'stale revision',
      correlationId: 'correlation-1',
    );
    expect(
      jsonEncode(error.toJson()),
      '{"error":"revision_conflict","message":"stale revision",'
      '"correlation_id":"correlation-1"}',
    );
  });
}
