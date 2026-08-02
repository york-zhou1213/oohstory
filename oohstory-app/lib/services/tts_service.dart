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
  double baseRate = 1.0;
  bool active = false;

  List<_TtsItem> _plan = [];
  int _currentIndex = -1;
  StreamSubscription<PlayerState>? _stateSub;
  void Function(int paraIdx)? onParagraphChange;
  void Function()? onComplete;

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

  void buildPlan(List<String> paragraphs) {
    _plan = [];
    for (int i = 0; i < paragraphs.length; i++) {
      final line = paragraphs[i].trim();
      if (line.isEmpty) continue;
      final cleaned = line.replaceAll(RegExp(r'[——]+'), '，').replaceAll(RegExp(r'[【】\[\]]'), '');
      final emotion = detectEmotion(cleaned);
      final baseRatePct = ((baseRate - 1) * 100).round();
      final finalRatePct = baseRatePct + emotion.rateMod;
      final rate = finalRatePct >= 0 ? '+$finalRatePct%' : '$finalRatePct%';
      final url = _api.ttsUrl(cleaned, voice, rate: rate, pitch: emotion.pitch);
      _plan.add(_TtsItem(url: url, paraIdx: i));
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
