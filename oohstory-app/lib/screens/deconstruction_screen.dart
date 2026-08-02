import 'dart:async';
import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../models/book.dart';

class DeconstructionScreen extends StatefulWidget {
  const DeconstructionScreen({super.key});

  @override
  State<DeconstructionScreen> createState() => _DeconstructionScreenState();
}

class _DeconstructionScreenState extends State<DeconstructionScreen> {
  final _api = ApiService();
  List<Deconstruction> _items = [];
  bool _loading = true;
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    _load();
    _refreshTimer = Timer.periodic(const Duration(seconds: 30), (_) => _silentRefresh());
  }

  Future<void> _load() async {
    try {
      final items = await _api.getDeconstructions();
      if (mounted) setState(() { _items = items; _loading = false; });
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _silentRefresh() async {
    try {
      final items = await _api.getDeconstructions();
      if (mounted) setState(() => _items = items);
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_items.isEmpty) return const Center(child: Text('暂无拆书档案'));
    final theme = Theme.of(context);

    final active = _items.where((i) => i.isActive).toList();
    final completed = _items.where((i) => i.isCompleted).toList();
    final pending = _items.where((i) => !i.isActive && !i.isCompleted).toList();

    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (active.isNotEmpty) ...[
            _sectionHeader(theme, '拆解中', active.length, const Color(0xFF2196F3)),
            ...active.map((item) => _buildCard(theme, item)),
            const SizedBox(height: 16),
          ],
          if (completed.isNotEmpty) ...[
            _sectionHeader(theme, '已完成', completed.length, const Color(0xFF4CAF50)),
            ...completed.map((item) => _buildCard(theme, item)),
            const SizedBox(height: 16),
          ],
          if (pending.isNotEmpty) ...[
            _sectionHeader(theme, '档案已建立', pending.length, Colors.grey),
            ...pending.map((item) => _buildCard(theme, item)),
          ],
          const SizedBox(height: 80),
        ],
      ),
    );
  }

  Widget _sectionHeader(ThemeData theme, String title, int count, Color color) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        children: [
          Container(
            width: 4, height: 18,
            decoration: BoxDecoration(color: color, borderRadius: BorderRadius.circular(2)),
          ),
          const SizedBox(width: 8),
          Text(title, style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.bold)),
          const SizedBox(width: 6),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
            decoration: BoxDecoration(
              color: color.withValues(alpha: 0.15),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Text('$count', style: TextStyle(fontSize: 11, color: color, fontWeight: FontWeight.w600)),
          ),
        ],
      ),
    );
  }

  Widget _buildCard(ThemeData theme, Deconstruction item) {
    final statusColor = item.isActive
        ? const Color(0xFF2196F3)
        : item.isCompleted
            ? const Color(0xFF4CAF50)
            : Colors.grey;

    return Card(
      clipBehavior: Clip.antiAlias,
      margin: const EdgeInsets.only(bottom: 10),
      child: InkWell(
        onTap: () => _openDetail(item),
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(6),
                child: item.coverUrl != null
                    ? Image.network(
                        '${ApiService.baseUrl}${item.coverUrl}',
                        width: 64, height: 88, fit: BoxFit.cover,
                        errorBuilder: (_, __, ___) => _coverPlaceholder(theme),
                      )
                    : _coverPlaceholder(theme),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(item.title, style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.bold), maxLines: 1, overflow: TextOverflow.ellipsis),
                        ),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                          decoration: BoxDecoration(
                            color: statusColor.withValues(alpha: 0.12),
                            borderRadius: BorderRadius.circular(4),
                          ),
                          child: Text(item.statusText, style: TextStyle(fontSize: 10, color: statusColor, fontWeight: FontWeight.w600)),
                        ),
                      ],
                    ),
                    const SizedBox(height: 6),
                    if (item.totalChapters > 0) ...[
                      Row(
                        children: [
                          Expanded(
                            child: ClipRRect(
                              borderRadius: BorderRadius.circular(3),
                              child: LinearProgressIndicator(
                                value: (item.progressPercent / 100).clamp(0.0, 1.0),
                                minHeight: 6,
                                backgroundColor: theme.colorScheme.surfaceContainerHighest,
                                valueColor: AlwaysStoppedAnimation(statusColor),
                              ),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Text(
                            '${item.progressPercent.toStringAsFixed(1)}%',
                            style: TextStyle(fontSize: 11, color: statusColor, fontWeight: FontWeight.w600),
                          ),
                        ],
                      ),
                      const SizedBox(height: 4),
                      Text(
                        '${item.completedChapters} / ${item.totalChapters} 章',
                        style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                      ),
                    ],
                    if (item.documents.isNotEmpty) ...[
                      const SizedBox(height: 8),
                      Wrap(
                        spacing: 6, runSpacing: 4,
                        children: item.documents.map((doc) => Container(
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                          decoration: BoxDecoration(
                            color: theme.colorScheme.primaryContainer.withValues(alpha: 0.4),
                            borderRadius: BorderRadius.circular(4),
                          ),
                          child: Text(doc.label, style: TextStyle(fontSize: 11, color: theme.colorScheme.primary)),
                        )).toList(),
                      ),
                    ],
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _coverPlaceholder(ThemeData theme) {
    return Container(
      width: 64, height: 88,
      decoration: BoxDecoration(
        color: theme.colorScheme.primaryContainer,
        borderRadius: BorderRadius.circular(6),
      ),
      child: Icon(Icons.analytics, size: 24, color: theme.colorScheme.primary),
    );
  }

  void _openDetail(Deconstruction item) {
    Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => _DeconstructionDetailScreen(slug: item.slug, title: item.title),
    ));
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    _api.dispose();
    super.dispose();
  }
}

