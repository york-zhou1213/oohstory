import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test(
    'Flutter audiobook uses POST manifests, volatile audio, and continuous streams',
    () {
      final api = File('lib/services/api_service.dart').readAsStringSync();
      final cache = File(
        'lib/services/audiobook_cache.dart',
      ).readAsStringSync();
      final tts = File('lib/services/tts_service.dart').readAsStringSync();

      expect(api, contains('/api/v1/audiobook/sessions'));
      expect(api, contains('createAudiobookSession'));
      expect(api, contains("'resume': resume"));
      expect(
        api,
        contains("'start_paragraph_index': max(0, startParagraphIndex)"),
      );
      expect(api, contains('saveAudiobookProgress'));
      expect(api, contains(r'/api/v1/audiobook/sessions/$sessionId/progress'));
      expect(api, contains("'item_index': max(0, itemIndex)"));
      expect(api, contains("'audio_offset_ms': max(0, audioOffsetMs)"));
      expect(api, contains('fetchAudiobookSegment'));
      expect(api, contains('audiobookContinuousStreamUri'));
      expect(api, contains('fetchAudiobookTimeline'));
      expect(api, contains("'continuous': '1'"));
      expect(api, contains("'full_chapter': '1'"));
      expect(api, contains('activateAudiobookChapter'));
      expect(cache, contains('getApplicationSupportDirectory'));
      expect(cache, contains('sessionSegmentLimit = 5'));
      expect(cache, contains('clearSession'));
      expect(cache, contains('audiobook-cache-v1'));
      expect(cache, isNot(contains(".mp3")));
      expect(cache, isNot(contains(".json")));
      expect(cache, isNot(contains(r"File('${root.path}/")));
      expect(cache, isNot(contains('sha256.convert(response.bodyBytes)')));
      expect(cache, isNot(contains("'complete': true")));
      expect(cache, contains('evictLru'));
      expect(cache, contains('ConnectivityResult.wifi'));
      expect(tts, contains('buildAuthoritativePlan'));
      expect(tts, contains('allowServerResume'));
      expect(tts, contains("_api.saveAudiobookProgress"));
      expect(tts, contains("payload['resume']"));
      expect(tts, contains("payload['start']"));
      expect(tts, contains('_initialStreamOffset'));
      expect(tts, contains('_api.audiobookContinuousStreamUri'));
      expect(tts, contains('_api.activateAudiobookChapter'));
      expect(tts, contains('_audiobookCache.clearSession()'));
      expect(tts, contains('_api.cancelAudiobookSession(sessionId)'));
    },
  );

  test('logout stops active audiobook before revoking account session', () {
    final profile = File('lib/screens/profile_screen.dart').readAsStringSync();
    final logoutStart = profile.indexOf(
      'onPressed: () async {',
      profile.indexOf('Widget _buildLogoutButton'),
    );
    final source = profile.substring(logoutStart, logoutStart + 180);
    expect(
      source.indexOf('ttsService.stop()'),
      lessThan(source.indexOf('await _account.logout()')),
    );
  });
}
