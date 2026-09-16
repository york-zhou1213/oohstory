import 'package:flutter_test/flutter_test.dart';
import 'package:oohstory/utils/user_content_guard.dart';

void main() {
  test('content guard catches usernames and split website evasions', () {
    final identity = UserContentGuard.issue('玩球+我vXxXx赚钱', identity: true);
    expect(identity, contains('这个昵称暂时无法使用'));
    final promotion = UserContentGuard.issue('请看 e x a m p l e c o m');
    expect(promotion, contains('这条评论需要修改'));
    expect(UserContentGuard.issue('备用网址 e.x.a.m.p.l.e 点 c o m'), isNotNull);
    expect(UserContentGuard.issue('联系薇信 abc12345'), isNotNull);
    final community = UserContentGuard.issue('出售冰 毒');
    expect(community, contains('这条评论暂时不能发布'));
    expect(UserContentGuard.issue('这一段的人物动机写得很真实。'), isNull);
    expect(UserContentGuard.issue('这里提到微信读书和纸质书的差异。'), isNull);
    expect(UserContentGuard.issue('长风万里_07', identity: true), isNull);
    expect(UserContentGuard.notice(identity, identity: true).title, '换个昵称吧');
    expect(UserContentGuard.notice(promotion).actionLabel, '返回修改');
    expect(UserContentGuard.notice(promotion).promotion, isTrue);
    expect(UserContentGuard.notice(community).promotion, isFalse);
  });
}
