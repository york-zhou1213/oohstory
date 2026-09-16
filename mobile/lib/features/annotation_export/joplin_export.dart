import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../adapters/cloud/cloud.dart';
import '../../adapters/contracts/adapter_contracts.dart';
import '../../core/core.dart';
import 'annotation_documents.dart';
import 'annotation_markdown.dart';

final class JoplinExportConfiguration {
  JoplinExportConfiguration({required this.port, required String notebookId})
    : notebookId = normalizeJoplinId(notebookId) {
    _joplinLoopbackEndpoint(port);
  }

  final int port;
  final String notebookId;

  Uri get endpoint => Uri(scheme: 'http', host: '127.0.0.1', port: port);

  String get targetKey => sha256
      .convert(utf8.encode('joplin-desktop-v1\u0000$notebookId'))
      .toString();

  factory JoplinExportConfiguration.fromJson(Map<String, Object?> json) =>
      JoplinExportConfiguration(
        port: json['port'] as int,
        notebookId: json['notebook_id'] as String,
      );

  Map<String, Object?> toJson() => <String, Object?>{
    'port': port,
    'notebook_id': notebookId,
  };
}

Uri _joplinLoopbackEndpoint(int port) {
  if (port < 1024 || port > 65535) {
    throw const FormatException('Joplin 端口必须在 1024 到 65535 之间');
  }
  return Uri(scheme: 'http', host: '127.0.0.1', port: port);
}

const joplinDiscoveryPorts = <int>[
  41184,
  41185,
  41186,
  41187,
  41188,
  41189,
  41190,
  41191,
  41192,
  41193,
  41194,
];

Future<int?> discoverJoplinPort({
  required CloudHttpTransport transport,
  Iterable<int> ports = joplinDiscoveryPorts,
  Duration requestTimeout = const Duration(milliseconds: 750),
}) async {
  final candidates = ports.toSet().toList(growable: false);
  if (candidates.isEmpty || candidates.length > joplinDiscoveryPorts.length) {
    throw ArgumentError.value(ports, 'ports', 'must contain 1 to 11 ports');
  }
  for (final port in candidates) {
    _joplinLoopbackEndpoint(port);
    final client = JoplinApiClient.connection(
      port: port,
      transport: transport,
      token: () async =>
          throw StateError('Joplin probe must not request token'),
      retryPolicy: const RetryPolicy(maxAttempts: 1),
      requestTimeout: requestTimeout,
    );
    try {
      await client.probe();
      return port;
    } on CoreException {
      // A closed port, timeout, non-Joplin response, or HTTP error means this
      // candidate is not the local Clipper service. Continue the bounded scan.
    } on FormatException {
      // Malformed text from another local service is also not Joplin.
    }
  }
  return null;
}

final class JoplinNotebook {
  const JoplinNotebook({
    required this.id,
    required this.title,
    required this.parentId,
    required this.path,
  });

  final String id;
  final String title;
  final String parentId;
  final String path;
}

String normalizeJoplinId(String value) {
  final normalized = value.trim().toLowerCase();
  if (!RegExp(r'^[0-9a-f]{32}$').hasMatch(normalized)) {
    throw const FormatException('Joplin 笔记本 ID 必须是 32 位十六进制字符');
  }
  return normalized;
}

String deterministicJoplinId(String namespace, String value) => sha256
    .convert(utf8.encode('$namespace\u0000$value'))
    .toString()
    .substring(0, 32);

final class JoplinConnectionRepository {
  JoplinConnectionRepository({
    required SharedPreferences preferences,
    required SecureCredentialStore credentialStore,
  }) : _preferences = preferences,
       _credentialStore = credentialStore;

  static const _configurationKey = 'oohstory_joplin_connection_v1';
  static const _tokenKey = 'oohstory.integration.joplin.data_api_token';
  final SharedPreferences _preferences;
  final SecureCredentialStore _credentialStore;

  JoplinExportConfiguration? loadConfiguration() {
    final raw = _preferences.getString(_configurationKey);
    if (raw == null) return null;
    try {
      return JoplinExportConfiguration.fromJson(
        Map<String, Object?>.from(jsonDecode(raw) as Map),
      );
    } on Object {
      return null;
    }
  }

  Future<bool> hasToken() async =>
      (await _credentialStore.read(_tokenKey))?.isNotEmpty ?? false;

  Future<String> requireToken() async {
    final token = await _credentialStore.read(_tokenKey);
    if (token == null || token.isEmpty) {
      throw const CoreException(
        CoreErrorCode.unauthorized,
        'Joplin Data API 令牌不可用',
      );
    }
    return validateJoplinToken(token);
  }

  Future<void> save(
    JoplinExportConfiguration configuration, {
    String? token,
  }) async {
    final candidate = token?.trim();
    final previous = candidate == null || candidate.isEmpty
        ? null
        : await _credentialStore.read(_tokenKey);
    if (candidate != null && candidate.isNotEmpty) {
      await _credentialStore.write(_tokenKey, validateJoplinToken(candidate));
    } else if (!await hasToken()) {
      throw const FormatException('请输入 Joplin Data API 令牌');
    }
    try {
      await _preferences.setString(
        _configurationKey,
        jsonEncode(configuration.toJson()),
      );
    } on Object {
      if (candidate != null && candidate.isNotEmpty) {
        if (previous == null) {
          await _credentialStore.delete(_tokenKey);
        } else {
          await _credentialStore.write(_tokenKey, previous);
        }
      }
      rethrow;
    }
  }

  Future<void> disconnect() async {
    await _credentialStore.delete(_tokenKey);
    await _preferences.remove(_configurationKey);
  }
}

String validateJoplinToken(String value) {
  if (value.length < 16 ||
      value.length > 4096 ||
      value.runes.any((rune) => rune < 0x20 || rune == 0x7f)) {
    throw const FormatException('Joplin Data API 令牌格式无效');
  }
  return value;
}

final class JoplinResourceExportState {
  JoplinResourceExportState({
    required this.attachmentId,
    required String resourceId,
    required this.localFingerprint,
    required this.remoteFingerprint,
  }) : resourceId = normalizeJoplinId(resourceId) {
    if (attachmentId.trim().isEmpty) {
      throw const FormatException('Joplin 附件导出状态标识无效');
    }
    for (final hash in <String>[localFingerprint, remoteFingerprint]) {
      if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(hash)) {
        throw const FormatException('Joplin 附件导出状态哈希无效');
      }
    }
  }

  final String attachmentId;
  final String resourceId;
  final String localFingerprint;
  final String remoteFingerprint;

  factory JoplinResourceExportState.fromJson(Map<String, Object?> json) =>
      JoplinResourceExportState(
        attachmentId: json['attachment_id'] as String,
        resourceId: json['resource_id'] as String,
        localFingerprint: json['local_fingerprint'] as String,
        remoteFingerprint: json['remote_fingerprint'] as String,
      );

  Map<String, Object?> toJson() => <String, Object?>{
    'attachment_id': attachmentId,
    'resource_id': resourceId,
    'local_fingerprint': localFingerprint,
    'remote_fingerprint': remoteFingerprint,
  };
}

