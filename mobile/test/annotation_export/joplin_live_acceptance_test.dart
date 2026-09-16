import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/cloud/cloud.dart';
import 'package:oohstory/core/core.dart';
import 'package:oohstory/features/annotation_export/annotation_export.dart';
import 'package:shared_preferences/shared_preferences.dart';

final _livePort = int.tryParse(
  Platform.environment['OOHSTORY_JOPLIN_E2E_PORT'] ?? '',
);
final _liveToken = Platform.environment['OOHSTORY_JOPLIN_E2E_TOKEN'];
final _liveNotebookId = Platform.environment['OOHSTORY_JOPLIN_E2E_NOTEBOOK_ID'];
final _skipReason =
    _livePort == null || _liveToken == null || _liveNotebookId == null
    ? 'requires an explicit disposable Joplin Data API instance'
    : false;

void main() {
  test(
    'live Data API exports note tags resource idempotency and conflict recovery',
    () async {
      SharedPreferences.setMockInitialValues(<String, Object>{});
      final preferences = await SharedPreferences.getInstance();
      final configuration = JoplinExportConfiguration(
        port: _livePort!,
        notebookId: _liveNotebookId!,
      );
      final api = JoplinApiClient(
        configuration: configuration,
        transport: PackageHttpTransport(),
        token: () async => _liveToken!,
        retryPolicy: const RetryPolicy(maxAttempts: 1),
      );
      final stateStore = SharedPreferencesJoplinExportStateStore(preferences);
      final exporter = JoplinAnnotationExporter(
        configuration: configuration,
        api: api,
        stateStore: stateStore,
        now: () => DateTime.utc(2026, 9, 16),
      );
      final attachmentBytes = utf8.encode('OOHStory live Joplin resource');
      final document = AnnotationExportDocument(
        identity: const DocumentIdentity(
          id: 'joplin-live-e2e-book',
          title: 'OOHStory Joplin Live E2E',
          author: 'OOHStory',
          documentVersion: 'live-v1',
        ),
        annotations: <Annotation>[
          Annotation(
            id: 'joplin-live-e2e-annotation',
            bookId: 'joplin-live-e2e-book',
            location: 'progress:0.5',
            text: '真实 Data API 高亮',
            note: '真实写入与读回验证',
            type: 'highlight',
            createdAt: DateTime.utc(2026, 9, 16),
          ),
        ],
        attachments: <AnnotationExportAttachment>[
          AnnotationExportAttachment(
            id: 'joplin-live-e2e-attachment',
            bookId: 'joplin-live-e2e-book',
            annotationId: 'joplin-live-e2e-annotation',
            fileName: 'live-evidence.txt',
            mediaType: 'text/plain',
            bytes: attachmentBytes,
            provenance: const AnnotationAttachmentProvenance(
              source: 'oohstory.live-acceptance',
              sourceId: 'joplin-live-e2e-attachment',
            ),
          ),
        ],
      );

      await api.probe();
      final notebooks = await api.listNotebooks();
      expect(
        notebooks.map((item) => item.id),
        contains(configuration.notebookId),
      );
      expect(await api.verifyNotebook(), 'OOHStory E2E');

      final first = await exporter.exportDocument(
        document,
        idempotencyKey: 'live-first',
      );
      expect(first.disposition, ExportDisposition.created);
      final second = await exporter.exportDocument(
        document,
        idempotencyKey: 'live-second',
      );
      expect(second.disposition, ExportDisposition.unchanged);

      final noteId = first.target.substring('joplin:note:'.length);
      final note = await api.readNote(noteId);
      expect(note, isNotNull);
      expect(note!.parentId, configuration.notebookId);
      expect(note.title, 'OOHStory Joplin Live E2E');
      expect(note.body, contains('真实 Data API 高亮'));

      final state = await stateStore.read(
        targetKey: configuration.targetKey,
        documentId: document.identity.id,
      );
      expect(state, isNotNull);
      expect(state!.resources, hasLength(1));
      final resourceState = state.resources.single;
      expect(note.body, contains(':/${resourceState.resourceId}'));
      final resource = await api.readResource(resourceState.resourceId);
      expect(resource, isNotNull);
      expect(resource!.fileName, 'live-evidence.txt');
      expect(resource.mediaType, 'text/plain');
      expect(resource.size, attachmentBytes.length);
      expect(resource.contentHash, sha256.convert(attachmentBytes).toString());

      await api.updateNote(
        noteId: noteId,
        title: note.title,
        body: '${note.body}\n\n外部编辑',
        applicationData: note.applicationData,
      );
      await expectLater(
        exporter.exportDocument(document, idempotencyKey: 'live-conflict'),
        throwsA(isA<JoplinExportConflict>()),
      );
      final overwritten = await exporter.exportDocument(
        document,
        idempotencyKey: 'live-overwrite',
        overwriteExternalChanges: true,
      );
      expect(overwritten.disposition, ExportDisposition.overwritten);
      expect((await api.readNote(noteId))!.body, isNot(contains('外部编辑')));
    },
    skip: _skipReason,
    timeout: const Timeout(Duration(minutes: 2)),
  );
}
