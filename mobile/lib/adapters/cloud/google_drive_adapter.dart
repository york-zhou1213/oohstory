import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

import '../../core/errors.dart';
import '../../core/models.dart';
import 'cloud_support.dart';
import 'http_cloud_adapter.dart';
import 'secure_credentials.dart';

final class GoogleDriveCloudAdapter extends HttpCloudLibraryAdapter {
  // ignore: use_super_parameters
  GoogleDriveCloudAdapter({
    required String rootFolderId,
    required CloudHttpTransport transport,
    required SecureCredentialStore credentialStore,
    required CredentialScope credentialScope,
    Uri? apiEndpoint,
    Uri? uploadEndpoint,
    Uri? tokenEndpoint,
    CloudRuntimePolicy? runtimePolicy,
    super.retryPolicy,
    super.cancellationToken,
    super.logger,
    super.maxUploadBytes,
    DateTime Function()? clock,
  }) : _uploadEndpoint =
           uploadEndpoint ??
           Uri.https('www.googleapis.com', '/upload/drive/v3'),
       _clock = clock ?? (() => DateTime.now().toUtc()),
       rootFolderId = _validateRootFolderId(rootFolderId),
       _tokens = OAuthAccessTokenProvider(
         store: credentialStore,
         scope: credentialScope,
         tokenEndpoint:
             tokenEndpoint ?? Uri.https('oauth2.googleapis.com', '/token'),
         transport: transport,
         runtimePolicy: runtimePolicy ?? CloudRuntimePolicy.current(),
         retryPolicy: retryPolicy,
       ),
       super(
         providerId: 'google-drive',
         endpoint: apiEndpoint ?? Uri.https('www.googleapis.com', '/drive/v3'),
         root: 'OOHStory',
         transport: transport,
         runtimePolicy: runtimePolicy,
       ) {
    this.runtimePolicy.validate(_uploadEndpoint);
  }

  static const String _folderMimeType = 'application/vnd.google-apps.folder';

  final String rootFolderId;
  final Uri _uploadEndpoint;
  final OAuthAccessTokenProvider _tokens;
  final DateTime Function() _clock;

  @override
  Future<SyncPage<CloudEntry>> list(String path, {String? cursor}) async {
    final validCursor = validateCursor(cursor);
    final parent = await _resolveFolder(path);
    final query = <String, String>{
      'q': "'${_escapeQuery(parent.id)}' in parents and trashed = false",
      'fields': 'nextPageToken,files(id,name,mimeType,md5Checksum)',
      'pageSize': '200',
      if (validCursor != null) 'pageToken': validCursor,
    };
    final response = await _authorized(
      CloudHttpRequest(
        method: 'GET',
        uri: _apiUri(<String>['files'], query: query),
      ),
    );
    final payload = await _jsonSuccess(response);
    final rawFiles = payload['files'];
    if (rawFiles is! List) invalidProviderResponse();
    final parentPath = root.relativeFromSegments(root.resolve(path));
    final entries = rawFiles
        .map((value) {
          if (value is! Map<String, Object?>) invalidProviderResponse();
          final item = _DriveItem.fromJson(value, invalidProviderResponse);
          return CloudEntry(
            path: _childPath(parentPath, item.name),
            isDirectory: item.isDirectory,
          );
        })
        .toList(growable: false);
    final rawNextCursor = payload['nextPageToken'];
    if (rawNextCursor != null && rawNextCursor is! String) {
      invalidProviderResponse();
    }
    return SyncPage<CloudEntry>(
      items: entries,
      nextCursor: validateCursor(rawNextCursor as String?),
      serverTime: _clock(),
    );
  }

  @override
  Future<CloudEntry> stat(String path) async {
    final normalized = root.relativeFromSegments(root.resolve(path));
    if (normalized.isEmpty) {
      return CloudEntry(path: '', isDirectory: true);
    }
    final item = await _resolve(path);
    final response = await _authorized(
      CloudHttpRequest(
        method: 'GET',
        uri: _apiUri(
          <String>['files', item.id],
          query: const <String, String>{
            'fields': 'id,name,mimeType,md5Checksum',
          },
        ),
      ),
    );
    final payload = await _jsonSuccess(response);
    final fresh = _DriveItem.fromJson(payload, invalidProviderResponse);
    return CloudEntry(
      path: normalized,
      isDirectory: fresh.isDirectory,
      etag: response.header('etag'),
    );
  }

