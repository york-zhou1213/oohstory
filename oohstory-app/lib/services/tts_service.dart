import 'dart:async';
import 'package:just_audio/just_audio.dart';
import 'api_service.dart';

class EmotionResult {
  final String pitch;
  final int rateMod;
  EmotionResult(this.pitch, this.rateMod);
}

class TtsService {
  final ApiService _api;
  final AudioPlayer _player = AudioPlayer();
  String voice = 'nuanxi';
  String narrator = 'mocheng';
  String mode = 'normal';
  double baseRate = 1.0;
  bool active = false;

  List<_TtsItem> _plan = [];
  int _currentIndex = -1;
  StreamSubscription<PlayerState>? _stateSub;
  void Function(int paraIdx)? onParagraphChange;
  void Function()? onComplete;

  static const _femalePool = ['nuanxi', 'lingxian', 'shuanger', 'yanzhi'];
  static const _malePool = ['kuangyun', 'qingyan', 'tongzhen', 'mocheng'];
  static const _cantonesePool = ['wanqing', 'muyao', 'yueming'];
  static const _hokkienPool = ['qianyu', 'ruoxi', 'hanfeng'];

  TtsService(this._api);

  static EmotionResult detectEmotion(String text) {
    e(String p, int r) => EmotionResult(p, r);
    if (RegExp(r'大喊|大叫|嘶吼|嘶喊|声嘶力竭|扯着嗓子|放声|吼叫|嚎叫|尖叫').hasMatch(text)) return e('+8Hz', 15);
    if (RegExp(r'吼|怒|骂|咆哮|暴怒|斥|喝道|呵斥|怒吼|怒骂|愤怒|愤然|混蛋|滚|去死|畜生').hasMatch(text) || RegExp(r'[！!]{2,}').hasMatch(text)) return e('+6Hz', 10);
    if (RegExp(r'魂飞魄散|吓死|死定了|完了完了|救命|不要过来|求你|饶命').hasMatch(text)) return e('+4Hz', 12);
    if (RegExp(r'害怕|恐惧|颤抖|发抖|惊恐|吓|心惊|胆寒|毛骨悚然|惶恐|冷汗').hasMatch(text)) return e('+3Hz', 8);
    if (RegExp(r'哭|泪|悲伤|痛哭|呜咽|哽咽|心碎|难过|伤心|悲痛|含泪|流泪').hasMatch(text)) return e('-5Hz', -18);
    if (RegExp(r'哈哈|笑|开心|高兴|兴奋|激动|太好了|好极了|真棒|哇|厉害|爽').hasMatch(text) && !RegExp(r'冷笑|苦笑|惨笑|嘲笑').hasMatch(text)) return e('+4Hz', 10);
    if (RegExp(r'嫉妒|眼红|羡慕|凭什么|不公平').hasMatch(text)) return e('+2Hz', 5);
    if (RegExp(r'冷笑|嗤|鄙夷|不屑|嘲讽|撇嘴|哼|轻蔑|看不起|呸|废物|垃圾').hasMatch(text)) return e('+3Hz', 5);
    if (RegExp(r'冷冷|冰冷|寒声|阴沉|冷淡|疏离|漠然|无情|绝情|懒得|不稀罕').hasMatch(text)) return e('-2Hz', -5);
    if (RegExp(r'尴尬|羞|窘|脸红|不好意思|难为情|讨厌|人家|哎呀').hasMatch(text)) return e('+2Hz', 5);
    if (RegExp(r'叹息|无奈|沮丧|失落|叹气|苦笑|算了|罢了|认命|没办法|唉|哎').hasMatch(text)) return e('-4Hz', -15);
    if (RegExp(r'抱歉|对不起|惭愧|内疚|自责|懊悔|都怪我|后悔').hasMatch(text)) return e('-3Hz', -10);
    if (RegExp(r'理解|懂你|辛苦了|不容易|感同身受|别难过|会好的|有我在').hasMatch(text)) return e('-2Hz', -8);
    if (RegExp(r'轻声|低语|悄悄|耳语|喃喃|小声|压低.*声|嘘|安静').hasMatch(text)) return e('-5Hz', -20);
    if (RegExp(r'温柔|亲切|和蔼|慈祥|柔和|暖意|关切|心疼|宠溺').hasMatch(text)) return e('-2Hz', -10);
    if (RegExp(r'严肃|认真|郑重|凝重|严厉|不准|禁止|住手|够了|闭嘴|命令|必须').hasMatch(text)) return e('+1Hz', -5);
    if (RegExp(r'平静|冷静|从容|淡定|镇定|无所谓|随便|都行|没关系').hasMatch(text)) return e('-1Hz', -8);
    if (RegExp(r'亲爱|宝贝|甜蜜|深情|想念|思念|爱你|喜欢你|舍不得|别离开').hasMatch(text)) return e('-2Hz', -10);
    if (RegExp(r'希望|期待|终于.*了|有希望|有救|来得及|一定能|相信').hasMatch(text)) return e('+3Hz', 5);
    if (RegExp(r'惊讶|诧异|震惊|难以置信|怎么可能|竟然|不会吧|你说什么').hasMatch(text)) return e('+6Hz', 15);
    if (RegExp(r'催促|快点|赶紧|来不及|快跑|快走|别磨蹭').hasMatch(text)) return e('+4Hz', 18);
    if (RegExp(r'得意|嘚瑟|傲|自信|胸有成竹|笃定').hasMatch(text)) return e('+3Hz', -5);
    if (RegExp(r'疲惫|疲倦|累|没力气|精疲力竭|有气无力|虚弱').hasMatch(text)) return e('-3Hz', -15);
    if (RegExp(r'紧张|忐忑|心跳加速|屏住呼吸|绷|攥').hasMatch(text)) return e('+2Hz', 8);
    if (RegExp(r'[！!]').hasMatch(text)) return e('+2Hz', 5);
    if (RegExp(r'[？?]').hasMatch(text)) return e('+3Hz', 3);
    return e('+0Hz', 0);
  }

