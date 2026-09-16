import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/core/core.dart';
import 'package:oohstory/features/annotation_export/annotation_export.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../cloud/cloud_test_support.dart';

const _pageId = '12345678-1234-1234-1234-123456789abc';
const _parentId = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
const _token = 'ntn_test_token_value_123456789';

void main() {
  setUp(() => SharedPreferences.setMockInitialValues(<String, Object>{}));

  test(
    'configuration normalizes IDs and keeps token out of preferences',
    () async {
      final preferences = await SharedPreferences.getInstance();
      final credentials = MemoryCredentialStore();
      final repository = NotionConnectionRepository(
        preferences: preferences,
        credentialStore: credentials,
      );
      final configuration = NotionExportConfiguration(
        parentKind: NotionParentKind.dataSource,
        parentId: 'aaaaaaaabbbbccccddddeeeeeeeeeeee',
        titleProperty: 'Book name',
      );

      await repository.save(configuration, accessToken: _token);

      expect(repository.loadConfiguration()!.parentId, _parentId);
      expect(repository.loadConfiguration()!.titleProperty, 'Book name');
      expect(await repository.requireAccessToken(), _token);
      expect(preferences.getKeys(), contains('oohstory_notion_connection_v1'));
      expect(
        preferences.getString('oohstory_notion_connection_v1'),
        isNot(contains(_token)),
      );
      expect(credentials.values.values, contains(_token));

      await repository.disconnect();
      expect(repository.loadConfiguration(), isNull);
      expect(credentials.values, isEmpty);
    },
  );

  test('page creation uses the pinned API without workspace search', () async {
    final configuration = NotionExportConfiguration(
      parentKind: NotionParentKind.dataSource,
      parentId: _parentId,
      titleProperty: 'Name',
    );
    late String createdMarkdown;
    final transport = FixtureTransport((request, body) async {
      if (request.method == 'POST') {
        final payload = jsonDecode(utf8.decode(body)) as Map<String, dynamic>;
        createdMarkdown = payload['markdown'] as String;
        expect(payload['parent'], <String, Object?>{
          'type': 'data_source_id',
          'data_source_id': _parentId,
        });
        expect((payload['properties'] as Map)['Name'], isA<Map>());
        return jsonResponse(200, jsonEncode(<String, Object>{'id': _pageId}));
      }
      expect(request.method, 'GET');
      return jsonResponse(
        200,
        jsonEncode(<String, Object>{'markdown': createdMarkdown}),
      );
    });
    final exporter = await _exporter(configuration, transport);

    final receipt = await exporter.export(
      _document,
      _annotations,
      idempotencyKey: 'export-key',
    );

    expect(receipt.disposition, ExportDisposition.created);
    expect(receipt.target, 'notion:page:$_pageId');
    expect(transport.requests.map((request) => request.uri.path), <String>[
      '/v1/pages',
      '/v1/pages/$_pageId/markdown',
    ]);
    for (final request in transport.requests) {
      expect(request.headers['notion-version'], NotionApiClient.apiVersion);
      expect(request.headers['authorization'], 'Bearer $_token');
      expect(request.uri.path, isNot(contains('search')));
    }
  });

  test(
    'repeat export reads and reuses the same page without a write',
    () async {
      final configuration = NotionExportConfiguration(
        parentKind: NotionParentKind.page,
        parentId: _parentId,
      );
      var remoteMarkdown = '';
      final transport = FixtureTransport((request, body) async {
        if (request.method == 'POST') {
          final payload = jsonDecode(utf8.decode(body)) as Map<String, dynamic>;
          remoteMarkdown = payload['markdown'] as String;
          return jsonResponse(200, jsonEncode(<String, Object>{'id': _pageId}));
        }
        return jsonResponse(
          200,
          jsonEncode(<String, Object>{'markdown': remoteMarkdown}),
        );
      });
      final exporter = await _exporter(configuration, transport);
      await exporter.export(_document, _annotations, idempotencyKey: 'first');

      final receipt = await exporter.export(
        _document,
        _annotations,
        idempotencyKey: 'second',
      );

      expect(receipt.disposition, ExportDisposition.unchanged);
      expect(
        transport.requests.where((request) => request.method == 'POST'),
        hasLength(1),
      );
      expect(
        transport.requests.where((request) => request.method == 'PATCH'),
        isEmpty,
      );
    },
  );

  test('external edits conflict until explicit safe replacement', () async {
    final configuration = NotionExportConfiguration(
      parentKind: NotionParentKind.page,
      parentId: _parentId,
    );
    var remoteMarkdown = '';
    Map<String, dynamic>? replacement;
    final transport = FixtureTransport((request, body) async {
      if (request.method == 'POST') {
        final payload = jsonDecode(utf8.decode(body)) as Map<String, dynamic>;
        remoteMarkdown = payload['markdown'] as String;
        return jsonResponse(200, jsonEncode(<String, Object>{'id': _pageId}));
      }
      if (request.method == 'PATCH') {
        replacement = jsonDecode(utf8.decode(body)) as Map<String, dynamic>;
        remoteMarkdown =
            ((replacement!['replace_content'] as Map)['new_str'] as String);
        return jsonResponse(
          200,
          jsonEncode(<String, Object>{'markdown': remoteMarkdown}),
        );
      }
      return jsonResponse(
        200,
        jsonEncode(<String, Object>{'markdown': remoteMarkdown}),
      );
    });
    final exporter = await _exporter(configuration, transport);
    await exporter.export(_document, _annotations, idempotencyKey: 'first');
    remoteMarkdown = '$remoteMarkdown\n用户在 Notion 中添加的内容';

    await expectLater(
      exporter.export(_document, _annotations, idempotencyKey: 'conflict'),
      throwsA(isA<NotionExportConflict>()),
    );
    expect(replacement, isNull);

    final receipt = await exporter.export(
      _document,
      _annotations,
      idempotencyKey: 'confirmed',
      overwriteExternalChanges: true,
    );
    expect(receipt.disposition, ExportDisposition.overwritten);
    expect(replacement!['type'], 'replace_content');
    expect(
      (replacement!['replace_content'] as Map)['allow_deleting_content'],
      isFalse,
    );
  });

  test('safe reads honor Retry-After but creation is never retried', () async {
    var readAttempts = 0;
    final delays = <Duration>[];
    final readTransport = FixtureTransport((request, body) async {
      readAttempts++;
      if (readAttempts == 1) {
        return jsonResponse(429, '{}', headers: {'retry-after': '3'});
      }
      return jsonResponse(200, '{"markdown":"ok"}');
    });
    final reader = NotionApiClient(
      transport: readTransport,
      accessToken: () async => _token,
      sleep: (delay) async => delays.add(delay),
    );

    expect(await reader.readMarkdown(_pageId), 'ok');
    expect(readAttempts, 2);
    expect(delays, <Duration>[const Duration(seconds: 3)]);

    var createAttempts = 0;
    final createTransport = FixtureTransport((request, body) async {
      createAttempts++;
      return jsonResponse(503, '{}');
    });
    final creator = NotionApiClient(
      transport: createTransport,
      accessToken: () async => _token,
    );
    await expectLater(
      creator.createPage(
        configuration: NotionExportConfiguration(
          parentKind: NotionParentKind.page,
          parentId: _parentId,
        ),
        title: '测试',
        markdown: 'content',
      ),
      throwsA(
        isA<CoreException>().having(
          (error) => error.code,
          'code',
          CoreErrorCode.upstreamError,
        ),
      ),
    );
    expect(createAttempts, 1);
  });

  test('acknowledged page ID survives a failed verification read', () async {
    final configuration = NotionExportConfiguration(
      parentKind: NotionParentKind.page,
      parentId: _parentId,
    );
    var postCount = 0;
    var failReads = true;
    var remoteMarkdown = '';
    final transport = FixtureTransport((request, body) async {
      if (request.method == 'POST') {
        postCount++;
        final payload = jsonDecode(utf8.decode(body)) as Map<String, dynamic>;
        remoteMarkdown = payload['markdown'] as String;
        return jsonResponse(200, jsonEncode(<String, Object>{'id': _pageId}));
      }
      if (failReads) return jsonResponse(503, '{}');
      return jsonResponse(
        200,
        jsonEncode(<String, Object>{'markdown': remoteMarkdown}),
      );
    });
    final exporter = await _exporter(configuration, transport);

    await expectLater(
      exporter.export(_document, _annotations, idempotencyKey: 'first'),
      throwsA(isA<CoreException>()),
    );
    failReads = false;
    final receipt = await exporter.export(
      _document,
      _annotations,
      idempotencyKey: 'retry',
    );

    expect(postCount, 1);
    expect(receipt.target, 'notion:page:$_pageId');
    expect(receipt.disposition, ExportDisposition.unchanged);
  });

  test('disconnect state cleanup is scoped to the selected parent', () async {
    final preferences = await SharedPreferences.getInstance();
    final store = SharedPreferencesNotionExportStateStore(preferences);
    final first = NotionExportConfiguration(
      parentKind: NotionParentKind.page,
      parentId: _parentId,
    );
    final second = NotionExportConfiguration(
      parentKind: NotionParentKind.page,
      parentId: 'bbbbbbbb-cccc-dddd-eeee-ffffffffffff',
    );
    final hash = sha256.convert(utf8.encode('content')).toString();
    for (final config in <NotionExportConfiguration>[first, second]) {
      await store.write(
        NotionExportState(
          parentKey: config.parentKey,
          documentId: 'book-1',
          pageId: _pageId,
          localContentHash: hash,
          remoteContentHash: hash,
        ),
      );
    }

    await store.removeParent(first.parentKey);

    expect(
      await store.read(parentKey: first.parentKey, documentId: 'book-1'),
      isNull,
    );
    expect(
      await store.read(parentKey: second.parentKey, documentId: 'book-1'),
      isNotNull,
    );
  });
}

Future<NotionAnnotationExporter> _exporter(
  NotionExportConfiguration configuration,
  FixtureTransport transport,
) async {
  final preferences = await SharedPreferences.getInstance();
  return NotionAnnotationExporter(
    configuration: configuration,
    api: NotionApiClient(transport: transport, accessToken: () async => _token),
    stateStore: SharedPreferencesNotionExportStateStore(preferences),
    now: () => DateTime.utc(2026, 9, 15),
  );
}

const _document = DocumentIdentity(
  id: 'book-1',
  title: '测试书',
  author: '作者',
  documentVersion: 'v1',
);

final _annotations = <Annotation>[
  Annotation(
    id: 'annotation-1',
    bookId: 'book-1',
    location: 'progress:0.500000',
    text: '一条高亮',
    note: '一条笔记',
    createdAt: DateTime.utc(2026, 9, 15),
  ),
];
