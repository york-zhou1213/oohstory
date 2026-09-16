enum AdapterCapability {
  localBooks,
  remoteBooks,
  textDecoding,
  comicDecoding,
  dictionary,
  localOcr,
  remoteOcr,
  progressStorage,
  progressSync,
  cloudLibrary,
  annotationExport,
  updateChannel,
}

class ProviderCapabilities {
  ProviderCapabilities({
    required this.providerId,
    Iterable<AdapterCapability> supported = const <AdapterCapability>[],
  }) : supported = Set<AdapterCapability>.unmodifiable(supported);

  final String providerId;
  final Set<AdapterCapability> supported;

  bool supports(AdapterCapability capability) => supported.contains(capability);
}

class CapabilityRegistry {
  final Map<String, _Registration> _providers = <String, _Registration>{};

  void register(ProviderCapabilities capabilities, {bool enabled = false}) {
    if (_providers.containsKey(capabilities.providerId)) {
      throw StateError(
        'Provider already registered: ${capabilities.providerId}',
      );
    }
    _providers[capabilities.providerId] = _Registration(capabilities, enabled);
  }

  bool isEnabled(String providerId) => _providers[providerId]?.enabled ?? false;

  bool supports(String providerId, AdapterCapability capability) {
    final provider = _providers[providerId];
    return provider != null &&
        provider.enabled &&
        provider.capabilities.supports(capability);
  }

  void setEnabled(String providerId, bool enabled) {
    final provider = _providers[providerId];
    if (provider == null) throw StateError('Unknown provider: $providerId');
    provider.enabled = enabled;
  }

  Map<String, Object?> report() => <String, Object?>{
    for (final entry
        in (_providers.entries.toList()
          ..sort((a, b) => a.key.compareTo(b.key))))
      entry.key: <String, Object?>{
        'enabled': entry.value.enabled,
        'supported':
            (entry.value.capabilities.supported
                .map((value) => value.name)
                .toList()
              ..sort()),
      },
  };
}

class _Registration {
  _Registration(this.capabilities, this.enabled);
  final ProviderCapabilities capabilities;
  bool enabled;
}
