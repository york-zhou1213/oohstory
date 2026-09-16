import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/cloud/cloud.dart';
import 'package:oohstory/core/core.dart';
import 'package:oohstory/features/cloud_library/cloud_library.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'cloud_test_support.dart';

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  test(
    'verified connection separates settings from secure credentials',
    () async {
      final preferences = await SharedPreferences.getInstance();
      final credentials = MemoryCredentialStore();
      final repository = CloudConnectionRepository(
        preferences: preferences,
        credentialStore: credentials,
      );
      final transport = FixtureTransport((request, _) async {
        expect(request.method, 'PROPFIND');
        expect(request.headers['authorization'], isNotNull);
        return CloudHttpResponse.bytes(
          statusCode: 207,
          body: utf8.encode('<d:multistatus xmlns:d="DAV:"/>'),
        );
      });
      final manager = CloudConnectionManager(
        repository: repository,
        adapterFactory: CloudAdapterFactory(transport: transport),
      );
      final config = CloudConnectionConfig(
        id: 'webdav-primary',
        provider: CloudProviderKind.webDav,
        name: 'Home DAV',
        endpoint: Uri.parse('https://dav.example.test/dav'),
        root: 'OOHStory',
      );

      await manager.verifyAndSave(
        config,
        CloudConnectionCredentials.webDav(
          username: 'reader',
          password: 'do-not-store-in-preferences',
        ),
      );

      expect(repository.load().single.name, 'Home DAV');
      final storedSettings = preferences.getString(
        'oohstory.cloud.connections.v1',
      )!;
      expect(storedSettings, contains('dav.example.test'));
      expect(storedSettings, isNot(contains('reader')));
      expect(storedSettings, isNot(contains('do-not-store-in-preferences')));
      final scope = CredentialScope(config.id);
      expect(credentials.values[scope.key('username')], 'reader');
      expect(
        credentials.values[scope.key('password')],
        'do-not-store-in-preferences',
      );
    },
  );

  test(
    'failed verification restores credentials and saves no config',
    () async {
      final preferences = await SharedPreferences.getInstance();
      final config = CloudConnectionConfig(
        id: 'webdav-rollback',
        provider: CloudProviderKind.webDav,
        name: 'Broken DAV',
        endpoint: Uri.parse('https://dav.example.test/dav'),
        root: 'OOHStory',
      );
      final scope = CredentialScope(config.id);
      final credentials = MemoryCredentialStore(<String, String>{
        scope.key('username'): 'old-user',
        scope.key('password'): 'old-password',
      });
      final repository = CloudConnectionRepository(
        preferences: preferences,
        credentialStore: credentials,
      );
      final manager = CloudConnectionManager(
        repository: repository,
        adapterFactory: CloudAdapterFactory(
          transport: FixtureTransport(
            (_, _) async => CloudHttpResponse.bytes(statusCode: 401),
          ),
        ),
      );

      await expectLater(
        manager.verifyAndSave(
          config,
          CloudConnectionCredentials.webDav(
            username: 'new-user',
            password: 'new-password',
          ),
        ),
        throwsA(
          isA<CoreException>().having(
            (error) => error.code,
            'code',
            CoreErrorCode.unauthorized,
          ),
        ),
      );

      expect(repository.load(), isEmpty);
      expect(credentials.values[scope.key('username')], 'old-user');
      expect(credentials.values[scope.key('password')], 'old-password');
    },
  );

  test('removing a connection clears all credential fields', () async {
    final preferences = await SharedPreferences.getInstance();
    final credentials = MemoryCredentialStore();
    final repository = CloudConnectionRepository(
      preferences: preferences,
      credentialStore: credentials,
    );
    final config = CloudConnectionConfig(
      id: 's3-remove',
      provider: CloudProviderKind.s3,
      name: 'Archive',
      endpoint: Uri.parse('https://s3.example.test'),
      root: 'OOHStory',
      bucket: 'books-bucket',
      region: 'us-east-1',
    );
    await repository.upsert(config);
    await repository.writeCredentials(
      config,
      CloudConnectionCredentials.s3(
        accessKey: 'access',
        secretKey: 'secret',
        sessionToken: 'session',
      ),
    );

    await repository.remove(config.id);

    expect(repository.load(), isEmpty);
    expect(credentials.values, isEmpty);
  });

  test('stored config rejects duplicates and never accepts HTTP', () async {
    final preferences = await SharedPreferences.getInstance();
    final credentials = MemoryCredentialStore();
    final repository = CloudConnectionRepository(
      preferences: preferences,
      credentialStore: credentials,
    );
    expect(
      () => CloudConnectionConfig(
        id: 'unsafe-http',
        provider: CloudProviderKind.webDav,
        name: 'Unsafe',
        endpoint: Uri.parse('http://dav.example.test'),
        root: 'OOHStory',
      ),
      throwsA(isA<CoreException>()),
    );
    await preferences.setString(
      'oohstory.cloud.connections.v1',
      jsonEncode(<String, Object?>{
        'version': 1,
        'connections': <Object?>[
          <String, Object?>{
            'id': 'duplicate',
            'provider': 'webDav',
            'name': 'One',
            'endpoint': 'https://dav.example.test',
            'root': 'OOHStory',
          },
          <String, Object?>{
            'id': 'duplicate',
            'provider': 'webDav',
            'name': 'Two',
            'endpoint': 'https://dav.example.test',
            'root': 'OOHStory',
          },
        ],
      }),
    );
    expect(repository.load, throwsA(isA<CoreException>()));
  });
}
