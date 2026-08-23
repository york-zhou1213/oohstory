import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:http/http.dart' as http;

import '../../core/errors.dart';

final class CloudOperationCancelled implements Exception {
  const CloudOperationCancelled();

  @override
  String toString() => 'Cloud operation cancelled';
}

final class CancellationToken {
  final Completer<void> _cancelledCompleter = Completer<void>();
  bool _cancelled = false;

  bool get isCancelled => _cancelled;
  Future<void> get whenCancelled => _cancelledCompleter.future;

  void cancel() {
    if (_cancelled) return;
    _cancelled = true;
    _cancelledCompleter.complete();
  }

  void throwIfCancelled() {
    if (_cancelled) throw const CloudOperationCancelled();
  }
}

final class CloudHttpRequest {
  const CloudHttpRequest({
    required this.method,
    required this.uri,
    this.headers = const <String, String>{},
    this.bodyFactory,
    this.contentLength,
  });

  final String method;
  final Uri uri;
  final Map<String, String> headers;
  final Stream<List<int>> Function()? bodyFactory;
  final int? contentLength;

  CloudHttpRequest copyWith({
    Uri? uri,
    Map<String, String>? headers,
    Stream<List<int>> Function()? bodyFactory,
    int? contentLength,
  }) => CloudHttpRequest(
    method: method,
    uri: uri ?? this.uri,
    headers: headers ?? this.headers,
    bodyFactory: bodyFactory ?? this.bodyFactory,
    contentLength: contentLength ?? this.contentLength,
  );
}

final class CloudHttpResponse {
  CloudHttpResponse({
    required this.statusCode,
    Map<String, String> headers = const <String, String>{},
    Stream<List<int>>? body,
  }) : headers = <String, String>{
         for (final entry in headers.entries)
           entry.key.toLowerCase(): entry.value,
       },
       body = body ?? const Stream<List<int>>.empty();

  factory CloudHttpResponse.bytes({
    required int statusCode,
    Map<String, String> headers = const <String, String>{},
    List<int> body = const <int>[],
  }) => CloudHttpResponse(
    statusCode: statusCode,
    headers: headers,
    body: Stream<List<int>>.value(body),
  );

  final int statusCode;
  final Map<String, String> headers;
  final Stream<List<int>> body;

  String? header(String name) => headers[name.toLowerCase()];
}

abstract interface class CloudHttpTransport {
  Future<CloudHttpResponse> send(
    CloudHttpRequest request, {
    CancellationToken? cancellationToken,
  });
}

final class PackageHttpTransport implements CloudHttpTransport {
  PackageHttpTransport({http.Client? client})
    : _client = client ?? http.Client();

  final http.Client _client;

  @override
  Future<CloudHttpResponse> send(
    CloudHttpRequest request, {
    CancellationToken? cancellationToken,
  }) async {
    cancellationToken?.throwIfCancelled();
    final stopBody = Completer<void>();
    final stopSignal = cancellationToken == null
        ? stopBody.future
        : Future.any<void>(<Future<void>>[
            stopBody.future,
            cancellationToken.whenCancelled,
          ]);
    final outgoing = _CloudStreamedRequest(
      request.method,
      request.uri,
      abortTrigger: stopSignal,
    )..followRedirects = false;
    if (request.contentLength != null) {
      outgoing.contentLength = request.contentLength!;
    }
    outgoing.headers.addAll(request.headers);

    http.StreamedResponse? response;
    Object? responseError;
    StackTrace? responseStackTrace;
    final responseDone =
        Future<http.StreamedResponse>.sync(
          () => _client.send(outgoing),
        ).then<void>(
          (value) => response = value,
          onError: (Object error, StackTrace stackTrace) {
            responseError = error;
            responseStackTrace = stackTrace;
            if (!stopBody.isCompleted) stopBody.complete();
          },
        );

    await Future.any<void>(<Future<void>>[
      outgoing.whenListening,
      responseDone,
      stopSignal,
    ]);
    cancellationToken?.throwIfCancelled();
    if (responseError != null) {
      Error.throwWithStackTrace(responseError!, responseStackTrace!);
    }
    if (!outgoing.isListening) {
      throw StateError('HTTP client completed without consuming the request');
    }

    Object? bodyError;
    StackTrace? bodyStackTrace;
    final bodyDone =
        _writeRequestBody(
          outgoing,
          request.bodyFactory,
          stopSignal,
          cancellationToken,
          () {
            if (!stopBody.isCompleted) stopBody.complete();
          },
        ).then<void>(
          (_) {},
          onError: (Object error, StackTrace stackTrace) {
            bodyError = error;
            bodyStackTrace = stackTrace;
            if (!stopBody.isCompleted) stopBody.complete();
          },
        );

    await Future.wait<void>(<Future<void>>[responseDone, bodyDone]);
    cancellationToken?.throwIfCancelled();
    if (bodyError != null) {
      Error.throwWithStackTrace(bodyError!, bodyStackTrace!);
    }
    if (responseError != null) {
      Error.throwWithStackTrace(responseError!, responseStackTrace!);
    }
    return CloudHttpResponse(
      statusCode: response!.statusCode,
      headers: response!.headers,
      body: response!.stream.transform(
        StreamTransformer<List<int>, List<int>>.fromHandlers(
          handleData: (chunk, sink) {
            try {
              cancellationToken?.throwIfCancelled();
              sink.add(chunk);
            } on Object catch (error, stackTrace) {
              sink.addError(error, stackTrace);
            }
          },
          handleError: (error, stackTrace, sink) {
            if (error is http.RequestAbortedException &&
                error.uri == request.uri &&
                (cancellationToken?.isCancelled ?? false)) {
              sink.addError(const CloudOperationCancelled(), stackTrace);
              return;
            }
            sink.addError(error, stackTrace);
          },
        ),
      ),
    );
  }

