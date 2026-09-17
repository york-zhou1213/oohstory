import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/core/core.dart';

void main() {
  test('export receipt has a stable JSON contract', () {
    final receipt = ExportReceipt(
      providerId: 'obsidian',
      documentId: 'book-1',
      target: '/vault/OOHStory/book.md',
      contentHash: 'abc123',
      exportedAt: DateTime.utc(2026, 9, 15),
      disposition: ExportDisposition.overwritten,
      backupTarget: '/vault/OOHStory/.oohstory-backups/book.md',
    );

    expect(ExportReceipt.fromJson(receipt.toJson()).toJson(), receipt.toJson());
  });

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

  test(
    'production profile exposes local formats but gates remote features',
    () {
      const profile = ProductCapabilityProfile();
      final registry = profile.buildRegistry(platform: 'linux');

      expect(profile.localContentEnabled, isTrue);
      expect(
        registry.supports('kindle-drm-free', AdapterCapability.textDecoding),
        isTrue,
      );
      expect(
        registry.supports(
          'comic-archive-safe',
          AdapterCapability.comicDecoding,
        ),
        isTrue,
      );
      expect(
        registry.supports('local-mdx', AdapterCapability.dictionary),
        isTrue,
      );
      expect(
        registry.supports('local-ocr-linux', AdapterCapability.localOcr),
        isFalse,
      );
      expect(
        registry.supports(
          'oohstory-progress-v1',
          AdapterCapability.progressSync,
        ),
        isFalse,
      );
      expect(
        registry.supports('dropbox', AdapterCapability.cloudLibrary),
        isFalse,
      );
      expect(
        registry.supports('obsidian', AdapterCapability.annotationExport),
        isFalse,
      );
      expect(
        registry.supports('notion', AdapterCapability.annotationExport),
        isFalse,
      );
      expect(
        registry.supports('joplin', AdapterCapability.annotationExport),
        isFalse,
      );
      expect(
        registry.supports('readwise', AdapterCapability.annotationExport),
        isFalse,
      );
    },
  );

  test('profile can independently enable only verified providers', () {
    const profile = ProductCapabilityProfile(
      localFormatsEnabled: false,
      localDictionaryEnabled: false,
      accountProgressSyncEnabled: true,
      webDavEnabled: true,
      obsidianExportEnabled: true,
      notionExportEnabled: true,
      joplinExportEnabled: true,
      readwiseExportEnabled: true,
    );
    final registry = profile.buildRegistry(platform: 'windows');

    expect(profile.localContentEnabled, isFalse);
    expect(profile.cloudLibraryEnabled, isTrue);
    expect(
      registry.supports('oohstory-progress-v1', AdapterCapability.progressSync),
      isTrue,
    );
    expect(registry.supports('webdav', AdapterCapability.cloudLibrary), isTrue);
    expect(registry.supports('s3', AdapterCapability.cloudLibrary), isFalse);
    expect(
      registry.supports('obsidian', AdapterCapability.annotationExport),
      isTrue,
    );
    expect(
      registry.supports('notion', AdapterCapability.annotationExport),
      isTrue,
    );
    expect(
      registry.supports('joplin', AdapterCapability.annotationExport),
      isTrue,
    );
    expect(
      registry.supports('readwise', AdapterCapability.annotationExport),
      isTrue,
    );
    expect(
      profile
          .buildRegistry(platform: 'android')
          .supports('joplin', AdapterCapability.annotationExport),
      isFalse,
    );
  });
}
