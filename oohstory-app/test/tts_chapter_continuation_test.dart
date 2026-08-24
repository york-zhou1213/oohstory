import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('reader keeps one TTS session while the next chapter loads', () {
    final source = File('lib/screens/reader_screen.dart').readAsStringSync();

    expect(source, contains('bool _ttsContinueOnLoad = false;'));
    expect(source, contains('_changeChapter(1, continueTts: true);'));
    expect(source, contains('final shouldContinueTts = _ttsContinueOnLoad;'));
    expect(source, contains('unawaited(_startTts(startParagraph: 0));'));
    expect(source, contains('_tts.onChapterChange = (chapterId, _)'));
    expect(source, contains('_tts.currentChapterId == loadChapterId'));
    expect(source, contains('_tts.chapterTitle = _chapter?.displayTitle;'));
  });

  test(
    'TTS uses continuous chapter streams and activates following chapters',
    () {
      final source = File('lib/services/tts_service.dart').readAsStringSync();
      final subscription = source.indexOf(
        '_stateSub = _player.playerStateStream.listen',
      );
      final play = source.indexOf('_handler.play()', subscription);

      expect(subscription, greaterThanOrEqualTo(0));
      expect(play, greaterThan(subscription));
      expect(source, contains('_api.audiobookContinuousStreamUri'));
      expect(source, contains('static const int _streamBatchSize = 5;'));
      expect(source, contains('final streamEnd = _plan.length;'));
      expect(source, contains('if (!_plan[index].durationExact) break;'));
      expect(source, isNot(contains('nextBatchIndex')));
      expect(source, contains('_completeCurrentChapter'));
      expect(source, contains('_api.activateAudiobookChapter'));
      expect(source, contains('_ensureFollowingChapterQueued'));
      expect(source, contains('_prefetchAuthoritativeNext'));
      expect(source, contains('_streamStartIndex = target;'));
      expect(source, contains('_resolvedStreamPlanIndex(position)'));
      expect(source, contains('_offsetWithinCurrentItem('));
      expect(source, contains('allowServerResume = true'));
      expect(source, contains('resume: allowServerResume'));
      expect(source, contains("payload['resume']"));
      expect(source, contains('_initialStreamOffset'));
      expect(source, contains('_api.saveAudiobookProgress'));
      expect(source, contains('itemIndex: item.segmentIndex'));
      expect(source, contains('_saveProgress(position: _player.position)'));
      expect(source, contains('fetchAudiobookTimeline'));
      expect(source, contains('limit: _streamBatchSize'));
      expect(source, contains('_sourceFor(_plan[target], offset: offset)'));
      expect(source, contains('recoverOnFailure: false'));
      expect(
        source,
        contains('_player.processingState == ProcessingState.idle'),
      );
      expect(source, isNot(contains('await _player.seek(')));
      expect(
        source,
        contains(
          'if (!active || _plan.isEmpty || _chapterTransitionInProgress) return;',
        ),
      );
      expect(
        source,
        contains('state.processingState == ProcessingState.completed &&'),
      );
      expect(source, isNot(contains('ConcatenatingAudioSource(')));
      expect(source, isNot(contains('appendChapter(')));
      expect(source, isNot(contains('await _player.setUrl(')));
    },
  );

  test(
    'reader main listen control does not resume an old audiobook session',
    () {
      final source = File('lib/screens/reader_screen.dart').readAsStringSync();
      final start = source.indexOf('  void _toggleTts() {');
      final end = source.indexOf('\n  Future<Map<String, dynamic>>', start);
      final toggle = source.substring(start, end);

      expect(toggle, isNot(contains('_tts.resume();')));
      expect(toggle, contains('unawaited(_startTts(startParagraph: 0));'));
    },
  );

  test(
    'reader retries TTS text cursor scrolling until the paragraph is attached',
    () {
      final source = File('lib/screens/reader_screen.dart').readAsStringSync();

      expect(source, contains('int? _pendingTtsScrollParagraph;'));
      expect(source, contains('void _scheduleTtsScrollRetry()'));
      expect(source, contains('WidgetsBinding.instance.addPostFrameCallback'));
      expect(source, contains('!_scrollCtrl.isAttached'));
      expect(source, contains('_ttsScrollRetryAttempts >= 8'));
      expect(source, contains('_pendingTtsScrollParagraph = idx;'));
    },
  );

  test('reader normal listen starts at chapter opening', () {
    final source = File('lib/screens/reader_screen.dart').readAsStringSync();
    final start = source.indexOf('  Future<void> _startTts');
    final end = source.indexOf('\n  void _toggleTts()', start);
    final startTts = source.substring(start, end);

    expect(startTts, contains('final explicitStart = startParagraph != null;'));
    expect(startTts, contains('final allowServerResume = false;'));
    expect(
      startTts,
      contains('final requestedStart = explicitStart ? startParagraph : 0'),
    );
    expect(startTts, isNot(contains('_currentVisibleParagraph()')));
    expect(startTts, isNot(contains('_loadTtsCheckpoint()')));
    expect(startTts, contains('allowServerResume: allowServerResume'));
  });

  test(
    'reader sends explicit listen-from-here start to authoritative session',
    () {
      final api = File('lib/services/api_service.dart').readAsStringSync();
      final tts = File('lib/services/tts_service.dart').readAsStringSync();

      expect(api, contains('int startParagraphIndex = 0'));
      expect(
        api,
        contains("'start_paragraph_index': max(0, startParagraphIndex)"),
      );
      expect(
        tts,
        contains('final requestedStartParagraph = max(0, startParagraph);'),
      );
      expect(tts, contains('startParagraphIndex: requestedStartParagraph'));
      expect(tts, contains("final start = payload['start'] is Map"));
      expect(
        tts,
        contains('startSegmentIndex: resumeItemIndex ?? startItemIndex'),
      );
    },
  );

  test('background media metadata uses the current book cover', () {
    final reader = File('lib/screens/reader_screen.dart').readAsStringSync();
    final api = File('lib/services/api_service.dart').readAsStringSync();
    final service = File('lib/services/tts_service.dart').readAsStringSync();
    final handler = File(
      'lib/services/tts_audio_handler.dart',
    ).readAsStringSync();

    expect(
      reader,
      contains('_tts.coverArtUrl = _api.mediaCoverArtUrl(widget.bookId);'),
    );
    expect(api, contains('mediaCoverArtUrl(String bookId)'));
    expect(service, contains('String? coverArtUrl;'));
    expect(service, contains("artUri: Uri.tryParse(coverArtUrl ?? '')"));
    expect(handler, contains('Uri? _artUri;'));
    expect(handler, contains('artUri: _artUri'));
  });
}
