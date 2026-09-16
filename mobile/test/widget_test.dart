// This is a basic Flutter widget test.
//
// To perform an interaction with a widget in your test, use the WidgetTester
// utility in the flutter_test package. For example, you can send tap and scroll
// gestures. You can also use WidgetTester to find child widgets in the widget
// tree, read text, and verify that the values of widget properties are correct.

import 'package:flutter_test/flutter_test.dart';
import 'package:flutter/material.dart';

import 'package:oohstory/main.dart';
import 'package:oohstory/screens/reading_bookshelf_screen.dart';
import 'package:oohstory/screens/profile_screen.dart';

void main() {
  testWidgets('OOH Story app opens the main shell after the branded splash', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(const OohStoryApp(checkForUpdates: false));
    await tester.pump(const Duration(seconds: 2));
    await tester.pump(const Duration(milliseconds: 500));

    expect(find.text('发现'), findsWidgets);
    expect(find.text('书库'), findsOneWidget);
    expect(find.text('书架'), findsOneWidget);
    expect(find.text('拆书'), findsNothing);

    var rail = tester.widget<NavigationRail>(find.byType(NavigationRail));
    rail.onDestinationSelected!(2);
    await tester.pump();
    expect(find.byType(ReadingBookshelfScreen), findsOneWidget);

    rail = tester.widget<NavigationRail>(find.byType(NavigationRail));
    rail.onDestinationSelected!(3);
    await tester.pump();
    expect(find.byType(ProfileScreen), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
