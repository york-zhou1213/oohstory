import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../adapters/cloud/cloud.dart';
import '../core/errors.dart';
import '../core/models.dart';
import '../core/product_capabilities.dart';
import '../features/annotation_export/annotation_export.dart';
import '../models/reader_preferences.dart';
import '../services/local_storage_service.dart';

class OfflineNotesScreen extends StatefulWidget {
  const OfflineNotesScreen({
    super.key,
    this.capabilities = ProductCapabilityProfile.production,
  });

  final ProductCapabilityProfile capabilities;

  @override
  State<OfflineNotesScreen> createState() => _OfflineNotesScreenState();
}

class _OfflineNotesScreenState extends State<OfflineNotesScreen> {
  final _storage = LocalStorageService();
  final _search = TextEditingController();
  List<OfflineAnnotation> _items = [];
  bool _loading = true;
  bool _exportingObsidian = false;
  bool _exportingNotion = false;
  bool _exportingJoplin = false;
  bool _hasAnnotations = false;

  @override
  void initState() {
    super.initState();
    _initialize();
  }

  Future<void> _initialize() async {
    await _storage.init();
    _reload();
    if (mounted) setState(() => _loading = false);
  }

  void _reload() {
    final query = _search.text.trim().toLowerCase();
    final items = _storage.getAnnotations();
    setState(() {
      _hasAnnotations = items.isNotEmpty;
      _items = query.isEmpty
          ? items
          : items
                .where(
                  (item) =>
                      item.excerpt.toLowerCase().contains(query) ||
                      item.note.toLowerCase().contains(query),
                )
                .toList();
    });
  }

