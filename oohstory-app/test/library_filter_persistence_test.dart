import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test(
    'library category survives book navigation and screen reconstruction',
    () {
      final library = File(
        'lib/screens/library_screen.dart',
      ).readAsStringSync();
      final home = File('lib/screens/home_screen.dart').readAsStringSync();

      expect(library, contains("'oohstory_library_category'"));
      expect(library, contains('final String? initialCategory;'));
      expect(library, contains('_initializeLibrary()'));
      expect(library, contains('_persistCategory(value)'));
      expect(
        library,
        contains('preferences.getString(_categoryPreferenceKey)'),
      );
      expect(home, contains('LibraryScreen(initialCategory: catName)'));
    },
  );
}
