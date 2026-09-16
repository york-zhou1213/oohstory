import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/cloud/cloud.dart';
import 'package:oohstory/core/core.dart';
import 'package:oohstory/features/annotation_export/annotation_export.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../cloud/cloud_test_support.dart';

const _token = '0123456789abcdef0123456789abcdef';
const _notebookId = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const _childNotebookId = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
const _otherNotebookId = 'cccccccccccccccccccccccccccccccc';

void main() {
  setUp(() => SharedPreferences.setMockInitialValues(<String, Object>{}));

  test('attachment bytes are copied and exposed read-only', () {
    final source = <int>[1, 2, 3];
    final attachment = _attachment('immutable', 'immutable.png', source);
    source[0] = 9;

    expect(attachment.bytes, orderedEquals(<int>[1, 2, 3]));
    expect(() => attachment.bytes[0] = 8, throwsUnsupportedError);
  });

  test('connection keeps token out of ordinary preferences', () async {
    final preferences = await SharedPreferences.getInstance();
    final credentials = MemoryCredentialStore();
    final repository = JoplinConnectionRepository(
      preferences: preferences,
      credentialStore: credentials,
    );
    final configuration = JoplinExportConfiguration(
      port: 41184,
      notebookId: _notebookId,
    );

    await repository.save(configuration, token: _token);

    expect(repository.loadConfiguration()!.toJson(), configuration.toJson());
    expect(await repository.requireToken(), _token);
    expect(preferences.getKeys(), hasLength(1));
    expect(
      preferences.getString(preferences.getKeys().single),
      isNot(contains(_token)),
    );

    await repository.disconnect();
    expect(repository.loadConfiguration(), isNull);
    expect(credentials.values, isEmpty);
  });

  test('modern 128-character Joplin tokens remain accepted', () {
    final modernToken = List<String>.filled(4, _token).join();

    expect(modernToken, hasLength(128));
    expect(validateJoplinToken(modernToken), modernToken);
    expect(() => validateJoplinToken('short'), throwsFormatException);
  });

  test('configuration only constructs a fixed loopback endpoint', () {
    final configuration = JoplinExportConfiguration(
      port: 41190,
      notebookId: _notebookId.toUpperCase(),
    );

    expect(configuration.endpoint, Uri.parse('http://127.0.0.1:41190'));
    expect(configuration.notebookId, _notebookId);
    expect(
      configuration.targetKey,
      JoplinExportConfiguration(port: 41184, notebookId: _notebookId).targetKey,
    );
    expect(
      () => JoplinExportConfiguration(port: 80, notebookId: _notebookId),
      throwsFormatException,
    );
    expect(
      () => JoplinExportConfiguration(port: 41184, notebookId: '../notes'),
      throwsFormatException,
    );
  });

  test('probe is tokenless and notebook verification authenticates', () async {
    final transport = FixtureTransport((request, body) async {
      if (request.uri.path == '/ping') {
        return CloudHttpResponse.bytes(
          statusCode: 200,
          body: utf8.encode('JoplinClipperServer'),
        );
      }
      return jsonResponse(
        200,
        jsonEncode(<String, Object>{'id': _notebookId, 'title': '阅读'}),
      );
    });
    final client = _client(transport);

    await client.probe();
    expect(await client.verifyNotebook(), '阅读');

    expect(transport.requests, hasLength(2));
    for (final request in transport.requests) {
      expect(request.uri.scheme, 'http');
      expect(request.uri.host, '127.0.0.1');
      expect(request.uri.port, 41184);
      expect(request.headers.keys, isNot(contains('authorization')));
    }
    expect(transport.requests.first.uri.path, '/ping');
    expect(
      transport.requests.first.uri.queryParameters,
      isNot(contains('token')),
    );
    expect(transport.requests.last.uri.queryParameters['token'], _token);
  });

  test('port discovery scans the official range without a token', () async {
    final transport = FixtureTransport((request, body) async {
      if (request.uri.port == 41186) {
        return CloudHttpResponse.bytes(
          statusCode: 200,
          body: utf8.encode('JoplinClipperServer'),
        );
      }
      return CloudHttpResponse.bytes(statusCode: 404);
    });

    final port = await discoverJoplinPort(
      transport: transport,
      ports: const <int>[41184, 41185, 41186, 41187],
    );

    expect(port, 41186);
    expect(transport.requests.map((request) => request.uri.port), <int>[
      41184,
      41185,
      41186,
    ]);
    for (final request in transport.requests) {
      expect(request.uri.path, '/ping');
      expect(request.uri.host, '127.0.0.1');
      expect(request.uri.queryParameters, isNot(contains('token')));
    }
  });

  test(
    'connection client lists bounded notebooks with hierarchy paths',
    () async {
      final transport = FixtureTransport((request, body) async {
        if (request.uri.path == '/ping') {
          return CloudHttpResponse.bytes(
            statusCode: 200,
            body: utf8.encode('JoplinClipperServer'),
          );
        }
        if (request.uri.path == '/folders/$_childNotebookId') {
          return jsonResponse(
            200,
            jsonEncode(<String, Object>{'id': _childNotebookId, 'title': '项目'}),
          );
        }
        final page = request.uri.queryParameters['page'];
        return page == '1'
            ? jsonResponse(
                200,
                jsonEncode(<String, Object>{
                  'items': <Map<String, Object>>[
                    <String, Object>{
                      'id': _childNotebookId,
                      'parent_id': _notebookId,
                      'title': '项目',
                    },
                    <String, Object>{
                      'id': _notebookId,
                      'parent_id': '',
                      'title': '阅读',
                    },
                  ],
                  'has_more': true,
                }),
              )
            : jsonResponse(
                200,
                jsonEncode(<String, Object>{
                  'items': <Map<String, Object>>[
                    <String, Object>{
                      'id': _otherNotebookId,
                      'parent_id': '',
                      'title': '归档',
                    },
                  ],
                  'has_more': false,
                }),
              );
      });
      final client = JoplinApiClient.connection(
        port: 41184,
        transport: transport,
        token: () async => _token,
        retryPolicy: const RetryPolicy(maxAttempts: 1),
      );

      await client.probe();
      final notebooks = await client.listNotebooks();
      expect(await client.verifyNotebook(_childNotebookId), '项目');

      expect(notebooks.map((item) => item.path), <String>[
        '归档',
        '阅读',
        '阅读 / 项目',
      ]);
      final folderRequests = transport.requests
          .where((request) => request.uri.path == '/folders')
          .toList();
      expect(folderRequests, hasLength(2));
      expect(
        folderRequests.first.uri.queryParameters['fields'],
        'id,parent_id,title',
      );
      expect(folderRequests.first.uri.queryParameters['limit'], '100');
      for (final request in transport.requests) {
        expect(request.uri.host, '127.0.0.1');
        if (request.uri.path == '/ping') {
          expect(request.uri.queryParameters, isNot(contains('token')));
        } else {
          expect(request.uri.queryParameters['token'], _token);
        }
      }
    },
  );

  test('notebook discovery rejects cyclic folder ancestry', () async {
    final transport = FixtureTransport((request, body) async {
      return jsonResponse(
        200,
        jsonEncode(<String, Object>{
          'items': <Map<String, Object>>[
            <String, Object>{
              'id': _notebookId,
              'parent_id': _childNotebookId,
              'title': 'A',
            },
            <String, Object>{
              'id': _childNotebookId,
              'parent_id': _notebookId,
              'title': 'B',
            },
          ],
          'has_more': false,
        }),
      );
    });
    final client = JoplinApiClient.connection(
      port: 41184,
      transport: transport,
      token: () async => _token,
      retryPolicy: const RetryPolicy(maxAttempts: 1),
    );

    expect(
      client.listNotebooks,
      throwsA(
        isA<CoreException>().having(
          (error) => error.code,
          'code',
          CoreErrorCode.upstreamError,
        ),
      ),
    );
  });

  test('connection timeout is bounded and does not expose the token', () async {
    final pending = Completer<CloudHttpResponse>();
    final transport = FixtureTransport((request, body) => pending.future);
    final client = JoplinApiClient.connection(
      port: 41184,
      transport: transport,
      token: () async => _token,
      retryPolicy: const RetryPolicy(maxAttempts: 1),
      requestTimeout: const Duration(milliseconds: 1),
    );

    await expectLater(
      client.listNotebooks(),
      throwsA(
        isA<CoreException>()
            .having((error) => error.code, 'code', CoreErrorCode.upstreamError)
            .having(
              (error) => error.message,
              'message',
              allOf(contains('超时'), isNot(contains(_token))),
            ),
      ),
    );
  });

  test('repeat export reuses deterministic note and tag identities', () async {
    final fixture = _JoplinFixture();
    final exporter = await _exporter(fixture);

    final first = await exporter.export(
      _document,
      _annotations,
      idempotencyKey: 'first',
    );
    final second = await exporter.export(
      _document,
      _annotations,
      idempotencyKey: 'second',
    );

    expect(first.disposition, ExportDisposition.created);
    expect(second.disposition, ExportDisposition.unchanged);
    expect(first.target, second.target);
    expect(fixture.notes, hasLength(1));
    expect(fixture.tags.values.toSet(), JoplinApiClient.tagTitles.toSet());
    expect(fixture.noteTags.values.single, hasLength(2));
    expect(fixture.count('POST', '/notes'), 1);
    expect(fixture.count('POST', '/tags'), 2);
    expect(
      fixture.countMatching('POST', RegExp(r'^/tags/[0-9a-f]{32}/notes$')),
      2,
    );
  });

  test('resources reuse deterministic identities through provenance', () async {
    final fixture = _JoplinFixture();
    final exporter = await _exporter(fixture);

    final first = await exporter.exportDocument(
      _documentWithAttachment(),
      idempotencyKey: 'resource-first',
    );
    final second = await exporter.exportDocument(
      _documentWithAttachment(),
      idempotencyKey: 'resource-second',
    );

    expect(first.disposition, ExportDisposition.created);
    expect(second.disposition, ExportDisposition.unchanged);
    expect(fixture.resources, hasLength(1));
    final resourceId = fixture.resources.keys.single;
    expect(fixture.notes.values.single['body'], contains('!['));
    expect(fixture.notes.values.single['body'], contains('(:/$resourceId)'));
    expect(
      resourceId,
      deterministicJoplinId(
        'oohstory-joplin-resource-v1',
        '${_configuration.targetKey}\u0000${_document.id}\u0000attachment-1',
      ),
    );
    expect(fixture.count('POST', '/resources'), 1);
    expect(fixture.count('PUT', '/resources/$resourceId'), 1);
    expect(fixture.resources[resourceId]!['user_data'], contains('oohstory'));
  });

  test(
    'resource edits stop by default and require explicit replacement',
    () async {
      final fixture = _JoplinFixture();
      final exporter = await _exporter(fixture);
      final document = _documentWithAttachment();
      await exporter.exportDocument(document, idempotencyKey: 'resource-first');
      final resourceId = fixture.resources.keys.single;
      fixture.resourceBytes[resourceId] = Uint8List.fromList(
        utf8.encode('external bytes'),
      );
      fixture.resources[resourceId]!['size'] =
          fixture.resourceBytes[resourceId]!.length;

      await expectLater(
        exporter.exportDocument(document, idempotencyKey: 'resource-blocked'),
        throwsA(
          isA<JoplinExportConflict>().having(
            (error) => error.resourceId,
            'resourceId',
            resourceId,
          ),
        ),
      );

      final replaced = await exporter.exportDocument(
        document,
        idempotencyKey: 'resource-replaced',
        overwriteExternalChanges: true,
      );
      expect(replaced.disposition, ExportDisposition.overwritten);
      expect(
        fixture.resourceBytes[resourceId],
        orderedEquals(utf8.encode('image bytes')),
      );
      expect(fixture.count('PUT', '/resources/$resourceId'), 3);
    },
  );

  test('all resource conflicts are checked before the first write', () async {
    final fixture = _JoplinFixture();
    final exporter = await _exporter(fixture);
    final initial = _documentWithAttachments(<AnnotationExportAttachment>[
      _attachment('attachment-1', 'first.png', utf8.encode('first bytes')),
      _attachment('attachment-2', 'second.png', utf8.encode('second bytes')),
    ]);
    await exporter.exportDocument(initial, idempotencyKey: 'two-first');
    expect(fixture.count('GET', '/resources'), 1);

    final resourceIds = <String, String>{
      for (final entry in fixture.resources.entries)
        (jsonDecode(entry.value['user_data']! as String)
                    as Map<String, dynamic>)['attachment_id']!
                as String:
            entry.key,
    };
    final secondId = resourceIds['attachment-2']!;
    fixture.resourceBytes[secondId] = Uint8List.fromList(
      utf8.encode('external second bytes'),
    );
    fixture.resources[secondId]!['size'] =
        fixture.resourceBytes[secondId]!.length;
    final changed = _documentWithAttachments(<AnnotationExportAttachment>[
      _attachment('attachment-1', 'first.png', utf8.encode('changed first')),
      _attachment('attachment-2', 'second.png', utf8.encode('second bytes')),
    ]);

    await expectLater(
      exporter.exportDocument(changed, idempotencyKey: 'two-conflict'),
      throwsA(isA<JoplinExportConflict>()),
    );
    expect(
      fixture.count('PUT', '/resources/${resourceIds['attachment-1']}'),
      1,
    );
  });

  test('ambiguous resource create is recovered without a duplicate', () async {
    final fixture = _JoplinFixture(failAfterFirstResourceCreate: true);
    final exporter = await _exporter(fixture);
    final document = _documentWithAttachment();

    await expectLater(
      exporter.exportDocument(document, idempotencyKey: 'resource-first'),
      throwsA(isA<CoreException>()),
    );
    expect(fixture.resources, hasLength(1));
    expect(fixture.notes, isEmpty);

    final recovered = await exporter.exportDocument(
      document,
      idempotencyKey: 'resource-second',
    );
    expect(recovered.disposition, ExportDisposition.created);
    expect(fixture.resources, hasLength(1));
    expect(fixture.count('POST', '/resources'), 1);
    expect(
      fixture.count('PUT', '/resources/${fixture.resources.keys.single}'),
      2,
    );
  });

  test(
    'ambiguous note create recovers its generated resource mapping',
    () async {
      final fixture = _JoplinFixture(failAfterFirstNoteCreate: true);
      final exporter = await _exporter(fixture);
      final document = _documentWithAttachment();

      await expectLater(
        exporter.exportDocument(document, idempotencyKey: 'note-first'),
        throwsA(isA<CoreException>()),
      );
      expect(fixture.notes, hasLength(1));
      expect(fixture.resources, hasLength(1));

      final recovered = await exporter.exportDocument(
        document,
        idempotencyKey: 'note-second',
      );
      expect(recovered.disposition, ExportDisposition.unchanged);
      expect(fixture.count('POST', '/notes'), 1);
      expect(fixture.count('POST', '/resources'), 1);
    },
  );

  test('legacy note-only state loads with no resource mappings', () {
    final state = JoplinExportState.fromJson(<String, Object?>{
      'target_key': _configuration.targetKey,
      'document_id': _document.id,
      'note_id': 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
      'local_content_hash': List<String>.filled(64, 'a').join(),
      'remote_fingerprint': List<String>.filled(64, 'b').join(),
    });

    expect(state.resources, isEmpty);
  });

  test('resource ownership marker cannot be replaced', () async {
    final fixture = _JoplinFixture();
    final exporter = await _exporter(fixture);
    final document = _documentWithAttachment();
    await exporter.exportDocument(document, idempotencyKey: 'resource-first');
    final resourceId = fixture.resources.keys.single;
    fixture.resources[resourceId]!['user_data'] = '{"application":"other"}';

    final error = await exporter
        .exportDocument(
          document,
          idempotencyKey: 'resource-owner-changed',
          overwriteExternalChanges: true,
        )
        .then<JoplinExportConflict?>((_) => null, onError: (error) => error);

    expect(error, isNotNull);
    expect(error!.resourceId, resourceId);
    expect(error.canOverwrite, isFalse);
    expect(fixture.count('PUT', '/resources/$resourceId'), 1);
  });

  test(
    'removing a local attachment leaves the remote resource intact',
    () async {
      final fixture = _JoplinFixture();
      final exporter = await _exporter(fixture);
      await exporter.exportDocument(
        _documentWithAttachment(),
        idempotencyKey: 'resource-first',
      );
      final resourceId = fixture.resources.keys.single;

      final receipt = await exporter.export(
        _document,
        _annotations,
        idempotencyKey: 'resource-removed',
      );

      expect(receipt.disposition, ExportDisposition.updated);
      expect(fixture.resources, contains(resourceId));
      expect(fixture.count('DELETE', '/resources/$resourceId'), 0);
      expect(fixture.notes.values.single['body'], isNot(contains('## 附件')));
    },
  );

  test(
    'remote edits stop by default and require explicit replacement',
    () async {
      final fixture = _JoplinFixture();
      final exporter = await _exporter(fixture);
      await exporter.export(_document, _annotations, idempotencyKey: 'first');
      fixture.notes.values.single['body'] = '用户在 Joplin 中修改的正文';

      await expectLater(
        exporter.export(_document, _annotations, idempotencyKey: 'second'),
        throwsA(isA<JoplinExportConflict>()),
      );
      expect(fixture.notes.values.single['body'], contains('用户在 Joplin'));

      final receipt = await exporter.export(
        _document,
        _annotations,
        idempotencyKey: 'third',
        overwriteExternalChanges: true,
      );
      expect(receipt.disposition, ExportDisposition.overwritten);
      expect(fixture.notes.values.single['body'], contains('# 测试书'));
    },
  );

  test('remote deletion is not silently recreated', () async {
    final fixture = _JoplinFixture();
    final exporter = await _exporter(fixture);
    await exporter.export(_document, _annotations, idempotencyKey: 'first');
    fixture.notes.clear();

    await expectLater(
      exporter.export(_document, _annotations, idempotencyKey: 'second'),
      throwsA(
        isA<JoplinExportConflict>().having(
          (conflict) => conflict.reason,
          'reason',
          contains('删除'),
        ),
      ),
    );
    expect(fixture.count('POST', '/notes'), 1);

    final receipt = await exporter.export(
      _document,
      _annotations,
      idempotencyKey: 'third',
      overwriteExternalChanges: true,
    );
    expect(receipt.disposition, ExportDisposition.overwritten);
    expect(fixture.notes, hasLength(1));
    expect(fixture.count('POST', '/notes'), 2);
  });

  test('ambiguous create is recovered without a duplicate note', () async {
    final fixture = _JoplinFixture(failAfterFirstNoteCreate: true);
    final exporter = await _exporter(fixture);

    await expectLater(
      exporter.export(_document, _annotations, idempotencyKey: 'first'),
      throwsA(
        isA<CoreException>().having(
          (error) => error.message,
          'safe message',
          isNot(contains(_token)),
        ),
      ),
    );
    expect(fixture.notes, hasLength(1));

    final recovered = await exporter.export(
      _document,
      _annotations,
      idempotencyKey: 'second',
    );
    expect(recovered.disposition, ExportDisposition.unchanged);
    expect(fixture.notes, hasLength(1));
    expect(fixture.count('POST', '/notes'), 1);
  });

  test('an occupied deterministic ID is never overwritten', () async {
    final fixture = _JoplinFixture();
    final exporter = await _exporter(fixture);
    final configuration = _configuration;
    final noteId = deterministicJoplinId(
      'oohstory-joplin-note-v1',
      '${configuration.targetKey}\u0000${_document.id}',
    );
    fixture.notes[noteId] = <String, Object?>{
      'id': noteId,
      'parent_id': _notebookId,
      'title': '其他应用笔记',
      'body': '不可覆盖',
      'application_data': '',
      'source': 'other',
    };

    final error = await exporter
        .export(
          _document,
          _annotations,
          idempotencyKey: 'collision',
          overwriteExternalChanges: true,
        )
        .then<JoplinExportConflict?>((_) => null, onError: (error) => error);

    expect(error, isNotNull);
    expect(error!.canOverwrite, isFalse);
    expect(fixture.notes[noteId]!['body'], '不可覆盖');
    expect(fixture.count('PUT', '/notes/$noteId'), 0);
  });
}