  Future<void> _export() async {
    final archive = await _storage.createAnnotationExport();
    final outputPath = await FilePicker.platform.saveFile(
      dialogTitle: '导出阅读批注',
      fileName: archive.uri.pathSegments.last,
      type: FileType.custom,
      allowedExtensions: const ['zip'],
    );
    if (outputPath == null) return;
    await archive.copy(outputPath);
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('已导出 CSV、Markdown、HTML、TXT、JSON 与附件')),
    );
  }

  Future<void> _exportObsidian() async {
    if (_exportingObsidian || kIsWeb) return;
    final vaultPath = await FilePicker.platform.getDirectoryPath(
      dialogTitle: '选择 Obsidian Vault',
    );
    if (vaultPath == null || !mounted) return;
    final subdirectory = await _askObsidianSubdirectory();
    if (subdirectory == null || !mounted) return;
    setState(() => _exportingObsidian = true);
    try {
      final preferences = await SharedPreferences.getInstance();
      final exporter = ObsidianVaultExporter(
        vaultRoot: Directory(vaultPath),
        subdirectory: subdirectory,
        receiptStore: SharedPreferencesExportReceiptStore(preferences),
      );
      final service = ObsidianAnnotationExportService(exporter);
      final documents = ObsidianAnnotationExportService.documentsFrom(_storage);
      final firstPass = await service.exportDocuments(documents);
      var receipts = [...firstPass.receipts];
      var unresolved = firstPass.conflicts.length;

      if (firstPass.conflicts.isNotEmpty && mounted) {
        final overwrite = await _confirmObsidianOverwrite(firstPass.conflicts);
        if (overwrite == true) {
          final conflictIds = firstPass.conflicts
              .map((conflict) => conflict.document.id)
              .toSet();
          final retry = await service.exportDocuments(
            documents.where(
              (document) => conflictIds.contains(document.identity.id),
            ),
            overwriteExternalChanges: true,
          );
          receipts.addAll(retry.receipts);
          unresolved = retry.conflicts.length;
        }
      }

      if (!mounted) return;
      final changed = receipts
          .where(
            (receipt) => receipt.disposition != ExportDisposition.unchanged,
          )
          .length;
      final unchanged = receipts.length - changed;
      final parts = <String>[
        '已写入 $changed 本',
        if (unchanged > 0) '$unchanged 本无变化',
        if (unresolved > 0) '$unresolved 本保留外部修改',
      ];
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(parts.join('，'))));
    } on Object catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(_friendlyExportError(error))));
    } finally {
      if (mounted) setState(() => _exportingObsidian = false);
    }
  }

  Future<String?> _askObsidianSubdirectory() async {
    final controller = TextEditingController(text: 'OOHStory');
    final result = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('导出到 Vault 子目录'),
        content: TextField(
          controller: controller,
          autofocus: true,
          decoration: const InputDecoration(
            labelText: '相对路径',
            helperText: '例如 OOHStory/阅读批注',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(controller.text),
            child: const Text('继续'),
          ),
        ],
      ),
    );
    controller.dispose();
    return result;
  }

  Future<bool?> _confirmObsidianOverwrite(
    List<ObsidianExportConflict> conflicts,
  ) => showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('检测到 Obsidian 外部修改'),
      content: Text(
        '${conflicts.length} 个文件与上次导出回执不一致。'
        '覆盖前会把当前文件复制到 .oohstory-backups。',
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: const Text('保留外部修改'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(true),
          child: const Text('备份并覆盖'),
        ),
      ],
    ),
  );

  String _friendlyExportError(Object error) {
    if (error is FormatException) return error.message;
    if (error is FileSystemException) {
      return '无法写入 Obsidian Vault，请检查目录权限';
    }
    return 'Obsidian 导出失败，请稍后重试';
  }

  Future<void> _exportNotion({bool configure = false}) async {
    if (_exportingNotion || kIsWeb) return;
    final preferences = await SharedPreferences.getInstance();
    final connection = NotionConnectionRepository(
      preferences: preferences,
      credentialStore: const FlutterSecureCredentialStore(),
    );
    var configuration = connection.loadConfiguration();
    if (configure ||
        configuration == null ||
        !await connection.hasAccessToken()) {
      if (!mounted) return;
      final setup = await _askNotionSetup(
        configuration,
        hasSavedToken: await connection.hasAccessToken(),
      );
      if (setup == null || !mounted) return;
      try {
        configuration = NotionExportConfiguration(
          parentKind: setup.parentKind,
          parentId: setup.parentId,
          titleProperty: setup.titleProperty,
        );
        await connection.save(configuration, accessToken: setup.accessToken);
      } on Object catch (error) {
        if (!mounted) return;
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(_friendlyNotionError(error))));
        return;
      }
    }
    if (!_hasAnnotations || !mounted) return;

    setState(() => _exportingNotion = true);
    try {
      final exporter = NotionAnnotationExporter(
        configuration: configuration,
        api: NotionApiClient(
          transport: PackageHttpTransport(),
          accessToken: connection.requireAccessToken,
        ),
        stateStore: SharedPreferencesNotionExportStateStore(preferences),
      );
      final service = NotionAnnotationExportService(exporter);
      final documents = StoredAnnotationExportSource.documentsFrom(_storage);
      final firstPass = await service.exportDocuments(documents);
      var receipts = [...firstPass.receipts];
      var unresolved = firstPass.conflicts.length;
      if (firstPass.conflicts.isNotEmpty && mounted) {
        final overwrite = await _confirmNotionOverwrite(firstPass.conflicts);
        if (overwrite == true) {
          final conflictIds = firstPass.conflicts
              .map((conflict) => conflict.document.id)
              .toSet();
          final retry = await service.exportDocuments(
            documents.where(
              (document) => conflictIds.contains(document.identity.id),
            ),
            overwriteExternalChanges: true,
          );
          receipts.addAll(retry.receipts);
          unresolved = retry.conflicts.length;
        }
      }
      if (!mounted) return;
      final changed = receipts
          .where(
            (receipt) => receipt.disposition != ExportDisposition.unchanged,
          )
          .length;
      final unchanged = receipts.length - changed;
      final parts = <String>[
        'Notion 已更新 $changed 本',
        if (unchanged > 0) '$unchanged 本无变化',
        if (unresolved > 0) '$unresolved 本保留 Notion 修改',
      ];
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(parts.join('，'))));
    } on Object catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(_friendlyNotionError(error))));
    } finally {
      if (mounted) setState(() => _exportingNotion = false);
    }
  }

  Future<_NotionSetup?> _askNotionSetup(
    NotionExportConfiguration? current, {
    required bool hasSavedToken,
  }) async {
    final idController = TextEditingController(text: current?.parentId ?? '');
    final propertyController = TextEditingController(
      text: current?.parentKind == NotionParentKind.dataSource
          ? current!.titleProperty
          : 'Name',
    );
    final tokenController = TextEditingController();
    var kind = current?.parentKind ?? NotionParentKind.page;
    final result = await showDialog<_NotionSetup>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          title: Text(current == null ? '连接 Notion' : '配置 Notion 导出'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                DropdownButtonFormField<NotionParentKind>(
                  value: kind,
                  decoration: const InputDecoration(labelText: '目标类型'),
                  items: const [
                    DropdownMenuItem(
                      value: NotionParentKind.page,
                      child: Text('页面'),
                    ),
                    DropdownMenuItem(
                      value: NotionParentKind.dataSource,
                      child: Text('数据库 / Data source'),
                    ),
                  ],
                  onChanged: (value) {
                    if (value != null) setDialogState(() => kind = value);
                  },
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: idController,
                  decoration: const InputDecoration(
                    labelText: '目标 ID',
                    helperText: '只访问你明确共享给连接的目标',
                  ),
                ),
                if (kind == NotionParentKind.dataSource) ...[
                  const SizedBox(height: 12),
                  TextField(
                    controller: propertyController,
                    decoration: const InputDecoration(labelText: '标题字段名称'),
                  ),
                ],
                const SizedBox(height: 12),
                TextField(
                  controller: tokenController,
                  obscureText: true,
                  enableSuggestions: false,
                  autocorrect: false,
                  decoration: InputDecoration(
                    labelText: 'Internal connection token',
                    helperText: hasSavedToken
                        ? '留空则保留系统安全存储中的令牌'
                        : '仅保存到系统安全存储',
                  ),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('取消'),
            ),
            FilledButton(
              onPressed: () => Navigator.of(context).pop(
                _NotionSetup(
                  parentKind: kind,
                  parentId: idController.text,
                  titleProperty: propertyController.text,
                  accessToken: tokenController.text.trim().isEmpty
                      ? null
                      : tokenController.text.trim(),
                ),
              ),
              child: const Text('保存并导出'),
            ),
          ],
        ),
      ),
    );
    idController.dispose();
    propertyController.dispose();
    tokenController.dispose();
    return result;
  }

  Future<bool?> _confirmNotionOverwrite(List<NotionExportConflict> conflicts) =>
      showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('检测到 Notion 外部修改'),
          content: Text(
            '${conflicts.length} 个页面在上次导出后被修改。继续会替换页面正文；'
            '包含子页面或数据库时 Notion 会拒绝删除。',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(false),
              child: const Text('保留 Notion 修改'),
            ),
            FilledButton(
              onPressed: () => Navigator.of(context).pop(true),
              child: const Text('确认替换正文'),
            ),
          ],
        ),
      );

  Future<void> _disconnectNotion() async {
    try {
      final preferences = await SharedPreferences.getInstance();
      final connection = NotionConnectionRepository(
        preferences: preferences,
        credentialStore: const FlutterSecureCredentialStore(),
      );
      final configuration = connection.loadConfiguration();
      if (configuration != null) {
        await SharedPreferencesNotionExportStateStore(
          preferences,
        ).removeParent(configuration.parentKey);
      }
      await connection.disconnect();
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('已断开 Notion；远端页面未删除')));
    } on Object {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('无法清除 Notion 本机连接，请稍后重试')));
    }
  }

  String _friendlyNotionError(Object error) {
    if (error is FormatException) return error.message;
    if (error is CoreException) {
      return switch (error.code) {
        CoreErrorCode.unauthorized => 'Notion 授权无效，请重新配置令牌',
        CoreErrorCode.forbidden => 'Notion 连接没有目标页面的写入权限',
        CoreErrorCode.notFound => 'Notion 目标不存在或尚未共享给连接',
        CoreErrorCode.rateLimitExceeded => 'Notion 请求过于频繁，请稍后重试',
        CoreErrorCode.payloadTooLarge => error.message,
        _ => 'Notion 服务暂时不可用，请稍后重试',
      };
    }
    return 'Notion 导出失败，请稍后重试';
  }

  bool get _isDesktop =>
      !kIsWeb && (Platform.isLinux || Platform.isWindows || Platform.isMacOS);

  Future<void> _exportJoplin({bool configure = false}) async {
    if (_exportingJoplin || !_isDesktop) return;
    final preferences = await SharedPreferences.getInstance();
    final connection = JoplinConnectionRepository(
      preferences: preferences,
      credentialStore: const FlutterSecureCredentialStore(),
    );
    var configuration = connection.loadConfiguration();
    if (configure || configuration == null || !await connection.hasToken()) {
      if (!mounted) return;
      final setup = await _askJoplinSetup(
        configuration,
        connection: connection,
        hasSavedToken: await connection.hasToken(),
      );
      if (setup == null || !mounted) return;
      setState(() => _exportingJoplin = true);
      try {
        configuration = JoplinExportConfiguration(
          port: setup.port,
          notebookId: setup.notebookId,
        );
        final candidateToken = setup.token ?? await connection.requireToken();
        final verifier = JoplinApiClient(
          configuration: configuration,
          transport: PackageHttpTransport(),
          token: () async => candidateToken,
        );
        await verifier.probe();
        await verifier.verifyNotebook();
        await connection.save(configuration, token: setup.token);
      } on Object catch (error) {
        if (!mounted) return;
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(_friendlyJoplinError(error))));
        return;
      } finally {
        if (mounted) setState(() => _exportingJoplin = false);
      }
    }
    if (!_hasAnnotations || !mounted) return;

    setState(() => _exportingJoplin = true);
    try {
      final exporter = JoplinAnnotationExporter(
        configuration: configuration,
        api: JoplinApiClient(
          configuration: configuration,
          transport: PackageHttpTransport(),
          token: connection.requireToken,
        ),
        stateStore: SharedPreferencesJoplinExportStateStore(preferences),
      );
      final service = JoplinAnnotationExportService(exporter);
      final documents =
          await StoredAnnotationExportSource.documentsWithAttachmentsFrom(
            _storage,
          );
      final firstPass = await service.exportDocuments(documents);
      var receipts = [...firstPass.receipts];
      var unresolved = firstPass.conflicts.length;
      final overwritable = firstPass.conflicts
          .where((conflict) => conflict.canOverwrite)
          .toList(growable: false);
      if (overwritable.isNotEmpty && mounted) {
        final overwrite = await _confirmJoplinOverwrite(overwritable);
        if (overwrite == true) {
          final conflictIds = overwritable
              .map((conflict) => conflict.document.id)
              .toSet();
          final retry = await service.exportDocuments(
            documents.where(
              (document) => conflictIds.contains(document.identity.id),
            ),
            overwriteExternalChanges: true,
          );
          receipts.addAll(retry.receipts);
          unresolved =
              firstPass.conflicts.length -
              overwritable.length +
              retry.conflicts.length;
        }
      }
      if (!mounted) return;
      final changed = receipts
          .where(
            (receipt) => receipt.disposition != ExportDisposition.unchanged,
          )
          .length;
      final unchanged = receipts.length - changed;
      final parts = <String>[
        'Joplin 已更新 $changed 本',
        if (unchanged > 0) '$unchanged 本无变化',
        if (unresolved > 0) '$unresolved 本保留 Joplin 修改',
      ];
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(parts.join('，'))));
    } on Object catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(_friendlyJoplinError(error))));
    } finally {
      if (mounted) setState(() => _exportingJoplin = false);
    }
  }

  Future<_JoplinSetup?> _askJoplinSetup(
    JoplinExportConfiguration? current, {
    required JoplinConnectionRepository connection,
    required bool hasSavedToken,
  }) => showDialog<_JoplinSetup>(
    context: context,
    barrierDismissible: false,
    builder: (context) => _JoplinSetupDialog(
      current: current,
      connection: connection,
      hasSavedToken: hasSavedToken,
      errorMessage: _friendlyJoplinError,
    ),
  );

  Future<bool?> _confirmJoplinOverwrite(List<JoplinExportConflict> conflicts) =>
      showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('检测到 Joplin 外部修改'),
          content: Text(
            '${conflicts.length} 个笔记在上次导出后被修改或删除。'
            '继续会用当前 OOHStory 批注替换或重建这些笔记。',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(false),
              child: const Text('保留 Joplin 修改'),
            ),
            FilledButton(
              onPressed: () => Navigator.of(context).pop(true),
              child: const Text('确认替换'),
            ),
          ],
        ),
      );

  Future<void> _disconnectJoplin() async {
    try {
      final preferences = await SharedPreferences.getInstance();
      final connection = JoplinConnectionRepository(
        preferences: preferences,
        credentialStore: const FlutterSecureCredentialStore(),
      );
      final configuration = connection.loadConfiguration();
      if (configuration != null) {
        await SharedPreferencesJoplinExportStateStore(
          preferences,
        ).removeTarget(configuration.targetKey);
      }
      await connection.disconnect();
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('已断开 Joplin；远端笔记与标签未删除')));
    } on Object {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('无法清除 Joplin 本机连接，请稍后重试')));
    }
  }

  String _friendlyJoplinError(Object error) {
    if (error is FormatException) return error.message;
    if (error is CoreException) {
      return switch (error.code) {
        CoreErrorCode.unauthorized ||
        CoreErrorCode.forbidden => 'Joplin 授权无效，请重新配置 Data API token',
        CoreErrorCode.notFound => 'Joplin 目标笔记本不存在',
        CoreErrorCode.revisionConflict => error.message,
        CoreErrorCode.payloadTooLarge => error.message,
        _ => '无法连接 Joplin 桌面版，请确认 Web Clipper 已开启',
      };
    }
    return 'Joplin 导出失败，请稍后重试';
  }

  Future<void> _attachFile(OfflineAnnotation annotation) async {
    try {
      final attachment = await pickAnnotationAttachment(
        storage: _storage,
        annotation: annotation,
      );
      if (attachment == null) return;
      if (!mounted) return;
      _reload();
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('已添加附件：${attachment.fileName}')));
    } on Object catch (error) {
      if (!mounted) return;
      final message = error is FormatException
          ? error.message
          : '无法保存附件，请检查文件权限';
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(message)));
    }
  }

  Future<void> _showAttachments(OfflineAnnotation annotation) async {
    await showAnnotationAttachmentsDialog(
      context: context,
      storage: _storage,
      annotation: annotation,
    );
    if (mounted) _reload();
  }

  Future<void> _deleteAnnotation(OfflineAnnotation annotation) async {
    try {
      await _storage.removeAnnotation(annotation.id);
      if (mounted) _reload();
    } on Object {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('无法删除批注，请稍后重试')));
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('书签与批注'),
      actions: [
        if (widget.capabilities.obsidianExportEnabled && !kIsWeb)
          IconButton(
            onPressed: !_hasAnnotations || _exportingObsidian
                ? null
                : _exportObsidian,
            tooltip: '导出到 Obsidian',
            icon: _exportingObsidian
                ? const SizedBox.square(
                    dimension: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.folder_copy_outlined),
          ),
        if (widget.capabilities.notionExportEnabled && !kIsWeb)
          PopupMenuButton<_NotionAction>(
            tooltip: 'Notion 导出',
            enabled: !_exportingNotion,
            icon: _exportingNotion
                ? const SizedBox.square(
                    dimension: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.cloud_upload_outlined),
            onSelected: (action) {
              switch (action) {
                case _NotionAction.export:
                  _exportNotion();
                case _NotionAction.configure:
                  _exportNotion(configure: true);
                case _NotionAction.disconnect:
                  _disconnectNotion();
              }
            },
            itemBuilder: (context) => <PopupMenuEntry<_NotionAction>>[
              PopupMenuItem(
                value: _NotionAction.export,
                enabled: _hasAnnotations,
                child: const Text('立即导出'),
              ),
              const PopupMenuItem(
                value: _NotionAction.configure,
                child: Text('配置连接'),
              ),
              const PopupMenuItem(
                value: _NotionAction.disconnect,
                child: Text('断开连接'),
              ),
            ],
          ),
        if (widget.capabilities.joplinExportEnabled && _isDesktop)
          PopupMenuButton<_JoplinAction>(
            tooltip: 'Joplin 导出',
            enabled: !_exportingJoplin,
            icon: _exportingJoplin
                ? const SizedBox.square(
                    dimension: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.note_alt_outlined),
            onSelected: (action) {
              switch (action) {
                case _JoplinAction.export:
                  _exportJoplin();
                case _JoplinAction.configure:
                  _exportJoplin(configure: true);
                case _JoplinAction.disconnect:
                  _disconnectJoplin();
              }
            },
            itemBuilder: (context) => <PopupMenuEntry<_JoplinAction>>[
              PopupMenuItem(
                value: _JoplinAction.export,
                enabled: _hasAnnotations,
                child: const Text('立即导出'),
              ),
              const PopupMenuItem(
                value: _JoplinAction.configure,
                child: Text('配置桌面连接'),
              ),
              const PopupMenuItem(
                value: _JoplinAction.disconnect,
                child: Text('断开连接'),
              ),
            ],
          ),
        IconButton(
          onPressed: _hasAnnotations ? _export : null,
          tooltip: '导出',
          icon: const Icon(Icons.ios_share_rounded),
        ),
      ],
    ),
    body: _loading
        ? const Center(child: CircularProgressIndicator())
        : Column(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
                child: TextField(
                  controller: _search,
                  onChanged: (_) => _reload(),
                  decoration: const InputDecoration(
                    hintText: '搜索高亮、书签与笔记',
                    prefixIcon: Icon(Icons.search_rounded),
                  ),
                ),
              ),
              Expanded(
                child: _items.isEmpty
                    ? const Center(child: Text('还没有离线批注'))
                    : ListView.separated(
                        padding: const EdgeInsets.fromLTRB(12, 0, 12, 24),
                        itemCount: _items.length,
                        separatorBuilder: (_, __) => const Divider(height: 1),
                        itemBuilder: (context, index) {
                          final item = _items[index];
                          final attachments = _storage.getAnnotationAttachments(
                            annotationId: item.id,
                          );
                          return ListTile(
                            leading: CircleAvatar(
                              child: Icon(switch (item.type) {
                                'note' => Icons.edit_note_rounded,
                                'highlight' => Icons.highlight_rounded,
                                _ => Icons.bookmark_rounded,
                              }),
                            ),
                            title: Text(
                              item.note.isNotEmpty ? item.note : item.excerpt,
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                            ),
                            subtitle: Text(
                              '${(item.progress * 100).round()}% · ${item.excerpt}'
                              '${attachments.isEmpty ? '' : ' · ${attachments.length} 个附件'}',
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                            trailing: PopupMenuButton<_AnnotationAction>(
                              tooltip: '管理批注',
                              onSelected: (action) {
                                switch (action) {
                                  case _AnnotationAction.attach:
                                    _attachFile(item);
                                  case _AnnotationAction.attachments:
                                    _showAttachments(item);
                                  case _AnnotationAction.delete:
                                    _deleteAnnotation(item);
                                }
                              },
                              itemBuilder: (context) => [
                                if (!kIsWeb)
                                  const PopupMenuItem(
                                    value: _AnnotationAction.attach,
                                    child: Text('添加附件'),
                                  ),
                                if (attachments.isNotEmpty)
                                  PopupMenuItem(
                                    value: _AnnotationAction.attachments,
                                    child: Text('管理附件 (${attachments.length})'),
                                  ),
                                const PopupMenuItem(
                                  value: _AnnotationAction.delete,
                                  child: Text('删除批注'),
                                ),
                              ],
                            ),
                          );
                        },
                      ),
              ),
            ],
          ),
  );

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }
}

