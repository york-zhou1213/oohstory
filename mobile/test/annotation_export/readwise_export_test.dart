import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/cloud/cloud.dart';
import 'package:oohstory/core/core.dart';
import 'package:oohstory/features/annotation_export/annotation_export.dart';
import 'package:oohstory/services/api_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../cloud/cloud_test_support.dart';

const _token = 'readwise-test-token-123456789';

void main() {
  setUp(() => SharedPreferences.setMockInitialValues(<String, Object>{}));

  test('connection keeps token only in secure credential storage', () async {
    final credentials = MemoryCredentialStore();
    final connection = ReadwiseConnectionRepository(
      credentialStore: credentials,
    );

    await connection.saveAccessToken(_token);

    expect(await connection.hasAccessToken(), isTrue);
    expect(await connection.requireAccessToken(), _token);
    expect(credentials.values.values, contains(_token));
    final preferences = await SharedPreferences.getInstance();
    expect(preferences.getKeys(), isEmpty);

    await connection.disconnect();
    expect(await connection.hasAccessToken(), isFalse);
  });

  test('connection verification uses token auth and accepts 204', () async {
    final transport = FixtureTransport((request, body) async {
      expect(request.method, 'GET');
      expect(request.uri, Uri.parse('https://readwise.io/api/v2/auth/'));
      expect(request.headers['authorization'], 'Token $_token');
      expect(body, isEmpty);
      return CloudHttpResponse.bytes(statusCode: 204);
    });

    await ReadwiseApiClient(
      transport: transport,
      accessToken: () async => _token,
    ).verifyConnection();

    expect(transport.requests, hasLength(1));
  });

  test(
    'first export creates stable highlight and persists remote state',
    () async {
      late Map<String, dynamic> created;
      final remote = <String, Object?>{
        'id': 901,
        'text': '一条高亮',
        'note': '一条笔记',
        'location': 500000,
        'location_type': 'location',
        'url': '',
        'is_deleted': false,
      };
      final transport = FixtureTransport((request, body) async {
        if (request.method == 'POST') {
          created =
              ((jsonDecode(utf8.decode(body)) as Map)['highlights'] as List)
                      .single
                  as Map<String, dynamic>;
          remote['url'] = created['highlight_url'];
          return jsonResponse(200, '[{"modified_highlights":[901]}]');
        }
        expect(request.uri.path, '/api/v2/highlights/901/');
        return jsonResponse(200, jsonEncode(remote));
      });
      final exporter = await _exporter(transport);

      final receipt = await exporter.export(
        _document,
        _annotations,
        idempotencyKey: 'first',
      );

      expect(receipt.disposition, ExportDisposition.created);
      expect(receipt.providerId, 'readwise');
      expect(created['source_type'], 'oohstory');
      expect(created['category'], 'books');
      expect(created['source_url'], startsWith('${ApiService.baseUrl}/app/#'));
      expect(
        created['highlight_url'],
        startsWith('${ApiService.baseUrl}/app/#'),
      );
      expect(created['highlight_url'], isNot(contains('annotation-1')));
      expect(created['highlighted_at'], '2026-09-15T00:00:00.000Z');

      final preferences = await SharedPreferences.getInstance();
      final state = await SharedPreferencesReadwiseExportStateStore(
        preferences,
      ).read(_document.id);
      expect(state!.highlights['annotation-1']!.remoteId, 901);
    },
  );

  test('unchanged repeat reads remote and performs no write', () async {
    final remote = <String, Object?>{
      'id': 901,
      'text': '一条高亮',
      'note': '一条笔记',
      'location': 500000,
      'location_type': 'location',
      'url': '${ApiService.baseUrl}/app/#readwise-highlight-test',
      'is_deleted': false,
    };
    var postCount = 0;
    var patchCount = 0;
    final transport = FixtureTransport((request, body) async {
      if (request.method == 'POST') {
        postCount++;
        final created =
            ((jsonDecode(utf8.decode(body)) as Map)['highlights'] as List)
                    .single
                as Map;
        remote['url'] = created['highlight_url'] as String;
        return jsonResponse(200, '[{"modified_highlights":[901]}]');
      }
      if (request.method == 'PATCH') patchCount++;
      return jsonResponse(200, jsonEncode(remote));
    });
    final exporter = await _exporter(transport);
    await exporter.export(_document, _annotations, idempotencyKey: 'first');

    final receipt = await exporter.export(
      _document,
      _annotations,
      idempotencyKey: 'repeat',
    );

    expect(receipt.disposition, ExportDisposition.unchanged);
    expect(postCount, 1);
    expect(patchCount, 0);
  });

  test('external modification conflicts until explicit replacement', () async {
    final remote = <String, Object?>{
      'id': 901,
      'text': '一条高亮',
      'note': '一条笔记',
      'location': 500000,
      'location_type': 'location',
      'url': '',
      'is_deleted': false,
    };
    var patchCount = 0;
    final transport = FixtureTransport((request, body) async {
      if (request.method == 'POST') {
        final created =
            ((jsonDecode(utf8.decode(body)) as Map)['highlights'] as List)
                    .single
                as Map;
        remote['url'] = created['highlight_url'] as String;
        return jsonResponse(200, '[{"modified_highlights":[901]}]');
      }
      if (request.method == 'PATCH') {
        patchCount++;
        remote.addAll(
          Map<String, Object?>.from(jsonDecode(utf8.decode(body)) as Map),
        );
      }
      return jsonResponse(200, jsonEncode(remote));
    });
    final exporter = await _exporter(transport);
    await exporter.export(_document, _annotations, idempotencyKey: 'first');
    remote['note'] = '用户在 Readwise 中修改的笔记';

    await expectLater(
      exporter.export(_document, _annotations, idempotencyKey: 'conflict'),
      throwsA(isA<ReadwiseExportConflict>()),
    );
    expect(patchCount, 0);

    final receipt = await exporter.export(
      _document,
      _annotations,
      idempotencyKey: 'overwrite',
      overwriteExternalChanges: true,
    );
    expect(receipt.disposition, ExportDisposition.overwritten);
    expect(patchCount, 1);
    expect(remote['note'], '一条笔记');
  });

  test(
    'soft-deleted remote highlight is recreated only after consent',
    () async {
      var nextId = 901;
      var postCount = 0;
      final remote = <String, Object?>{
        'id': nextId,
        'text': '一条高亮',
        'note': '一条笔记',
        'location': 500000,
        'location_type': 'location',
        'url': '',
        'is_deleted': false,
      };
      final transport = FixtureTransport((request, body) async {
        if (request.method == 'POST') {
          postCount++;
          final created =
              ((jsonDecode(utf8.decode(body)) as Map)['highlights'] as List)
                      .single
                  as Map;
          remote
            ..['id'] = nextId
            ..['url'] = created['highlight_url'] as String
            ..['is_deleted'] = false;
          return jsonResponse(200, '[{"modified_highlights":[$nextId]}]');
        }
        return jsonResponse(200, jsonEncode(remote));
      });
      final exporter = await _exporter(transport);
      await exporter.export(_document, _annotations, idempotencyKey: 'first');
      remote['is_deleted'] = true;

      await expectLater(
        exporter.export(_document, _annotations, idempotencyKey: 'deleted'),
        throwsA(isA<ReadwiseExportConflict>()),
      );
      expect(postCount, 1);

      nextId = 902;
      final receipt = await exporter.export(
        _document,
        _annotations,
        idempotencyKey: 'recreate',
        overwriteExternalChanges: true,
      );
      expect(receipt.disposition, ExportDisposition.overwritten);
      expect(postCount, 2);

      final preferences = await SharedPreferences.getInstance();
      final state = await SharedPreferencesReadwiseExportStateStore(
        preferences,
      ).read(_document.id);
      expect(state!.highlights['annotation-1']!.remoteId, 902);
    },
  );

  test(
    'local edits update acknowledged highlight without duplication',
    () async {
      final remote = <String, Object?>{
        'id': 901,
        'text': '一条高亮',
        'note': '一条笔记',
        'location': 500000,
        'location_type': 'location',
        'url': '',
        'is_deleted': false,
      };
      var postCount = 0;
      var patchCount = 0;
      final transport = FixtureTransport((request, body) async {
        if (request.method == 'POST') {
          postCount++;
          final created =
              ((jsonDecode(utf8.decode(body)) as Map)['highlights'] as List)
                      .single
                  as Map;
          remote['url'] = created['highlight_url'] as String;
          return jsonResponse(200, '[{"modified_highlights":[901]}]');
        }
        if (request.method == 'PATCH') {
          patchCount++;
          remote.addAll(
            Map<String, Object?>.from(jsonDecode(utf8.decode(body)) as Map),
          );
        }
        return jsonResponse(200, jsonEncode(remote));
      });
      final exporter = await _exporter(transport);
      await exporter.export(_document, _annotations, idempotencyKey: 'first');
      final changed = <Annotation>[
        Annotation(
          id: 'annotation-1',
          bookId: 'book-1',
          location: 'progress:0.500000',
          text: '一条高亮',
          note: '修改后的笔记',
          createdAt: DateTime.utc(2026, 9, 15),
        ),
      ];

      final receipt = await exporter.export(
        _document,
        changed,
        idempotencyKey: 'changed',
      );

      expect(receipt.disposition, ExportDisposition.updated);
      expect(postCount, 1);
      expect(patchCount, 1);
      expect(remote['note'], '修改后的笔记');
    },
  );

  test(
    'ambiguous create recovery searches export pages by stable URL',
    () async {
      late String highlightUrl;
      var exportPage = 0;
      final transport = FixtureTransport((request, body) async {
        if (request.method == 'POST') {
          final created =
              ((jsonDecode(utf8.decode(body)) as Map)['highlights'] as List)
                      .single
                  as Map;
          highlightUrl = created['highlight_url'] as String;
          return jsonResponse(200, '[{"modified_highlights":[]}]');
        }
        if (request.uri.path == '/api/v2/export/') {
          exportPage++;
          if (exportPage == 1) {
            return jsonResponse(200, '{"results":[],"nextPageCursor":"next"}');
          }
          expect(request.uri.queryParameters['pageCursor'], 'next');
          return jsonResponse(
            200,
            jsonEncode(<String, Object?>{
              'results': <Object?>[
                <String, Object?>{
                  'highlights': <Object?>[
                    <String, Object?>{
                      'id': 901,
                      'text': '一条高亮',
                      'note': '一条笔记',
                      'location': 500000,
                      'location_type': 'location',
                      'url': highlightUrl,
                      'is_deleted': false,
                    },
                  ],
                },
              ],
              'nextPageCursor': null,
            }),
          );
        }
        fail('unexpected request: ${request.method} ${request.uri}');
      });

      final receipt = await (await _exporter(
        transport,
      )).export(_document, _annotations, idempotencyKey: 'recover');

      expect(receipt.disposition, ExportDisposition.created);
      expect(exportPage, 2);
    },
  );

  test('disconnect cleanup clears only local export state', () async {
    final preferences = await SharedPreferences.getInstance();
    final store = SharedPreferencesReadwiseExportStateStore(preferences);
    await store.write(
      ReadwiseExportState(
        documentId: 'book-1',
        highlights: <String, ReadwiseHighlightState>{
          'annotation-1': ReadwiseHighlightState(
            annotationId: 'annotation-1',
            remoteId: 901,
            localContentHash: List<String>.filled(64, 'a').join(),
            remoteContentHash: List<String>.filled(64, 'b').join(),
          ),
        },
      ),
    );

    await store.clear();

    expect(await store.read('book-1'), isNull);
  });
}

Future<ReadwiseAnnotationExporter> _exporter(FixtureTransport transport) async {
  final preferences = await SharedPreferences.getInstance();
  return ReadwiseAnnotationExporter(
    api: ReadwiseApiClient(
      transport: transport,
      accessToken: () async => _token,
    ),
    stateStore: SharedPreferencesReadwiseExportStateStore(preferences),
    now: () => DateTime.utc(2026, 9, 17),
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