final class JoplinExportState {
  JoplinExportState({
    required this.targetKey,
    required this.documentId,
    required String noteId,
    required this.localContentHash,
    required this.remoteFingerprint,
    this.resources = const <JoplinResourceExportState>[],
  }) : noteId = normalizeJoplinId(noteId) {
    if (targetKey.isEmpty || documentId.trim().isEmpty) {
      throw const FormatException('Joplin 导出状态标识无效');
    }
    for (final hash in <String>[localContentHash, remoteFingerprint]) {
      if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(hash)) {
        throw const FormatException('Joplin 导出状态哈希无效');
      }
    }
  }

  final String targetKey;
  final String documentId;
  final String noteId;
  final String localContentHash;
  final String remoteFingerprint;
  final List<JoplinResourceExportState> resources;

  factory JoplinExportState.fromJson(Map<String, Object?> json) =>
      JoplinExportState(
        targetKey: json['target_key'] as String,
        documentId: json['document_id'] as String,
        noteId: json['note_id'] as String,
        localContentHash: json['local_content_hash'] as String,
        remoteFingerprint: json['remote_fingerprint'] as String,
        resources: (json['resources'] as List? ?? const <Object?>[])
            .whereType<Map>()
            .map(
              (item) => JoplinResourceExportState.fromJson(
                Map<String, Object?>.from(item),
              ),
            )
            .toList(growable: false),
      );

  Map<String, Object?> toJson() => <String, Object?>{
    'target_key': targetKey,
    'document_id': documentId,
    'note_id': noteId,
    'local_content_hash': localContentHash,
    'remote_fingerprint': remoteFingerprint,
    'resources': resources.map((item) => item.toJson()).toList(growable: false),
  };
}

abstract interface class JoplinExportStateStore {
  Future<JoplinExportState?> read({
    required String targetKey,
    required String documentId,
  });
  Future<void> write(JoplinExportState state);
  Future<void> removeTarget(String targetKey);
}

final class SharedPreferencesJoplinExportStateStore
    implements JoplinExportStateStore {
  SharedPreferencesJoplinExportStateStore(this._preferences);

  static const _storageKey = 'oohstory_joplin_export_states_v1';
  final SharedPreferences _preferences;

  @override
  Future<JoplinExportState?> read({
    required String targetKey,
    required String documentId,
  }) async {
    final raw = _readAll()[_stateKey(targetKey, documentId)];
    if (raw is! Map) return null;
    try {
      final state = JoplinExportState.fromJson(Map<String, Object?>.from(raw));
      if (state.targetKey != targetKey || state.documentId != documentId) {
        return null;
      }
      return state;
    } on Object {
      return null;
    }
  }

  @override
  Future<void> write(JoplinExportState state) async {
    final states = _readAll();
    states[_stateKey(state.targetKey, state.documentId)] = state.toJson();
    await _preferences.setString(_storageKey, jsonEncode(states));
  }

  @override
  Future<void> removeTarget(String targetKey) async {
    final states = _readAll()
      ..removeWhere(
        (_, value) =>
            value is Map && value['target_key']?.toString() == targetKey,
      );
    if (states.isEmpty) {
      await _preferences.remove(_storageKey);
    } else {
      await _preferences.setString(_storageKey, jsonEncode(states));
    }
  }

  Map<String, Object?> _readAll() {
    final raw = _preferences.getString(_storageKey);
    if (raw == null) return <String, Object?>{};
    try {
      return Map<String, Object?>.from(jsonDecode(raw) as Map);
    } on Object {
      return <String, Object?>{};
    }
  }

  String _stateKey(String targetKey, String documentId) =>
      sha256.convert(utf8.encode('$targetKey\u0000$documentId')).toString();
}

final class JoplinRemoteNote {
  JoplinRemoteNote({
    required String id,
    required String parentId,
    required this.title,
    required this.body,
    required this.applicationData,
    required this.source,
  }) : id = normalizeJoplinId(id),
       parentId = normalizeJoplinId(parentId);

  final String id;
  final String parentId;
  final String title;
  final String body;
  final String applicationData;
  final String source;

  String get contentFingerprint => sha256
      .convert(
        utf8.encode(
          jsonEncode(<String, String>{
            'parent_id': parentId,
            'title': title,
            'body': body,
            'source': source,
          }),
        ),
      )
      .toString();

  String get fingerprint => sha256
      .convert(
        utf8.encode(
          jsonEncode(<String, String>{
            'parent_id': parentId,
            'title': title,
            'body': body,
            'application_data': applicationData,
            'source': source,
          }),
        ),
      )
      .toString();
}

final class JoplinRemoteResource {
  JoplinRemoteResource({
    required String id,
    required this.title,
    required this.mediaType,
    required this.fileName,
    required this.size,
    required this.userData,
    required this.contentHash,
  }) : id = normalizeJoplinId(id) {
    if (size < 0 || !RegExp(r'^[0-9a-f]{64}$').hasMatch(contentHash)) {
      throw const FormatException('Joplin 附件响应无效');
    }
  }

  final String id;
  final String title;
  final String mediaType;
  final String fileName;
  final int size;
  final String userData;
  final String contentHash;

  String get fingerprint => sha256
      .convert(
        utf8.encode(
          jsonEncode(<String, Object?>{
            'id': id,
            'title': title,
            'mime': mediaType,
            'filename': fileName,
            'size': size,
            'user_data': userData,
            'content_sha256': contentHash,
          }),
        ),
      )
      .toString();
}

final class _JoplinResourcePayload {
  const _JoplinResourcePayload({
    required this.title,
    required this.mediaType,
    required this.fileName,
    required this.userData,
    required this.bytes,
  });

  final String title;
  final String mediaType;
  final String fileName;
  final String userData;
  final Uint8List bytes;

  String get contentHash => sha256.convert(bytes).toString();

  String get localFingerprint => sha256
      .convert(
        utf8.encode(
          jsonEncode(<String, Object?>{
            'title': title,
            'mime': mediaType,
            'filename': fileName,
            'user_data': userData,
            'content_sha256': contentHash,
          }),
        ),
      )
      .toString();

  JoplinRemoteResource expected(String resourceId) => JoplinRemoteResource(
    id: resourceId,
    title: title,
    mediaType: mediaType,
    fileName: fileName,
    size: bytes.length,
    userData: userData,
    contentHash: contentHash,
  );
}

final class _JoplinResourcePlan {
  const _JoplinResourcePlan({
    required this.attachmentId,
    required this.annotationId,
    required this.fileName,
    required this.isImage,
    required this.resourceId,
    required this.payload,
  });

  final String attachmentId;
  final String annotationId;
  final String fileName;
  final bool isImage;
  final String resourceId;
  final _JoplinResourcePayload payload;
}

final class _JoplinResourceSyncResult {
  const _JoplinResourceSyncResult({
    required this.states,
    required this.changed,
    required this.overwritten,
  });

  final List<JoplinResourceExportState> states;
  final bool changed;
  final bool overwritten;
}

enum _JoplinResourceAction { none, create, update }

final class _JoplinPreparedResource {
  _JoplinPreparedResource({
    required this.plan,
    required this.resourceId,
    required this.remote,
    required this.action,
    required this.overwritten,
  });

  final _JoplinResourcePlan plan;
  String? resourceId;
  final JoplinRemoteResource? remote;
  final _JoplinResourceAction action;
  final bool overwritten;
}

final class JoplinApiClient {
  JoplinApiClient({
    required JoplinExportConfiguration configuration,
    required CloudHttpTransport transport,
    required Future<String> Function() token,
    RetryPolicy retryPolicy = const RetryPolicy(),
    Future<void> Function(Duration delay)? sleep,
    Duration requestTimeout = const Duration(seconds: 10),
  }) : this._(
         endpoint: configuration.endpoint,
         notebookId: configuration.notebookId,
         transport: transport,
         token: token,
         retryPolicy: retryPolicy,
         sleep: sleep,
         requestTimeout: requestTimeout,
       );

