import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:scrollable_positioned_list/scrollable_positioned_list.dart';
import '../models/book.dart';
import '../services/api_service.dart';
import '../services/local_storage_service.dart';
import '../services/reading_progress.dart' show ReadingProgressService;
import '../services/tts_service.dart';
import '../main.dart' show ttsHandler;
import '../theme/app_theme.dart';

class ReaderScreen extends StatefulWidget {
  final String bookId;
  final String chapterId;
  final List<Chapter> chapters;
  final Book? book;

  const ReaderScreen({
    super.key,
    required this.bookId,
    required this.chapterId,
    required this.chapters,
    this.book,
  });

  @override
  State<ReaderScreen> createState() => _ReaderScreenState();
}

class _ReaderScreenState extends State<ReaderScreen> {
  final _api = ApiService();
  final _progress = ReadingProgressService();
  final _storage = LocalStorageService();
  late TtsService _tts;
  final ItemScrollController _scrollCtrl = ItemScrollController();
  final ItemPositionsListener _positionsListener = ItemPositionsListener.create();

  static final _illustPattern = RegExp(r'^\[illustration:(.+)\]$');

  Chapter? _chapter;
  List<_ReaderItem> _items = [];
  List<String> _ttsParagraphs = [];
  bool _loading = true;
  bool _showControls = true;
  int _ttsHighlight = -1;

  late String _currentChapterId;
  int _currentChapterIdx = 0;

  double _fontSize = 18;
  double _lineHeight = 1.8;
  bool _darkMode = false;
  bool _ttsPlaying = false;
  double _readProgress = 0.0;

  final List<Color> _bgColors = [
    const Color(0xFFF5F1E8),
    Colors.white,
    const Color(0xFFE8F5E9),
    const Color(0xFF263238),
  ];
  int _bgIndex = 0;

