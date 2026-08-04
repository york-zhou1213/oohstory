import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import '../services/api_service.dart';
import '../services/local_storage_service.dart';
import '../services/reading_progress.dart';
import '../services/account_service.dart';
import '../theme/app_theme.dart';
import 'book_detail_screen.dart';
import 'local_reader_screen.dart';
import 'history_page.dart';
import 'favorites_page.dart';
import 'auth_screen.dart';
import 'user_uploads_screen.dart';

class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key});

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  final _storage = LocalStorageService();
  final _progress = ReadingProgressService();
  final _api = ApiService();
  final _account = AccountService.instance;
  bool _initialized = false;

  List<HistoryEntry> _history = [];
  List<BookMeta> _favorites = [];
  List<DownloadedBookInfo> _downloads = [];
  List<LocalBookInfo> _localBooks = [];
  int _totalDownloadSize = 0;

  @override
  void initState() {
    super.initState();
    _account.addListener(_accountChanged);
    _init();
  }

  @override
  void dispose() {
    _account.removeListener(_accountChanged);
    _api.dispose();
    super.dispose();
  }

  void _accountChanged() {
    if (mounted) setState(() {});
  }

  Future<void> _init() async {
    await _storage.init();
    await _progress.init();
    await _account.initialize();
    if (_account.isSignedIn) await _account.mergeLocalState(_storage);
    _refresh();
    setState(() => _initialized = true);
  }

  void _refresh() {
    setState(() {
      _history = _storage.getHistory();
      _favorites = _storage.getFavorites();
      _downloads = _storage.getDownloadedBooks();
      _localBooks = _storage.getLocalBooks();
    });
    _storage.totalDownloadSize().then((size) {
      if (mounted) setState(() => _totalDownloadSize = size);
    });
  }

  void _openBook(String bookId) {
    Navigator.of(context)
        .push(
          MaterialPageRoute(builder: (_) => BookDetailScreen(bookId: bookId)),
        )
        .then((_) => _refresh());
  }

  void _openLocalBook(LocalBookInfo book) {
    Navigator.of(
      context,
    ).push(MaterialPageRoute(builder: (_) => LocalReaderScreen(book: book)));
  }

  Future<void> _openAuth() async {
    final changed = await Navigator.of(
      context,
    ).push<bool>(MaterialPageRoute(builder: (_) => const AuthScreen()));
    if (changed == true) {
      await _account.mergeLocalState(_storage);
      _refresh();
    }
  }

  Future<void> _linkGoogle() async {
    try {
      await _account.linkGoogle();
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Google 账户绑定成功，今后可直接使用 Google 登录')),
      );
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(error.toString())));
    }
  }

  void _openCloudBookshelf() {
    final books = _account.cloudState['bookshelf'] as List? ?? const [];
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (context) => SafeArea(
        child: SizedBox(
          height: MediaQuery.sizeOf(context).height * .7,
          child: Column(
            children: [
              const Padding(
                padding: EdgeInsets.fromLTRB(20, 4, 20, 14),
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: Text(
                    '云端私人书架',
                    style: TextStyle(fontSize: 20, fontWeight: FontWeight.w800),
                  ),
                ),
              ),
              Expanded(
                child: books.isEmpty
                    ? const Center(child: Text('还没有加入云端书架的作品'))
                    : ListView.builder(
                        itemCount: books.length,
                        itemBuilder: (context, index) {
                          final book = Map<String, dynamic>.from(
                            books[index] as Map,
                          );
                          return ListTile(
                            leading: const CircleAvatar(
                              child: Icon(Icons.book_outlined),
                            ),
                            title: Text(book['title'] as String? ?? ''),
                            subtitle: Text(book['author'] as String? ?? ''),
                            trailing: const Icon(Icons.chevron_right),
                            onTap: () {
                              Navigator.pop(context);
                              _openBook(book['book_id'] as String);
                            },
                          );
                        },
                      ),
              ),
            ],
          ),
        ),
      ),
    );
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

  Future<void> _importLocalBook() async {
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
          SnackBar(
            content: Text(
              '已导入「${file.name.replaceAll(RegExp(r'\.(txt|TXT)$'), '')}」',
            ),
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
    }
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

          // Local source files intentionally remain device-only.
          _buildSectionHeader(
            theme,
            '本地书架',
            Icons.library_books_rounded,
            count: _localBooks.length,
            action: _importLocalBook,
            actionLabel: '导入',
            actionIcon: Icons.add_rounded,
          ),
          SliverToBoxAdapter(child: _buildBookshelf(theme, isDark)),

          // 继续阅读
          _buildSectionHeader(
            theme,
            '继续阅读',
            Icons.menu_book_rounded,
            count: _history.length,
            onSeeAll: _history.isNotEmpty
                ? () => _showHistorySheet(context)
                : null,
          ),
          SliverToBoxAdapter(
            child: _history.isNotEmpty
                ? _buildReadingShelf(theme, isDark)
                : _emptyHint(theme, '还没有阅读记录', '去书库逛逛吧'),
          ),

          // 我的收藏
          _buildSectionHeader(
            theme,
            '我的收藏',
            Icons.favorite_rounded,
            count: _favorites.length,
            onSeeAll: _favorites.isNotEmpty
                ? () => _showFavoritesSheet(context)
                : null,
          ),
          SliverToBoxAdapter(
            child: _favorites.isNotEmpty
                ? _buildFavoritesGrid(theme, isDark)
                : _emptyHint(theme, '还没有收藏', '浏览书籍时点击收藏按钮'),
          ),

          // 离线下载
          _buildSectionHeader(
            theme,
            '离线下载',
            Icons.download_done_rounded,
            count: _downloads.fold<int>(0, (s, d) => s + d.chapters.length),
          ),
          SliverToBoxAdapter(
            child: _downloads.isNotEmpty
                ? _buildDownloadsList(theme, isDark)
                : _emptyHint(theme, '还没有下载', '在书籍详情页点击下载按钮'),
          ),

          SliverToBoxAdapter(child: _buildSettingsSection(theme, isDark)),
          const SliverToBoxAdapter(child: SizedBox(height: 100)),
        ],
      ),
    );
  }

  Widget _emptyHint(ThemeData theme, String title, String subtitle) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 4, 20, 8),
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 24),
        decoration: BoxDecoration(
          color: theme.brightness == Brightness.dark
              ? const Color(0xFF1E1E30)
              : const Color(0xFFF8F8FC),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(
          children: [
            Icon(
              Icons.inbox_outlined,
              size: 28,
              color: theme.colorScheme.onSurface.withValues(alpha: 0.15),
            ),
            const SizedBox(height: 8),
            Text(
              title,
              style: TextStyle(
                fontSize: 13,
                color: theme.colorScheme.onSurface.withValues(alpha: 0.4),
              ),
            ),
            const SizedBox(height: 2),
            Text(
              subtitle,
              style: TextStyle(
                fontSize: 11,
                color: theme.colorScheme.onSurface.withValues(alpha: 0.25),
              ),
            ),
          ],
        ),
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
            width: 56,
            height: 56,
            decoration: BoxDecoration(
              color: Colors.white.withValues(alpha: 0.2),
              shape: BoxShape.circle,
              border: Border.all(
                color: Colors.white.withValues(alpha: 0.3),
                width: 2,
              ),
            ),
            child: const Icon(
              Icons.person_rounded,
              color: Colors.white,
              size: 28,
            ),
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  _account.user?.displayName ?? '我的书房',
                  style: TextStyle(
                    fontSize: 20,
                    fontWeight: FontWeight.w800,
                    color: Colors.white,
                    letterSpacing: -0.3,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  _account.user == null
                      ? '登录后同步三端阅读记录'
                      : '${_account.user!.email}${_account.user!.emailVerified ? ' · 已验证' : ' · 待验证'}${_account.user!.googleLinked ? ' · Google 已绑定' : ''}',
                  style: TextStyle(
                    fontSize: 13,
                    color: Colors.white.withValues(alpha: 0.7),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          if (_account.user == null)
            FilledButton.tonal(
              onPressed: _openAuth,
              style: FilledButton.styleFrom(
                backgroundColor: Colors.white.withValues(alpha: .18),
                foregroundColor: Colors.white,
              ),
              child: const Text('登录'),
            )
          else
            PopupMenuButton<String>(
              color: theme.colorScheme.surface,
              icon: const Icon(Icons.more_horiz_rounded, color: Colors.white),
              onSelected: (value) async {
                if (value == 'uploads') {
                  await Navigator.of(context).push(
                    MaterialPageRoute(
                      builder: (_) => const UserUploadsScreen(),
                    ),
                  );
                } else if (value == 'google-link') {
                  await _linkGoogle();
                } else if (value == 'logout') {
                  await _account.logout();
                  _refresh();
                }
              },
              itemBuilder: (_) => [
                const PopupMenuItem(value: 'uploads', child: Text('拆书上传记录')),
                if (!_account.user!.googleLinked)
                  PopupMenuItem(
                    value: 'google-link',
                    enabled: _account.googleAvailable,
                    child: Text(
                      _account.googleAvailable
                          ? '绑定 Google 账户'
                          : 'Google 绑定待配置',
                    ),
                  ),
                const PopupMenuItem(value: 'logout', child: Text('退出登录')),
              ],
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
          _statCard(
            theme,
            cardColor,
            '${_history.length}',
            '已读',
            Icons.auto_stories,
            const Color(0xFF6C5CE7),
            onTap: () => Navigator.of(context)
                .push(MaterialPageRoute(builder: (_) => const HistoryPage()))
                .then((_) => _refresh()),
          ),
          const SizedBox(width: 10),
          _statCard(
            theme,
            cardColor,
            '${_favorites.length}',
            '收藏',
            Icons.favorite,
            const Color(0xFFFD79A8),
            onTap: () => Navigator.of(context)
                .push(MaterialPageRoute(builder: (_) => const FavoritesPage()))
                .then((_) => _refresh()),
          ),
          const SizedBox(width: 10),
          _statCard(
            theme,
            cardColor,
            '${(_account.cloudState['bookshelf'] as List? ?? const []).length}',
            '云书架',
            Icons.shelves,
            const Color(0xFF00B894),
            onTap: _account.isSignedIn ? _openCloudBookshelf : _openAuth,
          ),
        ],
      ),
    );
  }

  Widget _statCard(
    ThemeData theme,
    Color cardColor,
    String value,
    String label,
    IconData icon,
    Color accent, {
    VoidCallback? onTap,
  }) {
    return Expanded(
      child: GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 16),
          decoration: BoxDecoration(
            color: cardColor,
            borderRadius: BorderRadius.circular(14),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withValues(alpha: 0.04),
                blurRadius: 8,
                offset: const Offset(0, 2),
              ),
            ],
          ),
          child: Column(
            children: [
              Icon(icon, color: accent, size: 22),
              const SizedBox(height: 6),
              Text(
                value,
                style: TextStyle(
                  fontSize: 20,
                  fontWeight: FontWeight.w800,
                  color: accent,
                ),
              ),
              const SizedBox(height: 2),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Text(
                    label,
                    style: TextStyle(
                      fontSize: 11,
                      color: theme.colorScheme.onSurface.withValues(alpha: 0.5),
                    ),
                  ),
                  const SizedBox(width: 2),
                  Icon(
                    Icons.chevron_right,
                    size: 14,
                    color: theme.colorScheme.onSurface.withValues(alpha: 0.3),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  SliverToBoxAdapter _buildSectionHeader(
    ThemeData theme,
    String title,
    IconData icon, {
    int? count,
    VoidCallback? onSeeAll,
    VoidCallback? action,
    String? actionLabel,
    IconData? actionIcon,
  }) {
    return SliverToBoxAdapter(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 20, 16, 8),
        child: Row(
          children: [
            Icon(icon, size: 18, color: AppTheme.seedPurple),
            const SizedBox(width: 8),
            Text(
              title,
              style: theme.textTheme.titleSmall?.copyWith(
                fontWeight: FontWeight.w700,
                fontSize: 15,
              ),
            ),
            if (count != null) ...[
              const SizedBox(width: 6),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 1),
                decoration: BoxDecoration(
                  color: AppTheme.seedPurple.withValues(alpha: 0.1),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  '$count',
                  style: TextStyle(
                    fontSize: 11,
                    color: AppTheme.seedPurple,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ],
            const Spacer(),
            if (action != null)
              TextButton.icon(
                onPressed: action,
                icon: Icon(actionIcon ?? Icons.add, size: 16),
                label: Text(
                  actionLabel ?? '添加',
                  style: const TextStyle(fontSize: 12),
                ),
                style: TextButton.styleFrom(
                  foregroundColor: AppTheme.seedPurple,
                  padding: const EdgeInsets.symmetric(horizontal: 10),
                  minimumSize: const Size(0, 32),
                ),
              ),
            if (onSeeAll != null)
              TextButton(
                onPressed: onSeeAll,
                style: TextButton.styleFrom(
                  padding: const EdgeInsets.symmetric(horizontal: 8),
                  minimumSize: const Size(0, 32),
                ),
                child: Text(
                  '查看全部',
                  style: TextStyle(fontSize: 12, color: AppTheme.seedPurple),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildBookshelf(ThemeData theme, bool isDark) {
    if (_localBooks.isEmpty) {
      return Padding(
        padding: const EdgeInsets.fromLTRB(20, 4, 20, 8),
        child: GestureDetector(
          onTap: _importLocalBook,
          child: Container(
            padding: const EdgeInsets.symmetric(vertical: 28),
            decoration: BoxDecoration(
              color: isDark ? const Color(0xFF1E1E30) : const Color(0xFFF8F8FC),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(
                color: AppTheme.seedPurple.withValues(alpha: 0.15),
                width: 1.5,
                strokeAlign: BorderSide.strokeAlignInside,
              ),
            ),
            child: Column(
              children: [
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: AppTheme.seedPurple.withValues(alpha: 0.08),
                    shape: BoxShape.circle,
                  ),
                  child: Icon(
                    Icons.add_rounded,
                    size: 28,
                    color: AppTheme.seedPurple.withValues(alpha: 0.6),
                  ),
                ),
                const SizedBox(height: 10),
                Text(
                  '导入本地小说',
                  style: TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                    color: theme.colorScheme.onSurface.withValues(alpha: 0.5),
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  '支持 TXT 格式',
                  style: TextStyle(
                    fontSize: 11,
                    color: theme.colorScheme.onSurface.withValues(alpha: 0.3),
                  ),
                ),
              ],
            ),
          ),
        ),
      );
    }

    return SizedBox(
      height: 180,
      child: ListView.builder(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 16),
        itemCount: _localBooks.length + 1,
        itemBuilder: (context, i) {
          if (i == _localBooks.length) {
            return GestureDetector(
              onTap: _importLocalBook,
              child: Container(
                width: 100,
                margin: const EdgeInsets.only(left: 8),
                child: Column(
                  children: [
                    Container(
                      height: 130,
                      decoration: BoxDecoration(
                        color: isDark
                            ? const Color(0xFF2A2A40)
                            : const Color(0xFFF0F0F5),
                        borderRadius: BorderRadius.circular(10),
                        border: Border.all(
                          color: AppTheme.seedPurple.withValues(alpha: 0.15),
                        ),
                      ),
                      child: Center(
                        child: Icon(
                          Icons.add_rounded,
                          size: 32,
                          color: AppTheme.seedPurple.withValues(alpha: 0.4),
                        ),
                      ),
                    ),
                    const SizedBox(height: 6),
                    Text(
                      '导入更多',
                      style: TextStyle(
                        fontSize: 11,
                        color: theme.colorScheme.onSurface.withValues(
                          alpha: 0.4,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            );
          }

          final book = _localBooks[i];
          return GestureDetector(
            onTap: () => _openLocalBook(book),
            onLongPress: () => _showDeleteLocalBookDialog(book),
            child: Container(
              width: 110,
              margin: EdgeInsets.only(
                right: i < _localBooks.length - 1 ? 12 : 0,
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    height: 130,
                    decoration: BoxDecoration(
                      gradient: LinearGradient(
                        begin: Alignment.topLeft,
                        end: Alignment.bottomRight,
                        colors: [
                          Color.lerp(
                                AppTheme.seedPurple,
                                Colors.blue,
                                (i * 0.15) % 1.0,
                              ) ??
                              AppTheme.seedPurple,
                          Color.lerp(
                                const Color(0xFFA29BFE),
                                Colors.teal,
                                (i * 0.2) % 1.0,
                              ) ??
                              const Color(0xFFA29BFE),
                        ],
                      ),
                      borderRadius: BorderRadius.circular(10),
                      boxShadow: [
                        BoxShadow(
                          color: Colors.black.withValues(alpha: 0.1),
                          blurRadius: 8,
                          offset: const Offset(0, 3),
                        ),
                      ],
                    ),
                    child: Stack(
                      children: [
                        Center(
                          child: Padding(
                            padding: const EdgeInsets.all(10),
                            child: Text(
                              book.title,
                              style: const TextStyle(
                                fontSize: 14,
                                fontWeight: FontWeight.w700,
                                color: Colors.white,
                                height: 1.3,
                              ),
                              textAlign: TextAlign.center,
                              maxLines: 3,
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                        ),
                        Positioned(
                          bottom: 6,
                          right: 6,
                          child: Container(
                            padding: const EdgeInsets.symmetric(
                              horizontal: 5,
                              vertical: 2,
                            ),
                            decoration: BoxDecoration(
                              color: Colors.black.withValues(alpha: 0.3),
                              borderRadius: BorderRadius.circular(4),
                            ),
                            child: Text(
                              'TXT',
                              style: TextStyle(
                                fontSize: 8,
                                color: Colors.white.withValues(alpha: 0.8),
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    book.title,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodySmall?.copyWith(
                      fontWeight: FontWeight.w600,
                      fontSize: 12,
                    ),
                  ),
                  Text(
                    _formatSize(book.fileSize),
                    maxLines: 1,
                    style: TextStyle(
                      fontSize: 10,
                      color: theme.colorScheme.onSurface.withValues(alpha: 0.4),
                    ),
                  ),
                ],
              ),
            ),
          );
        },
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
                        BoxShadow(
                          color: Colors.black.withValues(alpha: 0.1),
                          blurRadius: 8,
                          offset: const Offset(0, 3),
                        ),
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
                            errorBuilder: (_, __, ___) =>
                                _fallbackCover(entry.book.title, isDark),
                          ),
                          Positioned(
                            bottom: 0,
                            left: 0,
                            right: 0,
                            child: Container(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 6,
                                vertical: 4,
                              ),
                              decoration: BoxDecoration(
                                gradient: LinearGradient(
                                  begin: Alignment.bottomCenter,
                                  end: Alignment.topCenter,
                                  colors: [
                                    Colors.black.withValues(alpha: 0.7),
                                    Colors.transparent,
                                  ],
                                ),
                              ),
                              child: Text(
                                _formatTime(entry.lastReadAt),
                                style: const TextStyle(
                                  fontSize: 9,
                                  color: Colors.white70,
                                ),
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
                    style: theme.textTheme.bodySmall?.copyWith(
                      fontWeight: FontWeight.w600,
                      fontSize: 12,
                    ),
                  ),
                  Text(
                    entry.lastChapterTitle,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 10,
                      color: theme.colorScheme.onSurface.withValues(alpha: 0.4),
                    ),
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
                        BoxShadow(
                          color: Colors.black.withValues(alpha: 0.08),
                          blurRadius: 6,
                          offset: const Offset(0, 2),
                        ),
                      ],
                    ),
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(10),
                      child: Image.network(
                        _api.coverUrl(fav.id),
                        fit: BoxFit.cover,
                        width: double.infinity,
                        errorBuilder: (_, __, ___) =>
                            _fallbackCover(fav.title, isDark),
                      ),
                    ),
                  ),
                ),
                const SizedBox(height: 6),
                Text(
                  fav.title,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodySmall?.copyWith(
                    fontWeight: FontWeight.w600,
                    fontSize: 12,
                  ),
                ),
                Text(
                  fav.author,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 10,
                    color: theme.colorScheme.onSurface.withValues(alpha: 0.4),
                  ),
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
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withValues(alpha: 0.04),
                  blurRadius: 6,
                  offset: const Offset(0, 2),
                ),
              ],
            ),
            child: ListTile(
              contentPadding: const EdgeInsets.fromLTRB(12, 4, 8, 4),
              leading: ClipRRect(
                borderRadius: BorderRadius.circular(6),
                child: Image.network(
                  _api.coverUrl(dl.book.id),
                  width: 40,
                  height: 54,
                  fit: BoxFit.cover,
                  errorBuilder: (_, __, ___) => Container(
                    width: 40,
                    height: 54,
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
                dl.book.title,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  fontWeight: FontWeight.w600,
                  fontSize: 14,
                ),
              ),
              subtitle: Text(
                '${dl.chapters.length}章 · ${_formatSize(dl.totalSize)}',
                style: TextStyle(
                  fontSize: 12,
                  color: theme.colorScheme.onSurface.withValues(alpha: 0.5),
                ),
              ),
              trailing: IconButton(
                icon: Icon(
                  Icons.delete_outline,
                  size: 20,
                  color: theme.colorScheme.onSurface.withValues(alpha: 0.4),
                ),
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
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.04),
              blurRadius: 6,
              offset: const Offset(0, 2),
            ),
          ],
        ),
        child: Column(
          children: [
            _settingsTile(
              theme,
              Icons.cleaning_services_rounded,
              '清除缓存',
              subtitle: _formatSize(_totalDownloadSize),
              onTap: _clearCache,
            ),
            Divider(
              height: 1,
              indent: 52,
              color: theme.colorScheme.onSurface.withValues(alpha: 0.06),
            ),
            _settingsTile(
              theme,
              Icons.history,
              '清除阅读历史',
              onTap: _clearHistoryConfirm,
            ),
            Divider(
              height: 1,
              indent: 52,
              color: theme.colorScheme.onSurface.withValues(alpha: 0.06),
            ),
            _settingsTile(
              theme,
              Icons.info_outline_rounded,
              '关于',
              subtitle: 'OohStory v1.3.3',
              onTap: _showAbout,
            ),
          ],
        ),
      ),
    );
  }

  Widget _settingsTile(
    ThemeData theme,
    IconData icon,
    String title, {
    String? subtitle,
    VoidCallback? onTap,
  }) {
    return ListTile(
      leading: Icon(icon, size: 20, color: AppTheme.seedPurple),
      title: Text(
        title,
        style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500),
      ),
      subtitle: subtitle != null
          ? Text(
              subtitle,
              style: TextStyle(
                fontSize: 12,
                color: theme.colorScheme.onSurface.withValues(alpha: 0.4),
              ),
            )
          : null,
      trailing: Icon(
        Icons.chevron_right,
        size: 18,
        color: theme.colorScheme.onSurface.withValues(alpha: 0.3),
      ),
      onTap: onTap,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
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
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
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

  void _showDeleteLocalBookDialog(LocalBookInfo book) {
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
              Navigator.pop(ctx);
            },
            style: FilledButton.styleFrom(backgroundColor: Colors.red.shade400),
            child: const Text('删除'),
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
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
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
                    Text(
                      '阅读历史',
                      style: theme.textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const Spacer(),
                    TextButton(
                      onPressed: () {
                        Navigator.pop(ctx);
                        _clearHistoryConfirm();
                      },
                      child: Text(
                        '清空',
                        style: TextStyle(
                          color: Colors.red.shade400,
                          fontSize: 13,
                        ),
                      ),
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
                            width: 40,
                            height: 54,
                            fit: BoxFit.cover,
                            errorBuilder: (_, __, ___) => Container(
                              width: 40,
                              height: 54,
                              color: AppTheme.seedPurple.withValues(alpha: 0.1),
                              child: const Icon(Icons.book, size: 20),
                            ),
                          ),
                        ),
                        title: Text(
                          entry.book.title,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
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
                child: Text(
                  '我的收藏',
                  style: theme.textTheme.titleMedium?.copyWith(
                    fontWeight: FontWeight.w700,
                  ),
                ),
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
                            width: 40,
                            height: 54,
                            fit: BoxFit.cover,
                            errorBuilder: (_, __, ___) => Container(
                              width: 40,
                              height: 54,
                              color: AppTheme.seedPurple.withValues(alpha: 0.1),
                              child: const Icon(Icons.book, size: 20),
                            ),
                          ),
                        ),
                        title: Text(
                          fav.title,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                        subtitle: Text(
                          fav.author,
                          style: TextStyle(
                            fontSize: 12,
                            color: theme.colorScheme.onSurface.withValues(
                              alpha: 0.5,
                            ),
                          ),
                        ),
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
        content: Text(
          '将删除所有已下载的章节内容（${_formatSize(_totalDownloadSize)}）。收藏和阅读记录不受影响。',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('清除'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await _storage.clearAllDownloads();
      _refresh();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('缓存已清除'),
            behavior: SnackBarBehavior.floating,
          ),
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

  void _showAbout() {
    showAboutDialog(
      context: context,
      applicationName: 'OohStory',
      applicationVersion: 'v1.3.3',
      applicationIcon: Container(
        width: 48,
        height: 48,
        decoration: BoxDecoration(
          gradient: const LinearGradient(
            colors: [Color(0xFF6C5CE7), Color(0xFFA29BFE)],
          ),
          borderRadius: BorderRadius.circular(12),
        ),
        child: const Icon(Icons.auto_stories, color: Colors.white, size: 24),
      ),
      children: [
        const Text('免费中文小说阅读与深度拆书'),
        const SizedBox(height: 8),
        Text(
          '开源 · 免费 · 自托管',
          style: TextStyle(color: Colors.grey.shade500, fontSize: 13),
        ),
      ],
    );
  }
}
