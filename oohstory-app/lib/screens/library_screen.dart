import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../services/api_service.dart';
import '../models/book.dart';
import '../widgets/book_card.dart';
import '../widgets/ooh_ui.dart';

class LibraryScreen extends StatefulWidget {
  final String? initialCategory;

  const LibraryScreen({super.key, this.initialCategory});

  @override
  State<LibraryScreen> createState() => _LibraryScreenState();
}

class _LibraryScreenState extends State<LibraryScreen> {
  static const _categoryPreferenceKey = 'oohstory_library_category';
  final _api = ApiService();
  final _searchController = TextEditingController();
  final _scrollController = ScrollController();
  List<Book> _books = [];
  List<String> _categories = [];
  String? _selectedCategory;
  String _sort = 'recent';
  String _words = '';
  String _serialization = '';
  int _page = 1;
  int _totalPages = 1;
  int _totalBooks = 0;
  bool _loading = true;
  bool _loadingMore = false;
  bool _searchFocused = false;

  static const _sortOptions = [
    ('recent', '最近入库'),
    ('title', '按书名'),
    ('long', '长篇优先'),
  ];

  static const _wordOptions = [
    ('', '全部字数'),
    ('under_100k', '10万以下'),
    ('over_100k', '10万以上'),
    ('over_200k', '20万以上'),
    ('over_300k', '30万以上'),
    ('over_500k', '50万以上'),
    ('over_1m', '100万以上'),
    ('over_2m', '200万以上'),
  ];

  static const _statusOptions = [
    ('', '全部状态'),
    ('finished', '已完本'),
    ('ongoing', '连载中'),
  ];

  @override
  void initState() {
    super.initState();
    _initializeLibrary();
    _scrollController.addListener(_onScroll);
  }

  void _onScroll() {
    if (_scrollController.position.pixels >
        _scrollController.position.maxScrollExtent - 300) {
      _loadMore();
    }
  }

  Future<void> _initializeLibrary() async {
    final preferences = await SharedPreferences.getInstance();
    var selected = widget.initialCategory?.trim();
    if (selected == null || selected.isEmpty) {
      selected = preferences.getString(_categoryPreferenceKey)?.trim();
    }
    if (selected != null && selected.isEmpty) selected = null;
    List<String> categories = const [];
    try {
      categories = await _api.getCategories();
    } catch (_) {}
    if (selected != null &&
        categories.isNotEmpty &&
        !categories.contains(selected)) {
      selected = null;
      await preferences.remove(_categoryPreferenceKey);
    }
    if (!mounted) return;
    setState(() {
      _categories = categories;
      _selectedCategory = selected;
    });
    if (selected != null) {
      await preferences.setString(_categoryPreferenceKey, selected);
    }
    await _loadBooks();
  }

  Future<void> _persistCategory(String? value) async {
    final preferences = await SharedPreferences.getInstance();
    if (value == null || value.isEmpty) {
      await preferences.remove(_categoryPreferenceKey);
    } else {
      await preferences.setString(_categoryPreferenceKey, value);
    }
  }

