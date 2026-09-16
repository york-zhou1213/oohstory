import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/formats/formats.dart';
import 'package:oohstory/core/core.dart';

void main() {
  test('format providers report exact capabilities and default off', () {
    const kindle = KindleFormatDecoder();
    const comics = ComicArchiveFormatDecoder();
    final registry = CapabilityRegistry()
      ..register(kindle.capabilities)
      ..register(comics.capabilities);

    expect(kindle.capabilities.supported, <AdapterCapability>{
      AdapterCapability.textDecoding,
    });
    expect(comics.capabilities.supported, <AdapterCapability>{
      AdapterCapability.comicDecoding,
    });
    expect(registry.isEnabled(kindle.providerId), isFalse);
    expect(registry.isEnabled(comics.providerId), isFalse);
  });

  test('production format adapters remain pure Dart and platform-neutral', () {
    final files = Directory(
      'lib/adapters/formats',
    ).listSync().whereType<File>().where((file) => file.path.endsWith('.dart'));

    for (final file in files) {
      final source = file.readAsStringSync();
      expect(source, isNot(contains("import 'dart:io'")), reason: file.path);
      expect(source, isNot(contains('package:flutter/')), reason: file.path);
      expect(source, isNot(contains('/services/')), reason: file.path);
      expect(source, isNot(contains('/screens/')), reason: file.path);
    }
  });
}
