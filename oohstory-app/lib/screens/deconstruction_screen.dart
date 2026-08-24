import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import '../services/api_service.dart';
import '../models/book.dart';
import '../theme/app_theme.dart';

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
    _refreshTimer = Timer.periodic(
      const Duration(seconds: 30),
      (_) => _silentRefresh(),
    );
  }

  Future<void> _load() async {
    try {
      final items = await _api.getDeconstructions();
      if (mounted) {
        setState(() {
          _items = items;
          _loading = false;
        });
      }
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
    final theme = Theme.of(context);
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_items.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.auto_stories,
              size: 48,
              color: theme.colorScheme.onSurface.withValues(alpha: 0.2),
            ),
            const SizedBox(height: 12),
            Text(
              '暂无拆书档案',
              style: theme.textTheme.bodyMedium?.copyWith(
                color: theme.colorScheme.onSurface.withValues(alpha: 0.4),
              ),
            ),
          ],
        ),
      );
    }

    final active = _items.where((i) => i.isActive).toList();
    final completed = _items.where((i) => i.isCompleted).toList();
    final pending = _items.where((i) => !i.isActive && !i.isCompleted).toList();

    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(20, 12, 20, 100),
        children: [
          if (active.isNotEmpty) ...[
            _sectionHeader(
              theme,
              '拆解中',
              active.length,
              const Color(0xFF6C5CE7),
            ),
            ...active.map((item) => _buildCard(theme, item)),
            const SizedBox(height: 20),
          ],
          if (completed.isNotEmpty) ...[
            _sectionHeader(
              theme,
              '已完成',
              completed.length,
              const Color(0xFF00B894),
            ),
            ...completed.map((item) => _buildCard(theme, item)),
            const SizedBox(height: 20),
          ],
          if (pending.isNotEmpty) ...[
            _sectionHeader(
              theme,
              '待处理',
              pending.length,
              const Color(0xFFB2BEC3),
            ),
            ...pending.map((item) => _buildCard(theme, item)),
          ],
        ],
      ),
    );
  }

  Widget _sectionHeader(ThemeData theme, String title, int count, Color color) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        children: [
          Container(
            width: 4,
            height: 20,
            decoration: BoxDecoration(
              color: color,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          const SizedBox(width: 10),
          Text(
            title,
            style: theme.textTheme.titleSmall?.copyWith(
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(width: 8),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
            decoration: BoxDecoration(
              color: color.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(10),
            ),
            child: Text(
              '$count',
              style: TextStyle(
                fontSize: 11,
                color: color,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildCard(ThemeData theme, Deconstruction item) {
    final statusColor = item.isActive
        ? AppTheme.seedPurple
        : item.isCompleted
        ? const Color(0xFF00B894)
        : const Color(0xFFB2BEC3);
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: BoxDecoration(
        color: theme.cardTheme.color,
        borderRadius: BorderRadius.circular(16),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.04),
            blurRadius: 10,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: Material(
        color: Colors.transparent,
        borderRadius: BorderRadius.circular(16),
        child: InkWell(
          borderRadius: BorderRadius.circular(16),
          onTap: () => Navigator.of(context).push(
            MaterialPageRoute(
              builder: (_) => DeconstructionDetailScreen(
                slug: item.slug,
                title: item.title,
              ),
            ),
          ),
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                ClipRRect(
                  borderRadius: BorderRadius.circular(10),
                  child: item.coverUrl != null
                      ? Image.network(
                          '${ApiService.baseUrl}${item.coverUrl}',
                          width: 68,
                          height: 94,
                          fit: BoxFit.cover,
                          errorBuilder: (_, __, ___) =>
                              _coverPlaceholder(theme, item),
                        )
                      : _coverPlaceholder(theme, item),
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              item.title,
                              style: theme.textTheme.titleSmall?.copyWith(
                                fontWeight: FontWeight.w700,
                              ),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                          Container(
                            padding: const EdgeInsets.symmetric(
                              horizontal: 8,
                              vertical: 3,
                            ),
                            decoration: BoxDecoration(
                              color: statusColor.withValues(alpha: 0.1),
                              borderRadius: BorderRadius.circular(6),
                            ),
                            child: Text(
                              item.statusText,
                              style: TextStyle(
                                fontSize: 10,
                                color: statusColor,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          ),
                        ],
                      ),
                      if (item.totalChapters > 0) ...[
                        const SizedBox(height: 10),
                        Row(
                          children: [
                            Expanded(
                              child: ClipRRect(
                                borderRadius: BorderRadius.circular(4),
                                child: LinearProgressIndicator(
                                  value: (item.progressPercent / 100).clamp(
                                    0.0,
                                    1.0,
                                  ),
                                  minHeight: 5,
                                  backgroundColor: theme
                                      .colorScheme
                                      .surfaceContainerHighest
                                      .withValues(alpha: 0.5),
                                  valueColor: AlwaysStoppedAnimation(
                                    statusColor,
                                  ),
                                ),
                              ),
                            ),
                            const SizedBox(width: 10),
                            Text(
                              '${item.progressPercent.toStringAsFixed(0)}%',
                              style: TextStyle(
                                fontSize: 11,
                                color: statusColor,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 6),
                        Text(
                          '${item.completedChapters}/${item.totalChapters} 章已拆解',
                          style: theme.textTheme.labelSmall?.copyWith(
                            color: theme.colorScheme.onSurface.withValues(
                              alpha: 0.45,
                            ),
                          ),
                        ),
                      ],
                      if (item.documents.isNotEmpty) ...[
                        const SizedBox(height: 10),
                        Wrap(
                          spacing: 6,
                          runSpacing: 6,
                          children: item.documents
                              .take(4)
                              .map(
                                (doc) => Container(
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: 8,
                                    vertical: 4,
                                  ),
                                  decoration: BoxDecoration(
                                    color: AppTheme.seedPurple.withValues(
                                      alpha: 0.06,
                                    ),
                                    borderRadius: BorderRadius.circular(6),
                                    border: Border.all(
                                      color: AppTheme.seedPurple.withValues(
                                        alpha: 0.1,
                                      ),
                                    ),
                                  ),
                                  child: Row(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      Icon(
                                        _docIcon(doc.label),
                                        size: 12,
                                        color: AppTheme.seedPurple.withValues(
                                          alpha: 0.6,
                                        ),
                                      ),
                                      const SizedBox(width: 4),
                                      Text(
                                        doc.label,
                                        style: TextStyle(
                                          fontSize: 10,
                                          color: AppTheme.seedPurple,
                                          fontWeight: FontWeight.w500,
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                              )
                              .toList(),
                        ),
                      ],
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _coverPlaceholder(ThemeData theme, Deconstruction item) {
    return Container(
      width: 68,
      height: 94,
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            AppTheme.seedPurple.withValues(alpha: 0.6),
            AppTheme.accentPink.withValues(alpha: 0.4),
          ],
        ),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Center(
        child: Text(
          item.title.length > 2 ? item.title.substring(0, 2) : item.title,
          style: const TextStyle(
            color: Colors.white,
            fontSize: 16,
            fontWeight: FontWeight.w700,
          ),
        ),
      ),
    );
  }

  static IconData _docIcon(String label) {
    if (label.contains('黄金')) return Icons.stars;
    if (label.contains('概要')) return Icons.summarize;
    if (label.contains('拆文') || label.contains('报告')) return Icons.analytics;
    if (label.contains('文风')) return Icons.style;
    return Icons.description;
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    _api.dispose();
    super.dispose();
  }
}

// ── Detail screen ──────────────────────────────────────────────

class DeconstructionDetailScreen extends StatefulWidget {
  final String slug;
  final String title;
  const DeconstructionDetailScreen({
    super.key,
    required this.slug,
    required this.title,
  });

  @override
  State<DeconstructionDetailScreen> createState() =>
      _DeconstructionDetailScreenState();
}

class _DeconstructionDetailScreenState extends State<DeconstructionDetailScreen>
    with TickerProviderStateMixin {
  final _api = ApiService();
  Deconstruction? _detail;
  bool _loading = true;
  Timer? _refreshTimer;
  TabController? _tabController;

  @override
  void initState() {
    super.initState();
    _load();
    _refreshTimer = Timer.periodic(
      const Duration(seconds: 30),
      (_) => _silentRefresh(),
    );
  }

  Future<void> _load() async {
    try {
      final data = await _api.getDeconstruction(widget.slug);
      if (mounted) {
        final detail = Deconstruction.fromJson(data);
        _rebuildTabs(detail);
        setState(() {
          _detail = detail;
          _loading = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _silentRefresh() async {
    try {
      final data = await _api.getDeconstruction(widget.slug);
      if (mounted) {
        final detail = Deconstruction.fromJson(data);
        final prev = _tabController?.index ?? 0;
        _rebuildTabs(detail, initialIndex: prev);
        setState(() => _detail = detail);
      }
    } catch (_) {}
  }

  void _rebuildTabs(Deconstruction detail, {int initialIndex = 0}) {
    final totalTabs = detail.documents.length + detail.subdirectories.length;
    if (totalTabs > 0 &&
        (_tabController == null || _tabController!.length != totalTabs)) {
      _tabController?.dispose();
      _tabController = TabController(
        length: totalTabs,
        vsync: this,
        initialIndex: initialIndex.clamp(0, totalTabs - 1),
      );
    }
  }

  IconData _subdirIcon(String name) {
    if (name == '剧情') return Icons.movie_creation;
    if (name == '角色') return Icons.people;
    if (name == '设定') return Icons.settings_suggest;
    if (name == '章节') return Icons.list_alt;
    return Icons.folder;
  }

  IconData _docTabIcon(String label) {
    if (label.contains('黄金')) return Icons.stars;
    if (label.contains('概要')) return Icons.summarize;
    if (label.contains('拆文') || label.contains('报告')) return Icons.analytics;
    if (label.contains('文风')) return Icons.style;
    return Icons.description;
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    if (_loading) {
      return Scaffold(
        appBar: AppBar(title: Text(widget.title)),
        body: const Center(child: CircularProgressIndicator()),
      );
    }
    if (_detail == null) {
      return Scaffold(
        appBar: AppBar(title: Text(widget.title)),
        body: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                Icons.error_outline,
                size: 48,
                color: theme.colorScheme.error.withValues(alpha: 0.5),
              ),
              const SizedBox(height: 12),
              const Text('加载失败'),
              const SizedBox(height: 12),
              FilledButton(
                onPressed: () {
                  setState(() => _loading = true);
                  _load();
                },
                child: const Text('重试'),
              ),
            ],
          ),
        ),
      );
    }
    return _buildContent(theme);
  }

  Widget _buildContent(ThemeData theme) {
    final item = _detail!;
    final totalTabs = item.documents.length + item.subdirectories.length;

    return Scaffold(
      body: NestedScrollView(
        headerSliverBuilder: (context, innerScrolled) => [
          SliverAppBar(
            expandedHeight: 200,
            pinned: true,
            stretch: true,
            backgroundColor: theme.appBarTheme.backgroundColor,
            flexibleSpace: FlexibleSpaceBar(
              background: _buildHeader(theme, item),
            ),
          ),
          if (totalTabs > 0 && _tabController != null)
            SliverPersistentHeader(
              pinned: true,
              delegate: _TabBarDelegate(
                TabBar(
                  controller: _tabController,
                  isScrollable: true,
                  tabAlignment: TabAlignment.start,
                  indicatorColor: AppTheme.seedPurple,
                  indicatorWeight: 3,
                  indicatorSize: TabBarIndicatorSize.label,
                  labelColor: AppTheme.seedPurple,
                  unselectedLabelColor: theme.colorScheme.onSurface.withValues(
                    alpha: 0.45,
                  ),
                  labelStyle: const TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                  ),
                  unselectedLabelStyle: const TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w500,
                  ),
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  tabs: [
                    ...item.documents.map(
                      (doc) => Tab(
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(_docTabIcon(doc.label), size: 14),
                            const SizedBox(width: 6),
                            Text(doc.label),
                          ],
                        ),
                      ),
                    ),
                    ...item.subdirectories.map(
                      (sd) => Tab(
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(_subdirIcon(sd.name), size: 14),
                            const SizedBox(width: 6),
                            Text(sd.label),
                          ],
                        ),
                      ),
                    ),
                  ],
                ),
                theme.scaffoldBackgroundColor,
              ),
            ),
        ],
        body: totalTabs > 0 && _tabController != null
            ? TabBarView(
                controller: _tabController,
                children: [
                  ...item.documents.map(
                    (doc) => _buildMarkdownTab(theme, doc.content),
                  ),
                  ...item.subdirectories.map(
                    (sd) => _buildSubdirTab(theme, sd),
                  ),
                ],
              )
            : Center(
                child: Padding(
                  padding: const EdgeInsets.all(40),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(
                        Icons.hourglass_empty,
                        size: 48,
                        color: theme.colorScheme.onSurface.withValues(
                          alpha: 0.2,
                        ),
                      ),
                      const SizedBox(height: 16),
                      Text(
                        item.isActive ? '拆解进行中…' : '暂无可用文档',
                        style: theme.textTheme.bodyMedium?.copyWith(
                          color: theme.colorScheme.onSurface.withValues(
                            alpha: 0.45,
                          ),
                        ),
                        textAlign: TextAlign.center,
                      ),
                    ],
                  ),
                ),
              ),
      ),
    );
  }

  Widget _buildHeader(ThemeData theme, Deconstruction item) {
    final statusColor = item.isActive
        ? AppTheme.seedPurple
        : item.isCompleted
        ? const Color(0xFF00B894)
        : const Color(0xFFB2BEC3);
    return Container(
      decoration: const BoxDecoration(gradient: AppTheme.heroGradient),
      child: SafeArea(
        bottom: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 56, 20, 20),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(10),
                child: item.coverUrl != null
                    ? Image.network(
                        '${ApiService.baseUrl}${item.coverUrl}',
                        width: 72,
                        height: 100,
                        fit: BoxFit.cover,
                        errorBuilder: (_, __, ___) => _headerPlaceholder(item),
                      )
                    : _headerPlaceholder(item),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      item.title,
                      style: const TextStyle(
                        fontSize: 20,
                        fontWeight: FontWeight.w800,
                        color: Colors.white,
                        height: 1.2,
                      ),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 10),
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 10,
                        vertical: 4,
                      ),
                      decoration: BoxDecoration(
                        color: statusColor.withValues(alpha: 0.25),
                        borderRadius: BorderRadius.circular(6),
                      ),
                      child: Text(
                        item.statusText,
                        style: const TextStyle(
                          fontSize: 11,
                          color: Colors.white,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                    if (item.totalChapters > 0) ...[
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          Expanded(
                            child: ClipRRect(
                              borderRadius: BorderRadius.circular(4),
                              child: LinearProgressIndicator(
                                value: (item.progressPercent / 100).clamp(
                                  0.0,
                                  1.0,
                                ),
                                minHeight: 5,
                                backgroundColor: Colors.white.withValues(
                                  alpha: 0.2,
                                ),
                                valueColor: const AlwaysStoppedAnimation(
                                  Colors.white,
                                ),
                              ),
                            ),
                          ),
                          const SizedBox(width: 10),
                          Text(
                            '${item.completedChapters}/${item.totalChapters}',
                            style: TextStyle(
                              fontSize: 12,
                              color: Colors.white.withValues(alpha: 0.9),
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ],
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

  Widget _headerPlaceholder(Deconstruction item) {
    return Container(
      width: 72,
      height: 100,
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.15),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Center(
        child: Text(
          item.title.length > 2 ? item.title.substring(0, 2) : item.title,
          style: const TextStyle(
            color: Colors.white,
            fontSize: 16,
            fontWeight: FontWeight.w700,
          ),
        ),
      ),
    );
  }

  Widget _buildMarkdownTab(ThemeData theme, String? content) {
    if (content == null || content.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.hourglass_empty,
              size: 40,
              color: theme.colorScheme.onSurface.withValues(alpha: 0.2),
            ),
            const SizedBox(height: 12),
            Text(
              '文档内容生成中…',
              style: theme.textTheme.bodyMedium?.copyWith(
                color: theme.colorScheme.onSurface.withValues(alpha: 0.4),
              ),
            ),
          ],
        ),
      );
    }
    return Markdown(
      data: content,
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 80),
      selectable: true,
      styleSheet: _mdStyle(theme),
    );
  }

  Widget _buildSubdirTab(ThemeData theme, DeconstructionSubdir subdir) {
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 80),
      itemCount: subdir.items.length,
      itemBuilder: (context, i) {
        final entry = subdir.items[i];
        if (entry.isDirectory) {
          return _buildSubdirFolder(theme, subdir, entry);
        }
        return _buildFileItem(theme, subdir.name, entry);
      },
    );
  }

  Widget _buildSubdirFolder(
    ThemeData theme,
    DeconstructionSubdir parentDir,
    DeconstructionEntry folder,
  ) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(4, 12, 4, 8),
          child: Row(
            children: [
              Icon(
                Icons.folder,
                size: 18,
                color: AppTheme.seedPurple.withValues(alpha: 0.6),
              ),
              const SizedBox(width: 8),
              Text(
                folder.label,
                style: theme.textTheme.titleSmall?.copyWith(
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(width: 8),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                decoration: BoxDecoration(
                  color: AppTheme.seedPurple.withValues(alpha: 0.08),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  '${folder.items.length}',
                  style: TextStyle(
                    fontSize: 10,
                    color: AppTheme.seedPurple,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ],
          ),
        ),
        ...folder.items.map(
          (child) =>
              _buildFileItem(theme, '${parentDir.name}/${folder.name}', child),
        ),
        const SizedBox(height: 8),
      ],
    );
  }

  Widget _buildFileItem(
    ThemeData theme,
    String subdirPath,
    DeconstructionEntry entry,
  ) {
    return Container(
      margin: const EdgeInsets.only(bottom: 4),
      decoration: BoxDecoration(
        color: theme.cardTheme.color,
        borderRadius: BorderRadius.circular(12),
      ),
      child: ListTile(
        dense: true,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        leading: Container(
          width: 32,
          height: 32,
          decoration: BoxDecoration(
            color: AppTheme.seedPurple.withValues(alpha: 0.08),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Icon(
            _fileIcon(subdirPath),
            size: 16,
            color: AppTheme.seedPurple,
          ),
        ),
        title: Text(
          entry.label,
          style: theme.textTheme.bodyMedium?.copyWith(
            fontWeight: FontWeight.w500,
          ),
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
        ),
        trailing: Icon(
          Icons.chevron_right,
          size: 18,
          color: theme.colorScheme.onSurface.withValues(alpha: 0.3),
        ),
        onTap: () {
          final filePath = '$subdirPath/${entry.filename}';
          Navigator.of(context).push(
            MaterialPageRoute(
              builder: (_) => _FileViewScreen(
                slug: widget.slug,
                filePath: filePath,
                title: entry.label,
              ),
            ),
          );
        },
      ),
    );
  }

  IconData _fileIcon(String path) {
    if (path.startsWith('剧情')) return Icons.movie_creation;
    if (path.startsWith('角色')) return Icons.person;
    if (path.contains('世界观')) return Icons.public;
    if (path.contains('势力')) return Icons.groups;
    if (path.startsWith('设定')) return Icons.settings_suggest;
    if (path.startsWith('章节')) return Icons.article;
    return Icons.description;
  }

  MarkdownStyleSheet _mdStyle(ThemeData theme) {
    return MarkdownStyleSheet(
      h1: theme.textTheme.titleLarge?.copyWith(
        fontWeight: FontWeight.w800,
        height: 1.4,
      ),
      h2: theme.textTheme.titleMedium?.copyWith(
        fontWeight: FontWeight.w700,
        height: 1.5,
      ),
      h3: theme.textTheme.titleSmall?.copyWith(
        fontWeight: FontWeight.w700,
        height: 1.5,
      ),
      p: theme.textTheme.bodyMedium?.copyWith(height: 1.75, fontSize: 14),
      listBullet: theme.textTheme.bodyMedium?.copyWith(
        height: 1.75,
        fontSize: 14,
      ),
      blockquotePadding: const EdgeInsets.fromLTRB(16, 8, 8, 8),
      blockquoteDecoration: BoxDecoration(
        border: Border(
          left: BorderSide(
            color: AppTheme.seedPurple.withValues(alpha: 0.4),
            width: 3,
          ),
        ),
        color: AppTheme.seedPurple.withValues(alpha: 0.04),
      ),
      code: TextStyle(
        fontSize: 13,
        color: AppTheme.seedPurple,
        backgroundColor: AppTheme.seedPurple.withValues(alpha: 0.06),
      ),
      codeblockDecoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(10),
      ),
      codeblockPadding: const EdgeInsets.all(14),
      tableBorder: TableBorder.all(
        color: theme.colorScheme.onSurface.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(8),
      ),
      tableHead: theme.textTheme.bodySmall?.copyWith(
        fontWeight: FontWeight.w700,
      ),
      tableBody: theme.textTheme.bodySmall?.copyWith(height: 1.5),
      tableCellsPadding: const EdgeInsets.symmetric(
        horizontal: 10,
        vertical: 6,
      ),
      horizontalRuleDecoration: BoxDecoration(
        border: Border(
          top: BorderSide(
            color: theme.colorScheme.onSurface.withValues(alpha: 0.08),
          ),
        ),
      ),
    );
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    _tabController?.dispose();
    _api.dispose();
    super.dispose();
  }
}

// ── File viewer screen ─────────────────────────────────────────

class _FileViewScreen extends StatefulWidget {
  final String slug;
  final String filePath;
  final String title;
  const _FileViewScreen({
    required this.slug,
    required this.filePath,
    required this.title,
  });

  @override
  State<_FileViewScreen> createState() => _FileViewScreenState();
}

class _FileViewScreenState extends State<_FileViewScreen> {
  final _api = ApiService();
  String? _content;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await _api.getDeconstructionFile(
        widget.slug,
        widget.filePath,
      );
      if (mounted) {
        setState(() {
          _content = data['content'] as String?;
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

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.title, style: const TextStyle(fontSize: 16)),
        elevation: 0,
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
          ? Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    Icons.error_outline,
                    size: 48,
                    color: theme.colorScheme.error.withValues(alpha: 0.5),
                  ),
                  const SizedBox(height: 12),
                  Text('加载失败', style: theme.textTheme.bodyMedium),
                  const SizedBox(height: 12),
                  FilledButton(
                    onPressed: () {
                      setState(() {
                        _loading = true;
                        _error = null;
                      });
                      _load();
                    },
                    child: const Text('重试'),
                  ),
                ],
              ),
            )
          : _content != null && _content!.isNotEmpty
          ? Markdown(
              data: _content!,
              padding: const EdgeInsets.fromLTRB(20, 16, 20, 80),
              selectable: true,
              styleSheet: MarkdownStyleSheet(
                h1: theme.textTheme.titleLarge?.copyWith(
                  fontWeight: FontWeight.w800,
                  height: 1.4,
                ),
                h2: theme.textTheme.titleMedium?.copyWith(
                  fontWeight: FontWeight.w700,
                  height: 1.5,
                ),
                h3: theme.textTheme.titleSmall?.copyWith(
                  fontWeight: FontWeight.w700,
                  height: 1.5,
                ),
                p: theme.textTheme.bodyMedium?.copyWith(
                  height: 1.75,
                  fontSize: 14,
                ),
                listBullet: theme.textTheme.bodyMedium?.copyWith(
                  height: 1.75,
                  fontSize: 14,
                ),
                blockquotePadding: const EdgeInsets.fromLTRB(16, 8, 8, 8),
                blockquoteDecoration: BoxDecoration(
                  border: Border(
                    left: BorderSide(
                      color: AppTheme.seedPurple.withValues(alpha: 0.4),
                      width: 3,
                    ),
                  ),
                  color: AppTheme.seedPurple.withValues(alpha: 0.04),
                ),
                code: TextStyle(
                  fontSize: 13,
                  color: AppTheme.seedPurple,
                  backgroundColor: AppTheme.seedPurple.withValues(alpha: 0.06),
                ),
                codeblockDecoration: BoxDecoration(
                  color: theme.colorScheme.surfaceContainerHighest.withValues(
                    alpha: 0.5,
                  ),
                  borderRadius: BorderRadius.circular(10),
                ),
                tableBorder: TableBorder.all(
                  color: theme.colorScheme.onSurface.withValues(alpha: 0.1),
                  borderRadius: BorderRadius.circular(8),
                ),
                tableHead: theme.textTheme.bodySmall?.copyWith(
                  fontWeight: FontWeight.w700,
                ),
                tableBody: theme.textTheme.bodySmall?.copyWith(height: 1.5),
                tableCellsPadding: const EdgeInsets.symmetric(
                  horizontal: 10,
                  vertical: 6,
                ),
              ),
            )
          : Center(
              child: Text(
                '暂无内容',
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.onSurface.withValues(alpha: 0.4),
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

// ── Tab bar delegate ───────────────────────────────────────────

class _TabBarDelegate extends SliverPersistentHeaderDelegate {
  final TabBar tabBar;
  final Color backgroundColor;
  _TabBarDelegate(this.tabBar, this.backgroundColor);

  @override
  double get minExtent => tabBar.preferredSize.height;
  @override
  double get maxExtent => tabBar.preferredSize.height;

  @override
  Widget build(
    BuildContext context,
    double shrinkOffset,
    bool overlapsContent,
  ) {
    return Container(color: backgroundColor, child: tabBar);
  }

  @override
  bool shouldRebuild(covariant _TabBarDelegate oldDelegate) =>
      tabBar != oldDelegate.tabBar ||
      backgroundColor != oldDelegate.backgroundColor;
}
