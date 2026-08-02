import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../models/book.dart';
import 'reader_screen.dart';

class BookDetailScreen extends StatefulWidget {
  final String bookId;
  const BookDetailScreen({super.key, required this.bookId});

  @override
  State<BookDetailScreen> createState() => _BookDetailScreenState();
}

class _BookDetailScreenState extends State<BookDetailScreen> {
  final _api = ApiService();
  Book? _book;
  List<Chapter> _chapters = [];
  bool _loading = true;
  bool _catalogExpanded = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final results = await Future.wait([
        _api.getBook(widget.bookId),
        _api.getChapters(widget.bookId),
      ]);
      if (mounted) {
        setState(() {
          _book = results[0] as Book;
          _chapters = results[1] as List<Chapter>;
          _loading = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  String _formatWordCount(int? count) {
    if (count == null) return '';
    if (count >= 10000) return '${(count / 10000).toStringAsFixed(1)}万字';
    return '$count字';
  }

  void _openReader(String chapterId) {
    Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => ReaderScreen(bookId: widget.bookId, chapterId: chapterId, chapters: _chapters),
    ));
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Scaffold(body: Center(child: CircularProgressIndicator()));
    if (_book == null) return Scaffold(appBar: AppBar(), body: const Center(child: Text('书籍不存在')));
    final book = _book!;
    final theme = Theme.of(context);

    return Scaffold(
      body: CustomScrollView(
        slivers: [
          SliverAppBar(
            expandedHeight: 240,
            pinned: true,
            flexibleSpace: FlexibleSpaceBar(
              background: Container(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [theme.colorScheme.primaryContainer, theme.scaffoldBackgroundColor],
                  ),
                ),
                child: SafeArea(
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(20, 56, 20, 16),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        ClipRRect(
                          borderRadius: BorderRadius.circular(8),
                          child: Image.network(
                            _api.coverUrl(widget.bookId),
                            width: 100,
                            height: 140,
                            fit: BoxFit.cover,
                            errorBuilder: (_, __, ___) => Container(
                              width: 100, height: 140,
                              color: theme.colorScheme.surfaceContainerHighest,
                              child: const Icon(Icons.book, size: 40),
                            ),
                          ),
                        ),
                        const SizedBox(width: 16),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Text(book.title, style: theme.textTheme.titleLarge?.copyWith(fontWeight: FontWeight.bold), maxLines: 2, overflow: TextOverflow.ellipsis),
                              const SizedBox(height: 8),
                              Text(book.author, style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
                              const SizedBox(height: 8),
                              Wrap(
                                spacing: 8, runSpacing: 4,
                                children: [
                                  if (book.category != null) Chip(label: Text(book.category!, style: const TextStyle(fontSize: 12)), padding: EdgeInsets.zero, materialTapTargetSize: MaterialTapTargetSize.shrinkWrap),
                                  if (book.status != null) Chip(label: Text(book.status!, style: const TextStyle(fontSize: 12)), padding: EdgeInsets.zero, materialTapTargetSize: MaterialTapTargetSize.shrinkWrap),
                                ],
                              ),
                              const SizedBox(height: 8),
                              Text('${_formatWordCount(book.wordCount)} · ${book.chapterCount ?? _chapters.length}章', style: theme.textTheme.bodySmall),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
          if (book.description != null && book.description!.isNotEmpty)
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Text(book.description!, style: theme.textTheme.bodyMedium?.copyWith(height: 1.6), maxLines: 6, overflow: TextOverflow.ellipsis),
              ),
            ),
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
              child: Row(
                children: [
                  Text('目录 (${_chapters.length}章)', style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.bold)),
                  const Spacer(),
                  TextButton(
                    onPressed: () => setState(() => _catalogExpanded = !_catalogExpanded),
                    child: Text(_catalogExpanded ? '收起' : '展开全部'),
                  ),
                ],
              ),
            ),
          ),
          SliverList(
            delegate: SliverChildBuilderDelegate(
              (context, i) {
                final ch = _chapters[i];
                return ListTile(
                  dense: true,
                  title: Text(ch.title, maxLines: 1, overflow: TextOverflow.ellipsis),
                  trailing: ch.wordCount != null ? Text('${ch.wordCount}字', style: theme.textTheme.bodySmall) : null,
                  onTap: () => _openReader(ch.id),
                );
              },
              childCount: _catalogExpanded ? _chapters.length : _chapters.length.clamp(0, 20),
            ),
          ),
          const SliverToBoxAdapter(child: SizedBox(height: 100)),
        ],
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
          child: Row(
            children: [
              Expanded(
                child: FilledButton.icon(
                  onPressed: _chapters.isNotEmpty ? () => _openReader(_chapters.first.id) : null,
                  icon: const Icon(Icons.play_arrow),
                  label: const Text('开始阅读'),
                ),
              ),
            ],
          ),
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
