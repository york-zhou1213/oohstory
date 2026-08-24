import 'dart:ui' show SemanticsAction, SemanticsFlag;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/theme/app_theme.dart';
import 'package:oohstory/widgets/ooh_primary_navigation_bar.dart';

void main() {
  testWidgets('selected navigation surface contains both icon and label', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        home: Scaffold(
          bottomNavigationBar: OohPrimaryNavigationBar(
            selectedIndex: 2,
            onDestinationSelected: (_) {},
            destinations: const [
              OohPrimaryDestination(
                label: '发现',
                icon: Icons.explore_outlined,
                selectedIcon: Icons.explore_rounded,
              ),
              OohPrimaryDestination(
                label: '书库',
                icon: Icons.local_library_outlined,
                selectedIcon: Icons.local_library_rounded,
              ),
              OohPrimaryDestination(
                label: '书架',
                icon: Icons.shelves,
                selectedIcon: Icons.shelves,
              ),
              OohPrimaryDestination(
                label: '我的',
                icon: Icons.person_outline_rounded,
                selectedIcon: Icons.person_rounded,
              ),
            ],
          ),
        ),
      ),
    );

    final selectedButton = find.byKey(const ValueKey('primary-navigation-2'));
    expect(selectedButton, findsOneWidget);
    expect(
      find.descendant(of: selectedButton, matching: find.text('书架')),
      findsOneWidget,
    );
    expect(
      find.descendant(of: selectedButton, matching: find.byIcon(Icons.shelves)),
      findsOneWidget,
    );

    final background = tester.widget<AnimatedContainer>(
      find.descendant(
        of: selectedButton,
        matching: find.byType(AnimatedContainer),
      ),
    );
    final decoration = background.decoration! as BoxDecoration;
    expect(decoration.color, AppTheme.light().colorScheme.primaryContainer);
  });

  testWidgets('primary destinations expose their semantic tap action', (
    tester,
  ) async {
    final semantics = tester.ensureSemantics();
    int? selectedIndex;
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light(),
        home: Scaffold(
          bottomNavigationBar: OohPrimaryNavigationBar(
            selectedIndex: 1,
            onDestinationSelected: (index) => selectedIndex = index,
            destinations: const [
              OohPrimaryDestination(
                label: '发现',
                icon: Icons.explore_outlined,
                selectedIcon: Icons.explore_rounded,
              ),
              OohPrimaryDestination(
                label: '书库',
                icon: Icons.local_library_outlined,
                selectedIcon: Icons.local_library_rounded,
              ),
            ],
          ),
        ),
      ),
    );

    final library = find.bySemanticsLabel('书库');
    final librarySemantics = tester.getSemantics(library);
    expect(librarySemantics.label, '书库');
    expect(librarySemantics.hasFlag(SemanticsFlag.isButton), isTrue);
    expect(librarySemantics.hasFlag(SemanticsFlag.hasSelectedState), isTrue);
    expect(
      librarySemantics.getSemanticsData().hasAction(SemanticsAction.tap),
      isTrue,
    );

    await tester.sendKeyEvent(LogicalKeyboardKey.tab);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pump();
    expect(selectedIndex, 0);

    await tester.tap(library);
    expect(selectedIndex, 1);
    semantics.dispose();
  });
}