  JoplinApiClient.connection({
    required int port,
    required CloudHttpTransport transport,
    required Future<String> Function() token,
    RetryPolicy retryPolicy = const RetryPolicy(),
    Future<void> Function(Duration delay)? sleep,
    Duration requestTimeout = const Duration(seconds: 10),
  }) : this._(
         endpoint: _joplinLoopbackEndpoint(port),
         notebookId: null,
         transport: transport,
         token: token,
         retryPolicy: retryPolicy,
         sleep: sleep,
         requestTimeout: requestTimeout,
       );

  JoplinApiClient._({
    required Uri endpoint,
    required String? notebookId,
    required CloudHttpTransport transport,
    required Future<String> Function() token,
    required RetryPolicy retryPolicy,
    required Future<void> Function(Duration delay)? sleep,
    required Duration requestTimeout,
  }) : _endpoint = endpoint,
       _notebookId = notebookId,
       _transport = transport,
       _token = token,
       _retryPolicy = retryPolicy,
       _sleep = sleep,
       _requestTimeout = requestTimeout {
    if (requestTimeout <= Duration.zero) {
      throw ArgumentError.value(requestTimeout, 'requestTimeout');
    }
  }

  static const _maxBodyBytes = 1024 * 1024;
  static const maxResourceBytes = 16 * 1024 * 1024;
  static const tagTitles = <String>['oohstory', 'reading-notes'];

  final Uri _endpoint;
  final String? _notebookId;
  final CloudHttpTransport _transport;
  final Future<String> Function() _token;
  final RetryPolicy _retryPolicy;
  final Future<void> Function(Duration delay)? _sleep;
  final Duration _requestTimeout;

  Future<void> probe() async {
    final response = await _send(
      'GET',
      '/ping',
      authenticated: false,
      safeToRetry: false,
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      await response.body.drain<void>();
      throw cloudStatusError(response.statusCode);
    }
    final bytes = await collectResponseBytes(response, maxBytes: 64);
    if (utf8.decode(bytes).trim() != 'JoplinClipperServer') {
      throw const CoreException(
        CoreErrorCode.upstreamError,
        '端口上的服务不是 Joplin Web Clipper',
      );
    }
  }

  Future<List<JoplinNotebook>> listNotebooks() async {
    final notebooksById = <String, ({String title, String parentId})>{};
    for (var page = 1; page <= 20; page++) {
      final payload = await _requestObject(
        'GET',
        '/folders',
        query: <String, String>{
          'fields': 'id,parent_id,title',
          'limit': '100',
          'page': '$page',
        },
      );
      final items = payload['items'];
      if (items is! List) _invalidResponse();
      if (items.length > 100) _invalidResponse();
      for (final item in items) {
        if (item is! Map) _invalidResponse();
        final raw = Map<String, Object?>.from(item);
        final rawId = raw['id'];
        final rawParentId = raw['parent_id'];
        final rawTitle = raw['title'];
        if (rawId is! String || rawParentId is! String || rawTitle is! String) {
          _invalidResponse();
        }
        final id = normalizeJoplinId(rawId);
        final parentId = rawParentId.isEmpty
            ? ''
            : normalizeJoplinId(rawParentId);
        final normalizedTitle = AnnotationMarkdownRenderer.singleLine(rawTitle);
        final title = String.fromCharCodes(
          (normalizedTitle.isEmpty ? '未命名笔记本' : normalizedTitle).runes.take(
            255,
          ),
        );
        if (notebooksById.containsKey(id)) _invalidResponse();
        notebooksById[id] = (title: title, parentId: parentId);
        if (notebooksById.length > 2000) {
          throw const CoreException(
            CoreErrorCode.payloadTooLarge,
            'Joplin 笔记本数量超过本地读取上限',
          );
        }
      }
      final hasMore = payload['has_more'];
      if (hasMore != null && hasMore is! bool) _invalidResponse();
      if (hasMore != true) {
        return _buildNotebookPaths(notebooksById);
      }
    }
    throw const CoreException(
      CoreErrorCode.payloadTooLarge,
      'Joplin 笔记本数量超过本地读取上限',
    );
  }

  Future<String> verifyNotebook([String? notebookId]) async {
    final expectedId = normalizeJoplinId(notebookId ?? _requireNotebookId());
    final payload = await _requestObject(
      'GET',
      '/folders/$expectedId',
      query: const <String, String>{'fields': 'id,title'},
    );
    final id = payload['id'];
    final title = payload['title'];
    if (id is! String ||
        normalizeJoplinId(id) != expectedId ||
        title is! String) {
      _invalidResponse();
    }
    return title;
  }

  String _requireNotebookId() {
    final notebookId = _notebookId;
    if (notebookId == null) {
      throw StateError('Joplin notebook is required for export operations');
    }
    return notebookId;
  }

  static List<JoplinNotebook> _buildNotebookPaths(
    Map<String, ({String title, String parentId})> notebooksById,
  ) {
    final paths = <String, String>{};
    String resolve(String id, Set<String> visiting) {
      final cached = paths[id];
      if (cached != null) return cached;
      final notebook = notebooksById[id];
      if (notebook == null || !visiting.add(id)) _invalidResponse();
      if (visiting.length > 100) {
        throw const CoreException(
          CoreErrorCode.payloadTooLarge,
          'Joplin 笔记本层级超过本地读取上限',
        );
      }
      final parentPath = notebook.parentId.isEmpty
          ? null
          : resolve(notebook.parentId, visiting);
      visiting.remove(id);
      final path = parentPath == null
          ? notebook.title
          : '$parentPath / ${notebook.title}';
      paths[id] = path;
      return path;
    }

    final notebooks = notebooksById.entries
        .map(
          (entry) => JoplinNotebook(
            id: entry.key,
            title: entry.value.title,
            parentId: entry.value.parentId,
            path: resolve(entry.key, <String>{}),
          ),
        )
        .toList();
    notebooks.sort((left, right) {
      final byPath = left.path.toLowerCase().compareTo(
        right.path.toLowerCase(),
      );
      return byPath == 0 ? left.id.compareTo(right.id) : byPath;
    });
    return List<JoplinNotebook>.unmodifiable(notebooks);
  }

  Future<JoplinRemoteNote?> readNote(String noteId) async {
    final response = await _send(
      'GET',
      '/notes/${normalizeJoplinId(noteId)}',
      query: const <String, String>{
        'fields': 'id,parent_id,title,body,application_data,source',
      },
    );
    if (response.statusCode == 404) {
      await response.body.drain<void>();
      return null;
    }
    final payload = await _decodeSuccess(response);
    return _noteFrom(payload);
  }

  Future<void> createNote({
    required String noteId,
    required String title,
    required String body,
    required String applicationData,
  }) async {
    final normalizedNoteId = normalizeJoplinId(noteId);
    final payload = await _requestObject(
      'POST',
      '/notes',
      body: <String, Object?>{
        'id': normalizedNoteId,
        'parent_id': _requireNotebookId(),
        'title': _boundedTitle(title),
        'body': _validatedBody(body),
        'application_data': applicationData,
        'source': 'oohstory',
        'source_application': 'OOHStory',
      },
      safeToRetry: false,
    );
    final returnedId = payload['id'];
    if (returnedId is! String ||
        normalizeJoplinId(returnedId) != normalizedNoteId) {
      _invalidResponse();
    }
    await updateNote(
      noteId: normalizedNoteId,
      title: title,
      body: body,
      applicationData: applicationData,
    );
  }

