import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('Android release never falls back to the debug signing key', () {
    final gradle = File('android/app/build.gradle.kts').readAsStringSync();

    expect(gradle, isNot(contains('signingConfigs.getByName("debug")')));
    expect(gradle, contains('rootProject.file("key.properties")'));
    expect(gradle, contains('signingConfigs.getByName("release")'));
    expect(File('android/key.properties.example').existsSync(), isTrue);
  });
}