  static const _femaleChars = '芳兰梅莲莉花玉凤雪月云霞丽美婷娟娜燕清静秀琴琳瑶薇颖慧蝶蓉萍雯珊妮媛莹冰虹蕊珍柔漪婉姝妍茹菲灵纤黛绮韵语欣悠萱瑾璇思依怡晴彤馨曦嫣儿蕾珂雅岚姗琪瑗瑜璐蓓苒珺琬蓁嫚姿妤婕瑄彩翠巧素贞惠淑媚荷苓茉蔓葵棠樱桃杏梨';
  static const _maleChars = '强伟刚军明龙虎飞志勇杰磊鹏波超涛辰翔宇峰浩亮华昊天锋剑武威壮坤鸿博轩逸远哲铭泽阳诚毅恒煜旭霆骏凯斌彬松柏森权桐栋梁钧钢锐钦铮磐石岩崇琛卓晟烨熠焱';

  static String? _guessGender(String name) {
    int f = 0, m = 0;
    for (final ch in name.split('')) {
      if (_femaleChars.contains(ch)) f++;
      if (_maleChars.contains(ch)) m++;
    }
    if (f > m) return 'female';
    if (m > f) return 'male';
    return null;
  }

  static bool _isDialogueLine(String line) {
    if (RegExp(r'"[^"]+"').hasMatch(line)) return true;
    if (RegExp(r'【[^】]+】').hasMatch(line)) return true;
    final colonIdx = line.indexOf('：');
    if (colonIdx > 0 && colonIdx <= 15) {
      final after = line.substring(colonIdx + 1).trim();
      if (after.length >= 4) return true;
    }
    return false;
  }

  static final _quoteRe = RegExp(r'"[^"]*"|【[^】]*】');
  static final _verbRe = RegExp(r'([一-鿿]{2,6})[说道喊叫嚷吼骂笑哼嗤叹问答斥喝呵]+[道说]?');

