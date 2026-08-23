import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/ocr/ocr.dart';
import 'package:oohstory/core/capabilities.dart';
import 'package:oohstory/core/errors.dart';
import 'package:oohstory/core/models.dart';

void main() {
  group('LocalOcrAdapter', () {
    test('runs a deterministic local golden and reports languages', () async {
      final engine = _GoldenEngine();
      final adapter = LocalOcrAdapter.available(
        engine: engine,
        platform: 'Linux',
      );
      final image = _png(width: 32, height: 16);

      final result = await adapter.recognize(image, locale: 'zh-hans');

      expect(result.text, '本地识别 OK');
      expect(result.confidence, 0.98);
      expect(adapter.providerId, 'local-ocr-linux');
      expect(adapter.supportedLanguages, <String>['en', 'zh-Hans']);
      expect(adapter.capabilities.supports(AdapterCapability.localOcr), isTrue);
      expect(
        adapter.capabilities.supports(AdapterCapability.remoteOcr),
        isFalse,
      );
      expect(engine.lastLocale, 'zh-Hans');
    });

    test(
      'clears the ephemeral image copy and does not mutate caller bytes',
      () async {
        final engine = _GoldenEngine();
        final adapter = LocalOcrAdapter.available(
          engine: engine,
          platform: 'android',
        );
        final image = _png(width: 8, height: 8);
        final original = Uint8List.fromList(image);

        await adapter.recognize(image, locale: 'en');

        expect(image, original);
        expect(engine.retainedBytes, isNotNull);
        expect(engine.retainedBytes, everyElement(0));
      },
    );

    test('cancels before local engine execution', () async {
      final engine = _GoldenEngine();
      final adapter = LocalOcrAdapter.available(
        engine: engine,
        platform: 'ios',
      );

      final job = adapter.start(_png(width: 2, height: 2), locale: 'en');
      job.cancel();

      await expectLater(
        job.result,
        throwsA(_coreError(CoreErrorCode.validationError)),
      );
      expect(job.isCancelled, isTrue);
      expect(engine.calls, 0);
    });

    test('cancels an in-flight local engine operation', () async {
      final engine = _BlockingEngine();
      final adapter = LocalOcrAdapter.available(
        engine: engine,
        platform: 'macos',
      );
      final job = adapter.start(_png(width: 2, height: 2), locale: 'en');
      await engine.started.future;

      job.cancel();
      engine.finish.complete();

      await expectLater(
        job.result,
        throwsA(_coreError(CoreErrorCode.validationError)),
      );
      expect(engine.observedToken?.isCancelled, isTrue);
      expect(engine.retainedBytes, everyElement(0));
    });

    test('reports unsupported platforms without a fallback', () async {
      final adapter = LocalOcrAdapter.unavailable(platform: 'web');

      expect(adapter.isAvailable, isFalse);
      expect(adapter.supportedLanguages, isEmpty);
      expect(adapter.capabilities.supported, isEmpty);
      expect(
        () => adapter.start(_png(width: 1, height: 1)),
        throwsA(_coreError(CoreErrorCode.unsupported)),
      );
      await expectLater(
        adapter.recognize(_png(width: 1, height: 1)),
        throwsA(_coreError(CoreErrorCode.unsupported)),
      );
    });

    test('enforces encoded-size, dimensions, pixels and image type', () {
      final adapter = LocalOcrAdapter.available(
        engine: _GoldenEngine(),
        platform: 'windows',
        limits: const OcrImageLimits(
          maxEncodedBytes: 32,
          maxWidth: 100,
          maxHeight: 100,
          maxPixels: 5000,
        ),
      );

      expect(
        () => adapter.start(Uint8List(33)),
        throwsA(_coreError(CoreErrorCode.payloadTooLarge)),
      );
      expect(
        () => adapter.start(_png(width: 101, height: 1)),
        throwsA(_coreError(CoreErrorCode.payloadTooLarge)),
      );
      expect(
        () => adapter.start(_png(width: 80, height: 80)),
        throwsA(_coreError(CoreErrorCode.payloadTooLarge)),
      );
      expect(
        () => adapter.start(Uint8List.fromList(<int>[1, 2, 3, 4])),
        throwsA(_coreError(CoreErrorCode.unsupported)),
      );
    });

    test(
      'rejects unavailable languages and invalid engine confidence',
      () async {
        final adapter = LocalOcrAdapter.available(
          engine: _InvalidConfidenceEngine(),
          platform: 'android',
        );

        expect(
          () => adapter.start(_png(width: 1, height: 1), locale: 'fr'),
          throwsA(_coreError(CoreErrorCode.unsupported)),
        );
        await expectLater(
          adapter.recognize(_png(width: 1, height: 1), locale: 'en'),
          throwsA(_coreError(CoreErrorCode.validationError)),
        );
      },
    );

    test('production OCR adapter has no network or persistence surface', () {
      final files = Directory(
        'lib/adapters/ocr',
      ).listSync(recursive: true).whereType<File>();
      for (final file in files) {
        final source = file.readAsStringSync();
        expect(source, isNot(contains("dart:io")), reason: file.path);
        expect(source, isNot(contains("package:http")), reason: file.path);
        expect(source, isNot(contains('Socket')), reason: file.path);
        expect(source, isNot(contains('HttpClient')), reason: file.path);
        expect(source, isNot(contains('writeAsBytes')), reason: file.path);
        expect(source, isNot(contains('remoteOcr')), reason: file.path);
      }
    });
  });
}