  Future<void> _writeRequestBody(
    _CloudStreamedRequest outgoing,
    Stream<List<int>> Function()? bodyFactory,
    Future<void> stopSignal,
    CancellationToken? cancellationToken,
    void Function() stopRequest,
  ) async {
    Object? bodyError;
    StackTrace? bodyStackTrace;
    try {
      final body = bodyFactory?.call();
      if (body != null) {
        await outgoing.sink.addStream(_untilStopped(body, stopSignal));
      }
    } on Object catch (error, stackTrace) {
      bodyError = error;
      bodyStackTrace = stackTrace;
      stopRequest();
    }

    Object? closeError;
    StackTrace? closeStackTrace;
    final closeDone = outgoing.sink.close().then<void>(
      (_) {},
      onError: (Object error, StackTrace stackTrace) {
        closeError = error;
        closeStackTrace = stackTrace;
      },
    );
    await closeDone;
    cancellationToken?.throwIfCancelled();
    if (bodyError != null) {
      Error.throwWithStackTrace(bodyError, bodyStackTrace!);
    }
    if (closeError != null) {
      Error.throwWithStackTrace(closeError!, closeStackTrace!);
    }
  }

  Stream<List<int>> _untilStopped(
    Stream<List<int>> body,
    Future<void> stopSignal,
  ) async* {
    final iterator = StreamIterator<List<int>>(body);
    try {
      while (true) {
        final event = await Future.any<int>(<Future<int>>[
          iterator.moveNext().then((hasNext) => hasNext ? 1 : 0),
          stopSignal.then((_) => -1),
        ]);
        if (event <= 0) return;
        yield iterator.current;
      }
    } finally {
      await iterator.cancel();
    }
  }
}

final class _CloudStreamedRequest extends http.BaseRequest with http.Abortable {
  _CloudStreamedRequest(super.method, super.url, {this.abortTrigger});

  @override
  final Future<void>? abortTrigger;

  final Completer<void> _listening = Completer<void>();
  late final StreamController<List<int>> _body = StreamController<List<int>>(
    sync: true,
    onListen: () => _listening.complete(),
  );

  StreamSink<List<int>> get sink => _body.sink;
  Future<void> get whenListening => _listening.future;
  bool get isListening => _listening.isCompleted;

  @override
  http.ByteStream finalize() {
    super.finalize();
    return http.ByteStream(_body.stream);
  }
}

final class RetryPolicy {
  const RetryPolicy({
    this.maxAttempts = 3,
    this.initialDelay = const Duration(milliseconds: 100),
  }) : assert(maxAttempts > 0);

  final int maxAttempts;
  final Duration initialDelay;

