import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/models/book.dart';
import 'package:oohstory/screens/volume_detail_screen.dart';

void main() {
  test('light novel volume preserves deep chapter and illustration paths', () {
    final volume = Volume.fromJson({
      'id': 1,
      'title': '正文',
      'chapter_ids': [1, 2],
      'illustration_count': 2,
      'cover_path': '',
      'illustration_paths': [
        '001-正文/插画/illustration-1.jpg',
        '001-正文/插画/illustration-2.jpg',
      ],
    });

    expect(volume.hasCover, isFalse);
    expect(volume.chapterIds, [1, 2]);
    expect(volume.illustrationPaths.first, '001-正文/插画/illustration-1.jpg');
  });

  test('volume page removes only its own repeated source prefix', () {
    expect(volumeChapterDisplayTitle('第一卷-第一章-开始与结束', '第一卷'), '第一章-开始与结束');
    expect(
      volumeChapterDisplayTitle('第二卷-黑暗战士-prologue', '第二卷 黑暗战士'),
      'prologue',
    );
    expect(
      volumeChapterDisplayTitle('短篇-三个妹子一台戏', 'BD特典 外传 亡国的吸血姬'),
      '短篇-三个妹子一台戏',
    );
  });

  test(
    'generic source volume title is completed without changing named volumes',
    () {
      expect(volumeDisplayTitle('OVERLORD不死者之王', '第一卷'), 'OVERLORD不死者之王 第一卷');
      expect(
        volumeDisplayTitle('约会大作战 DATE A LIVE', '约会大作战 DATE A LIVE 1 未路人十香'),
        '约会大作战 DATE A LIVE 1 未路人十香',
      );
    },
  );

  testWidgets('coverless light novel reuses the existing chapter tab UI', (
    WidgetTester tester,
  ) async {
    final volume = Volume(id: 1, title: '正文', chapterIds: const [1]);
    final chapter = Chapter(id: '1', title: '序章', position: 1);

    await tester.pumpWidget(
      MaterialApp(
        home: VolumeDetailScreen(
          bookId: 'test-book',
          volume: volume,
          chapters: [chapter],
        ),
      ),
    );

    expect(find.text('章节目录'), findsOneWidget);
    expect(find.textContaining('插画'), findsNothing);
    expect(find.text('本卷暂无可展示插画'), findsNothing);
    expect(find.text('序章'), findsOneWidget);
    expect(find.byType(TabBar), findsOneWidget);
    expect(find.byType(TabBarView), findsOneWidget);
    expect(find.byType(ListTile), findsOneWidget);
    expect(find.byIcon(Icons.auto_stories_rounded), findsNothing);
  });
}
