import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:path/path.dart' as paths;
import 'package:shared_preferences/shared_preferences.dart';

import '../../adapters/contracts/adapter_contracts.dart';
import '../../core/core.dart';
import '../../services/local_storage_service.dart';
import 'annotation_documents.dart';
import 'annotation_markdown.dart';

abstract interface class ExportReceiptStore {
  Future<ExportReceipt?> read({
    required String providerId,
    required String documentId,
    required String target,
  });

  Future<void> write(ExportReceipt receipt);
}

class SharedPreferencesExportReceiptStore implements ExportReceiptStore {
  SharedPreferencesExportReceiptStore(this._preferences);

  static const _storageKey = 'oohstory_export_receipts_v1';
  final SharedPreferences _preferences;

  @override
  Future<ExportReceipt?> read({
    required String providerId,
    required String documentId,
    required String target,
  }) async {
    final raw = _readAll()[_receiptKey(providerId, documentId, target)];
    if (raw is! Map) return null;
    try {
      return ExportReceipt.fromJson(Map<String, Object?>.from(raw));
    } on Object {
      return null;
    }
  }

  @override
  Future<void> write(ExportReceipt receipt) async {
    final receipts = _readAll();
    receipts[_receiptKey(
      receipt.providerId,
      receipt.documentId,
      receipt.target,
    )] = receipt
        .toJson();
    await _preferences.setString(_storageKey, jsonEncode(receipts));
  }

  Map<String, Object?> _readAll() {
    final raw = _preferences.getString(_storageKey);
    if (raw == null) return <String, Object?>{};
    try {
      return Map<String, Object?>.from(jsonDecode(raw) as Map);
    } on Object {
      return <String, Object?>{};
    }
  }

  String _receiptKey(String providerId, String documentId, String target) =>
      sha256
          .convert(utf8.encode('$providerId\u0000$documentId\u0000$target'))
          .toString();
}

class ObsidianExportConflict implements Exception {
  const ObsidianExportConflict({
    required this.document,
    required this.target,
    required this.reason,
  });

  final DocumentIdentity document;
  final String target;
  final String reason;

  @override
  String toString() => 'Obsidian export conflict for ${document.id}: $reason';
}

typedef ObsidianExportDocument = AnnotationExportDocument;

class ObsidianExportBatchResult {
  const ObsidianExportBatchResult({
    required this.receipts,
    required this.conflicts,
  });

  final List<ExportReceipt> receipts;
  final List<ObsidianExportConflict> conflicts;

  int get changedCount => receipts
      .where((receipt) => receipt.disposition != ExportDisposition.unchanged)
      .length;

  int get unchangedCount => receipts.length - changedCount;
}

class ObsidianVaultExporter implements AnnotationSink {
  ObsidianVaultExporter({
    required Directory vaultRoot,
    required String subdirectory,
    required ExportReceiptStore receiptStore,
    DateTime Function()? now,
  }) : _vaultRoot = vaultRoot,
       _subdirectory = _validateSubdirectory(subdirectory),
       _receiptStore = receiptStore,
       _now = now ?? (() => DateTime.now().toUtc());

  static const int _maxMarkdownBytes = 32 * 1024 * 1024;
  final Directory _vaultRoot;
  final List<String> _subdirectory;
  final ExportReceiptStore _receiptStore;
  final DateTime Function() _now;
  static const _markdownRenderer = AnnotationMarkdownRenderer();

  @override
  String get providerId => 'obsidian';

  @override
  ProviderCapabilities get capabilities => ProviderCapabilities(
    providerId: providerId,
    supported: const <AdapterCapability>[AdapterCapability.annotationExport],
  );

