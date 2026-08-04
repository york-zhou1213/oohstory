import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../services/local_storage_service.dart';
import '../services/reading_progress.dart';
import '../services/account_service.dart';
import '../models/book.dart';
import '../theme/app_theme.dart';
import 'reader_screen.dart';
import 'volume_detail_screen.dart';
import 'auth_screen.dart';

class BookDetailScreen extends StatefulWidget {
  final String bookId;
  const BookDetailScreen({super.key, required this.bookId});

  @override
  State<BookDetailScreen> createState() => _BookDetailScreenState();
}

class _BookDetailScreenState extends State<BookDetailScreen> {
  final _api = ApiService();
  final _storage = LocalStorageService();
  final _progress = ReadingProgressService();
  Book? _book;
  List<Chapter> _chapters = [];
  List<Volume> _volumes = [];
  bool _loading = true;
  bool _catalogExpanded = false;
  bool _descExpanded = false;
  bool _isFavorite = false;
  bool _inCloudShelf = false;
  bool _downloading = false;
  int _downloadedCount = 0;
  ReadingProgress? _savedProgress;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    await _storage.init();
    await _progress.init();
    try {
      final results = await Future.wait([
        _api.getBook(widget.bookId),
        _api.getChapterCatalog(widget.bookId),
      ]);
      if (mounted) {
        final catalog = results[1] as ChapterCatalog;
        setState(() {
          _book = results[0] as Book;
          _chapters = catalog.chapters;
          _volumes = catalog.volumes;
          _loading = false;
          _isFavorite = _storage.isFavorite(widget.bookId);
          _inCloudShelf = AccountService.instance.contains(
            'bookshelf',
            widget.bookId,
          );
          _downloadedCount = _storage.downloadedChapterCount(widget.bookId);
          _savedProgress = _progress.get(widget.bookId);
        });
      }
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _toggleFavorite() async {
    if (_book == null) return;
    _storage.toggleFavorite(_book!);
    setState(() => _isFavorite = !_isFavorite);
    if (AccountService.instance.isSignedIn) {
      try {
        await AccountService.instance.setBookCollection(
          'favorites',
          _book!,
          _isFavorite,
        );
      } catch (_) {}
    }
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(_isFavorite ? '已加入收藏' : '已取消收藏'),
        behavior: SnackBarBehavior.floating,
        duration: const Duration(seconds: 1),
      ),
    );
  }

  Future<void> _toggleCloudShelf() async {
    if (_book == null) return;
    if (!AccountService.instance.isSignedIn) {
      final signedIn = await Navigator.of(
        context,
      ).push<bool>(MaterialPageRoute(builder: (_) => const AuthScreen()));
      if (signedIn != true || !mounted) return;
    }
    try {
      await AccountService.instance.setBookCollection(
        'bookshelf',
        _book!,
        !_inCloudShelf,
      );
      if (mounted) setState(() => _inCloudShelf = !_inCloudShelf);
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(error.toString()),
            behavior: SnackBarBehavior.floating,
          ),
        );
      }
    }
  }

  Future<void> _downloadAll() async {
    if (_book == null || _downloading) return;
    setState(() => _downloading = true);

    int downloaded = 0;
    for (final ch in _chapters) {
      if (_storage.isChapterDownloaded(_book!.id, ch.id)) {
        downloaded++;
        continue;
      }
      try {
        final full = await _api.getChapter(_book!.id, ch.id);
        if (full.content != null && full.content!.isNotEmpty) {
          await _storage.downloadChapter(_book!, full, full.content!);
          downloaded++;
          if (mounted) setState(() => _downloadedCount = downloaded);
        }
      } catch (_) {}
    }
    if (mounted) {
      setState(() => _downloading = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('下载完成，共$downloaded章'),
          behavior: SnackBarBehavior.floating,
        ),
      );
    }
  }

  String _formatWordCount(int? count) {
    if (count == null) return '';
    if (count >= 100000000)
      return '${(count / 100000000).toStringAsFixed(1)}亿字';
    if (count >= 10000) return '${(count / 10000).toStringAsFixed(1)}万字';
    return '$count字';
  }

  String _statusLabel(String? s) {
    if (s == null) return '';
    if (s == 'finished' || s == '完结') return '已完结';
    if (s == 'ongoing' || s == '连载') return '连载中';
    return s;
  }

  void _openReader(String chapterId) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => ReaderScreen(
          bookId: widget.bookId,
          chapterId: chapterId,
          chapters: _chapters,
          book: _book,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (_loading)
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    if (_book == null)
      return Scaffold(
        appBar: AppBar(),
        body: const Center(child: Text('书籍不存在')),
      );
    final book = _book!;
    final theme = Theme.of(context);

    return Scaffold(
      body: CustomScrollView(
        slivers: [
          _buildAppBar(theme, book),
          SliverToBoxAdapter(child: _buildInfoSection(theme, book)),
          if (book.description != null && book.description!.isNotEmpty)
            SliverToBoxAdapter(child: _buildDescription(theme, book)),
          SliverToBoxAdapter(child: _buildChapterHeader(theme)),
          _buildChapterList(theme),
          const SliverToBoxAdapter(child: SizedBox(height: 100)),
        ],
      ),
      bottomNavigationBar: _buildBottomBar(theme),
    );
  }

  Widget _buildAppBar(ThemeData theme, Book book) {
    return SliverAppBar(
      expandedHeight: 280,
      pinned: true,
      stretch: true,
      backgroundColor: theme.appBarTheme.backgroundColor,
      flexibleSpace: FlexibleSpaceBar(
        background: Container(
          decoration: const BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: [Color(0xFF6C5CE7), Color(0xFF8B7CF6), Color(0xFFA29BFE)],
            ),
          ),
          child: SafeArea(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(20, 56, 20, 20),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Hero(
                    tag: 'book_cover_${widget.bookId}',
                    child: Container(
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(12),
                        boxShadow: [
                          BoxShadow(
                            color: Colors.black.withValues(alpha: 0.3),
                            blurRadius: 20,
                            offset: const Offset(0, 8),
                          ),
                        ],
                      ),
                      child: ClipRRect(
                        borderRadius: BorderRadius.circular(12),
                        child: Image.network(
                          _api.coverUrl(widget.bookId),
                          width: 110,
                          height: 155,
                          fit: BoxFit.cover,
                          errorBuilder: (_, __, ___) => Container(
                            width: 110,
                            height: 155,
                            decoration: BoxDecoration(
                              gradient: LinearGradient(
                                begin: Alignment.topLeft,
                                end: Alignment.bottomRight,
                                colors: [
                                  Colors.white.withValues(alpha: 0.2),
                                  Colors.white.withValues(alpha: 0.05),
                                ],
                              ),
                              borderRadius: BorderRadius.circular(12),
                            ),
                            child: Center(
                              child: Text(
                                book.title.length > 4
                                    ? book.title.substring(0, 4)
                                    : book.title,
                                style: const TextStyle(
                                  color: Colors.white,
                                  fontSize: 18,
                                  fontWeight: FontWeight.w700,
                                ),
                                textAlign: TextAlign.center,
                              ),
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(width: 18),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          book.title,
                          style: const TextStyle(
                            fontSize: 22,
                            fontWeight: FontWeight.w800,
                            color: Colors.white,
                            height: 1.2,
                            letterSpacing: -0.3,
                          ),
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                        const SizedBox(height: 8),
                        Text(
                          book.author,
                          style: TextStyle(
                            fontSize: 14,
                            color: Colors.white.withValues(alpha: 0.8),
                          ),
                        ),
                        const SizedBox(height: 12),
                        Wrap(
                          spacing: 8,
                          runSpacing: 6,
                          children: [
                            if (book.category != null)
                              _tagBadge(book.category!),
                            if (book.status != null)
                              _tagBadge(_statusLabel(book.status)),
                          ],
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _tagBadge(String label) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.18),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: Colors.white.withValues(alpha: 0.2)),
      ),
      child: Text(
        label,
        style: const TextStyle(
          color: Colors.white,
          fontSize: 11,
          fontWeight: FontWeight.w500,
        ),
      ),
    );
  }

  Widget _buildInfoSection(ThemeData theme, Book book) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 20, 20, 8),
      child: Row(
        children: [
          _infoTile(theme, _formatWordCount(book.wordCount), '总字数'),
          _divider(theme),
          _infoTile(theme, '${book.chapterCount ?? _chapters.length}', '章节'),
          _divider(theme),
          _infoTile(theme, _statusLabel(book.status ?? ''), '状态'),
        ],
      ),
    );
  }

  Widget _infoTile(ThemeData theme, String value, String label) {
    return Expanded(
      child: Column(
        children: [
          Text(
            value,
            style: theme.textTheme.titleSmall?.copyWith(
              fontWeight: FontWeight.w700,
              color: AppTheme.seedPurple,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            label,
            style: theme.textTheme.labelSmall?.copyWith(
              color: theme.colorScheme.onSurface.withValues(alpha: 0.45),
            ),
          ),
        ],
      ),
    );
  }

  Widget _divider(ThemeData theme) {
    return Container(
      width: 1,
      height: 28,
      color: theme.colorScheme.onSurface.withValues(alpha: 0.08),
    );
  }

  Widget _buildDescription(ThemeData theme, Book book) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '简介',
            style: theme.textTheme.titleSmall?.copyWith(
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: 8),
          GestureDetector(
            onTap: () => setState(() => _descExpanded = !_descExpanded),
            child: AnimatedCrossFade(
              firstChild: Text(
                book.description!,
                style: theme.textTheme.bodyMedium?.copyWith(
                  height: 1.6,
                  color: theme.colorScheme.onSurface.withValues(alpha: 0.7),
                ),
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
              ),
              secondChild: Text(
                book.description!,
                style: theme.textTheme.bodyMedium?.copyWith(
                  height: 1.6,
                  color: theme.colorScheme.onSurface.withValues(alpha: 0.7),
                ),
              ),
              crossFadeState: _descExpanded
                  ? CrossFadeState.showSecond
                  : CrossFadeState.showFirst,
              duration: const Duration(milliseconds: 200),
            ),
          ),
          if (!_descExpanded && (book.description?.length ?? 0) > 100)
            Align(
              alignment: Alignment.centerRight,
              child: TextButton(
                onPressed: () => setState(() => _descExpanded = true),
                style: TextButton.styleFrom(
                  padding: EdgeInsets.zero,
                  minimumSize: const Size(0, 32),
                ),
                child: Text(
                  '展开',
                  style: TextStyle(fontSize: 12, color: AppTheme.seedPurple),
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildChapterHeader(ThemeData theme) {
    final label = _volumes.isNotEmpty
        ? '${_volumes.length}卷 · ${_chapters.length}章'
        : '${_chapters.length}章';
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 8),
      child: Row(
        children: [
          Text(
            _volumes.isNotEmpty ? '分卷目录' : '目录',
            style: theme.textTheme.titleSmall?.copyWith(
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(width: 8),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
            decoration: BoxDecoration(
              color: AppTheme.seedPurple.withValues(alpha: 0.1),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Text(
              label,
              style: TextStyle(
                fontSize: 11,
                color: AppTheme.seedPurple,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          const Spacer(),
          if (_volumes.isEmpty)
            TextButton.icon(
              onPressed: () =>
                  setState(() => _catalogExpanded = !_catalogExpanded),
              icon: Icon(
                _catalogExpanded ? Icons.unfold_less : Icons.unfold_more,
                size: 16,
              ),
              label: Text(
                _catalogExpanded ? '收起' : '展开全部',
                style: const TextStyle(fontSize: 12),
              ),
              style: TextButton.styleFrom(
                padding: const EdgeInsets.symmetric(horizontal: 8),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildChapterList(ThemeData theme) {
    if (_volumes.isNotEmpty) return _buildVolumeList(theme);
    final showCount = _catalogExpanded
        ? _chapters.length
        : _chapters.length.clamp(0, 20);
    return SliverList(
      delegate: SliverChildBuilderDelegate((context, i) {
        final ch = _chapters[i];
        return Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: ListTile(
            dense: true,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(10),
            ),
            leading: Container(
              width: 28,
              height: 28,
              decoration: BoxDecoration(
                color: AppTheme.seedPurple.withValues(alpha: 0.08),
                borderRadius: BorderRadius.circular(7),
              ),
              child: Center(
                child: Text(
                  '${i + 1}',
                  style: TextStyle(
                    fontSize: 11,
                    color: AppTheme.seedPurple,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ),
            title: Text(
              ch.displayTitle,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.bodyMedium?.copyWith(
                fontWeight: FontWeight.w500,
              ),
            ),
            trailing: ch.wordCount != null
                ? Text(
                    '${ch.wordCount}字',
                    style: TextStyle(
                      fontSize: 11,
                      color: theme.colorScheme.onSurface.withValues(alpha: 0.4),
                    ),
                  )
                : null,
            onTap: () => _openReader(ch.id),
          ),
        );
      }, childCount: showCount),
    );
  }

  Widget _buildVolumeList(ThemeData theme) {
    final chapterMap = <int, Chapter>{};
    for (final ch in _chapters) {
      chapterMap[int.tryParse(ch.id) ?? 0] = ch;
    }
    return SliverPadding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      sliver: SliverGrid(
        gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
          crossAxisCount: 3,
          childAspectRatio: 0.55,
          mainAxisSpacing: 12,
          crossAxisSpacing: 10,
        ),
        delegate: SliverChildBuilderDelegate((context, volIdx) {
          final vol = _volumes[volIdx];
          final volChapters = vol.chapterIds
              .map((cid) => chapterMap[cid])
              .whereType<Chapter>()
              .toList();
          return _buildVolumeCard(theme, vol, volChapters);
        }, childCount: _volumes.length),
      ),
    );
  }

  Widget _buildVolumeCard(
    ThemeData theme,
    Volume vol,
    List<Chapter> volChapters,
  ) {
    return GestureDetector(
      onTap: () {
        Navigator.of(context).push(
          MaterialPageRoute(
            builder: (_) => VolumeDetailScreen(
              bookId: widget.bookId,
              volume: vol,
              chapters: volChapters,
              book: _book,
            ),
          ),
        );
      },
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
                    blurRadius: 8,
                    offset: const Offset(0, 3),
                  ),
                ],
              ),
              child: ClipRRect(
                borderRadius: BorderRadius.circular(10),
                child: vol.hasCover
                    ? Image.network(
                        _api.illustrationUrl(widget.bookId, vol.coverPath),
                        fit: BoxFit.cover,
                        width: double.infinity,
                        errorBuilder: (_, __, ___) =>
                            _volumePlaceholder(theme, vol),
                      )
                    : _volumePlaceholder(theme, vol),
              ),
            ),
          ),
          const SizedBox(height: 6),
          Text(
            vol.title,
            style: theme.textTheme.bodySmall?.copyWith(
              fontWeight: FontWeight.w600,
              fontSize: 11,
              height: 1.2,
            ),
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
          ),
          Text(
            '${vol.chapterIds.length}章',
            style: TextStyle(
              fontSize: 10,
              color: theme.colorScheme.onSurface.withValues(alpha: 0.5),
            ),
          ),
        ],
      ),
    );
  }

  Widget _volumePlaceholder(ThemeData theme, Volume vol) {
    return Container(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            AppTheme.seedPurple.withValues(alpha: 0.15),
            AppTheme.seedPurple.withValues(alpha: 0.05),
          ],
        ),
      ),
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.menu_book_rounded,
              size: 28,
              color: AppTheme.seedPurple.withValues(alpha: 0.4),
            ),
            const SizedBox(height: 4),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 6),
              child: Text(
                '第${vol.id}卷',
                style: TextStyle(
                  fontSize: 11,
                  fontWeight: FontWeight.w600,
                  color: AppTheme.seedPurple.withValues(alpha: 0.6),
                ),
                textAlign: TextAlign.center,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildBottomBar(ThemeData theme) {
    return Container(
      decoration: BoxDecoration(
        color: theme.scaffoldBackgroundColor,
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.05),
            blurRadius: 10,
            offset: const Offset(0, -2),
          ),
        ],
      ),
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
          child: Row(
            children: [
              _bottomIconBtn(
                icon: _isFavorite ? Icons.favorite : Icons.favorite_border,
                label: _isFavorite ? '已收藏' : '收藏',
                color: _isFavorite
                    ? AppTheme.accentPink
                    : theme.colorScheme.onSurface.withValues(alpha: 0.6),
                onTap: _toggleFavorite,
              ),
              const SizedBox(width: 4),
              _bottomIconBtn(
                icon: _inCloudShelf ? Icons.bookmark : Icons.bookmark_border,
                label: _inCloudShelf ? '已在书架' : '书架',
                color: _inCloudShelf
                    ? const Color(0xFF00B894)
                    : theme.colorScheme.onSurface.withValues(alpha: 0.6),
                onTap: _toggleCloudShelf,
              ),
              const SizedBox(width: 4),
              _bottomIconBtn(
                icon: _downloading
                    ? Icons.downloading
                    : Icons.download_outlined,
                label: _downloading
                    ? '$_downloadedCount/${_chapters.length}'
                    : _downloadedCount > 0
                    ? '$_downloadedCount章'
                    : '下载',
                color: _downloadedCount > 0
                    ? const Color(0xFF00B894)
                    : theme.colorScheme.onSurface.withValues(alpha: 0.6),
                onTap: _downloading ? null : _downloadAll,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: FilledButton.icon(
                  onPressed: _chapters.isNotEmpty
                      ? () {
                          if (_savedProgress != null &&
                              _chapters.any(
                                (c) => c.id == _savedProgress!.chapterId,
                              )) {
                            _openReader(_savedProgress!.chapterId);
                          } else {
                            _openReader(_chapters.first.id);
                          }
                        }
                      : null,
                  icon: Icon(
                    _savedProgress != null
                        ? Icons.play_arrow_rounded
                        : Icons.auto_stories,
                    size: 18,
                  ),
                  label: Text(_savedProgress != null ? '继续阅读' : '开始阅读'),
                  style: FilledButton.styleFrom(
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(14),
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _bottomIconBtn({
    required IconData icon,
    required String label,
    required Color color,
    VoidCallback? onTap,
  }) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(10),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 22, color: color),
            const SizedBox(height: 2),
            Text(
              label,
              style: TextStyle(
                fontSize: 10,
                color: color,
                fontWeight: FontWeight.w500,
              ),
            ),
          ],
        ),
      ),
    );
  }

  @override
  void dispose() {
    _api.dispose();
    super.dispose();
  }
}