JoplinApiClient _client(CloudHttpTransport transport) => JoplinApiClient(
  configuration: _configuration,
  transport: transport,
  token: () async => _token,
  retryPolicy: const RetryPolicy(maxAttempts: 1),
);

Future<JoplinAnnotationExporter> _exporter(_JoplinFixture fixture) async {
  final preferences = await SharedPreferences.getInstance();
  return JoplinAnnotationExporter(
    configuration: _configuration,
    api: _client(fixture.transport),
    stateStore: SharedPreferencesJoplinExportStateStore(preferences),
    now: () => DateTime.utc(2026, 9, 15),
  );
}

JoplinExportConfiguration get _configuration =>
    JoplinExportConfiguration(port: 41184, notebookId: _notebookId);

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
    location: 'progress:0.5',
    text: '高亮正文',
    note: '我的笔记',
    type: 'highlight',
    createdAt: DateTime.utc(2026, 9, 15),
  ),
];

AnnotationExportDocument _documentWithAttachment({
  List<int>? bytes,
  String fileName = 'cover.png',
}) => _documentWithAttachments(<AnnotationExportAttachment>[
  _attachment('attachment-1', fileName, bytes ?? utf8.encode('image bytes')),
]);

AnnotationExportDocument _documentWithAttachments(
  List<AnnotationExportAttachment> attachments,
) => AnnotationExportDocument(
  identity: _document,
  annotations: _annotations,
  attachments: attachments,
);

