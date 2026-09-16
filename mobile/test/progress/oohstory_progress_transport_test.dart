import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:oohstory/adapters/progress/progress.dart';
import 'package:oohstory/core/core.dart';

void main() {
  const token = 'Bearer test-session';

  ProgressRecord record({int revision = 0, double percentage = .25}) =>
      ProgressRecord(
        bookId: 'oohstory:42',
        documentVersion: 'catalog-v1',
        location: '{"chapter_id":"3","within":0.25}',
        percentage: percentage,
        deviceId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        updatedAt: DateTime.utc(2026, 9, 15),
        revision: revision,
      );

  http.Response jsonResponse(Object value, {int status = 200}) => http.Response(
    jsonEncode(value),
    status,
    headers: const {'content-type': 'application/json; charset=utf-8'},
  );

  test('checks the frozen server capability contract', () async {
    final transport = OohStoryProgressTransport(
      baseUri: Uri.parse('https://sync.example.test'),
      authHeaders: () => const {'Authorization': token},
      client: MockClient((request) async {
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/sync/capabilities');
        expect(request.headers['Authorization'], token);
        return jsonResponse({
          'contract_id': OohStoryProgressTransport.contractId,
          'contract_version': OohStoryProgressTransport.contractVersion,
          'future_field': true,
        });
      }),
    );

    final result = await transport.fetchCapabilities();
    expect(result['future_field'], isTrue);
  });

  test('pulls a page and ignores additive record fields', () async {
    final transport = OohStoryProgressTransport(
      baseUri: Uri.parse('https://sync.example.test'),
      authHeaders: () async => const {'Authorization': token},
      client: MockClient((request) async {
        expect(request.url.path, '/api/v1/sync/progress');
        expect(request.url.queryParameters, {'cursor': 'next', 'limit': '25'});
        return jsonResponse({
          'items': [record().toJson()..['future_field'] = 'ignored'],
          'next_cursor': null,
          'server_time': '2026-09-15T00:00:01Z',
        });
      }),
    );

    final page = await transport.pull(cursor: 'next', limit: 25);
    expect(page.items.single.bookId, 'oohstory:42');
    expect(page.serverTime, DateTime.utc(2026, 9, 15, 0, 0, 1));
  });

  test('sends quoted If-Match and exact PUT payload', () async {
    final updated = record(revision: 4, percentage: .75);
    final transport = OohStoryProgressTransport(
      baseUri: Uri.parse('https://sync.example.test'),
      authHeaders: () => const {'Authorization': token},
      client: MockClient((request) async {
        expect(request.method, 'PUT');
        expect(request.url.path, '/api/v1/sync/progress/oohstory:42');
        expect(request.headers['If-Match'], '"3"');
        expect(jsonDecode(request.body), updated.toJson());
        return jsonResponse(updated.toJson());
      }),
    );

    expect((await transport.put(updated, ifMatch: 3)).revision, 4);
  });

  test('preserves the current remote record on revision conflict', () async {
    final current = record(revision: 7, percentage: .9);
    final transport = OohStoryProgressTransport(
      baseUri: Uri.parse('https://sync.example.test'),
      authHeaders: () => const {'Authorization': token},
      client: MockClient(
        (_) async => jsonResponse({
          'error': 'revision_conflict',
          'message': 'progress revision conflict',
          'correlation_id': 'request-42',
          'current': current.toJson(),
        }, status: 409),
      ),
    );

    await expectLater(
      transport.put(record(revision: 1), ifMatch: 0),
      throwsA(
        isA<ProgressTransportException>()
            .having(
              (error) => error.code,
              'code',
              CoreErrorCode.revisionConflict,
            )
            .having((error) => error.current?.revision, 'current revision', 7)
            .having(
              (error) => error.correlationId,
              'correlation',
              'request-42',
            ),
      ),
    );
  });

  test('requires HTTPS outside loopback and bounds response bodies', () async {
    expect(
      () => OohStoryProgressTransport(
        baseUri: Uri.parse('http://sync.example.test'),
        authHeaders: () => const {},
      ),
      throwsA(isA<CoreException>()),
    );

    final transport = OohStoryProgressTransport(
      baseUri: Uri.parse('http://127.0.0.1:8011'),
      authHeaders: () => const {'Authorization': token},
      maxResponseBytes: 8,
      client: MockClient((_) async => jsonResponse({'status': 'too-large'})),
    );
    await expectLater(
      transport.fetchCapabilities(),
      throwsA(
        isA<ProgressTransportException>().having(
          (error) => error.code,
          'code',
          CoreErrorCode.payloadTooLarge,
        ),
      ),
    );
  });

  test('sends DELETE with the mandatory revision precondition', () async {
    final tombstone = ProgressRecord(
      bookId: 'oohstory:42',
      documentVersion: 'catalog-v1',
      location: 'chapter:3',
      percentage: .25,
      deviceId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      updatedAt: DateTime.utc(2026, 9, 15),
      revision: 2,
      tombstone: true,
    );
    final transport = OohStoryProgressTransport(
      baseUri: Uri.parse('https://sync.example.test'),
      authHeaders: () => const {'Authorization': token},
      client: MockClient((request) async {
        expect(request.method, 'DELETE');
        expect(request.headers['If-Match'], '"1"');
        return jsonResponse(tombstone.toJson());
      }),
    );

    expect(
      (await transport.delete('oohstory:42', ifMatch: 1)).tombstone,
      isTrue,
    );
  });
}
