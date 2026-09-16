import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test(
    'pure Dart core and contracts do not import Flutter or existing UI/services',
    () {
      final files = <File>[
        ...Directory('lib/core').listSync(recursive: true).whereType<File>(),
        ...Directory(
          'lib/adapters/contracts',
        ).listSync(recursive: true).whereType<File>(),
      ];
      for (final file in files) {
        final source = file.readAsStringSync();
        expect(source, isNot(contains("package:flutter/")), reason: file.path);
        expect(source, isNot(contains("/screens/")), reason: file.path);
        expect(source, isNot(contains("/services/")), reason: file.path);
      }
    },
  );
}