AnnotationExportAttachment _attachment(
  String id,
  String fileName,
  List<int> bytes,
) => AnnotationExportAttachment(
  id: id,
  bookId: _document.id,
  annotationId: _annotations.single.id,
  fileName: fileName,
  mediaType: 'image/png',
  bytes: bytes,
  provenance: AnnotationAttachmentProvenance(
    source: 'oohstory-local-note',
    sourceId: 'annotation-1/$id',
  ),
);

final class _JoplinFixture {
  _JoplinFixture({
    this.failAfterFirstNoteCreate = false,
    this.failAfterFirstResourceCreate = false,
  }) : transport = FixtureTransport(
         (_, __) async => throw StateError('unset'),
       ) {
    transport = FixtureTransport(_handle);
  }

  final bool failAfterFirstNoteCreate;
  final bool failAfterFirstResourceCreate;
  late FixtureTransport transport;
  final Map<String, Map<String, Object?>> notes = {};
  final Map<String, String> tags = {};
  final Map<String, Set<String>> noteTags = {};
  final Map<String, Map<String, Object?>> resources = {};
  final Map<String, Uint8List> resourceBytes = {};
  bool _failedCreate = false;
  bool _failedResourceCreate = false;

  int count(String method, String path) => transport.requests
      .where((request) => request.method == method && request.uri.path == path)
      .length;

