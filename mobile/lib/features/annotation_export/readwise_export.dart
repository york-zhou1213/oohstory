import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../adapters/cloud/cloud.dart';
import '../../adapters/contracts/adapter_contracts.dart';
import '../../core/core.dart';
import '../../services/api_service.dart';
import 'annotation_documents.dart';
import 'annotation_markdown.dart';

final class ReadwiseConnectionRepository {
  ReadwiseConnectionRepository({required SecureCredentialStore credentialStore})
    : _credentialStore = credentialStore;

  static const _tokenKey = 'oohstory.integration.readwise.access_token';
  final SecureCredentialStore _credentialStore;

  Future<bool> hasAccessToken() async =>
      (await _credentialStore.read(_tokenKey))?.isNotEmpty ?? false;

  Future<String> requireAccessToken() async {
    final token = await _credentialStore.read(_tokenKey);
    if (token == null || token.isEmpty) {
      throw const CoreException(CoreErrorCode.unauthorized, 'Readwise 授权令牌不可用');
    }
    return validateReadwiseToken(token);
  }

  Future<void> saveAccessToken(String token) =>
      _credentialStore.write(_tokenKey, validateReadwiseToken(token.trim()));

  Future<void> disconnect() => _credentialStore.delete(_tokenKey);
}

String validateReadwiseToken(String value) {
  if (value.length < 16 ||
      value.length > 4096 ||
      value.runes.any((rune) => rune < 0x21 || rune == 0x7f)) {
    throw const FormatException('Readwise 授权令牌格式无效');
  }
  return value;
}

final class ReadwiseRemoteHighlight {
  const ReadwiseRemoteHighlight({
    required this.id,
    required this.text,
    required this.note,
    required this.location,
    required this.locationType,
    required this.url,
    required this.isDeleted,
  });

  final int id;
  final String text;
  final String note;
  final int location;
  final String locationType;
  final String? url;
  final bool isDeleted;

  factory ReadwiseRemoteHighlight.fromJson(Map<String, Object?> json) {
    final id = json['id'];
    final text = json['text'];
    final location = json['location'];
    final locationType = json['location_type'];
    if (id is! int ||
        id <= 0 ||
        text is! String ||
        location is! int ||
        location < 0 ||
        locationType is! String) {
      throw const CoreException(
        CoreErrorCode.upstreamError,
        'Readwise 返回了无效高亮数据',
      );
    }
    return ReadwiseRemoteHighlight(
      id: id,
      text: text,
      note: json['note'] as String? ?? '',
      location: location,
      locationType: locationType,
      url: json['url'] as String?,
      isDeleted: json['is_deleted'] as bool? ?? false,
    );
  }

  String get managedHash => _hashJson(<String, Object?>{
    'text': text,
    'note': note,
    'location': location,
    'location_type': locationType,
  });
}

final class ReadwiseHighlightState {
  ReadwiseHighlightState({
    required this.annotationId,
    required this.remoteId,
    required this.localContentHash,
    required this.remoteContentHash,
  }) {
    if (annotationId.trim().isEmpty || remoteId <= 0) {
      throw const FormatException('Readwise 高亮状态标识无效');
    }
    for (final hash in <String>[localContentHash, remoteContentHash]) {
      if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(hash)) {
        throw const FormatException('Readwise 高亮状态哈希无效');
      }
    }
  }

  final String annotationId;
  final int remoteId;
  final String localContentHash;
  final String remoteContentHash;

  factory ReadwiseHighlightState.fromJson(Map<String, Object?> json) =>
      ReadwiseHighlightState(
        annotationId: json['annotation_id'] as String,
        remoteId: json['remote_id'] as int,
        localContentHash: json['local_content_hash'] as String,
        remoteContentHash: json['remote_content_hash'] as String,
      );

  Map<String, Object?> toJson() => <String, Object?>{
    'annotation_id': annotationId,
    'remote_id': remoteId,
    'local_content_hash': localContentHash,
    'remote_content_hash': remoteContentHash,
  };
}

final class ReadwiseExportState {
  ReadwiseExportState({
    required this.documentId,
    required Map<String, ReadwiseHighlightState> highlights,
  }) : highlights = Map<String, ReadwiseHighlightState>.unmodifiable(
         highlights,
       ) {
    if (documentId.trim().isEmpty ||
        highlights.entries.any(
          (entry) => entry.key != entry.value.annotationId,
        )) {
      throw const FormatException('Readwise 导出状态无效');
    }
  }

