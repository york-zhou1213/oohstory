import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/adapters/ocr/local_ocr_adapter.dart';
import 'package:oohstory/core/models.dart';
import 'package:oohstory/features/local_content/local_content.dart';
import 'package:oohstory/main.dart';
import 'package:oohstory/theme/app_theme.dart';

import '../../dictionary/mdx_fixture.dart';
import '../../fixtures/formats/fixture_factory.dart';

void main() {
  testWidgets('app shell exposes local reading without adding a fifth tab', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(const OohStoryApp(checkForUpdates: false));
    await tester.pump(const Duration(seconds: 1));
    await tester.pump();

    expect(find.byTooltip('打开本地阅读'), findsOneWidget);
    for (final label in const ['发现', '书库', '书架', '我的']) {
      expect(find.text(label), findsWidgets);
    }
    expect(find.text('本地阅读'), findsNothing);

    await tester.tap(find.byTooltip('打开本地阅读'));
    await tester.pumpAndSettle();

    expect(find.text('本地阅读'), findsOneWidget);
    expect(find.text('导入本地书'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('landing remains usable at phone, tablet and desktop widths', (
    tester,
  ) async {
    for (final size in const <Size>[
      Size(375, 812),
      Size(768, 1024),
      Size(1280, 800),
    ]) {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      await tester.pumpWidget(_app(service: _unavailableService()));
      await tester.pump();

      expect(find.text('导入本地书'), findsOneWidget);
      expect(find.text('本地 MDX 查词'), findsOneWidget);
      expect(find.text('可取消本地 OCR'), findsOneWidget);
      expect(find.text('当前不可用'), findsOneWidget);
      expect(tester.takeException(), isNull, reason: '$size');
    }
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
  });

  testWidgets(
    'imports Kindle, changes page by keyboard and looks up selection',
    (tester) async {
      final picker = _FixturePicker(<String, LocalPickedFile>{
        'mobi': LocalPickedFile.fromBytes('fixture.azw3', kindleFixture()),
        'mdx': LocalPickedFile.fromBytes('fixture.mdx', buildMdxFixture()),
      });
      await tester.pumpWidget(
        _app(service: _unavailableService(), picker: picker.call),
      );

      await tester.tap(find.text('导入本地书'));
      await tester.pumpAndSettle();

      expect(find.text('Fixture Book'), findsOneWidget);
      expect(find.text('Chapter 1'), findsOneWidget);
      expect(find.text('1 / 2'), findsOneWidget);

      await tester.sendKeyEvent(LogicalKeyboardKey.arrowRight);
      await tester.pump();
      expect(find.text('Hello reader.'), findsOneWidget);
      expect(find.text('2 / 2'), findsOneWidget);

      final selectable = tester.widget<SelectableText>(
        find.byKey(const Key('local-reader-text')),
      );
      selectable.onSelectionChanged!(
        const TextSelection(baseOffset: 0, extentOffset: 5),
        SelectionChangedCause.longPress,
      );
      await tester.pump();
      await tester.tap(find.text('查词'));
      await tester.pumpAndSettle();

      expect(find.text('“Hello”的本地释义'), findsOneWidget);
      expect(find.text('词典中没有找到该词'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('DRM failure is specific, inline and recoverable', (
    tester,
  ) async {
    final picker = _FixturePicker(<String, LocalPickedFile>{
      'mobi': LocalPickedFile.fromBytes(
        'locked.azw',
        kindleFixture(encryptionType: 1),
      ),
    });
    await tester.pumpWidget(
      _app(service: _unavailableService(), picker: picker.call),
    );

    await tester.tap(find.text('导入本地书'));
    await tester.pumpAndSettle();

    expect(find.textContaining('DRM 或加密保护'), findsOneWidget);
    expect(find.text('导入本地书'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('OCR shows immediate progress and cancels a blocked engine', (
    tester,
  ) async {
    final engine = _BlockingEngine();
    final service = LocalContentService(
      ocrAdapter: LocalOcrAdapter.available(
        engine: engine,
        platform: 'android',
      ),
    );
    final picker = _FixturePicker(<String, LocalPickedFile>{
      'png': LocalPickedFile.fromBytes(
        'scan.png',
        _pngHeader(width: 2, height: 2),
      ),
    });
    await tester.pumpWidget(_app(service: service, picker: picker.call));

    await tester.ensureVisible(find.text('识别图片'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('识别图片'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 1));
    expect(engine.started.isCompleted, isTrue);

    expect(find.text('正在本机识别；可随时取消'), findsOneWidget);
    expect(find.text('取消识别'), findsOneWidget);
    await tester.tap(find.text('取消识别'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 1));

    expect(find.text('已取消本地 OCR'), findsOneWidget);
    expect(tester.takeException(), isNull);
    engine.finish.complete();
    await tester.pump();
  });

  testWidgets('reader controls expose semantic tap actions and 48px targets', (
    tester,
  ) async {
    final handle = tester.ensureSemantics();
    final picker = _FixturePicker(<String, LocalPickedFile>{
      'mobi': LocalPickedFile.fromBytes('fixture.mobi', kindleFixture()),
    });
    await tester.pumpWidget(
      _app(service: _unavailableService(), picker: picker.call),
    );
    await tester.tap(find.text('导入本地书'));
    await tester.pumpAndSettle();

    final next = find.byTooltip('下一页');
    expect(next, findsOneWidget);
    expect(tester.getSize(next).width, greaterThanOrEqualTo(48));
    expect(tester.getSize(next).height, greaterThanOrEqualTo(48));
    final semantics = tester.getSemantics(next);
    expect(semantics.getSemanticsData().hasAction(SemanticsAction.tap), isTrue);
    handle.dispose();
  });
}

Widget _app({
  required LocalContentService service,
  LocalContentPicker picker = _cancelPicker,
}) => MaterialApp(
  debugShowCheckedModeBanner: false,
  theme: AppTheme.light(),
  home: LocalContentHubScreen(service: service, picker: picker),
);

LocalContentService _unavailableService() => LocalContentService(
  ocrAdapter: LocalOcrAdapter.unavailable(platform: 'web'),
);

Future<LocalPickedFile?> _cancelPicker(List<String> _) async => null;

class _FixturePicker {
  _FixturePicker(this.files);

  final Map<String, LocalPickedFile> files;

  Future<LocalPickedFile?> call(List<String> extensions) async {
    for (final extension in extensions) {
      final file = files[extension];
      if (file != null) return file;
    }
    return null;
  }
}

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
