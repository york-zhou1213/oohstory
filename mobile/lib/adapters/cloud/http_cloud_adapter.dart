import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

import '../../core/capabilities.dart';
import '../../core/errors.dart';
import '../contracts/adapter_contracts.dart';
import 'cloud_support.dart';

abstract base class HttpCloudLibraryAdapter implements CloudLibraryAdapter {
  HttpCloudLibraryAdapter({
    required this.providerId,
    required this.endpoint,
    required String root,
    required this.transport,
    CloudRuntimePolicy? runtimePolicy,
    this.retryPolicy = const RetryPolicy(),
    this.cancellationToken,
    this.logger = const SafeCloudLogger(),
    this.maxUploadBytes = 128 * 1024 * 1024,
  }) : runtimePolicy = runtimePolicy ?? CloudRuntimePolicy.current(),
       root = CloudRoot(root) {
    this.runtimePolicy.validate(endpoint);
  }

  @override
  final String providerId;
  final Uri endpoint;
  final CloudRoot root;
  final CloudHttpTransport transport;
  final CloudRuntimePolicy runtimePolicy;
  final RetryPolicy retryPolicy;
  final CancellationToken? cancellationToken;
  final SafeCloudLogger logger;
  final int maxUploadBytes;

  @override
  ProviderCapabilities get capabilities => ProviderCapabilities(
    providerId: providerId,
    supported: const <AdapterCapability>[AdapterCapability.cloudLibrary],
  );

  Uri endpointWithSegments(
    Iterable<String> segments, {
    Map<String, String>? queryParameters,
  }) {
    final baseSegments = endpoint.pathSegments.where(
      (segment) => segment.isNotEmpty,
    );
    return endpoint.replace(
      pathSegments: <String>[...baseSegments, ...segments],
      queryParameters: queryParameters,
    );
  }

  Future<CloudHttpResponse> send(
    CloudHttpRequest request, {
    Future<Map<String, String>> Function()? authorizationHeaders,
    Future<bool> Function()? refreshAuthorization,
    RetryPolicy? requestRetryPolicy,
  }) async {
    runtimePolicy.validate(request.uri);
    cancellationToken?.throwIfCancelled();
    var authorized = request;
    if (authorizationHeaders != null) {
      authorized = request.copyWith(
        headers: <String, String>{
          ...request.headers,
          ...await authorizationHeaders(),
        },
      );
    }
    logger.event('cloud request', <String, Object?>{
      'provider': providerId,
      'method': authorized.method,
      'uri': authorized.uri,
    });
    final effectiveRetryPolicy = requestRetryPolicy ?? retryPolicy;
    var response = await effectiveRetryPolicy.send(
      transport,
      authorized,
      cancellationToken: cancellationToken,
    );
    if (response.statusCode == 401 && refreshAuthorization != null) {
      await response.body.drain<void>();
      if (await refreshAuthorization()) {
        authorized = request.copyWith(
          headers: <String, String>{
            ...request.headers,
            if (authorizationHeaders != null) ...await authorizationHeaders(),
          },
        );
        response = await effectiveRetryPolicy.send(
          transport,
          authorized,
          cancellationToken: cancellationToken,
        );
      }
    }
    return response;
  }

  Future<Uint8List> uploadBytes(Stream<List<int>> source) => collectBytes(
    source,
    maxBytes: maxUploadBytes,
    cancellationToken: cancellationToken,
  );

  Stream<List<int>> repeatableBody(Uint8List bytes) =>
      Stream<List<int>>.value(bytes);

  String? validateCursor(String? cursor) {
    if (cursor != null &&
        (cursor.length > 4096 || cursor.runes.any((rune) => rune < 0x20))) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Cloud provider cursor is invalid',
      );
    }
    return cursor;
  }

  String idempotencyKey(String method, String path, List<int> bytes) =>
      sha256.convert(<int>[
        ...utf8.encode('$providerId\u0000$method\u0000$path\u0000'),
        ...bytes,
      ]).toString();

  Future<void> expectStatus(
    CloudHttpResponse response,
    Set<int> allowed,
  ) async {
    if (allowed.contains(response.statusCode)) return;
    await response.body.drain<void>();
    throw cloudStatusError(response.statusCode);
  }

  Never invalidProviderResponse() => throw const CoreException(
    CoreErrorCode.upstreamError,
    'Cloud provider returned an invalid response',
  );
}
