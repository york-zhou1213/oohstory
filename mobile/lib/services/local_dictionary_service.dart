import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:path/path.dart' as path_utils;
import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../adapters/dictionary/mdx_dictionary_adapter.dart';
import '../core/errors.dart';
import '../core/models.dart';

class LocalDictionaryInfo {
  const LocalDictionaryInfo({
    required this.id,
    required this.name,
    required this.mdxSize,
    required this.mdxSha256,
    required this.entryCount,
    required this.enabled,
    required this.order,
    required this.addedAt,
    this.mddSize,
    this.mddSha256,
  });

  final String id;
  final String name;
  final int mdxSize;
  final String mdxSha256;
  final int? mddSize;
  final String? mddSha256;
  final int entryCount;
  final bool enabled;
  final int order;
  final int addedAt;

  bool get hasResources => mddSize != null;

  LocalDictionaryInfo copyWith({bool? enabled, int? order}) =>
      LocalDictionaryInfo(
        id: id,
        name: name,
        mdxSize: mdxSize,
        mdxSha256: mdxSha256,
        mddSize: mddSize,
        mddSha256: mddSha256,
        entryCount: entryCount,
        enabled: enabled ?? this.enabled,
        order: order ?? this.order,
        addedAt: addedAt,
      );

  Map<String, Object?> toJson() => <String, Object?>{
    'id': id,
    'name': name,
    'mdxSize': mdxSize,
    'mdxSha256': mdxSha256,
    'mddSize': mddSize,
    'mddSha256': mddSha256,
    'entryCount': entryCount,
    'enabled': enabled,
    'order': order,
    'addedAt': addedAt,
  };

  factory LocalDictionaryInfo.fromJson(Map<String, dynamic> json) {
    final id = json['id'] as String? ?? '';
    if (!RegExp(r'^dict_[a-f0-9]{24}$').hasMatch(id)) {
      throw const FormatException('Invalid local dictionary ID');
    }
    final mdxSha256 = json['mdxSha256'] as String? ?? '';
    final mddSha256 = json['mddSha256'] as String?;
    if (!_isSha256(mdxSha256) || (mddSha256 != null && !_isSha256(mddSha256))) {
      throw const FormatException('Invalid local dictionary hash');
    }
    final name = (json['name'] as String? ?? '').trim();
    final mdxSize = json['mdxSize'] as int? ?? -1;
    final mddSize = json['mddSize'] as int?;
    final entryCount = json['entryCount'] as int? ?? -1;
    final order = json['order'] as int? ?? -1;
    final addedAt = json['addedAt'] as int? ?? 0;
    if (name.isEmpty ||
        name.length > 240 ||
        mdxSize <= 0 ||
        (mddSize != null && mddSize <= 0) ||
        entryCount <= 0 ||
        order < 0 ||
        addedAt <= 0) {
      throw const FormatException('Invalid local dictionary metadata');
    }
    return LocalDictionaryInfo(
      id: id,
      name: name,
      mdxSize: mdxSize,
      mdxSha256: mdxSha256,
      mddSize: mddSize,
      mddSha256: mddSha256,
      entryCount: entryCount,
      enabled: json['enabled'] as bool? ?? true,
      order: order,
      addedAt: addedAt,
    );
  }
}

class LocalDictionaryResult {
  const LocalDictionaryResult({
    required this.dictionaryId,
    required this.dictionaryName,
    required this.entries,
  });

  final String dictionaryId;
  final String dictionaryName;
  final List<DictionaryEntry> entries;
}

class LocalDictionaryService {
  LocalDictionaryService({
    Future<Directory> Function()? documentsDirectory,
    SharedPreferences? preferences,
  }) : _documentsDirectory =
           documentsDirectory ?? getApplicationDocumentsDirectory,
       _preferences = preferences;

  static const maxDictionaryBytes = 64 * 1024 * 1024;
  static const maxResourceBytes = 64 * 1024 * 1024;
  static const _metadataKey = 'oohstory_local_dictionaries_v1';
  static const _limits = MdxLimits(
    maxInputBytes: maxDictionaryBytes,
    maxExpandedBytes: 128 * 1024 * 1024,
  );

  final Future<Directory> Function() _documentsDirectory;
  SharedPreferences? _preferences;
  Directory? _directory;
  List<LocalDictionaryInfo> _items = const <LocalDictionaryInfo>[];
  final Map<String, _LoadedDictionary> _cache = <String, _LoadedDictionary>{};
  bool _initialized = false;

  Future<void> init() async {
    if (_initialized) return;
    _preferences ??= await SharedPreferences.getInstance();
    final documents = await _documentsDirectory();
    _directory = Directory(
      path_utils.join(documents.path, 'oohstory_dictionaries'),
    );
    await _directory!.create(recursive: true);
    _items = _readMetadata();
    _initialized = true;
  }

  List<LocalDictionaryInfo> list() {
    _requireInitialized();
    return List<LocalDictionaryInfo>.unmodifiable(_items);
  }

