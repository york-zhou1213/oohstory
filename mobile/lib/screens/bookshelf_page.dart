import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import '../services/local_storage_service.dart';
import '../services/offline_book_parser.dart';
import '../theme/app_theme.dart';
import 'offline_book_screen.dart';
import 'opds_catalog_screen.dart';

class BookshelfPage extends StatefulWidget {
  final bool embedded;

  const BookshelfPage({super.key, this.embedded = false});

  @override
  State<BookshelfPage> createState() => _BookshelfPageState();
}

class _BookshelfPageState extends State<BookshelfPage> {
  final _storage = LocalStorageService();
  final _searchController = TextEditingController();
  List<LocalBookInfo> _books = [];
  String _query = '';
  bool _initialized = false;
  bool _importing = false;

  @override
  void initState() {
    super.initState();
    _init();
  }

  Future<void> _init() async {
    await _storage.init();
    _refresh();
    setState(() => _initialized = true);
  }

  void _refresh() {
    setState(() {
      _books = _query.isEmpty
          ? _storage.getLocalBooks()
          : _storage.searchLocalBooks(_query);
    });
  }

  String _formatSize(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
    return '${(bytes / 1024 / 1024).toStringAsFixed(1)} MB';
  }

  Future<void> _import() async {
    final result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: OfflineBookParser.supportedExtensions.toList(),
      allowMultiple: true,
    );
    if (result == null || result.files.isEmpty) return;
    final files = result.files
        .where((file) => file.path != null)
        .map((file) => (path: file.path!, name: file.name))
        .toList();
    if (files.isEmpty) return;
    setState(() => _importing = true);
    try {
      final imported = await _storage.importLocalBooks(files);
      _refresh();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('已导入 ${imported.length} 本书，可断网阅读'),
            behavior: SnackBarBehavior.floating,
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('导入失败: $e'),
            behavior: SnackBarBehavior.floating,
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _importing = false);
    }
  }

  Future<void> _backup() async {
    try {
      final backup = await _storage.createOfflineBackup();
      final outputPath = await FilePicker.platform.saveFile(
        dialogTitle: '保存 OOHStory 离线备份',
        fileName: backup.uri.pathSegments.last,
        type: FileType.custom,
        allowedExtensions: const ['zip'],
      );
      if (outputPath == null) return;
      await backup.copy(outputPath);
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('离线书籍、批注、进度和设置已备份')));
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('备份失败：$error')));
    }
  }

  Future<void> _restore() async {
    final result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: const ['zip'],
    );
    final filePath = result?.files.single.path;
    if (filePath == null) return;
    try {
      if (_storage.getLocalBooks().isNotEmpty) {
        await _storage.createOfflineSnapshot();
      }
      await _storage.restoreOfflineBackup(filePath);
      _refresh();
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('离线备份已校验并恢复')));
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('恢复失败：$error')));
    }
  }

  Future<void> _openOpds() async {
    await Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => OpdsCatalogScreen(storage: _storage)),
    );
    _refresh();
  }

  void _handleTool(String value) {
    if (value == 'backup') _backup();
    if (value == 'restore') _restore();
    if (value == 'snapshots') _showSnapshots();
    if (value == 'opds') _openOpds();
  }

  Widget _toolMenu() => PopupMenuButton<String>(
    tooltip: '离线书库工具',
    onSelected: _handleTool,
    itemBuilder: (_) => const [
      PopupMenuItem(value: 'opds', child: Text('OPDS / 局域网书库')),
      PopupMenuItem(value: 'snapshots', child: Text('离线历史快照')),
      PopupMenuItem(value: 'backup', child: Text('备份离线书库')),
      PopupMenuItem(value: 'restore', child: Text('恢复离线备份')),
    ],
  );

  Widget _importButton() => IconButton(
    tooltip: '导入本地书籍',
    onPressed: _importing ? null : _import,
    icon: _importing
        ? const SizedBox.square(
            dimension: 20,
            child: CircularProgressIndicator(strokeWidth: 2),
          )
        : const Icon(Icons.add_rounded),
  );

  Future<void> _showSnapshots() async {
    await showDialog<void>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          title: const Text('离线历史快照'),
          content: SizedBox(
            width: 520,
            child: FutureBuilder<List<OfflineSnapshotInfo>>(
              future: _storage.listOfflineSnapshots(),
              builder: (context, snapshot) {
                if (!snapshot.hasData) {
                  return const SizedBox(
                    height: 120,
                    child: Center(child: CircularProgressIndicator()),
                  );
                }
                final items = snapshot.data!;
                if (items.isEmpty) {
                  return const SizedBox(
                    height: 120,
                    child: Center(child: Text('还没有快照')),
                  );
                }
                return ListView.separated(
                  shrinkWrap: true,
                  itemCount: items.length,
                  separatorBuilder: (_, __) => const Divider(height: 1),
                  itemBuilder: (context, index) {
                    final item = items[index];
                    final date = DateTime.fromMillisecondsSinceEpoch(
                      item.createdAt,
                    );
                    return ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: const Icon(Icons.history_rounded),
                      title: Text(
                        '${date.month.toString().padLeft(2, '0')}-${date.day.toString().padLeft(2, '0')} '
                        '${date.hour.toString().padLeft(2, '0')}:${date.minute.toString().padLeft(2, '0')}',
                      ),
                      subtitle: Text(_formatSize(item.size)),
                      onTap: () async {
                        if (_storage.getLocalBooks().isNotEmpty) {
                          await _storage.createOfflineSnapshot(
                            preservePath: item.path,
                          );
                        }
                        await _storage.restoreOfflineBackup(item.path);
                        if (!mounted || !dialogContext.mounted) return;
                        Navigator.pop(dialogContext);
                        _refresh();
                        ScaffoldMessenger.of(this.context).showSnackBar(
                          const SnackBar(content: Text('快照已校验并恢复，恢复前状态也已保留')),
                        );
                      },
                      trailing: IconButton(
                        tooltip: '删除快照',
                        onPressed: () async {
                          await _storage.deleteOfflineSnapshot(item.path);
                          setDialogState(() {});
                        },
                        icon: const Icon(Icons.delete_outline_rounded),
                      ),
                    );
                  },
                );
              },
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogContext),
              child: const Text('关闭'),
            ),
            FilledButton.icon(
              onPressed: () async {
                await _storage.createOfflineSnapshot();
                setDialogState(() {});
              },
              icon: const Icon(Icons.add_rounded),
              label: const Text('创建快照'),
            ),
          ],
        ),
      ),
    );
  }

  void _delete(LocalBookInfo book) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('删除书籍'),
        content: Text('确定要从书架中删除「${book.title}」吗？'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () async {
              await _storage.deleteLocalBook(book.id);
              _refresh();
              if (!ctx.mounted) return;
              Navigator.pop(ctx);
            },
            style: FilledButton.styleFrom(backgroundColor: Colors.red.shade400),
            child: const Text('删除'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    final body = !_initialized
        ? const Center(child: CircularProgressIndicator())
        : LayoutBuilder(
            builder: (context, constraints) {
              final columns = constraints.maxWidth >= 900
                  ? 5
                  : constraints.maxWidth >= 600
                  ? 4
                  : 2;
              return CustomScrollView(
                slivers: [
                  SliverToBoxAdapter(
                    child: Padding(
                      padding: const EdgeInsets.fromLTRB(16, 8, 16, 18),
                      child: Row(
                        children: [
                          Expanded(
                            child: TextField(
                              controller: _searchController,
                              onChanged: (value) {
                                _query = value.trim();
                                _refresh();
                              },
                              decoration: InputDecoration(
                                hintText: '搜索本地书名、作者或文件名',
                                prefixIcon: const Icon(Icons.search_rounded),
                                suffixIcon: _query.isEmpty
                                    ? null
                                    : IconButton(
                                        onPressed: () {
                                          _searchController.clear();
                                          _query = '';
                                          _refresh();
                                        },
                                        icon: const Icon(Icons.close_rounded),
                                      ),
                              ),
                            ),
                          ),
                          if (widget.embedded) ...[
                            const SizedBox(width: 4),
                            _toolMenu(),
                            _importButton(),
                          ],
                        ],
                      ),
                    ),
                  ),
                  if (_books.isEmpty)
                    SliverFillRemaining(
                      hasScrollBody: false,
                      child: _EmptyOfflineLibrary(
                        hasQuery: _query.isNotEmpty,
                        onImport: _import,
                      ),
                    )
                  else
                    SliverPadding(
                      padding: const EdgeInsets.fromLTRB(16, 0, 16, 28),
                      sliver: SliverGrid.builder(
                        gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                          crossAxisCount: columns,
                          crossAxisSpacing: 14,
                          mainAxisSpacing: 18,
                          childAspectRatio: .62,
                        ),
                        itemCount: _books.length,
                        itemBuilder: (context, index) => _OfflineBookTile(
                          book: _books[index],
                          index: index,
                          isDark: isDark,
                          formatSize: _formatSize,
                          onDelete: () => _delete(_books[index]),
                          onOpen: () => Navigator.of(context)
                              .push(
                                MaterialPageRoute(
                                  builder: (_) =>
                                      buildOfflineBookScreen(_books[index]),
                                ),
                              )
                              .then((_) => _refresh()),
                        ),
                      ),
                    ),
                ],
              );
            },
          );

    if (widget.embedded) return body;
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          '我的书架',
          style: TextStyle(fontWeight: FontWeight.w700, fontSize: 17),
        ),
        actions: [_toolMenu(), _importButton()],
      ),
      body: body,
    );
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }
}