  Future<CloudHttpResponse> send(
    CloudHttpTransport transport,
    CloudHttpRequest request, {
    CancellationToken? cancellationToken,
    Future<void> Function(Duration delay)? sleep,
  }) async {
    final wait = sleep ?? Future<void>.delayed;
    Object? lastError;
    for (var attempt = 1; attempt <= maxAttempts; attempt++) {
      cancellationToken?.throwIfCancelled();
      try {
        final response = await transport.send(
          request,
          cancellationToken: cancellationToken,
        );
        if (!_isTransient(response.statusCode) || attempt == maxAttempts) {
          return response;
        }
        await response.body.drain<void>();
        await _pause(_delayFor(attempt, response), wait, cancellationToken);
      } on CloudOperationCancelled {
        rethrow;
      } on Object catch (error) {
        cancellationToken?.throwIfCancelled();
        lastError = error;
        if (attempt == maxAttempts) break;
        await _pause(initialDelay * attempt, wait, cancellationToken);
      }
    }
    throw CoreException(
      CoreErrorCode.upstreamError,
      'Cloud provider request failed after $maxAttempts attempts',
      correlationId: lastError is CoreException
          ? lastError.correlationId
          : null,
    );
  }

  bool _isTransient(int status) =>
      status == 408 ||
      status == 429 ||
      status == 500 ||
      status == 502 ||
      status == 503 ||
      status == 504;

  Duration _delayFor(int attempt, CloudHttpResponse response) {
    final retryAfter = int.tryParse(response.header('retry-after') ?? '');
    return retryAfter == null
        ? initialDelay * attempt
        : Duration(seconds: retryAfter.clamp(0, 30));
  }

  Future<void> _pause(
    Duration delay,
    Future<void> Function(Duration delay) wait,
    CancellationToken? cancellationToken,
  ) async {
    if (cancellationToken == null) {
      await wait(delay);
      return;
    }
    await Future.any<void>(<Future<void>>[
      wait(delay),
      cancellationToken.whenCancelled,
    ]);
    cancellationToken.throwIfCancelled();
  }
}

final class CloudRoot {
  CloudRoot(String root) : segments = _normalize(root, allowEmpty: false);

  final List<String> segments;

  List<String> resolve(String path) => <String>[
    ...segments,
    ..._normalize(path, allowEmpty: true),
  ];

  String requireDescendant(String path) {
    final relative = _normalize(path, allowEmpty: true).join('/');
    if (relative.isEmpty) {
      throw const CoreException(
        CoreErrorCode.forbidden,
        'Cloud root cannot be read, overwritten, or deleted as a file',
      );
    }
    return relative;
  }

  String slashPath(String path, {bool leadingSlash = true}) {
    final joined = resolve(path).join('/');
    return leadingSlash ? '/$joined' : joined;
  }

  String relativeFromSegments(Iterable<String> providerSegments) {
    final candidate = <String>[];
    for (final segment in providerSegments) {
      if (segment.isEmpty) continue;
      if (segment == '.' ||
          segment == '..' ||
          segment.contains('/') ||
          segment.contains('\\') ||
          segment.runes.any((rune) => rune < 0x20)) {
        _outsideRoot();
      }
      if (segment.length > 255) _outsideRoot();
      candidate.add(segment);
    }
    if (candidate.join('/').length > 4096) _outsideRoot();
    if (candidate.length < segments.length) _outsideRoot();
    for (var index = 0; index < segments.length; index++) {
      if (candidate[index] != segments[index]) _outsideRoot();
    }
    return candidate.skip(segments.length).join('/');
  }

  static List<String> _normalize(String value, {required bool allowEmpty}) {
    if (value.length > 4096 ||
        value.contains('\\') ||
        value.runes.any((rune) => rune < 0x20)) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Cloud path contains an unsafe character',
      );
    }
    final result = <String>[];
    for (final segment in value.split('/')) {
      if (segment.isEmpty || segment == '.') continue;
      if (segment == '..') _outsideRoot();
      if (segment.length > 255) {
        throw const CoreException(
          CoreErrorCode.validationError,
          'Cloud path segment is too long',
        );
      }
      result.add(segment);
    }
    if (!allowEmpty && result.isEmpty) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Cloud root must not be empty',
      );
    }
    return List<String>.unmodifiable(result);
  }

  static Never _outsideRoot() => throw const CoreException(
    CoreErrorCode.forbidden,
    'Cloud path is outside the configured OOHStory root',
  );
}

final class CloudRuntimePolicy {
  const CloudRuntimePolicy({
    required this.isWeb,
    this.corsVerified = false,
    this.allowInsecureLoopback = false,
  });