  Future<LocalDictionaryInfo> import({
    required String name,
    required Uint8List mdxBytes,
    Uint8List? mddBytes,
  }) async {
    _requireInitialized();
    final displayName = _safeDisplayName(name);
    if (mdxBytes.isEmpty || mdxBytes.length > maxDictionaryBytes) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'MDX input exceeds the configured size limit',
      );
    }
    if (mddBytes != null &&
        (mddBytes.isEmpty || mddBytes.length > maxResourceBytes)) {
      throw const CoreException(
        CoreErrorCode.payloadTooLarge,
        'MDD input exceeds the configured size limit',
      );
    }

    final adapter = MdxDictionaryAdapter.fromBytes(mdxBytes, limits: _limits);
    final resources = mddBytes == null
        ? null
        : MddResourceAdapter.fromBytes(mddBytes, limits: _limits);
    final mdxHash = sha256.convert(mdxBytes).toString();
    final id = 'dict_${mdxHash.substring(0, 24)}';
    final mddHash = mddBytes == null
        ? null
        : sha256.convert(mddBytes).toString();
    final existingIndex = _items.indexWhere((item) => item.id == id);
    final existing = existingIndex < 0 ? null : _items[existingIndex];
    final info = LocalDictionaryInfo(
      id: id,
      name: displayName,
      mdxSize: mdxBytes.length,
      mdxSha256: mdxHash,
      mddSize: mddBytes?.length,
      mddSha256: mddHash,
      entryCount: adapter.entryCount,
      enabled: existing?.enabled ?? true,
      order: existing?.order ?? _items.length,
      addedAt: existing?.addedAt ?? DateTime.now().millisecondsSinceEpoch,
    );

    final mdxFile = _file(id, 'mdx');
    final mddFile = _file(id, 'mdd');
    await _writeAtomically(mdxFile, mdxBytes);
    if (mddBytes != null) {
      await _writeAtomically(mddFile, mddBytes);
    } else if (await mddFile.exists()) {
      await mddFile.delete();
    }
    final next = <LocalDictionaryInfo>[..._items];
    if (existingIndex < 0) {
      next.add(info);
    } else {
      next[existingIndex] = info;
    }
    await _persist(next);
    _cacheLoaded(id, _LoadedDictionary(adapter, resources));
    return info;
  }

  Future<void> setEnabled(String id, bool enabled) async {
    _requireInitialized();
    await _replace(id, (item) => item.copyWith(enabled: enabled));
    if (!enabled) _cache.remove(id);
  }

  Future<void> move(String id, int delta) async {
    _requireInitialized();
    if (delta != -1 && delta != 1) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Dictionary move delta must be -1 or 1',
      );
    }
    final index = _items.indexWhere((item) => item.id == id);
    if (index < 0) throw const FormatException('Dictionary not found');
    final target = index + delta;
    if (target < 0 || target >= _items.length) return;
    final next = <LocalDictionaryInfo>[..._items];
    final moved = next.removeAt(index);
    next.insert(target, moved);
    await _persist(_withSequentialOrder(next));
  }

  Future<void> remove(String id) async {
    _requireInitialized();
    final index = _items.indexWhere((item) => item.id == id);
    if (index < 0) return;
    final next = <LocalDictionaryInfo>[..._items]..removeAt(index);
    await _persist(_withSequentialOrder(next));
    _cache.remove(id);
    for (final extension in const <String>['mdx', 'mdd']) {
      final file = _file(id, extension);
      if (await file.exists()) await file.delete();
    }
  }

  Future<List<LocalDictionaryResult>> lookup(String term) async {
    _requireInitialized();
    final query = term.trim();
    if (query.isEmpty || query.length > 512) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Dictionary lookup term is blank or too long',
      );
    }
    final results = <LocalDictionaryResult>[];
    for (final info in _items.where((item) => item.enabled)) {
      final loaded = await _load(info);
      final entries = await loaded.adapter.lookup(query);
      if (entries.isNotEmpty) {
        results.add(
          LocalDictionaryResult(
            dictionaryId: info.id,
            dictionaryName: info.name,
            entries: entries,
          ),
        );
      }
    }
    return List<LocalDictionaryResult>.unmodifiable(results);
  }

  Future<Uint8List?> resource(String dictionaryId, String path) async {
    _requireInitialized();
    final info = _items.where((item) => item.id == dictionaryId).singleOrNull;
    if (info == null || !info.hasResources) return null;
    final loaded = await _load(info);
    return loaded.resources?.lookup(path);
  }

  Future<_LoadedDictionary> _load(LocalDictionaryInfo info) async {
    final cached = _cache[info.id];
    if (cached != null) return cached;
    final mdxBytes = await _readVerified(
      _file(info.id, 'mdx'),
      info.mdxSize,
      info.mdxSha256,
      maxDictionaryBytes,
    );
    Uint8List? mddBytes;
    if (info.hasResources) {
      mddBytes = await _readVerified(
        _file(info.id, 'mdd'),
        info.mddSize!,
        info.mddSha256!,
        maxResourceBytes,
      );
    }
    final loaded = _LoadedDictionary(
      MdxDictionaryAdapter.fromBytes(mdxBytes, limits: _limits),
      mddBytes == null
          ? null
          : MddResourceAdapter.fromBytes(mddBytes, limits: _limits),
    );
    _cacheLoaded(info.id, loaded);
    return loaded;
  }

  void _cacheLoaded(String id, _LoadedDictionary loaded) {
    _cache.remove(id);
    while (_cache.length >= 2) {
      _cache.remove(_cache.keys.first);
    }
    _cache[id] = loaded;
  }

  Future<Uint8List> _readVerified(
    File file,
    int expectedSize,
    String expectedHash,
    int maxBytes,
  ) async {
    final stat = await file.stat();
    if (stat.type != FileSystemEntityType.file ||
        stat.size != expectedSize ||
        stat.size > maxBytes) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Local dictionary file is missing or has changed',
      );
    }
    final bytes = await file.readAsBytes();
    if (sha256.convert(bytes).toString() != expectedHash) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Local dictionary checksum mismatch',
      );
    }
    return bytes;
  }

  Future<void> _replace(
    String id,
    LocalDictionaryInfo Function(LocalDictionaryInfo) update,
  ) async {
    final index = _items.indexWhere((item) => item.id == id);
    if (index < 0) throw const FormatException('Dictionary not found');
    final next = <LocalDictionaryInfo>[..._items];
    next[index] = update(next[index]);
    await _persist(next);
  }

  Future<void> _persist(List<LocalDictionaryInfo> items) async {
    final sorted = <LocalDictionaryInfo>[...items]
      ..sort((left, right) => left.order.compareTo(right.order));
    final encoded = jsonEncode(
      sorted.map((item) => item.toJson()).toList(growable: false),
    );
    final saved = await _preferences!.setString(_metadataKey, encoded);
    if (!saved) throw const FileSystemException('Could not save dictionaries');
    _items = List<LocalDictionaryInfo>.unmodifiable(sorted);
  }

  List<LocalDictionaryInfo> _readMetadata() {
    final raw = _preferences!.getString(_metadataKey);
    if (raw == null || raw.isEmpty) return const <LocalDictionaryInfo>[];
    try {
      final values = jsonDecode(raw) as List<dynamic>;
      final unique = <String>{};
      final parsed = <LocalDictionaryInfo>[];
      for (final value in values) {
        try {
          final item = LocalDictionaryInfo.fromJson(
            Map<String, dynamic>.from(value as Map),
          );
          if (unique.add(item.id)) parsed.add(item);
        } on Object {
          continue;
        }
      }
      parsed.sort((left, right) => left.order.compareTo(right.order));
      return List<LocalDictionaryInfo>.unmodifiable(
        _withSequentialOrder(parsed),
      );
    } on Object {
      return const <LocalDictionaryInfo>[];
    }
  }

  List<LocalDictionaryInfo> _withSequentialOrder(
    List<LocalDictionaryInfo> items,
  ) => List<LocalDictionaryInfo>.generate(
    items.length,
    (index) => items[index].copyWith(order: index),
    growable: false,
  );

  File _file(String id, String extension) {
    if (!RegExp(r'^dict_[a-f0-9]{24}$').hasMatch(id) ||
        !const <String>{'mdx', 'mdd'}.contains(extension)) {
      throw const FormatException('Unsafe dictionary file identifier');
    }
    return File(path_utils.join(_directory!.path, '$id.$extension'));
  }

  Future<void> _writeAtomically(File target, Uint8List bytes) async {
    final temporary = File('${target.path}.tmp');
    try {
      await temporary.writeAsBytes(bytes, flush: true);
      if (await target.exists()) await target.delete();
      await temporary.rename(target.path);
    } finally {
      if (await temporary.exists()) await temporary.delete();
    }
  }

  void _requireInitialized() {
    if (!_initialized) throw StateError('LocalDictionaryService.init required');
  }
}

class _LoadedDictionary {
  const _LoadedDictionary(this.adapter, this.resources);
  final MdxDictionaryAdapter adapter;
  final MddResourceAdapter? resources;
}

String _safeDisplayName(String name) {
  final normalized = name.replaceAll('\\', '/').split('/').last.trim();
  final withoutExtension = normalized.toLowerCase().endsWith('.mdx')
      ? normalized.substring(0, normalized.length - 4)
      : normalized;
  if (withoutExtension.isEmpty || withoutExtension.length > 240) {
    throw const CoreException(
      CoreErrorCode.validationError,
      'Dictionary name is invalid',
    );
  }
  return withoutExtension;
}

bool _isSha256(String value) => RegExp(r'^[a-f0-9]{64}$').hasMatch(value);
