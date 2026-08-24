import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test(
    'reader serializes chapter navigation and ignores old list positions',
    () {
      final source = File('lib/screens/reader_screen.dart').readAsStringSync();

      expect(source, contains('bool _chapterNavigationPending = false;'));
      expect(
        source,
        contains('if (_loading || _chapterNavigationPending) return;'),
      );
      expect(source, contains('_chapterNavigationPending = true;'));
      expect(source, contains('_chapterNavigationPending = false;'));
      expect(source, contains('unawaited(_loadChapter());'));
    },
  );

  test('reader initializes persistence before the first chapter load', () {
    final source = File('lib/screens/reader_screen.dart').readAsStringSync();

    expect(source, contains('unawaited(_initializeReader());'));
    expect(
      source,
      contains(
        'await _loadSettings();\n    if (mounted) await _loadChapter();',
      ),
    );
  });
}
