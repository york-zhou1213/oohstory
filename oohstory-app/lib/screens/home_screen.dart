import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../models/book.dart';
import '../widgets/book_card.dart';
import '../theme/app_theme.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final _api = ApiService();
  List<Book> _featured = [];
  Map<String, dynamic> _stats = {};
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await _api.getHome();
      final books = (data['featured'] as List? ?? [])
          .map((e) => Book.fromJson(e as Map<String, dynamic>))
          .toList();
      if (mounted) setState(() {
        _featured = books;
        _stats = data['stats'] as Map<String, dynamic>? ?? {};
        _loading = false;
      });
    } catch (e) {
      if (mounted) setState(() { _error = e.toString(); _loading = false; });
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.cloud_off, size: 48, color: theme.colorScheme.onSurface.withValues(alpha: 0.3)),
            const SizedBox(height: 16),
            Text('无法连接服务器', style: theme.textTheme.titleMedium),
            const SizedBox(height: 12),
            FilledButton(onPressed: () { setState(() { _loading = true; _error = null; }); _load(); }, child: const Text('重试')),
          ],
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: _load,
      child: CustomScrollView(
        slivers: [
          SliverToBoxAdapter(child: _buildHero(theme)),
          SliverToBoxAdapter(child: _buildStats(theme)),
          SliverToBoxAdapter(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(20, 24, 20, 12),
              child: Text('精选推荐', style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700)),
            ),
          ),
          SliverPadding(
            padding: const EdgeInsets.symmetric(horizontal: 20),
            sliver: SliverGrid(
              delegate: SliverChildBuilderDelegate(
                (context, index) => BookCard(book: _featured[index]),
                childCount: _featured.length,
              ),
              gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
                maxCrossAxisExtent: 160,
                childAspectRatio: 0.56,
                mainAxisSpacing: 16,
                crossAxisSpacing: 12,
              ),
            ),
          ),
          const SliverToBoxAdapter(child: SizedBox(height: 100)),
        ],
      ),
    );
  }

  Widget _buildHero(ThemeData theme) {
    return Container(
      margin: const EdgeInsets.fromLTRB(20, 16, 20, 0),
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xFF6C5CE7), Color(0xFFA29BFE)],
        ),
        borderRadius: BorderRadius.circular(20),
        boxShadow: [
          BoxShadow(
            color: AppTheme.seedPurple.withValues(alpha: 0.25),
            blurRadius: 20,
            offset: const Offset(0, 8),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '好故事\n正在发生',
            style: theme.textTheme.headlineMedium?.copyWith(
              color: Colors.white,
              fontWeight: FontWeight.w800,
              height: 1.2,
              letterSpacing: -0.5,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            '免费阅读 · 深度拆书 · AI 情感朗读',
            style: TextStyle(color: Colors.white.withValues(alpha: 0.8), fontSize: 13),
          ),
        ],
      ),
    );
  }

  Widget _buildStats(ThemeData theme) {
    final bookTotal = _stats['book_total'] ?? 0;
    final wordTotal = _stats['word_total'] ?? 0;
    final deconTotal = _stats['deconstruction_total'] ?? 0;

    String formatWord(dynamic w) {
      final n = (w is int) ? w : int.tryParse(w.toString()) ?? 0;
      if (n >= 100000000) return '${(n / 100000000).toStringAsFixed(1)}亿';
      if (n >= 10000) return '${(n / 10000).toStringAsFixed(0)}万';
      return n.toString();
    }

    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 0),
      child: Row(
        children: [
          _statItem(theme, formatWord(bookTotal), '本书'),
          const SizedBox(width: 12),
          _statItem(theme, '${formatWord(wordTotal)}字', '总字数'),
          const SizedBox(width: 12),
          _statItem(theme, deconTotal.toString(), '拆书档案'),
        ],
      ),
    );
  }

  Widget _statItem(ThemeData theme, String value, String label) {
    return Expanded(
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 14),
        decoration: BoxDecoration(
          color: theme.cardTheme.color,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(
          children: [
            Text(value, style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700)),
            const SizedBox(height: 2),
            Text(label, style: theme.textTheme.labelSmall?.copyWith(color: theme.colorScheme.onSurface.withValues(alpha: 0.5))),
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
