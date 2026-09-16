import 'package:flutter/material.dart';

import '../models/book.dart';
import '../services/account_service.dart';
import '../services/api_service.dart';
import '../services/local_storage_service.dart';
import '../widgets/ooh_ui.dart';
import 'book_detail_screen.dart';
import 'reader_screen.dart';

class ReadingBookshelfScreen extends StatefulWidget {
  final VoidCallback? onBrowse;
  final bool syncAccount;
  final ImageProvider? Function(String bookId)? coverProvider;

  const ReadingBookshelfScreen({
    super.key,
    this.onBrowse,
    this.syncAccount = true,
    this.coverProvider,
  });

  @override
  State<ReadingBookshelfScreen> createState() => _ReadingBookshelfScreenState();
}

class _ReadingBookshelfScreenState extends State<ReadingBookshelfScreen> {
  final _storage = LocalStorageService();
  final _api = ApiService();
  final _account = AccountService.instance;
  final _searchController = TextEditingController();

  List<_ShelfItem> _items = const [];
  bool _loading = true;
  String? _error;
  String _query = '';
  String? _openingBookId;

  @override
  void initState() {
    super.initState();
    if (widget.syncAccount) _account.addListener(_accountChanged);
    LocalStorageService.historyVersion.addListener(_historyChanged);
    _load();
  }

  void _accountChanged() {
    if (!mounted) return;
    setState(() => _items = _composeItems());
  }

  void _historyChanged() {
    if (!mounted || _loading) return;
    setState(() => _items = _composeItems());
  }