  static final _nonNameWords = {
    '不过', '但是', '然而', '可是', '只是', '于是', '因此', '所以', '如果', '虽然',
    '忽然', '突然', '接着', '随后', '此时', '这时', '那时', '一时', '顿时', '刹那',
    '其实', '果然', '毕竟', '几乎', '似乎', '仿佛', '大概', '或许', '也许', '同时',
    '他们', '她们', '我们', '你们', '大家', '众人', '所有', '一切', '这些', '那些',
    '说道', '笑道', '喊道', '叫道', '吼道', '骂道', '问道', '答道', '叹道', '嚷道',
    '低声', '高声', '冷笑', '苦笑', '大声', '轻声', '转头', '回头', '抬头', '低头',
    '看到', '听到', '想到', '见到', '看见', '不禁', '只见', '赫然', '猛然', '忽而',
    '对方', '那人', '这人', '此人', '旁边', '身边', '前方', '后方', '眼前', '身后',
    '已经', '正在', '依然', '仍然', '居然', '竟然', '终于', '马上', '立刻', '当即',
    '开始', '继续', '停止', '结束', '完成', '准备', '发现', '感觉', '知道', '明白',
    '心想', '暗想', '心道', '暗道', '自言', '自语', '不由', '不免', '不觉',
    '摇头', '点头', '皱眉', '挑眉', '眯眼', '瞪眼', '张嘴', '闭嘴', '咬牙',
  };

  static Map<String, String> _buildSpeakerMap(List<String> paragraphs, String narratorVoice) {
    final nameCounts = <String, int>{};
    for (final line in paragraphs) {
      if (!_isDialogueLine(line)) continue;
      final narration = line.replaceAll(_quoteRe, '');
      for (final match in _verbRe.allMatches(narration)) {
        final raw = match.group(1)!;
        final c2 = raw.substring(0, 2);
        if (!_nonNameWords.contains(c2)) nameCounts[c2] = (nameCounts[c2] ?? 0) + 1;
        if (raw.length >= 3) {
          final c3 = raw.substring(0, 3);
          if (!_nonNameWords.contains(c3) && !_nonNameWords.contains(c3.substring(0, 2))) {
            nameCounts[c3] = (nameCounts[c3] ?? 0) + 1;
          }
        }
      }
    }
    final confirmed = <String>{};
    for (final entry in nameCounts.entries) {
      if (entry.value >= 2) confirmed.add(entry.key);
      else if (_guessGender(entry.key) != null) confirmed.add(entry.key);
    }
    final fPool = _femalePool.where((v) => v != narratorVoice).toList();
    final mPool = _malePool.where((v) => v != narratorVoice).toList();
    final map = <String, String>{};
    int fIdx = 0, mIdx = 0, uIdx = 0;
    for (final name in confirmed) {
      final gender = _guessGender(name);
      if (gender == 'female') {
        map[name] = fPool[fIdx++ % fPool.length];
      } else if (gender == 'male') {
        map[name] = mPool[mIdx++ % mPool.length];
      } else {
        map[name] = (uIdx % 2 == 0 ? mPool : fPool)[(uIdx ~/ 2) % (uIdx % 2 == 0 ? mPool : fPool).length];
        uIdx++;
      }
    }
    return map;
  }

  static String? _findSpeaker(String line, Map<String, String> speakerMap) {
    final narration = line.replaceAll(_quoteRe, '');
    final names = speakerMap.keys.toList()..sort((a, b) => b.length.compareTo(a.length));
    for (final name in names) {
      if (narration.contains(name)) return name;
    }
    return null;
  }

  Uri _buildItemUrl(String text, String voiceKey) {
    final cleaned = text.replaceAll(RegExp(r'[——]+'), '，').replaceAll(RegExp(r'[【】\[\]"""]'), '');
    final emotion = detectEmotion(cleaned);
    final baseRatePct = ((baseRate - 1) * 100).round();
    final finalRatePct = baseRatePct + emotion.rateMod;
    final rate = finalRatePct >= 0 ? '+$finalRatePct%' : '$finalRatePct%';
    return _api.ttsUrl(cleaned, voiceKey, rate: rate, pitch: emotion.pitch);
  }

