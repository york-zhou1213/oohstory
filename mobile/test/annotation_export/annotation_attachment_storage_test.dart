import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/features/annotation_export/annotation_documents.dart';
import 'package:oohstory/services/local_storage_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late Directory workspace;
  late LocalStorageService storage;

  setUp(() async {
    SharedPreferences.setMockInitialValues(<String, Object>{});
    workspace = await Directory.systemTemp.createTemp(
      'oohstory-annotation-attachments-',
    );
    storage = LocalStorageService(
      documentsDirectory: () async => workspace,
      temporaryDirectory: () async => workspace,
    );
    await storage.init();
  });

  tearDown(() async {
    if (await workspace.exists()) await workspace.delete(recursive: true);
  });

  test(
    'persists a bounded attachment and exposes it to Joplin export',
    () async {
      final annotation = storage.addAnnotation(
        bookId: 'book-1',
        type: 'note',
        excerpt: '银河边缘',
        note: '现场资料',
        progress: .4,
      );
      final source = File('${workspace.path}/evidence.PDF');
      await source.writeAsBytes(<int>[1, 2, 3, 4], flush: true);

      final attachment = await storage.addAnnotationAttachment(
        annotationId: annotation.id,
        sourcePath: source.path,
        fileName: 'evidence.PDF',
      );

      expect(attachment.mediaType, 'application/pdf');
      expect(attachment.byteLength, 4);
      expect(storage.getAnnotationAttachments(), hasLength(1));
      expect(
        await storage.readAnnotationAttachment(attachment),
        Uint8List.fromList(<int>[1, 2, 3, 4]),
      );

      final restarted = LocalStorageService(
        documentsDirectory: () async => workspace,
      );
      await restarted.init();
      final document =
          (await StoredAnnotationExportSource.documentsWithAttachmentsFrom(
            restarted,
          )).single;
      final exported = document.attachments.single;
      expect(exported.annotationId, annotation.id);
      expect(exported.fileName, 'evidence.PDF');
      expect(exported.bytes, <int>[1, 2, 3, 4]);
      expect(exported.provenance.source, 'oohstory.local-attachment');
      expect(exported.provenance.sourceId, attachment.id);
    },
  );

  test(
    'reattaching the same content to one annotation is idempotent',
    () async {
      final annotation = storage.addAnnotation(
        bookId: 'book-1',
        type: 'highlight',
        excerpt: '相同证据',
        progress: .2,
      );
      final first = File('${workspace.path}/first.txt')
        ..writeAsStringSync('same-content');
      final second = File('${workspace.path}/second.txt')
        ..writeAsStringSync('same-content');

      final firstAttachment = await storage.addAnnotationAttachment(
        annotationId: annotation.id,
        sourcePath: first.path,
        fileName: 'first.txt',
      );
      final secondAttachment = await storage.addAnnotationAttachment(
        annotationId: annotation.id,
        sourcePath: second.path,
        fileName: 'second.txt',
      );

      expect(secondAttachment.id, firstAttachment.id);
      expect(storage.getAnnotationAttachments(), hasLength(1));
      expect(storage.getAnnotationAttachments().single.fileName, 'second.txt');
    },
  );

  test('detects an attachment changed outside the metadata store', () async {
    final annotation = storage.addAnnotation(
      bookId: 'book-1',
      type: 'bookmark',
      excerpt: '完整性',
      progress: .1,
    );
    final source = File('${workspace.path}/image.png')
      ..writeAsBytesSync(<int>[1, 2, 3]);
    final attachment = await storage.addAnnotationAttachment(
      annotationId: annotation.id,
      sourcePath: source.path,
      fileName: 'image.png',
    );
    await File(
      '${workspace.path}/annotation_attachments/${attachment.id}.bin',
    ).writeAsBytes(<int>[3, 2, 1], flush: true);

    expect(
      () => storage.readAnnotationAttachment(attachment),
      throwsA(
        isA<FormatException>().having(
          (error) => error.message,
          'message',
          '附件完整性校验失败',
        ),
      ),
    );
  });

  test(
    'deleting an annotation removes its attachment file and metadata',
    () async {
      final annotation = storage.addAnnotation(
        bookId: 'book-1',
        type: 'note',
        excerpt: '待删除',
        progress: .8,
      );
      final source = File('${workspace.path}/note.md')
        ..writeAsStringSync('# note');
      final attachment = await storage.addAnnotationAttachment(
        annotationId: annotation.id,
        sourcePath: source.path,
        fileName: 'note.md',
      );
      final storedFile = File(
        '${workspace.path}/annotation_attachments/${attachment.id}.bin',
      );
      expect(await storedFile.exists(), isTrue);

      await storage.removeAnnotation(annotation.id);

      expect(storage.getAnnotations(), isEmpty);
      expect(storage.getAnnotationAttachments(), isEmpty);
      expect(await storedFile.exists(), isFalse);
    },
  );

  test(
    'offline backup restores attachment metadata and verified bytes',
    () async {
      final annotation = storage.addAnnotation(
        bookId: 'book-1',
        type: 'note',
        excerpt: '需要迁移',
        progress: .6,
      );
      final source = File('${workspace.path}/archive.json')
        ..writeAsStringSync('{"ok":true}');
      final attachment = await storage.addAnnotationAttachment(
        annotationId: annotation.id,
        sourcePath: source.path,
        fileName: 'archive.json',
      );
      final backup = await storage.createOfflineBackup();

      SharedPreferences.setMockInitialValues(<String, Object>{});
      final restoredDirectory = await Directory.systemTemp.createTemp(
        'oohstory-restored-attachments-',
      );
      addTearDown(() async {
        if (await restoredDirectory.exists()) {
          await restoredDirectory.delete(recursive: true);
        }
      });
      final restored = LocalStorageService(
        documentsDirectory: () async => restoredDirectory,
        temporaryDirectory: () async => restoredDirectory,
      );
      await restored.init();

      await restored.restoreOfflineBackup(backup.path);

      final restoredAttachment = restored.getAnnotationAttachments().single;
      expect(restoredAttachment.toJson(), attachment.toJson());
      expect(
        await restored.readAnnotationAttachment(restoredAttachment),
        source.readAsBytesSync(),
      );
    },
  );

  test(
    'annotation export includes a verified attachment index and bytes',
    () async {
      final annotation = storage.addAnnotation(
        bookId: 'book-1',
        type: 'note',
        excerpt: '导出附件',
        note: '查看证据',
        progress: .3,
      );
      final source = File('${workspace.path}/evidence.PDF');
      await source.writeAsBytes(<int>[9, 8, 7, 6], flush: true);
      final attachment = await storage.addAnnotationAttachment(
        annotationId: annotation.id,
        sourcePath: source.path,
        fileName: 'evidence.PDF',
      );

      final output = await storage.createAnnotationExport();
      final archive = ZipDecoder().decodeBytes(await output.readAsBytes());
      final archivePath = 'attachments/${attachment.id}.pdf';
      final index =
          jsonDecode(
                utf8.decode(
                  archive.findFile('attachments.json')!.content as List<int>,
                ),
              )
              as List;
      final markdown = utf8.decode(
        archive.findFile('annotations.md')!.content as List<int>,
      );

      expect(index, hasLength(1));
      expect((index.single as Map)['archivePath'], archivePath);
      expect(markdown, contains('[evidence.PDF]($archivePath)'));
      expect(archive.findFile(archivePath)!.content as List<int>, <int>[
        9,
        8,
        7,
        6,
      ]);
    },
  );

  test('rejects attachments larger than the Joplin resource limit', () async {
    final annotation = storage.addAnnotation(
      bookId: 'book-1',
      type: 'note',
      excerpt: '过大附件',
      progress: .5,
    );
    final source = File('${workspace.path}/too-large.bin');
    await source.writeAsBytes(
      Uint8List(LocalStorageService.maxAnnotationAttachmentBytes + 1),
      flush: true,
    );

    expect(
      () => storage.addAnnotationAttachment(
        annotationId: annotation.id,
        sourcePath: source.path,
        fileName: 'too-large.bin',
      ),
      throwsA(
        isA<FormatException>().having(
          (error) => error.message,
          'message',
          '单个附件不能超过 16 MB',
        ),
      ),
    );
    expect(storage.getAnnotationAttachments(), isEmpty);
  });
}
