import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  group('Apple entitlement contracts', () {
    test('macOS deployment target satisfies current native plugins', () {
      final project = File(
        'macos/Runner.xcodeproj/project.pbxproj',
      ).readAsStringSync();
      final podfile = File('macos/Podfile').readAsStringSync();

      expect(project, isNot(contains('MACOSX_DEPLOYMENT_TARGET = 10.14;')));
      expect(
        'MACOSX_DEPLOYMENT_TARGET = 10.15;'
            .allMatches(project)
            .length,
        greaterThanOrEqualTo(3),
      );
      expect(podfile, contains("platform :osx, '10.15'"));
      expect(podfile, isNot(contains("platform :osx, '10.14'")));
    });

    for (final path in <String>[
      'macos/Runner/DebugProfile.entitlements',
      'macos/Runner/Release.entitlements',
    ]) {
      test('$path supports secure storage and user-selected exports', () {
        final source = File(path).readAsStringSync();

        expect(source, contains('<key>keychain-access-groups</key>'));
        expect(
          source,
          contains(
            '<string>\$(AppIdentifierPrefix)\$(PRODUCT_BUNDLE_IDENTIFIER)</string>',
          ),
        );
        expect(
          source,
          contains(
            '<key>com.apple.security.files.user-selected.read-write</key>',
          ),
        );
        expect(
          source,
          isNot(contains('com.apple.security.files.user-selected.read-only')),
        );
      });
    }
  });
}
