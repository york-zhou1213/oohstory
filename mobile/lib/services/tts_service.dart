import 'dart:async';
import 'dart:math';

import 'package:just_audio/just_audio.dart';
import 'api_service.dart';
import 'audiobook_cache.dart';
import 'tts_audio_handler.dart';

class TtsService {
  static const int _streamBatchSize = 5;

  final ApiService _api;
  final TtsAudioHandler _handler;
  late final AudioPlayer _player;
  late final AudiobookCache _audiobookCache;
  String _audiobookSessionId = '';
  String _audiobookManifestHash = '';
  String voice = 'nuanxi';
  String narrator = 'mocheng';
  String mode = 'normal';
  double baseRate = 1.0;
  bool active = false;

  String? bookTitle;
  String? chapterTitle;
  String? authorName;
  String? coverArtUrl;

  List<_TtsItem> _plan = [];
  int _currentIndex = -1;
  int _generation = 0;
  int _streamStartIndex = 0;
  Duration _streamResumeOffset = Duration.zero;
  Duration _lastStreamPosition = Duration.zero;
  Duration _initialStreamOffset = Duration.zero;
  int _trustedIndex = 0;
  Duration _trustedOffset = Duration.zero;
  int _timelineLoadedThrough = -1;
  int _lastTimelineRefreshMs = 0;
  int _lastProgressSaveMs = 0;
  Future<void>? _timelineRequest;
  StreamSubscription<Duration>? _positionSub;
  StreamSubscription<PlayerState>? _stateSub;
  StreamSubscription<PlaybackEvent>? _eventSub;
  String _buildingBookId = '';
  String _buildingChapterId = '';
  String _buildingChapterTitle = '';
  final Set<String> _queuedChapterIds = {};
  Map<String, dynamic>? _nextManifest;
  bool _queueingFollowingChapter = false;
  Future<void>? _followingChapterRequest;
  bool _chapterTransitionInProgress = false;
  String? currentBookId;
  String? currentChapterId;
  String? currentChapterTitle;
  int _recoveryAttempts = 0;
  bool _recoveryInProgress = false;
  bool _replayRecoveryInProgress = false;
  void Function(int paraIdx)? onParagraphChange;
  void Function(String chapterId, String chapterTitle)? onChapterChange;
  void Function()? onComplete;
  void Function()? onSkipPrev;
  void Function()? onSkipNext;

  TtsService(this._api, this._handler) {
    _player = _handler.player;
    _audiobookCache = AudiobookCache(_api);
    unawaited(_audiobookCache.clearSession());
    _handler.onSkipPrev = () {
      if (_chapterTransitionInProgress) return;
      if (_currentIndex > 0) {
        unawaited(_seekToPlanIndex(_currentIndex - 1));
      } else {
        onSkipPrev?.call();
      }
    };
    _handler.onSkipNext = () {
      if (_chapterTransitionInProgress) return;
      if (_currentIndex >= 0 && _currentIndex < _plan.length - 1) {
        unawaited(_seekToPlanIndex(_currentIndex + 1));
      } else {
        onSkipNext?.call();
      }
    };
  }

  void configureChapter({
    required String bookId,
    required String chapterId,
    required String title,
  }) {
    _buildingBookId = bookId;
    _buildingChapterId = chapterId;
    _buildingChapterTitle = title;
    currentBookId = bookId;
    currentChapterId = chapterId;
    currentChapterTitle = title;
  }

  void configureCatalog({
    required List<String> chapterIds,
    Map<String, String> chapterTitles = const {},
  }) {
    // Chapter progression is supplied by the authoritative server manifest.
  }

  bool hasQueuedChapter(String chapterId) =>
      _queuedChapterIds.contains(chapterId) ||
      '${_nextManifest?['chapter_id'] ?? ''}' == chapterId;
  bool get isPlaying => active && _player.playing;