  Future<void> updateNote({
    required String noteId,
    required String title,
    required String body,
    required String applicationData,
  }) async {
    await _requestObject(
      'PUT',
      '/notes/${normalizeJoplinId(noteId)}',
      body: <String, Object?>{
        'parent_id': _requireNotebookId(),
        'title': _boundedTitle(title),
        'body': _validatedBody(body),
        'application_data': applicationData,
      },
    );
  }

  Future<JoplinRemoteResource?> readResource(String resourceId) async {
    final normalizedId = normalizeJoplinId(resourceId);
    final response = await _send(
      'GET',
      '/resources/$normalizedId',
      query: const <String, String>{
        'fields': 'id,title,mime,filename,size,user_data',
      },
    );
    if (response.statusCode == 404) {
      await response.body.drain<void>();
      return null;
    }
    final metadata = await _decodeSuccess(response);
    final id = metadata['id'];
    final title = metadata['title'];
    final mediaType = metadata['mime'];
    final fileName = metadata['filename'];
    final size = metadata['size'];
    final userData = metadata['user_data'];
    if (id is! String ||
        normalizeJoplinId(id) != normalizedId ||
        title is! String ||
        mediaType is! String ||
        fileName is! String ||
        size is! int ||
        size < 0 ||
        size > maxResourceBytes ||
        userData is! String) {
      _invalidResponse();
    }
    final fileResponse = await _send('GET', '/resources/$normalizedId/file');
    if (fileResponse.statusCode < 200 || fileResponse.statusCode >= 300) {
      await fileResponse.body.drain<void>();
      throw cloudStatusError(fileResponse.statusCode);
    }
    final bytes = await collectResponseBytes(
      fileResponse,
      maxBytes: maxResourceBytes,
    );
    if (bytes.length != size) _invalidResponse();
    return JoplinRemoteResource(
      id: normalizedId,
      title: title,
      mediaType: mediaType,
      fileName: fileName,
      size: size,
      userData: userData,
      contentHash: sha256.convert(bytes).toString(),
    );
  }

  Future<Map<String, List<String>>> _findResourceIdsByUserData(
    Iterable<String> userDataValues,
  ) async {
    final desired = userDataValues.toSet();
    final matches = <String, List<String>>{
      for (final value in desired) value: <String>[],
    };
    if (desired.isEmpty) return matches;
    for (var page = 1; page <= 20; page++) {
      final payload = await _requestObject(
        'GET',
        '/resources',
        query: <String, String>{
          'fields': 'id,user_data',
          'limit': '100',
          'page': '$page',
        },
      );
      final items = payload['items'];
      if (items is! List) _invalidResponse();
      for (final item in items) {
        if (item is! Map || item['id'] is! String) _invalidResponse();
        final id = normalizeJoplinId(item['id'] as String);
        final remoteUserData = item['user_data'];
        if (remoteUserData is! String) _invalidResponse();
        matches[remoteUserData]?.add(id);
      }
      if (payload['has_more'] != true) return matches;
    }
    throw const CoreException(
      CoreErrorCode.payloadTooLarge,
      'Joplin 附件数量超过本地来源检查上限',
    );
  }

  Future<String> _createResource(
    String resourceId,
    _JoplinResourcePayload resource,
  ) => _writeResource(
    'POST',
    '/resources',
    resource,
    safeToRetry: false,
    expectedId: normalizeJoplinId(resourceId),
  );

  Future<String> _updateResource(
    String resourceId,
    _JoplinResourcePayload resource,
  ) => _writeResource(
    'PUT',
    '/resources/${normalizeJoplinId(resourceId)}',
    resource,
    safeToRetry: true,
    expectedId: normalizeJoplinId(resourceId),
  );

