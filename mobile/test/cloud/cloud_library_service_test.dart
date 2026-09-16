import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/ocr/local_ocr_adapter.dart';
import 'package:oohstory/adapters/contracts/adapter_contracts.dart';
import 'package:oohstory/core/core.dart';
import 'package:oohstory/features/cloud_library/cloud_library.dart';
import 'package:oohstory/features/local_content/local_content.dart';

import '../fixtures/formats/fixture_factory.dart';

void main() {
  test('cloud book reuses the bounded local Kindle parser', () async {
    final adapter = _CloudFixtureAdapter()..readBytes = kindleFixture();
    final service = CloudLibraryService(
      adapter: adapter,
      localContentService: _localContentService(),
    );

    final book = await service.open(
      const CloudEntry(
        path: 'books/fixture.azw3',
        isDirectory: false,
        etag: 'v1',
      ),
    );

    expect(book.title, 'Fixture Book');
    expect(book.pageCount, 2);
    expect(adapter.reads, <String>['books/fixture.azw3']);
  });

  test('upload is create-only and delete requires the current ETag', () async {
    final adapter = _CloudFixtureAdapter();
    final service = CloudLibraryService(
      adapter: adapter,
      localContentService: _localContentService(),
    );
    final payload = kindleFixture();

    final uploaded = await service.upload(
      'books',
      LocalPickedFile.fromBytes('fixture.azw3', payload),
    );
    expect(uploaded.path, 'books/fixture.azw3');
    expect(adapter.writes.single.$1, 'books/fixture.azw3');
    expect(adapter.writes.single.$2, isNull);
    expect(adapter.writes.single.$3, payload);

    await expectLater(
      service.delete(
        const CloudEntry(path: 'books/fixture.azw3', isDirectory: false),
      ),
      throwsA(
        isA<CoreException>().having(
          (error) => error.code,
          'code',
          CoreErrorCode.revisionConflict,
        ),
      ),
    );
    await service.delete(
      const CloudEntry(
        path: 'books/fixture.azw3',
        isDirectory: false,
        etag: 'v2',
      ),
    );
    expect(adapter.deletes, <(String, String?)>[('books/fixture.azw3', 'v2')]);
  });

  test('unsupported files are neither downloaded nor uploaded', () async {
    final adapter = _CloudFixtureAdapter();
    final service = CloudLibraryService(
      adapter: adapter,
      localContentService: _localContentService(),
    );
    const unsupported = CloudEntry(path: 'books/notes.txt', isDirectory: false);

    expect(service.canOpen(unsupported), isFalse);
    await expectLater(service.open(unsupported), throwsA(isA<CoreException>()));
    await expectLater(
      service.upload('', LocalPickedFile.fromBytes('notes.txt', <int>[1, 2])),
      throwsA(isA<CoreException>()),
    );
    expect(adapter.reads, isEmpty);
    expect(adapter.writes, isEmpty);
  });
}

LocalContentService _localContentService() => LocalContentService(
  ocrAdapter: LocalOcrAdapter.unavailable(platform: 'test'),
);

final class _CloudFixtureAdapter implements CloudLibraryAdapter {
  Uint8List readBytes = Uint8List(0);
  final List<String> reads = <String>[];
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
  Future<SyncPage<CloudEntry>> list(String path, {String? cursor}) async =>
      SyncPage<CloudEntry>(
        items: const <CloudEntry>[],
        nextCursor: null,
        serverTime: DateTime.utc(2026, 9, 15),
      );

  @override
  Stream<List<int>> read(String path) {
    reads.add(path);
    return Stream<List<int>>.value(readBytes);
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
    final data = await bytes.expand((chunk) => chunk).toList();
    writes.add((path, etag, Uint8List.fromList(data)));
    return CloudEntry(path: path, isDirectory: false, etag: 'v2');
  }
}
