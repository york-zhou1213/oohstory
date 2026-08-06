import 'dart:async';
import 'package:just_audio/just_audio.dart';
import 'api_service.dart';
import 'tts_audio_handler.dart';
import 'tts_dialogue_parser.dart';

class EmotionResult {
  final String pitch;
  final int rateMod;
  EmotionResult(this.pitch, this.rateMod);
}

class TtsService {
  final ApiService _api;
  final TtsAudioHandler _handler;
  late final AudioPlayer _player;
  String voice = 'nuanxi';
  String narrator = 'mocheng';
  String mode = 'normal';
  double baseRate = 1.0;
  bool active = false;

  String? bookTitle;
  String? chapterTitle;
  String? authorName;

  List<_TtsItem> _plan = [];
  int _currentIndex = -1;
  int _generation = 0;
  ConcatenatingAudioSource? _playlist;
  StreamSubscription<int?>? _indexSub;
  StreamSubscription<PlayerState>? _stateSub;
  StreamSubscription<PlaybackEvent>? _eventSub;
  String _buildingBookId = '';
  String _buildingChapterId = '';
  String _buildingChapterTitle = '';
  final Set<String> _queuedChapterIds = {};
  List<String> _catalogChapterIds = const [];
  Map<String, String> _catalogChapterTitles = const {};
  bool _queueingFollowingChapter = false;
  String? currentBookId;
  String? currentChapterId;
  String? currentChapterTitle;
  int _recoveryAttempts = 0;
  bool _recoveryInProgress = false;
  void Function(int paraIdx)? onParagraphChange;
  void Function(String chapterId, String chapterTitle)? onChapterChange;
  void Function()? onComplete;
  void Function()? onSkipPrev;
  void Function()? onSkipNext;

  static const _femalePool = ['nuanxi', 'lingxian', 'shuanger', 'yanzhi'];
  static const _malePool = ['kuangyun', 'qingyan', 'tongzhen', 'mocheng'];
  static const _cantonesePool = ['wanqing', 'muyao', 'yueming'];
  static const _hokkienPool = ['qianyu', 'ruoxi', 'hanfeng'];