  Future<String> _writeResource(
    String method,
    String path,
    _JoplinResourcePayload resource, {
    required bool safeToRetry,
    required String expectedId,
  }) async {
    if (resource.bytes.isEmpty || resource.bytes.length > maxResourceBytes) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        '单个 Joplin 附件必须在 1 字节到 16 MB 之间',
      );
    }
    final boundary = 'oohstory-${resource.localFingerprint.substring(0, 32)}';
    final body = _multipartResourceBody(boundary, expectedId, resource);
    final response = await _send(
      method,
      path,
      rawBody: body,
      rawContentType: 'multipart/form-data; boundary=$boundary',
      safeToRetry: safeToRetry,
    );
    final payload = await _decodeSuccess(response);
    final returnedId = payload['id'];
    if (returnedId is! String) {
      _invalidResponse();
    }
    final normalizedId = normalizeJoplinId(returnedId);
    if (normalizedId != expectedId) _invalidResponse();
    await _requestObject(
      'PUT',
      '/resources/$normalizedId',
      body: <String, Object?>{
        'title': resource.title,
        'mime': resource.mediaType,
        'filename': resource.fileName,
        'user_data': resource.userData,
      },
    );
    return normalizedId;
  }

  static Uint8List _multipartResourceBody(
    String boundary,
    String resourceId,
    _JoplinResourcePayload resource,
  ) {
    final props = jsonEncode(<String, Object?>{
      'id': resourceId,
      'title': resource.title,
    });
    final builder = BytesBuilder(copy: false)
      ..add(
        utf8.encode(
          '--$boundary\r\n'
          'Content-Disposition: form-data; name="props"\r\n'
          'Content-Type: application/json; charset=utf-8\r\n\r\n'
          '$props\r\n'
          '--$boundary\r\n'
          'Content-Disposition: form-data; name="data"; filename="resource.bin"\r\n'
          'Content-Type: application/octet-stream\r\n\r\n',
        ),
      )
      ..add(resource.bytes)
      ..add(utf8.encode('\r\n--$boundary--\r\n'));
    return builder.takeBytes();
  }

  Future<void> ensureTags(String noteId) async {
    final normalizedNoteId = normalizeJoplinId(noteId);
    final desired = <String, String>{
      for (final title in tagTitles)
        deterministicJoplinId('oohstory-joplin-tag-v1', title): title,
    };
    for (final entry in desired.entries) {
      final existing = await _readTag(entry.key);
      if (existing == null) {
        final payload = await _requestObject(
          'POST',
          '/tags',
          body: <String, Object?>{'id': entry.key, 'title': entry.value},
          safeToRetry: false,
        );
        final returnedId = payload['id'];
        final returnedTitle = payload['title'];
        if (returnedId is! String ||
            normalizeJoplinId(returnedId) != entry.key ||
            returnedTitle != entry.value) {
          _invalidResponse();
        }
      } else if (existing != entry.value) {
        throw const CoreException(
          CoreErrorCode.revisionConflict,
          'Joplin 标签 ID 已被其他内容占用',
        );
      }
    }

    final attached = await _attachedTagIds(normalizedNoteId, desired.keys);
    for (final tagId in desired.keys.where((id) => !attached.contains(id))) {
      await _requestObject(
        'POST',
        '/tags/$tagId/notes',
        body: <String, Object?>{'id': normalizedNoteId},
        safeToRetry: false,
      );
    }
  }

  Future<String?> _readTag(String tagId) async {
    final response = await _send(
      'GET',
      '/tags/${normalizeJoplinId(tagId)}',
      query: const <String, String>{'fields': 'id,title'},
    );
    if (response.statusCode == 404) {
      await response.body.drain<void>();
      return null;
    }
    final payload = await _decodeSuccess(response);
    final id = payload['id'];
    final title = payload['title'];
    if (id is! String || normalizeJoplinId(id) != tagId || title is! String) {
      _invalidResponse();
    }
    return title;
  }

  Future<Set<String>> _attachedTagIds(
    String noteId,
    Iterable<String> desiredIds,
  ) async {
    final desired = desiredIds.toSet();
    final found = <String>{};
    for (var page = 1; page <= 20 && !found.containsAll(desired); page++) {
      final payload = await _requestObject(
        'GET',
        '/notes/$noteId/tags',
        query: <String, String>{
          'fields': 'id',
          'limit': '100',
          'page': '$page',
        },
      );
      final items = payload['items'];
      if (items is! List) _invalidResponse();
      for (final item in items) {
        if (item is Map && item['id'] is String) {
          final id = item['id'] as String;
          if (RegExp(r'^[0-9a-fA-F]{32}$').hasMatch(id)) {
            found.add(id.toLowerCase());
          }
        }
      }
      if (payload['has_more'] != true) return found;
    }
    if (!found.containsAll(desired)) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Joplin 笔记标签数量超过本地检查上限',
      );
    }
    return found;
  }

  Future<Map<String, Object?>> _requestObject(
    String method,
    String path, {
    Map<String, String> query = const <String, String>{},
    Map<String, Object?>? body,
    bool safeToRetry = true,
  }) async {
    final response = await _send(
      method,
      path,
      query: query,
      body: body,
      safeToRetry: safeToRetry,
    );
    return _decodeSuccess(response);
  }

  Future<Map<String, Object?>> _decodeSuccess(
    CloudHttpResponse response,
  ) async {
    if (response.statusCode < 200 || response.statusCode >= 300) {
      await response.body.drain<void>();
      throw cloudStatusError(response.statusCode);
    }
    return decodeJsonObject(response);
  }

  Future<CloudHttpResponse> _send(
    String method,
    String path, {
    Map<String, String> query = const <String, String>{},
    Map<String, Object?>? body,
    Uint8List? rawBody,
    String? rawContentType,
    bool safeToRetry = true,
    bool authenticated = true,
  }) async {
    if (body != null && rawBody != null) {
      throw ArgumentError('Joplin request cannot contain two body formats');
    }
    if (query.containsKey('token')) {
      throw ArgumentError('Joplin token is managed by the API client');
    }
    final token = authenticated ? validateJoplinToken(await _token()) : null;
    final encoded = body == null ? rawBody : utf8.encode(jsonEncode(body));
    final maxRequestBytes = rawBody == null
        ? _maxBodyBytes + 64 * 1024
        : maxResourceBytes + 64 * 1024;
    if (encoded != null && encoded.length > maxRequestBytes) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Joplin 请求超过本地安全上限',
      );
    }
    final uri = _endpoint.replace(
      path: path,
      queryParameters: <String, String>{
        ...query,
        if (token != null) 'token': token,
      },
    );
    final request = CloudHttpRequest(
      method: method,
      uri: uri,
      headers: <String, String>{
        'accept': 'application/json',
        if (encoded != null)
          'content-type': rawContentType ?? 'application/json',
      },
      bodyFactory: encoded == null
          ? null
          : () => Stream<List<int>>.value(encoded),
      contentLength: encoded?.length,
    );
    final cancellation = CancellationToken();
    try {
      final response =
          await (safeToRetry
                  ? _retryPolicy.send(
                      _transport,
                      request,
                      cancellationToken: cancellation,
                      sleep: _sleep,
                    )
                  : _transport.send(request, cancellationToken: cancellation))
              .timeout(
                _requestTimeout,
                onTimeout: () {
                  cancellation.cancel();
                  throw const CoreException(
                    CoreErrorCode.upstreamError,
                    'Joplin 本机服务响应超时',
                  );
                },
              );
      return CloudHttpResponse(
        statusCode: response.statusCode,
        headers: response.headers,
        body: response.body.timeout(
          _requestTimeout,
          onTimeout: (sink) {
            cancellation.cancel();
            sink
              ..addError(
                const CoreException(
                  CoreErrorCode.upstreamError,
                  'Joplin 本机服务响应超时',
                ),
              )
              ..close();
          },
        ),
      );
    } on CoreException {
      rethrow;
    } on Object {
      // Transport exceptions can contain the full URI. Joplin requires its
      // token in that URI, so replace them with a provider-safe error.
      throw const CoreException(CoreErrorCode.upstreamError, 'Joplin 本机服务请求失败');
    }
  }

  static JoplinRemoteNote _noteFrom(Map<String, Object?> payload) {
    final id = payload['id'];
    final parentId = payload['parent_id'];
    final title = payload['title'];
    final body = payload['body'];
    final applicationData = payload['application_data'];
    final source = payload['source'];
    if (id is! String ||
        parentId is! String ||
        title is! String ||
        body is! String ||
        applicationData is! String ||
        source is! String) {
      _invalidResponse();
    }
    return JoplinRemoteNote(
      id: id,
      parentId: parentId,
      title: title,
      body: body,
      applicationData: applicationData,
      source: source,
    );
  }

  static String _boundedTitle(String value) {
    final normalized = AnnotationMarkdownRenderer.singleLine(value);
    return String.fromCharCodes(
      (normalized.isEmpty ? '未命名书籍' : normalized).runes.take(255),
    );
  }

  static String _validatedBody(String value) {
    if (utf8.encode(value).length > _maxBodyBytes) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        '单本书的 Joplin 导出不能超过 1 MB',
      );
    }
    return value;
  }

  static Never _invalidResponse() =>
      throw const CoreException(CoreErrorCode.upstreamError, 'Joplin 返回了无效响应');
}

final class JoplinExportConflict implements Exception {
  const JoplinExportConflict({
    required this.document,
    required this.noteId,
    required this.reason,
    this.resourceId,
    this.canOverwrite = true,
  });

  final DocumentIdentity document;
  final String noteId;
  final String reason;
  final String? resourceId;
  final bool canOverwrite;

  @override
  String toString() =>
      'Joplin export conflict for ${document.id}'
      '${resourceId == null ? '' : ' resource $resourceId'}: $reason';
}

final class JoplinAnnotationExporter implements AnnotationSink {
  JoplinAnnotationExporter({
    required this.configuration,
    required JoplinApiClient api,
    required JoplinExportStateStore stateStore,
    AnnotationMarkdownRenderer renderer = const AnnotationMarkdownRenderer(),
    DateTime Function()? now,
  }) : _api = api,
       _stateStore = stateStore,
       _renderer = renderer,
       _now = now ?? (() => DateTime.now().toUtc());

  final JoplinExportConfiguration configuration;
  final JoplinApiClient _api;
  final JoplinExportStateStore _stateStore;
  final AnnotationMarkdownRenderer _renderer;
  final DateTime Function() _now;

  @override
  String get providerId => 'joplin';

  @override
  ProviderCapabilities get capabilities => ProviderCapabilities(
    providerId: providerId,
    supported: const <AdapterCapability>[AdapterCapability.annotationExport],
  );

  @override
  Future<ExportReceipt> export(
    DocumentIdentity document,
    List<Annotation> annotations, {
    required String idempotencyKey,
    bool overwriteExternalChanges = false,
  }) => exportDocument(
    AnnotationExportDocument(identity: document, annotations: annotations),
    idempotencyKey: idempotencyKey,
    overwriteExternalChanges: overwriteExternalChanges,
  );

