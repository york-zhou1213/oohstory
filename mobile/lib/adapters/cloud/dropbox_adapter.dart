import 'dart:convert';
import 'dart:typed_data';

import '../../core/errors.dart';
import '../../core/models.dart';
import 'cloud_support.dart';
import 'http_cloud_adapter.dart';
import 'secure_credentials.dart';

final class DropboxCloudAdapter extends HttpCloudLibraryAdapter {
  // ignore: use_super_parameters
  DropboxCloudAdapter({
    required String root,
    required CloudHttpTransport transport,
    required SecureCredentialStore credentialStore,
    required CredentialScope credentialScope,
    Uri? apiEndpoint,
    Uri? contentEndpoint,
    Uri? tokenEndpoint,
    CloudRuntimePolicy? runtimePolicy,
    super.retryPolicy,
    super.cancellationToken,
    super.logger,
    super.maxUploadBytes,
    DateTime Function()? clock,
  }) : _contentEndpoint =
           contentEndpoint ?? Uri.https('content.dropboxapi.com', '/2'),
       _clock = clock ?? (() => DateTime.now().toUtc()),
       _tokens = OAuthAccessTokenProvider(
         store: credentialStore,
         scope: credentialScope,
         tokenEndpoint:
             tokenEndpoint ?? Uri.https('api.dropboxapi.com', '/oauth2/token'),
         transport: transport,
         runtimePolicy: runtimePolicy ?? CloudRuntimePolicy.current(),
         retryPolicy: retryPolicy,
       ),
       super(
         providerId: 'dropbox',
         endpoint: apiEndpoint ?? Uri.https('api.dropboxapi.com', '/2'),
         root: root,
         transport: transport,
         runtimePolicy: runtimePolicy,
       ) {
    this.runtimePolicy.validate(_contentEndpoint);
  }

  final Uri _contentEndpoint;
  final OAuthAccessTokenProvider _tokens;
  final DateTime Function() _clock;

  @override
  Future<SyncPage<CloudEntry>> list(String path, {String? cursor}) async {
    final validCursor = validateCursor(cursor);
    final body = validCursor == null
        ? <String, Object?>{
            'path': root.slashPath(path),
            'recursive': false,
            'include_deleted': false,
            'limit': 200,
          }
        : <String, Object?>{'cursor': validCursor};
    final response = await _authorizedJson(
      validCursor == null ? 'files/list_folder' : 'files/list_folder/continue',
      body,
    );
    final payload = await _jsonSuccess(response);
    final rawEntries = payload['entries'];
    if (rawEntries is! List) invalidProviderResponse();
    final entries = rawEntries
        .map((value) {
          if (value is! Map<String, Object?>) invalidProviderResponse();
          return _entry(value);
        })
        .toList(growable: false);
    final hasMore = payload['has_more'] == true;
    final nextCursor = payload['cursor'];
    if (hasMore && nextCursor is! String) invalidProviderResponse();
    return SyncPage<CloudEntry>(
      items: entries,
      nextCursor: hasMore ? validateCursor(nextCursor as String) : null,
      serverTime: _clock(),
    );
  }

  @override
  Future<CloudEntry> stat(String path) async {
    final response = await _authorizedJson(
      'files/get_metadata',
      <String, Object?>{'path': root.slashPath(path), 'include_deleted': false},
    );
    return _entry(await _jsonSuccess(response));
  }

  @override
  Stream<List<int>> read(String path) async* {
    root.requireDescendant(path);
    final response = await _authorized(
      CloudHttpRequest(
        method: 'POST',
        uri: _contentUri('files/download'),
        headers: <String, String>{
          'dropbox-api-arg': jsonEncode(<String, Object?>{
            'path': root.slashPath(path),
          }),
        },
      ),
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
    root.requireDescendant(path);
    final body = await uploadBytes(bytes);
    final mode = etag == null
        ? 'add'
        : <String, Object?>{'.tag': 'update', 'update': etag};
    final response = await _authorized(
      CloudHttpRequest(
        method: 'POST',
        uri: _contentUri('files/upload'),
        headers: <String, String>{
          'content-type': 'application/octet-stream',
          'dropbox-api-arg': jsonEncode(<String, Object?>{
            'path': root.slashPath(path),
            'mode': mode,
            'autorename': false,
            'mute': true,
            'strict_conflict': true,
          }),
        },
        bodyFactory: () => repeatableBody(body),
        contentLength: body.length,
      ),
    );
    if (response.statusCode == 409) {
      await response.body.drain<void>();
      if (await _matchesExisting(path, body)) return stat(path);
      throw cloudStatusError(409);
    }
    return _entry(await _jsonSuccess(response));
  }

  @override
  Future<void> delete(String path, {String? etag}) async {
    root.requireDescendant(path);
    if (etag != null) {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'Dropbox does not support atomic conditional delete',
      );
    }
    final response = await _authorizedJson('files/delete_v2', <String, Object?>{
      'path': root.slashPath(path),
    });
    if (response.statusCode == 404) {
      await response.body.drain<void>();
      return;
    }
    if (response.statusCode == 409) {
      final payload = await decodeJsonObject(response);
      final summary = payload['error_summary'];
      if (summary is String && summary.contains('not_found')) return;
      throw cloudStatusError(409);
    }
    await expectStatus(response, const <int>{200});
    await response.body.drain<void>();
  }

  Future<CloudHttpResponse> _authorizedJson(
    String method,
    Map<String, Object?> body,
  ) {
    final bytes = utf8.encode(jsonEncode(body));
    return _authorized(
      CloudHttpRequest(
        method: 'POST',
        uri: endpointWithSegments(method.split('/')),
        headers: const <String, String>{'content-type': 'application/json'},
        bodyFactory: () => Stream<List<int>>.value(bytes),
        contentLength: bytes.length,
      ),
    );
  }

  Future<CloudHttpResponse> _authorized(CloudHttpRequest request) => send(
    request,
    authorizationHeaders: _tokens.authorizationHeaders,
    refreshAuthorization: () =>
        _tokens.refresh(cancellationToken: cancellationToken),
  );

  Future<Map<String, Object?>> _jsonSuccess(CloudHttpResponse response) async {
    await expectStatus(response, const <int>{200});
    return decodeJsonObject(response);
  }

  CloudEntry _entry(Map<String, Object?> value) {
    final tag = value['.tag'];
    final providerPath = value['path_display'] ?? value['path_lower'];
    if ((tag != 'file' && tag != 'folder') || providerPath is! String) {
      invalidProviderResponse();
    }
    final revision = value['rev'];
    if (tag == 'file' && revision is! String) invalidProviderResponse();
    final relative = root.relativeFromSegments(
      Uri(path: providerPath).pathSegments,
    );
    return CloudEntry(
      path: relative,
      isDirectory: tag == 'folder',
      etag: revision as String?,
    );
  }

  Uri _contentUri(String method) {
    final base = _contentEndpoint.pathSegments.where(
      (segment) => segment.isNotEmpty,
    );
    return _contentEndpoint.replace(
      pathSegments: <String>[...base, ...method.split('/')],
    );
  }

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
}