  TtsService(this._api, this._handler) {
    _player = _handler.player;
    _handler.onSkipPrev = () {
      if (_currentIndex > 0) {
        unawaited(_player.seek(Duration.zero, index: _currentIndex - 1));
      } else {
        onSkipPrev?.call();
      }
    };
    _handler.onSkipNext = () {
      if (_currentIndex >= 0 && _currentIndex < _plan.length - 1) {
        unawaited(_player.seek(Duration.zero, index: _currentIndex + 1));
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
    _catalogChapterIds = List<String>.from(chapterIds);
    _catalogChapterTitles = Map<String, String>.from(chapterTitles);
  }

  bool hasQueuedChapter(String chapterId) =>
      _queuedChapterIds.contains(chapterId);
  bool get isPlaying => active && _player.playing;

  static EmotionResult detectEmotion(String text) {
    e(String p, int r) => EmotionResult(p, r);
    if (RegExp(r'大喊|大叫|嘶吼|嘶喊|声嘶力竭|扯着嗓子|放声|吼叫|嚎叫|尖叫').hasMatch(text)) {
      return e('+8Hz', 15);
    }
    if (RegExp(r'吼|怒|骂|咆哮|暴怒|斥|喝道|呵斥|怒吼|怒骂|愤怒|愤然|混蛋|滚|去死|畜生').hasMatch(text) ||
        RegExp(r'[！!]{2,}').hasMatch(text)) {
      return e('+6Hz', 10);
    }
    if (RegExp(r'魂飞魄散|吓死|死定了|完了完了|救命|不要过来|求你|饶命').hasMatch(text)) {
      return e('+4Hz', 12);
    }
    if (RegExp(r'害怕|恐惧|颤抖|发抖|惊恐|吓|心惊|胆寒|毛骨悚然|惶恐|冷汗').hasMatch(text)) {
      return e('+3Hz', 8);
    }
    if (RegExp(r'哭|泪|悲伤|痛哭|呜咽|哽咽|心碎|难过|伤心|悲痛|含泪|流泪').hasMatch(text)) {
      return e('-5Hz', -18);
    }
    if (RegExp(r'哈哈|笑|开心|高兴|兴奋|激动|太好了|好极了|真棒|哇|厉害|爽').hasMatch(text) &&
        !RegExp(r'冷笑|苦笑|惨笑|嘲笑').hasMatch(text)) {
      return e('+4Hz', 10);
    }
    if (RegExp(r'嫉妒|眼红|羡慕|凭什么|不公平').hasMatch(text)) return e('+2Hz', 5);
    if (RegExp(r'冷笑|嗤|鄙夷|不屑|嘲讽|撇嘴|哼|轻蔑|看不起|呸|废物|垃圾').hasMatch(text)) {
      return e('+3Hz', 5);
    }
    if (RegExp(r'冷冷|冰冷|寒声|阴沉|冷淡|疏离|漠然|无情|绝情|懒得|不稀罕').hasMatch(text)) {
      return e('-2Hz', -5);
    }
    if (RegExp(r'尴尬|羞|窘|脸红|不好意思|难为情|讨厌|人家|哎呀').hasMatch(text)) {
      return e('+2Hz', 5);
    }
    if (RegExp(r'叹息|无奈|沮丧|失落|叹气|苦笑|算了|罢了|认命|没办法|唉|哎').hasMatch(text)) {
      return e('-4Hz', -15);
    }
    if (RegExp(r'抱歉|对不起|惭愧|内疚|自责|懊悔|都怪我|后悔').hasMatch(text)) {
      return e('-3Hz', -10);
    }
    if (RegExp(r'理解|懂你|辛苦了|不容易|感同身受|别难过|会好的|有我在').hasMatch(text)) {
      return e('-2Hz', -8);
    }
    if (RegExp(r'轻声|低语|悄悄|耳语|喃喃|小声|压低.*声|嘘|安静').hasMatch(text)) {
      return e('-5Hz', -20);
    }
    if (RegExp(r'温柔|亲切|和蔼|慈祥|柔和|暖意|关切|心疼|宠溺').hasMatch(text)) {
      return e('-2Hz', -10);
    }
    if (RegExp(r'严肃|认真|郑重|凝重|严厉|不准|禁止|住手|够了|闭嘴|命令|必须').hasMatch(text)) {
      return e('+1Hz', -5);
    }
    if (RegExp(r'平静|冷静|从容|淡定|镇定|无所谓|随便|都行|没关系').hasMatch(text)) {
      return e('-1Hz', -8);
    }
    if (RegExp(r'亲爱|宝贝|甜蜜|深情|想念|思念|爱你|喜欢你|舍不得|别离开').hasMatch(text)) {
      return e('-2Hz', -10);
    }
    if (RegExp(r'希望|期待|终于.*了|有希望|有救|来得及|一定能|相信').hasMatch(text)) {
      return e('+3Hz', 5);
    }
    if (RegExp(r'惊讶|诧异|震惊|难以置信|怎么可能|竟然|不会吧|你说什么').hasMatch(text)) {
      return e('+6Hz', 15);
    }
    if (RegExp(r'催促|快点|赶紧|来不及|快跑|快走|别磨蹭').hasMatch(text)) return e('+4Hz', 18);
    if (RegExp(r'得意|嘚瑟|傲|自信|胸有成竹|笃定').hasMatch(text)) return e('+3Hz', -5);
    if (RegExp(r'疲惫|疲倦|累|没力气|精疲力竭|有气无力|虚弱').hasMatch(text)) {
      return e('-3Hz', -15);
    }
    if (RegExp(r'紧张|忐忑|心跳加速|屏住呼吸|绷|攥').hasMatch(text)) return e('+2Hz', 8);
    if (RegExp(r'[！!]').hasMatch(text)) return e('+2Hz', 5);
    if (RegExp(r'[？?]').hasMatch(text)) return e('+3Hz', 3);
    return e('+0Hz', 0);
  }

  static const _femaleChars =
      '芳兰梅莲莉花玉凤雪月云霞丽美婷娟娜燕清静秀琴琳瑶薇颖慧蝶蓉萍雯珊妮媛莹冰虹蕊珍柔漪婉姝妍茹菲灵纤黛绮韵语欣悠萱瑾璇思依怡晴彤馨曦嫣儿蕾珂雅岚姗琪瑗瑜璐蓓苒珺琬蓁嫚姿妤婕瑄彩翠巧素贞惠淑媚荷苓茉蔓葵棠樱桃杏梨';
  static const _maleChars =
      '强伟刚军明龙虎飞志勇杰磊鹏波超涛辰翔宇峰浩亮华昊天锋剑武威壮坤鸿博轩逸远哲铭泽阳诚毅恒煜旭霆骏凯斌彬松柏森权桐栋梁钧钢锐钦铮磐石岩崇琛卓晟烨熠焱';

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

  static final _verbRe = RegExp(r'([一-鿿]{2,6})[说道喊叫嚷吼骂笑哼嗤叹问答斥喝呵]+[道说]?');

  static final _nonNameWords = {
    '不过',
    '但是',
    '然而',
    '可是',
    '只是',
    '于是',
    '因此',
    '所以',
    '如果',
    '虽然',
    '忽然',
    '突然',
    '接着',
    '随后',
    '此时',
    '这时',
    '那时',
    '一时',
    '顿时',
    '刹那',
    '其实',
    '果然',
    '毕竟',
    '几乎',
    '似乎',
    '仿佛',
    '大概',
    '或许',
    '也许',
    '同时',
    '他们',
    '她们',
    '我们',
    '你们',
    '大家',
    '众人',
    '所有',
    '一切',
    '这些',
    '那些',
    '说道',
    '笑道',
    '喊道',
    '叫道',
    '吼道',
    '骂道',
    '问道',
    '答道',
    '叹道',
    '嚷道',
    '低声',
    '高声',
    '冷笑',
    '苦笑',
    '大声',
    '轻声',
    '转头',
    '回头',
    '抬头',
    '低头',
    '看到',
    '听到',
    '想到',
    '见到',
    '看见',
    '不禁',
    '只见',
    '赫然',
    '猛然',
    '忽而',
    '对方',
    '那人',
    '这人',
    '此人',
    '旁边',
    '身边',
    '前方',
    '后方',
    '眼前',
    '身后',
    '已经',
    '正在',
    '依然',
    '仍然',
    '居然',
    '竟然',
    '终于',
    '马上',
    '立刻',
    '当即',
    '开始',
    '继续',
    '停止',
    '结束',
    '完成',
    '准备',
    '发现',
    '感觉',
    '知道',
    '明白',
    '心想',
    '暗想',
    '心道',
    '暗道',
    '自言',
    '自语',
    '不由',
    '不免',
    '不觉',
    '摇头',
    '点头',
    '皱眉',
    '挑眉',
    '眯眼',
    '瞪眼',
    '张嘴',
    '闭嘴',
    '咬牙',
  };

  static Map<String, String> _buildSpeakerMap(
    List<String> paragraphs,
    String narratorVoice,
  ) {
    final nameCounts = <String, int>{};
    for (final line in paragraphs) {
      if (!TtsDialogueParser.isDialogueLine(line)) continue;
      final narration = TtsDialogueParser.narrationOnly(line);
      for (final match in _verbRe.allMatches(narration)) {
        final raw = match.group(1)!;
        final c2 = raw.substring(0, 2);
        if (!_nonNameWords.contains(c2)) {
          nameCounts[c2] = (nameCounts[c2] ?? 0) + 1;
        }
        if (raw.length >= 3) {
          final c3 = raw.substring(0, 3);
          if (!_nonNameWords.contains(c3) &&
              !_nonNameWords.contains(c3.substring(0, 2))) {
            nameCounts[c3] = (nameCounts[c3] ?? 0) + 1;
          }
        }
      }
    }
    final confirmed = <String>{};
    for (final entry in nameCounts.entries) {
      if (entry.value >= 2) {
        confirmed.add(entry.key);
      } else if (_guessGender(entry.key) != null) {
        confirmed.add(entry.key);
      }
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
        map[name] = (uIdx % 2 == 0
            ? mPool
            : fPool)[(uIdx ~/ 2) % (uIdx % 2 == 0 ? mPool : fPool).length];
        uIdx++;
      }
    }
    return map;
  }

  static String? _findSpeaker(String line, Map<String, String> speakerMap) {
    final narration = TtsDialogueParser.narrationOnly(line);
    final names = speakerMap.keys.toList()
      ..sort((a, b) => b.length.compareTo(a.length));
    for (final name in names) {
      if (narration.contains(name)) return name;
    }
    return null;
  }

  Uri _buildItemUrl(String text, String voiceKey) {
    final cleaned = text
        .replaceAll(RegExp(r'[——]+'), '，')
        .replaceAll(RegExp(r'[“”"「」『』【】\[\]［］]'), '');
    final emotion = detectEmotion(cleaned);
    final baseRatePct = ((baseRate - 1) * 100).round();
    final finalRatePct = baseRatePct + emotion.rateMod;
    final rate = finalRatePct >= 0 ? '+$finalRatePct%' : '$finalRatePct%';
    return _api.ttsUrl(cleaned, voiceKey, rate: rate, pitch: emotion.pitch);
  }

  void buildPlan(List<String> paragraphs, {int startParagraph = 0}) {
    if (mode == 'smart') {
      _buildSmartPlan(paragraphs, startParagraph);
    } else {
      _buildSimplePlan(paragraphs, startParagraph);
    }
    _queuedChapterIds
      ..clear()
      ..addAll(
        _plan.map((item) => item.chapterId).where((id) => id.isNotEmpty),
      );
  }

  AudioSource _sourceFor(_TtsItem item) => AudioSource.uri(item.url);

  Future<int> appendChapter({
    required String bookId,
    required String chapterId,
    required String title,
    required List<String> paragraphs,
  }) async {
    if (chapterId.isEmpty || _queuedChapterIds.contains(chapterId)) return 0;
    final previousPlan = List<_TtsItem>.from(_plan);
    final previousBookId = _buildingBookId;
    final previousChapterId = _buildingChapterId;
    final previousChapterTitle = _buildingChapterTitle;
    _buildingBookId = bookId;
    _buildingChapterId = chapterId;
    _buildingChapterTitle = title;
    if (mode == 'smart') {
      _buildSmartPlan(paragraphs, 0);
    } else {
      _buildSimplePlan(paragraphs, 0);
    }
    final appended = List<_TtsItem>.from(_plan);
    _plan = [...previousPlan, ...appended];
    _buildingBookId = previousBookId;
    _buildingChapterId = previousChapterId;
    _buildingChapterTitle = previousChapterTitle;
    if (appended.isEmpty) return 0;
    _queuedChapterIds.add(chapterId);
    final playlist = _playlist;
    if (playlist != null && active) {
      await playlist.addAll(appended.map(_sourceFor).toList());
    }
    return appended.length;
  }

  Future<void> _ensureFollowingChapterQueued() async {
    if (_queueingFollowingChapter || !active || _plan.isEmpty) return;
    final lastChapterId = _plan.last.chapterId;
    final position = _catalogChapterIds.indexOf(lastChapterId);
    if (position < 0 || position >= _catalogChapterIds.length - 1) return;
    final nextId = _catalogChapterIds[position + 1];
    if (_queuedChapterIds.contains(nextId)) return;
    _queueingFollowingChapter = true;
    try {
      final chapter = await _api.getChapter(
        currentBookId ?? _plan.last.bookId,
        nextId,
      );
      if (!active) return;
      final paragraphs = (chapter.content ?? '')
          .split(RegExp(r'\n+'))
          .where(
            (line) =>
                line.trim().isNotEmpty &&
                !RegExp(r'^\[illustration:.+\]$').hasMatch(line.trim()),
          )
          .toList();
      await appendChapter(
        bookId: currentBookId ?? _plan.last.bookId,
        chapterId: nextId,
        title: chapter.displayTitle.isNotEmpty
            ? chapter.displayTitle
            : (_catalogChapterTitles[nextId] ?? '下一章'),
        paragraphs: paragraphs,
      );
    } catch (_) {
      // Retry near the chapter boundary; current playback remains untouched.
    } finally {
      _queueingFollowingChapter = false;
    }
  }

  static const int _requestCharacterLimit = 900;

  static List<String> _splitForRequest(String text) {
    var remaining = text.trim();
    final chunks = <String>[];
    while (remaining.length > _requestCharacterLimit) {
      final window = remaining.substring(0, _requestCharacterLimit);
      var cut = <String>['。', '！', '？', '；', '，', ',', '!', '?']
          .map(window.lastIndexOf)
          .fold<int>(-1, (best, value) => value > best ? value : best);
      if (cut >= 0) cut += 1;
      if (cut < (_requestCharacterLimit * .42).floor()) {
        cut = <String>[' ', '　']
            .map(window.lastIndexOf)
            .fold<int>(-1, (best, value) => value > best ? value : best);
      }
      if (cut < (_requestCharacterLimit * .42).floor()) {
        cut = _requestCharacterLimit;
      }
      final chunk = remaining.substring(0, cut).trim();
      if (chunk.isNotEmpty) chunks.add(chunk);
      remaining = remaining.substring(cut).trim();
    }
    if (remaining.isNotEmpty) chunks.add(remaining);
    return chunks;
  }

  void _appendPlan(String text, String voiceKey, int paragraphIndex) {
    for (final chunk in _splitForRequest(text)) {
      _plan.add(
        _TtsItem(
          url: _buildItemUrl(chunk, voiceKey),
          paraIdx: paragraphIndex,
          bookId: _buildingBookId,
          chapterId: _buildingChapterId,
          chapterTitle: _buildingChapterTitle,
        ),
      );
    }
  }

  void _buildSimplePlan(List<String> paragraphs, int startParagraph) {
    _plan = [];
    final v = (mode == 'cantonese')
        ? _cantonesePool.first
        : (mode == 'hokkien')
        ? _hokkienPool.first
        : voice;
    for (int i = startParagraph; i < paragraphs.length; i++) {
      final line = paragraphs[i].trim();
      if (line.isEmpty) continue;
      _appendPlan(line, v, i);
    }
  }

  void _buildSmartPlan(List<String> paragraphs, int startParagraph) {
    _plan = [];
    final narratorVoice = narrator;
    final speakerMap = _buildSpeakerMap(paragraphs, narratorVoice);
    String? lastSpeakerVoice;
    final dialoguePool = [
      ..._femalePool,
      ..._malePool,
    ].where((v) => v != narratorVoice).toList();
    int unknownIdx = 0;

    for (int i = startParagraph; i < paragraphs.length; i++) {
      final line = paragraphs[i].trim();
      if (line.isEmpty) continue;
      final isDialogue = TtsDialogueParser.isDialogueLine(line);
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
        _appendPlan(line, narratorVoice, i);
        continue;
      }

      final segments = TtsDialogueParser.splitLine(line);
      if (segments.any((segment) => segment.isDialogue)) {
        for (final segment in segments) {
          _appendPlan(
            segment.text,
            segment.isDialogue ? speakerVoice : narratorVoice,
            i,
          );
        }
      } else {
        _appendPlan(line, speakerVoice, i);
      }
    }
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
    await _indexSub?.cancel();
    await _stateSub?.cancel();
    await _eventSub?.cancel();
    final playlist = ConcatenatingAudioSource(
      useLazyPreparation: true,
      children: _plan.map(_sourceFor).toList(),
    );
    _playlist = playlist;
    _indexSub = _player.currentIndexStream.listen((index) {
      if (!active || generation != _generation || index == null) return;
      _handleIndex(index);
    });
    _stateSub = _player.playerStateStream.listen((state) {
      if (!active || generation != _generation) return;
      if (state.processingState == ProcessingState.completed) {
        active = false;
        onComplete?.call();
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
    try {
      await _player.setAudioSource(
        playlist,
        initialIndex: _currentIndex,
        initialPosition: Duration.zero,
        preload: true,
      );
      if (!active || generation != _generation) return;
      _handleIndex(_player.currentIndex ?? _currentIndex);
      unawaited(_ensureFollowingChapterQueued());
      unawaited(
        _handler.play().catchError((_) => _recoverPlayback(generation)),
      );
    } catch (_) {
      await _recoverPlayback(generation);
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
      );
      onChapterChange?.call(item.chapterId, item.chapterTitle);
    } else if (index == 0) {
      _handler.updateMetadata(
        title: item.chapterTitle.isEmpty
            ? (chapterTitle ?? '听书')
            : item.chapterTitle,
        album: bookTitle ?? '',
        artist: authorName ?? 'OOH Story',
      );
    }
    onParagraphChange?.call(item.paraIdx);
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
          await _player.seek(
            Duration.zero,
            index: _currentIndex.clamp(0, _plan.length - 1),
          );
          unawaited(_handler.play().catchError((_) {}));
          return;
        } catch (_) {
          // Retry the same paragraph; never silently skip user content.
        }
      }
    } finally {
      _recoveryInProgress = false;
    }
  }

  void stop() {
    _generation++;
    active = false;
    _recoveryInProgress = false;
    unawaited(_indexSub?.cancel());
    unawaited(_stateSub?.cancel());
    unawaited(_eventSub?.cancel());
    _playlist = null;
    _queuedChapterIds.clear();
    _queueingFollowingChapter = false;
    unawaited(_handler.stop());
  }

  void pause() => _handler.pause();
  void resume() {
    if (!active || _plan.isEmpty) return;
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
  final Uri url;
  final int paraIdx;
  final String bookId;
  final String chapterId;
  final String chapterTitle;

  _TtsItem({
    required this.url,
    required this.paraIdx,
    required this.bookId,
    required this.chapterId,
    required this.chapterTitle,
  });
}
