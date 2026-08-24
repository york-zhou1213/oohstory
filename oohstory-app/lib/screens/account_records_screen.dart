import 'package:flutter/material.dart';

import '../models/book.dart';
import '../services/account_service.dart';
import '../services/api_service.dart';
import '../theme/app_theme.dart';
import 'book_detail_screen.dart';
import 'reader_screen.dart';

class AccountRecordsScreen extends StatefulWidget {
  final String kind;

  const AccountRecordsScreen({super.key, required this.kind});

  @override
  State<AccountRecordsScreen> createState() => _AccountRecordsScreenState();
}

class _AccountRecordsScreenState extends State<AccountRecordsScreen> {
  static const _pageSize = 10;
  final _account = AccountService.instance;
  final _api = ApiService();
  bool _loading = true;
  String? _error;
  int _page = 1;

  String get _title => switch (widget.kind) {
    'history' => '阅读记录',
    'favorites' => '收藏记录',
    _ => '我的书架',
  };

  List<Map<String, dynamic>> get _items =>
      (_account.cloudState[widget.kind] as List? ?? const [])
          .map((item) => Map<String, dynamic>.from(item as Map))
          .toList();

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  @override
  void dispose() {
    _api.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      await _account.refreshCloudState();
      final pageCount = (_items.length / _pageSize)
          .ceil()
          .clamp(1, 1000000)
          .toInt();
      if (_page > pageCount) _page = pageCount;
    } catch (error) {
      _error = error.toString();
    }
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _open(Map<String, dynamic> item) async {
    final id = item['book_id'] as String? ?? '';
    if (id.isEmpty || item['catalog_available'] == false) return;
    if (widget.kind != 'history') {
      await Navigator.of(
        context,
      ).push(MaterialPageRoute(builder: (_) => BookDetailScreen(bookId: id)));
      return;
    }
    setState(() => _loading = true);
    try {
      final results = await Future.wait<dynamic>([
        _api.getBook(id),
        _api.getChapters(id),
      ]);
      final book = results[0] as Book;
      final chapters = results[1] as List<Chapter>;
      if (!mounted || chapters.isEmpty) return;
      final wanted =
          (item['chapter_id'] as num?)?.toInt().toString() ?? chapters.first.id;
      final chapterId = chapters.any((chapter) => chapter.id == wanted)
          ? wanted
          : chapters.first.id;
      await Navigator.of(context).push(
        MaterialPageRoute(
          builder: (_) => ReaderScreen(
            bookId: id,
            chapterId: chapterId,
            chapters: chapters,
            book: book,
          ),
        ),
      );
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('暂时无法续读：$error')));
      }
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _remove(Map<String, dynamic> item) async {
    final id = item['book_id'] as String? ?? '';
    if (id.isEmpty) return;
    await _account.removeState(widget.kind, id);
    if (mounted) {
      setState(() {
        final pageCount = (_items.length / _pageSize)
            .ceil()
            .clamp(1, 1000000)
            .toInt();
        if (_page > pageCount) _page = pageCount;
      });
    }
  }

  void _goToPage(int page, int pageCount) {
    setState(() => _page = page.clamp(1, pageCount).toInt());
  }

  Future<void> _openPageJump(int pageCount) async {
    final controller = TextEditingController(text: '$_page');
    final page = await showDialog<int>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('跳转页数'),
        content: TextField(
          controller: controller,
          autofocus: true,
          keyboardType: TextInputType.number,
          decoration: InputDecoration(
            hintText: '输入 1 - $pageCount',
            suffixText: '/ $pageCount 页',
          ),
          onSubmitted: (value) => Navigator.pop(context, int.tryParse(value)),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () =>
                Navigator.pop(context, int.tryParse(controller.text)),
            child: const Text('跳转'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (page != null && mounted) _goToPage(page, pageCount);
  }

  Future<void> _editNote(Map<String, dynamic> item) async {
    final controller = TextEditingController(
      text: item['note'] as String? ?? '',
    );
    final note = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('私人备注'),
        content: TextField(
          controller: controller,
          maxLength: 500,
          maxLines: 4,
          decoration: const InputDecoration(hintText: '只对自己可见'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text),
            child: const Text('保存'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (note == null) return;
    await _account.updateBookshelfNote(item, note);
    if (mounted) setState(() {});
  }

  String _relativeTime(String? raw) {
    final date = DateTime.tryParse(raw ?? '')?.toLocal();
    if (date == null) return '';
    final diff = DateTime.now().difference(date);
    if (diff.inMinutes < 1) return '刚刚';
    if (diff.inHours < 1) return '${diff.inMinutes} 分钟前';
    if (diff.inDays < 1) return '${diff.inHours} 小时前';
    if (diff.inDays < 7) return '${diff.inDays} 天前';
    return '${date.year}/${date.month}/${date.day}';
  }

  @override
  Widget build(BuildContext context) {
    final items = _items;
    final pageCount = (items.length / _pageSize)
        .ceil()
        .clamp(1, 1000000)
        .toInt();
    final currentPage = _page.clamp(1, pageCount).toInt();
    final start = (currentPage - 1) * _pageSize;
    final visibleItems = items.skip(start).take(_pageSize).toList();
    return Scaffold(
      appBar: AppBar(title: Text(_title)),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: _loading && items.isEmpty
            ? const Center(child: CircularProgressIndicator())
            : _error != null && items.isEmpty
            ? ListView(
                children: [_EmptyState(title: _error!, icon: Icons.cloud_off)],
              )
            : items.isEmpty
            ? ListView(
                children: [
                  _EmptyState(
                    title: '这里还没有$_title',
                    icon: widget.kind == 'favorites'
                        ? Icons.favorite_border
                        : widget.kind == 'history'
                        ? Icons.history
                        : Icons.shelves,
                  ),
                ],
              )
            : ListView(
                padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(2, 0, 2, 10),
                    child: Text(
                      '共 ${items.length} 条记录 · 第 $currentPage / $pageCount 页',
                      style: TextStyle(
                        fontSize: 11,
                        color: Theme.of(
                          context,
                        ).colorScheme.onSurface.withValues(alpha: .48),
                      ),
                    ),
                  ),
                  ...visibleItems.map(_recordCard),
                  _pagination(currentPage, pageCount),
                ],
              ),
      ),
    );
  }

  Widget _pagination(int currentPage, int pageCount) {
    var firstPage = currentPage - 2;
    if (firstPage < 1) firstPage = 1;
    if (firstPage + 4 > pageCount) {
      firstPage = (pageCount - 4).clamp(1, pageCount).toInt();
    }
    final lastPage = (firstPage + 4).clamp(1, pageCount).toInt();
    final pages = [for (var page = firstPage; page <= lastPage; page++) page];
    final theme = Theme.of(context);
    Widget action(
      String label,
      int target,
      bool enabled, {
      bool active = false,
    }) {
      return SizedBox(
        width: label.length > 1 ? 50 : 38,
        height: 38,
        child: active
            ? FilledButton(
                onPressed: () => _goToPage(target, pageCount),
                style: FilledButton.styleFrom(
                  padding: EdgeInsets.zero,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(9),
                  ),
                ),
                child: Text(label),
              )
            : OutlinedButton(
                onPressed: enabled ? () => _goToPage(target, pageCount) : null,
                style: OutlinedButton.styleFrom(
                  padding: EdgeInsets.zero,
                  side: BorderSide(
                    color: theme.colorScheme.outline.withValues(alpha: .2),
                  ),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(9),
                  ),
                ),
                child: Text(label, style: const TextStyle(fontSize: 12)),
              ),
      );
    }

    return Container(
      margin: const EdgeInsets.only(top: 14),
      padding: const EdgeInsets.fromLTRB(10, 14, 10, 12),
      decoration: BoxDecoration(
        color: theme.colorScheme.surface,
        border: Border.all(
          color: theme.colorScheme.outline.withValues(alpha: .12),
        ),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        children: [
          Wrap(
            alignment: WrapAlignment.center,
            spacing: 6,
            runSpacing: 8,
            children: [
              action('<', currentPage - 1, currentPage > 1),
              ...pages.map(
                (page) =>
                    action('$page', page, true, active: page == currentPage),
              ),
              action('>', currentPage + 1, currentPage < pageCount),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              action('首页', 1, currentPage > 1),
              const SizedBox(width: 8),
              OutlinedButton.icon(
                onPressed: () => _openPageJump(pageCount),
                icon: const Icon(Icons.numbers_rounded, size: 15),
                label: const Text('自定义页数 · 跳转'),
                style: OutlinedButton.styleFrom(
                  minimumSize: const Size(0, 38),
                  visualDensity: VisualDensity.compact,
                ),
              ),
              const SizedBox(width: 8),
              action('尾页', pageCount, currentPage < pageCount),
            ],
          ),
        ],
      ),
    );
  }

  Widget _recordCard(Map<String, dynamic> item) {
    final theme = Theme.of(context);
    final dark = theme.brightness == Brightness.dark;
    final title = item['title'] as String? ?? '未命名作品';
    final author = item['author'] as String? ?? '';
    final status = item['serialization_status'] == 'finished' ? '已完结' : '连载中';
    final cover = _api.fullCoverUrl(item['cover_url'] as String?);
    final available = item['catalog_available'] != false;
    final progress = ((item['overall_progress'] as num?)?.toDouble() ?? 0)
        .clamp(0.0, 1.0);
    return Opacity(
      opacity: available ? 1 : .58,
      child: Container(
        margin: const EdgeInsets.only(bottom: 12),
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: dark ? const Color(0xFF1E1E30) : Colors.white,
          borderRadius: BorderRadius.circular(16),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: .04),
              blurRadius: 10,
            ),
          ],
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            SizedBox(
              width: 68,
              height: 94,
              child: ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: cover.isEmpty
                    ? _coverFallback(title)
                    : Image.network(
                        cover,
                        fit: BoxFit.cover,
                        errorBuilder: (_, __, ___) => _coverFallback(title),
                      ),
              ),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: InkWell(
                onTap: available ? () => _open(item) : null,
                borderRadius: BorderRadius.circular(10),
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 2),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Wrap(
                        spacing: 8,
                        runSpacing: 4,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        children: [
                          Text(
                            title,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontSize: 15,
                              fontWeight: FontWeight.w800,
                            ),
                          ),
                          _MetaPill(text: status),
                          if (_relativeTime(
                            item['updated_at'] as String?,
                          ).isNotEmpty)
                            Text(
                              _relativeTime(item['updated_at'] as String?),
                              style: TextStyle(
                                fontSize: 10,
                                color: theme.colorScheme.onSurface.withValues(
                                  alpha: .4,
                                ),
                              ),
                            ),
                        ],
                      ),
                      const SizedBox(height: 4),
                      Text(
                        author.isEmpty ? '作者未收录' : author,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontSize: 12,
                          color: theme.colorScheme.onSurface.withValues(
                            alpha: .52,
                          ),
                        ),
                      ),
                      if (widget.kind == 'history') ...[
                        const SizedBox(height: 8),
                        if (item['serialization_status'] != 'finished' &&
                            (item['latest_chapter'] as String? ?? '')
                                .isNotEmpty)
                          Text(
                            '最新 ${item['latest_chapter']}',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontSize: 11,
                              color: AppTheme.seedPurple,
                            ),
                          ),
                        const SizedBox(height: 4),
                        Text(
                          '${item['current_chapter'] ?? '第 1 章'} · 全书进度 ${(progress * 100).toStringAsFixed(progress * 100 % 1 == 0 ? 0 : 1)}%',
                          style: TextStyle(
                            fontSize: 11,
                            color: theme.colorScheme.onSurface.withValues(
                              alpha: .58,
                            ),
                          ),
                        ),
                        const SizedBox(height: 6),
                        LinearProgressIndicator(
                          value: progress,
                          minHeight: 5,
                          borderRadius: BorderRadius.circular(5),
                          color: AppTheme.seedPurple,
                          backgroundColor: AppTheme.seedPurple.withValues(
                            alpha: .1,
                          ),
                        ),
                      ],
                      if (widget.kind == 'bookshelf' &&
                          (item['note'] as String? ?? '').isNotEmpty) ...[
                        const SizedBox(height: 7),
                        Text(
                          item['note'] as String,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            fontSize: 11,
                            color: theme.colorScheme.onSurface.withValues(
                              alpha: .55,
                            ),
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
              ),
            ),
            PopupMenuButton<String>(
              onSelected: (value) =>
                  value == 'note' ? _editNote(item) : _remove(item),
              itemBuilder: (_) => [
                if (widget.kind == 'bookshelf')
                  const PopupMenuItem(value: 'note', child: Text('编辑私人备注')),
                const PopupMenuItem(value: 'remove', child: Text('移除记录')),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _coverFallback(String title) => Container(
    alignment: Alignment.center,
    color: AppTheme.seedPurple.withValues(alpha: .1),
    child: Text(
      title.characters.take(2).toString(),
      style: const TextStyle(
        fontWeight: FontWeight.w800,
        color: AppTheme.seedPurple,
      ),
    ),
  );
}

class _MetaPill extends StatelessWidget {
  final String text;
  const _MetaPill({required this.text});

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
    decoration: BoxDecoration(
      color: AppTheme.seedPurple.withValues(alpha: .1),
      borderRadius: BorderRadius.circular(20),
    ),
    child: Text(
      text,
      style: const TextStyle(
        fontSize: 9,
        color: AppTheme.seedPurple,
        fontWeight: FontWeight.w700,
      ),
    ),
  );
}

class _EmptyState extends StatelessWidget {
  final String title;
  final IconData icon;
  const _EmptyState({required this.title, required this.icon});

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(top: 120),
    child: Column(
      children: [
        Icon(
          icon,
          size: 52,
          color: Theme.of(context).colorScheme.onSurface.withValues(alpha: .14),
        ),
        const SizedBox(height: 14),
        Text(
          title,
          style: TextStyle(
            color: Theme.of(
              context,
            ).colorScheme.onSurface.withValues(alpha: .45),
          ),
        ),
      ],
    ),
  );
}
