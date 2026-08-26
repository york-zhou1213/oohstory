import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/ocr/local_ocr_adapter.dart';
import 'package:oohstory/core/models.dart';
import 'package:oohstory/features/local_content/local_content.dart';

import '../../dictionary/mdx_fixture.dart';
import '../../fixtures/formats/fixture_factory.dart';

void main() {
  group('LocalContentService', () {
    test('macOS picker entitlements allow user-selected read-only files', () {
      for (final path in const <String>[
        'macos/Runner/DebugProfile.entitlements',
        'macos/Runner/Release.entitlements',
      ]) {
        final entitlement = File(path).readAsStringSync();
        expect(
          entitlement,
          contains('com.apple.security.files.user-selected.read-only'),
          reason: path,
        );
        expect(
          RegExp(
            r'<key>com\.apple\.security\.files\.user-selected\.read-only</key>\s*<true/>',
          ).hasMatch(entitlement),
          isTrue,
          reason: path,
        );
      }
    });

    test('pickers expose only truthful local formats', () {
      expect(LocalContentService.imageExtensions, <String>['png']);
      expect(
        LocalContentService.bookExtensions,
        containsAll(<String>['cbr', 'cb7']),
      );
    });

    test('imports and renders DRM-free Kindle metadata and sections', () async {
      final service = _service();

      final book = await service.importBook(
        LocalPickedFile.fromBytes('fixture.azw3', kindleFixture()),
      );

      expect(book.kind, LocalContentKind.text);
      expect(book.title, 'Fixture Book');
      expect(book.format, 'AZW3');
      expect(book.sections, <String>['Chapter 1', 'Hello reader.']);
    });

    test(
      'rejects HUFF/CDIC Kindle compression with the narrowed contract',
      () async {
        final service = _service();

        await expectLater(
          service.importBook(
            LocalPickedFile.fromBytes(
              'huff.mobi',
              kindleFixture(compression: 17480),
            ),
          ),
          throwsA(
            isA<LocalContentException>().having(
              (error) => error.message,
              'message',
              allOf(contains('HUFF/CDIC'), contains('PalmDOC')),
            ),
          ),
        );
      },
    );

    test(
      'rejects DRM, malformed input and oversized streams clearly',
      () async {
        final service = _service();

        await expectLater(
          service.importBook(
            LocalPickedFile.fromBytes(
              'locked.azw',
              kindleFixture(encryptionType: 1),
            ),
          ),
          throwsA(
            isA<LocalContentException>().having(
              (error) => error.message,
              'message',
              contains('DRM'),
            ),
          ),
        );
        await expectLater(
          service.importBook(
            LocalPickedFile.fromBytes('broken.mobi', const <int>[1, 2, 3]),
          ),
          throwsA(
            isA<LocalContentException>().having(
              (error) => error.message,
              'message',
              contains('损坏'),
            ),
          ),
        );
        await expectLater(
          service.importBook(
            LocalPickedFile(
              name: 'huge.mobi',
              size: 64 * 1024 * 1024 + 1,
              stream: const Stream<List<int>>.empty(),
            ),
          ),
          throwsA(isA<LocalContentException>()),
        );
      },
    );

    test(
      'extracts naturally ordered CBT, stored CBR and stored CB7 pages',
      () async {
        final service = _service();
        final fixtures = <String, Uint8List>{
          'book.cbt': tarFixture(<String, int>{
            '10.jpg': 2,
            '2.jpg': 3,
            '001.png': 1,
          }),
          'book.cbr': rar4Fixture(<String, int>{
            '10.jpg': 2,
            '2.jpg': 3,
            '001.png': 1,
          }),
          'book.cb7': _storedSevenZip(<String, List<int>>{
            '10.jpg': <int>[10],
            '2.jpg': <int>[2, 2],
            '001.png': <int>[1, 1, 1],
          }),
        };

        for (final fixture in fixtures.entries) {
          final book = await service.importBook(
            LocalPickedFile.fromBytes(fixture.key, fixture.value),
          );
          expect(book.kind, LocalContentKind.comic, reason: fixture.key);
          expect(book.pages.map((page) => page.name), <String>[
            '001.png',
            '2.jpg',
            '10.jpg',
          ], reason: fixture.key);
          expect(
            book.pages.map((page) => page.bytes.length),
            everyElement(greaterThan(0)),
          );
          if (fixture.key.endsWith('.cb7')) {
            expect(book.pages.map((page) => page.bytes.first), <int>[1, 2, 10]);
          }
        }
      },
    );

    test('renders a default 7-Zip solid LZMA2 comic fixture', () async {
      final service = _service();
      final bytes = await File(
        'test/fixtures/formats/cb7-default-header.cb7',
      ).readAsBytes();

      final book = await service.importBook(
        LocalPickedFile.fromBytes('default.cb7', bytes),
      );

      expect(book.pages.map((page) => page.name), <String>['2.jpg', '10.jpg']);
      expect(book.pages.map((page) => page.bytes.length), <int>[100, 200]);
    });

    test('accepts real stored RAR5 and rejects real compressed RAR5', () async {
      final service = _service();

      final stored = await service.importBook(
        LocalPickedFile.fromBytes('stored.cbr', realRar5StoredFixture()),
      );
      expect(stored.pages.single.name, 'small.jpg');
      expect(stored.pages.single.bytes, isNotEmpty);

      await expectLater(
        service.importBook(
          LocalPickedFile.fromBytes(
            'compressed.cbr',
            realRar5CompressedFixture(),
          ),
        ),
        throwsA(
          isA<LocalContentException>().having(
            (error) => error.message,
            'message',
            allOf(contains('压缩 RAR'), contains('存储')),
          ),
        ),
      );
    });

    test('rejects JPEG before OCR and keeps the picker PNG-only', () async {
      final service = _service();

      expect(LocalContentService.imageExtensions, <String>['png']);
      await expectLater(
        service.readOcrImage(
          LocalPickedFile.fromBytes('scan.jpg', const <int>[0xff, 0xd8, 0xff]),
        ),
        throwsA(
          isA<LocalContentException>().having(
            (error) => error.message,
            'message',
            allOf(contains('PNG'), contains('JPEG 暂不支持')),
          ),
        ),
      );
    });

    test(
      'attaches local MDX and resolves selected words without network',
      () async {
        final service = _service();
        final dictionary = await service.attachDictionary(
          LocalPickedFile.fromBytes('fixture.mdx', buildMdxFixture()),
        );

        final entries = await service.lookup(dictionary, ' APPLE ');

        expect(dictionary.entryCount, 3);
        expect(entries.map((entry) => entry.definition), <String>[
          '<b>first</b>',
          '<b>second</b>',
        ]);
      },
    );

    test(
      'runs a real local OCR golden and exposes no remote fallback',
      () async {
        final service = LocalContentService(
          ocrAdapter: LocalOcrAdapter.portable(platform: 'linux'),
        );

        final job = service.startOcr(base64Decode(_helloPngGolden));
        final result = await job.result;

        expect(result.text, 'HELLO');
        expect(result.confidence, greaterThan(.99));
        expect(service.ocrAdapter.providerId, 'local-ocr-linux');
      },
    );

    test(
      'cancellation settles without waiting for a blocked OCR engine',
      () async {
        final engine = _BlockingEngine();
        final service = LocalContentService(
          ocrAdapter: LocalOcrAdapter.available(
            engine: engine,
            platform: 'android',
          ),
        );
        final job = service.startOcr(_pngHeader(width: 2, height: 2));
        await engine.started.future;

        job.cancel();

        await expectLater(
          job.result.timeout(const Duration(seconds: 1)),
          throwsA(anything),
        );
        expect(job.isCancelled, isTrue);
        engine.finish.complete();
      },
    );

    test('unsupported platforms report local OCR unavailable', () {
      final service = _service();

      expect(service.isOcrAvailable, isFalse);
      expect(
        () => service.startOcr(_pngHeader(width: 1, height: 1)),
        throwsA(
          isA<LocalContentException>().having(
            (error) => error.message,
            'message',
            contains('不支持本地 OCR'),
          ),
        ),
      );
    });
  });
}

