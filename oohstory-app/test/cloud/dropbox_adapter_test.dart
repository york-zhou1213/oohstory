import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/cloud/cloud.dart';
import 'package:oohstory/core/core.dart';

import 'cloud_test_support.dart';

void main() {
  test('Dropbox fixture refreshes OAuth and covers paging and CRUD', () async {
    final scope = CredentialScope('dropbox-test');
    final credentials = MemoryCredentialStore(<String, String>{
      scope.key('access_token'): 'expired-access-token',
      scope.key('refresh_token'): 'refresh-token',
      scope.key('client_id'): 'client-id',
      scope.key('client_secret'): 'client-secret',
    });
    var rejectedExpiredToken = false;
    final transport = FixtureTransport((request, body) async {
      if (request.uri.path == '/oauth2/token') {
        final form = utf8.decode(body);
        expect(form, contains('grant_type=refresh_token'));
        expect(form, contains('refresh_token=refresh-token'));
        return jsonResponse(
          200,
          '{"access_token":"fresh-access-token","token_type":"bearer"}',
        );
      }
      if (!rejectedExpiredToken) {
        rejectedExpiredToken = true;
        expect(request.headers['authorization'], 'Bearer expired-access-token');
        return jsonResponse(401, '{"error":"expired_access_token"}');
      }
      expect(request.headers['authorization'], 'Bearer fresh-access-token');
      switch (request.uri.path) {
        case '/2/files/list_folder':
          return jsonResponse(
            200,
            '{"entries":[{".tag":"file","name":"a.epub",'
            '"path_display":"/OOHStory/a.epub","rev":"rev1"}],'
            '"cursor":"cursor-1","has_more":true}',
          );
        case '/2/files/list_folder/continue':
          expect(jsonDecode(utf8.decode(body)), <String, Object?>{
            'cursor': 'cursor-1',
          });
          return jsonResponse(
            200,
            '{"entries":[{".tag":"folder","name":"folder",'
            '"path_display":"/OOHStory/folder"}],'
            '"cursor":"cursor-2","has_more":false}',
          );
        case '/2/files/get_metadata':
          return jsonResponse(
            200,
            '{".tag":"file","name":"a.epub",'
            '"path_display":"/OOHStory/a.epub","rev":"rev2"}',
          );
        case '/2/files/download':
          expect(
            request.headers['dropbox-api-arg'],
            contains('/OOHStory/a.epub'),
          );
          return CloudHttpResponse.bytes(statusCode: 200, body: <int>[1, 2, 3]);
        case '/2/files/upload':
          final args = jsonDecode(request.headers['dropbox-api-arg']!) as Map;
          expect(args['mode'], <String, Object?>{
            '.tag': 'update',
            'update': 'rev1',
          });
          expect(body, <int>[4, 5]);
          return jsonResponse(
            200,
            '{".tag":"file","name":"a.epub",'
            '"path_display":"/OOHStory/a.epub","rev":"rev2"}',
          );
        case '/2/files/delete_v2':
          return jsonResponse(200, '{"metadata":{".tag":"file"}}');
      }
      fail('Unexpected Dropbox request: ${request.method} ${request.uri}');
    });
    final adapter = DropboxCloudAdapter(
      root: 'OOHStory',
      transport: transport,
      credentialStore: credentials,
      credentialScope: scope,
      apiEndpoint: Uri.parse('https://dropbox.fixture/2'),
      contentEndpoint: Uri.parse('https://dropbox-content.fixture/2'),
      tokenEndpoint: Uri.parse('https://dropbox.fixture/oauth2/token'),
      clock: () => DateTime.utc(2026, 8, 23),
    );

    final first = await adapter.list('');
    expect(first.items.single.path, 'a.epub');
    expect(first.nextCursor, 'cursor-1');
    final second = await adapter.list('', cursor: first.nextCursor);
    expect(second.items.single.isDirectory, isTrue);
    expect(second.nextCursor, isNull);
    expect((await adapter.stat('a.epub')).etag, 'rev2');
    expect(
      await adapter.read('a.epub').expand((chunk) => chunk).toList(),
      <int>[1, 2, 3],
    );
    expect(
      (await adapter.write(
        'a.epub',
        Stream<List<int>>.value(<int>[4, 5]),
        etag: 'rev1',
      )).etag,
      'rev2',
    );
    await adapter.delete('a.epub', etag: 'rev2');
    expect(credentials.values[scope.key('access_token')], 'fresh-access-token');
    expect(
      transport.requests
          .where((request) => request.uri.path != '/oauth2/token')
          .skip(1)
          .every(
            (request) =>
                request.headers['authorization'] == 'Bearer fresh-access-token',
          ),
      isTrue,
    );
  });

  test('Dropbox refuses stale ETag deletion and path escape', () async {
    final scope = CredentialScope('dropbox-negative');
    final credentials = MemoryCredentialStore(<String, String>{
      scope.key('access_token'): 'token',
    });
    final transport = FixtureTransport((request, _) async {
      if (request.uri.path.endsWith('/get_metadata')) {
        return jsonResponse(
          200,
          '{".tag":"file","name":"a.epub",'
          '"path_display":"/OOHStory/a.epub","rev":"current"}',
        );
      }
      fail('Delete must stop before Dropbox mutation');
    });
    final adapter = DropboxCloudAdapter(
      root: 'OOHStory',
      transport: transport,
      credentialStore: credentials,
      credentialScope: scope,
      apiEndpoint: Uri.parse('https://dropbox.fixture/2'),
      contentEndpoint: Uri.parse('https://dropbox-content.fixture/2'),
      tokenEndpoint: Uri.parse('https://dropbox.fixture/oauth2/token'),
    );
    await expectLater(
      adapter.delete('a.epub', etag: 'stale'),
      throwsA(
        isA<CoreException>().having(
          (error) => error.code,
          'code',
          CoreErrorCode.revisionConflict,
        ),
      ),
    );
    await expectLater(
      adapter.stat('../outside'),
      throwsA(isA<CoreException>()),
    );
    expect(transport.requests, hasLength(1));
  });

  test('Dropbox exact update and delete retries are idempotent', () async {
    final scope = CredentialScope('dropbox-idempotency');
    final credentials = MemoryCredentialStore(<String, String>{
      scope.key('access_token'): 'token',
    });
    var missing = false;
    final transport = FixtureTransport((request, _) async {
      switch (request.uri.path) {
        case '/2/files/upload':
          return jsonResponse(409, '{"error_summary":"conflict"}');
        case '/2/files/download':
          return CloudHttpResponse.bytes(statusCode: 200, body: <int>[5, 6]);
        case '/2/files/get_metadata':
          if (missing) {
            return jsonResponse(404, '{"error_summary":"not_found"}');
          }
          return jsonResponse(
            200,
            '{".tag":"file","name":"a.epub",'
            '"path_display":"/OOHStory/a.epub","rev":"current"}',
          );
      }
      fail('Unexpected Dropbox request');
    });
    final adapter = DropboxCloudAdapter(
      root: 'OOHStory',
      transport: transport,
      credentialStore: credentials,
      credentialScope: scope,
      apiEndpoint: Uri.parse('https://dropbox.fixture/2'),
      contentEndpoint: Uri.parse('https://dropbox-content.fixture/2'),
      tokenEndpoint: Uri.parse('https://dropbox.fixture/oauth2/token'),
    );
    expect(
      (await adapter.write(
        'a.epub',
        Stream<List<int>>.value(<int>[5, 6]),
        etag: 'old',
      )).etag,
      'current',
    );
    missing = true;
    await adapter.delete('a.epub', etag: 'current');
    expect(
      transport.requests.where(
        (request) => request.uri.path == '/2/files/delete_v2',
      ),
      isEmpty,
    );
  });
}
