import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../adapters/ocr/local_ocr_adapter.dart';
import '../../core/models.dart';
import '../../theme/app_theme.dart';
import 'local_content_service.dart';

typedef LocalContentPicker =
    Future<LocalPickedFile?> Function(List<String> extensions);

Future<LocalPickedFile?> _defaultPicker(List<String> extensions) =>
    pickLocalContentFile(extensions: extensions);

class LocalContentHubScreen extends StatefulWidget {
  const LocalContentHubScreen({
    super.key,
    this.service,
    this.picker = _defaultPicker,
  });

  final LocalContentService? service;
  final LocalContentPicker picker;

  @override
  State<LocalContentHubScreen> createState() => _LocalContentHubScreenState();
}

class _LocalContentHubScreenState extends State<LocalContentHubScreen> {
  late final LocalContentService _service;
  LocalContentBook? _book;
  LocalDictionary? _dictionary;
  OcrJob? _ocrJob;
  String? _ocrText;
  String? _message;
  bool _messageIsError = false;
  bool _busy = false;
  int _pageIndex = 0;
  String _selectedText = '';

  @override
  void initState() {
    super.initState();
    _service = widget.service ?? LocalContentService.forCurrentPlatform();
  }

  Future<void> _importBook() async {
    if (_busy) return;
    _setBusy('正在选择本地书…');
    try {
      final file = await widget.picker(LocalContentService.bookExtensions);
      if (file == null) {
        _clearBusy();
        return;
      }
      _setMessage('正在安全解析 ${file.name}…');
      final book = await _service.importBook(file);
      if (!mounted) return;
      setState(() {
        _book = book;
        _pageIndex = 0;
        _selectedText = '';
        _busy = false;
        _messageIsError = false;
        _message = '已在本机打开《${book.title}》，共 ${book.pageCount} 页';
      });
    } on Object catch (error) {
      _showError(_service.describeError(error));
    }
  }

  Future<void> _attachDictionary() async {
    if (_busy) return;
    _setBusy('正在选择 MDX 词典…');
    try {
      final file = await widget.picker(
        LocalContentService.dictionaryExtensions,
      );
      if (file == null) {
        _clearBusy();
        return;
      }
      _setMessage('正在本机加载 ${file.name}…');
      final dictionary = await _service.attachDictionary(file);
      if (!mounted) return;
      setState(() {
        _dictionary = dictionary;
        _busy = false;
        _messageIsError = false;
        _message = '已挂载 ${dictionary.name}（${dictionary.entryCount} 条）';
      });
    } on Object catch (error) {
      _showError(_service.describeError(error));
    }
  }

  Future<void> _lookupSelection() async {
    final term = _selectedText.trim();
    if (term.isEmpty) {
      _showError('请先在正文中选择要查询的文字');
      return;
    }
    if (_dictionary == null) {
      await _attachDictionary();
      if (_dictionary == null || !mounted) return;
    }
    _setBusy('正在查询“$term”…');
    try {
      final results = await _service.lookup(_dictionary!, term);
      if (!mounted) return;
      setState(() => _busy = false);
      await _showDefinitions(term, results);
    } on Object catch (error) {
      _showError(_service.describeError(error));
    }
  }

  Future<void> _runOcr() async {
    if (_busy) return;
    if (!_service.isOcrAvailable) {
      _showError('此平台暂不支持本地 OCR，图片不会上传到远程服务');
      return;
    }
    _setBusy('正在选择 OCR 图片…');
    try {
      final file = await widget.picker(LocalContentService.imageExtensions);
      if (file == null) {
        _clearBusy();
        return;
      }
      final bytes = await _service.readOcrImage(file);
      final job = _service.startOcr(bytes);
      if (!mounted) return;
      setState(() {
        _ocrJob = job;
        _message = '正在本机识别；可随时取消';
      });
      final result = await job.result;
      if (!mounted || !identical(_ocrJob, job)) return;
      setState(() {
        _ocrJob = null;
        _ocrText = result.text;
        _busy = false;
        _messageIsError = false;
        _message = '本地 OCR 完成（置信度 ${(result.confidence * 100).round()}%）';
      });
    } on Object catch (error) {
      if (!mounted) return;
      setState(() => _ocrJob = null);
      _showError(_service.describeError(error));
    }
  }

  void _cancelOcr() {
    final job = _ocrJob;
    if (job == null) return;
    job.cancel();
    setState(() {
      _message = '正在取消本地 OCR…';
      _messageIsError = false;
    });
  }

  void _setBusy(String message) {
    if (!mounted) return;
    setState(() {
      _busy = true;
      _message = message;
      _messageIsError = false;
    });
  }