  int countMatching(String method, RegExp path) => transport.requests
      .where(
        (request) =>
            request.method == method && path.hasMatch(request.uri.path),
      )
      .length;

  Future<CloudHttpResponse> _handle(
    CloudHttpRequest request,
    Uint8List bytes,
  ) async {
    final path = request.uri.path;
    final contentType = request.headers['content-type'] ?? '';
    final multipart = contentType.startsWith('multipart/form-data;');
    final body = bytes.isEmpty || multipart
        ? <String, Object?>{}
        : Map<String, Object?>.from(jsonDecode(utf8.decode(bytes)) as Map);

    final resourceMatch = RegExp(
      r'^/resources/([0-9a-f]{32})$',
    ).firstMatch(path);
    final resourceFileMatch = RegExp(
      r'^/resources/([0-9a-f]{32})/file$',
    ).firstMatch(path);
    if (request.method == 'GET' && resourceFileMatch != null) {
      final data = resourceBytes[resourceFileMatch.group(1)!];
      return data == null
          ? jsonResponse(404, '{}')
          : CloudHttpResponse.bytes(statusCode: 200, body: data);
    }
    if (request.method == 'GET' && resourceMatch != null) {
      final resource = resources[resourceMatch.group(1)!];
      return resource == null
          ? jsonResponse(404, '{}')
          : jsonResponse(200, jsonEncode(resource));
    }
    if (request.method == 'GET' && path == '/resources') {
      return jsonResponse(
        200,
        jsonEncode(<String, Object?>{
          'items': resources.values
              .map(
                (item) => <String, Object?>{
                  'id': item['id'],
                  'user_data': item['user_data'],
                },
              )
              .toList(growable: false),
          'has_more': false,
        }),
      );
    }
    if (request.method == 'POST' && path == '/resources') {
      final payload = _parseMultipart(contentType, bytes);
      final id = payload.props['id']! as String;
      resources[id] = <String, Object?>{
        'id': id,
        'title': payload.props['title'],
        'mime': 'application/octet-stream',
        'filename': '',
        'user_data': '',
        'size': payload.data.length,
      };
      resourceBytes[id] = payload.data;
      if (failAfterFirstResourceCreate && !_failedResourceCreate) {
        _failedResourceCreate = true;
        throw StateError('simulated resource socket close after commit');
      }
      return jsonResponse(200, jsonEncode(resources[id]));
    }
    if (request.method == 'PUT' && resourceMatch != null && multipart) {
      final id = resourceMatch.group(1)!;
      if (!resources.containsKey(id)) return jsonResponse(404, '{}');
      final payload = _parseMultipart(contentType, bytes);
      resources[id] = <String, Object?>{
        ...resources[id]!,
        'title': payload.props['title'],
        'mime': 'application/octet-stream',
        'size': payload.data.length,
      };
      resourceBytes[id] = payload.data;
      return jsonResponse(200, jsonEncode(resources[id]));
    }
    if (request.method == 'PUT' && resourceMatch != null) {
      final id = resourceMatch.group(1)!;
      final resource = resources[id];
      if (resource == null) return jsonResponse(404, '{}');
      resource.addAll(body);
      return jsonResponse(200, jsonEncode(resource));
    }

    if (request.method == 'GET' &&
        path.startsWith('/notes/') &&
        !path.endsWith('/tags')) {
      final id = path.substring('/notes/'.length);
      final note = notes[id];
      return note == null
          ? jsonResponse(404, '{}')
          : jsonResponse(200, jsonEncode(note));
    }
    if (request.method == 'POST' && path == '/notes') {
      final id = body['id']! as String;
      if (notes.containsKey(id)) return jsonResponse(409, '{}');
      notes[id] = <String, Object?>{...body, 'application_data': ''}
        ..remove('source_application');
      if (failAfterFirstNoteCreate && !_failedCreate) {
        _failedCreate = true;
        throw StateError('simulated socket close after commit');
      }
      return jsonResponse(200, jsonEncode(notes[id]));
    }
    if (request.method == 'PUT' && path.startsWith('/notes/')) {
      final id = path.substring('/notes/'.length);
      final note = notes[id];
      if (note == null) return jsonResponse(404, '{}');
      note.addAll(body);
      return jsonResponse(200, jsonEncode(note));
    }
    if (request.method == 'GET' && path.startsWith('/tags/')) {
      final id = path.substring('/tags/'.length);
      final title = tags[id];
      return title == null
          ? jsonResponse(404, '{}')
          : jsonResponse(
              200,
              jsonEncode(<String, Object>{'id': id, 'title': title}),
            );
    }
    if (request.method == 'POST' && path == '/tags') {
      final id = body['id']! as String;
      final title = body['title']! as String;
      tags[id] = title;
      return jsonResponse(
        200,
        jsonEncode(<String, Object>{'id': id, 'title': title}),
      );
    }
    final tagLink = RegExp(r'^/tags/([0-9a-f]{32})/notes$').firstMatch(path);
    if (request.method == 'POST' && tagLink != null) {
      final noteId = body['id']! as String;
      noteTags.putIfAbsent(noteId, () => <String>{}).add(tagLink.group(1)!);
      return jsonResponse(200, '{}');
    }
    final noteTagList = RegExp(
      r'^/notes/([0-9a-f]{32})/tags$',
    ).firstMatch(path);
    if (request.method == 'GET' && noteTagList != null) {
      final ids = noteTags[noteTagList.group(1)!] ?? const <String>{};
      return jsonResponse(
        200,
        jsonEncode(<String, Object>{
          'items': ids.map((id) => <String, String>{'id': id}).toList(),
          'has_more': false,
        }),
      );
    }
    return jsonResponse(500, '{}');
  }
}