  Future<void> buildAuthoritativePlan({
    int startParagraph = 0,
    bool allowServerResume = true,
  }) async {
    if (_buildingBookId.isEmpty || _buildingChapterId.isEmpty) {
      throw StateError('configureChapter must be called first');
    }
    unawaited(_audiobookCache.clearSession());
    final requestedStartParagraph = max(0, startParagraph);
    final payload = await _api.createAudiobookSession(
      bookId: _buildingBookId,
      chapterId: _buildingChapterId,
      mode: mode,
      narrator: narrator,
      voice: voice,
      rate: baseRate,
      resume: allowServerResume,
      startParagraphIndex: requestedStartParagraph,
    );
    _audiobookSessionId = payload['session_id'] as String;
    final manifest = payload['current'] as Map<String, dynamic>;
    final resume = payload['resume'] is Map
        ? (payload['resume'] as Map).cast<String, dynamic>()
        : null;
    final manifestChapterId = '${manifest['chapter_id'] ?? _buildingChapterId}';
    final resumeMatchesChapter =
        allowServerResume &&
        resume != null &&
        '${resume['chapter_id'] ?? ''}' == manifestChapterId;
    final resumeItemIndex = resumeMatchesChapter
        ? (resume['item_index'] as num?)?.toInt()
        : null;
    final resumeOffset = resumeMatchesChapter
        ? Duration(
            milliseconds: max(
              0,
              (resume['audio_offset_ms'] as num?)?.toInt() ?? 0,
            ),
          )
        : Duration.zero;
    final start = payload['start'] is Map
        ? (payload['start'] as Map).cast<String, dynamic>()
        : null;
    final startItemIndex = !resumeMatchesChapter
        ? (start?['item_index'] as num?)?.toInt()
        : null;
    final resolvedStartParagraph = !resumeMatchesChapter
        ? ((start?['paragraph_index'] as num?)?.toInt() ??
              requestedStartParagraph)
        : requestedStartParagraph;
    _loadManifestPlan(
      manifest,
      bookId: _buildingBookId,
      fallbackChapterId: _buildingChapterId,
      fallbackTitle: _buildingChapterTitle,
      startParagraph: resolvedStartParagraph,
      startSegmentIndex: resumeItemIndex ?? startItemIndex,
      resumeOffset: resumeOffset,
    );
    _queuedChapterIds
      ..clear()
      ..add(currentChapterId ?? _buildingChapterId);
    _nextManifest = null;
  }

  Future<void> _prefetchAuthoritativeNext() async {
    if (_queueingFollowingChapter || _audiobookSessionId.isEmpty) return;
    _queueingFollowingChapter = true;
    final sessionId = _audiobookSessionId;
    final fromChapterId = currentChapterId;
    try {
      final manifest = await _api.prefetchAudiobookNext(
        sessionId,
        fromChapterId: fromChapterId,
      );
      if (manifest == null || _audiobookSessionId != sessionId || !active) {
        return;
      }
      final chapterId = '${manifest['chapter_id']}';
      _nextManifest = manifest;
      _queuedChapterIds.add(chapterId);
    } catch (_) {
      if (active &&
          _audiobookSessionId == sessionId &&
          _player.processingState == ProcessingState.completed) {
        active = false;
        onComplete?.call();
      }
    } finally {
      _queueingFollowingChapter = false;
    }
  }

  Future<void> _ensureFollowingChapterQueued() async {
    if (_followingChapterRequest != null) {
      await _followingChapterRequest;
      return;
    }
    if (!active || _plan.isEmpty) return;
    if (_nextManifest != null) return;
    if (_audiobookSessionId.isEmpty) return;
    final request = _prefetchAuthoritativeNext();
    _followingChapterRequest = request;
    try {
      await request;
    } finally {
      if (identical(_followingChapterRequest, request)) {
        _followingChapterRequest = null;
      }
    }
  }

