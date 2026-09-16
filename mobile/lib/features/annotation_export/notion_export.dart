import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../adapters/cloud/cloud.dart';
import '../../adapters/contracts/adapter_contracts.dart';
import '../../core/core.dart';
import 'annotation_documents.dart';
import 'annotation_markdown.dart';

enum NotionParentKind { page, dataSource }

final class NotionExportConfiguration {
  NotionExportConfiguration({
    required this.parentKind,
    required String parentId,
    String titleProperty = 'Name',
  }) : parentId = normalizeNotionId(parentId),
       titleProperty = _validateTitleProperty(parentKind, titleProperty);

  final NotionParentKind parentKind;
  final String parentId;
  final String titleProperty;

  String get parentKey => sha256
      .convert(
        utf8.encode('${parentKind.name}\u0000$parentId\u0000$titleProperty'),
      )
      .toString();

  factory NotionExportConfiguration.fromJson(Map<String, Object?> json) =>
      NotionExportConfiguration(
        parentKind: NotionParentKind.values.byName(
          json['parent_kind'] as String,
        ),
        parentId: json['parent_id'] as String,
        titleProperty: json['title_property'] as String? ?? 'Name',
      );

  Map<String, Object?> toJson() => <String, Object?>{
    'parent_kind': parentKind.name,
    'parent_id': parentId,
    'title_property': titleProperty,
  };

  static String _validateTitleProperty(NotionParentKind kind, String value) {
    if (kind == NotionParentKind.page) return 'title';
    final trimmed = value.trim();
    if (trimmed.isEmpty ||
        trimmed.runes.length > 200 ||
        trimmed.runes.any((rune) => rune < 0x20 || rune == 0x7f)) {
      throw const FormatException('Notion 标题字段名称无效');
    }
    return trimmed;
  }
}

String normalizeNotionId(String value) {
  final compact = value.trim().replaceAll('-', '').toLowerCase();
  if (!RegExp(r'^[0-9a-f]{32}$').hasMatch(compact)) {
    throw const FormatException('Notion 目标 ID 必须是 32 位 UUID');
  }
  return '${compact.substring(0, 8)}-'
      '${compact.substring(8, 12)}-'
      '${compact.substring(12, 16)}-'
      '${compact.substring(16, 20)}-'
      '${compact.substring(20)}';
}

final class NotionConnectionRepository {
  NotionConnectionRepository({
    required SharedPreferences preferences,
    required SecureCredentialStore credentialStore,
  }) : _preferences = preferences,
       _credentialStore = credentialStore;

  static const _configurationKey = 'oohstory_notion_connection_v1';
  static const _tokenKey = 'oohstory.integration.notion.access_token';
  final SharedPreferences _preferences;
  final SecureCredentialStore _credentialStore;

  NotionExportConfiguration? loadConfiguration() {
    final raw = _preferences.getString(_configurationKey);
    if (raw == null) return null;
    try {
      return NotionExportConfiguration.fromJson(
        Map<String, Object?>.from(jsonDecode(raw) as Map),
      );
    } on Object {
      return null;
    }
  }

  Future<bool> hasAccessToken() async =>
      (await _credentialStore.read(_tokenKey))?.isNotEmpty ?? false;

  Future<String> requireAccessToken() async {
    final token = await _credentialStore.read(_tokenKey);
    if (token == null || token.isEmpty) {
      throw const CoreException(CoreErrorCode.unauthorized, 'Notion 授权令牌不可用');
    }
    return _validateToken(token);
  }

