import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/core/product_capabilities.dart';
import 'package:oohstory/screens/offline_notes_screen.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues(<String, Object>{
      'oohstory_offline_annotations_v1': jsonEncode(<Map<String, Object>>[
        <String, Object>{
          'id': 'annotation-1',
          'bookId': 'book-1',
          'type': 'highlight',
          'excerpt': '一条高亮',
          'note': '',
          'progress': 0.5,
          'createdAt': 1,
        },
      ]),
    });
  });

  testWidgets('Obsidian action stays hidden while capability is disabled', (
    tester,
  ) async {
    await tester.pumpWidget(const MaterialApp(home: OfflineNotesScreen()));
    await tester.pumpAndSettle();

    expect(find.byTooltip('导出到 Obsidian'), findsNothing);
    expect(find.byTooltip('导出'), findsOneWidget);
  });

  testWidgets('verified native capability exposes the Obsidian action', (
    tester,
  ) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: OfflineNotesScreen(
          capabilities: ProductCapabilityProfile(obsidianExportEnabled: true),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byTooltip('导出到 Obsidian'), findsOneWidget);
  });

  testWidgets('Notion action is independently gated', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: OfflineNotesScreen(
          capabilities: ProductCapabilityProfile(notionExportEnabled: true),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byTooltip('Notion 导出'), findsOneWidget);
    expect(find.byTooltip('导出到 Obsidian'), findsNothing);
  });

  testWidgets('Joplin action is independently gated on desktop', (
    tester,
  ) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: OfflineNotesScreen(
          capabilities: ProductCapabilityProfile(joplinExportEnabled: true),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byTooltip('Joplin 导出'), findsOneWidget);
    expect(find.byTooltip('Notion 导出'), findsNothing);
    expect(find.byTooltip('导出到 Obsidian'), findsNothing);
  });

  testWidgets('Joplin setup verifies Data API before notebook selection', (
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
    await tester.pumpWidget(
      const MaterialApp(
        home: OfflineNotesScreen(
          capabilities: ProductCapabilityProfile(joplinExportEnabled: true),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('Joplin 导出'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('配置桌面连接'));
    await tester.pumpAndSettle();

    expect(find.text('Web Clipper 端口'), findsOneWidget);
    expect(find.text('自动查找本机 Joplin'), findsOneWidget);
    expect(find.text('Data API token'), findsOneWidget);
    expect(find.text('验证并读取笔记本'), findsOneWidget);
    expect(find.text('保存并导出'), findsOneWidget);
    expect(find.text('目标笔记本 ID'), findsNothing);
  });

  testWidgets('stored attachments are visible from the annotation menu', (
    tester,
  ) async {
    SharedPreferences.setMockInitialValues(<String, Object>{
      'oohstory_offline_annotations_v1': jsonEncode(<Map<String, Object>>[
        <String, Object>{
          'id': 'annotation-1',
          'bookId': 'book-1',
          'type': 'highlight',
          'excerpt': '一条高亮',
          'note': '',
          'progress': 0.5,
          'createdAt': 1,
        },
      ]),
      'oohstory_offline_annotation_attachments_v1': jsonEncode(
        <Map<String, Object>>[
          <String, Object>{
            'id': 'attachment_000000000000000000000000',
            'bookId': 'book-1',
            'annotationId': 'annotation-1',
            'fileName': '证据.pdf',
            'mediaType': 'application/pdf',
            'byteLength': 42,
            'contentSha256': 'hash',
            'createdAt': 2,
          },
        ],
      ),
    });

    await tester.pumpWidget(const MaterialApp(home: OfflineNotesScreen()));
    await tester.pumpAndSettle();

    expect(find.textContaining('1 个附件'), findsOneWidget);
    await tester.tap(find.byTooltip('管理批注'));
    await tester.pumpAndSettle();
    expect(find.text('添加附件'), findsOneWidget);
    expect(find.text('管理附件 (1)'), findsOneWidget);
    expect(find.text('删除批注'), findsOneWidget);
  });
}
