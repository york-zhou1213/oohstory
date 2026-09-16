import 'dart:io';

import 'package:flutter/cupertino.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/main.dart';
import 'package:oohstory/theme/app_theme.dart';
import 'package:oohstory/widgets/ooh_ui.dart';

void main() {
  test('primary navigation promotes bookshelf and My owns archives', () {
    final main = File('lib/main.dart').readAsStringSync();
    final profile = File('lib/screens/profile_screen.dart').readAsStringSync();

    expect(main, contains("static const _labels = ['发现', '书库', '书架', '我的']"));
    expect(main, contains('ReadingBookshelfScreen('));
    expect(main, isNot(contains('DeconstructionScreen(),')));
    expect(profile, contains("appBar: AppBar(title: const Text('拆书档案'))"));
    expect(profile, contains("body: const DeconstructionScreen()"));
    expect(profile, contains('查看深度拆解、写作技法与设定资料'));
    expect(profile, contains("title: '本地离线书库'"));
  });

  test('native design metrics scale from phone to iPad', () {
    expect(OohPageMetrics.horizontalPadding(390), 16);
    expect(OohPageMetrics.horizontalPadding(834), 28);
    expect(OohPageMetrics.gridColumns(390), 3);
    expect(OohPageMetrics.gridColumns(834), 4);
    expect(AppTheme.wideNavigationBreakpoint, lessThanOrEqualTo(768));
    expect(AppTheme.readerContentMaxWidth, lessThan(834));
  });

  testWidgets('iPhone uses Cupertino navigation chrome', (tester) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.iOS;
    try {
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(const OohStoryApp(checkForUpdates: false));
      await tester.pump(const Duration(seconds: 1));
      await tester.pump();

      expect(find.byType(CupertinoNavigationBar), findsOneWidget);
      expect(find.byType(CupertinoTabBar), findsOneWidget);
      expect(find.byType(NavigationRail), findsNothing);
      for (final label in const ['发现', '书库', '书架', '我的']) {
        expect(find.text(label), findsWidgets);
      }
      expect(tester.takeException(), isNull);
    } finally {
      debugDefaultTargetPlatformOverride = null;
    }
  });

  testWidgets('iPad uses a persistent navigation rail', (tester) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.iOS;
    try {
      tester.view.physicalSize = const Size(1024, 1366);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(const OohStoryApp(checkForUpdates: false));
      await tester.pump(const Duration(seconds: 1));
      await tester.pump();

      expect(find.byType(NavigationRail), findsOneWidget);
      expect(find.byType(CupertinoTabBar), findsNothing);
      final rail = tester.widget<NavigationRail>(find.byType(NavigationRail));
      expect(
        rail.destinations
            .map((destination) => (destination.label as Text).data)
            .toList(),
        ['发现', '书库', '书架', '我的'],
      );
      expect(tester.takeException(), isNull);
    } finally {
      debugDefaultTargetPlatformOverride = null;
    }
  });

  testWidgets('desktop uses a collapsible library sidebar and workspace bar', (
    tester,
  ) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.linux;
    try {
      tester.view.physicalSize = const Size(1440, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(const OohStoryApp(checkForUpdates: false));
      await tester.pump(const Duration(seconds: 1));
      await tester.pump();

      expect(find.byType(NavigationRail), findsNothing);
      expect(find.byTooltip('收起侧栏'), findsOneWidget);
      expect(find.text('精选作品与最新上架'), findsOneWidget);
      expect(find.text('Linux'), findsOneWidget);
      for (final label in const ['发现', '书库', '书架', '我的']) {
        expect(find.text(label), findsWidgets);
      }
      expect(tester.takeException(), isNull);
    } finally {
      debugDefaultTargetPlatformOverride = null;
    }
  });

  test('all Flutter platform targets and CI contracts exist', () {
    for (final path in const [
      'android/app/build.gradle.kts',
      'ios/Runner.xcodeproj/project.pbxproj',
      'web/index.html',
      'linux/CMakeLists.txt',
      'windows/CMakeLists.txt',
      'macos/Runner.xcodeproj/project.pbxproj',
    ]) {
      expect(File(path).existsSync(), isTrue, reason: '$path must exist');
    }

    final workflow = File(
      '.github/workflows/platform-builds.yml',
    ).readAsStringSync();
    for (final command in const [
      'flutter build web --release',
      'flutter build linux --release',
      'flutter build windows --release',
      'flutter build macos --release',
      'flutter build ios --release --no-codesign',
      'flutter build apk --debug',
    ]) {
      expect(workflow, contains(command));
    }
  });

  test('iOS target declares native phone and tablet capabilities', () {
    final root = Directory.current.path;
    final info = File('$root/ios/Runner/Info.plist').readAsStringSync();
    final project = File(
      '$root/ios/Runner.xcodeproj/project.pbxproj',
    ).readAsStringSync();
    final privacy = File(
      '$root/ios/Runner/PrivacyInfo.xcprivacy',
    ).readAsStringSync();

    expect(info, contains('<string>OOHStory</string>'));
    expect(info, contains('<key>UIBackgroundModes</key>'));
    expect(info, contains('<string>audio</string>'));
    expect(info, contains('<key>UIRequiresFullScreen</key>'));
    expect(info, contains('<key>ITSAppUsesNonExemptEncryption</key>'));
    expect(project, contains('TARGETED_DEVICE_FAMILY = "1,2"'));
    expect(project, contains('IPHONEOS_DEPLOYMENT_TARGET = 13.0'));
    expect(project, contains('PrivacyInfo.xcprivacy in Resources'));
    expect(privacy, contains('NSPrivacyAccessedAPICategoryUserDefaults'));
  });
}
