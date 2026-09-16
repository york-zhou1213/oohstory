import '../../adapters/cloud/cloud.dart';
import '../../adapters/contracts/adapter_contracts.dart';
import '../../core/errors.dart';
import '../../core/models.dart';
import '../local_content/local_content_service.dart';

final class CloudUploadResult {
  const CloudUploadResult.uploaded(this.entry) : queuedKey = null;
  const CloudUploadResult.queued(this.queuedKey) : entry = null;

  final CloudEntry? entry;
  final String? queuedKey;

  bool get queued => queuedKey != null;
}

final class CloudDeleteResult {
  const CloudDeleteResult.applied() : queuedKey = null;
  const CloudDeleteResult.queued(this.queuedKey);

  final String? queuedKey;

  bool get queued => queuedKey != null;
}

final class CloudLibraryService {
  CloudLibraryService({
    required this.adapter,
    LocalContentService? localContentService,
    this.offlineSynchronizer,
    this.maxDownloadBytes = 64 * 1024 * 1024,
  }) : localContentService =
           localContentService ?? LocalContentService.forCurrentPlatform();

  final CloudLibraryAdapter adapter;
  final LocalContentService localContentService;
  final OfflineCloudSynchronizer? offlineSynchronizer;
  final int maxDownloadBytes;

  Future<SyncPage<CloudEntry>> list(String path, {String? cursor}) =>
      adapter.list(path, cursor: cursor);

