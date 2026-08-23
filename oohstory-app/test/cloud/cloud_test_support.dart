import 'dart:typed_data';

import 'package:oohstory/adapters/cloud/cloud.dart';

final class MemoryCredentialStore implements SecureCredentialStore {
  MemoryCredentialStore([Map<String, String> values = const {}])
    : values = <String, String>{...values};

  final Map<String, String> values;
  final List<String> writes = <String>[];

  @override
  Future<String?> read(String key) async => values[key];

  @override
  Future<void> write(String key, String value) async {
    writes.add(key);
    values[key] = value;
  }

  @override
  Future<void> delete(String key) async {
    values.remove(key);
  }
}

typedef FixtureHandler =
    Future<CloudHttpResponse> Function(
      CloudHttpRequest request,
      Uint8List body,
    );

final class FixtureTransport implements CloudHttpTransport {
  FixtureTransport(this.handler);

  final FixtureHandler handler;
  final List<CloudHttpRequest> requests = <CloudHttpRequest>[];
  final List<Uint8List> bodies = <Uint8List>[];

  @override
  Future<CloudHttpResponse> send(
    CloudHttpRequest request, {
    CancellationToken? cancellationToken,
  }) async {
    cancellationToken?.throwIfCancelled();
    final body = request.bodyFactory == null
        ? Uint8List(0)
        : await collectBytes(
            request.bodyFactory!(),
            cancellationToken: cancellationToken,
          );
    requests.add(request);
    bodies.add(body);
    return handler(request, body);
  }
}

CloudHttpResponse jsonResponse(
  int status,
  String body, {
  Map<String, String> headers = const <String, String>{},
}) => CloudHttpResponse.bytes(
  statusCode: status,
  headers: <String, String>{'content-type': 'application/json', ...headers},
  body: body.codeUnits,
);