  void _loadManifestPlan(
    Map<String, dynamic> manifest, {
    required String bookId,
    required String fallbackChapterId,
    required String fallbackTitle,
    int startParagraph = 0,
    int? startSegmentIndex,
    Duration resumeOffset = Duration.zero,
  }) {
    final segments = (manifest['segments'] as List)
        .cast<Map<String, dynamic>>();
    final streamEndpoint = '${manifest['stream_endpoint'] ?? ''}';
    _audiobookManifestHash = '${manifest['manifest_hash'] ?? ''}';
    final chapterId = '${manifest['chapter_id'] ?? fallbackChapterId}';
    final title = (manifest['title'] as String?) ?? fallbackTitle;
    final hasSegmentStart = startSegmentIndex != null;
    _plan = [
      for (final segment in segments)
        if (hasSegmentStart
            ? (segment['index'] as num).toInt() >= startSegmentIndex
            : (segment['paragraph_index'] as num).toInt() >= startParagraph)
          _TtsItem(
            streamEndpoint: streamEndpoint,
            segmentIndex: (segment['index'] as num).toInt(),
            paraIdx: (segment['paragraph_index'] as num).toInt(),
            durationSeconds: _estimatedDurationSeconds(segment),
            durationExact: _hasExactDuration(segment),
            bookId: bookId,
            chapterId: chapterId,
            chapterTitle: title,
          ),
    ];
    currentBookId = bookId;
    currentChapterId = chapterId;
    currentChapterTitle = title;
    _currentIndex = _plan.isEmpty ? -1 : 0;
    _initialStreamOffset =
        hasSegmentStart &&
            _plan.isNotEmpty &&
            _plan.first.segmentIndex == startSegmentIndex
        ? resumeOffset
        : Duration.zero;
    _timelineLoadedThrough = -1;
    _timelineRequest = null;
  }

  AudioSource _sourceFor(_TtsItem item, {Duration offset = Duration.zero}) {
    return AudioSource.uri(
      _api.audiobookContinuousStreamUri(
        item.streamEndpoint,
        start: item.segmentIndex,
        offsetMs: offset.inMilliseconds,
        streamId: _newStreamId(),
      ),
    );
  }

  String _newStreamId() {
    final random = Random.secure();
    return List<int>.generate(
      16,
      (_) => random.nextInt(256),
    ).map((value) => value.toRadixString(16).padLeft(2, '0')).join();
  }

  double _estimatedDurationSeconds(Map<String, dynamic> segment) {
    final durationSeconds =
        segment['durationSeconds'] ?? segment['duration_seconds'];
    if (durationSeconds is num && durationSeconds > 0) {
      return durationSeconds.toDouble();
    }
    final durationMs = segment['duration_ms'];
    if (durationMs is num && durationMs > 0) {
      return durationMs.toDouble() / 1000;
    }
    final text = '${segment['text'] ?? ''}';
    final punctuation = RegExp(r'[，。！？；：,.!?;:…—]').allMatches(text).length;
    final rate = baseRate.clamp(0.5, 3.0).toDouble();
    return max(0.8, ((text.runes.length / 4.45) + punctuation * 0.12) / rate);
  }

  bool _hasExactDuration(Map<String, dynamic> segment) {
    final seconds = segment['durationSeconds'] ?? segment['duration_seconds'];
    final millis = segment['duration_ms'];
    return seconds is num && seconds > 0 || millis is num && millis > 0;
  }

