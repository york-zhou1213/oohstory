import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/widgets/account_success_toast.dart';
import 'package:oohstory/widgets/reading_identity.dart';
import 'package:oohstory/widgets/recommendation_donation_dialog.dart';

void main() {
  testWidgets('reading identity stays within a 390px phone viewport', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: ReadingIdentityCard(
            reading: {
              'level': 18,
              'roman': 'ⅩⅧ',
              'name': '水月镜花',
              'active_seconds': 360000000,
              'seconds_to_next': 0,
              'progress': 1.0,
              'is_max': true,
            },
          ),
        ),
      ),
    );

    expect(find.text('ⅩⅧ · 水月镜花'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  test('reading duration uses hours and minutes instead of decimal hours', () {
    expect(formatReadingDuration(2160), '36 分钟');
    expect(formatReadingDuration(3600), '1 小时');
    expect(formatReadingDuration(3660), '1 小时 1 分钟');
    expect(formatReadingDuration(61, remaining: true), '2 分钟');
  });

  testWidgets('recommendation gift dialog fits a 390px phone viewport', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: Center(
              child: FilledButton(
                onPressed: () => showRecommendationDonationDialog(context),
                child: const Text('推荐'),
              ),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('推荐'));
    await tester.pumpAndSettle();

    expect(find.text('为这本好书助力？'), findsOneWidget);
    expect(find.text('捐赠 1 小时阅读经验时长，将好书推荐给更多人。'), findsOneWidget);
    expect(find.text('助力推荐'), findsOneWidget);
    expect(find.text('再想想'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('account success toast appears and gradually fades away', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: Center(
              child: FilledButton(
                onPressed: () =>
                    showAccountSuccessToast(context, message: '个人资料保存成功'),
                child: const Text('保存'),
              ),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('保存'));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('个人资料保存成功'), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.pump(const Duration(seconds: 3));
    expect(find.text('个人资料保存成功'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  test('account UI exposes the same authenticated feature routes as Web', () {
    final root = Directory.current.path;
    final profile = File(
      '$root/lib/screens/profile_screen.dart',
    ).readAsStringSync();
    final service = File(
      '$root/lib/services/account_service.dart',
    ).readAsStringSync();
    final submissions = File(
      '$root/lib/screens/submission_center_screen.dart',
    ).readAsStringSync();
    final notifications = File(
      '$root/lib/screens/notifications_screen.dart',
    ).readAsStringSync();
    final records = File(
      '$root/lib/screens/account_records_screen.dart',
    ).readAsStringSync();
    final detail = File(
      '$root/lib/screens/book_detail_screen.dart',
    ).readAsStringSync();
    final settings = File(
      '$root/lib/screens/account_settings_screen.dart',
    ).readAsStringSync();
    final successToast = File(
      '$root/lib/widgets/account_success_toast.dart',
    ).readAsStringSync();
    final api = File('$root/lib/services/api_service.dart').readAsStringSync();
    final auth = File('$root/lib/screens/auth_screen.dart').readAsStringSync();

    for (final value in [
      'AccountRecordsScreen',
      'AccountSettingsScreen',
      'SubmissionCenterScreen',
      'NotificationsScreen',
      'ReadingIdentityCard',
    ]) {
      expect(profile, contains(value));
    }
    for (final path in [
      '/api/v1/me/profile',
      '/api/v1/me/password',
      '/api/v1/me/avatar',
      '/api/v1/me/reading-heartbeat',
      '/api/v1/me/novel-submissions',
      '/api/v1/me/notifications',
    ]) {
      expect(service, contains(path));
    }
    expect(submissions, isNot(contains('AI 审核')));
    expect(submissions, contains('请上传 ZIP。我们会长/短篇结构审核与内容复核完后，并通过消息中心告知您上传结果。'));
    expect(notifications, isNot(contains('AI 审核')));
    expect(submissions, contains('覆盖 TXT 全文、EPUB 内部章节'));
    expect(submissions, contains('伪装成正常书籍'));
    expect(submissions, contains('禁止涉黄、涉毒、涉赌'));
    expect(records, contains('static const _pageSize = 10'));
    expect(records, contains('BoxFit.cover'));
    for (final label in ['首页', '自定义页数 · 跳转', '尾页']) {
      expect(records, contains(label));
    }
    expect(detail, contains("'推荐 · \$_recommendCount'"));
    expect(detail, contains('· \$_favoriteCount'));
    expect(service, contains('/api/v1/books/\$bookId/recommend'));
    expect(service, contains("body: {'event_id': eventId ?? _uuidV4()}"));
    expect(detail, contains('showRecommendationDonationDialog'));
    expect(detail, contains('已捐赠 1 小时阅读经验时长'));
    expect(detail, isNot(contains('你已经推荐过这本书')));
    expect(detail, isNot(contains('不会重复扣除你的阅读时长')));
    expect(
      settings,
      contains("showAccountSuccessToast(context, message: '个人资料保存成功')"),
    );
    expect(
      settings,
      contains("showAccountSuccessToast(context, message: '密码修改成功')"),
    );
    expect(successToast, contains('FadeTransition'));
    expect(successToast, contains('reverseDuration'));
    expect(api, contains('/metrics/\$event'));
    expect(auth, contains('现在开放注册，邀请码为选填项'));
    expect(auth, contains('邀请码（选填）'));
    expect(auth, contains('value.trim().isNotEmpty'));
    expect(auth, isNot(contains('当前仅限受邀读者注册')));
    expect(auth, isNot(contains('凭邀请码创建账户')));
  });
}