class _DeconstructionDetailScreen extends StatefulWidget {
  final String slug;
  final String title;
  const _DeconstructionDetailScreen({required this.slug, required this.title});

  @override
  State<_DeconstructionDetailScreen> createState() => _DeconstructionDetailScreenState();
}

class _DeconstructionDetailScreenState extends State<_DeconstructionDetailScreen> {
  final _api = ApiService();
  Deconstruction? _detail;
  bool _loading = true;
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    _load();
    _refreshTimer = Timer.periodic(const Duration(seconds: 30), (_) => _silentRefresh());
  }

  Future<void> _load() async {
    try {
      final data = await _api.getDeconstruction(widget.slug);
      if (mounted) setState(() { _detail = Deconstruction.fromJson(data); _loading = false; });
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _silentRefresh() async {
    try {
      final data = await _api.getDeconstruction(widget.slug);
      if (mounted) setState(() => _detail = Deconstruction.fromJson(data));
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(widget.title)),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _detail == null
              ? const Center(child: Text('加载失败'))
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                    padding: const EdgeInsets.all(16),
                    children: [
                      _buildProgressHeader(theme),
                      const SizedBox(height: 16),
                      if (_detail!.documents.isNotEmpty) ...[
                        Text('档案文档', style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.bold)),
                        const SizedBox(height: 8),
                        ..._detail!.documents.map((doc) => _buildDocCard(theme, doc)),
                      ],
                      const SizedBox(height: 80),
                    ],
                  ),
                ),
    );
  }

  Widget _buildProgressHeader(ThemeData theme) {
    final item = _detail!;
    final statusColor = item.isActive
        ? const Color(0xFF2196F3)
        : item.isCompleted
            ? const Color(0xFF4CAF50)
            : Colors.grey;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: statusColor.withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Text(item.statusText, style: TextStyle(fontSize: 12, color: statusColor, fontWeight: FontWeight.w600)),
                ),
                const Spacer(),
                if (item.totalChapters > 0)
                  Text('${item.completedChapters} / ${item.totalChapters} 章', style: theme.textTheme.bodySmall),
              ],
            ),
            if (item.totalChapters > 0) ...[
              const SizedBox(height: 12),
              ClipRRect(
                borderRadius: BorderRadius.circular(4),
                child: LinearProgressIndicator(
                  value: (item.progressPercent / 100).clamp(0.0, 1.0),
                  minHeight: 8,
                  backgroundColor: theme.colorScheme.surfaceContainerHighest,
                  valueColor: AlwaysStoppedAnimation(statusColor),
                ),
              ),
              const SizedBox(height: 6),
              Text(
                '${item.progressPercent.toStringAsFixed(1)}% 完成',
                style: TextStyle(fontSize: 13, color: statusColor, fontWeight: FontWeight.w600),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildDocCard(ThemeData theme, DeconstructionDoc doc) {
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: doc.content != null && doc.content!.isNotEmpty
          ? ExpansionTile(
              title: Text(doc.label, style: theme.textTheme.titleSmall),
              leading: const Icon(Icons.description, size: 20),
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                  child: SelectableText(
                    doc.content!,
                    style: theme.textTheme.bodyMedium?.copyWith(height: 1.7),
                  ),
                ),
              ],
            )
          : ListTile(
              leading: const Icon(Icons.description, size: 20),
              title: Text(doc.label, style: theme.textTheme.titleSmall),
              trailing: const Icon(Icons.chevron_right, size: 20),
            ),
    );
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    _api.dispose();
    super.dispose();
  }
}
