import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

import '../../core/errors.dart';
import '../../core/models.dart';
import 'cloud_support.dart';
import 'http_cloud_adapter.dart';
import 'safe_xml.dart';
import 'secure_credentials.dart';

final class S3CloudAdapter extends HttpCloudLibraryAdapter {
  // ignore: use_super_parameters
  S3CloudAdapter({
    required Uri endpoint,
    required String bucket,
    required String root,
    required String region,
    required CloudHttpTransport transport,
    required this.credentialStore,
    required this.credentialScope,
    CloudRuntimePolicy? runtimePolicy,
    super.retryPolicy,
    super.cancellationToken,
    super.logger,
    super.maxUploadBytes,
    DateTime Function()? clock,
  }) : bucket = _validateBucket(bucket),
       region = _validateRegion(region),
       _clock = clock ?? (() => DateTime.now().toUtc()),
       super(
         providerId: 's3',
         endpoint: endpoint,
         root: root,
         transport: transport,
         runtimePolicy: runtimePolicy,
       );

  final String bucket;
  final String region;
  final SecureCredentialStore credentialStore;
  final CredentialScope credentialScope;
  final DateTime Function() _clock;

  @override
  Future<SyncPage<CloudEntry>> list(String path, {String? cursor}) async {
    final validCursor = validateCursor(cursor);
    var prefix = root.slashPath(path, leadingSlash: false);
    if (!prefix.endsWith('/')) prefix = '$prefix/';
    final query = <String, String>{
      'list-type': '2',
      'delimiter': '/',
      'prefix': prefix,
      if (validCursor != null) 'continuation-token': validCursor,
    };
    final response = await _signed(
      CloudHttpRequest(
        method: 'GET',
        uri: endpointWithSegments(<String>[bucket], queryParameters: query),
      ),
      const <int>[],
    );
    await expectStatus(response, const <int>{200});
    final bytes = await collectResponseBytes(response);
    try {
      final document = utf8.decode(bytes);
      final entries = <CloudEntry>[];
      for (final contents in xmlElements(document, 'Contents')) {
        final key = xmlText(contents, 'Key');
        if (key == null || key == prefix) continue;
        entries.add(
          CloudEntry(
            path: root.relativeFromSegments(key.split('/')),
            isDirectory: false,
            etag: xmlText(contents, 'ETag'),
          ),
        );
      }
      for (final commonPrefix in xmlElements(document, 'CommonPrefixes')) {
        final key = xmlText(commonPrefix, 'Prefix');
        if (key == null) continue;
        entries.add(
          CloudEntry(
            path: root.relativeFromSegments(
              key.split('/').where((segment) => segment.isNotEmpty),
            ),
            isDirectory: true,
          ),
        );
      }
      entries.sort((left, right) => left.path.compareTo(right.path));
      final next = validateCursor(xmlText(document, 'NextContinuationToken'));
      return SyncPage<CloudEntry>(
        items: entries,
        nextCursor: next,
        serverTime: _clock(),
      );
    } on CoreException {
      rethrow;
    } on Object {
      invalidProviderResponse();
    }
  }

  @override
  Future<CloudEntry> stat(String path) async {
    final response = await _signed(
      CloudHttpRequest(method: 'HEAD', uri: _objectUri(path)),
      const <int>[],
    );
    await expectStatus(response, const <int>{200});
    await response.body.drain<void>();
    return CloudEntry(
      path: root.relativeFromSegments(root.resolve(path)),
      isDirectory: false,
      etag: response.header('etag'),
    );
  }

  @override
  Stream<List<int>> read(String path) async* {
    final response = await _signed(
      CloudHttpRequest(method: 'GET', uri: _objectUri(path)),
      const <int>[],
    );
    await expectStatus(response, const <int>{200, 206});
    yield* response.body;
  }

  @override
  Future<CloudEntry> write(
    String path,
    Stream<List<int>> bytes, {
    String? etag,
  }) async {
    final body = await uploadBytes(bytes);
    final response = await _signed(
      CloudHttpRequest(
        method: 'PUT',
        uri: _objectUri(path),
        headers: <String, String>{
          'content-type': 'application/octet-stream',
          if (etag == null) 'if-none-match': '*' else 'if-match': etag,
        },
        bodyFactory: () => repeatableBody(body),
        contentLength: body.length,
      ),
      body,
    );
    if (response.statusCode == 409 || response.statusCode == 412) {
      await response.body.drain<void>();
      if (await _matchesExisting(path, body)) {
        return stat(path);
      }
      throw cloudStatusError(response.statusCode);
    }
    await expectStatus(response, const <int>{200, 201, 204});
    await response.body.drain<void>();
    return stat(path);
  }

  @override
  Future<void> delete(String path, {String? etag}) async {
    final response = await _signed(
      CloudHttpRequest(
        method: 'DELETE',
        uri: _objectUri(path),
        headers: <String, String>{if (etag != null) 'if-match': etag},
      ),
      const <int>[],
    );
    if (response.statusCode == 404) {
      await response.body.drain<void>();
      return;
    }
    await expectStatus(response, const <int>{200, 202, 204});
    await response.body.drain<void>();
  }

  Uri _objectUri(String path) => endpointWithSegments(<String>[
    bucket,
    ...root.resolve(root.requireDescendant(path)),
  ]);