enum _NotionAction { export, configure, disconnect }

enum _JoplinAction { export, configure, disconnect }

enum _AnnotationAction { attach, attachments, delete }

class _NotionSetup {
  const _NotionSetup({
    required this.parentKind,
    required this.parentId,
    required this.titleProperty,
    required this.accessToken,
  });

  final NotionParentKind parentKind;
  final String parentId;
  final String titleProperty;
  final String? accessToken;
}

class _JoplinSetup {
  const _JoplinSetup({
    required this.port,
    required this.notebookId,
    required this.token,
  });

  final int port;
  final String notebookId;
  final String? token;
}

class _JoplinSetupDialog extends StatefulWidget {
  const _JoplinSetupDialog({
    required this.current,
    required this.connection,
    required this.hasSavedToken,
    required this.errorMessage,
  });

  final JoplinExportConfiguration? current;
  final JoplinConnectionRepository connection;
  final bool hasSavedToken;
  final String Function(Object error) errorMessage;

  @override
  State<_JoplinSetupDialog> createState() => _JoplinSetupDialogState();
}

class _JoplinSetupDialogState extends State<_JoplinSetupDialog> {
  late final TextEditingController _portController;
  final _tokenController = TextEditingController();
  List<JoplinNotebook> _notebooks = const <JoplinNotebook>[];
  String? _selectedNotebookId;
  String? _error;
  var _loading = false;
  var _findingPort = false;