  Future<void> save(
    NotionExportConfiguration configuration, {
    String? accessToken,
  }) async {
    final candidate = accessToken?.trim();
    final previous = candidate == null || candidate.isEmpty
        ? null
        : await _credentialStore.read(_tokenKey);
    if (candidate != null && candidate.isNotEmpty) {
      await _credentialStore.write(_tokenKey, _validateToken(candidate));
    } else if (!await hasAccessToken()) {
      throw const FormatException('请输入 Notion 授权令牌');
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

  static String _validateToken(String value) {
    if (value.length < 16 ||
        value.length > 4096 ||
        value.runes.any((rune) => rune < 0x20 || rune == 0x7f)) {
      throw const FormatException('Notion 授权令牌格式无效');
    }
    return value;
  }
}

final class NotionExportState {
  NotionExportState({
    required this.parentKey,
    required this.documentId,
    required String pageId,
    required this.localContentHash,
    required this.remoteContentHash,
  }) : pageId = normalizeNotionId(pageId) {
    if (parentKey.isEmpty || documentId.trim().isEmpty) {
      throw const FormatException('Notion 导出状态标识无效');
    }
    for (final hash in <String>[localContentHash, remoteContentHash]) {
      if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(hash)) {
        throw const FormatException('Notion 导出状态哈希无效');
      }
    }
  }

  final String parentKey;
  final String documentId;
  final String pageId;
  final String localContentHash;
  final String remoteContentHash;

  factory NotionExportState.fromJson(Map<String, Object?> json) =>
      NotionExportState(
        parentKey: json['parent_key'] as String,
        documentId: json['document_id'] as String,
        pageId: json['page_id'] as String,
        localContentHash: json['local_content_hash'] as String,
        remoteContentHash: json['remote_content_hash'] as String,
      );

  Map<String, Object?> toJson() => <String, Object?>{
    'parent_key': parentKey,
    'document_id': documentId,
    'page_id': pageId,
    'local_content_hash': localContentHash,
    'remote_content_hash': remoteContentHash,
  };
}

abstract interface class NotionExportStateStore {
  Future<NotionExportState?> read({
    required String parentKey,
    required String documentId,
  });

