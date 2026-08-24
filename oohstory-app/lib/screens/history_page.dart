import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../services/local_storage_service.dart';
import '../theme/app_theme.dart';
import 'book_detail_screen.dart';

class HistoryPage extends StatefulWidget {
  const HistoryPage({super.key});

  @override
  State<HistoryPage> createState() => _HistoryPageState();
}

class _HistoryPageState extends State<HistoryPage> {
  final _storage = LocalStorageService();
  final _api = ApiService();
  List<HistoryEntry> _history = [];
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
    setState(() => _history = _storage.getHistory());
  }

  String _formatTime(int timestamp) {
    final dt = DateTime.fromMillisecondsSinceEpoch(timestamp);
    final now = DateTime.now();
    final diff = now.difference(dt);
    if (diff.inMinutes < 1) return '刚刚';
    if (diff.inHours < 1) return '${diff.inMinutes}分钟前';
    if (diff.inDays < 1) return '${diff.inHours}小时前';
    if (diff.inDays < 7) return '${diff.inDays}天前';
    return '${dt.month}月${dt.day}日';
  }

  void _openBook(String bookId) {
    Navigator.of(context)
        .push(
          MaterialPageRoute(builder: (_) => BookDetailScreen(bookId: bookId)),
        )
        .then((_) => _refresh());
  }

  void _clearAll() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('清除阅读历史'),
        content: const Text('确定要清除所有阅读历史吗？'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            style: FilledButton.styleFrom(backgroundColor: Colors.red.shade400),
            child: const Text('清除'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      _storage.clearHistory();
      _refresh();
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return Scaffold(
      appBar: AppBar(
        title: Text(
          '阅读历史',
          style: TextStyle(fontWeight: FontWeight.w700, fontSize: 17),
        ),
        actions: [
          if (_history.isNotEmpty)
            TextButton(
              onPressed: _clearAll,
              child: Text(
                '清空',
                style: TextStyle(color: Colors.red.shade400, fontSize: 13),
              ),
            ),
        ],
      ),
      body: !_initialized
          ? const Center(child: CircularProgressIndicator())
          : _history.isEmpty
          ? Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    Icons.history,
                    size: 48,
                    color: theme.colorScheme.onSurface.withValues(alpha: 0.15),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    '还没有阅读记录',
                    style: TextStyle(
                      fontSize: 15,
                      color: theme.colorScheme.onSurface.withValues(alpha: 0.4),
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    '去书库逛逛吧',
                    style: TextStyle(
                      fontSize: 12,
                      color: theme.colorScheme.onSurface.withValues(
                        alpha: 0.25,
                      ),
                    ),
                  ),
                ],
              ),
            )
          : ListView.builder(
              padding: const EdgeInsets.all(16),
              itemCount: _history.length,
              itemBuilder: (context, i) {
                final entry = _history[i];
                final cardColor = isDark
                    ? const Color(0xFF1E1E30)
                    : Colors.white;
                return Dismissible(
                  key: Key(entry.book.id),
                  direction: DismissDirection.endToStart,
                  background: Container(
                    alignment: Alignment.centerRight,
                    padding: const EdgeInsets.only(right: 20),
                    margin: const EdgeInsets.only(bottom: 10),
                    decoration: BoxDecoration(
                      color: Colors.red.shade400,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: const Icon(Icons.delete, color: Colors.white),
                  ),
                  onDismissed: (_) {
                    _storage.removeFromHistory(entry.book.id);
                    _refresh();
                  },
                  child: Container(
                    margin: const EdgeInsets.only(bottom: 10),
                    decoration: BoxDecoration(
                      color: cardColor,
                      borderRadius: BorderRadius.circular(12),
                      boxShadow: [
                        BoxShadow(
                          color: Colors.black.withValues(alpha: 0.04),
                          blurRadius: 6,
                          offset: const Offset(0, 2),
                        ),
                      ],
                    ),
                    child: ListTile(
                      contentPadding: const EdgeInsets.fromLTRB(12, 6, 12, 6),
                      leading: ClipRRect(
                        borderRadius: BorderRadius.circular(6),
                        child: Image.network(
                          _api.coverUrl(entry.book.id),
                          width: 44,
                          height: 60,
                          fit: BoxFit.cover,
                          errorBuilder: (_, __, ___) => Container(
                            width: 44,
                            height: 60,
                            color: AppTheme.seedPurple.withValues(alpha: 0.1),
                            child: const Icon(
                              Icons.book,
                              size: 20,
                              color: AppTheme.seedPurple,
                            ),
                          ),
                        ),
                      ),
                      title: Text(
                        entry.book.title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontWeight: FontWeight.w600,
                          fontSize: 14,
                        ),
                      ),
                      subtitle: Text(
                        '读到: ${entry.lastChapterTitle} · ${_formatTime(entry.lastReadAt)}',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontSize: 12,
                          color: theme.colorScheme.onSurface.withValues(
                            alpha: 0.5,
                          ),
                        ),
                      ),
                      onTap: () => _openBook(entry.book.id),
                    ),
                  ),
                );
              },
            ),
    );
  }

  @override
  void dispose() {
    _api.dispose();
    super.dispose();
  }
}
