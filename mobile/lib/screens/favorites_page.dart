import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../services/local_storage_service.dart';
import '../theme/app_theme.dart';
import 'book_detail_screen.dart';

class FavoritesPage extends StatefulWidget {
  const FavoritesPage({super.key});

  @override
  State<FavoritesPage> createState() => _FavoritesPageState();
}

class _FavoritesPageState extends State<FavoritesPage> {
  final _storage = LocalStorageService();
  final _api = ApiService();
  List<BookMeta> _favorites = [];
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
    setState(() => _favorites = _storage.getFavorites());
  }

  void _openBook(String bookId) {
    Navigator.of(context)
        .push(MaterialPageRoute(builder: (_) => BookDetailScreen(bookId: bookId)))
        .then((_) => _refresh());
  }

  void _remove(BookMeta book) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('取消收藏'),
        content: Text('确定要取消收藏「${book.title}」吗？'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
          FilledButton(
            onPressed: () {
              _storage.removeFavorite(book.id);
              _refresh();
              Navigator.pop(ctx);
            },
            child: const Text('确定'),
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
        title: Text('我的收藏', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 17)),
      ),
      body: !_initialized
          ? const Center(child: CircularProgressIndicator())
          : _favorites.isEmpty
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.favorite_outline, size: 48, color: theme.colorScheme.onSurface.withValues(alpha: 0.15)),
                      const SizedBox(height: 12),
                      Text('还没有收藏', style: TextStyle(fontSize: 15, color: theme.colorScheme.onSurface.withValues(alpha: 0.4))),
                      const SizedBox(height: 4),
                      Text('浏览书籍时点击收藏按钮', style: TextStyle(fontSize: 12, color: theme.colorScheme.onSurface.withValues(alpha: 0.25))),
                    ],
                  ),
                )
              : GridView.builder(
                  padding: const EdgeInsets.all(16),
                  gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                    crossAxisCount: 3,
                    mainAxisSpacing: 14,
                    crossAxisSpacing: 12,
                    childAspectRatio: 0.56,
                  ),
                  itemCount: _favorites.length,
                  itemBuilder: (context, i) {
                    final fav = _favorites[i];
                    return GestureDetector(
                      onTap: () => _openBook(fav.id),
                      onLongPress: () => _remove(fav),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Expanded(
                            child: Container(
                              decoration: BoxDecoration(
                                borderRadius: BorderRadius.circular(10),
                                boxShadow: [BoxShadow(color: Colors.black.withValues(alpha: 0.08), blurRadius: 6, offset: const Offset(0, 2))],
                              ),
                              child: ClipRRect(
                                borderRadius: BorderRadius.circular(10),
                                child: Image.network(
                                  _api.coverUrl(fav.id),
                                  fit: BoxFit.cover,
                                  width: double.infinity,
                                  errorBuilder: (_, __, ___) => Container(
                                    decoration: BoxDecoration(
                                      gradient: LinearGradient(
                                        begin: Alignment.topLeft, end: Alignment.bottomRight,
                                        colors: isDark
                                            ? [const Color(0xFF2A2A40), const Color(0xFF1E1E30)]
                                            : [const Color(0xFFE8E4FF), const Color(0xFFD4CFFF)],
                                      ),
                                    ),
                                    child: Center(
                                      child: Text(
                                        fav.title.length > 2 ? fav.title.substring(0, 2) : fav.title,
                                        style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: isDark ? Colors.white70 : AppTheme.seedPurple),
                                      ),
                                    ),
                                  ),
                                ),
                              ),
                            ),
                          ),
                          const SizedBox(height: 6),
                          Text(fav.title, maxLines: 1, overflow: TextOverflow.ellipsis, style: theme.textTheme.bodySmall?.copyWith(fontWeight: FontWeight.w600, fontSize: 12)),
                          Text(fav.author, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 10, color: theme.colorScheme.onSurface.withValues(alpha: 0.4))),
                        ],
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
