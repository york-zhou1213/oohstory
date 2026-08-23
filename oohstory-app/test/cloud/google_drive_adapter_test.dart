import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/cloud/cloud.dart';

import 'cloud_test_support.dart';

void main() {
  test('Google Drive fixture covers paging and full CRUD', () async {
    final scope = CredentialScope('drive-test');
    final credentials = MemoryCredentialStore(<String, String>{
      scope.key('access_token'): 'drive-access-token',
    });
    final transport = FixtureTransport((request, body) async {
      expect(request.headers['authorization'], 'Bearer drive-access-token');
      if (request.uri.path == '/drive/v3/files' &&
          request.uri.queryParameters['q']?.contains('name =') == true) {
        return jsonResponse(
          200,
          '{"files":[{"id":"file-a","name":"a.epub",'
          '"mimeType":"application/epub+zip","version":"1",'
          '"md5Checksum":"old-md5"}]}',
        );
      }
      if (request.uri.path == '/drive/v3/files') {
        expect(
          request.uri.queryParameters['q'],
          contains("'root-folder' in parents"),
        );
        if (request.uri.queryParameters['pageToken'] == 'drive-next') {
          return jsonResponse(
            200,
            '{"files":[{"id":"folder-1","name":"folder",'
            '"mimeType":"application/vnd.google-apps.folder",'
            '"version":"1"}]}',
          );
        }
        return jsonResponse(
          200,
          '{"nextPageToken":"drive-next","files":['
          '{"id":"file-a","name":"a.epub",'
          '"mimeType":"application/epub+zip","version":"1"}]}',
        );
      }
      if (request.uri.path == '/drive/v3/files/file-a' &&
          request.uri.queryParameters['alt'] == 'media') {
        return CloudHttpResponse.bytes(statusCode: 200, body: <int>[1, 2, 3]);
      }
      if (request.uri.path == '/drive/v3/files/file-a' &&
          request.method == 'GET') {
        return jsonResponse(
          200,
          '{"id":"file-a","name":"a.epub",'
          '"mimeType":"application/epub+zip","version":"2",'
          '"md5Checksum":"new-md5"}',
          headers: const <String, String>{'etag': '"etag-2"'},
        );
      }
      if (request.uri.path == '/upload/drive/v3/files/file-a') {
        expect(request.method, 'PATCH');
        expect(request.headers['if-match'], '"etag-1"');
        expect(body, <int>[4, 5]);
        return jsonResponse(
          200,
          '{"id":"file-a","name":"a.epub",'
          '"mimeType":"application/epub+zip","version":"2"}',
        );
      }
      if (request.uri.path == '/drive/v3/files/file-a' &&
          request.method == 'DELETE') {
        expect(request.headers['if-match'], '"etag-2"');
        return CloudHttpResponse.bytes(statusCode: 204);
      }
      fail('Unexpected Drive request: ${request.method} ${request.uri}');
    });
    final adapter = GoogleDriveCloudAdapter(
      rootFolderId: 'root-folder',
      transport: transport,
      credentialStore: credentials,
      credentialScope: scope,
      apiEndpoint: Uri.parse('https://drive.fixture/drive/v3'),
      uploadEndpoint: Uri.parse('https://drive.fixture/upload/drive/v3'),
      tokenEndpoint: Uri.parse('https://drive.fixture/oauth2/token'),
      clock: () => DateTime.utc(2026, 8, 23),
    );

    final first = await adapter.list('');
    expect(first.items.single.path, 'a.epub');
    expect(first.nextCursor, 'drive-next');
    final second = await adapter.list('', cursor: first.nextCursor);
    expect(second.items.single.isDirectory, isTrue);
    expect((await adapter.stat('a.epub')).etag, '"etag-2"');
    expect(
      await adapter.read('a.epub').expand((chunk) => chunk).toList(),
      <int>[1, 2, 3],
    );
    expect(
      (await adapter.write(
        'a.epub',
        Stream<List<int>>.value(<int>[4, 5]),
        etag: '"etag-1"',
      )).etag,
      '"etag-2"',
    );
    await adapter.delete('a.epub', etag: '"etag-2"');
  });

  test(
    'Google Drive create is idempotent by generated ID and content hash',
    () async {
      final scope = CredentialScope('drive-idempotency');
      final credentials = MemoryCredentialStore(<String, String>{
        scope.key('access_token'): 'token',
      });
      var created = false;
      final checksum = md5.convert(<int>[9]).toString();
      final transport = FixtureTransport((request, body) async {
        if (request.uri.path == '/drive/v3/files/generateIds') {
          return jsonResponse(200, '{"ids":["generated-id"]}');
        }
        if (request.uri.path == '/upload/drive/v3/files') {
          expect(utf8.decode(body), contains('"id":"generated-id"'));
          created = true;
          return jsonResponse(
            201,
            '{"id":"generated-id","name":"new.epub",'
            '"mimeType":"application/epub+zip"}',
          );
        }
        if (request.uri.path == '/drive/v3/files' &&
            request.uri.queryParameters['q']?.contains('name =') == true) {
          return jsonResponse(
            200,
            created
                ? '{"files":[{"id":"generated-id","name":"new.epub",'
                      '"mimeType":"application/epub+zip","version":"1",'
                      '"md5Checksum":"$checksum"}]}'
                : '{"files":[]}',
          );
        }
        if (request.uri.path == '/drive/v3/files/generated-id') {
          return jsonResponse(
            200,
            '{"id":"generated-id","name":"new.epub",'
            '"mimeType":"application/epub+zip","version":"1",'
            '"md5Checksum":"$checksum"}',
            headers: const <String, String>{'etag': '"created"'},
          );
        }
        fail('Unexpected Drive request: ${request.method} ${request.uri}');
      });
      final adapter = GoogleDriveCloudAdapter(
        rootFolderId: 'root-folder',
        transport: transport,
        credentialStore: credentials,
        credentialScope: scope,
        apiEndpoint: Uri.parse('https://drive.fixture/drive/v3'),
        uploadEndpoint: Uri.parse('https://drive.fixture/upload/drive/v3'),
        tokenEndpoint: Uri.parse('https://drive.fixture/oauth2/token'),
      );

      final first = await adapter.write(
        'new.epub',
        Stream<List<int>>.value(<int>[9]),
      );
      final retry = await adapter.write(
        'new.epub',
        Stream<List<int>>.value(<int>[9]),
      );
      expect(first.etag, '"created"');
      expect(retry.etag, '"created"');
      expect(
        transport.requests.where(
          (request) => request.uri.path == '/upload/drive/v3/files',
        ),
        hasLength(1),
      );
    },
  );

  test('Google Drive exact update retry verifies content hash', () async {
    final scope = CredentialScope('drive-update-retry');
    final credentials = MemoryCredentialStore(<String, String>{
      scope.key('access_token'): 'token',
    });
    final checksum = md5.convert(<int>[3, 4]).toString();
    var patched = false;
    final transport = FixtureTransport((request, _) async {
      if (request.uri.path == '/drive/v3/files') {
        final currentChecksum = patched ? checksum : 'old';
        return jsonResponse(
          200,
          '{"files":[{"id":"file-a","name":"a.epub",'
          '"mimeType":"application/epub+zip","version":"2",'
          '"md5Checksum":"$currentChecksum"}]}',
        );
      }
      if (request.uri.path == '/upload/drive/v3/files/file-a') {
        patched = true;
        return jsonResponse(412, '{"error":{"code":412}}');
      }
      if (request.uri.path == '/drive/v3/files/file-a') {
        return jsonResponse(
          200,
          '{"id":"file-a","name":"a.epub",'
          '"mimeType":"application/epub+zip","version":"2",'
          '"md5Checksum":"$checksum"}',
          headers: const <String, String>{'etag': '"current"'},
        );
      }
      fail('Unexpected Drive request');
    });
    final adapter = GoogleDriveCloudAdapter(
      rootFolderId: 'root-folder',
      transport: transport,
      credentialStore: credentials,
      credentialScope: scope,
      apiEndpoint: Uri.parse('https://drive.fixture/drive/v3'),
      uploadEndpoint: Uri.parse('https://drive.fixture/upload/drive/v3'),
      tokenEndpoint: Uri.parse('https://drive.fixture/oauth2/token'),
    );
    final result = await adapter.write(
      'a.epub',
      Stream<List<int>>.value(<int>[3, 4]),
      etag: '"old"',
    );
    expect(result.etag, '"current"');
  });
}
