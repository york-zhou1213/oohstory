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
}
