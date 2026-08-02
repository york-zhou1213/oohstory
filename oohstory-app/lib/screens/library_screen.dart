import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../models/book.dart';
import '../widgets/book_card.dart';

class LibraryScreen extends StatefulWidget {
  const LibraryScreen({super.key});

  @override
  State<LibraryScreen> createState() => _LibraryScreenState();
}

class _LibraryScreenState extends State<LibraryScreen> {
  final _api = ApiService();
  final _searchController = TextEditingController();
  final _scrollController = ScrollController();
  List<Book> _books = [];
  List<String> _categories = [];
  String? _selectedCategory;
  String _sort = 'title';
  int _page = 1;
  int _totalPages = 1;
  bool _loading = true;
  bool _loadingMore = false;
  bool _searchFocused = false;

  @override
  void initState() {
    super.initState();
    _loadCategories();
    _loadBooks();
    _scrollController.addListener(_onScroll);
  }

  void _onScroll() {
    if (_scrollController.position.pixels > _scrollController.position.maxScrollExtent - 300) {
      _loadMore();
    }
  }

  Future<void> _loadCategories() async {
    try {
      final cats = await _api.getCategories();
      if (mounted) setState(() => _categories = cats);
    } catch (_) {}
  }

  Future<void> _loadBooks({bool append = false}) async {
    if (!append) setState(() => _loading = true);
    try {
      final data = await _api.getBooks(
        category: _selectedCategory,
        query: _searchController.text.isNotEmpty ? _searchController.text : null,
        sort: _sort,
        page: _page,
      );
      final books = ((data['items'] ?? data['books']) as List)
          .map((e) => Book.fromJson(e as Map<String, dynamic>))
          .toList();
      final total = data['page_count'] as int? ?? data['total_pages'] as int? ?? 1;
      if (mounted) {
        setState(() {
          if (append) {
            _books.addAll(books);
          } else {
            _books = books;
          }
          _totalPages = total;
          _loading = false;
          _loadingMore = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() { _loading = false; _loadingMore = false; });
    }
  }

  void _search() {
    _page = 1;
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
        _buildSortRow(theme),
        Expanded(child: _buildBookGrid(theme)),
      ],
    );
  }

  Widget _buildSearchBar(ThemeData theme) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 8),
      child: Focus(
        onFocusChange: (f) => setState(() => _searchFocused = f),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          decoration: BoxDecoration(
            color: theme.inputDecorationTheme.fillColor,
            borderRadius: BorderRadius.circular(14),
            boxShadow: _searchFocused
                ? [BoxShadow(color: theme.colorScheme.primary.withValues(alpha: 0.15), blurRadius: 12, offset: const Offset(0, 2))]
                : [],
          ),
          child: TextField(
            controller: _searchController,
            decoration: InputDecoration(
              hintText: '搜索书名、作者…',
              hintStyle: TextStyle(color: theme.colorScheme.onSurface.withValues(alpha: 0.35), fontSize: 14),
              prefixIcon: Icon(Icons.search, size: 20, color: theme.colorScheme.onSurface.withValues(alpha: 0.4)),
              filled: false,
              border: InputBorder.none,
              contentPadding: const EdgeInsets.symmetric(vertical: 14),
              suffixIcon: _searchController.text.isNotEmpty
                  ? IconButton(
                      icon: Icon(Icons.clear, size: 18, color: theme.colorScheme.onSurface.withValues(alpha: 0.4)),
                      onPressed: () { _searchController.clear(); _search(); },
                    )
                  : null,
            ),
            onSubmitted: (_) => _search(),
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
        padding: const EdgeInsets.symmetric(horizontal: 16),
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
          setState(() { _selectedCategory = value; _page = 1; });
          _loadBooks();
        },
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 180),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 7),
          decoration: BoxDecoration(
            color: selected ? theme.colorScheme.primary : theme.chipTheme.backgroundColor,
            borderRadius: BorderRadius.circular(20),
          ),
          child: Text(
            label,
            style: TextStyle(
              fontSize: 13,
              fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
              color: selected ? Colors.white : theme.colorScheme.onSurface.withValues(alpha: 0.7),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildSortRow(ThemeData theme) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 8, 20, 4),
      child: Row(
        children: [
          Text(
            '共 ${_books.length} 本',
            style: theme.textTheme.labelMedium?.copyWith(color: theme.colorScheme.onSurface.withValues(alpha: 0.5)),
          ),
          const Spacer(),
          PopupMenuButton<String>(
            initialValue: _sort,
            onSelected: (v) {
              setState(() { _sort = v; _page = 1; });
              _loadBooks();
            },
            itemBuilder: (ctx) => const [
              PopupMenuItem(value: 'title', child: Text('按书名')),
              PopupMenuItem(value: 'latest', child: Text('最新更新')),
              PopupMenuItem(value: 'word_count', child: Text('按字数')),
            ],
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(Icons.sort, size: 16, color: theme.colorScheme.primary),
                const SizedBox(width: 4),
                Text(
                  _sort == 'title' ? '按书名' : _sort == 'latest' ? '最新更新' : '按字数',
                  style: TextStyle(fontSize: 12, color: theme.colorScheme.primary, fontWeight: FontWeight.w500),
                ),
              ],
            ),
          ),
        ],
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
            Icon(Icons.search_off, size: 48, color: theme.colorScheme.onSurface.withValues(alpha: 0.2)),
            const SizedBox(height: 12),
            Text('没有找到相关书籍', style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurface.withValues(alpha: 0.4))),
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
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 100),
        gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
          maxCrossAxisExtent: 160,
          childAspectRatio: 0.56,
          mainAxisSpacing: 16,
          crossAxisSpacing: 12,
        ),
        itemCount: _books.length + (_loadingMore ? 1 : 0),
        itemBuilder: (context, i) {
          if (i >= _books.length) {
            return const Center(child: Padding(
              padding: EdgeInsets.all(16),
              child: CircularProgressIndicator(strokeWidth: 2),
            ));
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
