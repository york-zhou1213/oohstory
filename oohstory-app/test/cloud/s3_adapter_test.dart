import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/cloud/cloud.dart';
import 'package:oohstory/core/core.dart';

import 'cloud_test_support.dart';

void main() {
  test('S3-compatible fixture covers signed paging and full CRUD', () async {
    final scope = CredentialScope('s3-test');
    final credentials = MemoryCredentialStore(<String, String>{
      scope.key('access_key'): 'AKIAFIXTURE',
      scope.key('secret_key'): 'fixture-secret-that-must-not-leak',
      scope.key('session_token'): 'fixture-session-token',
    });
    final transport = FixtureTransport((request, body) async {
      expect(
        request.headers['authorization'],
        matches(
          RegExp(
            r'^AWS4-HMAC-SHA256 Credential=AKIAFIXTURE/20260823/'
            r'us-east-1/s3/aws4_request, SignedHeaders=.+, Signature=[0-9a-f]{64}$',
          ),
        ),
      );
      expect(request.headers['x-amz-date'], '20260823T123456Z');
      expect(
        request.headers['x-amz-content-sha256'],
        request.method == 'PUT'
            ? '9f64a747e1b97f131fabb6b447296c9b6f0201e79fb3c5356e6c77e89b6a806a'
            : 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
      );
      if (request.method == 'GET' &&
          request.uri.queryParameters['list-type'] == '2') {
        expect(request.uri.queryParameters['prefix'], 'OOHStory/books/');
        return CloudHttpResponse.bytes(
          statusCode: 200,
          body: utf8.encode('''
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Contents><Key>OOHStory/books/a.epub</Key><ETag>&quot;a1&quot;</ETag></Contents>
  <CommonPrefixes><Prefix>OOHStory/books/folder/</Prefix></CommonPrefixes>
  <NextContinuationToken>next-page</NextContinuationToken>
</ListBucketResult>'''),
        );
      }
      if (request.method == 'HEAD') {
        return CloudHttpResponse.bytes(
          statusCode: 200,
          headers: const <String, String>{'etag': '"v2"'},
        );
      }
      if (request.method == 'GET') {
        return CloudHttpResponse.bytes(statusCode: 200, body: <int>[1, 2, 3]);
      }
      if (request.method == 'PUT') {
        expect(request.headers['if-match'], '"v1"');
        expect(body, <int>[1, 2, 3, 4]);
        return CloudHttpResponse.bytes(statusCode: 200);
      }
      if (request.method == 'DELETE') {
        expect(request.headers['if-match'], '"v2"');
        return CloudHttpResponse.bytes(statusCode: 204);
      }
      fail('Unexpected S3 request: ${request.method} ${request.uri}');
    });
    final adapter = S3CloudAdapter(
      endpoint: Uri.parse('https://s3.example.test'),
      bucket: 'fixture-bucket',
      root: 'OOHStory',
      region: 'us-east-1',
      transport: transport,
      credentialStore: credentials,
      credentialScope: scope,
      clock: () => DateTime.utc(2026, 8, 23, 12, 34, 56),
    );

    final page = await adapter.list('books');
    expect(page.items.map((entry) => entry.path), <String>[
      'books/a.epub',
      'books/folder',
    ]);
    expect(page.nextCursor, 'next-page');
    expect((await adapter.stat('books/a.epub')).etag, '"v2"');
    expect(
      await adapter.read('books/a.epub').expand((value) => value).toList(),
      <int>[1, 2, 3],
    );
    expect(
      (await adapter.write(
        'books/a.epub',
        Stream<List<int>>.value(<int>[1, 2, 3, 4]),
        etag: '"v1"',
      )).etag,
      '"v2"',
    );
    await adapter.delete('books/a.epub', etag: '"v2"');
    expect(
      transport.requests.every((request) {
        if (request.uri.queryParameters['list-type'] == '2') {
          return request.uri.path == '/fixture-bucket' &&
              request.uri.queryParameters['prefix']!.startsWith('OOHStory/');
        }
        return request.uri.path.startsWith('/fixture-bucket/OOHStory');
      }),
      isTrue,
    );
    expect(
      transport.requests.toString(),
      isNot(contains('fixture-secret-that-must-not-leak')),
    );
  });

  test('S3 rejects XML entities and stale ETags', () async {
    final scope = CredentialScope('s3-negative');
    final credentials = MemoryCredentialStore(<String, String>{
      scope.key('access_key'): 'key',
      scope.key('secret_key'): 'secret',
    });
    var listRequest = true;
    final transport = FixtureTransport((request, _) async {
      if (listRequest) {
        listRequest = false;
        return CloudHttpResponse.bytes(
          statusCode: 200,
          body: utf8.encode(
            '<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///etc/passwd">]>'
            '<ListBucketResult>&leak;</ListBucketResult>',
          ),
        );
      }
      return CloudHttpResponse.bytes(statusCode: 412);
    });
    final adapter = S3CloudAdapter(
      endpoint: Uri.parse('https://s3.example.test'),
      bucket: 'fixture-bucket',
      root: 'OOHStory',
      region: 'us-east-1',
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
      adapter.write(
        'book.epub',
        Stream<List<int>>.value(<int>[1]),
        etag: 'stale',
      ),
      throwsA(
        isA<CoreException>().having(
          (error) => error.code,
          'code',
          CoreErrorCode.revisionConflict,
        ),
      ),
    );
  });

  test('S3 exact update retry verifies remote bytes before success', () async {
    final scope = CredentialScope('s3-idempotency');
    final credentials = MemoryCredentialStore(<String, String>{
      scope.key('access_key'): 'key',
      scope.key('secret_key'): 'secret',
    });
    final transport = FixtureTransport((request, _) async {
      switch (request.method) {
        case 'PUT':
          expect(request.headers['if-match'], 'old');
          return CloudHttpResponse.bytes(statusCode: 412);
        case 'GET':
          return CloudHttpResponse.bytes(statusCode: 200, body: <int>[8, 9]);
        case 'HEAD':
          return CloudHttpResponse.bytes(
            statusCode: 200,
            headers: const <String, String>{'etag': 'current'},
          );
      }
      fail('Unexpected S3 request');
    });
    final adapter = S3CloudAdapter(
      endpoint: Uri.parse('https://s3.example.test'),
      bucket: 'fixture-bucket',
      root: 'OOHStory',
      region: 'us-east-1',
      transport: transport,
      credentialStore: credentials,
      credentialScope: scope,
    );
    final result = await adapter.write(
      'book.epub',
      Stream<List<int>>.value(<int>[8, 9]),
      etag: 'old',
    );
    expect(result.etag, 'current');
  });

  test('S3 SigV4 uses AWS RFC3986 query encoding', () async {
    final scope = CredentialScope('s3-signing-vector');
    final credentials = MemoryCredentialStore(<String, String>{
      scope.key('access_key'): 'AKIDEXAMPLE',
      scope.key('secret_key'): 'wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY',
    });
    final transport = FixtureTransport((request, _) async {
      expect(
        request.headers['authorization'],
        'AWS4-HMAC-SHA256 '
        'Credential=AKIDEXAMPLE/20260823/us-east-1/s3/aws4_request, '
        'SignedHeaders=host;x-amz-content-sha256;x-amz-date, '
        'Signature=8cf6bed4fdce411c655591eb0f2890286bd63ba399df968bcc9770b01e6d545a',
      );
      return CloudHttpResponse.bytes(
        statusCode: 200,
        body: utf8.encode('<ListBucketResult></ListBucketResult>'),
      );
    });
    final adapter = S3CloudAdapter(
      endpoint: Uri.parse('https://s3.example.test'),
      bucket: 'fixture-bucket',
      root: 'OOHStory',
      region: 'us-east-1',
      transport: transport,
      credentialStore: credentials,
      credentialScope: scope,
      clock: () => DateTime.utc(2026, 8, 23, 12, 34, 56),
    );

    await adapter.list('My Books/雪+人');
  });
}