  void _setMessage(String message) {
    if (!mounted) return;
    setState(() {
      _message = message;
      _messageIsError = false;
    });
  }

  void _clearBusy() {
    if (!mounted) return;
    setState(() {
      _busy = false;
      _message = null;
      _messageIsError = false;
    });
  }

  void _showError(String message) {
    if (!mounted) return;
    setState(() {
      _busy = false;
      _message = message;
      _messageIsError = true;
    });
  }

  void _previousPage() {
    if (_pageIndex <= 0) return;
    setState(() {
      _pageIndex--;
      _selectedText = '';
    });
  }

  void _nextPage() {
    final book = _book;
    if (book == null || _pageIndex >= book.pageCount - 1) return;
    setState(() {
      _pageIndex++;
      _selectedText = '';
    });
  }

  Future<void> _showDefinitions(
    String term,
    List<DictionaryEntry> entries,
  ) async {
    await showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (context) => SafeArea(
        child: ConstrainedBox(
          constraints: BoxConstraints(
            maxHeight: MediaQuery.sizeOf(context).height * .72,
          ),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 24),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  '“$term”的本地释义',
                  style: Theme.of(context).textTheme.titleLarge,
                ),
                const SizedBox(height: 8),
                Text(
                  _dictionary?.name ?? 'MDX',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                const SizedBox(height: 16),
                Flexible(
                  child: entries.isEmpty
                      ? const Center(child: Text('词典中没有找到该词'))
                      : ListView.separated(
                          shrinkWrap: true,
                          itemCount: entries.length,
                          separatorBuilder: (_, __) =>
                              const Divider(height: 24),
                          itemBuilder: (context, index) => SelectableText(
                            _plainText(entries[index].definition),
                            style: Theme.of(context).textTheme.bodyLarge,
                          ),
                        ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final book = _book;
    return Scaffold(
      appBar: AppBar(
        title: const Text('本地阅读'),
        actions: [
          IconButton(
            onPressed: _busy ? null : _attachDictionary,
            tooltip: _dictionary == null ? '挂载 MDX 词典' : '更换 MDX 词典',
            icon: Icon(
              _dictionary == null
                  ? Icons.menu_book_outlined
                  : Icons.menu_book_rounded,
            ),
          ),
          IconButton(
            onPressed: _busy ? null : _runOcr,
            tooltip: _service.isOcrAvailable ? '本地 OCR' : '本地 OCR 不可用',
            icon: const Icon(Icons.document_scanner_outlined),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: SafeArea(
        top: false,
        child: CallbackShortcuts(
          bindings: <ShortcutActivator, VoidCallback>{
            const SingleActivator(LogicalKeyboardKey.arrowLeft): _previousPage,
            const SingleActivator(LogicalKeyboardKey.arrowRight): _nextPage,
          },
          child: Focus(
            autofocus: true,
            child: Column(
              children: [
                if (_message != null)
                  _StatusBanner(
                    message: _message!,
                    isError: _messageIsError,
                    loading: _busy,
                    onCancel: _ocrJob == null ? null : _cancelOcr,
                  ),
                Expanded(
                  child: book == null
                      ? _buildLanding(context)
                      : _buildReader(context, book),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildLanding(BuildContext context) {
    final theme = Theme.of(context);
    return LayoutBuilder(
      builder: (context, constraints) {
        final wide = constraints.maxWidth >= 720;
        return ListView(
          padding: EdgeInsets.fromLTRB(wide ? 28 : 16, 20, wide ? 28 : 16, 32),
          children: [
            Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 920),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    DecoratedBox(
                      decoration: BoxDecoration(
                        gradient: AppTheme.heroGradient,
                        borderRadius: BorderRadius.circular(
                          AppTheme.cardRadius,
                        ),
                      ),
                      child: Padding(
                        padding: EdgeInsets.all(wide ? 30 : 22),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Icon(
                              Icons.offline_bolt_rounded,
                              color: Colors.white,
                              size: 30,
                            ),
                            const SizedBox(height: 18),
                            Text(
                              '文件留在设备上，阅读不必联网',
                              style: theme.textTheme.headlineSmall?.copyWith(
                                color: Colors.white,
                                fontWeight: FontWeight.w800,
                              ),
                            ),
                            const SizedBox(height: 10),
                            Text(
                              '打开无 DRM 的 Kindle 与漫画文件，挂载本地 MDX 词典，并在支持的平台离线识图。',
                              style: theme.textTheme.bodyLarge?.copyWith(
                                color: Colors.white.withValues(alpha: .82),
                              ),
                            ),
                            const SizedBox(height: 22),
                            FilledButton.icon(
                              onPressed: _busy ? null : _importBook,
                              icon: const Icon(Icons.file_open_rounded),
                              label: const Text('导入本地书'),
                            ),
                          ],
                        ),
                      ),
                    ),
                    const SizedBox(height: 20),
                    Wrap(
                      spacing: 14,
                      runSpacing: 14,
                      children: [
                        _CapabilityCard(
                          width: wide ? 284 : constraints.maxWidth,
                          icon: Icons.auto_stories_outlined,
                          title: 'Kindle 与漫画',
                          description: 'MOBI / AZW / AZW3\nCBR / CBT / CB7',
                          actionLabel: '选择文件',
                          onPressed: _busy ? null : _importBook,
                        ),
                        _CapabilityCard(
                          width: wide ? 284 : constraints.maxWidth,
                          icon: Icons.translate_rounded,
                          title: '本地 MDX 查词',
                          description: _dictionary == null
                              ? '选择正文后查词，全程不联网'
                              : '已挂载 ${_dictionary!.name}',
                          actionLabel: _dictionary == null ? '挂载词典' : '更换词典',
                          onPressed: _busy ? null : _attachDictionary,
                        ),
                        _CapabilityCard(
                          width: wide ? 284 : constraints.maxWidth,
                          icon: Icons.document_scanner_outlined,
                          title: '可取消本地 OCR',
                          description: _service.isOcrAvailable
                              ? '支持 ${_service.ocrLanguages.join('、')}；图片不上传'
                              : '此平台不可用；没有远程回退',
                          actionLabel: _service.isOcrAvailable
                              ? '识别图片'
                              : '当前不可用',
                          onPressed: _busy || !_service.isOcrAvailable
                              ? null
                              : _runOcr,
                        ),
                      ],
                    ),
                    if (_ocrText != null) ...[
                      const SizedBox(height: 20),
                      _OcrResultCard(text: _ocrText!),
                    ],
                  ],
                ),
              ),
            ),
          ],
        );
      },
    );
  }

  Widget _buildReader(BuildContext context, LocalContentBook book) {
    final theme = Theme.of(context);
    final textPage = book.kind == LocalContentKind.text;
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(
                maxWidth: AppTheme.readerContentMaxWidth,
              ),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          book.title,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.titleLarge,
                        ),
                        const SizedBox(height: 4),
                        Text(
                          '${book.format} · 本地文件 · ${book.pageCount} 页',
                          style: theme.textTheme.bodySmall,
                        ),
                      ],
                    ),
                  ),
                  if (textPage)
                    FilledButton.tonalIcon(
                      onPressed: _busy || _selectedText.trim().isEmpty
                          ? null
                          : _lookupSelection,
                      icon: const Icon(Icons.search_rounded),
                      label: const Text('查词'),
                    ),
                  const SizedBox(width: 8),
                  IconButton(
                    onPressed: _busy ? null : _importBook,
                    tooltip: '打开其他本地书',
                    icon: const Icon(Icons.file_open_outlined),
                  ),
                ],
              ),
            ),
          ),
        ),
        const Divider(height: 1),
        Expanded(
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(
                maxWidth: AppTheme.readerContentMaxWidth,
              ),
              child: textPage
                  ? _buildTextPage(context, book)
                  : _buildComicPage(context, book),
            ),
          ),
        ),
        const Divider(height: 1),
        _PageControls(
          page: _pageIndex,
          count: book.pageCount,
          onPrevious: _pageIndex == 0 ? null : _previousPage,
          onNext: _pageIndex >= book.pageCount - 1 ? null : _nextPage,
        ),
      ],
    );
  }

  Widget _buildTextPage(BuildContext context, LocalContentBook book) {
    final section = book.sections[_pageIndex];
    return SingleChildScrollView(
      key: ValueKey<int>(_pageIndex),
      padding: const EdgeInsets.fromLTRB(24, 28, 24, 40),
      child: Semantics(
        label: '正文第 ${_pageIndex + 1} 页，共 ${book.pageCount} 页',
        child: SelectableText(
          section,
          key: const Key('local-reader-text'),
          style: Theme.of(
            context,
          ).textTheme.bodyLarge?.copyWith(fontSize: 18, height: 1.75),
          onSelectionChanged: (selection, _) {
            final selected = selection.isValid
                ? selection.textInside(section)
                : '';
            if (selected == _selectedText || !mounted) return;
            setState(() => _selectedText = selected);
          },
        ),
      ),
    );
  }

  Widget _buildComicPage(BuildContext context, LocalContentBook book) {
    final page = book.pages[_pageIndex];
    final colors = Theme.of(context).colorScheme;
    return Semantics(
      image: true,
      label: '漫画第 ${_pageIndex + 1} 页，共 ${book.pageCount} 页，${page.name}',
      child: ColoredBox(
        color: colors.surfaceContainerHighest,
        child: Center(
          child: Image.memory(
            page.bytes,
            key: ValueKey<String>(page.name),
            fit: BoxFit.contain,
            gaplessPlayback: true,
            semanticLabel: '漫画页 ${_pageIndex + 1}',
            errorBuilder: (context, _, __) =>
                _ComicFallback(name: page.name, page: _pageIndex + 1),
          ),
        ),
      ),
    );
  }
}

