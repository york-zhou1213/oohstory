import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/dictionary/dictionary.dart';
import 'package:oohstory/core/capabilities.dart';
import 'package:oohstory/core/errors.dart';

import 'mdx_fixture.dart';

void main() {
  group('MdxDictionaryAdapter', () {
    test(
      'decodes zlib MDX blocks and returns duplicate definitions in order',
      () async {
        final adapter = MdxDictionaryAdapter.fromBytes(buildMdxFixture());

        final first = await adapter.lookup(' APPLE ');
        final second = await adapter.lookup('apple', locale: 'en');

        expect(adapter.providerId, 'local-mdx');
        expect(
          adapter.capabilities.supports(AdapterCapability.dictionary),
          isTrue,
        );
        expect(adapter.entryCount, 3);
        expect(first.map((entry) => entry.definition), <String>[
          '<b>first</b>',
          '<b>second</b>',
        ]);
        expect(second.map((entry) => entry.definition), <String>[
          '<b>first</b>',
          '<b>second</b>',
        ]);
        expect(await adapter.lookup('missing'), isEmpty);
      },
    );

    test('decodes uncompressed MDX blocks', () async {
      final adapter = MdxDictionaryAdapter.fromBytes(
        buildMdxFixture(compressed: false),
      );

      expect(
        (await adapter.lookup('banana')).single.definition,
        'yellow fruit',
      );
    });

    test('rejects encrypted dictionaries explicitly', () {
      expect(
        () => MdxDictionaryAdapter.fromBytes(buildMdxFixture(encrypted: true)),
        throwsA(_coreError(CoreErrorCode.unsupported)),
      );
    });

    test('rejects malformed headers and block checksums', () {
      final badHeader = buildMdxFixture()..[8] ^= 0x01;
      final badKeyHeader = buildMdxFixture();
      final headerSize =
          (badKeyHeader[0] << 24) |
          (badKeyHeader[1] << 16) |
          (badKeyHeader[2] << 8) |
          badKeyHeader[3];
      badKeyHeader[4 + headerSize + 4] ^= 0x01;
      final badBlock = buildMdxFixture()
        ..[buildMdxFixture().length - 1] ^= 0x01;

      expect(
        () => MdxDictionaryAdapter.fromBytes(badHeader),
        throwsA(_coreError(CoreErrorCode.validationError)),
      );
      expect(
        () => MdxDictionaryAdapter.fromBytes(badKeyHeader),
        throwsA(_coreError(CoreErrorCode.validationError)),
      );
      expect(
        () => MdxDictionaryAdapter.fromBytes(badBlock),
        throwsA(_coreError(CoreErrorCode.validationError)),
      );
      expect(
        () => MdxDictionaryAdapter.fromBytes(Uint8List.fromList(<int>[0, 1])),
        throwsA(_coreError(CoreErrorCode.validationError)),
      );
    });

    test('enforces input, entry and definition boundaries', () {
      final fixture = buildMdxFixture();
      expect(
        () => MdxDictionaryAdapter.fromBytes(
          fixture,
          limits: MdxLimits(maxInputBytes: fixture.length - 1),
        ),
        throwsA(_coreError(CoreErrorCode.payloadTooLarge)),
      );
      expect(
        () => MdxDictionaryAdapter.fromBytes(
          fixture,
          limits: const MdxLimits(maxEntries: 2),
        ),
        throwsA(_coreError(CoreErrorCode.validationError)),
      );
      expect(
        () => MdxDictionaryAdapter.fromBytes(
          fixture,
          limits: const MdxLimits(maxDefinitionBytes: 4),
        ),
        throwsA(_coreError(CoreErrorCode.payloadTooLarge)),
      );
    });

    test('rejects blank lookup inputs deterministically', () async {
      final adapter = MdxDictionaryAdapter.fromBytes(buildMdxFixture());

      await expectLater(
        adapter.lookup('  '),
        throwsA(_coreError(CoreErrorCode.validationError)),
      );
      await expectLater(
        adapter.lookup('apple', locale: ' '),
        throwsA(_coreError(CoreErrorCode.validationError)),
      );
    });
  });
}

Matcher _coreError(CoreErrorCode code) =>
    isA<CoreException>().having((error) => error.code, 'code', code);
