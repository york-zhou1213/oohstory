import 'dart:async';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:oohstory/services/authenticated_resource.dart';
import 'package:oohstory/services/bounded_stream.dart';
import 'package:oohstory/services/opds_catalog_service.dart';

class _SingleResponseClient extends http.BaseClient {
  _SingleResponseClient(this.response);

  final http.StreamedResponse response;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async =>
      response;
}

void main() {
  test('avatar bearer headers stay on the exact authenticated origin', () {
    const headers = <String, String>{'Authorization': 'Bearer test-token'};

    expect(
      oohstoryAuthenticatedResourceHeaders(
        'https://oohstory.com/media/avatar.png',
        headers,
      ),
      headers,
    );
    for (final hostile in const <String>[
      'http://oohstory.com/media/avatar.png',
      'https://cdn.oohstory.com/media/avatar.png',
      'https://oohstory.com.evil.example/avatar.png',
      'https://evil.example/avatar.png',
    ]) {
      expect(
        oohstoryAuthenticatedResourceHeaders(hostile, headers),
        isEmpty,
        reason: hostile,
      );
    }
  });

  test('chunked OPDS catalogs stop at 4 MB and cancel the response', () async {
    final cancelled = Completer<void>();
    late final StreamController<List<int>> body;
    body = StreamController<List<int>>(
      onListen: () {
        body.add(List<int>.filled(4 * 1024 * 1024, 32));
        body.add(const <int>[32]);
      },
      onCancel: () {
        if (!cancelled.isCompleted) cancelled.complete();
      },
    );
    addTearDown(() async {
      if (!body.isClosed) await body.close();
    });
    final service = OpdsCatalogService(
      client: _SingleResponseClient(http.StreamedResponse(body.stream, 200)),
    );
    addTearDown(service.close);

    await expectLater(
      service.fetch(Uri.parse('https://library.example/opds')),
      throwsA(
        isA<FormatException>().having(
          (error) => error.message,
          'message',
          contains('4 MB'),
        ),
      ),
    );
    await cancelled.future.timeout(const Duration(seconds: 1));
  });

  test('bounded backup reads cancel before buffering an extra chunk', () async {
    final cancelled = Completer<void>();
    late final StreamController<List<int>> body;
    body = StreamController<List<int>>(
      onListen: () {
        body.add(const <int>[1, 2, 3]);
        body.add(const <int>[4, 5]);
      },
      onCancel: () {
        if (!cancelled.isCompleted) cancelled.complete();
      },
    );
    addTearDown(() async {
      if (!body.isClosed) await body.close();
    });

    await expectLater(
      collectBoundedBytes(
        body.stream,
        maxBytes: 4,
        tooLarge: () => const FormatException('backup too large'),
      ),
      throwsA(isA<FormatException>()),
    );
    await cancelled.future.timeout(const Duration(seconds: 1));

    final source = File(
      'lib/services/local_storage_service.dart',
    ).readAsStringSync();
    expect(source, contains('collectBoundedBytes('));
    expect(source, contains('source.openRead()'));
    expect(source, isNot(contains('final bytes = await source.readAsBytes()')));
  });

  test('macOS debug and release sandboxes allow outbound networking', () {
    for (final path in const <String>[
      'macos/Runner/DebugProfile.entitlements',
      'macos/Runner/Release.entitlements',
    ]) {
      final entitlement = File(path).readAsStringSync();
      expect(
        entitlement,
        contains('<key>com.apple.security.network.client</key>'),
        reason: path,
      );
      expect(
        entitlement,
        contains('<key>com.apple.security.app-sandbox</key>'),
        reason: path,
      );
    }
  });
}
