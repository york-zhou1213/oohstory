import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('cloud adapters stay inside the frozen provider boundary', () {
    final files = Directory(
      'lib/adapters/cloud',
    ).listSync(recursive: true).whereType<File>();
    for (final file in files) {
      final source = file.readAsStringSync();
      expect(source, isNot(contains('/screens/')), reason: file.path);
      expect(source, isNot(contains('/services/')), reason: file.path);
      expect(source, isNot(contains('SharedPreferences')), reason: file.path);
      expect(source, isNot(contains('localhost/proxy')), reason: file.path);
    }
  });
}