class _StatusBanner extends StatelessWidget {
  const _StatusBanner({
    required this.message,
    required this.isError,
    required this.loading,
    this.onCancel,
  });

  final String message;
  final bool isError;
  final bool loading;
  final VoidCallback? onCancel;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Semantics(
      liveRegion: true,
      container: true,
      label: message,
      child: ColoredBox(
        color: isError ? colors.errorContainer : colors.secondaryContainer,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
          child: Row(
            children: [
              if (loading) ...[
                SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    semanticsLabel: message,
                  ),
                ),
                const SizedBox(width: 10),
              ] else ...[
                Icon(
                  isError
                      ? Icons.error_outline_rounded
                      : Icons.info_outline_rounded,
                  size: 20,
                ),
                const SizedBox(width: 10),
              ],
              Expanded(child: Text(message)),
              if (onCancel != null)
                TextButton(onPressed: onCancel, child: const Text('取消识别')),
            ],
          ),
        ),
      ),
    );
  }
}

class _CapabilityCard extends StatelessWidget {
  const _CapabilityCard({
    required this.width,
    required this.icon,
    required this.title,
    required this.description,
    required this.actionLabel,
    required this.onPressed,
  });

  final double width;
  final IconData icon;
  final String title;
  final String description;
  final String actionLabel;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return SizedBox(
      width: width,
      child: Card(
        child: Padding(
          padding: const EdgeInsets.all(18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(icon, size: 28, color: theme.colorScheme.primary),
              const SizedBox(height: 14),
              Text(title, style: theme.textTheme.titleMedium),
              const SizedBox(height: 7),
              Text(description, style: theme.textTheme.bodySmall),
              const SizedBox(height: 16),
              OutlinedButton(onPressed: onPressed, child: Text(actionLabel)),
            ],
          ),
        ),
      ),
    );
  }
}

