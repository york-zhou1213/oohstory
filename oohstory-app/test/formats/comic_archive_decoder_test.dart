import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/formats/formats.dart';
import 'package:oohstory/core/core.dart';

import '../fixtures/formats/fixture_factory.dart';

void main() {
  const decoder = ComicArchiveFormatDecoder();

  test('enumerates CBT pages in natural order', () async {
    final document = await decoder.decode(
      Stream<List<int>>.value(
        tarFixture(<String, int>{
          'pages/10.jpg': 1,
          'pages/2.jpg': 1,
          'pages/001.png': 1,
          'notes.txt': 1,
        }),
      ),
    );

    expect(document.version, 'cbt:tar:4');
    expect(document.sections, <String>[
      'pages/001.png',
      'pages/2.jpg',
      'pages/10.jpg',
    ]);
  });

  test('enumerates RAR4 CBR pages in natural order', () async {
    final document = await decoder.decode(
      Stream<List<int>>.value(
        rar4Fixture(<String, int>{'page12.webp': 1, 'page3.webp': 1}),
      ),
    );

    expect(document.version, 'cbr:rar4:2');
    expect(document.sections, <String>['page3.webp', 'page12.webp']);
  });

  test('enumerates RAR5 CBR pages in natural order', () async {
    final document = await decoder.decode(
      Stream<List<int>>.value(
        rar5Fixture(<String, int>{'page12.webp': 1, 'page3.webp': 1}),
      ),
    );

    expect(document.version, 'cbr:rar5:2');
    expect(document.sections, <String>['page3.webp', 'page12.webp']);
  });

  test('enumerates plain-header CB7 pages in natural order', () async {
    final document = await decoder.decode(
      Stream<List<int>>.value(
        sevenZipFixture(<String>['10.jpeg', '2.jpeg', 'metadata.json']),
      ),
    );

    expect(document.version, 'cb7:7z:3');
    expect(document.sections, <String>['2.jpeg', '10.jpeg']);
  });

  test(
    'decodes encoded Header with SubStreams and EmptyStream files',
    () async {
      // 7-Zip 23.01: 7z a -t7z -mtc=off -mta=off -mtm=off <fixture> ...
      final fixture = await File(
        'test/fixtures/formats/cb7-default-header.cb7',
      ).readAsBytes();
      final document = await decoder.decode(Stream<List<int>>.value(fixture));

      expect(document.version, 'cb7:7z:3');
      expect(document.sections, <String>['2.jpg', '10.jpg']);

      await expectLater(
        const ComicArchiveFormatDecoder(
          limits: FormatLimits(maxEntryBytes: 150),
        ).decode(Stream<List<int>>.value(fixture)),
        throwsA(
          isA<CoreException>().having(
            (error) => error.code,
            'code',
            CoreErrorCode.payloadTooLarge,
          ),
        ),
      );
    },
  );

  test('probe returns false for arbitrary binary TAR lookalikes', () async {
    final header = List<int>.filled(512, 0)..[257] = 0xff;

    expect(await decoder.probe('application/octet-stream', header), isFalse);
  });

  test('rejects traversal paths before filtering archive entries', () async {
    for (final path in <String>['../escape.jpg', r'C:\escape.jpg']) {
      await expectLater(
        decoder.decode(
          Stream<List<int>>.value(tarFixture(<String, int>{path: 1})),
        ),
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

  test('rejects corrupted archive checksums', () async {
    final tar = tarFixture(<String, int>{'page.jpg': 1})..[0] ^= 1;
    final sevenZip = sevenZipFixture(<String>['page.jpg'])..[8] ^= 1;

    for (final fixture in <List<int>>[tar, sevenZip]) {
      await expectLater(
        decoder.decode(Stream<List<int>>.value(fixture)),
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

  test('normalizes truncated archive inputs as validation errors', () async {
    final rar4 = rar4Fixture(<String, int>{'page.jpg': 1});
    final rar5 = rar5Fixture(<String, int>{'page.jpg': 1});
    final sevenZip = sevenZipFixture(<String>['page.jpg']);
    final fixtures = <List<int>>[
      tarFixture(<String, int>{'page.jpg': 1}).sublist(0, 513),
      rar4.sublist(0, rar4.length - 1),
      rar5.sublist(0, rar5.length - 1),
      sevenZip.sublist(0, sevenZip.length - 1),
    ];

    for (final fixture in fixtures) {
      await expectLater(
        decoder.decode(Stream<List<int>>.value(fixture)),
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

  test(
    'rejects encrypted RAR and AES-encrypted 7z headers explicitly',
    () async {
      for (final fixture in <List<int>>[
        rar4Fixture(<String, int>{'page.jpg': 1}, encrypted: true),
        await File(
          'test/fixtures/formats/cb7-encrypted-header.cb7',
        ).readAsBytes(),
      ]) {
        await expectLater(
          decoder.decode(Stream<List<int>>.value(fixture)),
          throwsA(
            isA<CoreException>().having(
              (error) => error.code,
              'code',
              CoreErrorCode.unsupported,
            ),
          ),
        );
      }
    },
  );

  test('enforces entry, page, size, and expansion limits', () async {
    final cases = <({ComicArchiveFormatDecoder decoder, List<int> fixture})>[
      (
        decoder: const ComicArchiveFormatDecoder(
          limits: FormatLimits(maxEntries: 1),
        ),
        fixture: tarFixture(<String, int>{'1.jpg': 1, '2.jpg': 1}),
      ),
      (
        decoder: const ComicArchiveFormatDecoder(
          limits: FormatLimits(maxPages: 1),
        ),
        fixture: tarFixture(<String, int>{'1.jpg': 1, '2.jpg': 1}),
      ),
      (
        decoder: const ComicArchiveFormatDecoder(
          limits: FormatLimits(maxEntryBytes: 4),
        ),
        fixture: rar4Fixture(<String, int>{'1.jpg': 1}, declaredSize: 5),
      ),
      (
        decoder: const ComicArchiveFormatDecoder(
          limits: FormatLimits(
            maxEntryBytes: 4096,
            maxExpandedBytes: 4096,
            maxExpansionRatio: 2,
          ),
        ),
        fixture: rar4Fixture(<String, int>{'1.jpg': 1}, declaredSize: 1000),
      ),
      (
        decoder: const ComicArchiveFormatDecoder(
          limits: FormatLimits(maxInputBytes: 32),
        ),
        fixture: tarFixture(<String, int>{'1.jpg': 1}),
      ),
    ];
    for (final value in cases) {
      await expectLater(
        value.decoder.decode(Stream<List<int>>.value(value.fixture)),
        throwsA(
          isA<CoreException>().having(
            (error) => error.code,
            'code',
            CoreErrorCode.payloadTooLarge,
          ),
        ),
      );
    }
  });
}