  @override
  Future<ExportReceipt> export(
    DocumentIdentity document,
    List<Annotation> annotations, {
    required String idempotencyKey,
    bool overwriteExternalChanges = false,
  }) async {
    _validateInput(document, annotations, idempotencyKey);
    final exportDirectory = await _prepareExportDirectory();
    final target = File(
      paths.join(exportDirectory.path, _stableFileName(document)),
    );
    final targetPath = paths.normalize(target.absolute.path);
    final type = await FileSystemEntity.type(targetPath, followLinks: false);
    if (type == FileSystemEntityType.link) {
      throw const FormatException('Obsidian 目标文件不能是符号链接');
    }
    if (type != FileSystemEntityType.notFound &&
        type != FileSystemEntityType.file) {
      throw const FormatException('Obsidian 目标不是普通文件');
    }

    final markdown = _markdownRenderer.render(document, annotations);
    final contentBytes = utf8.encode(markdown);
    if (contentBytes.length > _maxMarkdownBytes) {
      throw const FormatException('单本书的 Obsidian 导出不能超过 32 MB');
    }
    final contentHash = sha256.convert(contentBytes).toString();
    final existed = type == FileSystemEntityType.file;
    String? existingHash;
    List<int>? existingBytes;
    ExportReceipt? previous;
    if (existed) {
      if (await target.length() > _maxMarkdownBytes) {
        throw ObsidianExportConflict(
          document: document,
          target: targetPath,
          reason: '现有文件超过 32 MB，无法安全比对',
        );
      }
      existingBytes = await target.readAsBytes();
      existingHash = sha256.convert(existingBytes).toString();
      if (existingHash == contentHash) {
        final receipt = _receipt(
          document: document,
          target: targetPath,
          contentHash: contentHash,
          disposition: ExportDisposition.unchanged,
        );
        await _receiptStore.write(receipt);
        return receipt;
      }

      previous = await _receiptStore.read(
        providerId: providerId,
        documentId: document.id,
        target: targetPath,
      );
      final externallyModified =
          previous == null || previous.contentHash != existingHash;
      if (externallyModified && !overwriteExternalChanges) {
        throw ObsidianExportConflict(
          document: document,
          target: targetPath,
          reason: previous == null ? '现有文件没有可信导出回执' : '文件在上次导出后被修改',
        );
      }
    }

    String? backupTarget;
    var isExternalOverwrite = existed && previous?.contentHash != existingHash;
    if (existed) {
      final currentType = await FileSystemEntity.type(
        targetPath,
        followLinks: false,
      );
      if (currentType != FileSystemEntityType.file ||
          await target.length() > _maxMarkdownBytes) {
        throw ObsidianExportConflict(
          document: document,
          target: targetPath,
          reason: '目标文件在写入前发生结构变化',
        );
      }
      final currentBytes = await target.readAsBytes();
      final currentHash = sha256.convert(currentBytes).toString();
      if (currentHash != existingHash) {
        if (!overwriteExternalChanges) {
          throw ObsidianExportConflict(
            document: document,
            target: targetPath,
            reason: '目标文件在写入前再次被修改',
          );
        }
        isExternalOverwrite = true;
      }
      existingBytes = currentBytes;
      existingHash = currentHash;
    }
    if (isExternalOverwrite && overwriteExternalChanges) {
      backupTarget = await _backupExternalFile(
        exportDirectory,
        target,
        existingBytes!,
        existingHash!,
      );
    }

    await _replaceAtomically(target, contentBytes);
    final receipt = _receipt(
      document: document,
      target: targetPath,
      contentHash: contentHash,
      disposition: !existed
          ? ExportDisposition.created
          : isExternalOverwrite
          ? ExportDisposition.overwritten
          : ExportDisposition.updated,
      backupTarget: backupTarget,
    );
    await _receiptStore.write(receipt);
    return receipt;
  }

  ExportReceipt _receipt({
    required DocumentIdentity document,
    required String target,
    required String contentHash,
    required ExportDisposition disposition,
    String? backupTarget,
  }) => ExportReceipt(
    providerId: providerId,
    documentId: document.id,
    target: target,
    contentHash: contentHash,
    exportedAt: _now().toUtc(),
    disposition: disposition,
    backupTarget: backupTarget,
  );

  Future<Directory> _prepareExportDirectory() async {
    if (!await _vaultRoot.exists()) {
      throw const FormatException('选择的 Obsidian Vault 不存在');
    }
    final root = Directory(await _vaultRoot.resolveSymbolicLinks());
    var current = root;
    for (final segment in _subdirectory) {
      final nextPath = paths.join(current.path, segment);
      final type = await FileSystemEntity.type(nextPath, followLinks: false);
      if (type == FileSystemEntityType.link) {
        throw const FormatException('Obsidian 导出目录不能包含符号链接');
      }
      if (type == FileSystemEntityType.notFound) {
        await Directory(nextPath).create();
      } else if (type != FileSystemEntityType.directory) {
        throw const FormatException('Obsidian 导出路径不是目录');
      }
      final resolved = Directory(
        await Directory(nextPath).resolveSymbolicLinks(),
      );
      if (resolved.path != root.path &&
          !paths.isWithin(root.path, resolved.path)) {
        throw const FormatException('Obsidian 导出目录越过了 Vault 边界');
      }
      current = resolved;
    }
    return current;
  }

  Future<String> _backupExternalFile(
    Directory exportDirectory,
    File target,
    List<int> bytes,
    String contentHash,
  ) async {
    final backupDirectory = Directory(
      paths.join(exportDirectory.path, '.oohstory-backups'),
    );
    final type = await FileSystemEntity.type(
      backupDirectory.path,
      followLinks: false,
    );
    if (type == FileSystemEntityType.link) {
      throw const FormatException('Obsidian 备份目录不能是符号链接');
    }
    if (type == FileSystemEntityType.notFound) {
      await backupDirectory.create();
    } else if (type != FileSystemEntityType.directory) {
      throw const FormatException('Obsidian 备份路径不是目录');
    }
    final resolved = await backupDirectory.resolveSymbolicLinks();
    if (!paths.isWithin(exportDirectory.path, resolved)) {
      throw const FormatException('Obsidian 备份目录越过了导出边界');
    }
    final stamp = _now().toUtc().toIso8601String().replaceAll(
      RegExp(r'[^0-9]'),
      '',
    );
    final stem = paths.basenameWithoutExtension(target.path);
    var candidate = File(
      paths.join(resolved, '$stem--$stamp--${contentHash.substring(0, 8)}.md'),
    );
    var suffix = 1;
    while (await candidate.exists()) {
      candidate = File(
        paths.join(
          resolved,
          '$stem--$stamp--${contentHash.substring(0, 8)}-$suffix.md',
        ),
      );
      suffix++;
    }
    await candidate.writeAsBytes(bytes, flush: true);
    return paths.normalize(candidate.absolute.path);
  }