  Future<void> _refreshTimeline() {
    if (_audiobookSessionId.isEmpty ||
        _audiobookManifestHash.isEmpty ||
        _plan.isEmpty) {
      return Future<void>.value();
    }
    if (_timelineRequest != null) return _timelineRequest!;
    final nowMs = DateTime.now().millisecondsSinceEpoch;
    if (nowMs - _lastTimelineRefreshMs < 1000) return Future<void>.value();
    _lastTimelineRefreshMs = nowMs;
    final firstIndex =
        _plan[_streamStartIndex.clamp(0, _plan.length - 1).toInt()]
            .segmentIndex;
    final lastIndex = _plan.last.segmentIndex;
    final timelineStart = max(firstIndex, _timelineLoadedThrough + 1);
    if (timelineStart > lastIndex) return Future<void>.value();
    final request = _api
        .fetchAudiobookTimeline(
          _audiobookSessionId,
          _audiobookManifestHash,
          start: timelineStart,
          limit: _streamBatchSize,
        )
        .then((payload) {
          if (payload == null || !active) return;
          final durations = <int, int>{
            for (final item in (payload['segments'] as List? ?? const []))
              if ((item as Map)['duration_ms'] is num)
                (item['index'] as num).toInt(): (item['duration_ms'] as num)
                    .toInt(),
          };
          for (final item in _plan) {
            final durationMs = durations[item.segmentIndex] ?? 0;
            if (durationMs > 0) {
              item.durationSeconds = durationMs / 1000;
              item.durationExact = true;
            }
          }
          var loaded = timelineStart - 1;
          for (final item in (payload['segments'] as List? ?? const [])) {
            final row = item as Map;
            final index = (row['index'] as num?)?.toInt() ?? -1;
            final durationMs = (row['duration_ms'] as num?)?.toInt() ?? 0;
            if (index != loaded + 1 || durationMs <= 0) break;
            loaded = index;
          }
          _timelineLoadedThrough = max(_timelineLoadedThrough, loaded);
        })
        .catchError((_) {});
    _timelineRequest = request.whenComplete(() {
      if (identical(_timelineRequest, request)) _timelineRequest = null;
    });
    return _timelineRequest!;
  }

  Future<void> _seekToPlanIndex(int index) async {
    if (_plan.isEmpty) return;
    final target = index.clamp(0, _plan.length - 1);
    await _startStreamAt(target, generation: _generation);
  }

  int get currentParagraphIndex {
    if (_currentIndex < 0 || _currentIndex >= _plan.length) return 0;
    return _plan[_currentIndex].paraIdx;
  }

  Future<void> play({int fromIndex = 0}) async {
    if (_plan.isEmpty) {
      active = false;
      onComplete?.call();
      return;
    }
    active = true;
    _currentIndex = fromIndex.clamp(0, _plan.length - 1);
    final generation = ++_generation;
    _recoveryAttempts = 0;
    _recoveryInProgress = false;
    _replayRecoveryInProgress = false;
    _chapterTransitionInProgress = false;
    _lastProgressSaveMs = 0;
    await _positionSub?.cancel();
    await _stateSub?.cancel();
    await _eventSub?.cancel();
    _positionSub = _player.positionStream.listen((position) {
      if (!active || generation != _generation) return;
      _syncStreamPosition(position);
    });
    _stateSub = _player.playerStateStream.listen((state) {
      if (!active || generation != _generation) return;
      if (state.processingState == ProcessingState.completed &&
          !_chapterTransitionInProgress) {
        unawaited(_completeCurrentChapter(generation));
      }
    });
    _eventSub = _player.playbackEventStream.listen(
      (_) {},
      onError: (Object error, StackTrace stackTrace) {
        if (active && generation == _generation) {
          unawaited(_recoverPlayback(generation));
        }
      },
    );
    final startOffset = _currentIndex == 0
        ? _initialStreamOffset
        : Duration.zero;
    _initialStreamOffset = Duration.zero;
    await _startStreamAt(
      _currentIndex,
      offset: startOffset,
      generation: generation,
    );
  }

  void _syncStreamPosition(Duration position) {
    if (_plan.isEmpty) return;
    if (_rejectStreamReplay(position)) return;
    final candidate = _resolvedStreamPlanIndex(position);
    if (candidate != _currentIndex) _handleIndex(candidate);
    _rememberTrustedPosition(candidate, position: position);
    final nowMs = DateTime.now().millisecondsSinceEpoch;
    if (nowMs - _lastProgressSaveMs >= 5000) {
      unawaited(_saveProgress(position: position));
    }
    unawaited(_refreshTimeline());
  }