  Future<ExportReceipt> exportDocument(
    AnnotationExportDocument document, {
    required String idempotencyKey,
    bool overwriteExternalChanges = false,
  }) async {
    _validateInput(
      document.identity,
      document.annotations,
      document.attachments,
      idempotencyKey,
    );
    final resourcePlans = _resourcePlans(document);
    final identity = document.identity;
    final title = _title(identity.title);
    final noteId = deterministicJoplinId(
      'oohstory-joplin-note-v1',
      '${configuration.targetKey}\u0000${identity.id}',
    );
    final marker = jsonEncode(<String, String>{
      'application': 'oohstory',
      'schema': '1',
      'document_id': identity.id,
      'target_key': configuration.targetKey,
    });
    final state = await _stateStore.read(
      targetKey: configuration.targetKey,
      documentId: identity.id,
    );
    var remote = await _api.readNote(noteId);
    if (state != null && state.noteId != noteId) {
      throw const CoreException(
        CoreErrorCode.revisionConflict,
        'Joplin 本机导出映射不一致',
      );
    }
    if (state != null && remote == null && !overwriteExternalChanges) {
      throw JoplinExportConflict(
        document: identity,
        noteId: noteId,
        reason: 'Joplin 笔记已在外部删除',
      );
    }
    final recoverablePartialNoteCreate =
        state == null &&
        remote != null &&
        remote.source == 'oohstory' &&
        remote.applicationData.isEmpty;
    if (remote != null &&
        (remote.source != 'oohstory' ||
            (remote.applicationData != marker &&
                !recoverablePartialNoteCreate))) {
      throw JoplinExportConflict(
        document: identity,
        noteId: noteId,
        reason: state == null
            ? '确定性笔记 ID 已被非 OOHStory 内容占用'
            : 'Joplin 笔记归属标记已改变',
        canOverwrite: false,
      );
    }
    final externallyModified =
        state != null &&
        remote != null &&
        remote.fingerprint != state.remoteFingerprint;
    if (externallyModified && !overwriteExternalChanges) {
      throw JoplinExportConflict(
        document: identity,
        noteId: noteId,
        reason: 'Joplin 笔记在上次导出后被修改',
      );
    }
    Map<String, String> discoveredResourceIds = const <String, String>{};
    if (state == null && remote != null && !overwriteExternalChanges) {
      discoveredResourceIds = await _discoverUnregisteredResourceIds(
        identity,
        noteId,
        resourcePlans,
      );
      final preflightMarkdown = _renderMarkdown(
        document,
        resourcePlans,
        discoveredResourceIds,
      );
      final preflightExpected = JoplinRemoteNote(
        id: noteId,
        parentId: configuration.notebookId,
        title: title,
        body: preflightMarkdown,
        applicationData: marker,
        source: 'oohstory',
      );
      if (remote.fingerprint != preflightExpected.fingerprint &&
          recoverablePartialNoteCreate &&
          remote.contentFingerprint == preflightExpected.contentFingerprint) {
        await _api.updateNote(
          noteId: noteId,
          title: title,
          body: preflightMarkdown,
          applicationData: marker,
        );
        remote = await _api.readNote(noteId);
        if (remote == null ||
            remote.fingerprint != preflightExpected.fingerprint) {
          _invalidRemote();
        }
      } else if (remote.fingerprint != preflightExpected.fingerprint) {
        throw JoplinExportConflict(
          document: identity,
          noteId: noteId,
          reason: '发现未登记的 Joplin 笔记修改',
        );
      }
    }

    final resourceSync = await _synchronizeResources(
      identity,
      noteId,
      resourcePlans,
      state?.resources ?? const <JoplinResourceExportState>[],
      discoveredResourceIds: discoveredResourceIds,
      overwriteExternalChanges: overwriteExternalChanges,
    );
    final resourceIds = <String, String>{
      for (final item in resourceSync.states)
        item.attachmentId: item.resourceId,
    };
    final markdown = _renderMarkdown(document, resourcePlans, resourceIds);
    final localHash = _hash(
      jsonEncode(<String, Object?>{
        'markdown': markdown,
        'resources': resourcePlans
            .map((item) => item.payload.localFingerprint)
            .toList(growable: false),
      }),
    );
    final expected = JoplinRemoteNote(
      id: noteId,
      parentId: configuration.notebookId,
      title: title,
      body: markdown,
      applicationData: marker,
      source: 'oohstory',
    );
    final unregisteredRemoteChange =
        state == null &&
        remote != null &&
        remote.fingerprint != expected.fingerprint;

    var noteWritten = false;
    final noteCreated = remote == null;
    if (remote == null) {
      await _api.createNote(
        noteId: noteId,
        title: title,
        body: markdown,
        applicationData: marker,
      );
      noteWritten = true;
    } else if (remote.fingerprint != expected.fingerprint) {
      await _api.updateNote(
        noteId: noteId,
        title: title,
        body: markdown,
        applicationData: marker,
      );
      noteWritten = true;
    }
    final provisionalFingerprint = noteWritten
        ? expected.fingerprint
        : remote!.fingerprint;
    if (noteWritten ||
        resourceSync.changed ||
        state == null ||
        state.localContentHash != localHash) {
      await _writeState(
        identity.id,
        noteId,
        localHash,
        provisionalFingerprint,
        resourceSync.states,
      );
    }
    await _api.ensureTags(noteId);
    final confirmed = await _api.readNote(noteId);
    if (confirmed == null) _invalidRemote();
    if (confirmed.fingerprint != expected.fingerprint) {
      throw JoplinExportConflict(
        document: identity,
        noteId: noteId,
        reason: 'Joplin 笔记在导出确认期间发生变化',
      );
    }
    await _writeState(
      identity.id,
      noteId,
      localHash,
      confirmed.fingerprint,
      resourceSync.states,
    );
    final overwritten =
        (state != null && noteCreated) ||
        externallyModified ||
        unregisteredRemoteChange ||
        resourceSync.overwritten;
    final disposition = state == null && remote == null
        ? ExportDisposition.created
        : overwritten
        ? ExportDisposition.overwritten
        : noteWritten || resourceSync.changed
        ? ExportDisposition.updated
        : ExportDisposition.unchanged;
    return _receipt(identity, noteId, localHash, disposition);
  }

  List<_JoplinResourcePlan> _resourcePlans(AnnotationExportDocument document) {
    final plans = <_JoplinResourcePlan>[];
    for (final attachment in document.attachments) {
      final fileName = AnnotationMarkdownRenderer.singleLine(
        attachment.fileName,
      );
      final mediaType = attachment.mediaType.trim().toLowerCase();
      final userData = jsonEncode(<String, String>{
        'application': 'oohstory',
        'schema': '1',
        'target_key': configuration.targetKey,
        'document_id': document.identity.id,
        'annotation_id': attachment.annotationId,
        'attachment_id': attachment.id,
        'provenance_source': attachment.provenance.source,
        'provenance_id': attachment.provenance.sourceId,
      });
      plans.add(
        _JoplinResourcePlan(
          attachmentId: attachment.id,
          annotationId: attachment.annotationId,
          fileName: fileName,
          isImage: mediaType.startsWith('image/'),
          resourceId: deterministicJoplinId(
            'oohstory-joplin-resource-v1',
            '${configuration.targetKey}\u0000${document.identity.id}'
                '\u0000${attachment.id}',
          ),
          payload: _JoplinResourcePayload(
            title: fileName,
            mediaType: mediaType,
            fileName: fileName,
            userData: userData,
            bytes: attachment.bytes,
          ),
        ),
      );
    }
    plans.sort((left, right) {
      final annotationOrder = left.annotationId.compareTo(right.annotationId);
      return annotationOrder != 0
          ? annotationOrder
          : left.attachmentId.compareTo(right.attachmentId);
    });
    return List<_JoplinResourcePlan>.unmodifiable(plans);
  }

