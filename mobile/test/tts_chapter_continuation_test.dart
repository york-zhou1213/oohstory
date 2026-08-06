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

  test('TTS uses a gapless playlist and appends following chapters', () {
    final source = File('lib/services/tts_service.dart').readAsStringSync();
    final subscription = source.indexOf(
      '_stateSub = _player.playerStateStream.listen',
    );
    final play = source.indexOf('_handler.play()', subscription);

    expect(subscription, greaterThanOrEqualTo(0));
    expect(play, greaterThan(subscription));
    expect(source, contains('ConcatenatingAudioSource('));
    expect(source, contains('appendChapter('));
    expect(source, contains('_ensureFollowingChapterQueued'));
    expect(source, isNot(contains('await _player.setUrl(')));
  });
}
