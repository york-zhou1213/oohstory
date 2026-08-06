class UserContentNotice {
  final String title;
  final String message;
  final String actionLabel;
  final bool promotion;

  const UserContentNotice({
    required this.title,
    required this.message,
    required this.actionLabel,
    this.promotion = false,
  });
}

class UserContentGuard {
  static const _tlds =
      r'(?:com|cn|net|org|xyz|top|vip|io|cc|me|app|site|club|live|shop|online|link|bet|casino)';

  static const _contacts = <String>[
    '微信',
    '薇信',
    '威信',
    '维信',
    'v信',
    'vx',
    'wx',
    'weixin',
    'qq',
    '扣扣',
    '电报',
    'telegram',
    '飞机群',
    'whatsapp',
    '二维码',
    '扫码',
    '群号',
    '加好友',
    '私聊我',
    '私信我',
    '联系我',
  ];
  static const _risks = <String>[
    '傻逼',
    '脑残',
    '智障',
    '色情',
    '成人视频',
    '裸聊',
    '约炮',
    '刷单',
    '返利',
    '跑分',
    '杀猪盘',
    '博彩',
    '赌博',
    '下注',
    '玩球',
    '买球',
    '赌场',
    '毒品',
    '冰毒',
    '海洛因',
    '可卡因',
  ];
  static const _promotions = <String>[
    '赚钱',
    '网赚',
    '副业',
    '兼职',
    '高薪',
    '日结',
    '推广',
    '引流',
    '招代理',
    '开户',
    '带单',
    '稳赚',
    '躺赚',
  ];
  static const _strongContacts = <String>[
    '二维码',
    '扫码',
    '群号',
    '加好友',
    '私聊我',
    '私信我',
    '联系我',
  ];

  static String _normalize(String input) {
    final output = StringBuffer();
    for (final rune in input.runes) {
      if ((rune >= 0x200b && rune <= 0x200f) ||
          (rune >= 0x202a && rune <= 0x202e) ||
          rune == 0x2060 ||
          rune == 0xfeff) {
        continue;
      }
      if (rune == 0x3000) {
        output.write(' ');
      } else if (rune >= 0xff01 && rune <= 0xff5e) {
        output.writeCharCode(rune - 0xfee0);
      } else {
        output.writeCharCode(rune);
      }
    }
    return output.toString().toLowerCase();
  }

  static String? issue(String value, {bool identity = false}) {
    final visible = _normalize(value);
    final compact = visible.replaceAll(RegExp(r'[^0-9a-z\u3400-\u9fff]+'), '');
    final dotted = visible.replaceAll(
      RegExp(
        r'(?:。|．|点|點|丶|句号|小数点|d\W*o\W*t|d\W*i\W*a\W*n)',
        caseSensitive: false,
      ),
      '.',
    );
    final domainReady = dotted.replaceAll(
      RegExp(r'''[\s_+\-—·•,，/\\|:：;；'"`~!！?？()（）\[\]{}<>《》]+'''),
      '',
    );
    final separated = RegExp(
      r'(?:[a-z0-9][\s_+\-·•.,，。．/\\|:：;；]+){5,}[a-z0-9]',
      caseSensitive: false,
    ).allMatches(dotted);
    final splitDomain = separated.any((match) {
      final collapsed = match
          .group(0)!
          .replaceAll(RegExp(r'[^a-z0-9]', caseSensitive: false), '');
      return RegExp(
        '[a-z0-9]{3,}$_tlds\$',
        caseSensitive: false,
      ).hasMatch(collapsed);
    });
    final hasLink =
        RegExp(
          r'(?:h\W*[t7]\W*[t7]\W*p|h\W*x\W*x\W*p|ftp)\W*s?\W*[:：]?\W*/?\W*/?',
          caseSensitive: false,
        ).hasMatch(visible) ||
        RegExp(
          r'w\W*w\W*w(?:\W|点|點)+',
          caseSensitive: false,
        ).hasMatch(visible) ||
        RegExp(
          '(?:^|[^a-z0-9])(?:[a-z0-9][a-z0-9-]{1,62}\\.)+$_tlds(?:\$|[^a-z0-9])',
          caseSensitive: false,
        ).hasMatch(domainReady) ||
        splitDomain;
    final hasPhone = RegExp(
      r'1[3-9](?:[\s_+\-·•()（）]*\d){9}',
    ).hasMatch(visible);
    final contactHandle =
        _contacts.any(compact.contains) &&
        RegExp(r'[a-z0-9]{4,}').hasMatch(compact);
    final blocked =
        hasLink ||
        hasPhone ||
        _strongContacts.any(compact.contains) ||
        contactHandle ||
        _risks.any(compact.contains) ||
        (identity && _contacts.any(compact.contains)) ||
        (identity && _promotions.any(compact.contains));
    if (!blocked) return null;
    if (identity) {
      return '这个昵称暂时无法使用。请去掉联系方式、广告引流或不合适的内容后，再试一个更纯粹的名字。';
    }
    if (hasLink ||
        hasPhone ||
        _strongContacts.any(compact.contains) ||
        contactHandle) {
      return '这条评论需要修改。评论里似乎包含网站、联系方式或推广内容，请删除相关内容后再发布，让「字里行间」只留下阅读交流。';
    }
    return '这条评论暂时不能发布。内容可能不符合社区交流规范，请调整措辞、去掉不合适的内容后再发布。';
  }

  static bool isModerationMessage(String value, {bool identity = false}) {
    if (identity) {
      return value.contains('这个昵称暂时无法使用') ||
          value.contains('昵称包含违规') ||
          value.contains('昵称包含广告引流') ||
          value.contains('昵称包含联系方式');
    }
    return value.contains('这条评论需要修改') ||
        value.contains('这条评论暂时不能发布') ||
        value.contains('评论包含链接') ||
        value.contains('评论包含联系方式') ||
        value.contains('评论包含违规') ||
        value.contains('评论包含辱骂') ||
        value.contains('评论包含涉黄') ||
        value.contains('评论包含涉毒') ||
        value.contains('评论包含涉诈') ||
        value.contains('评论包含博彩');
  }

  static UserContentNotice notice(String? issue, {bool identity = false}) {
    if (identity) {
      return const UserContentNotice(
        title: '换个昵称吧',
        message: '这个昵称暂时无法使用。请去掉联系方式、广告引流或不合适的内容后，再试一个更纯粹的名字。',
        actionLabel: '我来修改',
      );
    }
    final text = issue ?? '';
    final promotion =
        text.contains('需要修改') ||
        text.contains('网站') ||
        text.contains('链接') ||
        text.contains('联系方式') ||
        text.contains('推广') ||
        text.contains('引流');
    if (promotion) {
      return const UserContentNotice(
        title: '这条评论需要修改',
        message: '评论里似乎包含网站、联系方式或推广内容。请删除相关内容后再发布，让「字里行间」只留下阅读交流。',
        actionLabel: '返回修改',
        promotion: true,
      );
    }
    return const UserContentNotice(
      title: '这条评论暂时不能发布',
      message: '内容可能不符合社区交流规范。请调整措辞、去掉不合适的内容后再发布。',
      actionLabel: '返回修改',
    );
  }
}
