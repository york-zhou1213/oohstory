import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/screens/local_reader_screen.dart';
import 'package:oohstory/models/reader_preferences.dart';
import 'package:oohstory/services/local_dictionary_service.dart';
import 'package:oohstory/services/local_storage_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../dictionary/mdx_fixture.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late Directory root;
  late LocalStorageService storage;
  late LocalDictionaryService dictionaries;
  late LocalBookInfo book;

  setUp(() async {
    SharedPreferences.setMockInitialValues(<String, Object>{});
    final preferences = await SharedPreferences.getInstance();
    root = await Directory.systemTemp.createTemp('oohstory-reader-mdx-');
    storage = _ReaderStorage('Apple is shown in this formal offline reader.');
    await storage.init();
    book = LocalBookInfo(
      id: 'local_test',
      title: 'Test book',
      fileName: 'book.txt',
      fileSize: 45,
      addedAt: 1,
    );
    dictionaries = LocalDictionaryService(
      documentsDirectory: () async => root,
      preferences: preferences,
    );
    await dictionaries.init();
    await dictionaries.import(
      name: 'Reader.mdx',
      mdxBytes: buildMdxFixture(
        entries: const <MapEntry<String, String>>[
          MapEntry<String, String>('apple', '<b>local definition</b>'),
        ],
      ),
    );
  });

  tearDown(() async {
    if (await root.exists()) await root.delete(recursive: true);
  });

  testWidgets('formal local reader looks up selected text in persisted MDX', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: LocalReaderScreen(
          book: book,
          storage: storage,
          dictionaryService: dictionaries,
        ),
      ),
    );
    for (var attempt = 0; attempt < 30; attempt++) {
      await tester.pump(const Duration(milliseconds: 100));
      if (find.byType(SelectableText).evaluate().isNotEmpty) break;
    }
    expect(find.byType(SelectableText), findsOneWidget);

    final selectable = tester.widget<SelectableText>(
      find.byType(SelectableText).first,
    );
    selectable.onSelectionChanged!(
      const TextSelection(baseOffset: 0, extentOffset: 5),
      SelectionChangedCause.longPress,
    );
    await tester.tap(find.byTooltip('本地词典（优先查询所选文字）'));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));

    expect(find.textContaining('“Apple”的本地释义'), findsOneWidget);
    expect(find.text('Reader'), findsOneWidget);
    expect(find.textContaining('local definition'), findsWidgets);
    expect(find.byTooltip('管理词典'), findsOneWidget);
    Navigator.of(
      tester.element(find.textContaining('“Apple”的本地释义')),
    ).pop();
    await tester.pump(const Duration(seconds: 1));
  });
}

class _ReaderStorage extends LocalStorageService {
  _ReaderStorage(this.content);

  final String content;

  @override
  Future<void> init() async {}

  @override
  Future<String?> getLocalBookContent(String bookId) async => content;

  @override
  ReaderPreferences getReaderPreferences() => const ReaderPreferences();

  @override
  void updateLocalBookProgress(String bookId, double progress) {}

  @override
  void recordReadingSession(String bookId, Duration duration) {}
}
