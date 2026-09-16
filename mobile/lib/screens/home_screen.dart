import 'dart:async';
import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../services/local_storage_service.dart';
import '../services/reading_progress.dart';
import '../models/book.dart';
import '../theme/app_theme.dart';
import '../widgets/home_hero_carousel.dart';
import '../widgets/ooh_ui.dart';
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

double heroCoverWidthFor(double availableWidth) {
  return (availableWidth * 0.35).clamp(124.0, 200.0).toDouble();
}

double heroCoverHeightFor(double availableWidth) {
  return heroCoverWidthFor(availableWidth) * 1.42;
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
  Map<String, List<Book>> _categoryBooks = {};
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
      if (mounted) {
        setState(() {
          _featured = books;
          _recommendations = recs;
          _longNovels = longs;
          _categoryBooks = catBooks;
          _lastRead = history.isNotEmpty ? history.first : null;
          _loading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = e.toString();
          _loading = false;
        });
      }
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
    if (_loading) return const OohLoadingState(itemCount: 8);
    if (_error != null) {
      return OohMessageState(
        icon: Icons.cloud_off_rounded,
        title: '暂时无法连接',
        message: '网络恢复后即可继续浏览，已下载的内容仍可离线阅读。',
        actionLabel: '重新连接',
        onAction: () {
          setState(() {
            _loading = true;
            _error = null;
          });
          _load();
        },
      );
    }

    return RefreshIndicator(
      onRefresh: _load,
      child: CustomScrollView(
        slivers: [
          SliverToBoxAdapter(child: _buildSearchBar(theme, isDark)),
          SliverToBoxAdapter(
            child: _featured.isEmpty
                ? _buildHero(theme)
                : _buildEditorialHero(theme),
          ),
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
            SliverToBoxAdapter(child: _buildLongNovelList(theme)),
          ],
          if (_categoryBooks.isNotEmpty)
            SliverToBoxAdapter(child: _buildCategoryRecommendations(theme)),
          SliverToBoxAdapter(
            child: _sectionHeader(
              theme,
              '新书入库',
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
    return LayoutBuilder(
      builder: (context, constraints) {
        final tablet = constraints.maxWidth >= 720;
        final coverWidth = tablet ? 132.0 : 112.0;
        final coverHeight = coverWidth * 1.5;
        final horizontal = OohPageMetrics.horizontalPadding(
          constraints.maxWidth,
        );
        return SizedBox(
          height: coverHeight + 74,
          child: ListView.separated(
            scrollDirection: Axis.horizontal,
            padding: EdgeInsets.symmetric(horizontal: horizontal),
            itemCount: books.length,
            separatorBuilder: (_, __) => SizedBox(width: tablet ? 18 : 13),
            itemBuilder: (context, i) {
              final book = books[i];
              final coverUrl = book.coverUrl != null
                  ? _api.fullCoverUrl(book.coverUrl)
                  : _api.coverUrl(book.id);
              return Semantics(
                button: true,
                label: '打开《${book.title}》',
                child: InkWell(
                  borderRadius: BorderRadius.circular(14),
                  onTap: () => Navigator.of(context).push(
                    MaterialPageRoute(
                      builder: (_) => BookDetailScreen(bookId: book.id),
                    ),
                  ),
                  child: SizedBox(
                    width: coverWidth,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        OohBookCover(
                          imageUrl: coverUrl,
                          title: book.title,
                          width: coverWidth,
                          height: coverHeight,
                        ),
                        const SizedBox(height: 10),
                        Text(
                          book.title,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.bodyMedium?.copyWith(
                            fontWeight: FontWeight.w700,
                            height: 1.2,
                          ),
                        ),
                        const SizedBox(height: 3),
                        Text(
                          book.author,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.bodySmall,
                        ),
                      ],
                    ),
                  ),
                ),
              );
            },
          ),
        );
      },
    );
  }

  Widget _buildLongNovelList(ThemeData theme) {
    final books = _longNovels.take(4).toList();
    return LayoutBuilder(
      builder: (context, constraints) {
        final horizontal = OohPageMetrics.horizontalPadding(
          constraints.maxWidth,
        );
        return Padding(
          padding: EdgeInsets.symmetric(horizontal: horizontal),
          child: Column(
            children: List.generate(books.length, (index) {
              final book = books[index];
              final coverUrl = book.coverUrl != null
                  ? _api.fullCoverUrl(book.coverUrl)
                  : _api.coverUrl(book.id);
              return Column(
                children: [
                  InkWell(
                    borderRadius: BorderRadius.circular(12),
                    onTap: () => Navigator.of(context).push(
                      MaterialPageRoute(
                        builder: (_) => BookDetailScreen(bookId: book.id),
                      ),
                    ),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(vertical: 12),
                      child: Row(
                        children: [
                          OohBookCover(
                            imageUrl: coverUrl,
                            title: book.title,
                            width: 72,
                            height: 108,
                            borderRadius: BorderRadius.circular(9),
                          ),
                          const SizedBox(width: 16),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  book.title,
                                  maxLines: 2,
                                  overflow: TextOverflow.ellipsis,
                                  style: theme.textTheme.titleMedium?.copyWith(
                                    height: 1.28,
                                  ),
                                ),
                                if (book.description?.isNotEmpty == true) ...[
                                  const SizedBox(height: 7),
                                  Text(
                                    book.description!,
                                    maxLines: 2,
                                    overflow: TextOverflow.ellipsis,
                                    style: theme.textTheme.bodySmall?.copyWith(
                                      height: 1.45,
                                    ),
                                  ),
                                ],
                                const SizedBox(height: 10),
                                Text(
                                  [
                                    book.author,
                                    if (book.wordCount != null)
                                      _formatWordCount(book.wordCount!),
                                    book.status == 'finished' ? '完结' : '连载',
                                  ].join('  '),
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: theme.textTheme.labelSmall?.copyWith(
                                    color: theme.colorScheme.onSurfaceVariant,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                  if (index < books.length - 1)
                    Divider(height: 1, color: theme.colorScheme.outlineVariant),
                ],
              );
            }),
          ),
        );
      },
    );
  }

  Widget _buildCategoryRecommendations(ThemeData theme) {
    final sortedEntries = _categoryBooks.entries.toList()
      ..sort((a, b) => b.value.length.compareTo(a.value.length));
    final catNames = sortedEntries.take(2).map((e) => e.key).toList();
    return Padding(
      padding: EdgeInsets.zero,
      child: Column(
        children: catNames.map((catName) {
          final books = (_categoryBooks[catName] ?? []).take(8).toList();
          if (books.isEmpty) return const SizedBox.shrink();
          return Padding(
            padding: const EdgeInsets.only(bottom: 16),
            child: Column(
              children: [
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  child: Row(
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
                              body: LibraryScreen(initialCategory: catName),
                            ),
                          ),
                        ),
                        child: Text(
                          '查看全部',
                          style: TextStyle(
                            fontSize: 12,
                            color: theme.colorScheme.primary,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 10),
                SizedBox(
                  height: 226,
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
                          width: 112,
                          margin: EdgeInsets.only(
                            right: i < books.length - 1 ? 10 : 0,
                          ),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              OohBookCover(
                                imageUrl: coverUrl,
                                title: book.title,
                                width: 112,
                                height: 168,
                                borderRadius: BorderRadius.circular(9),
                              ),
                              const SizedBox(height: 8),
                              Text(
                                book.title,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: theme.textTheme.bodySmall?.copyWith(
                                  fontWeight: FontWeight.w500,
                                  fontSize: 13,
                                  height: 1.25,
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
    if (count >= 10000) {
      return '${(count / 10000).toStringAsFixed(count >= 1000000 ? 0 : 1)}万字';
    }
    return '$count字';
  }

  Widget _sectionHeader(
    ThemeData theme,
    String title,
    String kicker,
    IconData icon, {
    Color? color,
  }) {
    return OohSectionHeader(title: title, subtitle: kicker);
  }

  Widget _buildSearchBar(ThemeData theme, bool isDark) {
    return LayoutBuilder(
      builder: (context, constraints) => Padding(
        padding: EdgeInsets.fromLTRB(
          OohPageMetrics.horizontalPadding(constraints.maxWidth),
          12,
          OohPageMetrics.horizontalPadding(constraints.maxWidth),
          0,
        ),
        child: SearchBar(
          hintText: '搜索书名、作者或分类',
          leading: const Icon(Icons.search_rounded),
          trailing: const [Icon(Icons.arrow_forward_rounded, size: 18)],
          onTap: () => Navigator.of(context).push(
            MaterialPageRoute(
              builder: (_) => Scaffold(
                appBar: AppBar(title: const Text('书库')),
                body: const LibraryScreen(),
              ),
            ),
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

    return LayoutBuilder(
      builder: (context, constraints) => Padding(
        padding: EdgeInsets.fromLTRB(
          OohPageMetrics.horizontalPadding(constraints.maxWidth),
          14,
          OohPageMetrics.horizontalPadding(constraints.maxWidth),
          0,
        ),
        child: OohSurface(
          onTap: _navigatingToReader ? null : () => _openContinueReading(entry),
          padding: const EdgeInsets.all(16),
          child: Row(
            children: [
              OohBookCover(
                imageUrl: _api.coverUrl(entry.book.id),
                title: entry.book.title,
                width: 52,
                height: 72,
                borderRadius: BorderRadius.circular(9),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Row(
                      children: [
                        Text(
                          '继续阅读',
                          style: theme.textTheme.labelMedium?.copyWith(
                            color: theme.colorScheme.primary,
                            fontWeight: FontWeight.w700,
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
                      const SizedBox(height: 5),
                      Text(
                        '本章进度 $progressPercent%',
                        style: theme.textTheme.bodySmall?.copyWith(
                          color: theme.colorScheme.primary,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              const SizedBox(width: 8),
              _navigatingToReader
                  ? const SizedBox(
                      width: 22,
                      height: 22,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : Icon(
                      Icons.arrow_forward_rounded,
                      color: theme.colorScheme.primary,
                    ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildEditorialHero(ThemeData theme) {
    final carouselBooks = _featured
        .take(oohHomeHeroBookLimit)
        .toList(growable: false);
    if (carouselBooks.isNotEmpty) {
      return OohHomeHeroCarousel(
        books: carouselBooks,
        coverUrlFor: (book) => book.coverUrl != null
            ? _api.fullCoverUrl(book.coverUrl)
            : _api.coverUrl(book.id),
        synopsisFor: heroSynopsisFor,
        chapterLabelFor: heroChapterCountLabelFor,
        onOpen: (book) => Navigator.of(context).push(
          MaterialPageRoute(builder: (_) => BookDetailScreen(bookId: book.id)),
        ),
      );
    }
    final heroBooks = _featured.take(oohHomeHeroBookLimit).toList();
    if (heroBooks.isEmpty) return const SizedBox.shrink();
    final book = heroBooks[_heroIndex % heroBooks.length];
    final coverUrl = book.coverUrl != null
        ? _api.fullCoverUrl(book.coverUrl)
        : _api.coverUrl(book.id);
    final synopsis = heroSynopsisFor(book);

    return LayoutBuilder(
      builder: (context, constraints) {
        final horizontal = OohPageMetrics.horizontalPadding(
          constraints.maxWidth,
        );
        final tablet = constraints.maxWidth >= 720;
        final coverWidth = tablet ? 138.0 : 106.0;
        final coverHeight = coverWidth * 1.5;
        return Padding(
          padding: EdgeInsets.fromLTRB(horizontal, 14, horizontal, 0),
          child: Semantics(
            button: true,
            label: '打开编辑精选《${book.title}》',
            child: AnimatedSwitcher(
              duration: const Duration(milliseconds: 220),
              child: OohSurface(
                key: ValueKey(book.id),
                onTap: () => Navigator.of(context).push(
                  MaterialPageRoute(
                    builder: (_) => BookDetailScreen(bookId: book.id),
                  ),
                ),
                padding: EdgeInsets.zero,
                child: SizedBox(
                  height: tablet ? 226 : 196,
                  child: Stack(
                    children: [
                      Positioned.fill(
                        left: constraints.maxWidth * .46,
                        child: Opacity(
                          opacity: theme.brightness == Brightness.dark
                              ? .13
                              : .08,
                          child: OohNetworkImage(
                            imageUrl: coverUrl,
                            fit: BoxFit.cover,
                            alignment: Alignment.topCenter,
                            error: const SizedBox.shrink(),
                          ),
                        ),
                      ),
                      Positioned.fill(
                        child: DecoratedBox(
                          decoration: BoxDecoration(
                            gradient: LinearGradient(
                              begin: Alignment.centerLeft,
                              end: Alignment.centerRight,
                              colors: [
                                theme.colorScheme.surface,
                                theme.colorScheme.surface.withValues(
                                  alpha: .96,
                                ),
                                theme.colorScheme.surface.withValues(
                                  alpha: .68,
                                ),
                              ],
                              stops: const [0, .62, 1],
                            ),
                          ),
                        ),
                      ),
                      Padding(
                        padding: EdgeInsets.all(tablet ? 20 : 16),
                        child: Row(
                          children: [
                            OohBookCover(
                              imageUrl: coverUrl,
                              title: book.title,
                              width: coverWidth,
                              height: coverHeight,
                              borderRadius: BorderRadius.circular(9),
                            ),
                            SizedBox(width: tablet ? 24 : 17),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    '编辑精选',
                                    style: theme.textTheme.labelMedium
                                        ?.copyWith(
                                          color: theme.colorScheme.primary,
                                          fontWeight: FontWeight.w800,
                                        ),
                                  ),
                                  SizedBox(height: tablet ? 8 : 6),
                                  Text(
                                    book.title,
                                    maxLines: 2,
                                    overflow: TextOverflow.ellipsis,
                                    style: theme.textTheme.headlineSmall
                                        ?.copyWith(
                                          fontWeight: FontWeight.w800,
                                          height: 1.16,
                                          letterSpacing: -.4,
                                        ),
                                  ),
                                  const SizedBox(height: 6),
                                  Text(
                                    book.author,
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                    style: theme.textTheme.bodySmall,
                                  ),
                                  if (tablet) ...[
                                    const SizedBox(height: 9),
                                    Text(
                                      synopsis,
                                      maxLines: 2,
                                      overflow: TextOverflow.ellipsis,
                                      style: theme.textTheme.bodySmall
                                          ?.copyWith(height: 1.45),
                                    ),
                                  ],
                                  const Spacer(),
                                  Row(
                                    children: [
                                      Expanded(
                                        child: Text(
                                          [
                                            if (book.category != null)
                                              book.category!,
                                            heroChapterCountLabelFor(book),
                                            book.status == 'finished'
                                                ? '完结'
                                                : '连载',
                                          ].join('  '),
                                          maxLines: 1,
                                          overflow: TextOverflow.ellipsis,
                                          style: theme.textTheme.labelSmall,
                                        ),
                                      ),
                                      Icon(
                                        Icons.arrow_forward_rounded,
                                        size: 20,
                                        color: theme.colorScheme.primary,
                                      ),
                                    ],
                                  ),
                                ],
                              ),
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
      },
    );
  }

  Widget _buildHero(ThemeData theme) {
    final heroBooks = _featured.take(oohHomeHeroBookLimit).toList();
    if (heroBooks.isEmpty) {
      return LayoutBuilder(
        builder: (context, constraints) => Padding(
          padding: EdgeInsets.fromLTRB(
            OohPageMetrics.horizontalPadding(constraints.maxWidth),
            14,
            OohPageMetrics.horizontalPadding(constraints.maxWidth),
            0,
          ),
          child: OohSurface(
            child: Row(
              children: [
                Icon(
                  Icons.auto_stories_outlined,
                  color: theme.colorScheme.primary,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    '好故事正在整理，稍后下拉刷新即可查看。',
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      );
    }
    final idx = _heroIndex % heroBooks.length;
    final book = heroBooks[idx];
    final coverUrl = book.coverUrl != null
        ? _api.fullCoverUrl(book.coverUrl)
        : _api.coverUrl(book.id);
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
          gradient: AppTheme.heroGradient,
          borderRadius: BorderRadius.circular(AppTheme.cardRadius),
          border: Border.all(color: Colors.white.withValues(alpha: .14)),
          boxShadow: [AppTheme.softShadow(theme.brightness)],
        ),
        child: LayoutBuilder(
          builder: (context, constraints) {
            final coverWidth = heroCoverWidthFor(constraints.maxWidth);
            final coverHeight = heroCoverHeightFor(constraints.maxWidth);

            return SizedBox(
              height: coverHeight,
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  SizedBox(
                    width: coverWidth,
                    height: coverHeight,
                    child: DecoratedBox(
                      decoration: BoxDecoration(color: AppTheme.brandNavy),
                      child: ClipRRect(
                        borderRadius: const BorderRadius.only(
                          topLeft: Radius.circular(AppTheme.cardRadius),
                          bottomLeft: Radius.circular(AppTheme.cardRadius),
                        ),
                        child: AnimatedSwitcher(
                          duration: const Duration(milliseconds: 400),
                          child: OohNetworkImage(
                            imageUrl: coverUrl,
                            key: ValueKey(book.id),
                            width: coverWidth,
                            height: coverHeight,
                            fit: BoxFit.cover,
                            error: Container(
                              width: coverWidth,
                              height: coverHeight,
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
                    ),
                  ),
                  Expanded(
                    child: Padding(
                      padding: const EdgeInsets.fromLTRB(20, 18, 16, 14),
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
                                    color: Colors.white.withValues(alpha: .14),
                                    borderRadius: BorderRadius.circular(8),
                                  ),
                                  child: Text(
                                    book.category ?? '小说',
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                    style: const TextStyle(
                                      fontSize: 11,
                                      fontWeight: FontWeight.w700,
                                      color: Colors.white,
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
                            style: theme.textTheme.titleLarge?.copyWith(
                              color: Colors.white,
                              fontWeight: FontWeight.w800,
                              fontSize: 20,
                              letterSpacing: -.4,
                            ),
                          ),
                          const SizedBox(height: 4),
                          Text(
                            [
                              book.author,
                              if (book.wordCount != null)
                                _formatWordCount(book.wordCount!),
                              heroChapterCountLabelFor(book),
                            ].join('  |  '),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              fontSize: 12,
                              color: Colors.white.withValues(alpha: .68),
                            ),
                          ),
                          const SizedBox(height: 6),
                          Text(
                            synopsis,
                            maxLines: 3,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              fontSize: 13,
                              height: 1.5,
                              color: Colors.white.withValues(alpha: .8),
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
                                                  ? Colors.white
                                                  : Colors.transparent,
                                              width: 1.5,
                                            ),
                                          ),
                                          child: ClipRRect(
                                            borderRadius: BorderRadius.circular(
                                              2,
                                            ),
                                            child: OohNetworkImage(
                                              imageUrl: tabCover,
                                              fit: BoxFit.cover,
                                              error: Container(
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

  void _startHeroTimer() {
    _heroTimer?.cancel();
    if (!mounted || MediaQuery.maybeDisableAnimationsOf(context) == true) {
      return;
    }
    final heroBooks = _featured.take(oohHomeHeroBookLimit).toList();
    if (heroBooks.length <= 1) return;
    _heroTimer = Timer.periodic(const Duration(seconds: 5), (_) {
      if (mounted) {
        setState(() => _heroIndex = (_heroIndex + 1) % heroBooks.length);
      }
    });
  }

  @override
  void dispose() {
    _heroTimer?.cancel();
    _api.dispose();
    super.dispose();
  }
}
