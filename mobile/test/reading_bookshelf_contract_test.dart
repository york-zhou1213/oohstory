import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/models/book.dart';
import 'package:oohstory/services/local_storage_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('reading a chapter automatically records shelf progress', () async {
    SharedPreferences.setMockInitialValues({});
    final storage = LocalStorageService();
    await storage.init();
    final book = Book(
      id: 'book-auto-shelf',
      title: '自动入架测试',
      author: 'OOHStory',
      chapterCount: 10,
    );

    storage.recordRead(
      book,
      'chapter-3',
      '第三章 星门初启',
      chapterPosition: 3,
      chapterCount: 10,
      chapterProgress: .5,
    );

    final entry = storage.getHistory().single;
    expect(entry.book.id, 'book-auto-shelf');
    expect(entry.lastChapterTitle, '第三章 星门初启');
    expect(entry.lastChapterPosition, 3);
    expect(entry.chapterProgress, .5);
    expect(entry.overallProgress, .25);
  });

  test('reader and account sync keep automatic shelf contract', () {
    final reader = File('lib/screens/reader_screen.dart').readAsStringSync();
    final account = File(
      'lib/services/account_service.dart',
    ).readAsStringSync();
    final shelf = File(
      'lib/screens/reading_bookshelf_screen.dart',
    ).readAsStringSync();

    expect(reader, contains('_recordReadingProgress(progress)'));
    expect(reader, contains('syncReadingEntry(checkpoint)'));
    expect(reader, contains('ProgressSyncRuntime.instance.record'));
    expect(reader, contains('ProgressSyncRuntime.instance.synchronize'));
    expect(account, contains("'bookshelf': [_automaticShelfState(entry)]"));
    expect(shelf, contains('lastChapterTitle'));
    expect(shelf, contains('overallProgress'));
    expect(shelf, contains('读过的作品会自动加入这里'));
  });

  test('local reader exposes the shared annotation attachment controls', () {
    final reader = File(
      'lib/screens/local_reader_screen.dart',
    ).readAsStringSync();

    expect(reader, contains('pickAnnotationAttachment('));
    expect(reader, contains('showAnnotationAttachmentsDialog('));
    expect(reader, contains("' · \${attachments.length} 个附件'"));
    expect(reader, contains("child: Text('添加附件')"));
    expect(reader, contains("child: Text('管理附件 (\${attachments.length})')"));
  });
}