  factory CloudRuntimePolicy.current({
    bool corsVerified = false,
    bool allowInsecureLoopback = false,
  }) => CloudRuntimePolicy(
    isWeb: kIsWeb,
    corsVerified: corsVerified,
    allowInsecureLoopback: allowInsecureLoopback,
  );

  final bool isWeb;
  final bool corsVerified;
  final bool allowInsecureLoopback;

  void validate(Uri endpoint) {
    if (endpoint.userInfo.isNotEmpty) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Cloud endpoint must not embed credentials',
      );
    }
    final secure = endpoint.scheme == 'https';
    final loopback =
        endpoint.host == 'localhost' ||
        endpoint.host == '127.0.0.1' ||
        endpoint.host == '::1';
    if (!secure && !(allowInsecureLoopback && loopback && !isWeb)) {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'Cloud provider requires HTTPS',
      );
    }
    if (isWeb && !corsVerified) {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'Cloud provider is unavailable on Web until HTTPS and CORS are verified',
      );
    }
  }
}

typedef CloudLogSink =
    void Function(String message, Map<String, Object?> fields);

final class SafeCloudLogger {
  const SafeCloudLogger([this._sink]);

  final CloudLogSink? _sink;

  static final RegExp _sensitiveKey = RegExp(
    r'(authorization|credential|password|secret|token|api[-_]?key)',
    caseSensitive: false,
  );

  void event(String message, Map<String, Object?> fields) {
    final safeFields = <String, Object?>{};
    for (final entry in fields.entries) {
      safeFields[entry.key] = _sensitiveKey.hasMatch(entry.key)
          ? '[REDACTED]'
          : _sanitize(entry.value);
    }
    _sink?.call(message, Map<String, Object?>.unmodifiable(safeFields));
  }

  Object? _sanitize(Object? value) {
    if (value is Uri) {
      return Uri(
        scheme: value.scheme,
        host: value.host,
        port: value.hasPort ? value.port : null,
        pathSegments: value.pathSegments,
      ).toString();
    }
    if (value is Map) {
      return <String, Object?>{
        for (final entry in value.entries)
          entry.key.toString(): _sensitiveKey.hasMatch(entry.key.toString())
              ? '[REDACTED]'
              : _sanitize(entry.value),
      };
    }
    return value;
  }
}

Future<Uint8List> collectBytes(
  Stream<List<int>> bytes, {
  int maxBytes = 128 * 1024 * 1024,
  CancellationToken? cancellationToken,
}) async {
  final builder = BytesBuilder(copy: false);
  await for (final chunk in bytes) {
    cancellationToken?.throwIfCancelled();
    if (chunk.length > maxBytes - builder.length) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'Cloud payload exceeds the configured upload limit',
      );
    }
    builder.add(chunk);
  }
  return builder.takeBytes();
}

Future<Uint8List> collectResponseBytes(
  CloudHttpResponse response, {
  int maxBytes = 4 * 1024 * 1024,
}) => collectBytes(response.body, maxBytes: maxBytes);

Future<Map<String, Object?>> decodeJsonObject(
  CloudHttpResponse response,
) async {
  try {
    final bytes = await collectResponseBytes(response);
    final value = jsonDecode(utf8.decode(bytes));
    if (value is Map<String, Object?>) return value;
  } on CoreException {
    rethrow;
  } on Object {
    // Provider payloads are never included in errors because they may contain
    // tokens or user-controlled cloud metadata.
  }
  throw const CoreException(
    CoreErrorCode.upstreamError,
    'Cloud provider returned an invalid response',
  );
}

CoreException cloudStatusError(int statusCode) {
  final code = switch (statusCode) {
    401 => CoreErrorCode.unauthorized,
    403 => CoreErrorCode.forbidden,
    404 => CoreErrorCode.notFound,
    409 || 412 => CoreErrorCode.revisionConflict,
    413 => CoreErrorCode.payloadTooLarge,
    429 => CoreErrorCode.rateLimitExceeded,
    _ => CoreErrorCode.upstreamError,
  };
  return CoreException(code, 'Cloud provider request failed ($statusCode)');
}

String normalizedEtag(String? value) {
  if (value == null) return '';
  final trimmed = value.trim();
  return trimmed.length >= 2 && trimmed.startsWith('"') && trimmed.endsWith('"')
      ? trimmed.substring(1, trimmed.length - 1)
      : trimmed;
}