  @override
  void initState() {
    super.initState();
    _tts = TtsService(_api, ttsHandler);
    _tts.mode = 'smart';
    _currentChapterId = widget.chapterId;
    _currentChapterIdx = widget.chapters.indexWhere((c) => c.id == _currentChapterId);
    if (_currentChapterIdx < 0) _currentChapterIdx = 0;
    _loadChapter();
    _loadSettings();
    _positionsListener.itemPositions.addListener(_updateReadProgress);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final isDark = Theme.of(context).brightness == Brightness.dark;
      if (isDark && _bgIndex == 0) {
        setState(() { _bgIndex = 3; _darkMode = true; });
      }
    });
  }

  void _updateReadProgress() {
    final positions = _positionsListener.itemPositions.value;
    if (positions.isEmpty || _items.isEmpty) return;
    final maxIndex = positions.map((p) => p.index).reduce((a, b) => a > b ? a : b);
    final totalItems = _items.length + 2;
    final progress = (maxIndex / (totalItems - 1)).clamp(0.0, 1.0);
    if ((_readProgress - progress).abs() > 0.005) {
      setState(() => _readProgress = progress);
      _progress.save(widget.bookId, _currentChapterId, progress);
    }
  }

  Future<void> _loadSettings() async {
    await _progress.init();
    await _storage.init();
  }

  Future<void> _loadChapter() async {
    setState(() => _loading = true);
    try {
      String content;
      final cached = await _storage.getDownloadedContent(widget.bookId, _currentChapterId);
      Chapter ch;
      if (cached != null) {
        final idx = widget.chapters.indexWhere((c) => c.id == _currentChapterId);
        ch = idx >= 0 ? widget.chapters[idx] : await _api.getChapter(widget.bookId, _currentChapterId);
        content = cached;
      } else {
        ch = await _api.getChapter(widget.bookId, _currentChapterId);
        content = ch.content ?? '';
      }
      final lines = content.split(RegExp(r'\n+')).where((p) => p.trim().isNotEmpty).toList();
      final items = <_ReaderItem>[];
      final ttsParas = <String>[];
      for (final line in lines) {
        final m = _illustPattern.firstMatch(line);
        if (m != null) {
          items.add(_ReaderItem.illustration(m.group(1)!));
        } else {
          items.add(_ReaderItem.text(line, ttsParas.length));
          ttsParas.add(line);
        }
      }
      if (mounted) {
        setState(() {
          _chapter = ch;
          _items = items;
          _ttsParagraphs = ttsParas;
          _loading = false;
          _ttsHighlight = -1;
          _readProgress = 0.0;
        });
        _progress.save(widget.bookId, _currentChapterId, 0.0);
        if (widget.book != null) {
          _storage.recordRead(widget.book!, _currentChapterId, ch.displayTitle);
        }
      }
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  void _toggleControls() {
    setState(() => _showControls = !_showControls);
    SystemChrome.setEnabledSystemUIMode(
      _showControls ? SystemUiMode.edgeToEdge : SystemUiMode.immersiveSticky,
    );
  }

  void _nextChapter() {
    if (_currentChapterIdx >= widget.chapters.length - 1) return;
    _tts.stop();
    _currentChapterIdx++;
    _currentChapterId = widget.chapters[_currentChapterIdx].id;
    _loadChapter();
  }

  void _prevChapter() {
    if (_currentChapterIdx <= 0) return;
    _tts.stop();
    _currentChapterIdx--;
    _currentChapterId = widget.chapters[_currentChapterIdx].id;
    _loadChapter();
  }

  void _toggleTts() {
    if (_ttsPlaying) {
      _tts.stop();
      setState(() { _ttsPlaying = false; _ttsHighlight = -1; });
    } else {
      _tts.bookTitle = widget.book?.title;
      _tts.chapterTitle = _chapter?.displayTitle;
      _tts.authorName = widget.book?.author;
      _tts.onSkipPrev = _prevChapter;
      _tts.onSkipNext = _nextChapter;
      _tts.buildPlan(_ttsParagraphs);
      _tts.onParagraphChange = (idx) {
        if (mounted) {
          setState(() => _ttsHighlight = idx);
          final scrollIdx = _items.indexWhere((item) => item.ttsIndex == idx);
          if (scrollIdx >= 0) {
            _scrollCtrl.scrollTo(index: scrollIdx + 1, duration: const Duration(milliseconds: 300));
          }
        }
      };
      _tts.onComplete = () {
        if (mounted) setState(() { _ttsPlaying = false; _ttsHighlight = -1; });
      };
      _tts.play();
      setState(() => _ttsPlaying = true);
    }
  }

  void _showSettingsSheet() {
    showModalBottomSheet(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheetState) => Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Row(
                children: [
                  const Text('字号'),
                  Expanded(
                    child: Slider(
                      value: _fontSize,
                      min: 14,
                      max: 28,
                      divisions: 7,
                      label: _fontSize.round().toString(),
                      onChanged: (v) {
                        setSheetState(() => _fontSize = v);
                        setState(() {});
                      },
                    ),
                  ),
                  Text('${_fontSize.round()}'),
                ],
              ),
              Row(
                children: [
                  const Text('行距'),
                  Expanded(
                    child: Slider(
                      value: _lineHeight,
                      min: 1.2,
                      max: 2.5,
                      divisions: 13,
                      label: _lineHeight.toStringAsFixed(1),
                      onChanged: (v) {
                        setSheetState(() => _lineHeight = v);
                        setState(() {});
                      },
                    ),
                  ),
                  Text(_lineHeight.toStringAsFixed(1)),
                ],
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  const Text('背景'),
                  const SizedBox(width: 16),
                  ...List.generate(_bgColors.length, (i) => Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: GestureDetector(
                      onTap: () {
                        setSheetState(() => _bgIndex = i);
                        setState(() => _darkMode = i == _bgColors.length - 1);
                      },
                      child: Container(
                        width: 36, height: 36,
                        decoration: BoxDecoration(
                          color: _bgColors[i],
                          shape: BoxShape.circle,
                          border: Border.all(
                            color: _bgIndex == i ? Theme.of(context).colorScheme.primary : Colors.grey.shade300,
                            width: _bgIndex == i ? 2.5 : 1,
                          ),
                        ),
                      ),
                    ),
                  )),
                ],
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  const Text('语速'),
                  Expanded(
                    child: Slider(
                      value: _tts.baseRate,
                      min: 0.5,
                      max: 2.0,
                      divisions: 15,
                      label: '${_tts.baseRate.toStringAsFixed(1)}x',
                      onChanged: (v) {
                        setSheetState(() => _tts.baseRate = v);
                      },
                    ),
                  ),
                  Text('${_tts.baseRate.toStringAsFixed(1)}x'),
                ],
              ),
              const SizedBox(height: 16),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final bg = _bgColors[_bgIndex];
    final textColor = _darkMode ? Colors.white.withValues(alpha: 0.85) : const Color(0xFF333333);

    return Scaffold(
      backgroundColor: bg,
      body: GestureDetector(
        onTap: _toggleControls,
        onHorizontalDragEnd: (details) {
          if (details.primaryVelocity == null) return;
          if (details.primaryVelocity! < -300) _nextChapter();
          if (details.primaryVelocity! > 300) _prevChapter();
        },
        child: Stack(
          children: [
            if (_loading)
              const Center(child: CircularProgressIndicator())
            else ...[
              ScrollablePositionedList.builder(
                itemScrollController: _scrollCtrl,
                itemPositionsListener: _positionsListener,
                itemCount: _items.length + 2,
                itemBuilder: (context, index) {
                  if (index == 0) {
                    return Padding(
                      padding: const EdgeInsets.fromLTRB(24, 80, 24, 24),
                      child: Text(
                        _chapter?.displayTitle ?? '',
                        style: TextStyle(
                          fontSize: _fontSize + 4,
                          fontWeight: FontWeight.bold,
                          color: textColor,
                          height: 1.4,
                        ),
                      ),
                    );
                  }
                  if (index == _items.length + 1) {
                    return _buildChapterNav(textColor);
                  }
                  final item = _items[index - 1];
                  if (item.isIllustration) {
                    return Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                      child: ClipRRect(
                        borderRadius: BorderRadius.circular(8),
                        child: Image.network(
                          _api.illustrationUrl(widget.bookId, item.illustPath!),
                          fit: BoxFit.contain,
                          loadingBuilder: (_, child, progress) {
                            if (progress == null) return child;
                            return Container(
                              height: 200,
                              alignment: Alignment.center,
                              child: CircularProgressIndicator(
                                value: progress.expectedTotalBytes != null
                                    ? progress.cumulativeBytesLoaded / progress.expectedTotalBytes!
                                    : null,
                                strokeWidth: 2,
                              ),
                            );
                          },
                          errorBuilder: (_, __, ___) => const SizedBox.shrink(),
                        ),
                      ),
                    );
                  }
                  final isHighlighted = item.ttsIndex >= 0 && item.ttsIndex == _ttsHighlight;
                  return Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 2),
                    child: Container(
                      decoration: isHighlighted
                          ? BoxDecoration(
                              color: Theme.of(context).colorScheme.primaryContainer.withValues(alpha: 0.3),
                              borderRadius: BorderRadius.circular(4),
                            )
                          : null,
                      padding: const EdgeInsets.symmetric(vertical: 2),
                      child: Text(
                        item.text!.startsWith('　') ? item.text! : '　　${item.text!}',
                        style: TextStyle(
                          fontSize: _fontSize,
                          height: _lineHeight,
                          color: textColor,
                        ),
                      ),
                    ),
                  );
                },
              ),
            ],
            Positioned(
              top: 0, left: 0, right: 0,
              child: LinearProgressIndicator(
                value: _readProgress,
                minHeight: 2,
                backgroundColor: Colors.transparent,
                valueColor: AlwaysStoppedAnimation(AppTheme.seedPurple.withValues(alpha: 0.5)),
              ),
            ),
            if (_showControls) _buildTopBar(),
            if (_showControls) _buildBottomBar(),
          ],
        ),
      ),
    );
  }

  Widget _buildTopBar() {
    return Positioned(
      top: 0, left: 0, right: 0,
      child: Container(
        decoration: BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [Colors.black.withValues(alpha: 0.6), Colors.transparent],
          ),
        ),
        child: SafeArea(
          bottom: false,
          child: Row(
            children: [
              IconButton(icon: const Icon(Icons.arrow_back, color: Colors.white), onPressed: () => Navigator.of(context).pop()),
              Expanded(
                child: Text(
                  _chapter?.displayTitle ?? '',
                  style: const TextStyle(color: Colors.white, fontSize: 16),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              if (_readProgress > 0)
                Padding(
                  padding: const EdgeInsets.only(right: 12),
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: Colors.white.withValues(alpha: 0.15),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Text(
                      '${(_readProgress * 100).round()}%',
                      style: TextStyle(color: Colors.white.withValues(alpha: 0.8), fontSize: 12, fontWeight: FontWeight.w500),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildBottomBar() {
    return Positioned(
      bottom: 0, left: 0, right: 0,
      child: Container(
        decoration: BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.bottomCenter,
            end: Alignment.topCenter,
            colors: [Colors.black.withValues(alpha: 0.6), Colors.transparent],
          ),
        ),
        child: SafeArea(
          top: false,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceAround,
              children: [
                IconButton(
                  icon: const Icon(Icons.skip_previous, color: Colors.white),
                  onPressed: _currentChapterIdx > 0 ? _prevChapter : null,
                ),
                IconButton(
                  icon: const Icon(Icons.list, color: Colors.white),
                  onPressed: _showCatalog,
                ),
                IconButton(
                  icon: Icon(_ttsPlaying ? Icons.stop : Icons.headphones, color: Colors.white),
                  onPressed: _ttsParagraphs.isNotEmpty ? _toggleTts : null,
                ),
                IconButton(
                  icon: const Icon(Icons.settings, color: Colors.white),
                  onPressed: _showSettingsSheet,
                ),
                IconButton(
                  icon: const Icon(Icons.skip_next, color: Colors.white),
                  onPressed: _currentChapterIdx < widget.chapters.length - 1 ? _nextChapter : null,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildChapterNav(Color textColor) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 40, 24, 80),
      child: Row(
        children: [
          if (_currentChapterIdx > 0)
            Expanded(
              child: OutlinedButton(
                onPressed: _prevChapter,
                child: const Text('上一章'),
              ),
            ),
          if (_currentChapterIdx > 0 && _currentChapterIdx < widget.chapters.length - 1)
            const SizedBox(width: 16),
          if (_currentChapterIdx < widget.chapters.length - 1)
            Expanded(
              child: FilledButton(
                onPressed: _nextChapter,
                child: const Text('下一章'),
              ),
            ),
        ],
      ),
    );
  }

  void _showCatalog() {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: 0.6,
        maxChildSize: 0.9,
        builder: (ctx, scrollCtrl) => Column(
          children: [
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text('目录', style: Theme.of(context).textTheme.titleMedium),
            ),
            Expanded(
              child: ListView.builder(
                controller: scrollCtrl,
                itemCount: widget.chapters.length,
                itemBuilder: (ctx, i) {
                  final ch = widget.chapters[i];
                  final isCurrent = ch.id == _currentChapterId;
                  return ListTile(
                    dense: true,
                    selected: isCurrent,
                    title: Text(ch.displayTitle, maxLines: 1, overflow: TextOverflow.ellipsis),
                    onTap: () {
                      Navigator.of(ctx).pop();
                      _tts.stop();
                      setState(() { _ttsPlaying = false; _ttsHighlight = -1; });
                      _currentChapterIdx = i;
                      _currentChapterId = ch.id;
                      _loadChapter();
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

  @override
  void dispose() {
    _positionsListener.itemPositions.removeListener(_updateReadProgress);
    _tts.dispose();
    _api.dispose();
    SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge);
    super.dispose();
  }
}

class _ReaderItem {
  final String? text;
  final String? illustPath;
  final int ttsIndex;

  _ReaderItem._({this.text, this.illustPath, this.ttsIndex = -1});

  factory _ReaderItem.text(String text, int ttsIndex) =>
      _ReaderItem._(text: text, ttsIndex: ttsIndex);

  factory _ReaderItem.illustration(String path) =>
      _ReaderItem._(illustPath: path);

  bool get isIllustration => illustPath != null;
}
