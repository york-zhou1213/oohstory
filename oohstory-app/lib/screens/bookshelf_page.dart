import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import '../services/local_storage_service.dart';
import '../theme/app_theme.dart';
import 'local_reader_screen.dart';

class BookshelfPage extends StatefulWidget {
  const BookshelfPage({super.key});

  @override
  State<BookshelfPage> createState() => _BookshelfPageState();
}

class _BookshelfPageState extends State<BookshelfPage> {
  final _storage = LocalStorageService();
  List<LocalBookInfo> _books = [];
  bool _initialized = false;

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
    setState(() => _books = _storage.getLocalBooks());
  }

  String _formatSize(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
    return '${(bytes / 1024 / 1024).toStringAsFixed(1)} MB';
  }

  Future<void> _import() async {
    final result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['txt'],
      allowMultiple: false,
    );
    if (result == null || result.files.isEmpty) return;
    final file = result.files.first;
    if (file.path == null) return;
    try {
      await _storage.importLocalBook(file.path!, file.name);
      _refresh();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('已导入「${file.name.replaceAll(RegExp(r'\.(txt|TXT)$'), '')}」'), behavior: SnackBarBehavior.floating),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('导入失败: $e'), behavior: SnackBarBehavior.floating),
        );
      }
    }
  }

  void _delete(LocalBookInfo book) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('删除书籍'),
        content: Text('确定要从书架中删除「${book.title}」吗？'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
          FilledButton(
            onPressed: () async {
              await _storage.deleteLocalBook(book.id);
              _refresh();
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

    return Scaffold(
      appBar: AppBar(
        title: Text('我的书架', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 17)),
        actions: [
          IconButton(onPressed: _import, icon: const Icon(Icons.add_rounded)),
        ],
      ),
      body: !_initialized
          ? const Center(child: CircularProgressIndicator())
          : _books.isEmpty
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.shelves, size: 48, color: theme.colorScheme.onSurface.withValues(alpha: 0.15)),
                      const SizedBox(height: 12),
                      Text('书架空空如也', style: TextStyle(fontSize: 15, color: theme.colorScheme.onSurface.withValues(alpha: 0.4))),
                      const SizedBox(height: 16),
                      FilledButton.icon(
                        onPressed: _import,
                        icon: const Icon(Icons.add, size: 18),
                        label: const Text('导入 TXT 小说'),
                      ),
                    ],
                  ),
                )
              : ListView.builder(
                  padding: const EdgeInsets.all(16),
                  itemCount: _books.length,
                  itemBuilder: (context, i) {
                    final book = _books[i];
                    final cardColor = isDark ? const Color(0xFF1E1E30) : Colors.white;
                    return Container(
                      margin: const EdgeInsets.only(bottom: 10),
                      decoration: BoxDecoration(
                        color: cardColor,
                        borderRadius: BorderRadius.circular(12),
                        boxShadow: [BoxShadow(color: Colors.black.withValues(alpha: 0.04), blurRadius: 6, offset: const Offset(0, 2))],
                      ),
                      child: ListTile(
                        contentPadding: const EdgeInsets.fromLTRB(12, 6, 8, 6),
                        leading: Container(
                          width: 44, height: 60,
                          decoration: BoxDecoration(
                            gradient: LinearGradient(
                              begin: Alignment.topLeft, end: Alignment.bottomRight,
                              colors: [
                                Color.lerp(AppTheme.seedPurple, Colors.blue, (i * 0.15) % 1.0) ?? AppTheme.seedPurple,
                                Color.lerp(const Color(0xFFA29BFE), Colors.teal, (i * 0.2) % 1.0) ?? const Color(0xFFA29BFE),
                              ],
                            ),
                            borderRadius: BorderRadius.circular(6),
                          ),
                          child: Center(
                            child: Text(
                              book.title.length > 2 ? book.title.substring(0, 2) : book.title,
                              style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: Colors.white),
                            ),
                          ),
                        ),
                        title: Text(book.title, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
                        subtitle: Text(
                          '${_formatSize(book.fileSize)} · TXT',
                          style: TextStyle(fontSize: 12, color: theme.colorScheme.onSurface.withValues(alpha: 0.5)),
                        ),
                        trailing: IconButton(
                          icon: Icon(Icons.delete_outline, size: 20, color: theme.colorScheme.onSurface.withValues(alpha: 0.4)),
                          onPressed: () => _delete(book),
                        ),
                        onTap: () => Navigator.of(context).push(
                          MaterialPageRoute(builder: (_) => LocalReaderScreen(book: book)),
                        ).then((_) => _refresh()),
                      ),
                    );
                  },
                ),
    );
  }
}