  @override
  Stream<List<int>> read(String path) async* {
    root.requireDescendant(path);
    final item = await _resolve(path);
    if (item.isDirectory) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Cannot read a Google Drive folder as a file',
      );
    }
    final response = await _authorized(
      CloudHttpRequest(
        method: 'GET',
        uri: _apiUri(
          <String>['files', item.id],
          query: const <String, String>{'alt': 'media'},
        ),
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
    final body = await uploadBytes(bytes);
    final parts = _splitFilePath(path);
    final parent = await _resolveFolder(parts.parent);
    final existing = await _findChild(parent.id, parts.name);
    if (existing != null) {
      if (etag == null) {
        if (existing.md5Checksum == md5.convert(body).toString()) {
          return stat(path);
        }
        throw cloudStatusError(409);
      }
      final response = await _authorized(
        CloudHttpRequest(
          method: 'PATCH',
          uri: _uploadUri(
            <String>['files', existing.id],
            query: const <String, String>{'uploadType': 'media'},
          ),
          headers: <String, String>{
            'content-type': 'application/octet-stream',
            'if-match': etag,
          },
          bodyFactory: () => repeatableBody(body),
          contentLength: body.length,
        ),
      );
      if (response.statusCode == 409 || response.statusCode == 412) {
        await response.body.drain<void>();
        final updated = await _getById(existing.id);
        if (updated?.md5Checksum == md5.convert(body).toString()) {
          return stat(path);
        }
        throw cloudStatusError(response.statusCode);
      }
      await expectStatus(response, const <int>{200});
      await response.body.drain<void>();
      return stat(path);
    }
    if (etag != null) throw cloudStatusError(409);
    final generatedId = await _generateId();
    final boundary = 'oohstory-${idempotencyKey('POST', path, body)}';
    final multipart = _multipartBody(
      boundary: boundary,
      metadata: <String, Object?>{
        'id': generatedId,
        'name': parts.name,
        'parents': <String>[parent.id],
      },
      bytes: body,
    );
    final response = await _authorized(
      CloudHttpRequest(
        method: 'POST',
        uri: _uploadUri(
          const <String>['files'],
          query: const <String, String>{'uploadType': 'multipart'},
        ),
        headers: <String, String>{
          'content-type': 'multipart/related; boundary=$boundary',
        },
        bodyFactory: () => Stream<List<int>>.fromIterable(multipart),
        contentLength: multipart.fold<int>(0, (sum, part) => sum + part.length),
      ),
    );
    if (response.statusCode == 409) {
      await response.body.drain<void>();
      final created = await _getById(generatedId);
      if (created?.md5Checksum == md5.convert(body).toString()) {
        return stat(path);
      }
      throw cloudStatusError(409);
    }
    await expectStatus(response, const <int>{200, 201});
    await response.body.drain<void>();
    return stat(path);
  }

  @override
  Future<void> delete(String path, {String? etag}) async {
    root.requireDescendant(path);
    _DriveItem item;
    try {
      item = await _resolve(path);
    } on CoreException catch (error) {
      if (error.code == CoreErrorCode.notFound) return;
      rethrow;
    }
    final response = await _authorized(
      CloudHttpRequest(
        method: 'DELETE',
        uri: _apiUri(<String>['files', item.id]),
        headers: <String, String>{if (etag != null) 'if-match': etag},
      ),
    );
    if (response.statusCode == 404) {
      await response.body.drain<void>();
      return;
    }
    await expectStatus(response, const <int>{200, 204});
    await response.body.drain<void>();
  }

  Future<_DriveItem> _resolveFolder(String path) async {
    final item = root.relativeFromSegments(root.resolve(path)).isEmpty
        ? _DriveItem(
            id: rootFolderId,
            name: 'OOHStory',
            mimeType: _folderMimeType,
          )
        : await _resolve(path);
    if (!item.isDirectory) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Google Drive path is not a folder',
      );
    }
    return item;
  }

  Future<_DriveItem> _resolve(String path) async {
    var current = _DriveItem(
      id: rootFolderId,
      name: 'OOHStory',
      mimeType: _folderMimeType,
    );
    final relative = root.relativeFromSegments(root.resolve(path));
    for (final segment
        in relative.split('/').where((value) => value.isNotEmpty)) {
      final child = await _findChild(current.id, segment);
      if (child == null) throw cloudStatusError(404);
      current = child;
    }
    return current;
  }

  Future<_DriveItem?> _findChild(String parentId, String name) async {
    final q =
        "'${_escapeQuery(parentId)}' in parents and "
        "name = '${_escapeQuery(name)}' and trashed = false";
    final response = await _authorized(
      CloudHttpRequest(
        method: 'GET',
        uri: _apiUri(
          const <String>['files'],
          query: <String, String>{
            'q': q,
            'fields': 'files(id,name,mimeType,md5Checksum)',
            'pageSize': '2',
          },
        ),
      ),
    );
    final payload = await _jsonSuccess(response);
    final files = payload['files'];
    if (files is! List) invalidProviderResponse();
    if (files.isEmpty) return null;
    if (files.length != 1 || files.single is! Map<String, Object?>) {
      throw const CoreException(
        CoreErrorCode.revisionConflict,
        'Google Drive path is ambiguous',
      );
    }
    return _DriveItem.fromJson(
      files.single as Map<String, Object?>,
      invalidProviderResponse,
    );
  }

  Future<_DriveItem?> _getById(String id) async {
    final response = await _authorized(
      CloudHttpRequest(
        method: 'GET',
        uri: _apiUri(
          <String>['files', id],
          query: const <String, String>{
            'fields': 'id,name,mimeType,md5Checksum',
          },
        ),
      ),
    );
    if (response.statusCode == 404) {
      await response.body.drain<void>();
      return null;
    }
    return _DriveItem.fromJson(
      await _jsonSuccess(response),
      invalidProviderResponse,
    );
  }

  Future<String> _generateId() async {
    final response = await _authorized(
      CloudHttpRequest(
        method: 'GET',
        uri: _apiUri(
          const <String>['files', 'generateIds'],
          query: const {'count': '1', 'space': 'drive', 'type': 'files'},
        ),
      ),
    );
    final payload = await _jsonSuccess(response);
    final ids = payload['ids'];
    if (ids is! List || ids.length != 1 || ids.single is! String) {
      invalidProviderResponse();
    }
    return ids.single as String;
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

  Uri _apiUri(Iterable<String> segments, {Map<String, String>? query}) =>
      endpointWithSegments(segments, queryParameters: query);

  Uri _uploadUri(Iterable<String> segments, {Map<String, String>? query}) {
    final base = _uploadEndpoint.pathSegments.where(
      (segment) => segment.isNotEmpty,
    );
    return _uploadEndpoint.replace(
      pathSegments: <String>[...base, ...segments],
      queryParameters: query,
    );
  }

  String _childPath(String parent, String name) {
    return root.relativeFromSegments(<String>[
      ...root.segments,
      ...parent.split('/').where((segment) => segment.isNotEmpty),
      name,
    ]);
  }

  _FilePath _splitFilePath(String path) {
    final relative = root.relativeFromSegments(root.resolve(path));
    final segments = relative
        .split('/')
        .where((value) => value.isNotEmpty)
        .toList();
    if (segments.isEmpty) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Cloud file path must not be empty',
      );
    }
    return _FilePath(
      parent: segments.take(segments.length - 1).join('/'),
      name: segments.last,
    );
  }

  List<List<int>> _multipartBody({
    required String boundary,
    required Map<String, Object?> metadata,
    required Uint8List bytes,
  }) {
    return <List<int>>[
      utf8.encode(
        '--$boundary\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'
        '${jsonEncode(metadata)}\r\n--$boundary\r\n'
        'Content-Type: application/octet-stream\r\n\r\n',
      ),
      bytes,
      utf8.encode('\r\n--$boundary--\r\n'),
    ];
  }

  String _escapeQuery(String value) =>
      value.replaceAll('\\', '\\\\').replaceAll("'", "\\'");

  static String _validateRootFolderId(String value) {
    if (!RegExp(r'^[A-Za-z0-9_-]{1,256}$').hasMatch(value)) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Google Drive root folder ID is invalid',
      );
    }
    return value;
  }
}

final class _DriveItem {
  const _DriveItem({
    required this.id,
    required this.name,
    required this.mimeType,
    this.md5Checksum,
  });

  final String id;
  final String name;
  final String mimeType;
  final String? md5Checksum;

  bool get isDirectory => mimeType == GoogleDriveCloudAdapter._folderMimeType;

  factory _DriveItem.fromJson(
    Map<String, Object?> value,
    Never Function() invalid,
  ) {
    final id = value['id'];
    final name = value['name'];
    final mimeType = value['mimeType'];
    if (id is! String || name is! String || mimeType is! String) invalid();
    return _DriveItem(
      id: id,
      name: name,
      mimeType: mimeType,
      md5Checksum: value['md5Checksum'] as String?,
    );
  }
}

final class _FilePath {
  const _FilePath({required this.parent, required this.name});
  final String parent;
  final String name;
}