  Future<void> write(NotionExportState state);
  Future<void> removeParent(String parentKey);
}

final class SharedPreferencesNotionExportStateStore
    implements NotionExportStateStore {
  SharedPreferencesNotionExportStateStore(this._preferences);

  static const _storageKey = 'oohstory_notion_export_states_v1';
  final SharedPreferences _preferences;

  @override
  Future<NotionExportState?> read({
    required String parentKey,
    required String documentId,
  }) async {
    final raw = _readAll()[_stateKey(parentKey, documentId)];
    if (raw is! Map) return null;
    try {
      final state = NotionExportState.fromJson(Map<String, Object?>.from(raw));
      if (state.parentKey != parentKey || state.documentId != documentId) {
        return null;
      }
      return state;
    } on Object {
      return null;
    }
  }

  @override
  Future<void> write(NotionExportState state) async {
    final states = _readAll();
    states[_stateKey(state.parentKey, state.documentId)] = state.toJson();
    await _preferences.setString(_storageKey, jsonEncode(states));
  }

  @override
  Future<void> removeParent(String parentKey) async {
    final states = _readAll()
      ..removeWhere(
        (_, value) =>
            value is Map && value['parent_key']?.toString() == parentKey,
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

  String _stateKey(String parentKey, String documentId) =>
      sha256.convert(utf8.encode('$parentKey\u0000$documentId')).toString();
}

final class NotionApiClient {
  NotionApiClient({
    required CloudHttpTransport transport,
    required Future<String> Function() accessToken,
    RetryPolicy retryPolicy = const RetryPolicy(),
    Future<void> Function(Duration delay)? sleep,
  }) : _transport = transport,
       _accessToken = accessToken,
       _retryPolicy = retryPolicy,
       _sleep = sleep;

  static final Uri _baseUri = Uri.parse('https://api.notion.com');
  static const apiVersion = '2026-03-11';
  static const _maxMarkdownBytes = 1024 * 1024;

  final CloudHttpTransport _transport;
  final Future<String> Function() _accessToken;
  final RetryPolicy _retryPolicy;
  final Future<void> Function(Duration delay)? _sleep;

  Future<String> createPage({
    required NotionExportConfiguration configuration,
    required String title,
    required String markdown,
  }) async {
    _validateMarkdown(markdown);
    final titleText = _boundedTitle(title);
    final titleValue = <String, Object?>{
      'type': 'title',
      'title': <Object?>[
        <String, Object?>{
          'type': 'text',
          'text': <String, Object?>{'content': titleText},
        },
      ],
    };
    final body = <String, Object?>{
      'parent': configuration.parentKind == NotionParentKind.page
          ? <String, Object?>{
              'type': 'page_id',
              'page_id': configuration.parentId,
            }
          : <String, Object?>{
              'type': 'data_source_id',
              'data_source_id': configuration.parentId,
            },
      'properties': configuration.parentKind == NotionParentKind.page
          ? <String, Object?>{'title': titleValue}
          : <String, Object?>{configuration.titleProperty: titleValue},
      'markdown': markdown,
    };
    // Creation is deliberately not retried: an ambiguous timeout could create
    // duplicate pages if POST were replayed.
    final payload = await _request(
      'POST',
      '/v1/pages',
      body: body,
      safeToRetry: false,
    );
    final id = payload['id'];
    if (id is! String) _invalidResponse();
    return normalizeNotionId(id);
  }

  Future<String> readMarkdown(String pageId) async {
    final payload = await _request(
      'GET',
      '/v1/pages/${normalizeNotionId(pageId)}/markdown',
    );
    final markdown = payload['markdown'];
    if (markdown is! String) _invalidResponse();
    _validateMarkdown(markdown);
    return markdown;
  }

  Future<String> replaceMarkdown(String pageId, String markdown) async {
    _validateMarkdown(markdown);
    final payload = await _request(
      'PATCH',
      '/v1/pages/${normalizeNotionId(pageId)}/markdown',
      body: <String, Object?>{
        'type': 'replace_content',
        'replace_content': <String, Object?>{
          'new_str': markdown,
          'allow_deleting_content': false,
        },
      },
    );
    final returnedMarkdown = payload['markdown'];
    if (returnedMarkdown is String) {
      _validateMarkdown(returnedMarkdown);
      return returnedMarkdown;
    }
    return readMarkdown(pageId);
  }

  Future<Map<String, Object?>> _request(
    String method,
    String path, {
    Map<String, Object?>? body,
    bool safeToRetry = true,
  }) async {
    final token = await _accessToken();
    if (token.length < 16 ||
        token.length > 4096 ||
        token.runes.any((rune) => rune < 0x20 || rune == 0x7f)) {
      throw const CoreException(CoreErrorCode.unauthorized, 'Notion 授权令牌格式无效');
    }
    final encoded = body == null ? null : utf8.encode(jsonEncode(body));
    if (encoded != null && encoded.length > _maxMarkdownBytes + 64 * 1024) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Notion 请求超过本地安全上限',
      );
    }
    final request = CloudHttpRequest(
      method: method,
      uri: _baseUri.resolve(path),
      headers: <String, String>{
        'accept': 'application/json',
        'authorization': 'Bearer $token',
        'notion-version': apiVersion,
        if (encoded != null) 'content-type': 'application/json',
      },
      bodyFactory: encoded == null
          ? null
          : () => Stream<List<int>>.value(encoded),
      contentLength: encoded?.length,
    );
    final response = safeToRetry
        ? await _retryPolicy.send(_transport, request, sleep: _sleep)
        : await _transport.send(request);
    if (response.statusCode < 200 || response.statusCode >= 300) {
      await response.body.drain<void>();
      throw cloudStatusError(response.statusCode);
    }
    return decodeJsonObject(response);
  }

  static String _boundedTitle(String title) {
    final normalized = AnnotationMarkdownRenderer.singleLine(title);
    final value = normalized.isEmpty ? '未命名书籍' : normalized;
    return String.fromCharCodes(value.runes.take(2000));
  }

  static void _validateMarkdown(String markdown) {
    if (utf8.encode(markdown).length > _maxMarkdownBytes) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        '单本书的 Notion 导出不能超过 1 MB',
      );
    }
  }

  static Never _invalidResponse() =>
      throw const CoreException(CoreErrorCode.upstreamError, 'Notion 返回了无效响应');
}

final class NotionExportConflict implements Exception {
  const NotionExportConflict({
    required this.document,
    required this.pageId,
    required this.reason,
  });

  final DocumentIdentity document;
  final String pageId;
  final String reason;

  @override
  String toString() => 'Notion export conflict for ${document.id}: $reason';
}

final class NotionAnnotationExporter implements AnnotationSink {
  NotionAnnotationExporter({
    required this.configuration,
    required NotionApiClient api,
    required NotionExportStateStore stateStore,
    AnnotationMarkdownRenderer renderer = const AnnotationMarkdownRenderer(),
    DateTime Function()? now,
  }) : _api = api,
       _stateStore = stateStore,
       _renderer = renderer,
       _now = now ?? (() => DateTime.now().toUtc());