  Future<CloudHttpResponse> _signed(
    CloudHttpRequest request,
    List<int> payload,
  ) async {
    final accessKey = await credentialStore.read(
      credentialScope.key('access_key'),
    );
    final secretKey = await credentialStore.read(
      credentialScope.key('secret_key'),
    );
    final sessionToken = await credentialStore.read(
      credentialScope.key('session_token'),
    );
    if (accessKey == null || secretKey == null) {
      throw const CoreException(
        CoreErrorCode.unauthorized,
        'Cloud provider credentials are unavailable',
      );
    }
    final safeAccessKey = _validateHeaderCredential(accessKey);
    final safeSessionToken = sessionToken == null
        ? null
        : _validateHeaderCredential(sessionToken);
    final signed = _sign(
      request,
      payload,
      accessKey: safeAccessKey,
      secretKey: secretKey,
      sessionToken: safeSessionToken,
    );
    return send(signed);
  }

  CloudHttpRequest _sign(
    CloudHttpRequest request,
    List<int> payload, {
    required String accessKey,
    required String secretKey,
    String? sessionToken,
  }) {
    final now = _clock().toUtc();
    final date = _date(now);
    final timestamp =
        '${date}T${_two(now.hour)}${_two(now.minute)}'
        '${_two(now.second)}Z';
    final payloadHash = sha256.convert(payload).toString();
    final headers = <String, String>{
      ...request.headers,
      'host': request.uri.hasPort
          ? '${request.uri.host}:${request.uri.port}'
          : request.uri.host,
      'x-amz-content-sha256': payloadHash,
      'x-amz-date': timestamp,
      if (sessionToken != null) 'x-amz-security-token': sessionToken,
    };
    final canonicalEntries =
        headers.entries
            .map(
              (entry) => MapEntry(
                entry.key.toLowerCase(),
                entry.value.trim().replaceAll(RegExp(r'\s+'), ' '),
              ),
            )
            .toList()
          ..sort((left, right) => left.key.compareTo(right.key));
    final canonicalHeaders = canonicalEntries
        .map((entry) => '${entry.key}:${entry.value}\n')
        .join();
    final signedHeaders = canonicalEntries.map((entry) => entry.key).join(';');
    final canonicalRequest = <String>[
      request.method,
      request.uri.path.isEmpty ? '/' : request.uri.path,
      _canonicalQuery(request.uri),
      canonicalHeaders,
      signedHeaders,
      payloadHash,
    ].join('\n');
    final scope = '$date/$region/s3/aws4_request';
    final stringToSign = <String>[
      'AWS4-HMAC-SHA256',
      timestamp,
      scope,
      sha256.convert(utf8.encode(canonicalRequest)).toString(),
    ].join('\n');
    final dateKey = _hmac(utf8.encode('AWS4$secretKey'), date);
    final regionKey = _hmac(dateKey, region);
    final serviceKey = _hmac(regionKey, 's3');
    final signingKey = _hmac(serviceKey, 'aws4_request');
    final signature = _hex(_hmac(signingKey, stringToSign));
    return request.copyWith(
      headers: <String, String>{
        ...headers,
        'authorization':
            'AWS4-HMAC-SHA256 Credential=$accessKey/$scope, '
            'SignedHeaders=$signedHeaders, Signature=$signature',
      },
    );
  }

  String _canonicalQuery(Uri uri) {
    final pairs = <MapEntry<String, String>>[];
    uri.queryParametersAll.forEach((key, values) {
      for (final value in values) {
        pairs.add(MapEntry(_encode(key), _encode(value)));
      }
    });
    pairs.sort((left, right) {
      final keyOrder = left.key.compareTo(right.key);
      return keyOrder != 0 ? keyOrder : left.value.compareTo(right.value);
    });
    return pairs.map((entry) => '${entry.key}=${entry.value}').join('&');
  }

  String _encode(String value) => Uri.encodeComponent(value)
      .replaceAll('!', '%21')
      .replaceAll("'", '%27')
      .replaceAll('(', '%28')
      .replaceAll(')', '%29')
      .replaceAll('*', '%2A');

  List<int> _hmac(List<int> key, String value) =>
      Hmac(sha256, key).convert(utf8.encode(value)).bytes;

  String _hex(List<int> bytes) =>
      bytes.map((value) => value.toRadixString(16).padLeft(2, '0')).join();

  String _date(DateTime value) =>
      '${value.year.toString().padLeft(4, '0')}${_two(value.month)}'
      '${_two(value.day)}';

  String _two(int value) => value.toString().padLeft(2, '0');

  Future<bool> _matchesExisting(String path, Uint8List expected) async {
    try {
      final actual = await collectBytes(
        read(path),
        maxBytes: maxUploadBytes,
        cancellationToken: cancellationToken,
      );
      if (actual.length != expected.length) return false;
      for (var index = 0; index < actual.length; index++) {
        if (actual[index] != expected[index]) return false;
      }
      return true;
    } on CoreException {
      return false;
    }
  }

  static String _validateBucket(String value) {
    if (!RegExp(r'^[A-Za-z0-9][A-Za-z0-9._-]{1,62}$').hasMatch(value)) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'S3 bucket name is invalid',
      );
    }
    return value;
  }

  static String _validateRegion(String value) {
    if (!RegExp(r'^[a-z0-9-]{1,64}$').hasMatch(value)) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'S3 region is invalid',
      );
    }
    return value;
  }

  static String _validateHeaderCredential(String value) {
    if (value.isEmpty ||
        value.length > 16384 ||
        value.runes.any((rune) => rune < 0x20 || rune == 0x7f)) {
      throw const CoreException(
        CoreErrorCode.unauthorized,
        'Cloud provider credentials are invalid',
      );
    }
    return value;
  }
}