  final String documentId;
  final Map<String, ReadwiseHighlightState> highlights;

  factory ReadwiseExportState.fromJson(Map<String, Object?> json) {
    final rawHighlights = json['highlights'];
    if (rawHighlights is! Map) {
      throw const FormatException('Readwise 导出状态无效');
    }
    return ReadwiseExportState(
      documentId: json['document_id'] as String,
      highlights: <String, ReadwiseHighlightState>{
        for (final entry in rawHighlights.entries)
          entry.key.toString(): ReadwiseHighlightState.fromJson(
            Map<String, Object?>.from(entry.value as Map),
          ),
      },
    );
  }

  Map<String, Object?> toJson() => <String, Object?>{
    'document_id': documentId,
    'highlights': <String, Object?>{
      for (final entry in highlights.entries) entry.key: entry.value.toJson(),
    },
  };
}

abstract interface class ReadwiseExportStateStore {
  Future<ReadwiseExportState?> read(String documentId);
  Future<void> write(ReadwiseExportState state);
  Future<void> clear();
}

final class SharedPreferencesReadwiseExportStateStore
    implements ReadwiseExportStateStore {
  SharedPreferencesReadwiseExportStateStore(this._preferences);

  static const _storageKey = 'oohstory_readwise_export_states_v1';
  final SharedPreferences _preferences;

  @override
  Future<ReadwiseExportState?> read(String documentId) async {
    final raw = _readAll()[_stateKey(documentId)];
    if (raw is! Map) return null;
    try {
      final state = ReadwiseExportState.fromJson(
        Map<String, Object?>.from(raw),
      );
      return state.documentId == documentId ? state : null;
    } on Object {
      return null;
    }
  }

  @override
  Future<void> write(ReadwiseExportState state) async {
    final states = _readAll();
    states[_stateKey(state.documentId)] = state.toJson();
    await _preferences.setString(_storageKey, jsonEncode(states));
  }

  @override
  Future<void> clear() => _preferences.remove(_storageKey);

  Map<String, Object?> _readAll() {
    final raw = _preferences.getString(_storageKey);
    if (raw == null) return <String, Object?>{};
    try {
      return Map<String, Object?>.from(jsonDecode(raw) as Map);
    } on Object {
      return <String, Object?>{};
    }
  }

  String _stateKey(String documentId) =>
      sha256.convert(utf8.encode(documentId)).toString();
}

final class ReadwiseApiClient {
  ReadwiseApiClient({
    required CloudHttpTransport transport,
    required Future<String> Function() accessToken,
    RetryPolicy retryPolicy = const RetryPolicy(),
    Future<void> Function(Duration delay)? sleep,
  }) : _transport = transport,
       _accessToken = accessToken,
       _retryPolicy = retryPolicy,
       _sleep = sleep;

  static final Uri _baseUri = Uri.parse('https://readwise.io');
  static const _maxResponseBytes = 4 * 1024 * 1024;
  static const _maxRecoveryPages = 20;

  final CloudHttpTransport _transport;
  final Future<String> Function() _accessToken;
  final RetryPolicy _retryPolicy;
  final Future<void> Function(Duration delay)? _sleep;

  Future<void> verifyConnection() async {
    final response = await _send('GET', '/api/v2/auth/');
    if (response.statusCode == 204) {
      await response.body.drain<void>();
      return;
    }
    await response.body.drain<void>();
    throw cloudStatusError(response.statusCode);
  }

  Future<ReadwiseRemoteHighlight> createHighlight(
    Map<String, Object?> highlight,
  ) async {
    final response = await _send(
      'POST',
      '/api/v2/highlights/',
      body: <String, Object?>{
        'highlights': <Object?>[highlight],
      },
    );
    final payload = await _decodeJson(response);
    if (payload is! List) _invalidResponse();
    final ids = <int>[];
    for (final item in payload.whereType<Map>()) {
      final modified = item['modified_highlights'];
      if (modified is List) ids.addAll(modified.whereType<int>());
    }
    if (ids.isNotEmpty) return readHighlight(ids.last);

    // Readwise de-duplicates POST requests by source metadata and highlight
    // URL. A retry after an ambiguous response can therefore legitimately
    // return no modified ID; recover the existing item from the export API.
    final highlightUrl = highlight['highlight_url'];
    if (highlightUrl is! String || highlightUrl.isEmpty) _invalidResponse();
    return findByHighlightUrl(highlightUrl);
  }