  Future<LocalContentBook> open(CloudEntry entry) async {
    if (entry.isDirectory || !_isSupportedBook(entry.path)) {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'Cloud file format is not supported for local reading',
      );
    }
    final bytes = await collectBytes(
      adapter.read(entry.path),
      maxBytes: maxDownloadBytes,
    );
    return localContentService.importBook(
      LocalPickedFile.fromBytes(_fileName(entry.path), bytes),
    );
  }

  Future<CloudEntry> upload(String directory, LocalPickedFile file) async {
    if (!_isSupportedBook(file.name)) {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'Only supported local reading formats can be uploaded',
      );
    }
    final bytes = await file.read(maxBytes: maxDownloadBytes);
    final path = _uploadPath(directory, file.name);
    return adapter.write(path, Stream<List<int>>.value(bytes));
  }

  /// Reads the picker stream exactly once, then safely stages the same bytes
  /// if a transient provider failure prevents the immediate create.
  Future<CloudUploadResult> uploadWithOfflineFallback(
    String directory,
    LocalPickedFile file,
  ) async {
    if (!_isSupportedBook(file.name)) {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'Only supported local reading formats can be uploaded',
      );
    }
    final bytes = await file.read(maxBytes: maxDownloadBytes);
    final path = _uploadPath(directory, file.name);
    try {
      return CloudUploadResult.uploaded(
        await adapter.write(path, Stream<List<int>>.value(bytes)),
      );
    } on Object catch (error) {
      if (!canQueueAfter(error)) rethrow;
      final key = await _requireOfflineSynchronizer().enqueueWrite(
        path,
        Stream<List<int>>.value(bytes),
      );
      return CloudUploadResult.queued(key);
    }
  }

  Future<String> queueUpload(String directory, LocalPickedFile file) async {
    final synchronizer = _requireOfflineSynchronizer();
    if (!_isSupportedBook(file.name)) {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'Only supported local reading formats can be queued',
      );
    }
    final bytes = await file.read(maxBytes: maxDownloadBytes);
    return synchronizer.enqueueWrite(
      _uploadPath(directory, file.name),
      Stream<List<int>>.value(bytes),
    );
  }

  Future<void> delete(CloudEntry entry) async {
    _validateDelete(entry);
    await adapter.delete(entry.path, etag: entry.etag);
  }

  Future<CloudDeleteResult> deleteWithOfflineFallback(CloudEntry entry) async {
    _validateDelete(entry);
    try {
      await adapter.delete(entry.path, etag: entry.etag);
      return const CloudDeleteResult.applied();
    } on Object catch (error) {
      if (!canQueueAfter(error)) rethrow;
      final key = await _requireOfflineSynchronizer().enqueueDelete(
        entry.path,
        etag: entry.etag,
      );
      return CloudDeleteResult.queued(key);
    }
  }

  Future<String> queueDelete(CloudEntry entry) {
    _validateDelete(entry);
    return _requireOfflineSynchronizer().enqueueDelete(
      entry.path,
      etag: entry.etag,
    );
  }

  Future<int> replayPending() => _requireOfflineSynchronizer().replay();

  Future<int> pendingMutationCount() async {
    final synchronizer = offlineSynchronizer;
    if (synchronizer == null) return 0;
    return (await synchronizer.store.pending(synchronizer.scope)).length;
  }

  bool get hasOfflineQueue => offlineSynchronizer != null;

  bool canQueueAfter(Object error) =>
      hasOfflineQueue &&
      error is CoreException &&
      const <CoreErrorCode>{
        CoreErrorCode.upstreamError,
        CoreErrorCode.rateLimitExceeded,
      }.contains(error.code);

  bool canOpen(CloudEntry entry) =>
      !entry.isDirectory && _isSupportedBook(entry.path);

  bool canDelete(CloudEntry entry) =>
      !entry.isDirectory && (entry.etag?.isNotEmpty ?? false);

  String describeError(Object error) {
    if (error is LocalContentException) return error.message;
    if (error is! CoreException) return '云书库暂不可用，请稍后重试';
    return switch (error.code) {
      CoreErrorCode.unauthorized => '云端凭据无效或已失效，请重新连接',
      CoreErrorCode.forbidden => '云端拒绝访问，请检查根目录和最小权限',
      CoreErrorCode.notFound => '云端文件已不存在，请刷新目录',
      CoreErrorCode.revisionConflict => '云端内容已变化，未执行覆盖或删除，请刷新后重试',
      CoreErrorCode.payloadTooLarge => '文件超过 64 MiB 的当前安全读取上限',
      CoreErrorCode.rateLimitExceeded => '云服务请求过多，请稍后重试',
      CoreErrorCode.unsupported => '该配置或文件格式当前不受支持',
      _ => '云服务响应异常，本地数据未受影响',
    };
  }

  static bool _isSupportedBook(String path) {
    final name = _fileName(path).toLowerCase();
    return LocalContentService.bookExtensions.any(
      (extension) => name.endsWith('.$extension'),
    );
  }

  static String _safeFileName(String value) {
    final name = _fileName(value).trim();
    if (name.isEmpty ||
        name == '.' ||
        name == '..' ||
        name.length > 255 ||
        name.contains('/') ||
        name.contains('\\') ||
        name.runes.any((rune) => rune < 0x20 || rune == 0x7f)) {
      throw const CoreException(
        CoreErrorCode.validationError,
        'Cloud file name is invalid',
      );
    }
    return name;
  }

  static String _fileName(String path) =>
      path.split('/').where((part) => part.isNotEmpty).lastOrNull ?? '';

  static String _uploadPath(String directory, String fileName) {
    final safeName = _safeFileName(fileName);
    return directory.isEmpty ? safeName : '$directory/$safeName';
  }

  static void _validateDelete(CloudEntry entry) {
    if (entry.isDirectory || entry.etag == null || entry.etag!.isEmpty) {
      throw const CoreException(
        CoreErrorCode.revisionConflict,
        'Cloud file cannot be deleted without a current ETag',
      );
    }
  }

  OfflineCloudSynchronizer _requireOfflineSynchronizer() {
    final synchronizer = offlineSynchronizer;
    if (synchronizer == null) {
      throw const CoreException(
        CoreErrorCode.unsupported,
        'Persistent offline cloud mutations are unavailable',
      );
    }
    return synchronizer;
  }
}