  final NotionExportConfiguration configuration;
  final NotionApiClient _api;
  final NotionExportStateStore _stateStore;
  final AnnotationMarkdownRenderer _renderer;
  final DateTime Function() _now;

  @override
  String get providerId => 'notion';

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
    final markdown = _renderer.render(document, annotations);
    final localHash = _hash(markdown);
    final state = await _stateStore.read(
      parentKey: configuration.parentKey,
      documentId: document.id,
    );
    if (state == null) {
      final pageId = await _api.createPage(
        configuration: configuration,
        title: document.title,
        markdown: markdown,
      );
      // Persist the acknowledged page ID before the verification read. If the
      // read fails, a retry must return to this page instead of creating a
      // duplicate. The local hash is a conservative provisional baseline;
      // Notion normalization can then surface as a conflict rather than data
      // loss or duplication.
      await _writeState(
        documentId: document.id,
        pageId: pageId,
        localHash: localHash,
        remoteHash: localHash,
      );
      final remoteMarkdown = await _api.readMarkdown(pageId);
      await _writeState(
        documentId: document.id,
        pageId: pageId,
        localHash: localHash,
        remoteHash: _hash(remoteMarkdown),
      );
      return _receipt(document, pageId, localHash, ExportDisposition.created);
    }

    final remoteMarkdown = await _api.readMarkdown(state.pageId);
    final remoteHash = _hash(remoteMarkdown);
    final externallyModified = remoteHash != state.remoteContentHash;
    if (externallyModified && !overwriteExternalChanges) {
      throw NotionExportConflict(
        document: document,
        pageId: state.pageId,
        reason: 'Notion 页面在上次导出后被修改',
      );
    }
    if (!externallyModified && localHash == state.localContentHash) {
      return _receipt(
        document,
        state.pageId,
        localHash,
        ExportDisposition.unchanged,
      );
    }

    final updatedMarkdown = await _api.replaceMarkdown(state.pageId, markdown);
    await _writeState(
      documentId: document.id,
      pageId: state.pageId,
      localHash: localHash,
      remoteHash: _hash(updatedMarkdown),
    );
    return _receipt(
      document,
      state.pageId,
      localHash,
      externallyModified
          ? ExportDisposition.overwritten
          : ExportDisposition.updated,
    );
  }

  Future<void> _writeState({
    required String documentId,
    required String pageId,
    required String localHash,
    required String remoteHash,
  }) => _stateStore.write(
    NotionExportState(
      parentKey: configuration.parentKey,
      documentId: documentId,
      pageId: pageId,
      localContentHash: localHash,
      remoteContentHash: remoteHash,
    ),
  );

  ExportReceipt _receipt(
    DocumentIdentity document,
    String pageId,
    String contentHash,
    ExportDisposition disposition,
  ) => ExportReceipt(
    providerId: providerId,
    documentId: document.id,
    target: 'notion:page:$pageId',
    contentHash: contentHash,
    exportedAt: _now().toUtc(),
    disposition: disposition,
  );

  static String _hash(String value) =>
      sha256.convert(utf8.encode(value)).toString();

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
      throw const FormatException('Notion 导出数据不完整');
    }
    if (annotations.any(
      (annotation) =>
          annotation.id.trim().isEmpty || annotation.bookId != document.id,
    )) {
      throw const FormatException('批注与书籍标识不匹配');
    }
  }
}

final class NotionExportBatchResult {
  const NotionExportBatchResult({
    required this.receipts,
    required this.conflicts,
  });

  final List<ExportReceipt> receipts;
  final List<NotionExportConflict> conflicts;
}

final class NotionAnnotationExportService {
  const NotionAnnotationExportService(this._exporter);

  final NotionAnnotationExporter _exporter;

  Future<NotionExportBatchResult> exportDocuments(
    Iterable<AnnotationExportDocument> documents, {
    bool overwriteExternalChanges = false,
  }) async {
    final receipts = <ExportReceipt>[];
    final conflicts = <NotionExportConflict>[];
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
      } on NotionExportConflict catch (conflict) {
        conflicts.add(conflict);
      }
    }
    return NotionExportBatchResult(
      receipts: List<ExportReceipt>.unmodifiable(receipts),
      conflicts: List<NotionExportConflict>.unmodifiable(conflicts),
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