  String _renderMarkdown(
    AnnotationExportDocument document,
    List<_JoplinResourcePlan> resources,
    Map<String, String> resourceIds,
  ) {
    final base = _renderer.render(document.identity, document.annotations);
    if (resources.isEmpty) return base;
    final output = StringBuffer(base);
    if (!base.endsWith('\n')) output.writeln();
    output
      ..writeln('## 附件')
      ..writeln();
    for (final resource in resources) {
      final label = _markdownLabel(resource.fileName);
      final resourceId = resourceIds[resource.attachmentId];
      if (resourceId == null) {
        throw const CoreException(
          CoreErrorCode.revisionConflict,
          'Joplin 附件缺少远端资源映射',
        );
      }
      output
        ..writeln(
          '<!-- oohstory-attachment:${jsonEncode(resource.attachmentId)} '
          'annotation:${jsonEncode(resource.annotationId)} -->',
        )
        ..writeln(
          resource.isImage
              ? '![$label](:/$resourceId)'
              : '[$label](:/$resourceId)',
        )
        ..writeln();
    }
    return output.toString();
  }

  Future<Map<String, String>> _discoverUnregisteredResourceIds(
    DocumentIdentity document,
    String noteId,
    List<_JoplinResourcePlan> resources,
  ) async {
    final discovered = <String, String>{};
    final matchesByMarker = await _api._findResourceIdsByUserData(
      resources.map((item) => item.payload.userData),
    );
    for (final resource in resources) {
      final matches = matchesByMarker[resource.payload.userData]!;
      if (matches.length > 1) {
        throw JoplinExportConflict(
          document: document,
          noteId: noteId,
          reason: 'Joplin 中存在重复的 OOHStory 附件来源标记',
          canOverwrite: false,
        );
      }
      if (matches.isEmpty) {
        throw JoplinExportConflict(
          document: document,
          noteId: noteId,
          reason: '未登记的 Joplin 笔记缺少对应附件来源',
        );
      }
      discovered[resource.attachmentId] = matches.single;
    }
    return discovered;
  }

  Future<_JoplinResourceSyncResult> _synchronizeResources(
    DocumentIdentity document,
    String noteId,
    List<_JoplinResourcePlan> resources,
    List<JoplinResourceExportState> previousStates, {
    required Map<String, String> discoveredResourceIds,
    required bool overwriteExternalChanges,
  }) async {
    final previousByAttachment = <String, JoplinResourceExportState>{};
    for (final state in previousStates) {
      if (previousByAttachment.putIfAbsent(state.attachmentId, () => state) !=
          state) {
        throw const CoreException(
          CoreErrorCode.revisionConflict,
          'Joplin 附件导出映射存在重复项',
        );
      }
    }
    final unmatchedPlans = resources.where(
      (resource) =>
          previousByAttachment[resource.attachmentId] == null &&
          discoveredResourceIds[resource.attachmentId] == null,
    );
    final matchesByMarker = await _api._findResourceIdsByUserData(
      unmatchedPlans.map((item) => item.payload.userData),
    );
    final prepared = <_JoplinPreparedResource>[];
    for (final resource in resources) {
      final payload = resource.payload;
      final previous = previousByAttachment[resource.attachmentId];
      String? resourceId =
          previous?.resourceId ?? discoveredResourceIds[resource.attachmentId];
      var deterministicCandidate = false;
      if (resourceId == null) {
        final matches = matchesByMarker[payload.userData]!;
        if (matches.length > 1) {
          throw JoplinExportConflict(
            document: document,
            noteId: noteId,
            reason: 'Joplin 中存在重复的 OOHStory 附件来源标记',
            canOverwrite: false,
          );
        }
        if (matches.isNotEmpty) {
          resourceId = matches.single;
        } else {
          resourceId = resource.resourceId;
          deterministicCandidate = true;
        }
      }
      var remote = await _api.readResource(resourceId);
      final recoverablePartialCreate =
          deterministicCandidate &&
          remote != null &&
          remote.userData.isEmpty &&
          remote.title == payload.title &&
          remote.size == payload.bytes.length &&
          remote.contentHash == payload.contentHash;
      if (remote != null &&
          remote.userData != payload.userData &&
          !recoverablePartialCreate) {
        throw JoplinExportConflict(
          document: document,
          noteId: noteId,
          resourceId: resourceId,
          reason: deterministicCandidate
              ? '确定性 Joplin 附件 ID 已被其他内容占用'
              : 'Joplin 附件归属标记已改变',
          canOverwrite: false,
        );
      }

      var action = _JoplinResourceAction.none;
      var overwritesRemote = false;
      if (previous == null) {
        if (remote == null) {
          action = _JoplinResourceAction.create;
        } else if (recoverablePartialCreate) {
          action = _JoplinResourceAction.update;
        } else if (remote.fingerprint !=
            payload.expected(resourceId).fingerprint) {
          if (!overwriteExternalChanges) {
            throw JoplinExportConflict(
              document: document,
              noteId: noteId,
              resourceId: resourceId,
              reason: '发现未登记的 Joplin 附件修改',
            );
          }
          action = _JoplinResourceAction.update;
          overwritesRemote = true;
        }
      } else if (remote == null) {
        if (!overwriteExternalChanges) {
          throw JoplinExportConflict(
            document: document,
            noteId: noteId,
            resourceId: resourceId,
            reason: 'Joplin 附件已在外部删除',
          );
        }
        resourceId = resource.resourceId;
        remote = await _api.readResource(resourceId);
        if (remote != null && remote.userData != payload.userData) {
          throw JoplinExportConflict(
            document: document,
            noteId: noteId,
            resourceId: resourceId,
            reason: '确定性 Joplin 附件 ID 已被其他内容占用',
            canOverwrite: false,
          );
        }
        action = remote == null
            ? _JoplinResourceAction.create
            : remote.fingerprint == payload.expected(resourceId).fingerprint
            ? _JoplinResourceAction.none
            : _JoplinResourceAction.update;
        overwritesRemote = true;
      } else {
        final externallyModified =
            remote.fingerprint != previous.remoteFingerprint;
        final matchesCurrentLocal =
            remote.fingerprint == payload.expected(resourceId).fingerprint;
        if (externallyModified &&
            !matchesCurrentLocal &&
            !overwriteExternalChanges) {
          throw JoplinExportConflict(
            document: document,
            noteId: noteId,
            resourceId: resourceId,
            reason: 'Joplin 附件在上次导出后被修改',
          );
        }
        if (!matchesCurrentLocal &&
            (externallyModified ||
                previous.localFingerprint != payload.localFingerprint)) {
          action = _JoplinResourceAction.update;
        }
        overwritesRemote = externallyModified && !matchesCurrentLocal;
      }
      prepared.add(
        _JoplinPreparedResource(
          plan: resource,
          resourceId: resourceId,
          remote: remote,
          action: action,
          overwritten: overwritesRemote,
        ),
      );
    }

    final nextStates = <JoplinResourceExportState>[];
    var changed = false;
    var overwritten = false;
    for (final item in prepared) {
      final payload = item.plan.payload;
      var resourceId = item.resourceId;
      switch (item.action) {
        case _JoplinResourceAction.none:
          break;
        case _JoplinResourceAction.create:
          resourceId = await _api._createResource(
            item.plan.resourceId,
            payload,
          );
          changed = true;
          break;
        case _JoplinResourceAction.update:
          if (resourceId == null) _invalidResource();
          await _api._updateResource(resourceId, payload);
          changed = true;
          break;
      }
      if (resourceId == null) _invalidResource();
      final remote = item.action == _JoplinResourceAction.none
          ? item.remote
          : await _api.readResource(resourceId);
      if (remote == null ||
          remote.fingerprint != payload.expected(resourceId).fingerprint) {
        _invalidResource();
      }
      overwritten = overwritten || item.overwritten;
      nextStates.add(
        JoplinResourceExportState(
          attachmentId: item.plan.attachmentId,
          resourceId: resourceId,
          localFingerprint: payload.localFingerprint,
          remoteFingerprint: remote.fingerprint,
        ),
      );
    }
    return _JoplinResourceSyncResult(
      states: List<JoplinResourceExportState>.unmodifiable(nextStates),
      changed: changed,
      overwritten: overwritten,
    );
  }