class _OcrResultCard extends StatelessWidget {
  const _OcrResultCard({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('本地 OCR 结果', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 10),
            SelectableText(text),
          ],
        ),
      ),
    );
  }
}

class _PageControls extends StatelessWidget {
  const _PageControls({
    required this.page,
    required this.count,
    required this.onPrevious,
    required this.onNext,
  });

  final int page;
  final int count;
  final VoidCallback? onPrevious;
  final VoidCallback? onNext;

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            IconButton(
              onPressed: onPrevious,
              tooltip: '上一页',
              constraints: const BoxConstraints.tightFor(width: 48, height: 48),
              icon: const Icon(Icons.arrow_back_rounded),
            ),
            Semantics(
              liveRegion: true,
              label: '第 ${page + 1} 页，共 $count 页',
              child: SizedBox(
                width: 112,
                child: Text(
                  '${page + 1} / $count',
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.labelLarge,
                ),
              ),
            ),
            IconButton(
              onPressed: onNext,
              tooltip: '下一页',
              constraints: const BoxConstraints.tightFor(width: 48, height: 48),
              icon: const Icon(Icons.arrow_forward_rounded),
            ),
          ],
        ),
      ),
    );
  }
}

class _ComicFallback extends StatelessWidget {
  const _ComicFallback({required this.name, required this.page});

  final String name;
  final int page;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(24),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.broken_image_outlined, size: 52),
          const SizedBox(height: 12),
          Text('第 $page 页无法显示', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 6),
          Text(name, textAlign: TextAlign.center),
        ],
      ),
    );
  }
}

String _plainText(String value) => value
    .replaceAll(RegExp(r'<[^>]*>'), ' ')
    .replaceAll('&nbsp;', ' ')
    .replaceAll('&amp;', '&')
    .replaceAll('&lt;', '<')
    .replaceAll('&gt;', '>')
    .replaceAll(RegExp(r'\s+'), ' ')
    .trim();