  bool get _busy => _loading || _findingPort;

  @override
  void initState() {
    super.initState();
    _portController = TextEditingController(
      text: '${widget.current?.port ?? 41184}',
    );
  }

  void _invalidateDiscovery(String _) {
    if (_notebooks.isEmpty && _error == null) return;
    setState(() {
      _notebooks = const <JoplinNotebook>[];
      _selectedNotebookId = null;
      _error = null;
    });
  }

  Future<void> _findPort() async {
    if (_busy) return;
    setState(() {
      _findingPort = true;
      _error = null;
      _notebooks = const <JoplinNotebook>[];
      _selectedNotebookId = null;
    });
    try {
      final port = await discoverJoplinPort(transport: PackageHttpTransport());
      if (port == null) {
        throw const FormatException(
          '未找到 Joplin Web Clipper，请确认 Joplin 已启动并开启 Web Clipper 服务',
        );
      }
      if (!mounted) return;
      setState(() {
        _findingPort = false;
        _portController.text = '$port';
      });
    } on Object catch (error) {
      if (!mounted) return;
      setState(() {
        _findingPort = false;
        _error = widget.errorMessage(error);
      });
    }
  }

  Future<void> _discover() async {
    if (_busy) return;
    final port = int.tryParse(_portController.text.trim());
    if (port == null) {
      setState(() => _error = '请输入有效的 Web Clipper 端口');
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
      _notebooks = const <JoplinNotebook>[];
      _selectedNotebookId = null;
    });
    try {
      final enteredToken = _tokenController.text.trim();
      final token = enteredToken.isEmpty
          ? await widget.connection.requireToken()
          : validateJoplinToken(enteredToken);
      final client = JoplinApiClient.connection(
        port: port,
        transport: PackageHttpTransport(),
        token: () async => token,
      );
      await client.probe();
      final notebooks = await client.listNotebooks();
      if (notebooks.isEmpty) {
        throw const FormatException('Joplin 中没有可用笔记本，请先创建一个笔记本');
      }
      if (!mounted) return;
      final currentId = widget.current?.port == port
          ? widget.current?.notebookId
          : null;
      final selected = notebooks.any((item) => item.id == currentId)
          ? currentId
          : notebooks.first.id;
      setState(() {
        _loading = false;
        _notebooks = notebooks;
        _selectedNotebookId = selected;
      });
    } on Object catch (error) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = widget.errorMessage(error);
      });
    }
  }

  void _submit() {
    final port = int.tryParse(_portController.text.trim());
    final notebookId = _selectedNotebookId;
    if (port == null || notebookId == null || _busy) return;
    final token = _tokenController.text.trim();
    Navigator.of(context).pop(
      _JoplinSetup(
        port: port,
        notebookId: notebookId,
        token: token.isEmpty ? null : token,
      ),
    );
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !_busy,
    child: AlertDialog(
      title: Text(widget.current == null ? '连接 Joplin 桌面版' : '配置 Joplin 导出'),
      content: SizedBox(
        width: 520,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(
                controller: _portController,
                enabled: !_busy,
                keyboardType: TextInputType.number,
                onChanged: _invalidateDiscovery,
                decoration: const InputDecoration(
                  labelText: 'Web Clipper 端口',
                  helperText: 'Joplin 设置 → Web Clipper；通常为 41184',
                ),
              ),
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  onPressed: _busy ? null : _findPort,
                  icon: _findingPort
                      ? const SizedBox.square(
                          dimension: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.search_rounded),
                  label: Text(
                    _findingPort ? '正在查找本机 Joplin…' : '自动查找本机 Joplin',
                  ),
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _tokenController,
                enabled: !_busy,
                obscureText: true,
                enableSuggestions: false,
                autocorrect: false,
                onChanged: _invalidateDiscovery,
                decoration: InputDecoration(
                  labelText: 'Data API token',
                  helperText: widget.hasSavedToken
                      ? '留空则使用系统安全存储中的令牌'
                      : '从 Joplin Web Clipper 设置复制；仅保存到系统安全存储',
                ),
              ),
              const SizedBox(height: 12),
              OutlinedButton.icon(
                onPressed: _busy ? null : _discover,
                icon: _loading
                    ? const SizedBox.square(
                        dimension: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.refresh_rounded),
                label: Text(_loading ? '正在验证并读取…' : '验证并读取笔记本'),
              ),
              if (_error != null) ...[
                const SizedBox(height: 10),
                Text(
                  _error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ],
              if (_notebooks.isNotEmpty) ...[
                const SizedBox(height: 12),
                DropdownButtonFormField<String>(
                  value: _selectedNotebookId,
                  isExpanded: true,
                  decoration: const InputDecoration(labelText: '目标笔记本'),
                  items: _notebooks
                      .map(
                        (notebook) => DropdownMenuItem<String>(
                          value: notebook.id,
                          child: Text(
                            notebook.path,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      )
                      .toList(growable: false),
                  onChanged: _busy
                      ? null
                      : (value) => setState(() => _selectedNotebookId = value),
                ),
                const SizedBox(height: 6),
                Text(
                  '已读取 ${_notebooks.length} 个笔记本；只读取标题与层级。',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: _busy ? null : () => Navigator.of(context).pop(),
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed: _selectedNotebookId == null || _busy ? null : _submit,
          child: const Text('保存并导出'),
        ),
      ],
    ),
  );

  @override
  void dispose() {
    _portController.dispose();
    _tokenController.dispose();
    super.dispose();
  }
}
