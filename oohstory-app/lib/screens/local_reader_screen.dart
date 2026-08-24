import 'dart:async';

import 'package:flutter/material.dart';
import '../models/reader_preferences.dart';
import '../services/local_storage_service.dart';
import '../theme/app_theme.dart';
import '../utils/reader_pagination.dart';

class LocalReaderScreen extends StatefulWidget {
  final LocalBookInfo book;
  const LocalReaderScreen({super.key, required this.book});

  @override
  State<LocalReaderScreen> createState() => _LocalReaderScreenState();
}

class _LocalReaderScreenState extends State<LocalReaderScreen> {
  final _storage = LocalStorageService();
  final _scrollController = ScrollController();
  final _pageController = PageController();
  final _sessionStartedAt = DateTime.now();
  String? _content;
  bool _loading = true;
  bool _showControls = true;
  ReaderPreferences _preferences = const ReaderPreferences();
  double _progress = 0;

  static const _backgrounds = [
    Color(0xFFF7F1E5),
    Color(0xFFFFFDF8),
    Color(0xFFEAF2E7),
    Color(0xFF171C26),
  ];

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_updateScrollProgress);
    unawaited(_load());
  }

  Future<void> _load() async {
    await _storage.init();
    final content = await _storage.getLocalBookContent(widget.book.id);
    final preferences = _storage.getReaderPreferences();
    if (!mounted) return;
    setState(() {
      _content = content;
      _preferences = preferences;
      _progress = widget.book.progress.clamp(0, 1);
      _loading = false;
    });
    WidgetsBinding.instance.addPostFrameCallback((_) => _restoreProgress());
  }

  void _restoreProgress() {
    if (!mounted || _progress <= 0) return;
    if (_preferences.viewMode == ReaderViewMode.scroll &&
        _scrollController.hasClients) {
      _scrollController.jumpTo(
        _scrollController.position.maxScrollExtent * _progress,
      );
    }
  }

  void _updateScrollProgress() {
    if (!_scrollController.hasClients ||
        _preferences.viewMode != ReaderViewMode.scroll) {
      return;
    }
    final max = _scrollController.position.maxScrollExtent;
    final next = max <= 0
        ? 0.0
        : (_scrollController.offset / max).clamp(0, 1).toDouble();
    _commitProgress(next);
  }

  void _commitProgress(double value) {
    if ((value - _progress).abs() < .002) return;
    _progress = value.clamp(0, 1);
    _storage.updateLocalBookProgress(widget.book.id, _progress);
    if (mounted) setState(() {});
  }

  Future<void> _savePreferences(ReaderPreferences preferences) async {
    setState(() => _preferences = preferences);
    await _storage.saveReaderPreferences(preferences);
  }

  void _showSettings() {
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (sheetContext) => StatefulBuilder(
        builder: (context, setSheetState) {
          void apply(ReaderPreferences next) {
            setSheetState(() => _preferences = next);
            unawaited(_savePreferences(next));
          }

          return SafeArea(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '阅读排版',
                    style: Theme.of(context).textTheme.titleLarge?.copyWith(
                      fontWeight: FontWeight.w900,
                    ),
                  ),
                  const SizedBox(height: 16),
                  SegmentedButton<ReaderViewMode>(
                    segments: ReaderViewMode.values
                        .map(
                          (mode) => ButtonSegment(
                            value: mode,
                            label: Text(switch (mode) {
                              ReaderViewMode.scroll => '滚动',
                              ReaderViewMode.page => '单页',
                              ReaderViewMode.spread => '双页',
                            }),
                            icon: Icon(switch (mode) {
                              ReaderViewMode.scroll => Icons.swap_vert_rounded,
                              ReaderViewMode.page =>
                                Icons.crop_portrait_rounded,
                              ReaderViewMode.spread => Icons.menu_book_rounded,
                            }),
                          ),
                        )
                        .toList(),
                    selected: {_preferences.viewMode},
                    onSelectionChanged: (selection) {
                      apply(_preferences.copyWith(viewMode: selection.first));
                    },
                  ),
                  const SizedBox(height: 18),
                  _SettingSlider(
                    label: '字号',
                    value: _preferences.fontSize,
                    min: 14,
                    max: 30,
                    divisions: 16,
                    valueLabel: _preferences.fontSize.round().toString(),
                    onChanged: (value) =>
                        apply(_preferences.copyWith(fontSize: value)),
                  ),
                  _SettingSlider(
                    label: '行距',
                    value: _preferences.lineHeight,
                    min: 1.3,
                    max: 2.4,
                    divisions: 11,
                    valueLabel: _preferences.lineHeight.toStringAsFixed(1),
                    onChanged: (value) =>
                        apply(_preferences.copyWith(lineHeight: value)),
                  ),
                  const SizedBox(height: 10),
                  Wrap(
                    spacing: 12,
                    children: List.generate(
                      _backgrounds.length,
                      (index) => InkWell(
                        onTap: () => apply(
                          _preferences.copyWith(backgroundIndex: index),
                        ),
                        customBorder: const CircleBorder(),
                        child: Container(
                          width: 42,
                          height: 42,
                          decoration: BoxDecoration(
                            color: _backgrounds[index],
                            shape: BoxShape.circle,
                            border: Border.all(
                              color: _preferences.backgroundIndex == index
                                  ? Theme.of(context).colorScheme.primary
                                  : Colors.black12,
                              width: _preferences.backgroundIndex == index
                                  ? 3
                                  : 1,
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          );
        },
      ),
    );
  }

  Future<void> _addBookmark() async {
    final excerpt = _currentExcerpt();
    _storage.addAnnotation(
      bookId: widget.book.id,
      type: 'bookmark',
      excerpt: excerpt,
      progress: _progress,
    );
    if (!mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(const SnackBar(content: Text('书签已保存在本机')));
  }

  Future<void> _addNote() async {
    final controller = TextEditingController();
    final note = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('添加阅读笔记'),
        content: TextField(
          controller: controller,
          autofocus: true,
          minLines: 3,
          maxLines: 8,
          decoration: const InputDecoration(hintText: '记录此刻的想法'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text.trim()),
            child: const Text('保存'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (note == null || note.isEmpty) return;
    _storage.addAnnotation(
      bookId: widget.book.id,
      type: 'note',
      excerpt: _currentExcerpt(),
      note: note,
      progress: _progress,
    );
  }

  void _showAnnotations() {
    final annotations = _storage.getAnnotations(bookId: widget.book.id);
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (context) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: .64,
        maxChildSize: .92,
        builder: (context, controller) => Column(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
              child: Row(
                children: [
                  Text(
                    '书签与笔记',
                    style: Theme.of(context).textTheme.titleLarge?.copyWith(
                      fontWeight: FontWeight.w900,
                    ),
                  ),
                  const Spacer(),
                  Text('${annotations.length} 条'),
                ],
              ),
            ),
            Expanded(
              child: annotations.isEmpty
                  ? const Center(child: Text('还没有书签或笔记'))
                  : ListView.separated(
                      controller: controller,
                      padding: const EdgeInsets.fromLTRB(12, 0, 12, 24),
                      itemCount: annotations.length,
                      separatorBuilder: (_, __) => const Divider(height: 1),
                      itemBuilder: (context, index) {
                        final item = annotations[index];
                        return ListTile(
                          leading: Icon(
                            item.type == 'note'
                                ? Icons.edit_note_rounded
                                : Icons.bookmark_rounded,
                          ),
                          title: Text(
                            item.note.isNotEmpty ? item.note : item.excerpt,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                          ),
                          subtitle: Text(
                            '${(item.progress * 100).round()}% · ${item.excerpt}',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                          trailing: IconButton(
                            icon: const Icon(Icons.delete_outline_rounded),
                            onPressed: () {
                              _storage.removeAnnotation(item.id);
                              Navigator.pop(context);
                              _showAnnotations();
                            },
                          ),
                          onTap: () {
                            Navigator.pop(context);
                            _jumpToProgress(item.progress);
                          },
                        );
                      },
                    ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _search() async {
    final controller = TextEditingController();
    final query = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('书内搜索'),
        content: TextField(
          controller: controller,
          autofocus: true,
          textInputAction: TextInputAction.search,
          onSubmitted: (value) => Navigator.pop(context, value.trim()),
          decoration: const InputDecoration(
            prefixIcon: Icon(Icons.search_rounded),
          ),
        ),
        actions: [
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text.trim()),
            child: const Text('查找'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (query == null || query.isEmpty || _content == null) return;
    final matches = _storage.searchLocalBookContent(_content!, query);
    if (!mounted) return;
    if (matches.isEmpty) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('没有找到“$query”')));
      return;
    }
    final target = (matches.first / _content!.length).clamp(0, 1).toDouble();
    _jumpToProgress(target);
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text('找到 ${matches.length} 处，已跳到第一处')));
  }

  void _jumpToProgress(double value) {
    _commitProgress(value);
    if (_preferences.viewMode == ReaderViewMode.scroll) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_scrollController.hasClients) {
          _scrollController.animateTo(
            _scrollController.position.maxScrollExtent * value,
            duration: const Duration(milliseconds: 320),
            curve: Curves.easeOutCubic,
          );
        }
      });
    } else if (_pageController.hasClients) {
      final pages =
          _pageController.positions.first.maxScrollExtent /
          _pageController.positions.first.viewportDimension;
      _pageController.animateToPage(
        (pages * value).round(),
        duration: const Duration(milliseconds: 320),
        curve: Curves.easeOutCubic,
      );
    }
  }

  String _currentExcerpt() {
    final content = _content ?? '';
    if (content.isEmpty) return '';
    final center = (content.length * _progress).round().clamp(
      0,
      content.length,
    );
    final start = (center - 45).clamp(0, content.length);
    final end = (center + 75).clamp(0, content.length);
    return content.substring(start, end).replaceAll(RegExp(r'\s+'), ' ').trim();
  }

  @override
  Widget build(BuildContext context) {
    final backgroundIndex = _preferences.backgroundIndex.clamp(
      0,
      _backgrounds.length - 1,
    );
    final background = _backgrounds[backgroundIndex];
    final dark = backgroundIndex == _backgrounds.length - 1;
    final textColor = dark
        ? Colors.white.withValues(alpha: .88)
        : const Color(0xFF252A31);

    return Scaffold(
      backgroundColor: background,
      body: Stack(
        children: [
          if (_loading)
            const Center(child: CircularProgressIndicator())
          else if (_content == null)
            const Center(child: Text('无法读取文件'))
          else
            Positioned.fill(
              child: GestureDetector(
                behavior: HitTestBehavior.translucent,
                onTap: () => setState(() => _showControls = !_showControls),
                child: LayoutBuilder(
                  builder: (context, constraints) =>
                      _preferences.viewMode == ReaderViewMode.scroll
                      ? _buildScrollReader(textColor)
                      : _buildPagedReader(
                          constraints,
                          textColor,
                          _preferences.viewMode == ReaderViewMode.spread,
                        ),
                ),
              ),
            ),
          Positioned(
            top: 0,
            left: 0,
            right: 0,
            child: LinearProgressIndicator(
              value: _progress,
              minHeight: 2,
              backgroundColor: Colors.transparent,
              color: AppTheme.sky,
            ),
          ),
          if (_showControls) _buildTopBar(dark),
          if (_showControls) _buildBottomBar(dark),
        ],
      ),
    );
  }

  Widget _buildScrollReader(Color textColor) => Scrollbar(
    controller: _scrollController,
    child: SingleChildScrollView(
      controller: _scrollController,
      padding: const EdgeInsets.fromLTRB(24, 96, 24, 110),
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(
            maxWidth: AppTheme.readerContentMaxWidth,
          ),
          child: SelectableText(
            _content!,
            style: TextStyle(
              fontSize: _preferences.fontSize,
              height: _preferences.lineHeight,
              color: textColor,
              letterSpacing: .25,
            ),
          ),
        ),
      ),
    ),
  );

  Widget _buildPagedReader(
    BoxConstraints constraints,
    Color textColor,
    bool requestedSpread,
  ) {
    final spread = requestedSpread && constraints.maxWidth >= 720;
    final pageWidth = spread ? constraints.maxWidth / 2 : constraints.maxWidth;
    final target = ReaderPagination.estimatedCharactersPerPage(
      width: pageWidth,
      height: constraints.maxHeight,
      fontSize: _preferences.fontSize,
      lineHeight: _preferences.lineHeight,
    );
    final pages = ReaderPagination.paginateText(_content!, target);
    final itemCount = spread ? (pages.length / 2).ceil() : pages.length;
    return PageView.builder(
      controller: _pageController,
      itemCount: itemCount,
      onPageChanged: (index) {
        _commitProgress(
          itemCount <= 1 ? 1 : (index / (itemCount - 1)).clamp(0, 1).toDouble(),
        );
      },
      itemBuilder: (context, index) {
        if (!spread) {
          return _page(pages[index], textColor, index + 1, pages.length);
        }
        final left = index * 2;
        return Row(
          children: [
            Expanded(
              child: _page(pages[left], textColor, left + 1, pages.length),
            ),
            Container(
              width: 1,
              margin: const EdgeInsets.symmetric(vertical: 88),
              color: textColor.withValues(alpha: .08),
            ),
            Expanded(
              child: left + 1 < pages.length
                  ? _page(pages[left + 1], textColor, left + 2, pages.length)
                  : const SizedBox.shrink(),
            ),
          ],
        );
      },
    );
  }

  Widget _page(String text, Color textColor, int number, int total) => Padding(
    padding: const EdgeInsets.fromLTRB(28, 92, 28, 72),
    child: Column(
      children: [
        Expanded(
          child: Align(
            alignment: Alignment.topLeft,
            child: SelectableText(
              text,
              style: TextStyle(
                fontSize: _preferences.fontSize,
                height: _preferences.lineHeight,
                color: textColor,
                letterSpacing: .25,
              ),
            ),
          ),
        ),
        Text(
          '$number / $total',
          style: TextStyle(
            color: textColor.withValues(alpha: .42),
            fontSize: 11,
          ),
        ),
      ],
    ),
  );

  Widget _buildTopBar(bool dark) => Positioned(
    top: 0,
    left: 0,
    right: 0,
    child: ColoredBox(
      color: (dark ? const Color(0xFF0B1019) : Colors.white).withValues(
        alpha: .94,
      ),
      child: SafeArea(
        bottom: false,
        child: Row(
          children: [
            IconButton(
              onPressed: () => Navigator.pop(context),
              icon: const Icon(Icons.arrow_back_rounded),
            ),
            Expanded(
              child: Text(
                widget.book.title,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontWeight: FontWeight.w800),
              ),
            ),
            Text(
              '${(_progress * 100).round()}%',
              style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700),
            ),
            IconButton(
              onPressed: _search,
              icon: const Icon(Icons.search_rounded),
            ),
          ],
        ),
      ),
    ),
  );

  Widget _buildBottomBar(bool dark) => Positioned(
    left: 12,
    right: 12,
    bottom: 10,
    child: SafeArea(
      top: false,
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 660),
          child: Material(
            color: const Color(0xFF081225).withValues(alpha: .94),
            borderRadius: BorderRadius.circular(22),
            child: SizedBox(
              height: 62,
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                children: [
                  IconButton(
                    onPressed: _addBookmark,
                    color: Colors.white,
                    tooltip: '添加书签',
                    icon: const Icon(Icons.bookmark_add_outlined),
                  ),
                  IconButton(
                    onPressed: _showAnnotations,
                    color: Colors.white,
                    tooltip: '书签与笔记',
                    icon: const Icon(Icons.collections_bookmark_outlined),
                  ),
                  FilledButton.tonalIcon(
                    onPressed: _addNote,
                    icon: const Icon(Icons.edit_note_rounded),
                    label: const Text('笔记'),
                  ),
                  IconButton(
                    onPressed: _showSettings,
                    color: Colors.white,
                    tooltip: '阅读排版',
                    icon: const Icon(Icons.tune_rounded),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    ),
  );

  @override
  void dispose() {
    _storage.recordReadingSession(
      widget.book.id,
      DateTime.now().difference(_sessionStartedAt),
    );
    _scrollController
      ..removeListener(_updateScrollProgress)
      ..dispose();
    _pageController.dispose();
    super.dispose();
  }
}

class _SettingSlider extends StatelessWidget {
  final String label;
  final double value;
  final double min;
  final double max;
  final int divisions;
  final String valueLabel;
  final ValueChanged<double> onChanged;

  const _SettingSlider({
    required this.label,
    required this.value,
    required this.min,
    required this.max,
    required this.divisions,
    required this.valueLabel,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) => Row(
    children: [
      SizedBox(width: 42, child: Text(label)),
      Expanded(
        child: Slider(
          value: value,
          min: min,
          max: max,
          divisions: divisions,
          label: valueLabel,
          onChanged: onChanged,
        ),
      ),
      SizedBox(width: 34, child: Text(valueLabel, textAlign: TextAlign.end)),
    ],
  );
}
