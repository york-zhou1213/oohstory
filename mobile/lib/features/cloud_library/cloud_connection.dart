import 'dart:convert';
import 'dart:math';

import 'package:shared_preferences/shared_preferences.dart';

import '../../adapters/cloud/cloud.dart';
import '../../adapters/contracts/adapter_contracts.dart';
import '../../core/errors.dart';

enum CloudProviderKind { webDav, s3 }

extension CloudProviderKindLabel on CloudProviderKind {
  String get providerId => switch (this) {
    CloudProviderKind.webDav => 'webdav',
    CloudProviderKind.s3 => 's3',
  };

  String get label => switch (this) {
    CloudProviderKind.webDav => 'WebDAV',
    CloudProviderKind.s3 => 'S3',
  };
}

final class CloudConnectionConfig {
  CloudConnectionConfig({
    required this.id,
    required this.provider,
    required String name,
    required this.endpoint,
    required String root,
    this.bucket,
    this.region,
    this.pathStyle = true,
    this.corsVerified = false,
  }) : name = _requiredText(name, 'Cloud connection name'),
       root = _validateRoot(root) {
    CredentialScope(id);
    CloudRuntimePolicy(isWeb: false).validate(endpoint);
    if (!endpoint.isAbsolute ||
        endpoint.host.isEmpty ||
        endpoint.query.isNotEmpty ||
        endpoint.fragment.isNotEmpty) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Cloud endpoint must not contain a query or fragment',
      );
    }
    if (provider == CloudProviderKind.s3) {
      if (!RegExp(
            r'^[A-Za-z0-9][A-Za-z0-9._-]{1,62}$',
          ).hasMatch(bucket ?? '') ||
          !RegExp(r'^[a-z0-9-]{1,64}$').hasMatch(region ?? '')) {
        throw const CoreException(
          CoreErrorCode.validationError,
          'S3 bucket or region is invalid',
        );
      }
    }
  }

  factory CloudConnectionConfig.create({
    required CloudProviderKind provider,
    required String name,
    required Uri endpoint,
    required String root,
    String? bucket,
    String? region,
    bool pathStyle = true,
    bool corsVerified = false,
  }) => CloudConnectionConfig(
    id: _newConnectionId(provider),
    provider: provider,
    name: name,
    endpoint: endpoint,
    root: root,
    bucket: bucket,
    region: region,
    pathStyle: pathStyle,
    corsVerified: corsVerified,
  );

  factory CloudConnectionConfig.fromJson(Map<String, Object?> json) {
    try {
      final provider = CloudProviderKind.values.byName(
        json['provider'] as String,
      );
      return CloudConnectionConfig(
        id: json['id'] as String,
        provider: provider,
        name: json['name'] as String,
        endpoint: Uri.parse(json['endpoint'] as String),
        root: json['root'] as String,
        bucket: json['bucket'] as String?,
        region: json['region'] as String?,
        pathStyle: json['path_style'] as bool? ?? true,
        corsVerified: json['cors_verified'] as bool? ?? false,
      );
    } on CoreException {
      rethrow;
    } on Object {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Stored cloud connection is invalid',
      );
    }
  }

  final String id;
  final CloudProviderKind provider;
  final String name;
  final Uri endpoint;
  final String root;
  final String? bucket;
  final String? region;
  final bool pathStyle;
  final bool corsVerified;

  Map<String, Object?> toJson() => <String, Object?>{
    'id': id,
    'provider': provider.name,
    'name': name,
    'endpoint': endpoint.toString(),
    'root': root,
    'bucket': bucket,
    'region': region,
    'path_style': pathStyle,
    'cors_verified': corsVerified,
  };

  static String _validateRoot(String value) {
    final trimmed = value.trim();
    CloudRoot(trimmed);
    return trimmed;
  }

  static String _requiredText(String value, String field) {
    final trimmed = value.trim();
    if (trimmed.isEmpty ||
        trimmed.length > 256 ||
        trimmed.runes.any((rune) => rune < 0x20 || rune == 0x7f)) {
      throw CoreException(CoreErrorCode.validationError, '$field is invalid');
    }
    return trimmed;
  }

  static String _newConnectionId(CloudProviderKind provider) {
    final random = Random.secure();
    final suffix = List<int>.generate(
      16,
      (_) => random.nextInt(256),
    ).map((value) => value.toRadixString(16).padLeft(2, '0')).join();
    return '${provider.providerId}-$suffix';
  }
}

final class CloudConnectionCredentials {
  const CloudConnectionCredentials._(this.values);

  factory CloudConnectionCredentials.webDav({
    required String username,
    required String password,
  }) => CloudConnectionCredentials._(<String, String>{
    'username': _credential(username),
    'password': _credential(password),
  });

  factory CloudConnectionCredentials.s3({
    required String accessKey,
    required String secretKey,
    String? sessionToken,
  }) => CloudConnectionCredentials._(<String, String>{
    'access_key': _credential(accessKey),
    'secret_key': _credential(secretKey),
    if (sessionToken != null && sessionToken.trim().isNotEmpty)
      'session_token': _credential(sessionToken),
  });

  final Map<String, String> values;

  static String _credential(String value) {
    if (value.isEmpty ||
        value.length > 16384 ||
        value.runes.any((rune) => rune < 0x20 || rune == 0x7f)) {
      throw const CoreException(
        CoreErrorCode.unauthorized,
        'Cloud provider credentials are invalid',
      );
    }
    return value;
  }
}

