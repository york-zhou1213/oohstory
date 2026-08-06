import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../services/api_service.dart';
import '../models/book.dart';
import '../widgets/book_card.dart';
import '../theme/app_theme.dart';

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

  String _sortLabel() => _sortOptions.firstWhere((o) => o.$1 == _sort).$2;
  String _wordsLabel() => _wordOptions.firstWhere((o) => o.$1 == _words).$2;
  String _statusLabel() =>
      _statusOptions.firstWhere((o) => o.$1 == _serialization).$2;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    return Column(
      children: [
        _buildSearchBar(theme),
        if (_categories.isNotEmpty) _buildCategoryRow(theme),
        _buildFilterBar(theme, isDark),
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

  Widget _buildFilterBar(ThemeData theme, bool isDark) {
    final hasFilter = _words.isNotEmpty || _serialization.isNotEmpty;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 6, 16, 4),
      child: Row(
        children: [
          Text(
            _loading ? '加载中…' : '共 $_totalBooks 本',
            style: theme.textTheme.labelMedium?.copyWith(
              color: theme.colorScheme.onSurface.withValues(alpha: 0.5),
            ),
          ),
          const Spacer(),
          _filterChip(
            theme,
            isDark,
            icon: Icons.sort_rounded,
            label: _sortLabel(),
            onTap: () => _showSortSheet(context),
          ),
          const SizedBox(width: 8),
          _filterChip(
            theme,
            isDark,
            icon: Icons.straighten_rounded,
            label: _wordsLabel(),
            active: _words.isNotEmpty,
            onTap: () => _showWordsSheet(context),
          ),
          const SizedBox(width: 8),
          _filterChip(
            theme,
            isDark,
            icon: Icons.circle_outlined,
            label: _statusLabel(),
            active: _serialization.isNotEmpty,
            onTap: () => _showStatusSheet(context),
          ),
          if (hasFilter) ...[
            const SizedBox(width: 6),
            GestureDetector(
              onTap: () {
                setState(() {
                  _words = '';
                  _serialization = '';
                });
                _resetAndLoad();
              },
              child: Container(
                padding: const EdgeInsets.all(4),
                decoration: BoxDecoration(
                  color: Colors.red.shade50,
                  shape: BoxShape.circle,
                ),
                child: Icon(Icons.close, size: 14, color: Colors.red.shade400),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _filterChip(
    ThemeData theme,
    bool isDark, {
    required IconData icon,
    required String label,
    bool active = false,
    required VoidCallback onTap,
  }) {
    final bgColor = active
        ? AppTheme.seedPurple.withValues(alpha: 0.12)
        : (isDark ? const Color(0xFF2A2A40) : const Color(0xFFF0F0F5));
    final textColor = active
        ? AppTheme.seedPurple
        : theme.colorScheme.onSurface.withValues(alpha: 0.6);
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: bgColor,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 13, color: textColor),
            const SizedBox(width: 4),
            Text(
              label,
              style: TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w500,
                color: textColor,
              ),
            ),
          ],
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

  void _showWordsSheet(BuildContext context) {
    _showOptionSheet(
      context,
      title: '字数筛选',
      options: _wordOptions.map((o) => (o.$1, o.$2)).toList(),
      current: _words,
      onSelect: (v) {
        setState(() => _words = v);
        _resetAndLoad();
      },
    );
  }

  void _showStatusSheet(BuildContext context) {
    _showOptionSheet(
      context,
      title: '连载状态',
      options: _statusOptions.map((o) => (o.$1, o.$2)).toList(),
      current: _serialization,
      onSelect: (v) {
        setState(() => _serialization = v);
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
                          ? AppTheme.seedPurple
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
      return const Center(child: CircularProgressIndicator());
    }
    if (_books.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.search_off,
              size: 48,
              color: theme.colorScheme.onSurface.withValues(alpha: 0.2),
            ),
            const SizedBox(height: 12),
            Text(
              '没有找到相关书籍',
              style: theme.textTheme.bodyMedium?.copyWith(
                color: theme.colorScheme.onSurface.withValues(alpha: 0.4),
              ),
            ),
            const SizedBox(height: 16),
            if (_words.isNotEmpty || _serialization.isNotEmpty)
              FilledButton.tonal(
                onPressed: () {
                  setState(() {
                    _words = '';
                    _serialization = '';
                  });
                  _resetAndLoad();
                },
                child: const Text('清除筛选'),
              ),
          ],
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: () async {
        _page = 1;
        await _loadBooks();
      },
      child: GridView.builder(
        controller: _scrollController,
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 100),
        gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
          maxCrossAxisExtent: 160,
          childAspectRatio: 0.56,
          mainAxisSpacing: 16,
          crossAxisSpacing: 12,
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