  Future<void> _saveProgress({Duration? position}) async {
    if (_audiobookSessionId.isEmpty ||
        _plan.isEmpty ||
        _currentIndex < 0 ||
        _currentIndex >= _plan.length) {
      return;
    }
    final planIndex = _resolvedStreamPlanIndex(position);
    if (planIndex < 0 || planIndex >= _plan.length) return;
    final item = _plan[planIndex];
    _lastProgressSaveMs = DateTime.now().millisecondsSinceEpoch;
    try {
      await _api.saveAudiobookProgress(
        _audiobookSessionId,
        chapterId: item.chapterId,
        paragraphIndex: item.paraIdx,
        itemIndex: item.segmentIndex,
        audioOffsetMs: _offsetWithinCurrentItem(
          planIndex: planIndex,
          position: position,
        ).inMilliseconds,
      );
    } catch (_) {
      // Playback must not stall when progress persistence is temporarily offline.
    }
  }

  bool _rejectStreamReplay(Duration position) {
    if (_plan.isEmpty ||
        _chapterTransitionInProgress ||
        _replayRecoveryInProgress) {
      return false;
    }
    final previousMs = _lastStreamPosition.inMilliseconds;
    final currentMs = position.inMilliseconds;
    if (previousMs > 8000 && currentMs < max(1000, previousMs - 3000)) {
      unawaited(_recoverFromStreamReplay(_generation));
      return true;
    }
    if (currentMs >= previousMs || previousMs - currentMs < 1000) {
      _lastStreamPosition = Duration(milliseconds: max(previousMs, currentMs));
    }
    return false;
  }

  void _rememberTrustedPosition(int index, {Duration? position}) {
    if (_plan.isEmpty) return;
    _trustedIndex = index.clamp(0, _plan.length - 1).toInt();
    _trustedOffset = _offsetWithinCurrentItem(
      planIndex: _trustedIndex,
      position: position,
    );
  }

  int _playedStreamDurationMs(int index) {
    final resumeMs = index == _streamStartIndex
        ? _streamResumeOffset.inMilliseconds
        : 0;
    return max(
      50,
      (_plan[index].durationSeconds * 1000).round() - resumeMs,
    ).toInt();
  }

  int _resolvedStreamPlanIndex([Duration? position]) {
    if (_plan.isEmpty) return max(0, _currentIndex).toInt();
    var elapsedMs = 0;
    var candidate = _streamStartIndex.clamp(0, _plan.length - 1).toInt();
    final currentMs = (position ?? _player.position).inMilliseconds;
    final streamEnd = _plan.length;
    for (var index = candidate; index < streamEnd; index++) {
      candidate = index;
      if (!_plan[index].durationExact) break;
      final durationMs = _playedStreamDurationMs(index);
      if (currentMs < elapsedMs + durationMs) break;
      elapsedMs += durationMs;
    }
    return candidate;
  }

  Duration _offsetWithinCurrentItem({int? planIndex, Duration? position}) {
    if (_plan.isEmpty || _currentIndex < 0) return Duration.zero;
    final target = (planIndex ?? _resolvedStreamPlanIndex(position))
        .clamp(0, _plan.length - 1)
        .toInt();
    var elapsedMs = 0;
    for (var index = _streamStartIndex; index < target; index++) {
      elapsedMs += _playedStreamDurationMs(index);
    }
    final currentPosition = position ?? _player.position;
    final relativeMs = max(0, currentPosition.inMilliseconds - elapsedMs);
    final rawMs = target == _streamStartIndex
        ? _streamResumeOffset.inMilliseconds + relativeMs
        : relativeMs;
    final durationMs = (_plan[target].durationSeconds * 1000).round();
    final boundedMs = _plan[target].durationExact && durationMs > 100
        ? min(rawMs, durationMs - 50)
        : rawMs;
    return Duration(milliseconds: max(0, boundedMs).toInt());
  }