LocalContentService _service() => LocalContentService(
  ocrAdapter: LocalOcrAdapter.unavailable(platform: 'web'),
);

class _BlockingEngine implements LocalOcrEngine {
  final started = Completer<void>();
  final finish = Completer<void>();

  @override
  Set<String> get supportedLanguages => const <String>{'en'};

  @override
  Future<OcrResult> recognize(
    Uint8List ephemeralImageBytes, {
    required OcrCancellationToken cancellation,
    String? locale,
  }) async {
    started.complete();
    await finish.future;
    return const OcrResult(text: 'late', confidence: 1);
  }
}

Uint8List _pngHeader({required int width, required int height}) {
  final bytes = Uint8List(24);
  bytes.setAll(0, const <int>[137, 80, 78, 71, 13, 10, 26, 10]);
  bytes.setAll(12, const <int>[73, 72, 68, 82]);
  _writeUint32(bytes, 16, width);
  _writeUint32(bytes, 20, height);
  return bytes;
}

void _writeUint32(Uint8List bytes, int offset, int value) {
  bytes[offset] = (value >> 24) & 0xff;
  bytes[offset + 1] = (value >> 16) & 0xff;
  bytes[offset + 2] = (value >> 8) & 0xff;
  bytes[offset + 3] = value & 0xff;
}