class _EmptyOfflineLibrary extends StatelessWidget {
  final bool hasQuery;
  final VoidCallback onImport;

  const _EmptyOfflineLibrary({required this.hasQuery, required this.onImport});

  @override
  Widget build(BuildContext context) => Center(
    child: Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(
          Icons.auto_stories_outlined,
          size: 54,
          color: Theme.of(context).colorScheme.primary.withValues(alpha: .28),
        ),
        const SizedBox(height: 16),
        Text(
          hasQuery ? '没有匹配的离线书籍' : '把故事带在身边',
          style: Theme.of(
            context,
          ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w800),
        ),
        const SizedBox(height: 8),
        Text(
          hasQuery ? '试试别的书名或作者' : '支持 TXT、EPUB、PDF、CBZ、DOCX、FB2、MD 与 HTML',
          style: Theme.of(context).textTheme.bodyMedium,
        ),
        if (!hasQuery) ...[
          const SizedBox(height: 20),
          FilledButton.icon(
            onPressed: onImport,
            icon: const Icon(Icons.add_rounded),
            label: const Text('批量导入'),
          ),
        ],
      ],
    ),
  );
}

class _OfflineBookTile extends StatelessWidget {
  final LocalBookInfo book;
  final int index;
  final bool isDark;
  final String Function(int bytes) formatSize;
  final VoidCallback onDelete;
  final VoidCallback onOpen;