class _GoldenEngine implements LocalOcrEngine {
  Uint8List? retainedBytes;
  String? lastLocale;
  int calls = 0;

  @override
  Set<String> get supportedLanguages => <String>{'zh-Hans', 'en'};

  @override
  Future<OcrResult> recognize(
    Uint8List ephemeralImageBytes, {
    required OcrCancellationToken cancellation,
    String? locale,
  }) async {
    calls++;
    cancellation.throwIfCancelled();
    retainedBytes = ephemeralImageBytes;
    lastLocale = locale;
    expect(ephemeralImageBytes.sublist(0, 8), <int>[
      137,
      80,
      78,
      71,
      13,
      10,
      26,
      10,
    ]);
    return const OcrResult(text: '本地识别 OK', confidence: 0.98);
  }
}

class _BlockingEngine implements LocalOcrEngine {
  final Completer<void> started = Completer<void>();
  final Completer<void> finish = Completer<void>();
  OcrCancellationToken? observedToken;
  Uint8List? retainedBytes;

  @override
  Set<String> get supportedLanguages => <String>{'en'};

  @override
  Future<OcrResult> recognize(
    Uint8List ephemeralImageBytes, {
    required OcrCancellationToken cancellation,
    String? locale,
  }) async {
    retainedBytes = ephemeralImageBytes;
    observedToken = cancellation;
    started.complete();
    await finish.future;
    return const OcrResult(text: 'late result', confidence: 1);
  }
}

class _InvalidConfidenceEngine implements LocalOcrEngine {
  @override
  Set<String> get supportedLanguages => <String>{'en'};

  @override
  Future<OcrResult> recognize(
    Uint8List ephemeralImageBytes, {
    required OcrCancellationToken cancellation,
    String? locale,
  }) async => const OcrResult(text: 'invalid', confidence: 2);
}

Uint8List _png({required int width, required int height}) {
  final bytes = Uint8List(24);
  bytes.setAll(0, const <int>[137, 80, 78, 71, 13, 10, 26, 10]);
  bytes.setAll(12, const <int>[73, 72, 68, 82]);
  _writeUint32Be(bytes, 16, width);
  _writeUint32Be(bytes, 20, height);
  return bytes;
}

void _writeUint32Be(Uint8List bytes, int offset, int value) {
  bytes[offset] = (value >> 24) & 0xff;
  bytes[offset + 1] = (value >> 16) & 0xff;
  bytes[offset + 2] = (value >> 8) & 0xff;
  bytes[offset + 3] = value & 0xff;
}

Matcher _coreError(CoreErrorCode code) =>
    isA<CoreException>().having((error) => error.code, 'code', code);