final class CloudConnectionRepository {
  CloudConnectionRepository({
    required SharedPreferences preferences,
    required this.credentialStore,
  }) : _preferences = preferences;

  static const _settingsKey = 'oohstory.cloud.connections.v1';
  static const _credentialFields = <String>{
    'username',
    'password',
    'access_key',
    'secret_key',
    'session_token',
  };

  final SharedPreferences _preferences;
  final SecureCredentialStore credentialStore;

  List<CloudConnectionConfig> load() {
    final raw = _preferences.getString(_settingsKey);
    if (raw == null || raw.isEmpty) return const <CloudConnectionConfig>[];
    try {
      final decoded = jsonDecode(raw);
      if (decoded is! Map<String, dynamic> || decoded['version'] != 1) {
        throw const FormatException();
      }
      final items = decoded['connections'];
      if (items is! List) throw const FormatException();
      final connections = items
          .map(
            (item) => CloudConnectionConfig.fromJson(
              Map<String, Object?>.from(item as Map),
            ),
          )
          .toList(growable: false);
      if (connections.map((item) => item.id).toSet().length !=
          connections.length) {
        throw const FormatException();
      }
      return connections;
    } on CoreException {
      rethrow;
    } on Object {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Stored cloud connections are invalid',
      );
    }
  }

  Future<void> upsert(CloudConnectionConfig config) async {
    final connections = load().toList(growable: true);
    final index = connections.indexWhere((item) => item.id == config.id);
    if (index < 0) {
      connections.add(config);
    } else {
      connections[index] = config;
    }
    await _persist(connections);
  }

  Future<void> remove(String id) async {
    final config = load().where((item) => item.id == id).firstOrNull;
    if (config == null) return;
    await clearCredentials(config);
    await _persist(load().where((item) => item.id != id).toList());
  }

  Future<Map<String, String?>> credentialSnapshot(
    CloudConnectionConfig config,
  ) async {
    final scope = CredentialScope(config.id);
    return <String, String?>{
      for (final field in _credentialFields)
        field: await credentialStore.read(scope.key(field)),
    };
  }

  Future<void> writeCredentials(
    CloudConnectionConfig config,
    CloudConnectionCredentials credentials,
  ) async {
    final scope = CredentialScope(config.id);
    for (final field in _credentialFields) {
      final value = credentials.values[field];
      if (value == null) {
        await credentialStore.delete(scope.key(field));
      } else {
        await credentialStore.write(scope.key(field), value);
      }
    }
  }

  Future<void> restoreCredentials(
    CloudConnectionConfig config,
    Map<String, String?> snapshot,
  ) async {
    final scope = CredentialScope(config.id);
    for (final field in _credentialFields) {
      final value = snapshot[field];
      if (value == null) {
        await credentialStore.delete(scope.key(field));
      } else {
        await credentialStore.write(scope.key(field), value);
      }
    }
  }

  Future<void> clearCredentials(CloudConnectionConfig config) async {
    final scope = CredentialScope(config.id);
    for (final field in _credentialFields) {
      await credentialStore.delete(scope.key(field));
    }
  }

  Future<void> _persist(List<CloudConnectionConfig> connections) async {
    final payload = jsonEncode(<String, Object?>{
      'version': 1,
      'connections': connections
          .map((connection) => connection.toJson())
          .toList(growable: false),
    });
    if (!await _preferences.setString(_settingsKey, payload)) {
      throw const CoreException(
        CoreErrorCode.upstreamError,
        'Cloud connection settings could not be saved',
      );
    }
  }
}

final class CloudAdapterFactory {
  const CloudAdapterFactory({this.transport});

  final CloudHttpTransport? transport;

  CloudLibraryAdapter build(
    CloudConnectionConfig config,
    SecureCredentialStore credentialStore,
  ) {
    final runtimePolicy = CloudRuntimePolicy.current(
      corsVerified: config.corsVerified,
    );
    final requestTransport = transport ?? PackageHttpTransport();
    final scope = CredentialScope(config.id);
    return switch (config.provider) {
      CloudProviderKind.webDav => WebDavCloudAdapter(
        endpoint: config.endpoint,
        root: config.root,
        transport: requestTransport,
        credentialStore: credentialStore,
        credentialScope: scope,
        runtimePolicy: runtimePolicy,
      ),
      CloudProviderKind.s3 => S3CloudAdapter(
        endpoint: config.endpoint,
        bucket: config.bucket!,
        root: config.root,
        region: config.region!,
        pathStyle: config.pathStyle,
        transport: requestTransport,
        credentialStore: credentialStore,
        credentialScope: scope,
        runtimePolicy: runtimePolicy,
      ),
    };
  }
}

final class CloudConnectionManager {
  const CloudConnectionManager({
    required this.repository,
    this.adapterFactory = const CloudAdapterFactory(),
  });

  final CloudConnectionRepository repository;
  final CloudAdapterFactory adapterFactory;

  Future<void> verifyAndSave(
    CloudConnectionConfig config,
    CloudConnectionCredentials credentials,
  ) async {
    final snapshot = await repository.credentialSnapshot(config);
    await repository.writeCredentials(config, credentials);
    try {
      final adapter = adapterFactory.build(config, repository.credentialStore);
      await adapter.list('');
      await repository.upsert(config);
    } on Object {
      await repository.restoreCredentials(config, snapshot);
      rethrow;
    }
  }

  CloudLibraryAdapter adapterFor(CloudConnectionConfig config) =>
      adapterFactory.build(config, repository.credentialStore);

  Future<void> remove(CloudConnectionConfig config) =>
      repository.remove(config.id);
}