  Future<ReadwiseRemoteHighlight> readHighlight(int id) async {
    if (id <= 0) throw const FormatException('Readwise 高亮 ID 无效');
    final response = await _send('GET', '/api/v2/highlights/$id/');
    final payload = await _decodeJson(response);
    if (payload is! Map) _invalidResponse();
    return ReadwiseRemoteHighlight.fromJson(Map<String, Object?>.from(payload));
  }

  Future<ReadwiseRemoteHighlight> updateHighlight(
    int id,
    Map<String, Object?> managedFields,
  ) async {
    final response = await _send(
      'PATCH',
      '/api/v2/highlights/$id/',
      body: managedFields,
    );
    final payload = await _decodeJson(response);
    if (payload is! Map) _invalidResponse();
    return ReadwiseRemoteHighlight.fromJson(Map<String, Object?>.from(payload));
  }

  Future<ReadwiseRemoteHighlight> findByHighlightUrl(
    String highlightUrl,
  ) async {
    String? cursor;
    for (var page = 0; page < _maxRecoveryPages; page++) {
      final uri = _baseUri
          .resolve('/api/v2/export/')
          .replace(
            queryParameters: cursor == null
                ? null
                : <String, String>{'pageCursor': cursor},
          );
      final response = await _sendUri('GET', uri);
      final payload = await _decodeJson(response);
      if (payload is! Map) _invalidResponse();
      final results = payload['results'];
      if (results is! List) _invalidResponse();
      for (final book in results.whereType<Map>()) {
        final highlights = book['highlights'];
        if (highlights is! List) continue;
        for (final raw in highlights.whereType<Map>()) {
          final item = ReadwiseRemoteHighlight.fromJson(
            Map<String, Object?>.from(raw),
          );
          if (item.url == highlightUrl) return item;
        }
      }
      final next = payload['nextPageCursor'];
      if (next == null) break;
      if (next is! String || next.isEmpty || next == cursor) _invalidResponse();
      cursor = next;
    }
    throw const CoreException(
      CoreErrorCode.notFound,
      'Readwise 中未找到已确认的 OOHStory 高亮',
    );
  }

  Future<CloudHttpResponse> _send(
    String method,
    String path, {
    Map<String, Object?>? body,
  }) => _sendUri(method, _baseUri.resolve(path), body: body);

  Future<CloudHttpResponse> _sendUri(
    String method,
    Uri uri, {
    Map<String, Object?>? body,
  }) async {
    if (uri.scheme != 'https' || uri.host != _baseUri.host) {
      throw const CoreException(CoreErrorCode.forbidden, 'Readwise 请求目标无效');
    }
    final token = validateReadwiseToken(await _accessToken());
    final encoded = body == null ? null : utf8.encode(jsonEncode(body));
    if (encoded != null && encoded.length > 1024 * 1024) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Readwise 请求超过本地安全上限',
      );
    }
    final response = await _retryPolicy.send(
      _transport,
      CloudHttpRequest(
        method: method,
        uri: uri,
        headers: <String, String>{
          'accept': 'application/json',
          'authorization': 'Token $token',
          if (encoded != null) 'content-type': 'application/json',
        },
        bodyFactory: encoded == null
            ? null
            : () => Stream<List<int>>.value(encoded),
        contentLength: encoded?.length,
      ),
      sleep: _sleep,
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      await response.body.drain<void>();
      throw cloudStatusError(response.statusCode);
    }
    return response;
  }

  Future<Object?> _decodeJson(CloudHttpResponse response) async {
    try {
      final bytes = await collectResponseBytes(
        response,
        maxBytes: _maxResponseBytes,
      );
      return jsonDecode(utf8.decode(bytes));
    } on CoreException {
      rethrow;
    } on Object {
      _invalidResponse();
    }
  }

  static Never _invalidResponse() => throw const CoreException(
    CoreErrorCode.upstreamError,
    'Readwise 返回了无效响应',
  );
}

final class ReadwiseExportConflict implements Exception {
  const ReadwiseExportConflict({
    required this.document,
    required this.annotationId,
    required this.remoteId,
    required this.reason,
  });

  final DocumentIdentity document;
  final String annotationId;
  final int remoteId;
  final String reason;

  @override
  String toString() =>
      'Readwise export conflict for ${document.id}/$annotationId: $reason';
}

final class ReadwiseAnnotationExporter implements AnnotationSink {
  ReadwiseAnnotationExporter({
    required ReadwiseApiClient api,
    required ReadwiseExportStateStore stateStore,
    DateTime Function()? now,
  }) : _api = api,
       _stateStore = stateStore,
       _now = now ?? (() => DateTime.now().toUtc());