  Future<void> _startStreamAt(
    int index, {
    Duration offset = Duration.zero,
    required int generation,
    bool recoverOnFailure = true,
  }) async {
    if (!active || generation != _generation || _plan.isEmpty) return;
    final target = index.clamp(0, _plan.length - 1);
    _currentIndex = target;
    _streamStartIndex = target;
    _streamResumeOffset = offset;
    _lastStreamPosition = Duration.zero;
    _trustedIndex = target;
    _trustedOffset = offset;
    _timelineLoadedThrough = min(
      _timelineLoadedThrough < 0
          ? _plan[target].segmentIndex - 1
          : _timelineLoadedThrough,
      _plan[target].segmentIndex - 1,
    ).toInt();
    try {
      await _player.setAudioSource(
        _sourceFor(_plan[target], offset: offset),
        initialPosition: Duration.zero,
        preload: true,
      );
      if (!active || generation != _generation) return;
      _handleIndex(target);
      unawaited(_refreshTimeline());
      unawaited(_ensureFollowingChapterQueued());
      unawaited(
        _handler.play().catchError((_) => _recoverPlayback(generation)),
      );
    } catch (_) {
      if (!recoverOnFailure) rethrow;
      await _recoverPlayback(generation);
    }
  }

  Future<void> _completeCurrentChapter(int generation) async {
    if (!active || generation != _generation) return;
    if (_chapterTransitionInProgress) return;
    _chapterTransitionInProgress = true;
    if (_audiobookSessionId.isNotEmpty) {
      await _ensureFollowingChapterQueued();
    }
    try {
      if (!active || generation != _generation) return;
      final manifest = _nextManifest;
      if (manifest == null) {
        active = false;
        onComplete?.call();
        return;
      }
      final chapterId = '${manifest['chapter_id']}';
      await _api.activateAudiobookChapter(_audiobookSessionId, chapterId);
      if (!active || generation != _generation) return;
      _nextManifest = null;
      _loadManifestPlan(
        manifest,
        bookId: currentBookId ?? _buildingBookId,
        fallbackChapterId: chapterId,
        fallbackTitle: (manifest['title'] as String?) ?? '下一章',
      );
      _queuedChapterIds.add(chapterId);
      _chapterTransitionInProgress = false;
      await play(fromIndex: 0);
    } catch (_) {
      if (!active || generation != _generation) return;
      active = false;
      onComplete?.call();
    } finally {
      if (generation == _generation) {
        _chapterTransitionInProgress = false;
      }
    }
  }

  void _handleIndex(int index) {
    if (index < 0 || index >= _plan.length) return;
    _currentIndex = index;
    _recoveryAttempts = 0;
    final item = _plan[index];
    final chapterChanged = currentChapterId != item.chapterId;
    currentBookId = item.bookId;
    currentChapterId = item.chapterId;
    currentChapterTitle = item.chapterTitle;
    if (chapterChanged) {
      _handler.updateMetadata(
        title: item.chapterTitle.isEmpty
            ? (chapterTitle ?? '听书')
            : item.chapterTitle,
        album: bookTitle ?? '',
        artist: authorName ?? 'OOH Story',
        artUri: Uri.tryParse(coverArtUrl ?? ''),
      );
      onChapterChange?.call(item.chapterId, item.chapterTitle);
    } else if (index == 0) {
      _handler.updateMetadata(
        title: item.chapterTitle.isEmpty
            ? (chapterTitle ?? '听书')
            : item.chapterTitle,
        album: bookTitle ?? '',
        artist: authorName ?? 'OOH Story',
        artUri: Uri.tryParse(coverArtUrl ?? ''),
      );
    }
    onParagraphChange?.call(item.paraIdx);
    unawaited(_saveProgress(position: _player.position));
    if (_plan.length - index <= 20) {
      unawaited(_ensureFollowingChapterQueued());
    }
  }

  Future<void> _recoverPlayback(int generation) async {
    if (_recoveryInProgress || !active || generation != _generation) return;
    _recoveryInProgress = true;
    try {
      while (active && generation == _generation && _recoveryAttempts < 8) {
        _recoveryAttempts++;
        await Future<void>.delayed(
          Duration(
            milliseconds: (350 * (1 << (_recoveryAttempts - 1)))
                .clamp(350, 5000)
                .toInt(),
          ),
        );
        if (!active || generation != _generation) return;
        try {
          final position = _player.position;
          final resolved = _resolvedStreamPlanIndex(position);
          final target = max(
            resolved,
            _trustedIndex,
          ).clamp(0, _plan.length - 1).toInt();
          await _startStreamAt(
            target,
            offset: target == _trustedIndex
                ? _trustedOffset
                : _offsetWithinCurrentItem(
                    planIndex: target,
                    position: position,
                  ),
            generation: generation,
            recoverOnFailure: false,
          );
          return;
        } catch (_) {
          // Retry the same paragraph; never silently skip user content.
        }
      }
    } finally {
      _recoveryInProgress = false;
    }
  }

