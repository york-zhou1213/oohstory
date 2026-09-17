import 'capabilities.dart';

/// Product-visible feature switches.
///
/// Adapter support and product availability are deliberately separate. A
/// parser or transport may be present for contract testing while its product
/// entry remains disabled until release evidence exists for that platform.
class ProductCapabilityProfile {
  const ProductCapabilityProfile({
    this.localFormatsEnabled = true,
    this.localDictionaryEnabled = true,
    this.localOcrEnabled = false,
    this.accountProgressSyncEnabled = false,
    this.webDavEnabled = false,
    this.s3Enabled = false,
    this.dropboxEnabled = false,
    this.googleDriveEnabled = false,
    this.obsidianExportEnabled = false,
    this.notionExportEnabled = false,
    this.joplinExportEnabled = false,
    this.readwiseExportEnabled = false,
  });

  static const production = ProductCapabilityProfile(
    localFormatsEnabled: bool.fromEnvironment(
      'OOHSTORY_LOCAL_FORMATS_ENABLED',
      defaultValue: true,
    ),
    localDictionaryEnabled: bool.fromEnvironment(
      'OOHSTORY_LOCAL_DICTIONARY_ENABLED',
      defaultValue: true,
    ),
    localOcrEnabled: bool.fromEnvironment('OOHSTORY_LOCAL_OCR_ENABLED'),
    accountProgressSyncEnabled: bool.fromEnvironment(
      'OOHSTORY_PROGRESS_SYNC_ENABLED',
    ),
    webDavEnabled: bool.fromEnvironment('OOHSTORY_WEBDAV_ENABLED'),
    s3Enabled: bool.fromEnvironment('OOHSTORY_S3_ENABLED'),
    dropboxEnabled: bool.fromEnvironment('OOHSTORY_DROPBOX_ENABLED'),
    googleDriveEnabled: bool.fromEnvironment('OOHSTORY_GOOGLE_DRIVE_ENABLED'),
    obsidianExportEnabled: bool.fromEnvironment(
      'OOHSTORY_OBSIDIAN_EXPORT_ENABLED',
    ),
    notionExportEnabled: bool.fromEnvironment('OOHSTORY_NOTION_EXPORT_ENABLED'),
    joplinExportEnabled: bool.fromEnvironment('OOHSTORY_JOPLIN_EXPORT_ENABLED'),
    readwiseExportEnabled: bool.fromEnvironment(
      'OOHSTORY_READWISE_EXPORT_ENABLED',
    ),
  );

  final bool localFormatsEnabled;
  final bool localDictionaryEnabled;
  final bool localOcrEnabled;
  final bool accountProgressSyncEnabled;
  final bool webDavEnabled;
  final bool s3Enabled;
  final bool dropboxEnabled;
  final bool googleDriveEnabled;
  final bool obsidianExportEnabled;
  final bool notionExportEnabled;
  final bool joplinExportEnabled;
  final bool readwiseExportEnabled;

  bool get localContentEnabled =>
      localFormatsEnabled || localDictionaryEnabled || localOcrEnabled;

  bool get cloudLibraryEnabled => webDavEnabled || s3Enabled;

  CapabilityRegistry buildRegistry({required String platform}) {
    final registry = CapabilityRegistry();
    registry.register(
      ProviderCapabilities(
        providerId: 'readwise',
        supported: const <AdapterCapability>[
          AdapterCapability.annotationExport,
        ],
      ),
      enabled: readwiseExportEnabled,
    );
    registry.register(
      ProviderCapabilities(
        providerId: 'kindle-drm-free',
        supported: const <AdapterCapability>[AdapterCapability.textDecoding],
      ),
      enabled: localFormatsEnabled,
    );
    registry.register(
      ProviderCapabilities(
        providerId: 'comic-archive-safe',
        supported: const <AdapterCapability>[AdapterCapability.comicDecoding],
      ),
      enabled: localFormatsEnabled,
    );
    registry.register(
      ProviderCapabilities(
        providerId: 'local-mdx',
        supported: const <AdapterCapability>[AdapterCapability.dictionary],
      ),
      enabled: localDictionaryEnabled,
    );
    registry.register(
      ProviderCapabilities(
        providerId: 'local-ocr-$platform',
        supported: const <AdapterCapability>[AdapterCapability.localOcr],
      ),
      enabled: localOcrEnabled,
    );
    registry.register(
      ProviderCapabilities(
        providerId: 'oohstory-progress-v1',
        supported: const <AdapterCapability>[AdapterCapability.progressSync],
      ),
      enabled: accountProgressSyncEnabled,
    );
    for (final provider in <(String, bool)>[
      ('webdav', webDavEnabled),
      ('s3', s3Enabled),
      ('dropbox', dropboxEnabled),
      ('google-drive', googleDriveEnabled),
    ]) {
      registry.register(
        ProviderCapabilities(
          providerId: provider.$1,
          supported: const <AdapterCapability>[AdapterCapability.cloudLibrary],
        ),
        enabled: provider.$2,
      );
    }
    registry.register(
      ProviderCapabilities(
        providerId: 'obsidian',
        supported: const <AdapterCapability>[
          AdapterCapability.annotationExport,
        ],
      ),
      enabled: obsidianExportEnabled,
    );
    registry.register(
      ProviderCapabilities(
        providerId: 'notion',
        supported: const <AdapterCapability>[
          AdapterCapability.annotationExport,
        ],
      ),
      enabled: notionExportEnabled,
    );
    registry.register(
      ProviderCapabilities(
        providerId: 'joplin',
        supported: const <AdapterCapability>[
          AdapterCapability.annotationExport,
        ],
      ),
      enabled:
          joplinExportEnabled &&
          const <String>{
            'linux',
            'windows',
            'macos',
          }.contains(platform.toLowerCase()),
    );
    return registry;
  }
}
