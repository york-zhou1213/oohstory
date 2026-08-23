import 'dart:convert';

import 'package:crypto/crypto.dart';

import '../../core/errors.dart';
import '../../core/models.dart';
import 'cloud_support.dart';
import 'http_cloud_adapter.dart';
import 'safe_xml.dart';
import 'secure_credentials.dart';

final class WebDavCloudAdapter extends HttpCloudLibraryAdapter {
  // ignore: use_super_parameters
  WebDavCloudAdapter({
    required Uri endpoint,
    required String root,
    required CloudHttpTransport transport,
    required SecureCredentialStore credentialStore,
    required CredentialScope credentialScope,
    CloudRuntimePolicy? runtimePolicy,
    super.retryPolicy,
    super.cancellationToken,
    super.logger,
    super.maxUploadBytes,
    this.pageSize = 200,
  }) : _credentials = BasicCredentialProvider(
         store: credentialStore,
         scope: credentialScope,
       ),
       super(
         providerId: 'webdav',
         endpoint: endpoint,
         root: root,
         transport: transport,
         runtimePolicy: runtimePolicy,
       ) {
    if (pageSize <= 0) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'WebDAV page size must be positive',
      );
    }
  }

  final BasicCredentialProvider _credentials;
  final int pageSize;

  @override
  Future<SyncPage<CloudEntry>> list(String path, {String? cursor}) async {
    final parsedCursor = _parseCursor(cursor);
    final start = parsedCursor.offset;
    final entries = await _propfind(path, depth: 1);
    final normalizedPath = root.relativeFromSegments(root.resolve(path));
    entries.removeWhere((entry) => entry.path == normalizedPath);
    entries.sort((left, right) => left.path.compareTo(right.path));
    final fingerprint = sha256
        .convert(
          utf8.encode(
            entries
                .map((entry) => '${entry.path}\u0000${entry.isDirectory}')
                .join('\u0001'),
          ),
        )
        .toString();
    if (parsedCursor.fingerprint != null &&
        parsedCursor.fingerprint != fingerprint) {
      throw const CoreException(
        CoreErrorCode.revisionConflict,
        'WebDAV directory changed while paging',
      );
    }
    if (start > entries.length) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'WebDAV cursor is outside the result set',
      );
    }
    final end = (start + pageSize).clamp(0, entries.length);
    return SyncPage<CloudEntry>(
      items: entries.sublist(start, end),
      nextCursor: end < entries.length
          ? base64UrlEncode(utf8.encode('$end:$fingerprint'))
          : null,
      serverTime: DateTime.now().toUtc(),
    );
  }

  @override
  Future<CloudEntry> stat(String path) async {
    final entries = await _propfind(path, depth: 0);
    if (entries.isEmpty) throw cloudStatusError(404);
    return entries.first;
  }

  @override
  Stream<List<int>> read(String path) async* {
    root.requireDescendant(path);
    final response = await _authorized(
      CloudHttpRequest(method: 'GET', uri: _uri(path)),
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
    final response = await _authorized(
      CloudHttpRequest(
        method: 'PUT',
        uri: _uri(path),
        headers: <String, String>{
          'content-type': 'application/octet-stream',
          if (etag == null) 'if-none-match': '*' else 'if-match': etag,
        },
        bodyFactory: () => repeatableBody(body),
        contentLength: body.length,
      ),
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
    root.requireDescendant(path);
    final response = await _authorized(
      CloudHttpRequest(
        method: 'DELETE',
        uri: _uri(path),
        headers: <String, String>{if (etag != null) 'if-match': etag},
      ),
    );
    if (response.statusCode == 404) {
      await response.body.drain<void>();
      return;
    }
    await expectStatus(response, const <int>{200, 202, 204});
    await response.body.drain<void>();
  }

  Uri _uri(String path) => endpointWithSegments(root.resolve(path));

  Future<CloudHttpResponse> _authorized(CloudHttpRequest request) =>
      send(request, authorizationHeaders: _credentials.authorizationHeaders);

  Future<List<CloudEntry>> _propfind(String path, {required int depth}) async {
    const body = '''<?xml version="1.0" encoding="utf-8" ?>
<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:getetag/></d:prop></d:propfind>''';
    final encodedBody = utf8.encode(body);
    final response = await _authorized(
      CloudHttpRequest(
        method: 'PROPFIND',
        uri: _uri(path),
        headers: <String, String>{
          'content-type': 'application/xml; charset=utf-8',
          'depth': '$depth',
        },
        bodyFactory: () => Stream<List<int>>.value(encodedBody),
        contentLength: encodedBody.length,
      ),
    );
    await expectStatus(response, const <int>{207});
    final bytes = await collectResponseBytes(response);
    try {
      final document = utf8.decode(bytes);
      return xmlElements(document, 'response')
          .where(_successfulResponse)
          .map(_entryFromResponse)
          .toList(growable: true);
    } on CoreException {
      rethrow;
    } on Object {
      invalidProviderResponse();
    }
  }

  CloudEntry _entryFromResponse(String response) {
    final href = xmlText(response, 'href');
    if (href == null) invalidProviderResponse();
    final uri = Uri.parse(href);
    final providerSegments = uri.pathSegments;
    final base = endpoint.pathSegments.where((segment) => segment.isNotEmpty);
    if (providerSegments.length < base.length) invalidProviderResponse();
    for (var index = 0; index < base.length; index++) {
      if (providerSegments[index] != base.elementAt(index)) {
        throw const CoreException(
          CoreErrorCode.forbidden,
          'WebDAV response escaped the configured endpoint',
        );
      }
    }
    final relative = root.relativeFromSegments(
      providerSegments.skip(base.length),
    );
    final isDirectory = hasXmlElement(response, 'collection');
    return CloudEntry(
      path: relative,
      isDirectory: isDirectory,
      etag: xmlText(response, 'getetag'),
    );
  }

  bool _successfulResponse(String response) {
    final statuses = xmlElements(response, 'status');
    return statuses.isEmpty ||
        statuses.any((status) => RegExp(r'\s2\d\d(?:\s|$)').hasMatch(status));
  }

  Future<bool> _matchesExisting(String path, List<int> expected) async {
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

  _WebDavCursor _parseCursor(String? cursor) {
    if (cursor == null) return const _WebDavCursor(0, null);
    if (cursor.length > 256) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'WebDAV cursor is invalid',
      );
    }
    try {
      final decoded = utf8.decode(base64Url.decode(cursor));
      final separator = decoded.indexOf(':');
      if (separator <= 0) throw const FormatException();
      final offset = int.parse(decoded.substring(0, separator));
      final fingerprint = decoded.substring(separator + 1);
      if (offset < 0 || !RegExp(r'^[0-9a-f]{64}$').hasMatch(fingerprint)) {
        throw const FormatException();
      }
      return _WebDavCursor(offset, fingerprint);
    } on Object {
      throw const CoreException(
        CoreErrorCode.validationError,
        'WebDAV cursor is invalid',
      );
    }
  }
}

final class _WebDavCursor {
  const _WebDavCursor(this.offset, this.fingerprint);
  final int offset;
  final String? fingerprint;
}
