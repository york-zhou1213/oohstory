import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/core/core.dart';

void main() {
  test('new providers are off and unsupported capabilities stay false', () {
    final registry = CapabilityRegistry()
      ..register(
        ProviderCapabilities(
          providerId: 'local',
          supported: const <AdapterCapability>[AdapterCapability.localBooks],
        ),
      );

    expect(registry.isEnabled('local'), isFalse);
    expect(registry.supports('local', AdapterCapability.localBooks), isFalse);
    registry.setEnabled('local', true);
    expect(registry.supports('local', AdapterCapability.localBooks), isTrue);
    expect(registry.supports('local', AdapterCapability.remoteBooks), isFalse);
    expect(registry.supports('missing', AdapterCapability.localBooks), isFalse);
  });

  test('capability report is deterministic', () {
    final registry = CapabilityRegistry()
      ..register(ProviderCapabilities(providerId: 'z-provider'))
      ..register(ProviderCapabilities(providerId: 'a-provider'));
    expect(registry.report().keys, <String>['a-provider', 'z-provider']);
  });
}
