import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/models/book.dart';
import 'package:oohstory/screens/home_screen.dart';

void main() {
  Book bookWithDescription(String? description) => Book(
    id: 'book-1',
    title: '测试作品',
    author: '测试作者',
    description: description,
  );

  test('hero synopsis trims a non-empty book description', () {
    expect(heroSynopsisFor(bookWithDescription('  一段简介。\n  ')), '一段简介。');
  });

  test('hero synopsis falls back for missing or blank descriptions', () {
    for (final description in <String?>[null, '', '  \n\t']) {
      expect(
        heroSynopsisFor(bookWithDescription(description)),
        '打开作品详情，立即开始阅读。',
      );
    }
  });

  test('exact chapter count beats a stale approximate count', () {
    final book = Book.fromJson({
      'public_id': 'book-1',
      'title': '测试作品',
      'author': '测试作者',
      'chapter_count': 128,
      'approx_chapter_count': 120,
    });

    expect(book.chapterCount, 128);
    expect(heroChapterCountLabelFor(book), '128章');
  });

  test('nonpositive chapter counts are treated as missing', () {
    expect(
      Book.fromJson({
        'public_id': 'book-1',
        'chapter_count': 0,
        'approx_chapter_count': -1,
      }).chapterCount,
      isNull,
    );
    expect(
      Book.fromJson({
        'public_id': 'book-2',
        'chapter_count': 0,
        'approx_chapter_count': 92,
      }).chapterCount,
      92,
    );
  });

  test('hero chapter label follows each rotating book', () {
    final heroBooks = [
      Book(id: 'book-1', title: '作品一', author: '作者一', chapterCount: 128),
      Book(id: 'book-2', title: '作品二', author: '作者二', chapterCount: 246),
      Book(id: 'book-3', title: '作品三', author: '作者三'),
      Book(id: 'book-4', title: '作品四', author: '作者四', chapterCount: 0),
    ];

    expect(heroChapterCountLabelFor(heroBooks[0]), '128章');
    expect(heroChapterCountLabelFor(heroBooks[1]), '246章');
    expect(heroChapterCountLabelFor(heroBooks[2]), '?章');
    expect(heroChapterCountLabelFor(heroBooks[3]), '?章');
  });

  test(
    'hero cover scales from phone to tablet with a reader-friendly frame',
    () {
      expect(heroCoverWidthFor(296), 124);
      expect(heroCoverHeightFor(296), closeTo(176.08, 0.001));
      expect(heroCoverWidthFor(366), closeTo(128.1, 0.001));
      expect(heroCoverHeightFor(366), closeTo(181.902, 0.001));
      expect(heroCoverWidthFor(500), 175);
      expect(heroCoverHeightFor(500), closeTo(248.5, 0.001));
      expect(heroCoverWidthFor(900), 200);
    },
  );
}
