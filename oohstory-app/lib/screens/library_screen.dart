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
  List<Book> _books = [];
  List<String> _categories = [];
  String? _selectedCategory;
  String _sort = 'title';
  int _page = 1;
  int _totalPages = 1;
  bool _loading = true;
  bool _loadingMore = false;

  @override
  void initState() {
    super.initState();
    _loadCategories();
    _loadBooks();
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
      final books = (data['books'] as List)
          .map((e) => Book.fromJson(e as Map<String, dynamic>))
          .toList();
      final total = data['total_pages'] as int? ?? 1;
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
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
          child: TextField(
            controller: _searchController,
            decoration: InputDecoration(
              hintText: '搜索书名或作者…',
              prefixIcon: const Icon(Icons.search),
              filled: true,
              fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
              border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
              contentPadding: const EdgeInsets.symmetric(vertical: 0),
              suffixIcon: _searchController.text.isNotEmpty
                  ? IconButton(icon: const Icon(Icons.clear), onPressed: () { _searchController.clear(); _search(); })
                  : null,
            ),
            onSubmitted: (_) => _search(),
          ),
        ),
        if (_categories.isNotEmpty)
          SizedBox(
            height: 40,
            child: ListView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12),
              children: [
                _categoryChip('全部', null),
                ..._categories.map((c) => _categoryChip(c, c)),
              ],
            ),
          ),
        const SizedBox(height: 8),
        Expanded(
          child: _loading
              ? const Center(child: CircularProgressIndicator())
              : _books.isEmpty
                  ? const Center(child: Text('没有找到相关书籍'))
                  : NotificationListener<ScrollNotification>(
                      onNotification: (n) {
                        if (n.metrics.pixels > n.metrics.maxScrollExtent - 200) _loadMore();
                        return false;
                      },
                      child: GridView.builder(
                        padding: const EdgeInsets.symmetric(horizontal: 16),
                        gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
                          maxCrossAxisExtent: 200,
                          childAspectRatio: 0.58,
                          mainAxisSpacing: 12,
                          crossAxisSpacing: 12,
                        ),
                        itemCount: _books.length + (_loadingMore ? 1 : 0),
                        itemBuilder: (context, i) {
                          if (i >= _books.length) return const Center(child: CircularProgressIndicator());
                          return BookCard(book: _books[i]);
                        },
                      ),
                    ),
        ),
      ],
    );
  }

  Widget _categoryChip(String label, String? value) {
    final selected = _selectedCategory == value;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 4),
      child: FilterChip(
        label: Text(label),
        selected: selected,
        onSelected: (_) {
          setState(() { _selectedCategory = value; _page = 1; });
          _loadBooks();
        },
      ),
    );
  }

  @override
  void dispose() {
    _api.dispose();
    _searchController.dispose();
    super.dispose();
  }
}
