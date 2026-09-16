import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../core/capabilities.dart';
import '../../core/errors.dart';
import '../../core/models.dart';
import '../contracts/adapter_contracts.dart';

typedef ProgressAuthHeaders = FutureOr<Map<String, String>> Function();

class ProgressTransportException extends CoreException {
  const ProgressTransportException(
    super.code,
    super.message, {
    super.correlationId,
    this.current,
    this.statusCode,
  });

  final ProgressRecord? current;
  final int? statusCode;
}

/// HTTP implementation of OOHStory progress contract v1.
///
/// The transport never persists bearer tokens. Callers provide fresh headers
/// for every request, allowing account logout and token rotation to take effect
/// without recreating the transport.
class OohStoryProgressTransport implements ProgressTransport {
  OohStoryProgressTransport({
    required Uri baseUri,
    required ProgressAuthHeaders authHeaders,
    http.Client? client,
    this.maxResponseBytes = 2 * 1024 * 1024,
    this.requestTimeout = const Duration(seconds: 20),
  }) : baseUri = _validateBaseUri(baseUri),
       _authHeaders = authHeaders,
       _client = client ?? http.Client() {
    if (maxResponseBytes <= 0) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Progress response limit must be positive',
      );
    }
    if (requestTimeout <= Duration.zero) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Progress request timeout must be positive',
      );
    }
  }

  static const contractId = 'CONTRACT-20260823-001';
  static const contractVersion = '1.0.0';

  final Uri baseUri;
  final ProgressAuthHeaders _authHeaders;
  final http.Client _client;
  final int maxResponseBytes;
  final Duration requestTimeout;

  @override
  String get providerId => 'oohstory-progress-v1';

  @override
  ProviderCapabilities get capabilities => ProviderCapabilities(
    providerId: providerId,
    supported: const <AdapterCapability>[AdapterCapability.progressSync],
  );

  Future<Map<String, Object?>> fetchCapabilities() async {
    final value = await _request('GET', _endpoint('capabilities'));
    if (value['contract_id'] != contractId ||
        value['contract_version'] != contractVersion) {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'Progress server contract is incompatible',
      );
    }
    return value;
  }

  @override
  Future<SyncPage<ProgressRecord>> pull({String? cursor, int? limit}) async {
    if (cursor != null && (cursor.isEmpty || cursor.length > 2048)) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Progress cursor is invalid',
      );
    }
    final pageSize = limit ?? 100;
    if (pageSize < 1 || pageSize > 100) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Progress page size must be between 1 and 100',
      );
    }
    final value = await _request(
      'GET',
      _endpoint(
        'progress',
        queryParameters: <String, String>{
          if (cursor != null) 'cursor': cursor,
          'limit': '$pageSize',
        },
      ),
    );
    try {
      final rawItems = value['items'];
      if (rawItems is! List<Object?>) throw const FormatException();
      final items = <ProgressRecord>[
        for (final item in rawItems)
          ProgressRecord.fromJson(Map<String, Object?>.from(item! as Map)),
      ];
      final nextCursor = value['next_cursor'];
      if (nextCursor != null && nextCursor is! String) {
        throw const FormatException();
      }
      final serverTime = DateTime.parse(value['server_time']! as String);
      if (!serverTime.isUtc) throw const FormatException();
      return SyncPage<ProgressRecord>(
        items: List<ProgressRecord>.unmodifiable(items),
        nextCursor: nextCursor as String?,
        serverTime: serverTime,
      );
    } on CoreException {
      rethrow;
    } on Object {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Progress page payload is invalid',
      );
    }
  }

  @override
  Future<ProgressRecord> put(ProgressRecord record, {int? ifMatch}) async {
    if (ifMatch != null && ifMatch < 0) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Progress If-Match revision must be nonnegative',
      );
    }
    final value = await _request(
      'PUT',
      _endpoint('progress', bookId: record.bookId),
      body: record.toJson(),
      ifMatch: ifMatch,
    );
    return _progress(value);
  }

  @override
  Future<ProgressRecord> delete(String bookId, {required int ifMatch}) async {
    if (bookId.trim().isEmpty || ifMatch < 0) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Progress delete precondition is invalid',
      );
    }
    final value = await _request(
      'DELETE',
      _endpoint('progress', bookId: bookId),
      ifMatch: ifMatch,
    );
    return _progress(value);
  }

  void close() => _client.close();

  Uri _endpoint(
    String resource, {
    String? bookId,
    Map<String, String>? queryParameters,
  }) => baseUri.replace(
    pathSegments: <String>[
      'api',
      'v1',
      'sync',
      resource,
      if (bookId != null) bookId,
    ],
    queryParameters: queryParameters,
    fragment: '',
  );

  Future<Map<String, Object?>> _request(
    String method,
    Uri uri, {
    Map<String, Object?>? body,
    int? ifMatch,
  }) async {
    final request = http.Request(method, uri);
    request.headers.addAll(<String, String>{
      'Accept': 'application/json',
      ...await _authHeaders(),
      if (body != null) 'Content-Type': 'application/json',
      if (ifMatch != null) 'If-Match': '"$ifMatch"',
    });
    if (body != null) request.body = jsonEncode(body);

    late final http.StreamedResponse response;
    try {
      response = await _client.send(request).timeout(requestTimeout);
    } on TimeoutException {
      throw const ProgressTransportException(
        CoreErrorCode.upstreamError,
        'Progress request timed out',
      );
    } on CoreException {
      rethrow;
    } on Object {
      throw const ProgressTransportException(
        CoreErrorCode.upstreamError,
        'Progress server is unavailable',
      );
    }

    final bytes = <int>[];
    await for (final chunk in response.stream) {
      if (bytes.length + chunk.length > maxResponseBytes) {
        throw ProgressTransportException(
          CoreErrorCode.payloadTooLarge,
          'Progress response is too large',
          statusCode: response.statusCode,
        );
      }
      bytes.addAll(chunk);
    }
    final contentType = response.headers['content-type'] ?? '';
    if (!contentType.toLowerCase().startsWith('application/json')) {
      throw ProgressTransportException(
        CoreErrorCode.upstreamError,
        'Progress server returned an unexpected content type',
        statusCode: response.statusCode,
      );
    }

    Map<String, Object?> value;
    try {
      final decoded = jsonDecode(utf8.decode(bytes));
      if (decoded is! Map) throw const FormatException();
      value = Map<String, Object?>.from(decoded);
    } on Object {
      throw ProgressTransportException(
        CoreErrorCode.upstreamError,
        'Progress server returned invalid JSON',
        statusCode: response.statusCode,
      );
    }
    if (response.statusCode >= 200 && response.statusCode < 300) return value;

    final code = _errorCode(response.statusCode, value['error']);
    ProgressRecord? current;
    try {
      final rawCurrent = value['current'];
      if (rawCurrent is Map) {
        current = ProgressRecord.fromJson(
          Map<String, Object?>.from(rawCurrent),
        );
      }
    } on Object {
      current = null;
    }
    throw ProgressTransportException(
      code,
      value['message'] is String
          ? value['message']! as String
          : 'Progress request failed',
      correlationId: value['correlation_id'] as String?,
      current: current,
      statusCode: response.statusCode,
    );
  }

  ProgressRecord _progress(Map<String, Object?> value) {
    try {
      return ProgressRecord.fromJson(value);
    } on CoreException {
      rethrow;
    } on Object {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Progress record payload is invalid',
      );
    }
  }
}

Uri _validateBaseUri(Uri value) {
  final loopback = value.host == 'localhost' || value.host == '127.0.0.1';
  if (!value.hasAuthority ||
      value.host.isEmpty ||
      (value.scheme != 'https' && !(loopback && value.scheme == 'http')) ||
      value.userInfo.isNotEmpty ||
      value.query.isNotEmpty ||
      value.fragment.isNotEmpty) {
    throw const CoreException(
      CoreErrorCode.validationError,
      'Progress base URI must be an HTTPS origin',
    );
  }
  return value.replace(path: '', query: '', fragment: '');
}

CoreErrorCode _errorCode(int statusCode, Object? wireCode) {
  for (final value in CoreErrorCode.values) {
    if (value.wireName == wireCode) return value;
  }
  return switch (statusCode) {
    400 => CoreErrorCode.validationError,
    401 => CoreErrorCode.unauthorized,
    403 => CoreErrorCode.forbidden,
    404 => CoreErrorCode.notFound,
    409 => CoreErrorCode.revisionConflict,
    413 => CoreErrorCode.payloadTooLarge,
    429 => CoreErrorCode.rateLimitExceeded,
    500 => CoreErrorCode.internalError,
    _ => CoreErrorCode.upstreamError,
  };
}