  void buildPlan(List<String> paragraphs) {
    if (mode == 'smart') {
      _buildSmartPlan(paragraphs);
    } else {
      _buildSimplePlan(paragraphs);
    }
  }

  void _buildSimplePlan(List<String> paragraphs) {
    _plan = [];
    final v = (mode == 'cantonese') ? _cantonesePool.first
        : (mode == 'hokkien') ? _hokkienPool.first
        : voice;
    for (int i = 0; i < paragraphs.length; i++) {
      final line = paragraphs[i].trim();
      if (line.isEmpty) continue;
      _plan.add(_TtsItem(url: _buildItemUrl(line, v), paraIdx: i));
    }
  }

  void _buildSmartPlan(List<String> paragraphs) {
    _plan = [];
    final narratorVoice = narrator;
    final speakerMap = _buildSpeakerMap(paragraphs, narratorVoice);
    String? lastSpeakerVoice;
    final dialoguePool = [..._femalePool, ..._malePool].where((v) => v != narratorVoice).toList();
    int unknownIdx = 0;

    for (int i = 0; i < paragraphs.length; i++) {
      final line = paragraphs[i].trim();
      if (line.isEmpty) continue;
      final isDialogue = _isDialogueLine(line);
      String speakerVoice = narratorVoice;

      if (isDialogue) {
        final speaker = _findSpeaker(line, speakerMap);
        if (speaker != null && speakerMap.containsKey(speaker)) {
          speakerVoice = speakerMap[speaker]!;
        } else if (lastSpeakerVoice != null) {
          speakerVoice = lastSpeakerVoice;
        } else {
          speakerVoice = dialoguePool[unknownIdx++ % dialoguePool.length];
        }
        lastSpeakerVoice = speakerVoice;
      }

      if (!isDialogue) {
        _plan.add(_TtsItem(url: _buildItemUrl(line, narratorVoice), paraIdx: i));
        continue;
      }

      final quoteMatches = _quoteRe.allMatches(line).toList();
      if (quoteMatches.isNotEmpty) {
        int last = 0;
        for (final match in quoteMatches) {
          if (match.start > last) {
            final before = line.substring(last, match.start).trim();
            if (before.isNotEmpty) _plan.add(_TtsItem(url: _buildItemUrl(before, narratorVoice), paraIdx: i));
          }
          final inside = match.group(0)!.replaceAll(RegExp(r'[""【】\[\]]'), '').trim();
          if (inside.isNotEmpty) _plan.add(_TtsItem(url: _buildItemUrl(inside, speakerVoice), paraIdx: i));
          last = match.end;
        }
        if (last < line.length) {
          final after = line.substring(last).trim();
          if (after.isNotEmpty) _plan.add(_TtsItem(url: _buildItemUrl(after, narratorVoice), paraIdx: i));
        }
      } else {
        _plan.add(_TtsItem(url: _buildItemUrl(line, speakerVoice), paraIdx: i));
      }
    }
  }

  Future<void> play({int fromIndex = 0}) async {
    active = true;
    _currentIndex = fromIndex;
    await _playAt(_currentIndex);
  }

  Future<void> _playAt(int index) async {
    if (!active || index >= _plan.length) {
      active = false;
      onComplete?.call();
      return;
    }
    _currentIndex = index;
    final item = _plan[index];
    onParagraphChange?.call(item.paraIdx);
    try {
      _stateSub?.cancel();
      await _player.setUrl(item.url.toString());
      await _player.play();
      _stateSub = _player.playerStateStream.listen((state) {
        if (state.processingState == ProcessingState.completed && active) {
          _stateSub?.cancel();
          _playAt(index + 1);
        }
      });
    } catch (_) {
      if (active) _playAt(index + 1);
    }
  }

  void stop() {
    active = false;
    _player.stop();
  }

  void pause() => _player.pause();
  void resume() => _player.play();

  void dispose() {
    active = false;
    _stateSub?.cancel();
    _player.dispose();
  }
}

class _TtsItem {
  final Uri url;
  final int paraIdx;
  _TtsItem({required this.url, required this.paraIdx});
}
