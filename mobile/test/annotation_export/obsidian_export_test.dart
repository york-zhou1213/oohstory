import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/core/core.dart';
import 'package:oohstory/features/annotation_export/annotation_export.dart';

void main() {
  late Directory vault;
  late _MemoryReceiptStore receipts;

  setUp(() async {
    vault = await Directory.systemTemp.createTemp('oohstory-obsidian-test-');
    receipts = _MemoryReceiptStore();
  });

  tearDown(() async {
    if (await vault.exists()) await vault.delete(recursive: true);
  });

  test(
    'writes deterministic readable Markdown with a safe stable name',
    () async {
      final exporter = ObsidianVaultExporter(
        vaultRoot: vault,
        subdirectory: 'OOHStory/阅读批注',
        receiptStore: receipts,
        now: () => DateTime.utc(2026, 9, 15, 4, 0),
      );

      final receipt = await exporter.export(
        _document,
        _annotations,
        idempotencyKey: 'book-1-v1',
      );

      expect(receipt.disposition, ExportDisposition.created);
      expect(receipt.target, endsWith('A-B-书名--ecda38a98aaa.md'));
      final markdown = await File(receipt.target).readAsString();
      expect(markdown, contains('oohstory_schema: 1'));
      expect(markdown, contains('title: "A/B:书名"'));
      expect(markdown, contains('annotation_count: 2'));
      expect(markdown, contains('# A/B:书名'));
      expect(markdown, contains('## 高亮 · 25%'));
      expect(markdown, contains('> 第一行\n> 第二行'));
      expect(markdown, contains('我的笔记'));
      expect(markdown, isNot(contains('2026-09-15T04:00:00.000Z')));
    },
  );

  test('repeated export is byte-idempotent', () async {
    final exporter = _exporter(vault, receipts);
    final first = await exporter.export(
      _document,
      _annotations,
      idempotencyKey: 'first',
    );
    final modifiedAt = await File(first.target).lastModified();

    final second = await exporter.export(
      _document,
      _annotations,
      idempotencyKey: 'second',
    );

    expect(second.disposition, ExportDisposition.unchanged);
    expect(second.contentHash, first.contentHash);
    expect(await File(second.target).lastModified(), modifiedAt);
  });

  test(
    'external edits block overwrite until forced and are backed up',
    () async {
      final exporter = _exporter(vault, receipts);
      final first = await exporter.export(
        _document,
        _annotations,
        idempotencyKey: 'first',
      );
      final target = File(first.target);
      await target.writeAsString(
        '${await target.readAsString()}\n用户在 Obsidian 的编辑\n',
      );

      await expectLater(
        exporter.export(_document, _annotations, idempotencyKey: 'blocked'),
        throwsA(
          isA<ObsidianExportConflict>().having(
            (error) => error.reason,
            'reason',
            contains('上次导出后被修改'),
          ),
        ),
      );

      final overwritten = await exporter.export(
        _document,
        _annotations,
        idempotencyKey: 'forced',
        overwriteExternalChanges: true,
      );
      expect(overwritten.disposition, ExportDisposition.overwritten);
      expect(overwritten.backupTarget, isNotNull);
      expect(
        await File(overwritten.backupTarget!).readAsString(),
        contains('用户在 Obsidian 的编辑'),
      );
      expect(await target.readAsString(), isNot(contains('用户在 Obsidian 的编辑')));
    },
  );

  test('existing untracked file is treated as an external conflict', () async {
    final exporter = _exporter(vault, receipts);
    final first = await exporter.export(
      _document,
      _annotations,
      idempotencyKey: 'discover-target',
    );
    receipts.clear();
    await File(first.target).writeAsString('untracked note');

    await expectLater(
      exporter.export(_document, _annotations, idempotencyKey: 'blocked'),
      throwsA(
        isA<ObsidianExportConflict>().having(
          (error) => error.reason,
          'reason',
          contains('没有可信导出回执'),
        ),
      ),
    );
  });

  test('rejects traversal and absolute export subdirectories', () {
    expect(
      () => ObsidianVaultExporter(
        vaultRoot: vault,
        subdirectory: '../outside',
        receiptStore: receipts,
      ),
      throwsFormatException,
    );
    expect(
      () => ObsidianVaultExporter(
        vaultRoot: vault,
        subdirectory: '/outside',
        receiptStore: receipts,
      ),
      throwsFormatException,
    );
  });

  test(
    'batch export reports conflicts without failing safe documents',
    () async {
      final exporter = _exporter(vault, receipts);
      final service = ObsidianAnnotationExportService(exporter);
      final documents = <ObsidianExportDocument>[
        ObsidianExportDocument(identity: _document, annotations: _annotations),
        const ObsidianExportDocument(
          identity: DocumentIdentity(
            id: 'book-2',
            title: '第二本',
            documentVersion: 'v1',
          ),
          annotations: <Annotation>[
            Annotation(
              id: 'a-3',
              bookId: 'book-2',
              location: 'progress:0.5',
              text: '另一条批注',
            ),
          ],
        ),
      ];
      final initial = await service.exportDocuments(documents);
      await File(initial.receipts.first.target).writeAsString('external edit');

      final result = await service.exportDocuments(documents);

      expect(result.conflicts, hasLength(1));
      expect(result.receipts, hasLength(1));
      expect(result.receipts.single.disposition, ExportDisposition.unchanged);
    },
  );
}

ObsidianVaultExporter _exporter(Directory vault, ExportReceiptStore receipts) =>
    ObsidianVaultExporter(
      vaultRoot: vault,
      subdirectory: 'OOHStory',
      receiptStore: receipts,
      now: () => DateTime.utc(2026, 9, 15, 4, 0),
    );

const _document = DocumentIdentity(
  id: 'book-1',
  title: 'A/B:书名',
  author: '作者',
  documentVersion: 'v1',
);

final _annotations = <Annotation>[
  Annotation(
    id: 'a-2',
    bookId: 'book-1',
    location: 'progress:0.75',
    text: '稍后的高亮',
    createdAt: DateTime.utc(2026, 9, 14),
  ),
  Annotation(
    id: 'a-1',
    bookId: 'book-1',
    location: 'progress:0.25',
    text: '第一行\n第二行',
    note: '我的笔记',
    createdAt: DateTime.utc(2026, 9, 13),
  ),
];

class _MemoryReceiptStore implements ExportReceiptStore {
  final _receipts = <String, ExportReceipt>{};

  @override
  Future<ExportReceipt?> read({
    required String providerId,
    required String documentId,
    required String target,
  }) async => _receipts['$providerId\u0000$documentId\u0000$target'];

  @override
  Future<void> write(ExportReceipt receipt) async {
    _receipts['${receipt.providerId}\u0000${receipt.documentId}\u0000${receipt.target}'] =
        receipt;
  }

  void clear() => _receipts.clear();
}
