import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/core/core.dart';

void main() {
  final timestamp = DateTime.utc(2026, 8, 23, 1, 2, 3);
  Map<String, Object?> validPayload() => ProgressRecord(
    bookId: 'book',
    documentVersion: 'v1',
    location: 'page:1',
    percentage: 0.5,
    deviceId: '123e4567-e89b-12d3-a456-426614174000',
    updatedAt: timestamp,
    revision: 0,
  ).toJson();

  test('progress serialization matches frozen contract deterministically', () {
    final record = ProgressRecord(
      bookId: 'sha256:abc',
      documentVersion: 'v1',
      location: 'chapter:4',
      percentage: 0.25,
      deviceId: '123e4567-e89b-12d3-a456-426614174000',
      updatedAt: timestamp,
      revision: 7,
    );

    expect(
      jsonEncode(record.toJson()),
      '{"book_id":"sha256:abc","document_version":"v1",'
      '"location":"chapter:4","percentage":0.25,'
      '"device_id":"123e4567-e89b-12d3-a456-426614174000",'
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
        deviceId: '123e4567-e89b-12d3-a456-426614174000',
        updatedAt: timestamp,
        revision: 0,
      ),
      throwsA(
        isA<CoreException>().having(
          (error) => error.code,
          'code',
          CoreErrorCode.validationError,
        ),
      ),
    );
  });

  test('progress rejects blank identifiers and invalid device UUIDs', () {
    for (final values in <({String bookId, String deviceId})>[
      (bookId: '   ', deviceId: '123e4567-e89b-12d3-a456-426614174000'),
      (bookId: 'book', deviceId: 'not-a-uuid'),
    ]) {
      expect(
        () => ProgressRecord(
          bookId: values.bookId,
          documentVersion: 'v1',
          location: 'page:1',
          percentage: 0.5,
          deviceId: values.deviceId,
          updatedAt: timestamp,
          revision: 0,
        ),
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

  test('progress JSON rejects malformed types and non-UTC timestamps', () {
    final valid = validPayload();

    for (final payload in <Map<String, Object?>>[
      <String, Object?>{...valid, 'revision': '0'},
      <String, Object?>{...valid, 'updated_at': '2026-08-23T09:02:03+08:00'},
      <String, Object?>{...valid, 'updated_at': 'not-a-date'},
    ]) {
      expect(
        () => ProgressRecord.fromJson(payload),
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

  test('progress JSON accepts explicit zero offset', () {
    final record = ProgressRecord.fromJson(<String, Object?>{
      ...validPayload(),
      'updated_at': '2026-08-23T01:02:03+00:00',
    });

    expect(record.updatedAt, timestamp);
    expect(record.updatedAt.isUtc, isTrue);
  });

  test('progress JSON rejects out-of-range calendar dates', () {
    expect(
      () => ProgressRecord.fromJson(<String, Object?>{
        ...validPayload(),
        'updated_at': '2026-02-30T01:02:03Z',
      }),
      throwsA(
        isA<CoreException>().having(
          (error) => error.code,
          'code',
          CoreErrorCode.validationError,
        ),
      ),
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