  const _OfflineBookTile({
    required this.book,
    required this.index,
    required this.isDark,
    required this.formatSize,
    required this.onDelete,
    required this.onOpen,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return InkWell(
      onTap: onOpen,
      borderRadius: BorderRadius.circular(18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Stack(
              children: [
                Container(
                  width: double.infinity,
                  decoration: BoxDecoration(
                    gradient: LinearGradient(
                      begin: Alignment.topLeft,
                      end: Alignment.bottomRight,
                      colors: [
                        Color.lerp(
                          AppTheme.brandNavy,
                          AppTheme.seedPurple,
                          (index % 5) / 6,
                        )!,
                        Color.lerp(
                          AppTheme.sky,
                          const Color(0xFF0B132B),
                          (index % 4) / 5,
                        )!,
                      ],
                    ),
                    borderRadius: BorderRadius.circular(18),
                    boxShadow: [
                      BoxShadow(
                        color: Colors.black.withValues(
                          alpha: isDark ? .24 : .12,
                        ),
                        blurRadius: 18,
                        offset: const Offset(0, 8),
                      ),
                    ],
                  ),
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        book.format.toUpperCase(),
                        style: TextStyle(
                          color: Colors.white.withValues(alpha: .66),
                          fontSize: 10,
                          fontWeight: FontWeight.w800,
                          letterSpacing: 1.2,
                        ),
                      ),
                      const Spacer(),
                      Text(
                        book.title,
                        maxLines: 4,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          color: Colors.white,
                          fontWeight: FontWeight.w900,
                          fontSize: 18,
                          height: 1.22,
                        ),
                      ),
                      if (book.author.isNotEmpty) ...[
                        const SizedBox(height: 8),
                        Text(
                          book.author,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            color: Colors.white.withValues(alpha: .72),
                            fontSize: 12,
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
                Positioned(
                  top: 6,
                  right: 6,
                  child: IconButton.filledTonal(
                    onPressed: onDelete,
                    icon: const Icon(Icons.more_horiz_rounded, size: 18),
                    style: IconButton.styleFrom(
                      backgroundColor: Colors.black.withValues(alpha: .22),
                      foregroundColor: Colors.white,
                    ),
                  ),
                ),
                if (book.progress > 0)
                  Positioned(
                    left: 0,
                    right: 0,
                    bottom: 0,
                    child: ClipRRect(
                      borderRadius: const BorderRadius.vertical(
                        bottom: Radius.circular(18),
                      ),
                      child: LinearProgressIndicator(
                        value: book.progress,
                        minHeight: 4,
                        backgroundColor: Colors.white24,
                      ),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 10),
          Text(
            book.title,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 3),
          Text(
            '${formatSize(book.fileSize)} · ${book.wordCount > 0 ? '${book.wordCount} 字' : book.format.toUpperCase()}',
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
        ],
      ),
    );
  }
}
