import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/contracts/adapter_contracts.dart';
import 'package:oohstory/adapters/formats/formats.dart';
import 'package:oohstory/core/core.dart';

import '../fixtures/formats/fixture_factory.dart';

void main() {
  const decoder = KindleFormatDecoder();

  test('probes Kindle media types and Palm database signature', () async {
    final fixture = kindleFixture();
    for (final mediaType in <String>[
      'application/x-mobipocket-ebook',
      'application/vnd.amazon.ebook',
      'application/x-mobi8-ebook',
    ]) {
      expect(await decoder.probe(mediaType, const <int>[]), isTrue);
    }
    expect(
      await decoder.probe('application/octet-stream', fixture.sublist(0, 68)),
      isTrue,
    );
    expect(await decoder.probe('text/plain', const <int>[1, 2]), isFalse);
  });

  test('decodes DRM-free AZW3 metadata and text deterministically', () async {
    final result = await decoder.decodeBook(
      Stream<List<int>>.value(kindleFixture(compression: 2)),
    );

    expect(result.metadata.title, 'Fixture Book');
    expect(result.metadata.format, 'azw3');
    expect(result.metadata.textEncoding, 65001);
    expect(result.metadata.uniqueId, 42);
    expect(result.document.version, 'azw3:42:8');
    expect(result.document.sections, <String>['Chapter 1', 'Hello reader.']);
  });

  test('implements FormatDecoder for DRM-free MOBI content', () async {
    final FormatDecoder decoder = const KindleFormatDecoder();
    final document = await decoder.decode(
      Stream<List<int>>.value(kindleFixture(mobiVersion: 6)),
    );

    expect(document.version, 'mobi:42:6');
    expect(document.sections, isNotEmpty);
  });

  test('rejects encrypted Kindle content with unsupported error', () async {
    await expectLater(
      decoder.decode(Stream<List<int>>.value(kindleFixture(encryptionType: 1))),
      throwsA(
        isA<CoreException>().having(
          (error) => error.code,
          'code',
          CoreErrorCode.unsupported,
        ),
      ),
    );
  });

  test('enforces Kindle input and expansion limits', () async {
    const limited = KindleFormatDecoder(
      limits: FormatLimits(maxInputBytes: 8, maxExpandedBytes: 8),
    );
    await expectLater(
      limited.decode(Stream<List<int>>.value(kindleFixture())),
      throwsA(
        isA<CoreException>().having(
          (error) => error.code,
          'code',
          CoreErrorCode.payloadTooLarge,
        ),
      ),
    );
  });

  test('normalizes truncated Kindle input as validation error', () async {
    final fixture = kindleFixture();

    for (final length in <int>[0, 67, fixture.length - 1]) {
      await expectLater(
        decoder.decode(Stream<List<int>>.value(fixture.sublist(0, length))),
        throwsA(
          isA<CoreException>().having(
            (error) => error.code,
            'code',
            CoreErrorCode.validationError,
          ),
        ),
      );
    }
  });
}
