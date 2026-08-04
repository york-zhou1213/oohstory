import 'dart:async';
import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../services/local_storage_service.dart';
import '../services/reading_progress.dart';
import '../models/book.dart';
import '../widgets/book_card.dart';
import '../theme/app_theme.dart';
import 'book_detail_screen.dart';
import 'reader_screen.dart';
import 'library_screen.dart';

const _heroSynopsisFallback = '打开作品详情，立即开始阅读。';

String heroSynopsisFor(Book book) {
  final description = book.description?.trim();
  return description == null || description.isEmpty
      ? _heroSynopsisFallback
      : description;
}

String heroChapterCountLabelFor(Book book) {
  final count = book.chapterCount;
  return '${count != null && count > 0 ? count : '?'}章';
}

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final _api = ApiService();
  final _storage = LocalStorageService();
  final _progressService = ReadingProgressService();
  List<Book> _featured = [];
  List<Book> _recommendations = [];
  List<Book> _longNovels = [];
  List<Book> _shortNovels = [];
  Map<String, List<Book>> _categoryBooks = {};
  Map<String, dynamic> _stats = {};
  bool _loading = true;
  String? _error;
  HistoryEntry? _lastRead;
  bool _navigatingToReader = false;
  int _heroIndex = 0;
  Timer? _heroTimer;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      await _storage.init();
      await _progressService.init();
      final data = await _api.getHome();
      final books = (data['featured'] as List? ?? [])
          .map((e) => Book.fromJson(e as Map<String, dynamic>))
          .toList();
      final recs = (data['recommendations'] as List? ?? [])
          .map((e) => Book.fromJson(e as Map<String, dynamic>))
          .toList();
      final longs = (data['long_novels'] as List? ?? [])
          .map((e) => Book.fromJson(e as Map<String, dynamic>))
          .toList();
      final shorts = (data['short_novels'] as List? ?? [])
          .map((e) => Book.fromJson(e as Map<String, dynamic>))
          .toList();
      final catBooksRaw = data['category_books'] as Map<String, dynamic>? ?? {};
      final catBooks = <String, List<Book>>{};
      catBooksRaw.forEach((key, val) {
        if (val is List) {
          catBooks[key] = val
              .map((e) => Book.fromJson(e as Map<String, dynamic>))
              .toList();
        }
      });
      final history = _storage.getHistory();
      if (mounted)
        setState(() {
          _featured = books;
          _recommendations = recs;
          _longNovels = longs;
          _shortNovels = shorts;
          _categoryBooks = catBooks;
          _stats = data['stats'] as Map<String, dynamic>? ?? {};
          _lastRead = history.isNotEmpty ? history.first : null;
          _loading = false;
        });
      _startHeroTimer();
    } catch (e) {
      if (mounted)
        setState(() {
          _error = e.toString();
          _loading = false;
        });
    }
  }

  Future<void> _openContinueReading(HistoryEntry entry) async {
    if (_navigatingToReader) return;
    setState(() => _navigatingToReader = true);
    try {
      final results = await Future.wait([
        _api.getBook(entry.book.id),
        _api.getChapters(entry.book.id),
      ]);
      final book = results[0] as Book;
      final chapters = results[1] as List<Chapter>;
      if (chapters.isEmpty || !mounted) return;

      final progress = _progressService.get(entry.book.id);
      final chapterId = progress?.chapterId ?? entry.lastChapterId;
      final validChapterId = chapters.any((c) => c.id == chapterId)
          ? chapterId
          : chapters.first.id;

      if (mounted) {
        Navigator.of(context)
            .push(
              MaterialPageRoute(
                builder: (_) => ReaderScreen(
                  bookId: entry.book.id,
                  chapterId: validChapterId,
                  chapters: chapters,
                  book: book,
                ),
              ),
            )
            .then((_) {
              // Refresh history after returning from reader
              final history = _storage.getHistory();
              if (mounted) {
                setState(
                  () => _lastRead = history.isNotEmpty ? history.first : null,
                );
              }
            });
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('无法加载书籍'),
            behavior: SnackBarBehavior.floating,
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _navigatingToReader = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.cloud_off,
              size: 48,
              color: theme.colorScheme.onSurface.withValues(alpha: 0.3),
            ),
            const SizedBox(height: 16),
            Text('无法连接服务器', style: theme.textTheme.titleMedium),
            const SizedBox(height: 12),
            FilledButton(
              onPressed: () {
                setState(() {
                  _loading = true;
                  _error = null;
                });
                _load();
              },
              child: const Text('重试'),
            ),
          ],
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: _load,
      child: CustomScrollView(
        slivers: [
          SliverToBoxAdapter(child: _buildHero(theme)),
          SliverToBoxAdapter(child: _buildSearchBar(theme, isDark)),
          if (_lastRead != null)
            SliverToBoxAdapter(child: _buildContinueReading(theme, isDark)),
          // stats removed
          if (_recommendations.isNotEmpty) ...[
            SliverToBoxAdapter(
              child: _sectionHeader(
                theme,
                '人气推荐',
                '每日精选',
                Icons.local_fire_department_rounded,
              ),
            ),
            SliverToBoxAdapter(
              child: _buildHorizontalBookScroll(theme, _recommendations),
            ),
          ],
          if (_longNovels.isNotEmpty) ...[
            SliverToBoxAdapter(
              child: _sectionHeader(
                theme,
                '经典长篇',
                '百万字巨著',
                Icons.auto_stories_rounded,
                color: const Color(0xFFE17055),
              ),
            ),
            SliverToBoxAdapter(child: _buildLongNovelList(theme, isDark)),
          ],
          if (_categoryBooks.isNotEmpty) ...[
            SliverToBoxAdapter(
              child: _sectionHeader(
                theme,
                '热门分类',
                '分类推荐',
                Icons.bolt_rounded,
                color: const Color(0xFF00B894),
              ),
            ),
            SliverToBoxAdapter(
              child: _buildCategoryRecommendations(theme, isDark),
            ),
          ],
          SliverToBoxAdapter(
            child: _sectionHeader(
              theme,
              '最新上架',
              '持续更新',
              Icons.auto_awesome_rounded,
            ),
          ),
          SliverToBoxAdapter(
            child: _buildHorizontalBookScroll(theme, _featured),
          ),
          const SliverToBoxAdapter(child: SizedBox(height: 100)),
        ],
      ),
    );
  }

  Widget _buildHorizontalBookScroll(ThemeData theme, List<Book> books) {
    return SizedBox(
      height: 180,
      child: ListView.builder(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 16),
        itemCount: books.length,
        itemBuilder: (context, i) {
          final book = books[i];
          final coverUrl = book.coverUrl != null
              ? _api.fullCoverUrl(book.coverUrl)
              : _api.coverUrl(book.id);
          return GestureDetector(
            onTap: () => Navigator.of(context).push(
              MaterialPageRoute(
                builder: (_) => BookDetailScreen(bookId: book.id),
              ),
            ),
            child: Container(
              width: 88,
              margin: EdgeInsets.only(right: i < books.length - 1 ? 8 : 0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  ClipRRect(
                    borderRadius: BorderRadius.circular(6),
                    child: Image.network(
                      coverUrl,
                      width: 88,
                      height: 124,
                      fit: BoxFit.cover,
                      errorBuilder: (_, __, ___) => Container(
                        width: 88,
                        height: 124,
                        decoration: BoxDecoration(
                          gradient: LinearGradient(
                            begin: Alignment.topLeft,
                            end: Alignment.bottomRight,
                            colors: [
                              theme.colorScheme.primaryContainer,
                              theme.colorScheme.tertiaryContainer,
                            ],
                          ),
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: Center(
                          child: Text(
                            book.title.length > 2
                                ? book.title.substring(0, 2)
                                : book.title,
                            style: TextStyle(
                              fontSize: 14,
                              fontWeight: FontWeight.w700,
                              color: theme.colorScheme.onPrimaryContainer,
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 5),
                  Text(
                    book.title,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodySmall?.copyWith(
                      fontWeight: FontWeight.w500,
                      fontSize: 11,
                      height: 1.3,
                    ),
                  ),
                  Text(
                    book.author,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 9,
                      color: theme.colorScheme.onSurface.withValues(alpha: 0.5),
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

  Widget _buildLongNovelList(ThemeData theme, bool isDark) {
    final cardColor = isDark ? const Color(0xFF1A1D27) : Colors.white;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Column(
        children: _longNovels.take(6).map((book) {
          final coverUrl = book.coverUrl != null
              ? _api.fullCoverUrl(book.coverUrl)
              : _api.coverUrl(book.id);
          return Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: GestureDetector(
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute(
                  builder: (_) => BookDetailScreen(bookId: book.id),
                ),
              ),
              child: Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: cardColor,
                  borderRadius: BorderRadius.circular(10),
                  border: Border.all(
                    color: theme.colorScheme.outlineVariant.withValues(
                      alpha: 0.3,
                    ),
                  ),
                ),
                child: Row(
                  children: [
                    ClipRRect(
                      borderRadius: BorderRadius.circular(6),
                      child: Image.network(
                        coverUrl,
                        width: 80,
                        height: 112,
                        fit: BoxFit.cover,
                        errorBuilder: (_, __, ___) => Container(
                          width: 80,
                          height: 112,
                          decoration: BoxDecoration(
                            gradient: LinearGradient(
                              begin: Alignment.topLeft,
                              end: Alignment.bottomRight,
                              colors: [
                                theme.colorScheme.primaryContainer,
                                theme.colorScheme.tertiaryContainer,
                              ],
                            ),
                            borderRadius: BorderRadius.circular(6),
                          ),
                          child: Center(
                            child: Text(
                              book.title.length > 2
                                  ? book.title.substring(0, 2)
                                  : book.title,
                              style: TextStyle(
                                fontSize: 16,
                                fontWeight: FontWeight.w700,
                                color: theme.colorScheme.onPrimaryContainer,
                              ),
                            ),
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            book.title,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: theme.textTheme.titleSmall?.copyWith(
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                          if (book.description != null &&
                              book.description!.isNotEmpty) ...[
                            const SizedBox(height: 4),
                            Text(
                              book.description!,
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(
                                fontSize: 12,
                                color: theme.colorScheme.onSurface.withValues(
                                  alpha: 0.55,
                                ),
                                height: 1.5,
                              ),
                            ),
                          ],
                          const SizedBox(height: 8),
                          Wrap(
                            spacing: 6,
                            runSpacing: 4,
                            children: [
                              if (book.category != null)
                                _metaTag(theme, book.category!),
                              Text(
                                book.author,
                                style: TextStyle(
                                  fontSize: 11,
                                  color: theme.colorScheme.onSurface.withValues(
                                    alpha: 0.5,
                                  ),
                                ),
                              ),
                              _statusTag(book.status),
                              if (book.wordCount != null)
                                Text(
                                  _formatWordCount(book.wordCount!),
                                  style: TextStyle(
                                    fontSize: 11,
                                    color: theme.colorScheme.onSurface
                                        .withValues(alpha: 0.5),
                                  ),
                                ),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }

  Widget _metaTag(ThemeData theme, String text) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
      decoration: BoxDecoration(
        color: AppTheme.seedPurple.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(3),
      ),
      child: Text(
        text,
        style: TextStyle(
          fontSize: 10,
          color: AppTheme.seedPurple,
          fontWeight: FontWeight.w500,
        ),
      ),
    );
  }

  Widget _statusTag(String? status) {
    final isFinished = status == 'finished' || status == '完结';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
      decoration: BoxDecoration(
        color: (isFinished ? const Color(0xFF00B894) : const Color(0xFFE17055))
            .withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(3),
      ),
      child: Text(
        isFinished ? '已完本' : '连载中',
        style: TextStyle(
          fontSize: 10,
          color: isFinished ? const Color(0xFF00B894) : const Color(0xFFE17055),
          fontWeight: FontWeight.w500,
        ),
      ),
    );
  }

  Widget _buildCategoryRecommendations(ThemeData theme, bool isDark) {
    final sortedEntries = _categoryBooks.entries.toList()
      ..sort((a, b) => b.value.length.compareTo(a.value.length));
    final catNames = sortedEntries.take(4).map((e) => e.key).toList();
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Column(
        children: catNames.map((catName) {
          final books = (_categoryBooks[catName] ?? []).take(10).toList();
          if (books.isEmpty) return const SizedBox.shrink();
          return Padding(
            padding: const EdgeInsets.only(bottom: 16),
            child: Column(
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                      catName,
                      style: theme.textTheme.titleSmall?.copyWith(
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    GestureDetector(
                      onTap: () => Navigator.of(context).push(
                        MaterialPageRoute(
                          builder: (_) => Scaffold(
                            appBar: AppBar(title: Text(catName)),
                            body: const LibraryScreen(),
                          ),
                        ),
                      ),
                      child: Text(
                        '更多→',
                        style: TextStyle(
                          fontSize: 12,
                          color: AppTheme.seedPurple,
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                SizedBox(
                  height: 180,
                  child: ListView.builder(
                    scrollDirection: Axis.horizontal,
                    itemCount: books.length,
                    itemBuilder: (context, i) {
                      final book = books[i];
                      final coverUrl = book.coverUrl != null
                          ? _api.fullCoverUrl(book.coverUrl)
                          : _api.coverUrl(book.id);
                      return GestureDetector(
                        onTap: () => Navigator.of(context).push(
                          MaterialPageRoute(
                            builder: (_) => BookDetailScreen(bookId: book.id),
                          ),
                        ),
                        child: Container(
                          width: 88,
                          margin: EdgeInsets.only(
                            right: i < books.length - 1 ? 10 : 0,
                          ),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              ClipRRect(
                                borderRadius: BorderRadius.circular(6),
                                child: Image.network(
                                  coverUrl,
                                  width: 88,
                                  height: 124,
                                  fit: BoxFit.cover,
                                  errorBuilder: (_, __, ___) => Container(
                                    width: 88,
                                    height: 124,
                                    decoration: BoxDecoration(
                                      gradient: LinearGradient(
                                        begin: Alignment.topLeft,
                                        end: Alignment.bottomRight,
                                        colors: [
                                          theme.colorScheme.primaryContainer,
                                          theme.colorScheme.tertiaryContainer,
                                        ],
                                      ),
                                      borderRadius: BorderRadius.circular(6),
                                    ),
                                    child: Center(
                                      child: Text(
                                        book.title.length > 2
                                            ? book.title.substring(0, 2)
                                            : book.title,
                                        style: TextStyle(
                                          fontSize: 14,
                                          fontWeight: FontWeight.w700,
                                          color: theme
                                              .colorScheme
                                              .onPrimaryContainer,
                                        ),
                                      ),
                                    ),
                                  ),
                                ),
                              ),
                              const SizedBox(height: 4),
                              Text(
                                book.title,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: theme.textTheme.bodySmall?.copyWith(
                                  fontWeight: FontWeight.w500,
                                  fontSize: 12,
                                ),
                              ),
                              if (book.author.isNotEmpty)
                                Text(
                                  book.author,
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: TextStyle(
                                    fontSize: 11,
                                    color: theme.colorScheme.onSurface
                                        .withValues(alpha: 0.5),
                                  ),
                                ),
                            ],
                          ),
                        ),
                      );
                    },
                  ),
                ),
              ],
            ),
          );
        }).toList(),
      ),
    );
  }

  String _formatWordCount(int count) {
    if (count >= 10000)
      return '${(count / 10000).toStringAsFixed(count >= 1000000 ? 0 : 1)}万字';
    return '$count字';
  }

  Widget _sectionHeader(
    ThemeData theme,
    String title,
    String kicker,
    IconData icon, {
    Color? color,
  }) {
    final accent = color ?? AppTheme.seedPurple;
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 24, 20, 12),
      child: Row(
        children: [
          Container(
            padding: const EdgeInsets.all(6),
            decoration: BoxDecoration(
              color: accent.withValues(alpha: 0.1),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Icon(icon, size: 16, color: accent),
          ),
          const SizedBox(width: 10),
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                kicker,
                style: TextStyle(
                  fontSize: 10,
                  fontWeight: FontWeight.w600,
                  color: accent,
                  letterSpacing: 1,
                ),
              ),
              Text(
                title,
                style: theme.textTheme.titleMedium?.copyWith(
                  fontWeight: FontWeight.w700,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildSearchBar(ThemeData theme, bool isDark) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
      child: GestureDetector(
        onTap: () {
          Navigator.of(context).push(
            MaterialPageRoute(
              builder: (_) => Scaffold(
                appBar: AppBar(title: const Text('书库')),
                body: const LibraryScreen(),
              ),
            ),
          );
        },
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          decoration: BoxDecoration(
            color: isDark ? const Color(0xFF1E1E30) : Colors.white,
            borderRadius: BorderRadius.circular(14),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withValues(alpha: 0.04),
                blurRadius: 8,
                offset: const Offset(0, 2),
              ),
            ],
          ),
          child: Row(
            children: [
              Icon(
                Icons.search,
                size: 20,
                color: theme.colorScheme.onSurface.withValues(alpha: 0.35),
              ),
              const SizedBox(width: 10),
              Text(
                '搜索书名、作者...',
                style: TextStyle(
                  fontSize: 14,
                  color: theme.colorScheme.onSurface.withValues(alpha: 0.35),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildContinueReading(ThemeData theme, bool isDark) {
    final entry = _lastRead!;
    final progressData = _progressService.get(entry.book.id);
    final progressPercent = progressData != null
        ? (progressData.within * 100).round()
        : 0;

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
      child: GestureDetector(
        onTap: _navigatingToReader ? null : () => _openContinueReading(entry),
        child: Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: isDark
                  ? [const Color(0xFF2A2545), const Color(0xFF1E1E30)]
                  : [const Color(0xFFF0EDFF), const Color(0xFFE8E4FF)],
            ),
            borderRadius: BorderRadius.circular(16),
            boxShadow: [
              BoxShadow(
                color: AppTheme.seedPurple.withValues(alpha: 0.08),
                blurRadius: 12,
                offset: const Offset(0, 4),
              ),
            ],
          ),
          child: Row(
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: Image.network(
                  _api.coverUrl(entry.book.id),
                  width: 48,
                  height: 64,
                  fit: BoxFit.cover,
                  errorBuilder: (_, __, ___) => Container(
                    width: 48,
                    height: 64,
                    decoration: BoxDecoration(
                      color: AppTheme.seedPurple.withValues(alpha: 0.15),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Center(
                      child: Text(
                        entry.book.title.length > 1
                            ? entry.book.title.substring(0, 1)
                            : entry.book.title,
                        style: const TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w700,
                          color: AppTheme.seedPurple,
                        ),
                      ),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Row(
                      children: [
                        Icon(
                          Icons.history,
                          size: 14,
                          color: AppTheme.seedPurple,
                        ),
                        const SizedBox(width: 4),
                        Text(
                          '继续阅读',
                          style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.w600,
                            color: AppTheme.seedPurple,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text(
                      entry.book.title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.titleSmall?.copyWith(
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      entry.lastChapterTitle,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 12,
                        color: theme.colorScheme.onSurface.withValues(
                          alpha: 0.5,
                        ),
                      ),
                    ),
                    if (progressPercent > 0) ...[
                      const SizedBox(height: 6),
                      Row(
                        children: [
                          Expanded(
                            child: ClipRRect(
                              borderRadius: BorderRadius.circular(3),
                              child: LinearProgressIndicator(
                                value: progressData!.within,
                                minHeight: 4,
                                backgroundColor: AppTheme.seedPurple.withValues(
                                  alpha: 0.1,
                                ),
                                valueColor: const AlwaysStoppedAnimation(
                                  AppTheme.seedPurple,
                                ),
                              ),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Text(
                            '$progressPercent%',
                            style: const TextStyle(
                              fontSize: 10,
                              fontWeight: FontWeight.w600,
                              color: AppTheme.seedPurple,
                            ),
                          ),
                        ],
                      ),
                    ],
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: AppTheme.seedPurple.withValues(alpha: 0.1),
                  shape: BoxShape.circle,
                ),
                child: _navigatingToReader
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: AppTheme.seedPurple,
                        ),
                      )
                    : const Icon(
                        Icons.play_arrow_rounded,
                        size: 20,
                        color: AppTheme.seedPurple,
                      ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildHero(ThemeData theme) {
    final heroBooks = _featured.take(6).toList();
    if (heroBooks.isEmpty) {
      return Container(
        margin: const EdgeInsets.fromLTRB(12, 8, 12, 0),
        padding: const EdgeInsets.all(20),
        decoration: BoxDecoration(
          gradient: const LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [Color(0xFF6C5CE7), Color(0xFF8B7CF6), Color(0xFFA29BFE)],
          ),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Text(
          '好故事正在发生',
          style: theme.textTheme.titleMedium?.copyWith(
            color: Colors.white,
            fontWeight: FontWeight.w800,
          ),
        ),
      );
    }
    final idx = _heroIndex % heroBooks.length;
    final book = heroBooks[idx];
    final coverUrl = book.coverUrl != null
        ? _api.fullCoverUrl(book.coverUrl)
        : _api.coverUrl(book.id);
    final isDark = theme.brightness == Brightness.dark;
    final statusText = book.status == 'finished' ? '已完结' : '连载中';
    final statusColor = book.status == 'finished'
        ? const Color(0xFF238760)
        : const Color(0xFF238AC8);
    final statusBg = book.status == 'finished'
        ? const Color(0xFFE7F7F0)
        : const Color(0xFFE7F4FC);
    final synopsis = heroSynopsisFor(book);

    return GestureDetector(
      onTap: () => Navigator.of(context).push(
        MaterialPageRoute(builder: (_) => BookDetailScreen(bookId: book.id)),
      ),
      child: Container(
        margin: const EdgeInsets.fromLTRB(12, 6, 12, 0),
        decoration: BoxDecoration(
          gradient: isDark
              ? null
              : const LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: [
                    Color(0xFFF4FAFF),
                    Color(0xFFEAF5FF),
                    Color(0xFFDBEEFF),
                  ],
                ),
          color: isDark ? const Color(0xFF1A1A2E) : null,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: isDark ? Colors.white10 : const Color(0xFFDBEAFA),
          ),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.06),
              blurRadius: 12,
              offset: const Offset(0, 4),
            ),
          ],
        ),
        child: LayoutBuilder(
          builder: (context, constraints) {
            final coverWidth = constraints.maxWidth < 340 ? 104.0 : 120.0;

            return IntrinsicHeight(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  ClipRRect(
                    borderRadius: const BorderRadius.only(
                      topLeft: Radius.circular(12),
                      bottomLeft: Radius.circular(12),
                    ),
                    child: AnimatedSwitcher(
                      duration: const Duration(milliseconds: 400),
                      child: Image.network(
                        coverUrl,
                        key: ValueKey(book.id),
                        width: coverWidth,
                        fit: BoxFit.cover,
                        errorBuilder: (_, __, ___) => Container(
                          width: coverWidth,
                          decoration: BoxDecoration(
                            gradient: LinearGradient(
                              begin: Alignment.topLeft,
                              end: Alignment.bottomRight,
                              colors: [
                                theme.colorScheme.primaryContainer,
                                theme.colorScheme.tertiaryContainer,
                              ],
                            ),
                          ),
                          child: Center(
                            child: Text(
                              book.title.length > 2
                                  ? book.title.substring(0, 2)
                                  : book.title,
                              style: TextStyle(
                                fontSize: 16,
                                fontWeight: FontWeight.w700,
                                color: theme.colorScheme.onPrimaryContainer,
                              ),
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                  Expanded(
                    child: Padding(
                      padding: const EdgeInsets.fromLTRB(12, 10, 12, 8),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            children: [
                              Flexible(
                                child: Container(
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: 6,
                                    vertical: 1,
                                  ),
                                  decoration: BoxDecoration(
                                    color: AppTheme.seedPurple.withValues(
                                      alpha: 0.12,
                                    ),
                                    borderRadius: BorderRadius.circular(4),
                                  ),
                                  child: Text(
                                    book.category ?? '小说',
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                    style: const TextStyle(
                                      fontSize: 9,
                                      fontWeight: FontWeight.w700,
                                      color: AppTheme.seedPurple,
                                    ),
                                  ),
                                ),
                              ),
                              const SizedBox(width: 6),
                              Container(
                                padding: const EdgeInsets.symmetric(
                                  horizontal: 4,
                                  vertical: 1,
                                ),
                                decoration: BoxDecoration(
                                  color: statusBg,
                                  borderRadius: BorderRadius.circular(4),
                                ),
                                child: Text(
                                  statusText,
                                  style: TextStyle(
                                    fontSize: 8,
                                    fontWeight: FontWeight.w700,
                                    color: statusColor,
                                  ),
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 6),
                          Text(
                            book.title,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: theme.textTheme.titleSmall?.copyWith(
                              fontWeight: FontWeight.w700,
                              fontSize: 15,
                              letterSpacing: -0.01,
                            ),
                          ),
                          const SizedBox(height: 4),
                          Text(
                            [
                              book.author,
                              if (book.wordCount != null)
                                _formatWordCount(book.wordCount!),
                              heroChapterCountLabelFor(book),
                            ].join(' · '),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              fontSize: 10,
                              color: theme.colorScheme.onSurface.withValues(
                                alpha: 0.5,
                              ),
                            ),
                          ),
                          const SizedBox(height: 6),
                          Text(
                            synopsis,
                            maxLines: 3,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              fontSize: 11,
                              height: 1.4,
                              color: theme.colorScheme.onSurface.withValues(
                                alpha: isDark ? 0.72 : 0.66,
                              ),
                            ),
                          ),
                          if (heroBooks.length > 1) ...[
                            const Spacer(),
                            const SizedBox(height: 8),
                            SizedBox(
                              height: 29,
                              child: SingleChildScrollView(
                                scrollDirection: Axis.horizontal,
                                child: Row(
                                  children: List.generate(heroBooks.length, (
                                    i,
                                  ) {
                                    final tabBook = heroBooks[i];
                                    final tabCover = tabBook.coverUrl != null
                                        ? _api.fullCoverUrl(tabBook.coverUrl)
                                        : _api.coverUrl(tabBook.id);
                                    final isActive = i == idx;
                                    return GestureDetector(
                                      onTap: () {
                                        setState(() => _heroIndex = i);
                                        _startHeroTimer();
                                      },
                                      child: AnimatedOpacity(
                                        duration: const Duration(
                                          milliseconds: 200,
                                        ),
                                        opacity: isActive ? 1.0 : 0.4,
                                        child: Container(
                                          width: 20,
                                          height: 27,
                                          margin: EdgeInsets.only(
                                            right: i < heroBooks.length - 1
                                                ? 2
                                                : 0,
                                          ),
                                          decoration: BoxDecoration(
                                            borderRadius: BorderRadius.circular(
                                              3,
                                            ),
                                            border: Border.all(
                                              color: isActive
                                                  ? AppTheme.seedPurple
                                                  : Colors.transparent,
                                              width: 1.5,
                                            ),
                                          ),
                                          child: ClipRRect(
                                            borderRadius: BorderRadius.circular(
                                              2,
                                            ),
                                            child: Image.network(
                                              tabCover,
                                              fit: BoxFit.cover,
                                              errorBuilder: (_, __, ___) =>
                                                  Container(
                                                    decoration: BoxDecoration(
                                                      gradient: LinearGradient(
                                                        colors: [
                                                          theme
                                                              .colorScheme
                                                              .primaryContainer,
                                                          theme
                                                              .colorScheme
                                                              .tertiaryContainer,
                                                        ],
                                                      ),
                                                    ),
                                                  ),
                                            ),
                                          ),
                                        ),
                                      ),
                                    );
                                  }),
                                ),
                              ),
                            ),
                          ],
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            );
          },
        ),
      ),
    );
  }

  Widget _buildStats(ThemeData theme, bool isDark) {
    final bookTotal = _stats['readable_total'] ?? _stats['book_total'] ?? 0;
    final catTotal = _stats['category_total'] ?? 0;
    final deconTotal = _stats['deconstruction_total'] ?? 0;

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
      child: Row(
        children: [
          _statCard(
            theme,
            isDark,
            _formatNum(bookTotal),
            '可读作品',
            Icons.menu_book_rounded,
            const Color(0xFF6C5CE7),
          ),
          const SizedBox(width: 8),
          _statCard(
            theme,
            isDark,
            _formatNum(catTotal),
            '题材分类',
            Icons.category_rounded,
            const Color(0xFFFD79A8),
          ),
          const SizedBox(width: 8),
          _statCard(
            theme,
            isDark,
            _formatNum(deconTotal),
            '拆书档案',
            Icons.analytics_rounded,
            const Color(0xFF00B894),
          ),
        ],
      ),
    );
  }

  Widget _statCard(
    ThemeData theme,
    bool isDark,
    String value,
    String label,
    IconData icon,
    Color accent,
  ) {
    final cardColor = isDark ? const Color(0xFF1E1E30) : Colors.white;
    return Expanded(
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 14),
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
            Icon(icon, size: 18, color: accent),
            const SizedBox(height: 6),
            Text(
              value,
              style: TextStyle(
                fontSize: 18,
                fontWeight: FontWeight.w800,
                color: accent,
              ),
            ),
            const SizedBox(height: 2),
            Text(
              label,
              style: TextStyle(
                fontSize: 10,
                color: theme.colorScheme.onSurface.withValues(alpha: 0.5),
              ),
            ),
          ],
        ),
      ),
    );
  }

  String _formatNum(dynamic n) {
    final v = (n is int) ? n : int.tryParse(n.toString()) ?? 0;
    if (v >= 10000) return '${(v / 10000).toStringAsFixed(0)}万';
    return v.toString();
  }

  @override
  void _startHeroTimer() {
    _heroTimer?.cancel();
    final heroBooks = _featured.take(6).toList();
    if (heroBooks.length <= 1) return;
    _heroTimer = Timer.periodic(const Duration(seconds: 5), (_) {
      if (mounted)
        setState(() => _heroIndex = (_heroIndex + 1) % heroBooks.length);
    });
  }

  @override
  void dispose() {
    _heroTimer?.cancel();
    _api.dispose();
    super.dispose();
  }
}
