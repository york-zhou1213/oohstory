import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/features/local_content/local_content.dart';

void main() {
  testWidgets('Web hides OCR instead of offering a remote fallback', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: LocalContentHubScreen(
          service: LocalContentService.forCurrentPlatform(),
        ),
      ),
    );
    await tester.pump();

    expect(find.text('可取消本地 OCR'), findsNothing);
    expect(find.byTooltip('本地 OCR'), findsNothing);
    expect(find.byTooltip('本地 OCR 不可用'), findsNothing);
  }, skip: !kIsWeb);
}
