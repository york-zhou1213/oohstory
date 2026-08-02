import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../services/local_storage_service.dart';
import '../services/reading_progress.dart';
import '../theme/app_theme.dart';
import 'book_detail_screen.dart';

class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key});

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  final _storage = LocalStorageService();
  final _progress = ReadingProgressService();
  final _api = ApiService();
  bool _initialized = false;

  List<HistoryEntry> _history = [];
  List<BookMeta> _favorites = [];
  List<DownloadedBookInfo> _downloads = [];
  int _totalDownloadSize = 0;

  @override
  void initState() {
    super.initState();
    _init();
  }

  Future<void> _init() async {
    await _storage.init();
    await _progress.init();
    _refresh();
    setState(() => _initialized = true);
  }

  void _refresh() {
    setState(() {
      _history = _storage.getHistory();
      _favorites = _storage.getFavorites();
      _downloads = _storage.getDownloadedBooks();
    });
    _storage.totalDownloadSize().then((size) {
      if (mounted) setState(() => _totalDownloadSize = size);
    });
  }

  void _openBook(String bookId) {
    Navigator.of(context)
        .push(MaterialPageRoute(builder: (_) => BookDetailScreen(bookId: bookId)))
        .then((_) => _refresh());
  }

  String _formatSize(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
    return '${(bytes / 1024 / 1024).toStringAsFixed(1)} MB';
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

  @override
  Widget build(BuildContext context) {
    if (!_initialized) return const Center(child: CircularProgressIndicator());
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;

    return RefreshIndicator(
      onRefresh: () async => _refresh(),
      child: CustomScrollView(
        slivers: [
          SliverToBoxAdapter(child: _buildHeader(theme, isDark)),
          SliverToBoxAdapter(child: _buildStatsRow(theme, isDark)),
          if (_history.isNotEmpty) ...[
            _buildSectionHeader(theme, '继续阅读', Icons.menu_book_rounded, onSeeAll: () => _showHistorySheet(context)),
            SliverToBoxAdapter(child: _buildReadingShelf(theme, isDark)),
          ],
          if (_favorites.isNotEmpty) ...[
            _buildSectionHeader(theme, '我的收藏', Icons.favorite_rounded, count: _favorites.length, onSeeAll: () => _showFavoritesSheet(context)),
            SliverToBoxAdapter(child: _buildFavoritesGrid(theme, isDark)),
          ],
          if (_downloads.isNotEmpty) ...[
            _buildSectionHeader(theme, '离线下载', Icons.download_done_rounded, count: _downloads.fold<int>(0, (s, d) => s + d.chapters.length)),
            SliverToBoxAdapter(child: _buildDownloadsList(theme, isDark)),
          ],
          SliverToBoxAdapter(child: _buildSettingsSection(theme, isDark)),
          if (_history.isEmpty && _favorites.isEmpty && _downloads.isEmpty)
            SliverToBoxAdapter(child: _buildEmptyState(theme)),
          const SliverToBoxAdapter(child: SizedBox(height: 100)),
        ],
      ),
    );
  }

  Widget _buildHeader(ThemeData theme, bool isDark) {
    return Container(
      margin: const EdgeInsets.fromLTRB(16, 8, 16, 0),
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xFF6C5CE7), Color(0xFF8B7CF6), Color(0xFFA29BFE)],
        ),
        borderRadius: BorderRadius.circular(20),
        boxShadow: [
          BoxShadow(
            color: AppTheme.seedPurple.withValues(alpha: 0.3),
            blurRadius: 20,
            offset: const Offset(0, 8),
          ),
        ],
      ),
      child: Row(
        children: [
          Container(
            width: 56, height: 56,
            decoration: BoxDecoration(
              color: Colors.white.withValues(alpha: 0.2),
              shape: BoxShape.circle,
              border: Border.all(color: Colors.white.withValues(alpha: 0.3), width: 2),
            ),
            child: const Icon(Icons.person_rounded, color: Colors.white, size: 28),
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  '我的书房',
                  style: TextStyle(fontSize: 20, fontWeight: FontWeight.w800, color: Colors.white, letterSpacing: -0.3),
                ),
                const SizedBox(height: 4),
                Text(
                  '阅读让世界更有趣',
                  style: TextStyle(fontSize: 13, color: Colors.white.withValues(alpha: 0.7)),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildStatsRow(ThemeData theme, bool isDark) {
    final cardColor = isDark ? const Color(0xFF1E1E30) : Colors.white;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
      child: Row(
        children: [
          _statCard(theme, cardColor, '${_history.length}', '已读', Icons.auto_stories, const Color(0xFF6C5CE7)),
          const SizedBox(width: 10),
          _statCard(theme, cardColor, '${_favorites.length}', '收藏', Icons.favorite, const Color(0xFFFD79A8)),
          const SizedBox(width: 10),
          _statCard(theme, cardColor, '${_downloads.fold(0, (int s, d) => s + d.chapters.length)}', '下载', Icons.download_done, const Color(0xFF00B894)),
        ],
      ),
    );
  }

  Widget _statCard(ThemeData theme, Color cardColor, String value, String label, IconData icon, Color accent) {
    return Expanded(
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 16),
        decoration: BoxDecoration(
          color: cardColor,
          borderRadius: BorderRadius.circular(14),
          boxShadow: [
            BoxShadow(color: Colors.black.withValues(alpha: 0.04), blurRadius: 8, offset: const Offset(0, 2)),
          ],
        ),
        child: Column(
          children: [
            Icon(icon, color: accent, size: 22),
            const SizedBox(height: 6),
            Text(value, style: TextStyle(fontSize: 20, fontWeight: FontWeight.w800, color: accent)),
            const SizedBox(height: 2),
            Text(label, style: TextStyle(fontSize: 11, color: theme.colorScheme.onSurface.withValues(alpha: 0.5))),
          ],
        ),
      ),
    );
  }

  SliverToBoxAdapter _buildSectionHeader(ThemeData theme, String title, IconData icon, {int? count, VoidCallback? onSeeAll}) {
    return SliverToBoxAdapter(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 20, 16, 8),
        child: Row(
          children: [
            Icon(icon, size: 18, color: AppTheme.seedPurple),
            const SizedBox(width: 8),
            Text(title, style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w700, fontSize: 15)),
            if (count != null) ...[
              const SizedBox(width: 6),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 1),
                decoration: BoxDecoration(
                  color: AppTheme.seedPurple.withValues(alpha: 0.1),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text('$count', style: TextStyle(fontSize: 11, color: AppTheme.seedPurple, fontWeight: FontWeight.w600)),
              ),
            ],
            const Spacer(),
            if (onSeeAll != null)
              TextButton(
                onPressed: onSeeAll,
                style: TextButton.styleFrom(padding: const EdgeInsets.symmetric(horizontal: 8), minimumSize: const Size(0, 32)),
                child: Text('查看全部', style: TextStyle(fontSize: 12, color: AppTheme.seedPurple)),
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildReadingShelf(ThemeData theme, bool isDark) {
    final items = _history.take(10).toList();
    return SizedBox(
      height: 180,
      child: ListView.builder(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 16),
        itemCount: items.length,
        itemBuilder: (context, i) {
          final entry = items[i];
          return GestureDetector(
            onTap: () => _openBook(entry.book.id),
            child: Container(
              width: 110,
              margin: EdgeInsets.only(right: i < items.length - 1 ? 12 : 0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    height: 130,
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(10),
                      boxShadow: [
                        BoxShadow(color: Colors.black.withValues(alpha: 0.1), blurRadius: 8, offset: const Offset(0, 3)),
                      ],
                    ),
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(10),
                      child: Stack(
                        fit: StackFit.expand,
                        children: [
                          Image.network(
                            _api.coverUrl(entry.book.id),
                            fit: BoxFit.cover,
                            errorBuilder: (_, __, ___) => _fallbackCover(entry.book.title, isDark),
                          ),
                          Positioned(
                            bottom: 0, left: 0, right: 0,
                            child: Container(
                              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
                              decoration: BoxDecoration(
                                gradient: LinearGradient(
                                  begin: Alignment.bottomCenter,
                                  end: Alignment.topCenter,
                                  colors: [Colors.black.withValues(alpha: 0.7), Colors.transparent],
                                ),
                              ),
                              child: Text(
                                _formatTime(entry.lastReadAt),
                                style: const TextStyle(fontSize: 9, color: Colors.white70),
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    entry.book.title,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodySmall?.copyWith(fontWeight: FontWeight.w600, fontSize: 12),
                  ),
                  Text(
                    entry.lastChapterTitle,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 10, color: theme.colorScheme.onSurface.withValues(alpha: 0.4)),
                  ),
                ],
              ),
            ),
          );
        },
      ),
    );
  }

  Widget _buildFavoritesGrid(ThemeData theme, bool isDark) {
    final items = _favorites.take(6).toList();
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: GridView.builder(
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
          crossAxisCount: 3,
          mainAxisSpacing: 12,
          crossAxisSpacing: 12,
          childAspectRatio: 0.62,
        ),
        itemCount: items.length,
        itemBuilder: (context, i) {
          final fav = items[i];
          return GestureDetector(
            onTap: () => _openBook(fav.id),
            onLongPress: () => _showRemoveFavoriteDialog(fav),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Container(
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(10),
                      boxShadow: [
                        BoxShadow(color: Colors.black.withValues(alpha: 0.08), blurRadius: 6, offset: const Offset(0, 2)),
                      ],
                    ),
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(10),
                      child: Image.network(
                        _api.coverUrl(fav.id),
                        fit: BoxFit.cover,
                        width: double.infinity,
                        errorBuilder: (_, __, ___) => _fallbackCover(fav.title, isDark),
                      ),
                    ),
                  ),
                ),
                const SizedBox(height: 6),
                Text(
                  fav.title,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodySmall?.copyWith(fontWeight: FontWeight.w600, fontSize: 12),
                ),
                Text(
                  fav.author,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 10, color: theme.colorScheme.onSurface.withValues(alpha: 0.4)),
                ),
              ],
            ),
          );
        },
      ),
    );
  }

  Widget _buildDownloadsList(ThemeData theme, bool isDark) {
    final cardColor = isDark ? const Color(0xFF1E1E30) : Colors.white;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Column(
        children: _downloads.map((dl) {
          return Container(
            margin: const EdgeInsets.only(bottom: 8),
            decoration: BoxDecoration(
              color: cardColor,
              borderRadius: BorderRadius.circular(12),
              boxShadow: [BoxShadow(color: Colors.black.withValues(alpha: 0.04), blurRadius: 6, offset: const Offset(0, 2))],
            ),
            child: ListTile(
              contentPadding: const EdgeInsets.fromLTRB(12, 4, 8, 4),
              leading: ClipRRect(
                borderRadius: BorderRadius.circular(6),
                child: Image.network(
                  _api.coverUrl(dl.book.id),
                  width: 40, height: 54,
                  fit: BoxFit.cover,
                  errorBuilder: (_, __, ___) => Container(
                    width: 40, height: 54,
                    color: AppTheme.seedPurple.withValues(alpha: 0.1),
                    child: const Icon(Icons.book, size: 20, color: AppTheme.seedPurple),
                  ),
                ),
              ),
              title: Text(dl.book.title, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
              subtitle: Text(
                '${dl.chapters.length}章 · ${_formatSize(dl.totalSize)}',
                style: TextStyle(fontSize: 12, color: theme.colorScheme.onSurface.withValues(alpha: 0.5)),
              ),
              trailing: IconButton(
                icon: Icon(Icons.delete_outline, size: 20, color: theme.colorScheme.onSurface.withValues(alpha: 0.4)),
                onPressed: () => _showDeleteDownloadDialog(dl),
              ),
              onTap: () => _openBook(dl.book.id),
            ),
          );
        }).toList(),
      ),
    );
  }

  Widget _buildSettingsSection(ThemeData theme, bool isDark) {
    final cardColor = isDark ? const Color(0xFF1E1E30) : Colors.white;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 20, 16, 0),
      child: Container(
        decoration: BoxDecoration(
          color: cardColor,
          borderRadius: BorderRadius.circular(14),
          boxShadow: [BoxShadow(color: Colors.black.withValues(alpha: 0.04), blurRadius: 6, offset: const Offset(0, 2))],
        ),
        child: Column(
          children: [
            _settingsTile(theme, Icons.cleaning_services_rounded, '清除缓存', subtitle: _formatSize(_totalDownloadSize), onTap: _clearCache),
            Divider(height: 1, indent: 52, color: theme.colorScheme.onSurface.withValues(alpha: 0.06)),
            _settingsTile(theme, Icons.history, '清除阅读历史', onTap: _clearHistoryConfirm),
            Divider(height: 1, indent: 52, color: theme.colorScheme.onSurface.withValues(alpha: 0.06)),
            _settingsTile(theme, Icons.info_outline_rounded, '关于', subtitle: 'OohStory v1.1.0', onTap: _showAbout),
          ],
        ),
      ),
    );
  }

  Widget _settingsTile(ThemeData theme, IconData icon, String title, {String? subtitle, VoidCallback? onTap}) {
    return ListTile(
      leading: Icon(icon, size: 20, color: AppTheme.seedPurple),
      title: Text(title, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500)),
      subtitle: subtitle != null
          ? Text(subtitle, style: TextStyle(fontSize: 12, color: theme.colorScheme.onSurface.withValues(alpha: 0.4)))
          : null,
      trailing: Icon(Icons.chevron_right, size: 18, color: theme.colorScheme.onSurface.withValues(alpha: 0.3)),
      onTap: onTap,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
    );
  }

  Widget _buildEmptyState(ThemeData theme) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 60),
      child: Column(
        children: [
          Icon(Icons.auto_stories_outlined, size: 64, color: theme.colorScheme.onSurface.withValues(alpha: 0.15)),
          const SizedBox(height: 16),
          Text(
            '还没有阅读记录',
            style: TextStyle(fontSize: 15, color: theme.colorScheme.onSurface.withValues(alpha: 0.4)),
          ),
          const SizedBox(height: 8),
          Text(
            '去书库逛逛，开始你的阅读之旅',
            style: TextStyle(fontSize: 13, color: theme.colorScheme.onSurface.withValues(alpha: 0.3)),
          ),
        ],
      ),
    );
  }

  Widget _fallbackCover(String title, bool isDark) {
    return Container(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: isDark
              ? [const Color(0xFF2A2A40), const Color(0xFF1E1E30)]
              : [const Color(0xFFE8E4FF), const Color(0xFFD4CFFF)],
        ),
      ),
      child: Center(
        child: Text(
          title.length > 2 ? title.substring(0, 2) : title,
          style: TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.w700,
            color: isDark ? Colors.white70 : AppTheme.seedPurple,
          ),
          textAlign: TextAlign.center,
        ),
      ),
    );
  }

  void _showRemoveFavoriteDialog(BookMeta book) {
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

  void _showDeleteDownloadDialog(DownloadedBookInfo dl) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('删除下载'),
        content: Text('确定要删除「${dl.book.title}」的${dl.chapters.length}个已下载章节吗？'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
          FilledButton(
            onPressed: () async {
              await _storage.deleteDownloadedBook(dl.book.id);
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

  void _showHistorySheet(BuildContext context) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: 0.7,
        maxChildSize: 0.9,
        builder: (ctx, scrollCtrl) {
          final theme = Theme.of(ctx);
          return Column(
            children: [
              Padding(
                padding: const EdgeInsets.all(16),
                child: Row(
                  children: [
                    Text('阅读历史', style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700)),
                    const Spacer(),
                    TextButton(
                      onPressed: () {
                        Navigator.pop(ctx);
                        _clearHistoryConfirm();
                      },
                      child: Text('清空', style: TextStyle(color: Colors.red.shade400, fontSize: 13)),
                    ),
                  ],
                ),
              ),
              Expanded(
                child: ListView.builder(
                  controller: scrollCtrl,
                  itemCount: _history.length,
                  itemBuilder: (ctx, i) {
                    final entry = _history[i];
                    return Dismissible(
                      key: Key(entry.book.id),
                      direction: DismissDirection.endToStart,
                      background: Container(
                        alignment: Alignment.centerRight,
                        padding: const EdgeInsets.only(right: 20),
                        color: Colors.red.shade400,
                        child: const Icon(Icons.delete, color: Colors.white),
                      ),
                      onDismissed: (_) {
                        _storage.removeFromHistory(entry.book.id);
                        _refresh();
                      },
                      child: ListTile(
                        leading: ClipRRect(
                          borderRadius: BorderRadius.circular(6),
                          child: Image.network(
                            _api.coverUrl(entry.book.id),
                            width: 40, height: 54,
                            fit: BoxFit.cover,
                            errorBuilder: (_, __, ___) => Container(
                              width: 40, height: 54,
                              color: AppTheme.seedPurple.withValues(alpha: 0.1),
                              child: const Icon(Icons.book, size: 20),
                            ),
                          ),
                        ),
                        title: Text(entry.book.title, maxLines: 1, overflow: TextOverflow.ellipsis),
                        subtitle: Text(
                          '读到: ${entry.lastChapterTitle} · ${_formatTime(entry.lastReadAt)}',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(fontSize: 12, color: theme.colorScheme.onSurface.withValues(alpha: 0.5)),
                        ),
                        onTap: () {
                          Navigator.pop(ctx);
                          _openBook(entry.book.id);
                        },
                      ),
                    );
                  },
                ),
              ),
            ],
          );
        },
      ),
    );
  }

  void _showFavoritesSheet(BuildContext context) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: 0.7,
        maxChildSize: 0.9,
        builder: (ctx, scrollCtrl) {
          final theme = Theme.of(ctx);
          return Column(
            children: [
              Padding(
                padding: const EdgeInsets.all(16),
                child: Text('我的收藏', style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700)),
              ),
              Expanded(
                child: ListView.builder(
                  controller: scrollCtrl,
                  itemCount: _favorites.length,
                  itemBuilder: (ctx, i) {
                    final fav = _favorites[i];
                    return Dismissible(
                      key: Key(fav.id),
                      direction: DismissDirection.endToStart,
                      background: Container(
                        alignment: Alignment.centerRight,
                        padding: const EdgeInsets.only(right: 20),
                        color: Colors.red.shade400,
                        child: const Icon(Icons.delete, color: Colors.white),
                      ),
                      onDismissed: (_) {
                        _storage.removeFavorite(fav.id);
                        _refresh();
                      },
                      child: ListTile(
                        leading: ClipRRect(
                          borderRadius: BorderRadius.circular(6),
                          child: Image.network(
                            _api.coverUrl(fav.id),
                            width: 40, height: 54,
                            fit: BoxFit.cover,
                            errorBuilder: (_, __, ___) => Container(
                              width: 40, height: 54,
                              color: AppTheme.seedPurple.withValues(alpha: 0.1),
                              child: const Icon(Icons.book, size: 20),
                            ),
                          ),
                        ),
                        title: Text(fav.title, maxLines: 1, overflow: TextOverflow.ellipsis),
                        subtitle: Text(fav.author, style: TextStyle(fontSize: 12, color: theme.colorScheme.onSurface.withValues(alpha: 0.5))),
                        onTap: () {
                          Navigator.pop(ctx);
                          _openBook(fav.id);
                        },
                      ),
                    );
                  },
                ),
              ),
            ],
          );
        },
      ),
    );
  }

  void _clearCache() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('清除缓存'),
        content: Text('将删除所有已下载的章节内容（${_formatSize(_totalDownloadSize)}）。收藏和阅读记录不受影响。'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('清除')),
        ],
      ),
    );
    if (confirmed == true) {
      await _storage.clearAllDownloads();
      _refresh();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('缓存已清除'), behavior: SnackBarBehavior.floating),
        );
      }
    }
  }

  void _clearHistoryConfirm() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('清除阅读历史'),
        content: const Text('确定要清除所有阅读历史吗？'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
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

  void _showAbout() {
    showAboutDialog(
      context: context,
      applicationName: 'OohStory',
      applicationVersion: 'v1.1.0',
      applicationIcon: Container(
        width: 48, height: 48,
        decoration: BoxDecoration(
          gradient: const LinearGradient(colors: [Color(0xFF6C5CE7), Color(0xFFA29BFE)]),
          borderRadius: BorderRadius.circular(12),
        ),
        child: const Icon(Icons.auto_stories, color: Colors.white, size: 24),
      ),
      children: [
        const Text('免费中文小说阅读与深度拆书'),
        const SizedBox(height: 8),
        Text('开源 · 免费 · 自托管', style: TextStyle(color: Colors.grey.shade500, fontSize: 13)),
      ],
    );
  }

  @override
  void dispose() {
    _api.dispose();
    super.dispose();
  }
}