  Future<void> _load() async {
    if (mounted) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      await _storage.init();
      if (widget.syncAccount) await _account.initialize();
      if (widget.syncAccount && _account.isSignedIn) {
        await _account.mergeLocalState(_storage);
      }
      if (!mounted) return;
      setState(() {
        _items = _composeItems();
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _items = _composeItems();
        _loading = false;
        _error = '云端书架暂时无法同步，本机阅读记录仍可使用';
      });
    }
  }

  List<_ShelfItem> _composeItems() {
    final byId = <String, _ShelfItem>{};
    for (final entry in _storage.getHistory()) {
      byId[entry.book.id] = _ShelfItem.fromLocal(entry);
    }

    final cloudHistory = <String, Map<String, dynamic>>{};
    for (final raw in _account.cloudState['history'] as List? ?? const []) {
      if (raw is! Map) continue;
      final item = Map<String, dynamic>.from(raw);
      final id = item['book_id'] as String? ?? '';
      if (id.isNotEmpty) cloudHistory[id] = item;
    }

    // Account reading history is included in the bookshelf even for records
    // created before automatic shelf promotion was introduced.
    final cloudCandidates = <String, Map<String, dynamic>>{...cloudHistory};
    for (final raw in _account.cloudState['bookshelf'] as List? ?? const []) {
      if (raw is! Map) continue;
      final item = Map<String, dynamic>.from(raw);
      final id = item['book_id'] as String? ?? '';
      if (id.isNotEmpty) cloudCandidates[id] = item;
    }

    for (final candidate in cloudCandidates.entries) {
      final history = cloudHistory[candidate.key];
      final cloud = _ShelfItem.fromCloud(candidate.value, history: history);
      final local = byId[candidate.key];
      if (local == null || cloud.lastReadAt > local.lastReadAt) {
        byId[candidate.key] = cloud;
      } else if (history != null) {
        byId[candidate.key] = local.withAuthoritativeMetadata(candidate.value);
      }
    }

    final items = byId.values.toList()
      ..sort((left, right) => right.lastReadAt.compareTo(left.lastReadAt));
    return items;
  }

  List<_ShelfItem> get _filteredItems {
    final query = _query.toLowerCase();
    if (query.isEmpty) return _items;
    return _items
        .where(
          (item) =>
              item.title.toLowerCase().contains(query) ||
              item.author.toLowerCase().contains(query) ||
              item.lastChapterTitle.toLowerCase().contains(query),
        )
        .toList();
  }

  Future<void> _openBook(_ShelfItem item) async {
    if (_openingBookId != null) return;
    setState(() => _openingBookId = item.bookId);
    try {
      final results = await Future.wait([
        _api.getBook(item.bookId),
        _api.getChapterCatalog(item.bookId),
      ]);
      if (!mounted) return;
      final book = results[0] as Book;
      final catalog = results[1] as ChapterCatalog;
      final chapters = catalog.chapters;
      if (chapters.isEmpty || item.lastChapterId.isEmpty) {
        await Navigator.of(context).push(
          MaterialPageRoute(
            builder: (_) => BookDetailScreen(bookId: item.bookId),
          ),
        );
      } else {
        Chapter target = chapters.first;
        for (final chapter in chapters) {
          if (chapter.id == item.lastChapterId ||
              chapter.position == item.lastChapterPosition) {
            target = chapter;
            break;
          }
        }
        await Navigator.of(context).push(
          MaterialPageRoute(
            builder: (_) => ReaderScreen(
              bookId: item.bookId,
              chapterId: target.id,
              chapters: chapters,
              book: book,
            ),
          ),
        );
      }
      if (mounted) setState(() => _items = _composeItems());
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('暂时无法打开作品，请检查网络后重试')));
    } finally {
      if (mounted) setState(() => _openingBookId = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return RefreshIndicator(
      onRefresh: _load,
      child: LayoutBuilder(
        builder: (context, constraints) {
          final horizontal = OohPageMetrics.horizontalPadding(
            constraints.maxWidth,
          );
          final columns = OohPageMetrics.gridColumns(constraints.maxWidth);
          final gap = constraints.maxWidth >= 620 ? 18.0 : 12.0;
          final cellWidth =
              (constraints.maxWidth - horizontal * 2 - gap * (columns - 1)) /
              columns;
          final visible = _filteredItems;

          return CustomScrollView(
            physics: const AlwaysScrollableScrollPhysics(),
            slivers: [
              SliverToBoxAdapter(
                child: Padding(
                  padding: EdgeInsets.fromLTRB(horizontal, 14, horizontal, 0),
                  child: Text(
                    '读过的作品会自动加入这里，并保留最后章节与全书进度。',
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                ),
              ),
              SliverToBoxAdapter(
                child: Padding(
                  padding: EdgeInsets.fromLTRB(horizontal, 14, horizontal, 8),
                  child: SearchBar(
                    controller: _searchController,
                    hintText: '搜索书架中的书名、作者或章节',
                    leading: const Icon(Icons.search_rounded),
                    trailing: _query.isEmpty
                        ? const []
                        : [
                            IconButton(
                              tooltip: '清除搜索',
                              onPressed: () {
                                _searchController.clear();
                                setState(() => _query = '');
                              },
                              icon: const Icon(Icons.close_rounded),
                            ),
                          ],
                    onChanged: (value) => setState(() => _query = value.trim()),
                  ),
                ),
              ),
              if (_error != null)
                SliverToBoxAdapter(
                  child: Padding(
                    padding: EdgeInsets.fromLTRB(horizontal, 4, horizontal, 8),
                    child: Row(
                      children: [
                        Icon(
                          Icons.cloud_off_rounded,
                          size: 17,
                          color: theme.colorScheme.onSurfaceVariant,
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            _error!,
                            style: theme.textTheme.bodySmall,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              if (_loading)
                SliverPadding(
                  padding: EdgeInsets.fromLTRB(horizontal, 12, horizontal, 28),
                  sliver: SliverGrid.builder(
                    gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                      crossAxisCount: columns,
                      crossAxisSpacing: gap,
                      mainAxisSpacing: 22,
                      mainAxisExtent: cellWidth * 1.42 + 82,
                    ),
                    itemCount: columns * 2,
                    itemBuilder: (_, __) => _ShelfSkeleton(width: cellWidth),
                  ),
                )
              else if (visible.isEmpty)
                SliverFillRemaining(
                  hasScrollBody: false,
                  child: _EmptyShelf(
                    hasQuery: _query.isNotEmpty,
                    onBrowse: widget.onBrowse,
                  ),
                )
              else ...[
                SliverToBoxAdapter(
                  child: OohSectionHeader(
                    title: _query.isEmpty ? '最近阅读' : '搜索结果',
                    subtitle: '${visible.length} 本',
                  ),
                ),
                SliverPadding(
                  padding: EdgeInsets.fromLTRB(horizontal, 0, horizontal, 30),
                  sliver: SliverGrid.builder(
                    gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                      crossAxisCount: columns,
                      crossAxisSpacing: gap,
                      mainAxisSpacing: 22,
                      mainAxisExtent: cellWidth * 1.42 + 82,
                    ),
                    itemCount: visible.length,
                    itemBuilder: (context, index) {
                      final item = visible[index];
                      return _ShelfBookTile(
                        item: item,
                        width: cellWidth,
                        opening: _openingBookId == item.bookId,
                        coverUrl: _api.coverUrl(item.bookId),
                        coverProvider: widget.coverProvider?.call(item.bookId),
                        onTap: () => _openBook(item),
                      );
                    },
                  ),
                ),
              ],
            ],
          );
        },
      ),
    );
  }

  @override
  void dispose() {
    if (widget.syncAccount) _account.removeListener(_accountChanged);
    LocalStorageService.historyVersion.removeListener(_historyChanged);
    _searchController.dispose();
    _api.dispose();
    super.dispose();
  }
}

class _ShelfBookTile extends StatelessWidget {
  final _ShelfItem item;
  final double width;
  final String coverUrl;
  final ImageProvider? coverProvider;
  final bool opening;
  final VoidCallback onTap;

  const _ShelfBookTile({
    required this.item,
    required this.width,
    required this.coverUrl,
    this.coverProvider,
    required this.opening,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final percent = (item.overallProgress * 100).clamp(0, 100).round();
    return Semantics(
      button: true,
      label: '《${item.title}》，读到${item.lastChapterTitle}，全书进度$percent%',
      child: InkWell(
        onTap: opening ? null : onTap,
        borderRadius: BorderRadius.circular(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Stack(
              children: [
                OohBookCover(
                  imageUrl: coverUrl,
                  title: item.title,
                  width: width,
                  height: width * 1.42,
                  borderRadius: BorderRadius.circular(12),
                  imageProvider: coverProvider,
                ),
                if (opening)
                  Positioned.fill(
                    child: DecoratedBox(
                      decoration: BoxDecoration(
                        color: theme.colorScheme.scrim.withValues(alpha: .36),
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: const Center(
                        child: SizedBox.square(
                          dimension: 24,
                          child: CircularProgressIndicator(
                            strokeWidth: 2.4,
                            color: Colors.white,
                          ),
                        ),
                      ),
                    ),
                  ),
                Positioned(
                  left: 0,
                  right: 0,
                  bottom: 0,
                  child: ClipRRect(
                    borderRadius: const BorderRadius.vertical(
                      bottom: Radius.circular(12),
                    ),
                    child: LinearProgressIndicator(
                      value: item.overallProgress.clamp(0.0, 1.0),
                      minHeight: 4,
                      backgroundColor: theme.colorScheme.surfaceContainerHighest
                          .withValues(alpha: .72),
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              item.title,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.titleSmall?.copyWith(
                fontWeight: FontWeight.w700,
              ),
            ),
            const SizedBox(height: 2),
            Text(
              item.lastChapterTitle.isEmpty ? '尚未开始阅读' : item.lastChapterTitle,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
            const SizedBox(height: 2),
            Text(
              percent > 0 ? '全书 $percent%' : item.author,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.labelSmall?.copyWith(
                color: percent > 0
                    ? theme.colorScheme.primary
                    : theme.colorScheme.onSurfaceVariant,
                fontWeight: percent > 0 ? FontWeight.w700 : FontWeight.w500,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ShelfSkeleton extends StatelessWidget {
  final double width;

  const _ShelfSkeleton({required this.width});

  @override
  Widget build(BuildContext context) {
    final color = Theme.of(context).colorScheme.surfaceContainerHighest;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          width: width,
          height: width * 1.42,
          decoration: BoxDecoration(
            color: color,
            borderRadius: BorderRadius.circular(12),
          ),
        ),
        const SizedBox(height: 9),
        Container(width: width * .76, height: 12, color: color),
        const SizedBox(height: 7),
        Container(width: width * .56, height: 9, color: color),
      ],
    );
  }
}

class _EmptyShelf extends StatelessWidget {
  final bool hasQuery;
  final VoidCallback? onBrowse;

  const _EmptyShelf({required this.hasQuery, required this.onBrowse});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              hasQuery ? Icons.search_off_rounded : Icons.shelves,
              size: 52,
              color: theme.colorScheme.primary.withValues(alpha: .7),
            ),
            const SizedBox(height: 16),
            Text(
              hasQuery ? '没有找到匹配的作品' : '书架还是空的',
              style: theme.textTheme.titleMedium?.copyWith(
                fontWeight: FontWeight.w800,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              hasQuery ? '换一个书名、作者或章节关键词' : '打开任意作品开始阅读，它会自动出现在这里。',
              textAlign: TextAlign.center,
              style: theme.textTheme.bodyMedium?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
            if (!hasQuery && onBrowse != null) ...[
              const SizedBox(height: 18),
              FilledButton.icon(
                onPressed: onBrowse,
                icon: const Icon(Icons.local_library_rounded),
                label: const Text('去书库选书'),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _ShelfItem {
  final String bookId;
  final String title;
  final String author;
  final String lastChapterId;
  final String lastChapterTitle;
  final int lastChapterPosition;
  final double overallProgress;
  final int lastReadAt;

  const _ShelfItem({
    required this.bookId,
    required this.title,
    required this.author,
    required this.lastChapterId,
    required this.lastChapterTitle,
    required this.lastChapterPosition,
    required this.overallProgress,
    required this.lastReadAt,
  });

  factory _ShelfItem.fromLocal(HistoryEntry entry) => _ShelfItem(
    bookId: entry.book.id,
    title: entry.book.title,
    author: entry.book.author,
    lastChapterId: entry.lastChapterId,
    lastChapterTitle: entry.lastChapterTitle,
    lastChapterPosition: entry.lastChapterPosition,
    overallProgress: entry.overallProgress,
    lastReadAt: entry.lastReadAt,
  );

  factory _ShelfItem.fromCloud(
    Map<String, dynamic> shelf, {
    Map<String, dynamic>? history,
  }) {
    final source = history ?? shelf;
    final updated = DateTime.tryParse(
      source['updated_at'] as String? ?? shelf['updated_at'] as String? ?? '',
    );
    final position = (source['chapter_id'] as num?)?.toInt() ?? 1;
    return _ShelfItem(
      bookId: shelf['book_id'] as String? ?? source['book_id'] as String? ?? '',
      title: shelf['title'] as String? ?? source['title'] as String? ?? '',
      author: shelf['author'] as String? ?? source['author'] as String? ?? '',
      lastChapterId: (source['chapter_id'] ?? '').toString(),
      lastChapterTitle:
          source['current_chapter'] as String? ??
          (history == null ? '' : '第 $position 章'),
      lastChapterPosition: position,
      overallProgress: ((source['overall_progress'] as num?)?.toDouble() ?? 0)
          .clamp(0.0, 1.0)
          .toDouble(),
      lastReadAt: updated?.millisecondsSinceEpoch ?? 0,
    );
  }

  _ShelfItem withAuthoritativeMetadata(Map<String, dynamic> cloud) =>
      _ShelfItem(
        bookId: bookId,
        title: cloud['title'] as String? ?? title,
        author: cloud['author'] as String? ?? author,
        lastChapterId: lastChapterId,
        lastChapterTitle: lastChapterTitle,
        lastChapterPosition: lastChapterPosition,
        overallProgress: overallProgress,
        lastReadAt: lastReadAt,
      );
}