  Future<void> _replaceAtomically(File target, List<int> bytes) async {
    final nonce = '${_now().microsecondsSinceEpoch}-${bytes.length}';
    final temporary = File('${target.path}.oohstory-tmp-$nonce');
    final swap = File('${target.path}.oohstory-swap-$nonce');
    await temporary.writeAsBytes(bytes, flush: true);
    var movedOriginal = false;
    try {
      if (await target.exists()) {
        await target.rename(swap.path);
        movedOriginal = true;
      }
      await temporary.rename(target.path);
      if (movedOriginal && await swap.exists()) await swap.delete();
    } on Object {
      if (await temporary.exists()) await temporary.delete();
      if (movedOriginal && await swap.exists() && !await target.exists()) {
        await swap.rename(target.path);
      }
      rethrow;
    }
  }

  static String _singleLine(String value) =>
      AnnotationMarkdownRenderer.singleLine(value);

  static String _stableFileName(DocumentIdentity document) {
    var title = _singleLine(document.title)
        .replaceAll(RegExp(r'[\x00-\x1f\x7f]'), '-')
        .replaceAll(RegExp(r'[\\/:*?"<>|]'), '-')
        .replaceAll(RegExp(r'\s+'), ' ')
        .replaceAll(RegExp(r'^[. ]+|[. ]+$'), '');
    if (title.isEmpty) title = '未命名书籍';
    if (RegExp(
      r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])$',
      caseSensitive: false,
    ).hasMatch(title)) {
      title = '$title-book';
    }
    if (title.runes.length > 80) {
      title = String.fromCharCodes(title.runes.take(80)).trimRight();
    }
    final suffix = sha256
        .convert(utf8.encode(document.id))
        .toString()
        .substring(0, 12);
    return '$title--$suffix.md';
  }

  static List<String> _validateSubdirectory(String value) {
    final trimmed = value.trim();
    if (trimmed.isEmpty) return const <String>['OOHStory'];
    if (paths.isAbsolute(trimmed) || trimmed.contains('\\')) {
      throw const FormatException('Obsidian 子目录必须是 Vault 内的相对路径');
    }
    final segments = trimmed.split('/');
    for (final segment in segments) {
      if (segment.isEmpty ||
          segment == '.' ||
          segment == '..' ||
          segment.contains(RegExp(r'[\x00-\x1f<>:"|?*]'))) {
        throw const FormatException('Obsidian 子目录包含不安全字符');
      }
    }
    return List<String>.unmodifiable(segments);
  }

  static void _validateInput(
    DocumentIdentity document,
    List<Annotation> annotations,
    String idempotencyKey,
  ) {
    if (document.id.trim().isEmpty ||
        document.title.trim().isEmpty ||
        document.documentVersion.trim().isEmpty ||
        idempotencyKey.trim().isEmpty ||
        annotations.isEmpty) {
      throw const FormatException('Obsidian 导出数据不完整');
    }
    if (annotations.any(
      (annotation) =>
          annotation.id.trim().isEmpty || annotation.bookId != document.id,
    )) {
      throw const FormatException('批注与书籍标识不匹配');
    }
  }
}

class ObsidianAnnotationExportService {
  const ObsidianAnnotationExportService(this._exporter);

  final ObsidianVaultExporter _exporter;

  Future<ObsidianExportBatchResult> exportDocuments(
    Iterable<ObsidianExportDocument> documents, {
    bool overwriteExternalChanges = false,
  }) async {
    final receipts = <ExportReceipt>[];
    final conflicts = <ObsidianExportConflict>[];
    for (final document in documents) {
      try {
        receipts.add(
          await _exporter.export(
            document.identity,
            document.annotations,
            idempotencyKey: _idempotencyKey(document),
            overwriteExternalChanges: overwriteExternalChanges,
          ),
        );
      } on ObsidianExportConflict catch (conflict) {
        conflicts.add(conflict);
      }
    }
    return ObsidianExportBatchResult(
      receipts: List<ExportReceipt>.unmodifiable(receipts),
      conflicts: List<ObsidianExportConflict>.unmodifiable(conflicts),
    );
  }

  static String _idempotencyKey(ObsidianExportDocument document) {
    final annotationIds = document.annotations.map((item) => item.id).toList()
      ..sort();
    return sha256
        .convert(
          utf8.encode(
            '${document.identity.id}\u0000${annotationIds.join('\u0000')}',
          ),
        )
        .toString();
  }

  static List<ObsidianExportDocument> documentsFrom(
    LocalStorageService storage,
  ) => StoredAnnotationExportSource.documentsFrom(storage);
}
