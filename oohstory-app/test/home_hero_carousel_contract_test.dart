import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/models/book.dart';
import 'package:oohstory/theme/app_theme.dart';
import 'package:oohstory/widgets/home_hero_carousel.dart';

void main() {
  List<Book> books(int count) => List.generate(
    count,
    (index) => Book(
      id: 'book-${index + 1}',
      title: '作品${index + 1}',
      author: '作者${index + 1}',
      description: '第${index + 1}本精选作品简介',
      category: '轻小说',
      chapterCount: 100 + index,
      status: index.isEven ? 'finished' : 'ongoing',
    ),
  );

  testWidgets('home hero exposes exactly seven selectable novels', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        home: Scaffold(
          body: OohHomeHeroCarousel(
            books: books(9),
            coverUrlFor: (_) => '',
            synopsisFor: (book) => book.description!,
            chapterLabelFor: (book) => '${book.chapterCount}章',
            onOpen: (_) {},
            autoplayInterval: const Duration(hours: 1),
          ),
        ),
      ),
    );
    await tester.pump();

    final card = tester.getSize(
      find.byKey(const ValueKey('home-hero-carousel-card')),
    );
    expect(card.height, lessThanOrEqualTo(222));
    expect(card.height, greaterThanOrEqualTo(210));

    for (var index = 0; index < 7; index++) {
      expect(find.byKey(ValueKey('home-hero-tab-$index')), findsOneWidget);
      expect(
        find.descendant(
          of: find.byKey(const ValueKey('home-hero-carousel-card')),
          matching: find.byKey(ValueKey('home-hero-tab-$index')),
        ),
        findsOneWidget,
      );
    }
    expect(find.byKey(const ValueKey('home-hero-tab-7')), findsNothing);
    expect(find.text('1 / 7'), findsOneWidget);
    expect(
      find.descendant(
        of: find.byKey(const ValueKey('home-hero-carousel-card')),
        matching: find.text('1 / 7'),
      ),
      findsOneWidget,
    );

    await tester.tap(find.byKey(const ValueKey('home-hero-tab-6')));
    await tester.pumpAndSettle();

    expect(find.text('7 / 7'), findsOneWidget);
    expect(find.text('作品7'), findsWidgets);
  });
}
