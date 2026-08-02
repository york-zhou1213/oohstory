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
  List<Book> _recommendations = [];
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
      final recs = (data['recommendations'] as List? ?? [])
          .map((e) => Book.fromJson(e as Map<String, dynamic>))
          .toList();
      if (mounted) setState(() {
        _featured = books;
        _recommendations = recs;
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
    final isDark = theme.brightness == Brightness.dark;
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
          SliverToBoxAdapter(child: _buildStats(theme, isDark)),
          if (_recommendations.isNotEmpty) ...[
            SliverToBoxAdapter(child: _sectionHeader(theme, '人气推荐', '每日精选', Icons.local_fire_department_rounded)),
            SliverPadding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              sliver: SliverGrid(
                delegate: SliverChildBuilderDelegate(
                  (context, index) => BookCard(book: _recommendations[index]),
                  childCount: _recommendations.length,
                ),
                gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
                  maxCrossAxisExtent: 160,
                  childAspectRatio: 0.56,
                  mainAxisSpacing: 16,
                  crossAxisSpacing: 12,
                ),
              ),
            ),
          ],
          SliverToBoxAdapter(child: _sectionHeader(theme, '新书入库', '持续更新', Icons.auto_awesome_rounded)),
          SliverPadding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
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

  Widget _sectionHeader(ThemeData theme, String title, String kicker, IconData icon) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 24, 20, 12),
      child: Row(
        children: [
          Container(
            padding: const EdgeInsets.all(6),
            decoration: BoxDecoration(
              color: AppTheme.seedPurple.withValues(alpha: 0.1),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Icon(icon, size: 16, color: AppTheme.seedPurple),
          ),
          const SizedBox(width: 10),
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(kicker, style: TextStyle(fontSize: 10, fontWeight: FontWeight.w600, color: AppTheme.seedPurple, letterSpacing: 1)),
              Text(title, style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700)),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildHero(ThemeData theme) {
    return Container(
      margin: const EdgeInsets.fromLTRB(16, 12, 16, 0),
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xFF6C5CE7), Color(0xFF8B7CF6), Color(0xFFA29BFE)],
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
          Row(
            children: [
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: const Icon(Icons.auto_stories, color: Colors.white, size: 24),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '好故事正在发生',
                      style: theme.textTheme.titleLarge?.copyWith(
                        color: Colors.white,
                        fontWeight: FontWeight.w800,
                        letterSpacing: -0.3,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      '免费阅读 · 深度拆书 · AI 朗读',
                      style: TextStyle(color: Colors.white.withValues(alpha: 0.7), fontSize: 12),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ],
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
          _statCard(theme, isDark, _formatNum(bookTotal), '可读作品', Icons.menu_book_rounded, const Color(0xFF6C5CE7)),
          const SizedBox(width: 8),
          _statCard(theme, isDark, _formatNum(catTotal), '题材分类', Icons.category_rounded, const Color(0xFFFD79A8)),
          const SizedBox(width: 8),
          _statCard(theme, isDark, _formatNum(deconTotal), '拆书档案', Icons.analytics_rounded, const Color(0xFF00B894)),
        ],
      ),
    );
  }

  Widget _statCard(ThemeData theme, bool isDark, String value, String label, IconData icon, Color accent) {
    final cardColor = isDark ? const Color(0xFF1E1E30) : Colors.white;
    return Expanded(
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 14),
        decoration: BoxDecoration(
          color: cardColor,
          borderRadius: BorderRadius.circular(14),
          boxShadow: [BoxShadow(color: Colors.black.withValues(alpha: 0.04), blurRadius: 8, offset: const Offset(0, 2))],
        ),
        child: Column(
          children: [
            Icon(icon, size: 18, color: accent),
            const SizedBox(height: 6),
            Text(value, style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: accent)),
            const SizedBox(height: 2),
            Text(label, style: TextStyle(fontSize: 10, color: theme.colorScheme.onSurface.withValues(alpha: 0.5))),
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
  void dispose() {
    _api.dispose();
    super.dispose();
  }
}
