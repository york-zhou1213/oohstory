import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  group('Apple entitlement contracts', () {
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
