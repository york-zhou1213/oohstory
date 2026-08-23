@TestOn('vm')
library;

import 'dart:async';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:oohstory/adapters/cloud/cloud.dart';

void main() {
  group('PackageHttpTransport', () {
    late HttpServer server;
    late http.Client client;
    late PackageHttpTransport transport;

    setUp(() async {
      server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      client = http.Client();
      transport = PackageHttpTransport(client: client);
    });

    tearDown(() async {
      client.close();
      await server.close(force: true);
    });

    test('sends a bodyless GET to a real loopback server', () async {
      final received = server.first.timeout(const Duration(seconds: 1));
      final sent = transport.send(
        CloudHttpRequest(method: 'GET', uri: _serverUri(server, '/books')),
      );

      final request = await received;
      expect(request.method, 'GET');
      expect(request.uri.path, '/books');
      expect(await request.fold<List<int>>(<int>[], _append), isEmpty);
      request.response.statusCode = HttpStatus.noContent;
      await request.response.close();

      final response = await sent.timeout(const Duration(seconds: 1));
      expect(response.statusCode, HttpStatus.noContent);
    });

    test('streams a PUT body to a real loopback server', () async {
      final received = server.first.timeout(const Duration(seconds: 1));
      final sent = transport.send(
        CloudHttpRequest(
          method: 'PUT',
          uri: _serverUri(server, '/books/a.epub'),
          contentLength: 5,
          bodyFactory: () async* {
            yield <int>[1, 2];
            await Future<void>.delayed(Duration.zero);
            yield <int>[3, 4, 5];
          },
        ),
      );

      final request = await received;
      expect(request.method, 'PUT');
      expect(request.uri.path, '/books/a.epub');
      expect(await request.fold<List<int>>(<int>[], _append), <int>[
        1,
        2,
        3,
        4,
        5,
      ]);
      request.response.statusCode = HttpStatus.created;
      await request.response.close();

      final response = await sent.timeout(const Duration(seconds: 1));
      expect(response.statusCode, HttpStatus.created);
    });
  });

  test(
    'cancellation settles the request and cancels its body stream',
    () async {
      final bodyStarted = Completer<void>();
      final bodyCancelled = Completer<void>();
      final body = StreamController<List<int>>(
        onCancel: bodyCancelled.complete,
      );
      final token = CancellationToken();
      final sent = PackageHttpTransport(client: _CollectingClient()).send(
        CloudHttpRequest(
          method: 'PUT',
          uri: Uri.parse('https://fixture.test/upload'),
          bodyFactory: () {
            bodyStarted.complete();
            return body.stream;
          },
        ),
        cancellationToken: token,
      );

      await bodyStarted.future.timeout(const Duration(seconds: 1));
      token.cancel();

      await expectLater(
        sent.timeout(const Duration(seconds: 1)),
        throwsA(isA<CloudOperationCancelled>()),
      );
      await bodyCancelled.future.timeout(const Duration(seconds: 1));
    },
  );

  test('propagates a request body stream error', () async {
    final error = StateError('body failed');
    final sent = PackageHttpTransport(client: _CollectingClient()).send(
      CloudHttpRequest(
        method: 'PUT',
        uri: Uri.parse('https://fixture.test/upload'),
        bodyFactory: () async* {
          yield <int>[1];
          throw error;
        },
      ),
    );

    await expectLater(
      sent.timeout(const Duration(seconds: 1)),
      throwsA(same(error)),
    );
  });

  test('propagates transport errors without opening the body stream', () async {
    final error = StateError('transport failed');
    var bodyCreated = false;
    final sent = PackageHttpTransport(client: _FailingClient(error)).send(
      CloudHttpRequest(
        method: 'PUT',
        uri: Uri.parse('https://fixture.test/upload'),
        bodyFactory: () {
          bodyCreated = true;
          return const Stream<List<int>>.empty();
        },
      ),
    );

    await expectLater(
      sent.timeout(const Duration(seconds: 1)),
      throwsA(same(error)),
    );
    expect(bodyCreated, isFalse);
  });

  test('propagates transport errors after cancelling an active body', () async {
    final error = StateError('transport failed after listening');
    final bodyCancelled = Completer<void>();
    final body = StreamController<List<int>>(onCancel: bodyCancelled.complete)
      ..add(<int>[1]);
    final sent = PackageHttpTransport(client: _FailingAfterListenClient(error))
        .send(
          CloudHttpRequest(
            method: 'PUT',
            uri: Uri.parse('https://fixture.test/upload'),
            bodyFactory: () => body.stream,
          ),
        );

    await expectLater(
      sent.timeout(const Duration(seconds: 1)),
      throwsA(same(error)),
    );
    await bodyCancelled.future.timeout(const Duration(seconds: 1));
  });
}

Uri _serverUri(HttpServer server, String path) =>
    Uri.parse('http://${server.address.address}:${server.port}$path');

List<int> _append(List<int> bytes, List<int> chunk) => bytes..addAll(chunk);

final class _CollectingClient extends http.BaseClient {
  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    await request.finalize().drain<void>();
    return http.StreamedResponse(const Stream<List<int>>.empty(), 200);
  }
}

final class _FailingClient extends http.BaseClient {
  _FailingClient(this.error);

  final Object error;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    throw error;
  }
}

final class _FailingAfterListenClient extends http.BaseClient {
  _FailingAfterListenClient(this.error);

  final Object error;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    await for (final _ in request.finalize()) {
      throw error;
    }
    throw StateError('request body ended before transport failure');
  }
}
