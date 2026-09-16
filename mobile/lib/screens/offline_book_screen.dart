import 'dart:async';
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:flutter/material.dart';
import 'package:path/path.dart' as path;
import 'package:pdfrx/pdfrx.dart';

import '../models/reader_preferences.dart';
import '../services/local_storage_service.dart';
import 'local_reader_screen.dart';

Widget buildOfflineBookScreen(LocalBookInfo book) => switch (book.format) {
  'pdf' => _OfflinePdfReader(book: book),
  'cbz' => _OfflineComicReader(book: book),
  _ => LocalReaderScreen(book: book),
};

class _OfflinePdfReader extends StatefulWidget {
  final LocalBookInfo book;

  const _OfflinePdfReader({required this.book});

  @override
  State<_OfflinePdfReader> createState() => _OfflinePdfReaderState();
}

class _OfflinePdfReaderState extends State<_OfflinePdfReader> {
  final _storage = LocalStorageService();
  final _controller = PdfViewerController();
  final _startedAt = DateTime.now();
  String? _filePath;
  Object? _error;
  int _page = 1;
  int _pageCount = 0;
  bool _storageReady = false;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    try {
      await _storage.init();
      _storageReady = true;
      final file = await _storage.getLocalBookFile(widget.book);
      if (!await file.exists()) throw const FormatException('PDF 本地文件已丢失');
      if (!mounted) return;
      setState(() => _filePath = file.path);
    } catch (error) {
      if (mounted) setState(() => _error = error);
    }
  }

  void _saveProgress(int? page) {
    if (page == null || _pageCount <= 0) return;
    _page = page;
    _storage.updateLocalBookProgress(widget.book.id, page / _pageCount);
    if (mounted) setState(() {});
  }

  void _bookmark() {
    _storage.addAnnotation(
      bookId: widget.book.id,
      type: 'bookmark',
      excerpt: 'PDF 第 $_page 页',
      progress: _pageCount <= 0 ? 0 : _page / _pageCount,
    );
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(const SnackBar(content: Text('当前 PDF 页已加入书签')));
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: Text(widget.book.title, overflow: TextOverflow.ellipsis),
      actions: [
        if (_pageCount > 0) Center(child: Text('$_page / $_pageCount')),
        IconButton(
          tooltip: '添加书签',
          onPressed: _filePath == null ? null : _bookmark,
          icon: const Icon(Icons.bookmark_add_outlined),
        ),
      ],
    ),
    body: _error != null
        ? Center(child: Text('PDF 打开失败：$_error'))
        : _filePath == null
        ? const Center(child: CircularProgressIndicator())
        : PdfViewer.file(
            _filePath!,
            controller: _controller,
            params: PdfViewerParams(
              backgroundColor: Theme.of(context).colorScheme.surfaceContainer,
              onViewerReady: (document, controller) async {
                _pageCount = document.pages.length;
                if (_pageCount <= 0) {
                  if (mounted) {
                    setState(() => _error = const FormatException('PDF 没有页面'));
                  }
                  return;
                }
                final target = (widget.book.progress * _pageCount)
                    .round()
                    .clamp(1, _pageCount);
                if (target > 1) {
                  await controller.goToPage(
                    pageNumber: target,
                    duration: Duration.zero,
                  );
                }
                _saveProgress(target);
              },
              onPageChanged: _saveProgress,
            ),
          ),
  );

  @override
  void dispose() {
    if (_storageReady) {
      _storage.recordReadingSession(
        widget.book.id,
        DateTime.now().difference(_startedAt),
      );
    }
    super.dispose();
  }
}

class _OfflineComicReader extends StatefulWidget {
  final LocalBookInfo book;

  const _OfflineComicReader({required this.book});

  @override
  State<_OfflineComicReader> createState() => _OfflineComicReaderState();
}