final class _MultipartFixturePayload {
  const _MultipartFixturePayload({required this.props, required this.data});

  final Map<String, Object?> props;
  final Uint8List data;
}

_MultipartFixturePayload _parseMultipart(String contentType, Uint8List bytes) {
  final boundaryMatch = RegExp(r'boundary=([^;]+)').firstMatch(contentType);
  if (boundaryMatch == null) throw StateError('missing multipart boundary');
  final boundary = boundaryMatch.group(1)!;
  final headerEnd = utf8.encode('\r\n\r\n');
  final nextPart = utf8.encode('\r\n--$boundary\r\n');
  final closing = utf8.encode('\r\n--$boundary--\r\n');
  final propsStart = _indexOf(bytes, headerEnd) + headerEnd.length;
  final propsEnd = _indexOf(bytes, nextPart, propsStart);
  final dataHeadersEnd =
      _indexOf(bytes, headerEnd, propsEnd + nextPart.length) + headerEnd.length;
  final dataEnd = _indexOf(bytes, closing, dataHeadersEnd);
  if (propsStart < headerEnd.length ||
      propsEnd < propsStart ||
      dataHeadersEnd < headerEnd.length ||
      dataEnd < dataHeadersEnd) {
    throw StateError('invalid multipart fixture body');
  }
  final props = Map<String, Object?>.from(
    jsonDecode(utf8.decode(bytes.sublist(propsStart, propsEnd))) as Map,
  );
  return _MultipartFixturePayload(
    props: props,
    data: Uint8List.fromList(bytes.sublist(dataHeadersEnd, dataEnd)),
  );
}

int _indexOf(Uint8List source, List<int> pattern, [int start = 0]) {
  for (var index = start; index <= source.length - pattern.length; index++) {
    var matches = true;
    for (var offset = 0; offset < pattern.length; offset++) {
      if (source[index + offset] != pattern[offset]) {
        matches = false;
        break;
      }
    }
    if (matches) return index;
  }
  return -1;
}
