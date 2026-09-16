import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/contracts/adapter_contracts.dart';
import 'package:oohstory/core/core.dart';
import 'package:oohstory/features/cloud_library/cloud_library.dart';
import 'package:oohstory/features/local_content/local_content.dart';
import 'package:oohstory/screens/profile_screen.dart';
import 'package:oohstory/adapters/ocr/local_ocr_adapter.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../fixtures/formats/fixture_factory.dart';
import 'cloud_test_support.dart';

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  testWidgets('connections screen only offers enabled providers', (
    tester,
  ) async {
    final manager = await _manager();
    await tester.pumpWidget(
      MaterialApp(
        home: CloudConnectionsScreen(
          capabilities: const ProductCapabilityProfile(webDavEnabled: true),
          manager: manager,
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('存储与同步'), findsOneWidget);
    expect(find.text('连接 WebDAV'), findsOneWidget);
    expect(find.text('连接 S3'), findsNothing);
    expect(find.textContaining('系统安全存储'), findsOneWidget);
  });

  testWidgets('profile exposes the gated storage and sync entry', (
    tester,
  ) async {
    const secureStorage = MethodChannel(
      'plugins.it_nomads.com/flutter_secure_storage',
    );
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(secureStorage, (_) async => null);
    addTearDown(
      () => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(secureStorage, null),
    );
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: ProfileScreen(
            capabilities: ProductCapabilityProfile(webDavEnabled: true),
          ),
        ),
      ),
    );
    await tester.pump(const Duration(seconds: 1));
    await tester.pump();
    await tester.scrollUntilVisible(
      find.text('存储与同步'),
      300,
      scrollable: find.byType(Scrollable).first,
    );

    expect(find.text('存储与同步'), findsOneWidget);
    expect(find.text('WebDAV · S3 云端书库'), findsOneWidget);
  });

  testWidgets('cloud browser navigates and opens a supported remote book', (
    tester,
  ) async {
    final adapter = _BrowserAdapter();
    final service = CloudLibraryService(
      adapter: adapter,
      localContentService: LocalContentService(
        ocrAdapter: LocalOcrAdapter.unavailable(platform: 'test'),
      ),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: CloudLibraryScreen(
          connection: _config(),
          manager: await _manager(),
          service: service,
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('books'), findsOneWidget);
    await tester.tap(find.text('books'));
    await tester.pumpAndSettle();
    expect(find.text('fixture.azw3'), findsOneWidget);

    await tester.tap(find.text('fixture.azw3'));
    await tester.pumpAndSettle();

    expect(find.text('Fixture Book'), findsOneWidget);
    expect(find.text('Chapter 1'), findsOneWidget);
    expect(adapter.reads, <String>['books/fixture.azw3']);
  });

  testWidgets('cloud browser uploads create-only and confirms ETag delete', (
    tester,
  ) async {
    final adapter = _BrowserAdapter(showFileAtRoot: true);
    final service = CloudLibraryService(
      adapter: adapter,
      localContentService: LocalContentService(
        ocrAdapter: LocalOcrAdapter.unavailable(platform: 'test'),
      ),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: CloudLibraryScreen(
          connection: _config(),
          manager: await _manager(),
          service: service,
          picker: (_) async =>
              LocalPickedFile.fromBytes('new.azw3', kindleFixture()),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('上传电子书'));
    await tester.pumpAndSettle();
    expect(adapter.writes.single.$1, 'new.azw3');
    expect(adapter.writes.single.$2, isNull);

    await tester.tap(find.byTooltip('条件删除').first);
    await tester.pumpAndSettle();
    expect(find.textContaining('按当前 ETag'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, '删除'));
    await tester.pumpAndSettle();

    expect(adapter.deletes, <(String, String?)>[('fixture.azw3', 'v1')]);
  });
}

Future<CloudConnectionManager> _manager() async => CloudConnectionManager(
  repository: CloudConnectionRepository(
    preferences: await SharedPreferences.getInstance(),
    credentialStore: MemoryCredentialStore(),
  ),
);

CloudConnectionConfig _config() => CloudConnectionConfig(
  id: 'webdav-screen',
  provider: CloudProviderKind.webDav,
  name: 'Fixture DAV',
  endpoint: Uri.parse('https://dav.example.test'),
  root: 'OOHStory',
);

final class _BrowserAdapter implements CloudLibraryAdapter {
  _BrowserAdapter({this.showFileAtRoot = false});

  final bool showFileAtRoot;
  final List<String> reads = [];
  final List<(String, String?, Uint8List)> writes = [];
  final List<(String, String?)> deletes = [];

  @override
  String get providerId => 'fixture';

  @override
  ProviderCapabilities get capabilities => ProviderCapabilities(
    providerId: providerId,
    supported: const <AdapterCapability>[AdapterCapability.cloudLibrary],
  );

  @override
  Future<void> delete(String path, {String? etag}) async {
    deletes.add((path, etag));
  }

  @override
  Future<SyncPage<CloudEntry>> list(String path, {String? cursor}) async {
    final items = path == 'books'
        ? const <CloudEntry>[
            CloudEntry(
              path: 'books/fixture.azw3',
              isDirectory: false,
              etag: 'v1',
            ),
          ]
        : showFileAtRoot
        ? const <CloudEntry>[
            CloudEntry(path: 'fixture.azw3', isDirectory: false, etag: 'v1'),
          ]
        : const <CloudEntry>[CloudEntry(path: 'books', isDirectory: true)];
    return SyncPage<CloudEntry>(
      items: items,
      nextCursor: null,
      serverTime: DateTime.utc(2026, 9, 15),
    );
  }

  @override
  Stream<List<int>> read(String path) {
    reads.add(path);
    return Stream<List<int>>.value(kindleFixture());
  }

  @override
  Future<CloudEntry> stat(String path) async =>
      CloudEntry(path: path, isDirectory: false, etag: 'v2');

  @override
  Future<CloudEntry> write(
    String path,
    Stream<List<int>> bytes, {
    String? etag,
  }) async {
    final body = await bytes.expand((chunk) => chunk).toList();
    writes.add((path, etag, Uint8List.fromList(body)));
    return CloudEntry(path: path, isDirectory: false, etag: 'v2');
  }
}