class _OfflineComicReaderState extends State<_OfflineComicReader> {
  final _storage = LocalStorageService();
  final _startedAt = DateTime.now();
  final _pageController = PageController();
  final _scrollController = ScrollController();
  List<Uint8List>? _pages;
  ReaderPreferences _preferences = const ReaderPreferences();
  Object? _error;
  int _page = 0;
  bool _storageReady = false;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    try {
      await _storage.init();
      _storageReady = true;
      final file = await _storage.getLocalBookFile(widget.book);
      final archive = ZipDecoder().decodeBytes(
        await file.readAsBytes(),
        verify: true,
      );
      final entries = archive.files.where(
        (entry) {
          if (!entry.isFile) return false;
          return const {
            '.jpg',
            '.jpeg',
            '.png',
            '.webp',
            '.gif',
          }.contains(path.extension(entry.name).toLowerCase());
        },
      ).toList()..sort((left, right) => _naturalCompare(left.name, right.name));
      final pages = entries
          .map((entry) {
            final content = entry.content as List<int>;
            return content is Uint8List ? content : Uint8List.fromList(content);
          })
          .toList(growable: false);
      if (pages.isEmpty) throw const FormatException('CBZ 中没有图片页');
      final page = (widget.book.progress * (pages.length - 1)).round().clamp(
        0,
        pages.length - 1,
      );
      if (!mounted) return;
      setState(() {
        _pages = pages;
        _page = page;
        _preferences = _storage.getReaderPreferences();
      });
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_preferences.viewMode == ReaderViewMode.scroll &&
            _scrollController.hasClients &&
            widget.book.progress > 0) {
          _scrollController.jumpTo(
            _scrollController.position.maxScrollExtent * widget.book.progress,
          );
        }
        if (_pageController.hasClients) {
          final target = _preferences.viewMode == ReaderViewMode.spread
              ? _page ~/ 2
              : _page;
          _pageController.jumpToPage(target);
        }
      });
    } catch (error) {
      if (mounted) setState(() => _error = error);
    }
  }

  int _naturalCompare(String left, String right) {
    final pattern = RegExp(r'(\d+)|(\D+)');
    final a = pattern
        .allMatches(left.toLowerCase())
        .map((match) => match.group(0)!)
        .toList();
    final b = pattern
        .allMatches(right.toLowerCase())
        .map((match) => match.group(0)!)
        .toList();
    for (var index = 0; index < a.length && index < b.length; index++) {
      final aNumber = int.tryParse(a[index]);
      final bNumber = int.tryParse(b[index]);
      final comparison = aNumber != null && bNumber != null
          ? aNumber.compareTo(bNumber)
          : a[index].compareTo(b[index]);
      if (comparison != 0) return comparison;
    }
    return a.length.compareTo(b.length);
  }

  void _commitPage(int page) {
    final pages = _pages;
    if (pages == null || pages.isEmpty) return;
    final next = page.clamp(0, pages.length - 1);
    if (next == _page) return;
    _page = next;
    _storage.updateLocalBookProgress(
      widget.book.id,
      (_page + 1) / pages.length,
    );
    if (mounted) setState(() {});
  }

  Future<void> _setMode(ReaderViewMode mode) async {
    final next = _preferences.copyWith(viewMode: mode);
    setState(() => _preferences = next);
    await _storage.saveReaderPreferences(next);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mode == ReaderViewMode.scroll && _scrollController.hasClients) {
        final pages = _pages;
        final progress = pages == null || pages.isEmpty
            ? 0.0
            : (_page + 1) / pages.length;
        _scrollController.jumpTo(
          _scrollController.position.maxScrollExtent * progress,
        );
        return;
      }
      if (!_pageController.hasClients) return;
      _pageController.jumpToPage(
        mode == ReaderViewMode.spread ? _page ~/ 2 : _page,
      );
    });
  }

  void _bookmark() {
    final pages = _pages;
    if (pages == null || pages.isEmpty) return;
    _storage.addAnnotation(
      bookId: widget.book.id,
      type: 'bookmark',
      excerpt: '漫画第 ${_page + 1} 页',
      progress: (_page + 1) / pages.length,
    );
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(const SnackBar(content: Text('当前漫画页已加入书签')));
  }

  Widget _image(Uint8List bytes) => Container(
    color: Colors.black,
    alignment: Alignment.center,
    child: InteractiveViewer(
      minScale: 1,
      maxScale: 5,
      child: Image.memory(bytes, fit: BoxFit.contain, gaplessPlayback: true),
    ),
  );

  Widget _reader(List<Uint8List> pages, double width) {
    var mode = _preferences.viewMode;
    if (mode == ReaderViewMode.spread && width < 700) {
      mode = ReaderViewMode.page;
    }
    if (mode == ReaderViewMode.scroll) {
      return NotificationListener<ScrollNotification>(
        onNotification: (notification) {
          final metrics = notification.metrics;
          if (metrics.maxScrollExtent > 0) {
            _commitPage(
              (metrics.pixels / metrics.maxScrollExtent * (pages.length - 1))
                  .round(),
            );
          }
          return false;
        },
        child: ListView.builder(
          controller: _scrollController,
          itemCount: pages.length,
          itemBuilder: (context, index) => Image.memory(
            pages[index],
            fit: BoxFit.fitWidth,
            gaplessPlayback: true,
          ),
        ),
      );
    }
    final spread = mode == ReaderViewMode.spread;
    return PageView.builder(
      controller: _pageController,
      itemCount: spread ? (pages.length / 2).ceil() : pages.length,
      onPageChanged: (index) => _commitPage(spread ? index * 2 : index),
      itemBuilder: (context, index) {
        if (!spread) return _image(pages[index]);
        final first = index * 2;
        return Row(
          children: [
            Expanded(child: _image(pages[first])),
            if (first + 1 < pages.length)
              Expanded(child: _image(pages[first + 1])),
          ],
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final pages = _pages;
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        title: Text(widget.book.title, overflow: TextOverflow.ellipsis),
        actions: [
          if (pages != null)
            Center(child: Text('${_page + 1} / ${pages.length}')),
          IconButton(
            tooltip: '添加书签',
            onPressed: pages == null ? null : _bookmark,
            icon: const Icon(Icons.bookmark_add_outlined),
          ),
          PopupMenuButton<ReaderViewMode>(
            tooltip: '漫画翻页模式',
            onSelected: _setMode,
            itemBuilder: (_) => const [
              PopupMenuItem(value: ReaderViewMode.scroll, child: Text('连续滚动')),
              PopupMenuItem(value: ReaderViewMode.page, child: Text('单页翻页')),
              PopupMenuItem(
                value: ReaderViewMode.spread,
                child: Text('iPad 双页'),
              ),
            ],
          ),
        ],
      ),
      body: _error != null
          ? Center(
              child: Text(
                'CBZ 打开失败：$_error',
                style: const TextStyle(color: Colors.white),
              ),
            )
          : pages == null
          ? const Center(child: CircularProgressIndicator())
          : LayoutBuilder(
              builder: (context, constraints) =>
                  _reader(pages, constraints.maxWidth),
            ),
    );
  }

  @override
  void dispose() {
    if (_storageReady) {
      _storage.recordReadingSession(
        widget.book.id,
        DateTime.now().difference(_startedAt),
      );
    }
    _pageController.dispose();
    _scrollController.dispose();
    super.dispose();
  }
}
