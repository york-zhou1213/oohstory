import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/services/app_update_service.dart';
import 'package:oohstory/widgets/app_update_dialog.dart';

void main() {
  test('Android update checker uses the public latest endpoint', () {
    final pubspec = File('pubspec.yaml').readAsStringSync();
    final api = File('lib/services/api_service.dart').readAsStringSync();
    final service = File(
      'lib/services/app_update_service.dart',
    ).readAsStringSync();
    final main = File('lib/main.dart').readAsStringSync();
    final activity = File(
      'android/app/src/main/kotlin/com/oohstory/oohstory/MainActivity.kt',
    ).readAsStringSync();

    expect(pubspec, contains('version: 1.18.21+65'));
    expect(AppUpdateService.currentVersionName, '1.18.21');
    expect(AppUpdateService.currentVersionCode, 65);
    expect(api, contains('/api/v1/app/android/latest'));
    expect(api, contains('version_code'));
    expect(api, contains('version_name'));
    expect(service, contains('release_notes_public'));
    expect(service, contains('/downloads/android/'));
    expect(service, contains("uri.scheme == 'https'"));
    expect(main, contains('WidgetsBindingObserver'));
    expect(main, contains('AppLifecycleState.resumed'));
    expect(main, contains('showAppUpdateDialog(context, info)'));
    expect(activity, contains('MethodChannel'));
    expect(activity, contains('Intent.ACTION_VIEW'));
  });

  testWidgets('update dialog shows target version and public notes', (
    tester,
  ) async {
    const info = AppUpdateInfo(
      versionName: '1.18.22',
      versionCode: 66,
      releaseDate: '2026-08-13',
      downloadUrl: 'https://reader.example.com/downloads/android/latest.apk',
      sha256: 'abc',
      sizeBytes: 28 * 1024 * 1024,
      releaseNotes: ['修复后台播放稳定性', '新增自动检查更新'],
    );

    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: FilledButton(
              onPressed: () => showAppUpdateDialog(context, info),
              child: const Text('检查更新'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('检查更新'));
    await tester.pumpAndSettle();

    expect(find.text('发现新版本'), findsOneWidget);
    expect(find.text('当前版本 v1.18.21，可更新至 v1.18.22。'), findsOneWidget);
    expect(find.text('更新内容'), findsOneWidget);
    expect(find.text('修复后台播放稳定性'), findsOneWidget);
    expect(find.text('新增自动检查更新'), findsOneWidget);
    expect(find.text('立即更新'), findsOneWidget);
    expect(find.text('稍后'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
