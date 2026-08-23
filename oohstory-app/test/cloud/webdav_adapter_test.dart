import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/cloud/cloud.dart';
import 'package:oohstory/core/core.dart';

import 'cloud_test_support.dart';

void main() {
  test('WebDAV fixture covers paging and full CRUD with ETags', () async {
    final scope = CredentialScope('webdav-test');
    final credentials = MemoryCredentialStore(<String, String>{
      scope.key('username'): 'fixture-user',
      scope.key('password'): 'fixture-password',
    });
    late FixtureTransport transport;
    transport = FixtureTransport((request, body) async {
      expect(request.headers['authorization'], startsWith('Basic '));
      switch (request.method) {
        case 'PROPFIND':
          final depth = request.headers['depth'];
          final xml = depth == '1'
              ? _multistatus(<String>[
                  _response('/dav/OOHStory/', directory: true),
                  _response('/dav/OOHStory/a.epub', etag: '"a1"'),
                  _response('/dav/OOHStory/folder/', directory: true),
                ])
              : _multistatus(<String>[
                  _response(request.uri.path, etag: '"next"'),
                ]);
          return CloudHttpResponse.bytes(
            statusCode: 207,
            body: utf8.encode(xml),
          );
        case 'GET':
          return CloudHttpResponse.bytes(statusCode: 200, body: <int>[1, 2, 3]);
        case 'PUT':
          expect(request.headers['if-match'], '"old"');
          expect(body, <int>[4, 5]);
          return CloudHttpResponse.bytes(statusCode: 204);
        case 'DELETE':
          expect(request.headers['if-match'], '"next"');
          return CloudHttpResponse.bytes(statusCode: 204);
      }
      fail('Unexpected WebDAV request: ${request.method} ${request.uri}');
    });
    final adapter = WebDavCloudAdapter(
      endpoint: Uri.parse('https://webdav.example.test/dav'),
      root: 'OOHStory',
      transport: transport,
      credentialStore: credentials,
      credentialScope: scope,
      pageSize: 1,
      retryPolicy: const RetryPolicy(initialDelay: Duration.zero),
    );

    final first = await adapter.list('');
    expect(first.items.single.path, 'a.epub');
    expect(first.nextCursor, isNotEmpty);
    final second = await adapter.list('', cursor: first.nextCursor);
    expect(second.items.single.path, 'folder');
    expect(second.nextCursor, isNull);
    expect((await adapter.stat('a.epub')).etag, '"next"');
    expect(
      await adapter.read('a.epub').expand((chunk) => chunk).toList(),
      <int>[1, 2, 3],
    );
    expect(
      (await adapter.write(
        'a.epub',
        Stream<List<int>>.value(<int>[4, 5]),
        etag: '"old"',
      )).etag,
      '"next"',
    );
    await adapter.delete('a.epub', etag: '"next"');
    expect(
      transport.requests.every(
        (request) => request.uri.path.startsWith('/dav/OOHStory'),
      ),
      isTrue,
    );
  });

  test(
    'WebDAV exact create and update retries require matching bytes',
    () async {
      final scope = CredentialScope('webdav-idempotency');
      final credentials = MemoryCredentialStore(<String, String>{
        scope.key('username'): 'user',
        scope.key('password'): 'password',
      });
      final putHeaders = <Map<String, String>>[];
      final transport = FixtureTransport((request, _) async {
        if (request.method == 'PUT') {
          putHeaders.add(request.headers);
          return CloudHttpResponse.bytes(statusCode: 412);
        }
        if (request.method == 'GET') {
          return CloudHttpResponse.bytes(statusCode: 200, body: <int>[7, 8]);
        }
        if (request.method == 'PROPFIND') {
          return CloudHttpResponse.bytes(
            statusCode: 207,
            body: utf8.encode(
              _multistatus(<String>[
                _response('/dav/OOHStory/book.epub', etag: 'same'),
              ]),
            ),
          );
        }
        fail('Unexpected request');
      });
      final adapter = WebDavCloudAdapter(
        endpoint: Uri.parse('https://webdav.example.test/dav'),
        root: 'OOHStory',
        transport: transport,
        credentialStore: credentials,
        credentialScope: scope,
      );
      final result = await adapter.write(
        'book.epub',
        Stream<List<int>>.value(<int>[7, 8]),
      );
      final updateRetry = await adapter.write(
        'book.epub',
        Stream<List<int>>.value(<int>[7, 8]),
        etag: 'old',
      );
      expect(result.etag, 'same');
      expect(updateRetry.etag, 'same');
      expect(putHeaders.first['if-none-match'], '*');
      expect(putHeaders.last['if-match'], 'old');
    },
  );

  test('WebDAV rejects unsafe XML and traversal before data use', () async {
    final scope = CredentialScope('webdav-unsafe');
    final credentials = MemoryCredentialStore(<String, String>{
      scope.key('username'): 'user',
      scope.key('password'): 'password',
    });
    final transport = FixtureTransport(
      (_, _) async => CloudHttpResponse.bytes(
        statusCode: 207,
        body: utf8.encode(
          '<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///etc/passwd">]>'
          '<d:multistatus xmlns:d="DAV:">&leak;</d:multistatus>',
        ),
      ),
    );
    final adapter = WebDavCloudAdapter(
      endpoint: Uri.parse('https://webdav.example.test/dav'),
      root: 'OOHStory',
      transport: transport,
      credentialStore: credentials,
      credentialScope: scope,
    );
    await expectLater(
      adapter.list(''),
      throwsA(
        isA<CoreException>().having(
          (error) => error.code,
          'code',
          CoreErrorCode.upstreamError,
        ),
      ),
    );
    await expectLater(
      adapter.stat('../outside'),
      throwsA(isA<CoreException>()),
    );
  });
}

String _multistatus(List<String> responses) =>
    '<d:multistatus xmlns:d="DAV:">${responses.join()}</d:multistatus>';

String _response(String href, {bool directory = false, String? etag}) =>
    '<d:response><d:href>$href</d:href><d:propstat><d:prop>'
    '<d:resourcetype>${directory ? '<d:collection/>' : ''}</d:resourcetype>'
    '${etag == null ? '' : '<d:getetag>$etag</d:getetag>'}'
    '</d:prop></d:propstat></d:response>';