  Future<void> _recoverFromStreamReplay(int generation) async {
    if (_replayRecoveryInProgress ||
        !active ||
        generation != _generation ||
        _plan.isEmpty) {
      return;
    }
    _replayRecoveryInProgress = true;
    try {
      final target = max(
        _trustedIndex,
        _currentIndex,
      ).clamp(0, _plan.length - 1).toInt();
      await _startStreamAt(
        target,
        offset: target == _trustedIndex ? _trustedOffset : Duration.zero,
        generation: generation,
        recoverOnFailure: false,
      );
    } catch (_) {
      if (active && generation == _generation) {
        await _recoverPlayback(generation);
      }
    } finally {
      _replayRecoveryInProgress = false;
    }
  }

  void stop() {
    final sessionId = _audiobookSessionId;
    unawaited(_saveProgress(position: _player.position));
    _generation++;
    active = false;
    _recoveryInProgress = false;
    _replayRecoveryInProgress = false;
    _streamStartIndex = 0;
    _streamResumeOffset = Duration.zero;
    _lastStreamPosition = Duration.zero;
    _initialStreamOffset = Duration.zero;
    _trustedIndex = 0;
    _trustedOffset = Duration.zero;
    _audiobookManifestHash = '';
    _timelineLoadedThrough = -1;
    _lastTimelineRefreshMs = 0;
    _lastProgressSaveMs = 0;
    _timelineRequest = null;
    unawaited(_positionSub?.cancel());
    unawaited(_stateSub?.cancel());
    unawaited(_eventSub?.cancel());
    _positionSub = null;
    _nextManifest = null;
    _followingChapterRequest = null;
    _chapterTransitionInProgress = false;
    _queuedChapterIds.clear();
    _queueingFollowingChapter = false;
    _audiobookSessionId = '';
    unawaited(_audiobookCache.clearSession());
    if (sessionId.isNotEmpty) unawaited(_api.cancelAudiobookSession(sessionId));
    unawaited(_handler.stop());
  }

  void pause() => _handler.pause();
  void resume() {
    if (!active || _plan.isEmpty || _chapterTransitionInProgress) return;
    if (_player.processingState == ProcessingState.completed) {
      unawaited(_completeCurrentChapter(_generation));
      return;
    }
    if (_player.processingState == ProcessingState.idle) {
      final position = _player.position;
      final resolved = _resolvedStreamPlanIndex(position);
      final target = max(
        resolved,
        _trustedIndex,
      ).clamp(0, _plan.length - 1).toInt();
      unawaited(
        _startStreamAt(
          target,
          offset: target == _trustedIndex
              ? _trustedOffset
              : _offsetWithinCurrentItem(planIndex: target, position: position),
          generation: _generation,
        ),
      );
      return;
    }
    unawaited(_handler.play().catchError((_) => _recoverPlayback(_generation)));
  }

  void detachCallbacks() {
    onParagraphChange = null;
    onChapterChange = null;
    onComplete = null;
    onSkipPrev = null;
    onSkipNext = null;
  }

  void dispose() {
    detachCallbacks();
  }
}

class _TtsItem {
  final String streamEndpoint;
  final int segmentIndex;
  final int paraIdx;
  double durationSeconds;
  bool durationExact;
  final String bookId;
  final String chapterId;
  final String chapterTitle;

  _TtsItem({
    required this.streamEndpoint,
    required this.segmentIndex,
    required this.paraIdx,
    required this.durationSeconds,
    required this.durationExact,
    required this.bookId,
    required this.chapterId,
    required this.chapterTitle,
  });
}