  Future<void> _loadBooks({bool append = false}) async {
    if (!append) setState(() => _loading = true);
    try {
      final data = await _api.getBooks(
        category: _selectedCategory,
        query: _searchController.text.isNotEmpty
            ? _searchController.text
            : null,
        sort: _sort,
        words: _words.isNotEmpty ? _words : null,
        status: _serialization.isNotEmpty ? _serialization : null,
        page: _page,
      );
      final books = ((data['items'] ?? data['books']) as List)
          .map((e) => Book.fromJson(e as Map<String, dynamic>))
          .toList();
      final total =
          data['page_count'] as int? ?? data['total_pages'] as int? ?? 1;
      final totalBooks = data['total'] as int? ?? 0;
      if (mounted) {
        setState(() {
          if (append) {
            _books.addAll(books);
          } else {
            _books = books;
          }
          _totalPages = total;
          _totalBooks = totalBooks;
          _loading = false;
          _loadingMore = false;
        });
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          _loading = false;
          _loadingMore = false;
        });
      }
    }
  }

  void _resetAndLoad() {
    _page = 1;
    if (_scrollController.hasClients) {
      _scrollController.jumpTo(0);
    }
    _loadBooks();
  }

  void _loadMore() {
    if (_loadingMore || _page >= _totalPages) return;
    _loadingMore = true;
    _page++;
    _loadBooks(append: true);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      children: [
        _buildSearchBar(theme),
        if (_categories.isNotEmpty) _buildCategoryRow(theme),
        _buildCompactFilterBar(theme),
        Expanded(child: _buildBookGrid(theme)),
      ],
    );
  }

  Widget _buildSearchBar(ThemeData theme) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
      child: Focus(
        onFocusChange: (f) => setState(() => _searchFocused = f),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          decoration: BoxDecoration(
            color: theme.inputDecorationTheme.fillColor,
            borderRadius: BorderRadius.circular(14),
            boxShadow: _searchFocused
                ? [
                    BoxShadow(
                      color: theme.colorScheme.primary.withValues(alpha: 0.15),
                      blurRadius: 12,
                      offset: const Offset(0, 2),
                    ),
                  ]
                : [],
          ),
          child: TextField(
            controller: _searchController,
            decoration: InputDecoration(
              hintText: '搜索书名、作者…',
              hintStyle: TextStyle(
                color: theme.colorScheme.onSurface.withValues(alpha: 0.35),
                fontSize: 14,
              ),
              prefixIcon: Icon(
                Icons.search,
                size: 20,
                color: theme.colorScheme.onSurface.withValues(alpha: 0.4),
              ),
              filled: false,
              border: InputBorder.none,
              contentPadding: const EdgeInsets.symmetric(vertical: 14),
              suffixIcon: _searchController.text.isNotEmpty
                  ? IconButton(
                      icon: Icon(
                        Icons.clear,
                        size: 18,
                        color: theme.colorScheme.onSurface.withValues(
                          alpha: 0.4,
                        ),
                      ),
                      onPressed: () {
                        _searchController.clear();
                        _resetAndLoad();
                      },
                    )
                  : null,
            ),
            onSubmitted: (_) => _resetAndLoad(),
            onChanged: (_) => setState(() {}),
          ),
        ),
      ),
    );
  }

  Widget _buildCategoryRow(ThemeData theme) {
    return SizedBox(
      height: 38,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 12),
        children: [
          _categoryChip(theme, '全部', null),
          ..._categories.map((c) => _categoryChip(theme, c, c)),
        ],
      ),
    );
  }

  Widget _buildCompactFilterBar(ThemeData theme) {
    final filterCount =
        (_words.isNotEmpty ? 1 : 0) + (_serialization.isNotEmpty ? 1 : 0);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 10, 16, 4),
      child: Row(
        children: [
          Text(
            _loading ? '正在整理书库' : '$_totalBooks 本作品',
            style: theme.textTheme.labelMedium?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
          const Spacer(),
          TextButton.icon(
            onPressed: () => _showSortSheet(context),
            icon: const Icon(Icons.swap_vert_rounded, size: 18),
            label: const Text('排序'),
          ),
          const SizedBox(width: 4),
          OutlinedButton.icon(
            onPressed: () => _showCombinedFilterSheet(context),
            icon: const Icon(Icons.tune_rounded, size: 18),
            label: Text(filterCount == 0 ? '筛选' : '筛选 $filterCount'),
            style: OutlinedButton.styleFrom(
              minimumSize: const Size(0, 40),
              padding: const EdgeInsets.symmetric(horizontal: 12),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _showCombinedFilterSheet(BuildContext context) async {
    var draftWords = _words;
    var draftStatus = _serialization;
    final applied = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (sheetContext) => StatefulBuilder(
        builder: (context, setSheetState) => SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 6, 20, 20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('筛选书库', style: Theme.of(context).textTheme.titleLarge),
                const SizedBox(height: 20),
                Text('字数', style: Theme.of(context).textTheme.titleSmall),
                const SizedBox(height: 10),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: _wordOptions.map((option) {
                    return ChoiceChip(
                      label: Text(option.$2),
                      selected: draftWords == option.$1,
                      onSelected: (_) =>
                          setSheetState(() => draftWords = option.$1),
                    );
                  }).toList(),
                ),
                const SizedBox(height: 22),
                Text('状态', style: Theme.of(context).textTheme.titleSmall),
                const SizedBox(height: 10),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: _statusOptions.map((option) {
                    return ChoiceChip(
                      label: Text(option.$2),
                      selected: draftStatus == option.$1,
                      onSelected: (_) =>
                          setSheetState(() => draftStatus = option.$1),
                    );
                  }).toList(),
                ),
                const SizedBox(height: 24),
                Row(
                  children: [
                    TextButton(
                      onPressed: () {
                        setSheetState(() {
                          draftWords = '';
                          draftStatus = '';
                        });
                      },
                      child: const Text('重置'),
                    ),
                    const Spacer(),
                    FilledButton(
                      onPressed: () => Navigator.pop(sheetContext, true),
                      child: const Text('应用筛选'),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
    if (applied != true || !mounted) return;
    setState(() {
      _words = draftWords;
      _serialization = draftStatus;
    });
    _resetAndLoad();
  }

  Widget _categoryChip(ThemeData theme, String label, String? value) {
    final selected = _selectedCategory == value;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4),
      child: GestureDetector(
        onTap: () {
          setState(() {
            _selectedCategory = value;
          });
          _persistCategory(value);
          _resetAndLoad();
        },
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 180),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 7),
          decoration: BoxDecoration(
            color: selected
                ? theme.colorScheme.primary
                : theme.chipTheme.backgroundColor,
            borderRadius: BorderRadius.circular(20),
          ),
          child: Text(
            label,
            style: TextStyle(
              fontSize: 13,
              fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
              color: selected
                  ? Colors.white
                  : theme.colorScheme.onSurface.withValues(alpha: 0.7),
            ),
          ),
        ),
      ),
    );
  }

  void _showSortSheet(BuildContext context) {
    _showOptionSheet(
      context,
      title: '排序方式',
      options: _sortOptions.map((o) => (o.$1, o.$2)).toList(),
      current: _sort,
      onSelect: (v) {
        setState(() => _sort = v);
        _resetAndLoad();
      },
    );
  }

  void _showOptionSheet(
    BuildContext context, {
    required String title,
    required List<(String, String)> options,
    required String current,
    required void Function(String) onSelect,
  }) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    showModalBottomSheet(
      context: context,
      backgroundColor: isDark ? const Color(0xFF1A1A2E) : Colors.white,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (ctx) => Padding(
        padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Center(
              child: Container(
                width: 36,
                height: 4,
                decoration: BoxDecoration(
                  color: theme.colorScheme.onSurface.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            const SizedBox(height: 16),
            Text(
              title,
              style: theme.textTheme.titleMedium?.copyWith(
                fontWeight: FontWeight.w700,
              ),
            ),
            const SizedBox(height: 16),
            Wrap(
              spacing: 10,
              runSpacing: 10,
              children: options.map((opt) {
                final selected = opt.$1 == current;
                return GestureDetector(
                  onTap: () {
                    Navigator.pop(ctx);
                    onSelect(opt.$1);
                  },
                  child: AnimatedContainer(
                    duration: const Duration(milliseconds: 150),
                    padding: const EdgeInsets.symmetric(
                      horizontal: 18,
                      vertical: 10,
                    ),
                    decoration: BoxDecoration(
                      color: selected
                          ? theme.colorScheme.primary
                          : (isDark
                                ? const Color(0xFF2A2A40)
                                : const Color(0xFFF5F5F8)),
                      borderRadius: BorderRadius.circular(12),
                      border: selected
                          ? null
                          : Border.all(
                              color: theme.colorScheme.onSurface.withValues(
                                alpha: 0.06,
                              ),
                            ),
                    ),
                    child: Text(
                      opt.$2,
                      style: TextStyle(
                        fontSize: 14,
                        fontWeight: selected
                            ? FontWeight.w600
                            : FontWeight.w400,
                        color: selected
                            ? Colors.white
                            : theme.colorScheme.onSurface.withValues(
                                alpha: 0.7,
                              ),
                      ),
                    ),
                  ),
                );
              }).toList(),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildBookGrid(ThemeData theme) {
    if (_loading) {
      return const OohLoadingState();
    }
    if (_books.isEmpty) {
      return OohMessageState(
        icon: Icons.search_off_rounded,
        title: '没有找到相关书籍',
        message: '换个关键词，或者清除当前筛选条件后再试。',
        actionLabel: _words.isNotEmpty || _serialization.isNotEmpty
            ? '清除筛选'
            : null,
        onAction: _words.isNotEmpty || _serialization.isNotEmpty
            ? () {
                setState(() {
                  _words = '';
                  _serialization = '';
                });
                _resetAndLoad();
              }
            : null,
      );
    }

    return RefreshIndicator(
      onRefresh: () async {
        _page = 1;
        await _loadBooks();
      },
      child: LayoutBuilder(
        builder: (context, constraints) {
          final columns = OohPageMetrics.gridColumns(constraints.maxWidth);
          return GridView.builder(
            controller: _scrollController,
            padding: EdgeInsets.fromLTRB(
              OohPageMetrics.horizontalPadding(constraints.maxWidth),
              12,
              OohPageMetrics.horizontalPadding(constraints.maxWidth),
              104,
            ),
            gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
              crossAxisCount: columns,
              childAspectRatio: columns == 2 ? .53 : .55,
              mainAxisSpacing: 24,
              crossAxisSpacing: constraints.maxWidth >= 720 ? 18 : 14,
            ),
            itemCount: _books.length + (_loadingMore ? 1 : 0),
            itemBuilder: (context, i) {
              if (i >= _books.length) {
                return const Center(
                  child: Padding(
                    padding: EdgeInsets.all(16),
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                );
              }
              return BookCard(book: _books[i]);
            },
          );
        },
      ),
    );
  }

  @override
  void dispose() {
    _api.dispose();
    _searchController.dispose();
    _scrollController.dispose();
    super.dispose();
  }
}
