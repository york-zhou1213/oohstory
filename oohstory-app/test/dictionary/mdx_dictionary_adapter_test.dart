import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/dictionary/_zlib_decoder.dart';
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

    test('rejects non-finite and malformed engine versions', () {
      for (final version in <String>['NaN', 'Infinity', '-Infinity', '2..0']) {
        expect(
          () => MdxDictionaryAdapter.fromBytes(
            buildMdxFixture(engineVersion: version),
          ),
          throwsA(_coreError(CoreErrorCode.unsupported)),
          reason: version,
        );
      }
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

    test('rejects an incomplete Huffman table accepted by the old decoder', () {
      final malformed = base64Decode(
        'eJwFwYcBwDAIAzBqNmH8/20tAbZmvmSZTaklRskwj7riGEHR86m9mM/PNI3y'
        'wSLpkLn66sNds6bVywGVRDIDpY8mJL/QFmTfR0hSV3fPGaMHKQ7dTGldHZpM'
        'fcxWJeFFLrA2Ootlob6ZWBOPiicb5N7NUw8TTH7buK2Qb8+grU4Whg6vozVn'
        'H0iUP6atoQPNGzsuDH1H3w/YtweK',
      );

      expect(
        () => decodeZlib(malformed, maxOutputBytes: 10000),
        throwsFormatException,
      );
    });

    test('decodes a literal-only dynamic block with no distance codes', () {
      final compressed = Uint8List.fromList(<int>[
        0x78,
        0x9c,
        0x05,
        0xc0,
        0x81,
        0x08,
        0x00,
        0x00,
        0x00,
        0x00,
        0x20,
        0xb6,
        0xfd,
        0xa5,
        0x4e,
        0x00,
        0x42,
        0x00,
        0x42,
      ]);

      expect(decodeZlib(compressed, maxOutputBytes: 1), <int>[65]);
    });

    test('rejects a distance reference when the distance table is empty', () {
      final malformed = Uint8List.fromList(<int>[
        0x78,
        0x9c,
        0x0d,
        0x80,
        0x81,
        0x08,
        0x00,
        0x00,
        0x00,
        0x80,
        0xd8,
        0xde,
        0x1f,
        0xea,
        0x63,
        0x00,
        0x00,
        0x00,
        0x00,
      ]);

      expect(
        () => decodeZlib(malformed, maxOutputBytes: 258),
        throwsFormatException,
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