  Future<void> _writeState(
    String documentId,
    String noteId,
    String localHash,
    String remoteFingerprint,
    List<JoplinResourceExportState> resources,
  ) => _stateStore.write(
    JoplinExportState(
      targetKey: configuration.targetKey,
      documentId: documentId,
      noteId: noteId,
      localContentHash: localHash,
      remoteFingerprint: remoteFingerprint,
      resources: List<JoplinResourceExportState>.unmodifiable(resources),
    ),
  );

  ExportReceipt _receipt(
    DocumentIdentity document,
    String noteId,
    String contentHash,
    ExportDisposition disposition,
  ) => ExportReceipt(
    providerId: providerId,
    documentId: document.id,
    target: 'joplin:note:$noteId',
    contentHash: contentHash,
    exportedAt: _now().toUtc(),
    disposition: disposition,
  );

  static String _title(String value) {
    final normalized = AnnotationMarkdownRenderer.singleLine(value);
    return String.fromCharCodes(
      (normalized.isEmpty ? '未命名书籍' : normalized).runes.take(255),
    );
  }

  static String _hash(String value) =>
      sha256.convert(utf8.encode(value)).toString();

  static String _markdownLabel(String value) => value
      .replaceAll('\\', '\\\\')
      .replaceAll('[', '\\[')
      .replaceAll(']', '\\]');

  static void _validateInput(
    DocumentIdentity document,
    List<Annotation> annotations,
    List<AnnotationExportAttachment> attachments,
    String idempotencyKey,
  ) {
    if (document.id.trim().isEmpty ||
        document.title.trim().isEmpty ||
        document.documentVersion.trim().isEmpty ||
        idempotencyKey.trim().isEmpty ||
        annotations.isEmpty) {
      throw const FormatException('Joplin 导出数据不完整');
    }
    if (annotations.any(
      (annotation) =>
          annotation.id.trim().isEmpty || annotation.bookId != document.id,
    )) {
      throw const FormatException('批注与书籍标识不匹配');
    }
    final annotationIds = annotations.map((item) => item.id).toSet();
    if (annotationIds.length != annotations.length) {
      throw const FormatException('批注标识不能重复');
    }
    if (attachments.length > 64) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        '单本书最多导出 64 个 Joplin 附件',
      );
    }
    final attachmentIds = <String>{};
    var totalBytes = 0;
    for (final attachment in attachments) {
      final fileName = AnnotationMarkdownRenderer.singleLine(
        attachment.fileName,
      );
      final mediaType = attachment.mediaType.trim().toLowerCase();
      if (!_validIdentityPart(attachment.id, maxLength: 256) ||
          !_validIdentityPart(attachment.annotationId, maxLength: 256) ||
          !_validIdentityPart(attachment.provenance.source, maxLength: 128) ||
          !_validIdentityPart(attachment.provenance.sourceId, maxLength: 512) ||
          attachment.bookId != document.id ||
          !annotationIds.contains(attachment.annotationId) ||
          !attachmentIds.add(attachment.id)) {
        throw const FormatException('Joplin 附件身份或来源无效');
      }
      if (fileName.isEmpty ||
          fileName.runes.length > 255 ||
          fileName.contains('/') ||
          fileName.contains('\\')) {
        throw const FormatException('Joplin 附件文件名无效');
      }
      if (!RegExp(
        r'^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$',
      ).hasMatch(mediaType)) {
        throw const FormatException('Joplin 附件媒体类型无效');
      }
      if (attachment.bytes.isEmpty ||
          attachment.bytes.length > JoplinApiClient.maxResourceBytes) {
        throw const CoreException(
          CoreErrorCode.payloadTooLarge,
          '单个 Joplin 附件必须在 1 字节到 16 MB 之间',
        );
      }
      totalBytes += attachment.bytes.length;
      if (totalBytes > 64 * 1024 * 1024) {
        throw const CoreException(
          CoreErrorCode.payloadTooLarge,
          '单本书的 Joplin 附件总量不能超过 64 MB',
        );
      }
    }
  }

  static bool _validIdentityPart(String value, {required int maxLength}) =>
      value.trim() == value &&
      value.isNotEmpty &&
      value.runes.length <= maxLength &&
      !value.runes.any((rune) => rune < 0x20 || rune == 0x7f);

  static Never _invalidRemote() => throw const CoreException(
    CoreErrorCode.upstreamError,
    'Joplin 写入后无法读取笔记',
  );

  static Never _invalidResource() => throw const CoreException(
    CoreErrorCode.upstreamError,
    'Joplin 写入后无法确认附件',
  );
}

final class JoplinExportBatchResult {
  const JoplinExportBatchResult({
    required this.receipts,
    required this.conflicts,
  });

  final List<ExportReceipt> receipts;
  final List<JoplinExportConflict> conflicts;
}

final class JoplinAnnotationExportService {
  const JoplinAnnotationExportService(this._exporter);

  final JoplinAnnotationExporter _exporter;

  Future<JoplinExportBatchResult> exportDocuments(
    Iterable<AnnotationExportDocument> documents, {
    bool overwriteExternalChanges = false,
  }) async {
    final receipts = <ExportReceipt>[];
    final conflicts = <JoplinExportConflict>[];
    for (final document in documents) {
      try {
        receipts.add(
          await _exporter.exportDocument(
            document,
            idempotencyKey: _idempotencyKey(document),
            overwriteExternalChanges: overwriteExternalChanges,
          ),
        );
      } on JoplinExportConflict catch (conflict) {
        conflicts.add(conflict);
      }
    }
    return JoplinExportBatchResult(
      receipts: List<ExportReceipt>.unmodifiable(receipts),
      conflicts: List<JoplinExportConflict>.unmodifiable(conflicts),
    );
  }

  static String _idempotencyKey(AnnotationExportDocument document) {
    final annotationIds = document.annotations.map((item) => item.id).toList()
      ..sort();
    final attachmentFingerprints =
        document.attachments
            .map(
              (item) => jsonEncode(<String, String>{
                'id': item.id,
                'annotation_id': item.annotationId,
                'source': item.provenance.source,
                'source_id': item.provenance.sourceId,
                'content_sha256': sha256.convert(item.bytes).toString(),
              }),
            )
            .toList()
          ..sort();
    return sha256
        .convert(
          utf8.encode(
            '${document.identity.id}\u0000${annotationIds.join('\u0000')}'
            '\u0000${attachmentFingerprints.join('\u0000')}',
          ),
        )
        .toString();
  }
}