  final ReadwiseApiClient _api;
  final ReadwiseExportStateStore _stateStore;
  final DateTime Function() _now;

  @override
  String get providerId => 'readwise';

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
  }) async {
    _validateInput(document, annotations, idempotencyKey);
    final previous = await _stateStore.read(document.id);
    final states = <String, ReadwiseHighlightState>{...?previous?.highlights};
    var created = false;
    var updated = false;
    var overwritten = false;
    final localHashes = <String>[];

    for (var index = 0; index < annotations.length; index++) {
      final annotation = annotations[index];
      final payload = _payload(document, annotation, index);
      final managedFields = Map<String, Object?>.from(
        payload['managed'] as Map,
      );
      final localHash = _hashJson(managedFields);
      localHashes.add('${annotation.id}:$localHash');
      final state = states[annotation.id];
      if (state == null) {
        final remote = await _api.createHighlight(
          Map<String, Object?>.from(payload['create'] as Map),
        );
        states[annotation.id] = ReadwiseHighlightState(
          annotationId: annotation.id,
          remoteId: remote.id,
          localContentHash: localHash,
          remoteContentHash: remote.managedHash,
        );
        await _writeState(document.id, states);
        created = true;
        continue;
      }

      late ReadwiseRemoteHighlight remote;
      try {
        remote = await _api.readHighlight(state.remoteId);
      } on CoreException catch (error) {
        if (error.code != CoreErrorCode.notFound) rethrow;
        if (!overwriteExternalChanges) {
          throw ReadwiseExportConflict(
            document: document,
            annotationId: annotation.id,
            remoteId: state.remoteId,
            reason: 'Readwise 高亮已被删除',
          );
        }
        remote = await _api.createHighlight(
          Map<String, Object?>.from(payload['create'] as Map),
        );
        states[annotation.id] = ReadwiseHighlightState(
          annotationId: annotation.id,
          remoteId: remote.id,
          localContentHash: localHash,
          remoteContentHash: remote.managedHash,
        );
        await _writeState(document.id, states);
        overwritten = true;
        continue;
      }

      if (remote.isDeleted) {
        if (!overwriteExternalChanges) {
          throw ReadwiseExportConflict(
            document: document,
            annotationId: annotation.id,
            remoteId: state.remoteId,
            reason: 'Readwise 高亮已被删除',
          );
        }
        remote = await _api.createHighlight(
          Map<String, Object?>.from(payload['create'] as Map),
        );
        states[annotation.id] = ReadwiseHighlightState(
          annotationId: annotation.id,
          remoteId: remote.id,
          localContentHash: localHash,
          remoteContentHash: remote.managedHash,
        );
        await _writeState(document.id, states);
        overwritten = true;
        continue;
      }

      final externallyModified = remote.managedHash != state.remoteContentHash;
      if (externallyModified && !overwriteExternalChanges) {
        throw ReadwiseExportConflict(
          document: document,
          annotationId: annotation.id,
          remoteId: state.remoteId,
          reason: 'Readwise 高亮在上次导出后被修改',
        );
      }
      if (!externallyModified && localHash == state.localContentHash) continue;

      remote = await _api.updateHighlight(state.remoteId, managedFields);
      states[annotation.id] = ReadwiseHighlightState(
        annotationId: annotation.id,
        remoteId: remote.id,
        localContentHash: localHash,
        remoteContentHash: remote.managedHash,
      );
      await _writeState(document.id, states);
      if (externallyModified) {
        overwritten = true;
      } else {
        updated = true;
      }
    }

    localHashes.sort();
    final disposition = overwritten
        ? ExportDisposition.overwritten
        : updated
        ? ExportDisposition.updated
        : created
        ? ExportDisposition.created
        : ExportDisposition.unchanged;
    return ExportReceipt(
      providerId: providerId,
      documentId: document.id,
      target: 'readwise:book:${_stableId(document.id)}',
      contentHash: sha256
          .convert(utf8.encode(localHashes.join('\u0000')))
          .toString(),
      exportedAt: _now().toUtc(),
      disposition: disposition,
    );
  }

  Future<void> _writeState(
    String documentId,
    Map<String, ReadwiseHighlightState> highlights,
  ) => _stateStore.write(
    ReadwiseExportState(documentId: documentId, highlights: highlights),
  );

  static Map<String, Object?> _payload(
    DocumentIdentity document,
    Annotation annotation,
    int index,
  ) {
    final cleanText = annotation.text.trim();
    final cleanNote = annotation.note?.trim() ?? '';
    final text = cleanText.isNotEmpty
        ? cleanText
        : cleanNote.isNotEmpty
        ? cleanNote
        : '书签：${document.title}';
    final parsedProgress = annotation.location.startsWith('progress:')
        ? double.tryParse(annotation.location.substring('progress:'.length))
        : null;
    final location = parsedProgress == null || !parsedProgress.isFinite
        ? index + 1
        : (parsedProgress.clamp(0, 1) * 1000000).round();
    final managed = <String, Object?>{
      'text': _bounded(text, 8191),
      'note': _bounded(cleanNote, 8191),
      'location': location,
      'location_type': parsedProgress == null ? 'order' : 'location',
    };
    final documentKey = _stableId(document.id);
    final annotationKey = _stableId('${document.id}\u0000${annotation.id}');
    return <String, Object?>{
      'managed': managed,
      'create': <String, Object?>{
        ...managed,
        'title': _bounded(
          AnnotationMarkdownRenderer.singleLine(document.title),
          511,
        ),
        'author': _bounded(
          AnnotationMarkdownRenderer.singleLine(document.author),
          1024,
        ),
        'source_type': 'oohstory',
        'category': 'books',
        'source_url': '${ApiService.baseUrl}/app/#readwise-book-$documentKey',
        'highlight_url':
            '${ApiService.baseUrl}/app/#readwise-highlight-$annotationKey',
        if (annotation.createdAt != null)
          'highlighted_at': annotation.createdAt!.toUtc().toIso8601String(),
      },
    };
  }

  static String _bounded(String value, int maxRunes) {
    final normalized = value.runes
        .where((rune) => rune >= 0x20 || rune == 0x0a || rune == 0x09)
        .take(maxRunes);
    return String.fromCharCodes(normalized);
  }

  static String _stableId(String value) =>
      sha256.convert(utf8.encode(value)).toString().substring(0, 32);

  static void _validateInput(
    DocumentIdentity document,
    List<Annotation> annotations,
    String idempotencyKey,
  ) {
    if (document.id.trim().isEmpty ||
        document.title.trim().isEmpty ||
        document.documentVersion.trim().isEmpty ||
        idempotencyKey.trim().isEmpty ||
        annotations.isEmpty) {
      throw const FormatException('Readwise 导出数据不完整');
    }
    if (annotations.any(
      (annotation) =>
          annotation.id.trim().isEmpty || annotation.bookId != document.id,
    )) {
      throw const FormatException('批注与书籍标识不匹配');
    }
    if (annotations.map((annotation) => annotation.id).toSet().length !=
        annotations.length) {
      throw const FormatException('批注标识不可重复');
    }
  }
}

