import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../../core/errors.dart';
import 'cloud_support.dart';

abstract interface class SecureCredentialStore {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
  Future<void> delete(String key);
}

final class FlutterSecureCredentialStore implements SecureCredentialStore {
  const FlutterSecureCredentialStore({
    FlutterSecureStorage storage = const FlutterSecureStorage(),
  }) : _storage = storage;

  final FlutterSecureStorage _storage;

  @override
  Future<String?> read(String key) => _storage.read(key: key);

  @override
  Future<void> write(String key, String value) =>
      _storage.write(key: key, value: value);

  @override
  Future<void> delete(String key) => _storage.delete(key: key);
}

final class CredentialScope {
  CredentialScope(String id) : id = _validate(id);

  final String id;

  String key(String field) => 'oohstory.cloud.$id.$field';

  static String _validate(String id) {
    if (!RegExp(r'^[A-Za-z0-9._-]{1,64}$').hasMatch(id)) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Credential scope is invalid',
      );
    }
    return id;
  }
}

final class BasicCredentialProvider {
  const BasicCredentialProvider({required this.store, required this.scope});

  final SecureCredentialStore store;
  final CredentialScope scope;

  Future<Map<String, String>> authorizationHeaders() async {
    final username = await store.read(scope.key('username'));
    final password = await store.read(scope.key('password'));
    if (username == null || password == null) _missingCredential();
    final encoded = base64Encode(utf8.encode('$username:$password'));
    return <String, String>{'authorization': 'Basic $encoded'};
  }
}

final class OAuthAccessTokenProvider {
  OAuthAccessTokenProvider({
    required this.store,
    required this.scope,
    required this.tokenEndpoint,
    required this.transport,
    required this.runtimePolicy,
    this.retryPolicy = const RetryPolicy(),
  });

  final SecureCredentialStore store;
  final CredentialScope scope;
  final Uri tokenEndpoint;
  final CloudHttpTransport transport;
  final CloudRuntimePolicy runtimePolicy;
  final RetryPolicy retryPolicy;
  Future<bool>? _refreshing;

  Future<Map<String, String>> authorizationHeaders() async {
    final token = await store.read(scope.key('access_token'));
    if (token == null || token.isEmpty) _missingCredential();
    return <String, String>{
      'authorization': 'Bearer ${_headerCredential(token)}',
    };
  }

  Future<bool> refresh({CancellationToken? cancellationToken}) async {
    final active = _refreshing;
    if (active != null) return active;
    late Future<bool> request;
    request = _refreshOnce(cancellationToken: cancellationToken).whenComplete(
      () {
        if (identical(_refreshing, request)) _refreshing = null;
      },
    );
    _refreshing = request;
    return request;
  }

  Future<bool> _refreshOnce({CancellationToken? cancellationToken}) async {
    runtimePolicy.validate(tokenEndpoint);
    final refreshToken = await store.read(scope.key('refresh_token'));
    final clientId = await store.read(scope.key('client_id'));
    final clientSecret = await store.read(scope.key('client_secret'));
    if (refreshToken == null || clientId == null) return false;

    final fields = <String, String>{
      'grant_type': 'refresh_token',
      'refresh_token': refreshToken,
      'client_id': clientId,
      if (clientSecret != null) 'client_secret': clientSecret,
    };
    final body = utf8.encode(
      fields.entries
          .map(
            (entry) =>
                '${Uri.encodeQueryComponent(entry.key)}='
                '${Uri.encodeQueryComponent(entry.value)}',
          )
          .join('&'),
    );
    final response = await retryPolicy.send(
      transport,
      CloudHttpRequest(
        method: 'POST',
        uri: tokenEndpoint,
        headers: const <String, String>{
          'content-type': 'application/x-www-form-urlencoded',
        },
        bodyFactory: () => Stream<List<int>>.value(body),
        contentLength: body.length,
      ),
      cancellationToken: cancellationToken,
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      await response.body.drain<void>();
      return false;
    }
    final payload = await decodeJsonObject(response);
    final accessToken = payload['access_token'];
    if (accessToken is! String || accessToken.isEmpty) return false;
    final rotatedRefreshToken = payload['refresh_token'];
    if (rotatedRefreshToken is String && rotatedRefreshToken.isNotEmpty) {
      await store.write(scope.key('refresh_token'), rotatedRefreshToken);
    }
    await store.write(scope.key('access_token'), accessToken);
    return true;
  }
}

String _headerCredential(String value) {
  if (value.length > 16384 ||
      value.runes.any((rune) => rune < 0x20 || rune == 0x7f)) {
    throw const CoreException(
      CoreErrorCode.unauthorized,
      'Cloud provider credentials are invalid',
    );
  }
  return value;
}

Never _missingCredential() => throw const CoreException(
  CoreErrorCode.unauthorized,
  'Cloud provider credentials are unavailable',
);