const _helloPngGolden =
    'iVBORw0KGgoAAAANSUhEUgAAAFkAAAAbCAAAAAAy8HwUAAAAUklEQVR4nGP4TyvA'
    'MERNZmBAmA9mM6ACVBUIZehGIdQPN5MJGIRXAMIeNXmYmIyRyFCT0AgzGVV0aMTg'
    'qMkDZDLuZIhd4D/R5fNgN5k2YCiaDABHfcuJcx5zoQAAAABJRU5ErkJggg==';

Uint8List _storedSevenZip(Map<String, List<int>> entries) {
  final packed = <int>[for (final data in entries.values) ...data];
  final names = <int>[0];
  for (final name in entries.keys) {
    for (final unit in name.codeUnits) {
      names
        ..add(unit & 0xff)
        ..add(unit >> 8);
    }
    names.addAll(const <int>[0, 0]);
  }
  final header = <int>[
    0x01,
    0x04,
    0x06,
    0,
    ..._sevenZipInt(entries.length),
    0x09,
    for (final data in entries.values) ..._sevenZipInt(data.length),
    0x00,
    0x07,
    0x0b,
    ..._sevenZipInt(entries.length),
    0,
    for (var index = 0; index < entries.length; index++) ...const <int>[
      1,
      1,
      0,
    ],
    0x0c,
    for (final data in entries.values) ..._sevenZipInt(data.length),
    0x00,
    0x08,
    0x0a,
    1,
    for (final data in entries.values) ..._little32(_crc32(data)),
    0x00,
    0x00,
    0x05,
    ..._sevenZipInt(entries.length),
    0x11,
    ..._sevenZipInt(names.length),
    ...names,
    0x00,
    0x00,
  ];
  final bytes = Uint8List(32 + packed.length + header.length);
  bytes.setRange(0, 6, const <int>[0x37, 0x7a, 0xbc, 0xaf, 0x27, 0x1c]);
  bytes[7] = 4;
  _write64Le(bytes, 12, packed.length);
  _write64Le(bytes, 20, header.length);
  _write32Le(bytes, 28, _crc32(header));
  _write32Le(bytes, 8, _crc32(bytes.sublist(12, 32)));
  bytes.setRange(32, 32 + packed.length, packed);
  bytes.setRange(32 + packed.length, bytes.length, header);
  return bytes;
}

List<int> _sevenZipInt(int value) {
  if (value < 0x80) return <int>[value];
  throw ArgumentError.value(value);
}

List<int> _little32(int value) => <int>[
  value & 0xff,
  (value >> 8) & 0xff,
  (value >> 16) & 0xff,
  (value >> 24) & 0xff,
];

void _write32Le(Uint8List bytes, int offset, int value) {
  for (var index = 0; index < 4; index++) {
    bytes[offset + index] = value >> (index * 8);
  }
}

void _write64Le(Uint8List bytes, int offset, int value) {
  for (var index = 0; index < 8; index++) {
    bytes[offset + index] = value >> (index * 8);
  }
}

int _crc32(List<int> bytes) {
  var crc = 0xffffffff;
  for (final byte in bytes) {
    crc ^= byte;
    for (var bit = 0; bit < 8; bit++) {
      crc = (crc & 1) == 0 ? crc >> 1 : (crc >> 1) ^ 0xedb88320;
    }
  }
  return (crc ^ 0xffffffff) & 0xffffffff;
}