final class ReadwiseExportBatchResult {
  const ReadwiseExportBatchResult({
    required this.receipts,
    required this.conflicts,
  });

  final List<ExportReceipt> receipts;
  final List<ReadwiseExportConflict> conflicts;
}

final class ReadwiseAnnotationExportService {
  const ReadwiseAnnotationExportService(this._exporter);

  final ReadwiseAnnotationExporter _exporter;

  Future<ReadwiseExportBatchResult> exportDocuments(
    Iterable<AnnotationExportDocument> documents, {
    bool overwriteExternalChanges = false,
  }) async {
    final receipts = <ExportReceipt>[];
    final conflicts = <ReadwiseExportConflict>[];
    for (final document in documents) {
      try {
        receipts.add(
          await _exporter.export(
            document.identity,
            document.annotations,
            idempotencyKey: _idempotencyKey(document),
            overwriteExternalChanges: overwriteExternalChanges,
          ),
        );
      } on ReadwiseExportConflict catch (conflict) {
        conflicts.add(conflict);
      }
    }
    return ReadwiseExportBatchResult(
      receipts: List<ExportReceipt>.unmodifiable(receipts),
      conflicts: List<ReadwiseExportConflict>.unmodifiable(conflicts),
    );
  }

  static String _idempotencyKey(AnnotationExportDocument document) {
    final annotationIds = document.annotations.map((item) => item.id).toList()
      ..sort();
    return sha256
        .convert(
          utf8.encode(
            '${document.identity.id}\u0000${annotationIds.join('\u0000')}',
          ),
        )
        .toString();
  }
}

String _hashJson(Map<String, Object?> value) =>
    sha256.convert(utf8.encode(jsonEncode(value))).toString();
