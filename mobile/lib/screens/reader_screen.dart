import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:scrollable_positioned_list/scrollable_positioned_list.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/book.dart';
import '../services/api_service.dart';
import '../services/local_storage_service.dart';
import '../services/account_service.dart';
import '../services/reading_progress.dart' show ReadingProgressService;
import '../services/tts_service.dart';
import '../main.dart' show ttsService;
import '../theme/app_theme.dart';
import '../utils/user_content_guard.dart';
import '../widgets/user_content_notice_dialog.dart';
import '../widgets/reading_identity.dart';

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

class _ReaderScreenState extends State<ReaderScreen>
    with WidgetsBindingObserver {
  final _api = ApiService();
  final _progress = ReadingProgressService();
  final _storage = LocalStorageService();
  late TtsService _tts;
  final ItemScrollController _scrollCtrl = ItemScrollController();
  final ItemPositionsListener _positionsListener =
      ItemPositionsListener.create();

  static final _illustPattern = RegExp(r'^\[illustration:(.+)\]$');

  Chapter? _chapter;
  List<_ReaderItem> _items = [];
  List<String> _ttsParagraphs = [];
  Map<int, Map<String, dynamic>> _paragraphThreads = {};
  bool _loading = true;
  bool _showControls = true;
  int _ttsHighlight = -1;

  late String _currentChapterId;
  int _currentChapterIdx = 0;

  double _fontSize = 18;
  double _lineHeight = 1.8;
  bool _darkMode = false;
  bool _ttsPlaying = false;
  bool _ttsContinueOnLoad = false;
  double _readProgress = 0.0;
  int? _pendingTtsScrollParagraph;
  bool _ttsScrollRetryScheduled = false;
  int _ttsScrollRetryAttempts = 0;
  Timer? _readingHeartbeatTimer;
  DateTime _lastInteraction = DateTime.now();
  bool _readerForeground = true;
  int _chapterLoadGeneration = 0;

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
    WidgetsBinding.instance.addObserver(this);
    _tts = ttsService;
    _currentChapterId = widget.chapterId;
    _currentChapterIdx = widget.chapters.indexWhere(
      (c) => c.id == _currentChapterId,
    );
    if (_currentChapterIdx < 0) _currentChapterIdx = 0;
    _loadChapter();
    _loadSettings();
    _positionsListener.itemPositions.addListener(_updateReadProgress);
    _readingHeartbeatTimer = Timer.periodic(
      const Duration(seconds: 30),
      (_) => _sendReadingHeartbeat(),
    );
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final isDark = Theme.of(context).brightness == Brightness.dark;
      if (isDark && _bgIndex == 0) {
        setState(() {
          _bgIndex = 3;
          _darkMode = true;
        });
      }
    });
  }

  void _updateReadProgress() {
    final positions = _positionsListener.itemPositions.value;
    if (positions.isEmpty || _items.isEmpty) return;
    final maxIndex = positions
        .map((p) => p.index)
        .reduce((a, b) => a > b ? a : b);
    final totalItems = _items.length + 2;
    final progress = (maxIndex / (totalItems - 1)).clamp(0.0, 1.0);
    if ((_readProgress - progress).abs() > 0.005) {
      _markInteraction();
      setState(() => _readProgress = progress);
      _progress.save(widget.bookId, _currentChapterId, progress);
    }
  }

  void _markInteraction() {
    _lastInteraction = DateTime.now();
  }

  Future<void> _sendReadingHeartbeat() async {
    if (!_readerForeground || _loading || !AccountService.instance.isSignedIn) {
      return;
    }
    if (DateTime.now().difference(_lastInteraction) >
        const Duration(seconds: 75)) {
      return;
    }
    try {
      await AccountService.instance.sendReadingHeartbeat(bookId: widget.bookId);
    } catch (_) {
      // Reading must continue even when account timing is temporarily offline.
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _readerForeground = state == AppLifecycleState.resumed;
  }

  Future<void> _loadSettings() async {
    await _progress.init();
    await _storage.init();
  }

  Future<void> _loadChapter() async {
    final loadGeneration = ++_chapterLoadGeneration;
    final loadChapterId = _currentChapterId;
    setState(() => _loading = true);
    final Future<Map<String, dynamic>> commentsFuture = AccountService.instance
        .chapterComments(widget.bookId, loadChapterId)
        .catchError((_) => <String, dynamic>{});
    try {
      String content;
      final cached = await _storage.getDownloadedContent(
        widget.bookId,
        loadChapterId,
      );
      Chapter ch;
      if (cached != null) {
        final idx = widget.chapters.indexWhere((c) => c.id == loadChapterId);
        ch = idx >= 0
            ? widget.chapters[idx]
            : await _api.getChapter(widget.bookId, loadChapterId);
        content = cached;
      } else {
        ch = await _api.getChapter(widget.bookId, loadChapterId);
        content = ch.content ?? '';
      }
      final lines = content
          .split(RegExp(r'\n+'))
          .where((p) => p.trim().isNotEmpty)
          .toList();
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
      Map<String, dynamic> comments = const {};
      try {
        comments = await commentsFuture;
      } catch (_) {
        // Chapter reading remains available when comments are temporarily offline.
      }
      if (mounted &&
          loadGeneration == _chapterLoadGeneration &&
          loadChapterId == _currentChapterId) {
        setState(() {
          _chapter = ch;
          _items = items;
          _ttsParagraphs = ttsParas;
          _paragraphThreads = _threadsByIndex(comments);
          _loading = false;
          _ttsHighlight = -1;
          _readProgress = 0.0;
        });
        final shouldContinueTts = _ttsContinueOnLoad;
        _ttsContinueOnLoad = false;
        if (shouldContinueTts) {
          unawaited(_startTts(startParagraph: 0));
        } else if (_tts.active &&
            _tts.currentBookId == widget.bookId &&
            _tts.currentChapterId == loadChapterId) {
          _attachTtsCallbacks();
          setState(() {
            _ttsPlaying = true;
            _ttsHighlight = _tts.currentParagraphIndex;
          });
          _scrollToTtsParagraph(_tts.currentParagraphIndex);
        }
        _progress.save(widget.bookId, loadChapterId, 0.0);
        if (widget.book != null) {
          _storage.recordRead(widget.book!, loadChapterId, ch.displayTitle);
          if (AccountService.instance.isSignedIn) {
            await AccountService.instance.mergeLocalState(_storage);
          }
        }
      }
    } catch (_) {
      if (mounted && loadGeneration == _chapterLoadGeneration) {
        _ttsContinueOnLoad = false;
        setState(() {
          _loading = false;
          _ttsPlaying = false;
          _ttsHighlight = -1;
        });
      }
    }
  }

  void _toggleControls() {
    setState(() => _showControls = !_showControls);
    SystemChrome.setEnabledSystemUIMode(
      _showControls ? SystemUiMode.edgeToEdge : SystemUiMode.immersiveSticky,
    );
  }

  void _changeChapter(int offset, {required bool continueTts}) {
    final nextIndex = _currentChapterIdx + offset;
    if (nextIndex < 0 || nextIndex >= widget.chapters.length) return;
    _ttsContinueOnLoad = continueTts;
    _tts.stop();
    if (mounted) {
      setState(() {
        _ttsPlaying = continueTts;
        _ttsHighlight = -1;
      });
    }
    _currentChapterIdx = nextIndex;
    _currentChapterId = widget.chapters[_currentChapterIdx].id;
    _loadChapter();
  }

  void _nextChapter() {
    _changeChapter(1, continueTts: _ttsPlaying);
  }

  void _prevChapter() {
    _changeChapter(-1, continueTts: _ttsPlaying);
  }

  static Map<int, Map<String, dynamic>> _threadsByIndex(
    Map<String, dynamic> data,
  ) {
    final result = <int, Map<String, dynamic>>{};
    final paragraphs = data['paragraphs'];
    if (paragraphs is! Map) return result;
    for (final value in paragraphs.values) {
      if (value is! Map) continue;
      final thread = Map<String, dynamic>.from(value);
      final index = (thread['paragraph_index'] as num?)?.toInt();
      if (index != null && index >= 0) result[index] = thread;
    }
    return result;
  }

  String get _ttsCheckpointKey => 'oohstory_tts_checkpoint_${widget.bookId}';

  Future<void> _saveTtsCheckpoint(int paragraphIndex) async {
    final preferences = await SharedPreferences.getInstance();
    await preferences.setString(
      _ttsCheckpointKey,
      jsonEncode({
        'chapter_id': _tts.currentChapterId ?? _currentChapterId,
        'paragraph_index': paragraphIndex,
        'updated_at': DateTime.now().millisecondsSinceEpoch,
      }),
    );
  }

  void _scheduleTtsScrollRetry() {
    if (_ttsScrollRetryScheduled || !mounted) return;
    _ttsScrollRetryScheduled = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _ttsScrollRetryScheduled = false;
      final pending = _pendingTtsScrollParagraph;
      if (!mounted || pending == null) return;
      if (_ttsScrollRetryAttempts >= 8) {
        _pendingTtsScrollParagraph = null;
        _ttsScrollRetryAttempts = 0;
        return;
      }
      _ttsScrollRetryAttempts++;
      _scrollToTtsParagraph(pending);
    });
  }

  void _scrollToTtsParagraph(int idx) {
    if (!mounted || _tts.currentChapterId != _currentChapterId) {
      _pendingTtsScrollParagraph = null;
      _ttsScrollRetryAttempts = 0;
      return;
    }
    final scrollIdx = _items.indexWhere((item) => item.ttsIndex == idx);
    if (scrollIdx < 0 || !_scrollCtrl.isAttached) {
      _pendingTtsScrollParagraph = idx;
      _scheduleTtsScrollRetry();
      return;
    }
    _pendingTtsScrollParagraph = null;
    _ttsScrollRetryAttempts = 0;
    unawaited(
      _scrollCtrl
          .scrollTo(
            index: scrollIdx + 1,
            duration: const Duration(milliseconds: 260),
            curve: Curves.easeOutCubic,
          )
          .catchError((_) {}),
    );
  }

  void _attachTtsCallbacks() {
    _tts.onSkipPrev = _prevChapter;
    _tts.onSkipNext = _nextChapter;
    _tts.onParagraphChange = (idx) {
      unawaited(_saveTtsCheckpoint(idx));
      if (!mounted || _tts.currentChapterId != _currentChapterId) return;
      setState(() {
        _ttsPlaying = true;
        _ttsHighlight = idx;
      });
      _scrollToTtsParagraph(idx);
    };
    _tts.onChapterChange = (chapterId, _) {
      if (!mounted || chapterId == _currentChapterId) return;
      final targetIndex = widget.chapters.indexWhere(
        (chapter) => chapter.id == chapterId,
      );
      if (targetIndex < 0) return;
      _currentChapterIdx = targetIndex;
      _currentChapterId = chapterId;
      setState(() {
        _ttsPlaying = true;
        _ttsHighlight = -1;
      });
      unawaited(_loadChapter());
    };
    _tts.onComplete = () {
      if (!mounted) return;
      if (_currentChapterIdx < widget.chapters.length - 1) {
        _changeChapter(1, continueTts: true);
      } else {
        setState(() {
          _ttsPlaying = false;
          _ttsHighlight = -1;
        });
      }
    };
  }

  Future<void> _startTts({int? startParagraph}) async {
    if (_ttsParagraphs.isEmpty) {
      if (mounted) setState(() => _ttsPlaying = false);
      return;
    }
    if (_tts.active) _tts.stop();
    _tts.bookTitle = widget.book?.title;
    _tts.chapterTitle = _chapter?.displayTitle;
    _tts.authorName = widget.book?.author;
    _tts.coverArtUrl = _api.mediaCoverArtUrl(widget.bookId);
    _tts.configureChapter(
      bookId: widget.bookId,
      chapterId: _currentChapterId,
      title: _chapter?.displayTitle ?? '',
    );
    _tts.configureCatalog(
      chapterIds: widget.chapters.map((chapter) => chapter.id).toList(),
      chapterTitles: {
        for (final chapter in widget.chapters) chapter.id: chapter.displayTitle,
      },
    );
    final explicitStart = startParagraph != null;
    final allowServerResume = false;
    final requestedStart = explicitStart ? startParagraph : 0;
    final start = requestedStart.clamp(0, _ttsParagraphs.length - 1).toInt();
    try {
      await _tts.buildAuthoritativePlan(
        startParagraph: start,
        allowServerResume: allowServerResume,
      );
    } catch (error) {
      _tts.stop();
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('听书服务暂时不可用，请稍后重试')));
      return;
    }
    _attachTtsCallbacks();
    unawaited(_tts.play());
    if (mounted) setState(() => _ttsPlaying = true);
  }

  void _toggleTts() {
    if (_tts.active && _ttsPlaying) {
      _ttsContinueOnLoad = false;
      _tts.stop();
      setState(() {
        _ttsPlaying = false;
        _ttsHighlight = -1;
      });
    } else {
      if (_tts.active) {
        _ttsContinueOnLoad = false;
        _tts.stop();
        setState(() {
          _ttsPlaying = false;
          _ttsHighlight = -1;
        });
      }
      unawaited(_startTts(startParagraph: 0));
    }
  }

  Future<Map<String, dynamic>> _refreshParagraphComments(
    int paragraphIndex,
  ) async {
    final data = await AccountService.instance.chapterComments(
      widget.bookId,
      _currentChapterId,
    );
    if (mounted) setState(() => _paragraphThreads = _threadsByIndex(data));
    return _paragraphThreads[paragraphIndex] ??
        <String, dynamic>{
          'paragraph_index': paragraphIndex,
          'count': 0,
          'comments': <dynamic>[],
        };
  }

  void _showParagraphActions(_ReaderItem item) {
    _markInteraction();
    final theme = Theme.of(context);
    showModalBottomSheet<void>(
      context: context,
      useSafeArea: true,
      backgroundColor: Colors.transparent,
      builder: (sheetContext) => Container(
        margin: const EdgeInsets.all(12),
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 18),
        decoration: BoxDecoration(
          color: theme.colorScheme.surface,
          borderRadius: BorderRadius.circular(24),
          border: Border.all(
            color: theme.colorScheme.outlineVariant.withValues(alpha: .55),
          ),
          boxShadow: const [
            BoxShadow(
              color: Color(0x2B111827),
              blurRadius: 34,
              offset: Offset(0, 14),
            ),
          ],
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Center(
              child: Container(
                width: 38,
                height: 4,
                decoration: BoxDecoration(
                  color: theme.colorScheme.onSurface.withValues(alpha: .14),
                  borderRadius: BorderRadius.circular(9),
                ),
              ),
            ),
            const SizedBox(height: 14),
            Text(
              '选中这一段',
              style: theme.textTheme.labelSmall?.copyWith(
                color: theme.colorScheme.onSurface.withValues(alpha: .52),
                fontWeight: FontWeight.w800,
                letterSpacing: 1.4,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              item.text ?? '',
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.bodyMedium?.copyWith(height: 1.65),
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: _ParagraphActionButton(
                    icon: Icons.headphones_rounded,
                    label: '从此处听书',
                    subtitle: '从本段连续播放',
                    emphasized: true,
                    onTap: () {
                      Navigator.of(sheetContext).pop();
                      unawaited(_startTts(startParagraph: item.ttsIndex));
                    },
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: _ParagraphActionButton(
                    icon: Icons.chat_bubble_outline_rounded,
                    label: '字里行间',
                    subtitle: '评论与感谢',
                    onTap: () {
                      Navigator.of(sheetContext).pop();
                      _openInterline(item);
                    },
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  void _openInterline(_ReaderItem item) {
    _markInteraction();
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      backgroundColor: Colors.transparent,
      builder: (context) => SizedBox(
        height: MediaQuery.sizeOf(context).height * .84,
        child: _InterlineSheet(
          bookId: widget.bookId,
          chapterId: _currentChapterId,
          paragraphIndex: item.ttsIndex,
          paragraphText: item.text ?? '',
          initialThread: _paragraphThreads[item.ttsIndex],
          refresh: () => _refreshParagraphComments(item.ttsIndex),
        ),
      ),
    );
  }

  Future<void> _rebuildActiveTtsPlan() async {
    if (!_ttsPlaying || !_tts.active || _ttsParagraphs.isEmpty) return;
    final paragraph = _tts.currentParagraphIndex;
    _tts.stop();
    try {
      await _tts.buildAuthoritativePlan(startParagraph: paragraph);
    } catch (error) {
      if (!mounted) return;
      setState(() => _ttsPlaying = false);
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('听书设置更新失败，请稍后重试')));
      return;
    }
    await _tts.play();
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
                  ...List.generate(
                    _bgColors.length,
                    (i) => Padding(
                      padding: const EdgeInsets.only(right: 8),
                      child: GestureDetector(
                        onTap: () {
                          setSheetState(() => _bgIndex = i);
                          setState(() => _darkMode = i == _bgColors.length - 1);
                        },
                        child: Container(
                          width: 36,
                          height: 36,
                          decoration: BoxDecoration(
                            color: _bgColors[i],
                            shape: BoxShape.circle,
                            border: Border.all(
                              color: _bgIndex == i
                                  ? Theme.of(context).colorScheme.primary
                                  : Colors.grey.shade300,
                              width: _bgIndex == i ? 2.5 : 1,
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
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
                      onChangeEnd: (_) => _rebuildActiveTtsPlan(),
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
    final textColor = _darkMode
        ? Colors.white.withValues(alpha: 0.85)
        : const Color(0xFF333333);

    return Scaffold(
      backgroundColor: bg,
      body: GestureDetector(
        onTapDown: (_) => _markInteraction(),
        onTap: _toggleControls,
        onHorizontalDragStart: (_) => _markInteraction(),
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
                      padding: const EdgeInsets.symmetric(
                        horizontal: 16,
                        vertical: 8,
                      ),
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
                                    ? progress.cumulativeBytesLoaded /
                                          progress.expectedTotalBytes!
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
                  final isHighlighted =
                      item.ttsIndex >= 0 && item.ttsIndex == _ttsHighlight;
                  final commentCount =
                      (_paragraphThreads[item.ttsIndex]?['count'] as num?)
                          ?.toInt() ??
                      0;
                  return Padding(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 24,
                      vertical: 2,
                    ),
                    child: GestureDetector(
                      behavior: HitTestBehavior.opaque,
                      onLongPress: () => _showParagraphActions(item),
                      child: Container(
                        decoration: isHighlighted
                            ? BoxDecoration(
                                color: Theme.of(context)
                                    .colorScheme
                                    .primaryContainer
                                    .withValues(alpha: 0.3),
                                borderRadius: BorderRadius.circular(4),
                              )
                            : null,
                        padding: const EdgeInsets.symmetric(vertical: 2),
                        child: Text.rich(
                          TextSpan(
                            children: [
                              TextSpan(
                                text: item.text!.startsWith('　')
                                    ? item.text!
                                    : '　　${item.text!}',
                              ),
                              if (commentCount > 0)
                                WidgetSpan(
                                  alignment: PlaceholderAlignment.middle,
                                  child: Padding(
                                    padding: const EdgeInsets.only(left: 7),
                                    child: InkWell(
                                      borderRadius: BorderRadius.circular(20),
                                      onTap: () => _openInterline(item),
                                      child: Container(
                                        padding: const EdgeInsets.symmetric(
                                          horizontal: 7,
                                          vertical: 2,
                                        ),
                                        decoration: BoxDecoration(
                                          color: AppTheme.seedPurple.withValues(
                                            alpha: .10,
                                          ),
                                          borderRadius: BorderRadius.circular(
                                            20,
                                          ),
                                          border: Border.all(
                                            color: AppTheme.seedPurple
                                                .withValues(alpha: .24),
                                          ),
                                        ),
                                        child: Text(
                                          '🫧 $commentCount',
                                          style: TextStyle(
                                            color: _darkMode
                                                ? Colors.white70
                                                : AppTheme.seedPurple,
                                            fontSize: 11,
                                            height: 1.35,
                                            fontWeight: FontWeight.w700,
                                          ),
                                        ),
                                      ),
                                    ),
                                  ),
                                ),
                            ],
                          ),
                          style: TextStyle(
                            fontSize: _fontSize,
                            height: _lineHeight,
                            color: textColor,
                          ),
                        ),
                      ),
                    ),
                  );
                },
              ),
            ],
            Positioned(
              top: 0,
              left: 0,
              right: 0,
              child: LinearProgressIndicator(
                value: _readProgress,
                minHeight: 2,
                backgroundColor: Colors.transparent,
                valueColor: AlwaysStoppedAnimation(
                  AppTheme.seedPurple.withValues(alpha: 0.5),
                ),
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
      top: 0,
      left: 0,
      right: 0,
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
              IconButton(
                icon: const Icon(Icons.arrow_back, color: Colors.white),
                onPressed: () => Navigator.of(context).pop(),
              ),
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
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8,
                      vertical: 3,
                    ),
                    decoration: BoxDecoration(
                      color: Colors.white.withValues(alpha: 0.15),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Text(
                      '${(_readProgress * 100).round()}%',
                      style: TextStyle(
                        color: Colors.white.withValues(alpha: 0.8),
                        fontSize: 12,
                        fontWeight: FontWeight.w500,
                      ),
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
      bottom: 0,
      left: 0,
      right: 0,
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
                  icon: Icon(
                    _ttsPlaying ? Icons.stop : Icons.headphones,
                    color: Colors.white,
                  ),
                  onPressed: _ttsParagraphs.isNotEmpty ? _toggleTts : null,
                ),
                IconButton(
                  icon: const Icon(Icons.settings, color: Colors.white),
                  onPressed: _showSettingsSheet,
                ),
                IconButton(
                  icon: const Icon(Icons.skip_next, color: Colors.white),
                  onPressed: _currentChapterIdx < widget.chapters.length - 1
                      ? _nextChapter
                      : null,
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
          if (_currentChapterIdx > 0 &&
              _currentChapterIdx < widget.chapters.length - 1)
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
                    title: Text(
                      ch.displayTitle,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    onTap: () {
                      Navigator.of(ctx).pop();
                      _ttsContinueOnLoad = false;
                      _tts.stop();
                      setState(() {
                        _ttsPlaying = false;
                        _ttsHighlight = -1;
                      });
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
    _readingHeartbeatTimer?.cancel();
    WidgetsBinding.instance.removeObserver(this);
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

class _ParagraphActionButton extends StatelessWidget {
  final IconData icon;
  final String label;
  final String subtitle;
  final bool emphasized;
  final VoidCallback onTap;

  const _ParagraphActionButton({
    required this.icon,
    required this.label,
    required this.subtitle,
    required this.onTap,
    this.emphasized = false,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final foreground = emphasized
        ? theme.colorScheme.onPrimary
        : theme.colorScheme.onSurface;
    return Material(
      color: emphasized
          ? theme.colorScheme.primary
          : theme.colorScheme.surfaceContainerHighest.withValues(alpha: .62),
      borderRadius: BorderRadius.circular(16),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(16),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 13),
          child: Row(
            children: [
              Container(
                width: 36,
                height: 36,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  color: emphasized
                      ? Colors.white.withValues(alpha: .16)
                      : theme.colorScheme.primary.withValues(alpha: .10),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Icon(
                  icon,
                  size: 20,
                  color: emphasized ? foreground : theme.colorScheme.primary,
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      label,
                      maxLines: 1,
                      style: TextStyle(
                        color: foreground,
                        fontSize: 13,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      subtitle,
                      maxLines: 1,
                      style: TextStyle(
                        color: foreground.withValues(alpha: .66),
                        fontSize: 10,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _InterlineSheet extends StatefulWidget {
  final String bookId;
  final String chapterId;
  final int paragraphIndex;
  final String paragraphText;
  final Map<String, dynamic>? initialThread;
  final Future<Map<String, dynamic>> Function() refresh;

  const _InterlineSheet({
    required this.bookId,
    required this.chapterId,
    required this.paragraphIndex,
    required this.paragraphText,
    required this.initialThread,
    required this.refresh,
  });

  @override
  State<_InterlineSheet> createState() => _InterlineSheetState();
}

class _InterlineSheetState extends State<_InterlineSheet> {
  final _account = AccountService.instance;
  final _controller = TextEditingController();
  final Set<String> _likeLoading = {};
  late Map<String, dynamic> _thread;
  bool _posting = false;

  @override
  void initState() {
    super.initState();
    _thread = Map<String, dynamic>.from(
      widget.initialThread ??
          <String, dynamic>{
            'paragraph_index': widget.paragraphIndex,
            'count': 0,
            'comments': <dynamic>[],
          },
    );
    _controller.addListener(_updateCounter);
  }

  void _updateCounter() {
    if (mounted) setState(() {});
  }

  List<Map<String, dynamic>> get _comments =>
      (_thread['comments'] as List? ?? const [])
          .whereType<Map>()
          .map((item) => Map<String, dynamic>.from(item))
          .toList();

  Future<void> _showMessage(String title, String message) => showDialog<void>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(title),
      content: Text(message),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('知道了'),
        ),
      ],
    ),
  );

  Future<void> _post() async {
    if (!AccountService.instance.isSignedIn) {
      await _showMessage('请先登录', '返回个人中心登录后，即可参与字里行间。');
      return;
    }
    final content = _controller.text.trim();
    if (content.isEmpty) {
      await _showMessage('无法评论', '评论不能为空');
      return;
    }
    final issue = UserContentGuard.issue(content);
    if (issue != null) {
      await showUserContentNoticeDialog(context, issue: issue);
      return;
    }
    setState(() => _posting = true);
    try {
      await AccountService.instance.createParagraphComment(
        bookId: widget.bookId,
        chapterId: widget.chapterId,
        paragraphIndex: widget.paragraphIndex,
        content: content,
      );
      final thread = await widget.refresh();
      if (!mounted) return;
      setState(() {
        _thread = thread;
        _posting = false;
        _controller.clear();
      });
    } on AccountException catch (error) {
      if (!mounted) return;
      setState(() => _posting = false);
      if (UserContentGuard.isModerationMessage(error.message)) {
        await showUserContentNoticeDialog(context, issue: error.message);
      } else {
        await _showMessage('无法评论', error.message);
      }
    } catch (_) {
      if (!mounted) return;
      setState(() => _posting = false);
      await _showMessage('无法评论', '网络暂时不可用，请稍后重试');
    }
  }

  Future<void> _addLike(Map<String, dynamic> comment) async {
    if (!AccountService.instance.isSignedIn) {
      await _showMessage('请先登录', '登录后才能为其他读者的评论点赞。');
      return;
    }
    final id = comment['id'] as String? ?? '';
    final viewerLikes = (comment['viewer_like_count'] as num?)?.toInt() ?? 0;
    if (id.isEmpty || viewerLikes >= 3 || _likeLoading.contains(id)) return;
    setState(() => _likeLoading.add(id));
    try {
      await AccountService.instance.addParagraphCommentLike(id);
      final thread = await widget.refresh();
      if (!mounted) return;
      setState(() => _thread = thread);
    } on AccountException catch (error) {
      if (mounted) await _showMessage('暂时无法点赞', error.message);
    } catch (_) {
      if (mounted) await _showMessage('暂时无法点赞', '网络暂时不可用，请稍后重试');
    } finally {
      if (mounted) setState(() => _likeLoading.remove(id));
    }
  }

  String _time(String? value) {
    final date = DateTime.tryParse(value ?? '')?.toLocal();
    if (date == null) return '';
    String two(int number) => number.toString().padLeft(2, '0');
    return '${two(date.month)}-${two(date.day)} ${two(date.hour)}:${two(date.minute)}';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final dark = theme.brightness == Brightness.dark;
    final comments = _comments;
    return Material(
      color: dark ? const Color(0xFF17242B) : const Color(0xFFFBFAF7),
      borderRadius: const BorderRadius.vertical(top: Radius.circular(26)),
      clipBehavior: Clip.antiAlias,
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 16, 10, 10),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '字里行间',
                        style: TextStyle(
                          color: theme.colorScheme.primary,
                          fontSize: 12,
                          fontWeight: FontWeight.w800,
                          letterSpacing: 2.2,
                        ),
                      ),
                      const SizedBox(height: 3),
                      Text(
                        '这一段的 ${comments.length} 条评论',
                        style: theme.textTheme.titleLarge?.copyWith(
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                    ],
                  ),
                ),
                IconButton(
                  onPressed: () => Navigator.of(context).pop(),
                  icon: const Icon(Icons.close),
                  tooltip: '关闭',
                ),
              ],
            ),
          ),
          Container(
            width: double.infinity,
            constraints: const BoxConstraints(maxHeight: 112),
            margin: const EdgeInsets.fromLTRB(20, 0, 20, 10),
            padding: const EdgeInsets.all(13),
            decoration: BoxDecoration(
              color: theme.colorScheme.primary.withValues(alpha: .08),
              borderRadius: BorderRadius.circular(13),
              border: Border(
                left: BorderSide(color: theme.colorScheme.primary, width: 3),
              ),
            ),
            child: SingleChildScrollView(
              child: Text(
                widget.paragraphText,
                style: const TextStyle(height: 1.65),
              ),
            ),
          ),
          Expanded(
            child: comments.isEmpty
                ? Center(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Text('🫧', style: TextStyle(fontSize: 31)),
                        const SizedBox(height: 8),
                        Text(
                          '这一段还没有评论',
                          style: theme.textTheme.titleSmall?.copyWith(
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text('留下第一个字里行间的想法。', style: theme.textTheme.bodySmall),
                      ],
                    ),
                  )
                : ListView.separated(
                    padding: const EdgeInsets.symmetric(horizontal: 20),
                    itemCount: comments.length,
                    separatorBuilder: (_, __) => const Divider(height: 1),
                    itemBuilder: (context, index) {
                      final comment = comments[index];
                      final author = Map<String, dynamic>.from(
                        comment['author'] as Map? ?? const {},
                      );
                      final reading = Map<String, dynamic>.from(
                        author['reading'] as Map? ?? const {},
                      );
                      final own = comment['is_own'] as bool? ?? false;
                      final viewerLikes =
                          ((comment['viewer_like_count'] as num?)?.toInt() ?? 0)
                              .clamp(0, 3);
                      final totalLikes =
                          (comment['like_count'] as num?)?.toInt() ??
                          (comment['thanks_count'] as num?)?.toInt() ??
                          0;
                      final id = comment['id'] as String? ?? '';
                      final displayName =
                          author['display_name'] as String? ?? '读者';
                      final avatarUrl = author['avatar_url'] as String? ?? '';
                      final initial = displayName.characters.isEmpty
                          ? '读'
                          : displayName.characters.first;
                      final rankLevel =
                          (reading['level'] as num?)?.toInt() ?? 1;
                      final rankRoman = reading['roman'] as String? ?? 'Ⅰ';
                      final rankName = reading['name'] as String? ?? '只如初见';
                      return Padding(
                        padding: const EdgeInsets.symmetric(vertical: 14),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                CircleAvatar(
                                  radius: 18,
                                  backgroundColor: theme.colorScheme.primary
                                      .withValues(alpha: .14),
                                  foregroundImage: avatarUrl.isEmpty
                                      ? null
                                      : NetworkImage(
                                          _account.avatarUrl(avatarUrl),
                                        ),
                                  child: Text(
                                    initial,
                                    style: TextStyle(
                                      color: theme.colorScheme.primary,
                                      fontSize: 12,
                                      fontWeight: FontWeight.w800,
                                    ),
                                  ),
                                ),
                                const SizedBox(width: 9),
                                Expanded(
                                  child: Column(
                                    crossAxisAlignment:
                                        CrossAxisAlignment.start,
                                    children: [
                                      Text(
                                        displayName,
                                        maxLines: 1,
                                        overflow: TextOverflow.ellipsis,
                                        style: const TextStyle(
                                          fontWeight: FontWeight.w700,
                                        ),
                                      ),
                                      const SizedBox(height: 3),
                                      Row(
                                        children: [
                                          ReadingRankBadge(
                                            level: rankLevel,
                                            roman: rankRoman,
                                            size: 23,
                                          ),
                                          const SizedBox(width: 6),
                                          Flexible(
                                            child: Text(
                                              '$rankRoman · $rankName',
                                              maxLines: 1,
                                              overflow: TextOverflow.ellipsis,
                                              style: theme.textTheme.labelSmall
                                                  ?.copyWith(
                                                    color: theme
                                                        .colorScheme
                                                        .onSurface
                                                        .withValues(alpha: .58),
                                                    fontSize: 10,
                                                  ),
                                            ),
                                          ),
                                        ],
                                      ),
                                    ],
                                  ),
                                ),
                                const SizedBox(width: 8),
                                Text(
                                  _time(comment['created_at'] as String?),
                                  style: theme.textTheme.bodySmall?.copyWith(
                                    fontSize: 11,
                                    color: theme.colorScheme.onSurface
                                        .withValues(alpha: .48),
                                  ),
                                ),
                              ],
                            ),
                            const SizedBox(height: 9),
                            Text(
                              comment['content'] as String? ?? '',
                              style: const TextStyle(height: 1.65),
                            ),
                            Align(
                              alignment: Alignment.centerRight,
                              child: TextButton.icon(
                                onPressed:
                                    own ||
                                        viewerLikes >= 3 ||
                                        _likeLoading.contains(id)
                                    ? null
                                    : () => _addLike(comment),
                                icon: Icon(
                                  Icons.favorite_border_rounded,
                                  size: 18,
                                  color: viewerLikes > 0
                                      ? const Color(0xFFE0526D)
                                      : theme.colorScheme.onSurface.withValues(
                                          alpha: .58,
                                        ),
                                ),
                                label: Text(
                                  own
                                      ? '收到点赞 · $totalLikes'
                                      : viewerLikes >= 3
                                      ? '已点满 3/3 · $totalLikes'
                                      : viewerLikes > 0
                                      ? '再赞一次 $viewerLikes/3 · $totalLikes'
                                      : '点赞 · $totalLikes',
                                ),
                              ),
                            ),
                          ],
                        ),
                      );
                    },
                  ),
          ),
          Container(
            padding: EdgeInsets.fromLTRB(
              20,
              12,
              20,
              12 + MediaQuery.viewInsetsOf(context).bottom,
            ),
            decoration: BoxDecoration(
              color: dark ? const Color(0xFF1B2A31) : const Color(0xFFF8F9F7),
              border: Border(top: BorderSide(color: theme.dividerColor)),
            ),
            child: Column(
              children: [
                TextField(
                  controller: _controller,
                  maxLength: 500,
                  minLines: 2,
                  maxLines: 4,
                  style: const TextStyle(fontSize: 16, height: 1.5),
                  decoration: const InputDecoration(
                    hintText: '说说你对这一段的理解…',
                    counterText: '',
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.all(Radius.circular(13)),
                    ),
                  ),
                ),
                const SizedBox(height: 9),
                Row(
                  children: [
                    Text(
                      '${_controller.text.characters.length} / 500',
                      style: theme.textTheme.bodySmall,
                    ),
                    const Spacer(),
                    FilledButton(
                      onPressed: _posting ? null : _post,
                      child: Text(_posting ? '发布中…' : '发布评论'),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  @override
  void dispose() {
    _controller.removeListener(_updateCounter);
    _controller.dispose();
    super.dispose();
  }
}
