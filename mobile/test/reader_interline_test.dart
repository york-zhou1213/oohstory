import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('reader TTS separates chapter start from listen-from-here', () {
    final source = File('lib/screens/reader_screen.dart').readAsStringSync();
    final service = File('lib/services/tts_service.dart').readAsStringSync();

    expect(
      source,
      contains('final requestedStart = explicitStart ? startParagraph : 0'),
    );
    expect(
      source,
      isNot(
        contains('startParagraph ?? checkpoint ?? _currentVisibleParagraph()'),
      ),
    );
    expect(source, contains('allowServerResume: allowServerResume'));
    expect(source, isNot(contains('_tts.buildPlan(_ttsParagraphs')));
    expect(source, contains('_startTts(startParagraph: item.ttsIndex)'));
    expect(source, contains("'从此处听书'"));
    expect(service, contains('Future<void> buildAuthoritativePlan'));
    expect(service, contains('_api.audiobookContinuousStreamUri'));
    expect(service, isNot(contains('/api/v1/tts/speak')));
  });

  test('reader exposes paragraph comments, bubbles and cumulative likes', () {
    final reader = File('lib/screens/reader_screen.dart').readAsStringSync();
    final account = File(
      'lib/services/account_service.dart',
    ).readAsStringSync();

    expect(reader, contains('onLongPress: () => _showParagraphActions(item)'));
    expect(reader, contains('_ParagraphActionButton('));
    expect(reader, contains("'字里行间'"));
    expect(reader, contains(r"'🫧 $commentCount'"));
    expect(reader, contains("'无法评论'"));
    expect(reader, contains('showUserContentNoticeDialog'));
    expect(reader, contains('addParagraphCommentLike'));
    expect(reader, contains("comment['viewer_like_count']"));
    expect(reader, contains(r"'已点满 3/3 · $totalLikes'"));
    expect(reader, contains('Icons.favorite_border_rounded'));
    expect(reader, isNot(contains('Icons.thumb_up')));
    expect(reader, contains("author['avatar_url']"));
    expect(reader, contains('_account.avatarUrl(avatarUrl)'));
    expect(reader, contains('ReadingRankBadge('));
    expect(reader, contains(r"'$rankRoman · $rankName'"));
    expect(
      account,
      contains('/api/v1/books/\$bookId/chapters/\$chapterId/comments'),
    );
    expect(account, contains('/api/v1/paragraph-comments/\$commentId/likes'));
  });
}
