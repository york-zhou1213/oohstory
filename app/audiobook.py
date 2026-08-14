from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import threading
import time
import unicodedata
from typing import Any, Awaitable, Callable
from uuid import uuid4

from .audiobook_cast import CastPrewarmManager, PROTAGONIST_NAME
from .gender_guess import (
    GenderGuessCache,
    GenderGuessClient,
    is_clean_chinese_name,
    lookup_gender_cache_only,
)
from .library import LibraryRepository, NotFoundError, _decode_public_id
from .audiobook_policy import (
    AudiobookContractError,
    CAST_ENGINE_VERSION,
    CAST_VOICES,
    FEMALE_VOICES,
    MALE_VOICES,
    POLICY_VERSION,
    validate_manifest_contract,
    validate_manifest_payload,
    validate_voice_selection,
)


ENGINE_VERSION = CAST_ENGINE_VERSION
MANIFEST_PIPELINE_VERSION = f"verified-cast-state-machine-v2-preload-contract:{POLICY_VERSION}"
QUOTE_RE = re.compile(
    r"“(?P<double>[^”\n]+)”|"
    r"‘(?P<single>[^’\n]+)’|"
    r"「(?P<corner>[^」\n]+)」|"
    r"『(?P<book>[^』\n]+)』|"
    r"【(?P<cjk_square>[^】\n]+)】|"
    r"\[(?P<square>[^\]\n]+)\]|"
    r'"(?P<ascii_double>[^"\n]+)"'
)
QUOTE_GROUPS = (
    "double", "single", "corner", "book", "cjk_square", "square", "ascii_double"
)
SPOKEN_CONTENT_RE = re.compile(r"[\u3400-\u9fffA-Za-z0-9]")
COLON_RE = re.compile(r"^\s*(?P<name>[\u3400-\u9fff·A-Za-z0-9_]{1,20})[：:]\s*(?P<speech>.+)$")
STRUCTURED_COLON_FIELD_ENDINGS = (
    "利润", "回报率", "收益率", "利率", "税率", "占比", "比例", "份额",
    "分红", "金额", "总额", "余额", "价格", "单价", "总价", "市值",
    "资本", "资产", "负债", "成本", "收入", "支出", "预算", "数量",
    "总数", "血量", "生命值", "法力值", "魔力值", "体力值", "攻击力",
    "防御力", "速度", "等级", "经验值", "完成度", "进度", "年龄",
    "身高", "体重", "温度", "时间", "日期", "坐标", "编号", "排名",
    "状态", "属性", "姓名", "职业", "阵营", "境界", "修为", "修为值",
    "战力", "评分", "委托资本", "担保金",
)
STRUCTURED_COLON_ORGANIZATION_ENDINGS = (
    "公司", "银行", "基金", "机构", "部门", "协会", "集团", "账户",
    "投资方", "委托方", "担保方",
)
STRUCTURED_COLON_VALUE_RE = re.compile(
    r"^\s*(?:[+\-−]?\s*)?(?:[$¥￥€£]|人民币|美元|欧元|马克)?\s*"
    r"(?:\d|[零〇一二两三四五六七八九十百千万亿])"
)
SPEECH_VERBS = (
    "咬牙切齿道", "解释道", "说明道", "补充道", "回应道", "提醒道",
    "反驳道", "询问道", "追问道", "吩咐道", "命令道", "警告道",
    "嘱咐道", "承诺道", "保证道", "坦言道", "分析道", "总结道",
    "安慰道", "抱怨道", "嘟囔道", "开口道", "回答道", "应声道",
    "说道", "问道", "喊道", "叫道", "答道", "叹道", "喝道", "吼道",
    "应道", "回道", "接道", "续道", "劝道", "笑道", "禀道",
    "嘀咕道", "嘀咕", "质问", "反问", "追问", "询问", "回答",
    "回应", "解释", "提醒", "反驳", "吩咐", "命令", "警告", "安慰",
    "抱怨",
)
SPEECH_VERB_PATTERN = "(?:" + "|".join(
    map(re.escape, sorted(SPEECH_VERBS, key=len, reverse=True))
) + ")"
EXPLICIT_SPEECH_VERB_PATTERN = "(?:" + "|".join(
    map(
        re.escape,
        sorted(
            (*SPEECH_VERBS, "喊", "叫", "答", "问", "说", "叹", "喝", "吼", "劝"),
            key=len,
            reverse=True,
        ),
    )
) + ")"
SPEAKER_RE = re.compile(
    r"(?:^|[。！？!?，,\s])"
    r"(?P<name>我|她|他|(?!你|这|那|其)[\u3400-\u9fff·]{1,8}?)"
    r"(?:一边.{0,12}?|咬牙切齿地|斜着眼|斜眼|轻声|低声|高声|冷冷地|笑着|愤怒地|大声|"
    r"不屑地|不解地|严肃地|认真地|狐疑地|惊恐地|温柔地|淡淡地|"
    r"沉声|得意地|神秘地|疲惫地|兴奋地|疑惑地|狡黠地|宽慰地|"
    r"面无表情地|饶有兴趣地|有气无力地|缓缓)?"
    + SPEECH_VERB_PATTERN + r"[：:,，,\s]*$"
)
LEADING_ATTRIBUTION_RE = re.compile(
    r"^(?P<name>[\u3400-\u9fff·]{1,8}?)(?:一边|咬牙切齿地|轻声|低声|大声|"
    r"笑着|哭着|冷冷地|愤怒地|对|向|又).{0,40}?"
    + SPEECH_VERB_PATTERN + r"[。.!！]?$"
)
OPENED_SPEECH_RE = re.compile(
    r"(?:^|[，,。])(?:[^，,。]{0,24}?的)?(?P<name>[\u3400-\u9fff·]{1,8}?)"
    r"(?:又|再次|突然|缓缓)?(?:开口(?:说道)?|出声(?:说道)?)了?[。.!！]?$"
)
TRAILING_SPEECH_ATTRIBUTION_RE = re.compile(
    r"^\s*(?P<name>我|她|他|[\u3400-\u9fff·]{1,8}?)"
    r"(?:轻声|低声|冷冷地|笑着|愤怒地|大声|沉声|温柔地|淡淡地)?"
    + SPEECH_VERB_PATTERN
)
STATE_ATTRIBUTION_TERMS = (
    "大惑不解", "百思不解", "困惑不解", "一头雾水", "满腹狐疑",
    "尴尬", "茫然", "不解", "疑惑", "纳闷", "困惑", "惊讶", "诧异",
    "震惊", "无奈", "犹豫", "迟疑", "紧张", "慌张", "恼火", "愤怒",
    "不屑", "狐疑", "好奇", "窘迫", "羞恼",
)
STATE_COLON_ACTOR_RE = re.compile(
    r"(?:^|[。！？!?，,；;\s])"
    r"(?P<name>我|她|他|(?!你|这|那|其)[\u3400-\u9fff·]{1,8}?)"
    r"(?:愈发|越发|更加|仍旧|仍然|显得|一脸|满脸|有些|十分|很是)?"
    r"(?:" + "|".join(
        map(re.escape, sorted(STATE_ATTRIBUTION_TERMS, key=len, reverse=True))
    ) + r")[：:]\s*$"
)
DESCRIPTIVE_STATE_COLON_ACTOR_RE = re.compile(
    r"(?:^|[。！？!?，,；;\s])"
    r"(?P<name>我|她|他|(?!你|这|那|其)[\u3400-\u9fff·]{1,8}?)"
    r"(?:面色|脸色|神色|表情|双颊|脸颊|耳根|眼眶|声音|嗓音|语气)"
    r"[^。！？!?；;：:\n]{0,18}[：:]\s*$"
)
CHANNEL_COLON_RE = re.compile(
    r"^\s*[【\[](?:地图|队伍|私聊|世界|帮派|公会|当前|喇叭)[】\]]\s*"
    r"(?P<name>[\u3400-\u9fff·A-Za-z0-9_]{1,20})[：:]\s*(?P<speech>.+)$",
    re.MULTILINE,
)
UI_LABEL_RE = re.compile(
    r"^\s*[【\[](?:地图|队伍|私聊|世界|帮派|系统|公会|当前|喇叭)[】\]]\s*"
)
DIRECT_SPEECH_FRAME_RE = re.compile(
    r"(?:^|[。！？!?；;，,\s])"
    r"(?:我|她|他|[\u3400-\u9fff·]{1,8})"
    r"(?:轻声|低声|高声|大声|沉声|冷声|柔声|笑着|哭着|咬牙|又|"
    r"突然|忽然|缓缓|慢慢)?"
    r"(?:说|问|答|喊|叫|吼|嚷|嘟囔|低语|耳语|回应|回答|反问|"
    r"追问|提醒|命令|警告|央求|劝|宣布|开口)"
    r"(?:着|了|道)?(?:一句话?|一声|几句|两句|这句|那句|起来)?"
    r"[：:,，\s]*$"
)
REFERENCE_QUOTE_PREFIX_RE = re.compile(
    r"(?:"
    r"所谓(?:的)?|号称|俗称|昵称|别称|名为|名叫|名字(?:是|叫|为)|"
    r"命名为|取名为|称为|称作|叫作|叫做|被称为|称之为|人称|"
    r"代号(?:为|是|叫)?|简称(?:为|是)?|全称(?:为|是)?|"
    r"标题(?:为|是|叫)?|题为|书名(?:为|是|叫)?|篇名(?:为|是)?|"
    r"标签(?:为|是|叫)?|标注(?:为|是|着)?|标记(?:为|是|着)?|"
    r"写道|写着|写有|写下|写成|印着|印有|刻着|刻有|贴着|显示(?:为|着)?|"
    r"标成|译为|翻译为|记载(?:着|为)?|报道(?:着|为)?|"
    r"术语|词语|单词|字样|字眼|口号|暗号|密码|代码|编号|数字|"
    r"符号|关键词|概念|定义为|释义为|原文|引文|引用|引述|摘录|"
    r"据[^。！？!?；;\n]{0,24}(?:记载|所言)|"
    r"正如[^。！？!?；;\n]{0,24}(?:所言|所写)|"
    r"(?:这|那|某|哪|一)句(?:话)?|原话|名言|格言|诗句|歌词|台词|句子|"
    r"文中|书中|诗中|信中|纸上|屏幕上|牌子上|标牌上|"
    r"按钮|选项|栏目|状态|提示(?:为|是)?|"
    r"口中的|眼里的|心中的|想象中的|大家说的|常说的|"
    r"例如|比如|譬如|一般来说"
    r")[^。！？!?；;\n]{0,16}$"
)
REFERENCE_QUOTE_SUFFIX_RE = re.compile(
    r"^\s*(?:"
    r"的|地|得|式|型|版|号|类|感|化|一词|这个词|这一词|二字|两字|"
    r"三字|四字|(?:一|两|几|三|四)个字|这个字|"
    r"(?:这|那|某|哪)句话|这一称呼|这个称呼|"
    r"这一叫法|这个叫法|之名|"
    r"字样|标签|标记|按钮|选项|栏目|代码|编号|数字|符号|术语|概念|"
    r"标题|书名|篇名|版本|状态|角色|人物|气味|味道|说法|称呼"
    r")"
)
QUOTED_TOKEN_RE = re.compile(
    r"^(?:[\u3400-\u9fff]{1,8}|[A-Za-z][A-Za-z0-9_.+/#-]{0,15}|"
    r"[+-]?\d+(?:\.\d+)?%?|[A-Za-z0-9_.+/#-]{1,12})$"
)
FEMALE_HINT = re.compile(
    r"(?:她|少女|女孩|女生|姑娘|小姐|夫人|母亲|姐姐?|妹妹?|女王|公主|"
    r"奶奶?|姑姑?|姨妈?|姨|妈妈?|婶婶?|嫂嫂?)"
)
MALE_HINT = re.compile(
    r"(?:他|少年|男孩|男生|男人|先生|父亲|哥哥?|弟弟?|国王|王子|"
    r"爷爷?|舅舅?|伯父|伯|叔叔?|爸爸?)"
)
CHILD_HINT = re.compile(r"(?:孩子|小孩|幼女|幼童|男孩|女孩|童声)")
SENIOR_HINT = re.compile(r"(?:老人|老者|老妪|爷爷|奶奶|苍老)")
YOUNG_HINT = re.compile(r"(?:少年|少女|青年|年轻|哥哥|姐姐)")
NON_NAMES = {
    "她", "他", "他们", "她们", "突然", "忽然", "然后", "接着", "众人", "有人",
    "对方", "自己", "大家", "这里", "那里", "此时", "这时", "那人", "声音",
    "责任编辑", "封面设计", "定价", "篇外篇", "新版后记", "第一版后记",
    "开始", "整天", "只好", "疲惫地", "再插一句",
    "车子", "大叫", "惊疑", "时起", "终于", "高声", "低声", "轻声",
    "大声", "沉声", "柔声", "冷声", "怒声", "扬声", "厉声", "朗声",
    *STATE_ATTRIBUTION_TERMS,
    "冷漠", "淡然", "平静", "严肃", "认真", "兴奋", "开心", "高兴",
    "难过", "悲伤", "害怕", "恐惧", "焦急", "着急", "疲惫", "倦怠",
    "沉默", "苦涩", "苦笑", "干笑", "讪笑", "安抚", "徐徐", "齐声",
    "嗓子", "声询", "淡哼", "眼哼", "成功",
    "解释", "直接", "准备", "回头", "转头", "偏头", "边走",
    "眼睛", "泪水", "多年", "外面", "松开", "果然", "最终",
    "什么", "小声", "轻轻", "微微", "缓缓", "狠狠", "哈哈",
    "宽慰", "调侃", "含混", "径直", "放声", "开玩", "照完相",
    "男孩", "女孩", "男生", "女生", "男子", "女人", "男人",
    "女士", "先生", "老板", "摊主", "司机", "老头", "白痴",
    "叔叔", "婢女", "班主任", "管家",
}
NON_NAMES.update({
    "席间", "干脆", "连连", "车子调", "马上联", "马经", "曲终",
    "并且", "并且终于", "是真", "没有一丝", "低头翻", "只是一个",
    "坚持", "看着他冷", "四肢和", "们欢歌", "说远", "对这些", "声明",
    "见他不", "剩下三位", "只是迈步", "跟着一张", "并没", "默了默",
})
NON_NAME_PARTS = (
    "一般", "甚至", "所以", "虽然", "就是", "与其", "作品", "名字",
    "语言", "题材", "网络", "粉丝", "共通", "也就", "对我", "成为",
)
NON_NAME_ENDINGS = (
    "的", "了", "来", "说", "诉", "为", "用", "先", "虽", "该", "其",
    "我", "你", "他", "她", "是", "有", "听", "地", "上", "下", "中", "后", "前", "边", "里", "种",
    "着", "过", "压", "往", "揭穿", "指着", "大叫",
)
NON_NAME_PREFIXES = (
    "我", "你", "他", "她", "它", "这", "那", "其", "不", "也", "但", "可",
    "如果", "于是", "然后", "后来", "此时", "接着", "说着", "说完", "边说",
    "从", "用", "就", "记得", "开始", "整天", "只好", "疲惫", "以往",
    "被", "再次", "继续", "听到", "见状", "这时", "紧接着", "闻言", "随即",
    "龇牙",
    "第一", "第二", "第三", "第四", "最后",
)
NON_NAME_PREFIXES += ("车子", "马上")
NON_NAME_GRAMMAR_RE = re.compile(
    r"^(?:"
    r"一(?:下子|个人|副|句|只|屁股|张|想|手|把|抹|旁|杯|点点|直|脸)|"
    r"两(?:个人|个男人|只手|只眼睛|只胳膊)|双(?:手|臂)|"
    r"伸手|冲(?:上去|我们|我|着)|立刻|突然|再次|又|便|却|一直|"
    r"发出|电话|忍不住|赶紧|想要|看到|对方|而我|只是自己|"
    r"上前|上来|端起|拿起|举起|放下|抬起|收起|递出|推开|转身|"
    r"就像|仍旧|依旧|原本|两个人|"
    r"拍桌|拍案|敲桌|抚掌|跺脚|斜眼|瞪眼"
    r")"
)

PRONOUN_GENDERS = {"她": "female", "他": "male"}
STRONG_GENDER_SOURCES = frozenset({
    "explicit-pronoun", "explicit-identity", "first_person", "role_suffix",
})
EXTERNAL_GENDER_ATTRIBUTION_SOURCES = frozenset({
    "colon", "speech-verb-before", "speech-verb-after",
    "speech-subject-before", "expressive-action-before",
    "named-action-before",
    "known-quote-before", "known-action-before", "known-action-after",
    "known-action-between", "action-before", "action-after", "action-between",
    "state-before", "response-source-before",
})
FEMALE_ROLE_SUFFIXES = (
    "姐姐", "姐", "妹妹", "妹", "奶奶", "奶", "姑姑", "姑",
    "姨妈", "姨", "妈妈", "妈", "婶婶", "婶", "嫂嫂", "嫂",
    "道姑", "尼姑", "姑娘", "夫人", "太太", "丫鬟", "婢女", "侍女",
    "女官", "女郎", "少女", "女子", "妇人", "老妪",
)
MALE_ROLE_SUFFIXES = (
    "哥哥", "哥", "弟弟", "弟", "爷爷", "爷", "舅舅", "舅",
    "伯父", "伯", "叔叔", "叔", "爸爸", "爸",
    "门童", "小厮", "书童", "和尚", "僧人", "公公", "太监",
    "公子", "少爷", "老爷", "男孩", "男子", "少年",
)
GENERIC_ROLE_ALIASES = {
    "小姐", "先生", "姐姐", "妹妹", "哥哥", "弟弟", "爷爷", "奶奶",
    "姑姑", "舅舅", "伯父", "姨妈", "妈妈", "爸爸", "叔叔", "婶婶",
    "嫂嫂", "护士", "医生", "孩子", "男人", "女人",
    "姐", "妹", "哥", "弟", "爷", "奶", "姑", "舅", "伯", "姨",
    "妈", "爸", "叔", "婶", "嫂", "文书",
}
ALWAYS_POLLUTED_NAME_SUFFIXES = (
    "再次", "继续", "这才", "立刻", "开口", "想要", "看到", "起身",
    "赶紧", "不敢", "轻声", "低声", "正在", "便", "却", "又", "就",
    "并未", "才", "忙",
)
KNOWN_ACTOR_ACTION_SUFFIXES = frozenset({
    "又", "才", "忙", "苦", "举", "眯", "气", "淡", "好", "轻",
    "半", "噙", "收", "皮", "讪", "摆", "嗤", "对", "闷", "抿",
    "反", "漫", "直", "假", "憋", "点", "冷", "玩", "震", "略",
    "自", "向", "不", "替", "重", "猛", "坏", "捋须", "微微",
    "改口", "稳重", "羞涩", "玩笑", "慈蔼", "执礼",
})
ALWAYS_POLLUTED_NAME_PREFIXES = (
    "唤来", "叫来", "喊来", "请来", "带来", "领来", "找来", "召来",
    "压着", "温声", "冷眼", "冷淡",
)
SUSPICIOUS_ACTOR_NAMES = frozenset({
    "关键字", "关键词", "开怀大", "勾唇", "后排", "毕竟", "都可以",
    "平常人", "满脸堆", "宁愿", "车窗",
})
PREFIX_ACTION_TAILS = frozenset({
    "也", "又", "才", "忙", "便", "却", "在", "没", "把", "将", "只",
    "继", "回", "端", "摊", "垂", "拿", "摆", "越", "浅", "似", "倾",
    "心", "伸", "大", "一", "临", "收", "偏", "满", "翘", "仰", "拎",
    "竖", "双", "点", "苦", "指", "噎", "噗",
})
ACTION_FRAGMENT_SUFFIXES = frozenset({
    "嘲", "反", "想", "一", "也", "又", "才", "忙", "便", "却",
    "打", "吃", "早", "则", "怒", "眼", "微", "联", "经", "调",
    "笑", "哭", "问", "答", "喊", "叫", "说", "看", "找", "将",
    "小声", "低声", "高声", "沉声", "轻声", "大声", "冷声", "怒声", "柔声",
})
PREFIX_COLLISION_SUFFIXES = (
    "冷", "轻", "怒", "笑", "哭", "问", "答", "喊", "叫", "说",
)
FEMALE_NAME_CHARS = set("娜妍媛姝芳婷娟莲雪薇秀英慧巧美静淑惠玲芬燕彩兰凤洁梅琳璃霞香月莺艳佳嘉欣怡倩妍梦瑶诗琪")
MALE_NAME_CHARS = set("刚军勇杰强峰龙虎鹏伟毅俊磊超斌涛宇浩轩明健志辉海山波胜武飞彬顺信豪博凯阳")

ACTION_WORDS = (
    "拽住", "拉住", "抓住", "握住", "挽住", "按住", "松开",
    "拍了拍", "挥了挥", "摆了摆", "点了点头", "摇了摇头",
    "挥手", "摆手", "点头", "摇头", "抬手", "抬头", "低头",
    "拍案", "拍桌", "摸了摸", "抚摸", "皱眉", "挑眉", "瞪",
    "好笑", "轻笑", "浅笑", "嗤笑", "讪笑", "苦笑", "冷笑", "大笑",
    "笑笑", "笑", "哭", "眯起", "眯", "举起", "垂眼", "垂眸",
    "推", "扶", "抱", "搂",
    "起身", "坐下", "转身", "递", "敲", "捂",
    "掏出", "放下", "耸肩", "咬牙", "叹了口气", "冷哼", "哼",
)
ACTION_ATTRIBUTION_RE = re.compile(
    r"(?:^|[。！？!?；;，,\s”’」』】\]\"])"
    r"(?P<name>我|她|他|[㐀-鿿·]{1,8}?)"
    r"(?:一脸|满脸|有些|很是)?(?:"
    + "|".join(map(re.escape, sorted(STATE_ATTRIBUTION_TERMS, key=len, reverse=True)))
    + r")?[，,\s]*"
    r"(?:轻轻地?|冷冷地?|猛地|突然|忽然|一把|缓缓|慢慢)?"
    r"(?P<action>" + "|".join(map(re.escape, sorted(ACTION_WORDS, key=len, reverse=True))) + r")"
)
BOUNDARY_ACTOR_RE = re.compile(
    r"^\s*(?P<name>我|她|他|[\u3400-\u9fff·]{1,3}?)(?="
    r"刚|又|再次|一时|正|仍|还|却|突然|忽然|缓缓|慢慢|"
    r"脸|一脸|双手|手|眼|眉|嘴|头|身|脚|语气|神情|表情|"
    r"走|站|坐|起|转|抬|低|挥|拽|拉|抓|握|拍|点|摇|"
    r"笑|哭|皱|挑|惊|瞪|冷哼|叹|推|扶|抱|递|掏|放|耸|咬|又)"
)
TRAILING_ACTOR_CUE_RE = re.compile(
    r"^\s*(?P<name>我|她|他|[\u3400-\u9fff·]{1,8}?)"
    r"(?:又|再|随口|顺口|淡淡地?|平静地?|冷冷地?|轻声|低声|沉声|"
    r"笑着|慢条斯理地?)?"
    r"(?:"
    r"(?:解释|说明|补充|回应|提醒|反驳|询问|追问|吩咐|命令|警告|"
    r"嘱咐|坦言|分析|总结|安慰|抱怨|嘟囔|开口|回答|说|问|答|喊|"
    r"叫|吼|喝|叹|笑|应|回|接|续|劝)"
    r"(?:道|完|罢|着|了|得[^，,。！？!?；;\n]{0,16})?|"
    r"扬唇|勾唇|弯唇|抿唇|颔首|点头|摇头|挑眉|蹙眉|皱眉|"
    r"抬眸|垂眸|抬眼|垂眼|冷哼|轻哼|"
    r"(?:将|把)[^，,。！？!?；;\n]{1,16}?"
    r"(?:搁|放|收|推|递|扔|丢|按|压|拿|塞|摆|移|合|展开|翻开|端|抬)"
    r")"
)
NARRATIVE_ACTOR_RE = re.compile(
    r"(?:^|[，,。！？!?；;])\s*"
    r"(?P<name>我|她|他|[\u3400-\u9fff·]{2,4}?)"
    r"(?:又|再次|正|仍|还|却|突然|忽然|缓缓|慢慢)?"
    r"(?="
    r"脸|一脸|双手|手|眼|眉|嘴|头|身|脚|语气|神情|表情|"
    r"出现|走|来|站|坐|起|转|抬|低|挥|拽|拉|抓|握|拍|点|摇|"
    r"笑|哭|皱|蹙|挑|惊|瞪|看|望|盯|冷哼|叹|推|扶|抱|递|"
    r"掏|放|耸|咬|点燃|抽|吐|开口|问|答)"
)
NARRATIVE_BEHAVIOR_RE = re.compile(
    r"^\s*(?:又|再次|正|仍|还|却|突然|忽然|缓缓|慢慢)?"
    r"(?:脸|一脸|双手|手|眼|眉|嘴|头|身|脚|语气|神情|表情|"
    r"出现|走|来|站|坐|起|转|抬|低|挥|拽|拉|抓|握|拍|点|摇|"
    r"笑|哭|皱|蹙|挑|惊|瞪|看|望|盯|冷哼|叹|推|扶|抱|递|"
    r"掏|放|耸|咬|点燃|抽|吐|开口|问|答)"
)
PRONOUN_SPEECH_FRAME_RE = re.compile(
    r"(?:^|[。！？!?；;，,\s])(?P<name>她|他)"
    r"(?=[^。！？!?；;\n]{0,48}(?:"
    r"脱口而出|聊起天来|"
    r"开口(?:(?:就|便|才|又)?(?:唤|叫|说|问|答|喊|道))?|"
    r"出声(?:说道)?|"
    r"说|问|答|喊|叫|吼|嚷|嘟囔|低语|耳语|回应|回答|反问|"
    r"追问|提醒|命令|警告|央求|劝|宣布"
    r")(?:道|着|了|起来)?[：:,，\s—－-]*$)"
)
PRONOUN_BACKREF_RE = re.compile(
    r"^\s*(?P<name>她|他)(?:的)?(?:语气|声音|声线|嗓音|口吻|话音)|"
    r"^\s*(?P<speech_name>她|他)(?:说(?:这|那)话|说完|说罢|话音)"
)
NAMED_EXPRESSIVE_SPEECH_RE = re.compile(
    r"(?:^|[。！？!?；;，,\s])"
    r"(?P<name>(?!你|这|那|其)[\u3400-\u9fff·]{2,4}?)"
    r"(?:一时忘形|情难自禁|忍俊不禁|怒不可遏)?[，,\s]*"
    r"(?:拍桌|拍案|敲桌|抚掌|跺脚)?(?:大笑|冷笑|苦笑|怒喝|高喊)"
    r"(?:着|道)?[：:,，\s]*$"
)
NAMED_ACTION_LEAD_PATTERN = (
    r"(?:又|再次|一时忘形|噎了噎|边走边|噗嗤|讪讪|有些|很是|"
    r"禀道|嘀咕|干笑|讪笑|笑开|大笑|苦笑|冷笑|脱口而出|"
    r"解释道|回应道|追问|反问|轻声问|低声问|扬声打断|边走边道)"
)
NAMED_ACTION_QUOTE_RE = re.compile(
    r"(?:^|[。！？!?；;，,\s])"
    r"(?P<name>(?!你|这|那|其|听她|听他|让她|让他)[\u3400-\u9fff·]{2,4})"
    rf"(?={NAMED_ACTION_LEAD_PATTERN})"
    r"[^。！？!?；;\n]{0,44}?"
    r"(?:禀道|嘀咕|干笑|讪笑|笑开|大笑|苦笑|冷笑|脱口而出|"
    r"解释道|回应道|追问|反问|轻声问|低声问|扬声打断|边走边道)"
    r"[^。！？!?；;\n]{0,8}[：:]\s*$"
)
OBJECT_SPEECH_RE = re.compile(
    r"(?:对|向|冲|朝)\s*(?P<object>我|你|她|他|[\u3400-\u9fff·]{1,8}?)"
    r"(?:轻声|低声|高声|大声|沉声|冷声|柔声|笑着|哭着|又|再|缓缓)?"
    + r"(?:" + SPEECH_VERB_PATTERN + r"|道)[：:,，\s]*$"
)
DIRECT_SUBJECT_OBJECT_SPEECH_RE = re.compile(
    r"^\s*(?P<subject>我|她|他|[\u3400-\u9fff·]{2,4})"
    r"(?:又|正|仍|还|却|突然|忽然|缓缓|慢慢)?"
    r"(?:对|向|冲|朝)\s*(?:我|你|她|他|[\u3400-\u9fff·]{1,8}?)"
    r"(?:轻声|低声|高声|大声|沉声|冷声|柔声|笑着|哭着|又|再|缓缓)?"
    + r"(?:" + SPEECH_VERB_PATTERN + r"|道)[：:,，\s]*$"
)
BARE_DAO_SPEAKER_RE = re.compile(
    r"(?:^|[。！？!?；;，,\s])"
    r"(?P<name>我|她|他|(?!你|这|那|其|作者|史官)[\u3400-\u9fff·]{1,8}?)"
    r"道[：:,，\s]*$"
)
RESPONSE_SOURCE_FRAME_RE = re.compile(
    r"(?:回答|回应|答复)(?:我|你|她|他|[\u3400-\u9fff·]{1,8})的[，,\s]*"
    r"是(?P<body>[^。！？!?；;\n]{1,48}?)"
    r"(?:一声|声音|话音|嗓音)[：:,，\s]*$"
)
RESPONSE_BODY_ACTOR_RE = re.compile(
    r"^\s*(?P<name>[\u3400-\u9fff·]{2,4}?)(?="
    r"板着|沉着|带着|顶着|挂着|露出|投来|递来|给了|冷冷|淡淡|轻轻|缓缓)"
)
BOUND_IDENTITY_RE_TEMPLATE = (
    r"(?<![与向冲朝跟同和])(?:{name})[^。！？!?；;\n]{{0,16}}?[，,；;]\s*(?P<pronoun>她|他)"
    r"(?:的|又|便|就|正|仍|还|却|才|只|总|常|从|已经|正在|将|会|要|可|没|不)"
)
REVERSE_IDENTITY_RE_TEMPLATE = (
    r"(?P<pronoun>她|他)(?:就是|正是|名叫|叫作|叫做|乃是|是)\s*(?:{name})"
)
POSSESSIVE_KIN_RE_TEMPLATE = (
    r"(?:{name})(?:和|与)(?P<pronoun>她|他)(?:的)?"
    r"(?:母亲|父亲|妈妈|爸爸|家人|姐妹|兄弟|妻子|丈夫)"
)
FEMALE_IDENTITY_ROLES = frozenset({"女性", "女人", "女子", "女孩", "少女"})
MALE_IDENTITY_ROLES = frozenset({"男性", "男人", "男子", "男孩", "少年"})
IDENTITY_ROLE_PATTERN = (
    r"(?:成年|成年的|年轻|年轻的|年少|年少的|中年|中年的|老年|老年的)?"
    r"(?P<role>女性|女人|女子|女孩|少女|男性|男人|男子|男孩|少年)"
)
EXPLICIT_IDENTITY_GENDER_RE_TEMPLATE = (
    r"(?:{name})[^。！？!?；;\n]{{0,12}}?(?:是|乃是|身为|作为)"
    r"(?:一个|一名|个|名)?" + IDENTITY_ROLE_PATTERN
)
EXPLICIT_IDENTITY_COLON_ACTOR_RE = re.compile(
    r"(?:^|[。！？!?；;，,\s])"
    r"(?P<name>我|她|他|(?!你|这|那|其)[\u3400-\u9fff·]{1,8}?)"
    r"[^。！？!?；;\n]{0,18}?(?:是|乃是|身为|作为)"
    r"(?:一个|一名|个|名)?"
    + IDENTITY_ROLE_PATTERN.replace("(?P<role>", "(?P<identity_role>")
    + r"[^。！？!?；;\n]{0,36}[：:]\s*$"
)

EMOTION_PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    (
        "angry",
        re.compile(
            r"愤怒|怒吼|恼火|凶狠|咬牙|滚开|混蛋|该死|找死|"
            r"不高兴|不客气|反悔|甭想|厉声|怒喝|暴喝|咆哮|"
            r"拍案|拍桌|攥紧|青筋|目光凶狠|眸光凶狠|！{2,}|!{2,}"
        ),
        0.9,
    ),
    ("fearful", re.compile(r"害怕|恐惧|惊恐|颤抖|发颤|战栗|脸色发白|后退|救命|别杀|鬼啊"), 0.88),
    ("sad", re.compile(r"哭|眼泪|泪水|哽咽|悲|难过|对不起|绝望|鼻尖发酸|苦涩|失声"), 0.86),
    ("affectionate", re.compile(r"爱你|想你|亲吻|拥抱|深情|依偎|心疼|宠溺|怜惜|耳鬓厮磨"), 0.84),
    ("joyful", re.compile(r"哈哈|太好了|高兴|开心|喜悦|笑出了声|眉眼弯弯|喜上眉梢|笑眯眯"), 0.84),
    ("humorous", re.compile(r"调侃|打趣|坏笑|玩笑|逗你|诡计得逞|戏谑|揶揄|促狭"), 0.8),
    ("weary", re.compile(r"疲惫|虚弱|有气无力|酸软无力|无力|沙哑|揉了揉眉心|喘着|喘气|叹了口气"), 0.8),
    (
        "surprised",
        re.compile(
            r"震惊|大吃一惊|错愕|目瞪口呆|瞪大(?:了)?眼|瞳孔(?:骤缩|一缩)|"
            r"(?:没想到[^。！？!?]{0,24}竟然|竟然[^。！？!?]{0,24}没想到)"
        ),
        0.86,
    ),
    (
        "comforting",
        re.compile(
            r"安慰|轻拍[^。！？!?]{0,16}(?:别怕|没事)|"
            r"(?:柔声|轻声|温声|轻哄)[^。！？!?]{0,16}(?:别怕|没事)|"
            r"(?:别怕|没事)[^。！？!?]{0,12}(?:我在|有我)"
        ),
        0.84,
    ),
    ("confident", re.compile(r"笃定|自信|胸有成竹|胜券在握|成竹在胸|斩钉截铁"), 0.83),
    ("shy", re.compile(r"羞怯|羞涩|忸怩|脸红|红了脸|耳根发红|羞得低下头|羞赧"), 0.83),
    ("disgusted", re.compile(r"恶心|厌恶|嫌弃|作呕|反胃|满脸鄙夷|皱眉避开"), 0.84),
    ("whispering", re.compile(r"耳语|悄声|压低(?:了)?声音|贴近[^。！？!?]{0,10}低语|附耳低语"), 0.84),
    ("gentle", re.compile(r"温柔|轻声|低声|呢喃|柔声|温声|轻哄|柔和|抚摸"), 0.78),
    ("tense", re.compile(r"小心|快走|危险|屏住呼吸|屏息|紧张|戒备|警惕|绷紧|来不及|赶紧"), 0.77),
    ("excited", re.compile(r"激动|振奋|赢了|成功了|冲啊|热血沸腾|迫不及待|两眼放光"), 0.77),
    ("mysterious", re.compile(r"秘密|诡异|幽暗|阴影|神秘|低语|意味深长|压低声音"), 0.74),
    ("solemn", re.compile(r"庄严|肃然|宣布|沉声|郑重|起誓|一字一顿|斩钉截铁"), 0.74),
)

FIRST_PERSON_FEMALE_RE = re.compile(
    r"本姑娘|老娘|小女子|妾身|本宫|寡妇|"
    r"我(?:是|作为|这个)[^。！？]{0,10}(?:女人|女孩|女生|少女|姑娘|妻子|母亲)"
)
FIRST_PERSON_MALE_RE = re.compile(
    r"老子|小爷|本少(?:爷)?|本王|朕|为父|"
    r"我(?:是|作为|这个)[^。！？]{0,10}(?:男人|男孩|男生|少年|丈夫|父亲)"
)


def _anonymous_dialogue_gender(
    speech: str, adjacent_context: str = ""
) -> tuple[str, float, str]:
    """Infer only the voice gender when a concrete identity is unresolved.

    Identity and gender are intentionally separate decisions.  A paragraph
    beginning with ``他再次点燃……`` can justify a male voice for the quote
    immediately above without proving which male character it is.
    """
    female_self = bool(FIRST_PERSON_FEMALE_RE.search(speech))
    male_self = bool(FIRST_PERSON_MALE_RE.search(speech))
    if female_self != male_self:
        return (
            "female" if female_self else "male",
            0.99,
            "first-person-self-title",
        )
    genders: set[str] = set()
    for line in str(adjacent_context or "").splitlines()[1:3]:
        boundary = _boundary_actor(line)
        if boundary in PRONOUN_GENDERS:
            genders.add(PRONOUN_GENDERS[boundary])
            continue
        action = _action_actors(line)
        if action and action[0][0] in PRONOUN_GENDERS:
            genders.add(PRONOUN_GENDERS[action[0][0]])
    if len(genders) == 1:
        return next(iter(genders)), 0.94, "adjacent-pronoun-action"
    return "unknown", 0.0, "unknown"


def plausible_speaker_name(value: str, *, strong_attribution: bool = False) -> bool:
    name = str(value or "").strip(" ·")
    if name == PROTAGONIST_NAME:
        return True
    return bool(
        (1 if strong_attribution else 2) <= len(name) <= 8
        and name not in NON_NAMES
        and name not in SUSPICIOUS_ACTOR_NAMES
        and not name.startswith(NON_NAME_PREFIXES)
        and not name.startswith(ALWAYS_POLLUTED_NAME_PREFIXES)
        and not name.endswith(ALWAYS_POLLUTED_NAME_SUFFIXES)
        and not name.endswith(tuple(STATE_ATTRIBUTION_TERMS))
        and not NON_NAME_GRAMMAR_RE.match(name)
        and (len(name) <= 4 or "·" in name)
        and (
            len(name) > 1
            or name in GENERIC_ROLE_ALIASES
            or name in FEMALE_NAME_CHARS
            or name in MALE_NAME_CHARS
        )
        and not name.endswith(NON_NAME_ENDINGS)
        and not any(part in name for part in NON_NAME_PARTS)
    )


def structured_colon_field(name: str, value: str) -> bool:
    """Return whether ``label: value`` is structured prose, not dialogue.

    Financial statements, system panels and character sheets commonly use
    the same colon shape as script dialogue.  Treat semantic field labels as
    narration, and treat numeric values after a non-personal prefix the same
    way.  The full line must remain spoken; stripping the prefix changes the
    meaning and makes lists sound like disconnected numbers.
    """
    label = str(name or "").strip(" ·")
    payload = str(value or "").strip()
    if not label or not payload:
        return False
    if label.endswith(STRUCTURED_COLON_FIELD_ENDINGS):
        return True
    return bool(
        STRUCTURED_COLON_VALUE_RE.match(payload)
        and (
            label.endswith(STRUCTURED_COLON_ORGANIZATION_ENDINGS)
            or not plausible_speaker_name(label, strong_attribution=True)
        )
    )


def _clean_extracted_actor_name(value: str) -> str:
    """Trim only a provable parser action tail from a Chinese personal name.

    Attribution expressions are intentionally wider than the persisted-name
    grammar.  For example, a lazy match can return ``肖亚文一`` from
    ``肖亚文一笑`` or ``桑迪嘲`` from ``桑迪嘲道``.  A prefix is accepted only
    when it independently passes the surname-aware external-provider grammar;
    otherwise the candidate is rejected instead of guessed.
    """
    name = str(value or "").strip(" ·")
    if not name or name in SUSPICIOUS_ACTOR_NAMES:
        return ""
    for suffix in sorted(ACTION_FRAGMENT_SUFFIXES, key=len, reverse=True):
        if not name.endswith(suffix):
            continue
        base = name[:-len(suffix)]
        if not is_clean_chinese_name(base):
            continue
        # Four-character single-surname candidates cannot be provider-valid
        # names, so their one-character grammar tail is unambiguous.  For a
        # three-character candidate, only strong action stems are safe to
        # remove; ``一/也`` can be a legitimate given-name character.
        if len(name) > 3 or suffix in {
            "嘲", "反", "想", "打", "吃", "怒", "眼", "调",
            "笑", "哭", "问", "答", "喊", "叫", "说", "看", "找", "将",
            "小声", "低声", "高声", "沉声", "轻声", "大声", "冷声", "怒声", "柔声",
        }:
            return base
    return name


def _clean_persisted_actor_name(
    canonical_name: str,
    aliases: list[Any] | tuple[Any, ...],
) -> str:
    """Recover a clean actor from an old action-prefixed canonical name.

    Earlier attribution could persist ``唤来小梅`` while also storing ``小梅``
    as an alias.  Reuse the existing character key and fixed voice, but expose
    the clean suffix as the canonical identity.  Only an exact suffix alias is
    accepted so legitimate longer names are not shortened heuristically.
    """
    canonical = _clean_extracted_actor_name(canonical_name)
    if not canonical:
        return ""
    if not canonical.startswith(ALWAYS_POLLUTED_NAME_PREFIXES):
        return canonical
    candidates = [
        str(alias or "").strip(" ·")
        for alias in aliases
        if str(alias or "").strip(" ·")
        and canonical.endswith(str(alias or "").strip(" ·"))
        and plausible_speaker_name(
            str(alias or "").strip(" ·"), strong_attribution=True
        )
    ]
    return min(candidates, key=lambda value: (len(value), value)) if candidates else canonical


def trusted_persisted_cast(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose only full-scan identities that pass the playback hard gate.

    Cameos without durable evidence remain anonymous.  A candidate that is a
    known identity plus a grammatical/action tail (``江叙没``/``孟姐也``) is
    rejected rather than receiving another permanent voice.
    """
    def effective_role_type(row: dict[str, Any]) -> str:
        ai_role_type = str(row.get("ai_review_role_type") or "")
        if (
            ai_role_type in {"protagonist", "supporting", "cameo"}
            and float(row.get("ai_review_confidence") or 0.0) >= 0.8
        ):
            return ai_role_type
        role_type = str(row.get("role_type") or "unclassified")
        if role_type in {"protagonist", "supporting", "cameo"}:
            return role_type
        if str(row.get("canonical_name") or "").strip() == PROTAGONIST_NAME:
            return "protagonist"
        if (
            int(row.get("chapter_count") or 0) >= 3
            or int(row.get("dialogue_count") or 0) >= 5
        ):
            return "supporting"
        return "cameo"

    established = {
        str(row.get("canonical_name") or "").strip()
        for row in rows
        if effective_role_type(row) in {"protagonist", "supporting"}
        and str(row.get("canonical_name") or "").strip()
        not in SUSPICIOUS_ACTOR_NAMES
    }
    trusted: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("canonical_name") or "").strip()
        structured_identity = bool(
            re.fullmatch(r"(?:小|阿|老)[\u3400-\u9fff]{1,2}", name)
            or re.fullmatch(
                r"[\u3400-\u9fff]{1,3}(?:姐|哥|弟|叔|婶|妈|姨|爷|"
                r"总|少|队长|律师|太太|夫人)",
                name,
            )
            or re.fullmatch(r"[\u3400-\u9fff]{1,3}儿", name)
            or (len(name) == 2 and name[0] == name[1])
        )
        reviewed_identity = bool(
            str(row.get("ai_review_role_type") or "")
            in {"protagonist", "supporting"}
            and float(row.get("ai_review_confidence") or 0.0) >= 0.8
        )
        role_type = effective_role_type(row)
        durable_identity = bool(
            name == PROTAGONIST_NAME
            or is_clean_chinese_name(name)
            or structured_identity
            or reviewed_identity
        )
        if (
            role_type not in {"protagonist", "supporting"}
            or name in SUSPICIOUS_ACTOR_NAMES
            or name in NON_NAMES
            or not durable_identity
            or not bool(row.get("voice_locked"))
            or str(row.get("gender") or "unknown") not in {"female", "male"}
        ):
            continue
        contaminated = any(
            name.startswith(base)
            and name != base
            and name[len(base):] in PREFIX_ACTION_TAILS
            for base in established
        )
        if not contaminated:
            row["role_type"] = role_type
            trusted.append(row)
    return trusted


def explicit_attribution_speaker(line: str) -> str:
    text = str(line or "").strip()
    quote = QUOTE_RE.search(text)
    leading = _leading_explicit_speech_actor(
        text[:quote.start()] if quote else text
    )
    if leading:
        return leading
    for pattern in (LEADING_ATTRIBUTION_RE, OPENED_SPEECH_RE):
        match = pattern.search(text)
        if match:
            name = match.group("name")
            if plausible_speaker_name(name, strong_attribution=True):
                return name
    if quote:
        prefix = text[:quote.start()]
        for pattern in (
            SPEAKER_RE,
            STATE_COLON_ACTOR_RE,
            DESCRIPTIVE_STATE_COLON_ACTOR_RE,
        ):
            match = pattern.search(prefix)
            if match:
                name = match.group("name")
                if plausible_speaker_name(name, strong_attribution=True):
                    return name
        match = TRAILING_SPEECH_ATTRIBUTION_RE.match(text[quote.end():])
        if match:
            name = match.group("name")
            if plausible_speaker_name(name, strong_attribution=True):
                return name
        trailing_actor = _trailing_actor_cue(text[quote.end():])
        if trailing_actor:
            return trailing_actor
    return ""


def _leading_explicit_speech_actor(fragment: str) -> str:
    """Extract a grammatical leading actor from an explicit speech clause.

    Web fiction overwhelmingly uses bare ``说/问`` and often inserts an
    action between the name and that verb (``肖亚文一上车就笑着说``).  The
    generic regex cannot safely treat that whole action phrase as a name.  A
    surname-valid 2--4 character prefix at the start of an explicit speech
    clause is strong identity evidence; anything else remains unresolved.
    """
    narrative = str(fragment or "").strip()
    if not narrative:
        return ""
    clause = re.split(r"[。！？!?；;]", narrative)[-1].strip(" ，,：:")
    verb = re.search(
        EXPLICIT_SPEECH_VERB_PATTERN + r"[：:,，,\s]*$", clause
    )
    if not verb:
        return ""
    subject = clause[:verb.start()].strip(" ，,")
    action_tail = re.compile(
        r"^(?:也|又|再|再次|仍|还|便|就|一边|边|一上|一进|一听|一看|"
        r"小声|轻声|低声|高声|沉声|冷声|大声|随口|笑着|哭着|冷冷地|"
        r"愤怒地|不解地|严肃地|认真地|狐疑地|惊恐地|温柔地|淡淡地|"
        r"惊|诧|蹙|扬|摆|挥|拍|点|摇|抬|转|瞪|瞥|扫|看|望|嘲|反|想|"
        r"闻|系|理|细|停|开|低头|人|(?:对|向|冲|朝).{1,8})"
    )
    regions = [region.strip() for region in re.split(r"[，,]", subject)]
    for region_index in range(len(regions) - 1, -1, -1):
        region = regions[region_index]
        if region in PRONOUN_GENDERS:
            return region
        if region[:1] in PRONOUN_GENDERS:
            tail = region[1:].strip()
            if tail and re.match(
                r"(?:又|再|再次|仍|还|便|就|一边|边|突然|忽然|缓缓|"
                r"慢慢|拨|走|上前|来到|转身|回头|抬头|低头|看|望|盯|"
                r"笑|哭|皱眉|挑眉|叹|冷哼|点头|摇头|抓|拽|拉|推|扶|抱)",
                tail,
            ):
                return region[:1]
        for length in (2, 3, 4):
            candidate = region[:length]
            if (
                len(candidate) != length
                or not is_clean_chinese_name(candidate)
                or not plausible_speaker_name(
                    candidate, strong_attribution=True
                )
            ):
                continue
            tail = region[length:]
            if tail and not action_tail.match(tail):
                continue
            return candidate
        cleaned = _clean_extracted_actor_name(region)
        if (
            len(regions) == 1
            and region_index == 0
            and cleaned == region
            and plausible_speaker_name(cleaned, strong_attribution=True)
            and (len(cleaned) <= 4 or "·" in cleaned)
        ):
            return cleaned
    return ""


def normalize_chapter_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for raw in text.split("\n"):
        line = re.sub(r"[\t\u3000 ]+", " ", raw).strip()
        if not line or re.fullmatch(r"\[(?:本地)?(?:插图|illustration)[:：].+\]", line, re.I):
            continue
        lines.append(line)
    return "\n".join(lines)


def split_tts_text(value: str, limit: int = 450) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    pieces = [item.strip() for item in re.split(r"(?<=[。！？!?；;…])", text) if item.strip()]
    result: list[str] = []
    current = ""
    for piece in pieces or [text]:
        while len(piece) > limit:
            if current:
                result.append(current)
                current = ""
            result.append(piece[:limit])
            piece = piece[limit:]
        if not piece:
            continue
        if current and len(current) + len(piece) > limit:
            result.append(current)
            current = piece
        else:
            current += piece
    if current:
        result.append(current)
    return result


def _confidence(left: int, right: int) -> tuple[str, float]:
    total = left + right
    if total <= 0 or left == right:
        return "unknown", 0.0
    return ("female" if left > right else "male", min(0.99, 0.55 + abs(left - right) / (total + 2)))


def _quote_speech(match: re.Match[str]) -> str:
    return next(
        (str(match.group(name) or "") for name in QUOTE_GROUPS if match.group(name)),
        "",
    )


def _role_gender(name: str) -> tuple[str, float, str]:
    clean = str(name or "").strip()
    if clean in PRONOUN_GENDERS:
        return PRONOUN_GENDERS[clean], 1.0, "context"
    if clean.endswith(FEMALE_ROLE_SUFFIXES):
        return "female", 0.97, "role_suffix"
    if clean.endswith(MALE_ROLE_SUFFIXES):
        return "male", 0.97, "role_suffix"
    female = sum(char in FEMALE_NAME_CHARS for char in clean)
    male = sum(char in MALE_NAME_CHARS for char in clean)
    gender, confidence = _confidence(female, male)
    return gender, confidence, "name_heuristic" if gender != "unknown" else "unknown"


def _bound_gender_evidence(name: str, context: str) -> tuple[str, float, str]:
    """Return only gender evidence grammatically attached to ``name``.

    A paragraph may mention many people and contain quoted pronouns.  Counting
    every ``他``/``她`` in that paragraph lets an addressee or quoted third
    party overwrite the actual speaker.  These deliberately narrow frames
    require an explicit identity link instead.
    """
    clean_name = str(name or "").strip()
    sample = QUOTE_RE.sub(" ", str(context or ""))
    if not clean_name or not sample:
        return "unknown", 0.0, "unknown"
    escaped = re.escape(clean_name)
    evidence: list[str] = []
    for template in (
        BOUND_IDENTITY_RE_TEMPLATE,
        REVERSE_IDENTITY_RE_TEMPLATE,
        POSSESSIVE_KIN_RE_TEMPLATE,
    ):
        pattern = re.compile(template.format(name=escaped))
        evidence.extend(
            str(match.group("pronoun") or "") for match in pattern.finditer(sample)
        )
    identity_pattern = re.compile(
        EXPLICIT_IDENTITY_GENDER_RE_TEMPLATE.format(name=escaped)
    )
    roles = [str(match.group("role") or "") for match in identity_pattern.finditer(sample)]
    female = sum(value == "她" for value in evidence) + sum(
        value in FEMALE_IDENTITY_ROLES for value in roles
    )
    male = sum(value == "他" for value in evidence) + sum(
        value in MALE_IDENTITY_ROLES for value in roles
    )
    if female and male:
        return "unknown", 0.0, "conflict"
    if female:
        source = "explicit-identity" if roles else "explicit-pronoun"
        return "female", 0.99, source
    if male:
        source = "explicit-identity" if roles else "explicit-pronoun"
        return "male", 0.99, source
    return "unknown", 0.0, "unknown"


def _traits(name: str, context: str) -> dict[str, Any]:
    gender, gender_confidence, gender_source = _bound_gender_evidence(name, context)
    if name == PROTAGONIST_NAME:
        female_first = len(FIRST_PERSON_FEMALE_RE.findall(context))
        male_first = len(FIRST_PERSON_MALE_RE.findall(context))
        first_gender, first_confidence = _confidence(female_first, male_first)
        if first_gender != "unknown":
            first_confidence = max(first_confidence, 0.94)
        if first_gender != "unknown" and gender == "unknown":
            gender, gender_confidence = first_gender, first_confidence
            gender_source = "first_person"
    role_gender, role_confidence, role_source = _role_gender(name)
    if role_gender != "unknown" and gender == "unknown":
        gender, gender_confidence = role_gender, role_confidence
        gender_source = role_source
    if gender == "unknown":
        name_female = sum(name.count(char) for char in FEMALE_NAME_CHARS)
        name_male = sum(name.count(char) for char in MALE_NAME_CHARS)
        gender, gender_confidence = _confidence(name_female, name_male)
        gender_source = "name_heuristic" if gender != "unknown" else "unknown"
    if CHILD_HINT.search(context):
        age, age_confidence = "child", 0.82
    elif SENIOR_HINT.search(context):
        age, age_confidence = "senior", 0.82
    elif YOUNG_HINT.search(context):
        age, age_confidence = "young", 0.72
    else:
        age, age_confidence = "unknown", 0.0
    tone = "neutral"
    tone_confidence = 0.4
    for candidate, pattern in (
        ("angry", r"怒|吼|滚|混蛋|！{2,}"),
        ("sad", r"哭|泪|悲|哽咽"),
        ("joyful", r"笑|开心|高兴|哈哈"),
        ("gentle", r"温柔|轻声|低语|呢喃"),
        ("fearful", r"害怕|恐惧|颤抖|救命"),
    ):
        if re.search(pattern, context):
            tone, tone_confidence = candidate, 0.72
            break
    return {
        "gender": gender,
        "gender_confidence": round(gender_confidence, 4),
        "gender_source": gender_source,
        "age_group": age,
        "age_confidence": age_confidence,
        "tone": tone,
        "tone_confidence": tone_confidence,
    }


def _select_gender_evidence(
    evidence: list[tuple[str, float, str]],
) -> tuple[str, float, str]:
    usable = [
        (gender, float(confidence), source)
        for gender, confidence, source in evidence
        if gender in {"female", "male"} and float(confidence) >= 0.55
    ]
    if not usable:
        return "unknown", 0.0, "unknown"
    strong = [item for item in usable if item[2] in STRONG_GENDER_SOURCES]
    if strong:
        genders = {item[0] for item in strong}
        if len(genders) != 1:
            return "unknown", 0.0, "conflict"
        return max(strong, key=lambda item: item[1])
    ranked = sorted(usable, key=lambda item: item[1], reverse=True)
    if (
        len({item[0] for item in ranked}) > 1
        and len(ranked) > 1
        and ranked[0][1] - ranked[1][1] < 0.08
    ):
        return "unknown", 0.0, "conflict"
    return ranked[0]


def reconcile_character_gender(
    existing: dict[str, Any], candidate: dict[str, Any]
) -> tuple[str, float, bool]:
    """Merge a chapter candidate into a persisted character conservatively."""
    old_gender = str(existing.get("gender") or "unknown")
    old_confidence = float(existing.get("gender_confidence") or 0.0)
    new_gender = str(candidate.get("gender") or "unknown")
    new_confidence = float(candidate.get("gender_confidence") or 0.0)
    new_source = str(candidate.get("gender_source") or "unknown")
    if new_gender not in {"female", "male"}:
        return old_gender, old_confidence, False
    if old_gender == new_gender:
        return old_gender, max(old_confidence, new_confidence), False
    if old_gender == "unknown":
        if new_confidence >= 0.8:
            return new_gender, new_confidence, True
        return old_gender, old_confidence, False
    if new_source in STRONG_GENDER_SOURCES and new_confidence >= 0.94:
        voice = str(existing.get("voice_key") or "")
        if (
            voice
            and voice not in _voice_pool(old_gender)
            and voice in _voice_pool(new_gender)
        ):
            return new_gender, new_confidence, True
    return old_gender, old_confidence, False


def _gender_source_is_verified(source: str) -> bool:
    return str(source or "unknown") in STRONG_GENDER_SOURCES | {
        "context", "external",
    }


def _merge_external_gender_evidence(
    character: dict[str, Any], result: dict[str, Any]
) -> None:
    """Add provider evidence without overriding explicit prose evidence."""
    external_gender = str(result.get("gender") or "unknown")
    external_confidence = float(result.get("confidence") or 0.0)
    if external_gender not in {"female", "male"}:
        return
    current_gender = str(character.get("gender") or "unknown")
    current_confidence = float(character.get("gender_confidence") or 0.0)
    current_source = str(character.get("gender_source") or "unknown")
    character["_external_gender_checked"] = True
    character["_external_gender"] = external_gender
    character["_external_gender_confidence"] = external_confidence
    if current_gender in {"female", "male"} and current_gender != external_gender:
        character["_gender_review_required"] = True
        # Explicit novel evidence remains authoritative for playback.  A name
        # heuristic is weaker than the dedicated provider, but disagreement is
        # still sent through AI before a permanent lock is restored.
        if not _gender_source_is_verified(current_source):
            character["gender"] = external_gender
            character["gender_confidence"] = external_confidence
            character["gender_source"] = "external"
        return
    if not _gender_source_is_verified(current_source):
        character["gender"] = external_gender
        character["gender_confidence"] = external_confidence
        character["gender_source"] = "external"
    elif current_gender == external_gender:
        character["gender_confidence"] = max(
            current_confidence, external_confidence
        )


def _character_gender_verified(character: dict[str, Any]) -> bool:
    return bool(
        str(character.get("gender") or "unknown") in {"female", "male"}
        and float(character.get("gender_confidence") or 0.0) >= 0.7
        and _gender_source_is_verified(
            str(character.get("gender_source") or "unknown")
        )
        and not bool(character.get("_gender_review_required"))
    )


def _voice_pool(gender: str) -> tuple[str, ...]:
    if gender == "female":
        return FEMALE_VOICES
    if gender == "male":
        return MALE_VOICES
    return ()


def _infer_emotion(text: str, context: str = "") -> tuple[str, float, str]:
    speech = str(text or "")
    sample = f"{context} {speech}".strip()
    ranked: list[tuple[float, int, str]] = []
    for priority, (emotion, pattern, confidence) in enumerate(EMOTION_PATTERNS):
        matches = pattern.findall(sample)
        if not matches:
            continue
        score = confidence + min(0.06, 0.02 * (len(matches) - 1))
        # Punctuation affects delivery only when it belongs to the spoken
        # fragment; narrative punctuation must not leak into another actor.
        if emotion in {"angry", "excited", "fearful"} and re.search(r"[！!]{2,}", speech):
            score += 0.03
        if emotion in {"fearful", "tense", "sad"} and "……" in speech:
            score += 0.02
        ranked.append((min(0.97, score), -priority, emotion))
    if ranked:
        confidence, _priority, emotion = max(ranked)
        return emotion, round(confidence, 4), "segment-context"
    return "neutral", 0.4, "default"


def _known_character_indexes(
    known_characters: list[dict[str, Any]] | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    profiles: dict[str, dict[str, Any]] = {}
    aliases: dict[str, list[dict[str, Any]]] = {}
    candidates: list[tuple[dict[str, Any], str, list[Any]]] = []
    for raw in known_characters or []:
        item = dict(raw)
        raw_aliases = item.get("aliases")
        if not isinstance(raw_aliases, list):
            try:
                raw_aliases = json.loads(str(raw_aliases or "[]"))
            except (TypeError, ValueError, json.JSONDecodeError):
                raw_aliases = []
        stored_canonical = str(item.get("canonical_name") or "").strip()
        canonical = _clean_persisted_actor_name(stored_canonical, raw_aliases)
        if not canonical or not plausible_speaker_name(
            canonical, strong_attribution=True
        ):
            continue
        item["canonical_name"] = canonical
        if canonical != stored_canonical:
            item["_stored_canonical_name"] = stored_canonical
        candidates.append((item, canonical, raw_aliases))

    canonical_names = {canonical for _item, canonical, _aliases in candidates}
    protagonist_names = {
        canonical
        for item, canonical, _aliases in candidates
        if str(item.get("role_type") or "") == "protagonist"
    }
    for item, canonical, raw_aliases in candidates:
        if canonical.endswith(ALWAYS_POLLUTED_NAME_SUFFIXES):
            continue
        if any(
            canonical == f"{base}{suffix}"
            for base in canonical_names
            if base != canonical and len(base) >= 2
            for suffix in (
                *PREFIX_COLLISION_SUFFIXES,
                *KNOWN_ACTOR_ACTION_SUFFIXES,
                *PREFIX_ACTION_TAILS,
            )
        ):
            continue
        candidate_aliases = {
            canonical,
            *(str(value).strip() for value in raw_aliases if str(value).strip()),
        }
        if len(protagonist_names) == 1 and canonical in protagonist_names:
            candidate_aliases.add("我")
        item["aliases"] = sorted(
            {
                value
                for value in candidate_aliases
                if not value.startswith(ALWAYS_POLLUTED_NAME_PREFIXES)
                if value == canonical
                or value == "我"
                or (
                    value not in canonical_names
                    and value not in GENERIC_ROLE_ALIASES
                    and plausible_speaker_name(value, strong_attribution=True)
                )
            },
            key=lambda value: (-len(value), value),
        )
        profiles[canonical] = item
        for alias in item["aliases"]:
            aliases.setdefault(alias, []).append(item)
    return profiles, aliases


def _action_actors(value: str) -> list[tuple[str, int]]:
    actors: list[tuple[str, int]] = []
    for match in ACTION_ATTRIBUTION_RE.finditer(str(value or "")):
        raw_name = str(match.group("name") or "").strip()
        if raw_name in {"我", "她", "他"} or plausible_speaker_name(
            raw_name, strong_attribution=True
        ):
            actors.append((raw_name, match.start("name")))
    return actors


def _boundary_actor(value: str) -> str:
    match = BOUNDARY_ACTOR_RE.match(str(value or ""))
    return str(match.group("name") or "").strip() if match else ""


def _trailing_actor_cue(value: str) -> str:
    match = TRAILING_ACTOR_CUE_RE.match(str(value or ""))
    if not match:
        return ""
    name = str(match.group("name") or "").strip()
    if name in {"我", "她", "他"} or plausible_speaker_name(
        name, strong_attribution=True
    ):
        return name
    return ""


def _pronoun_backref(value: str) -> str:
    match = PRONOUN_BACKREF_RE.match(str(value or ""))
    if not match:
        return ""
    return str(match.group("name") or match.group("speech_name") or "")


@dataclass(frozen=True)
class QuoteIntent:
    dialogue: bool
    confidence: float
    source: str


def _quote_only_line(line: str, matches: list[re.Match[str]]) -> bool:
    if not matches:
        return False
    cursor = 0
    outside: list[str] = []
    for match in matches:
        outside.append(line[cursor:match.start()])
        cursor = match.end()
    outside.append(line[cursor:])
    return not re.sub(r"[\s，,。；;、…—-]+", "", "".join(outside))


def _nearby_action_before(prefix: str) -> bool:
    matches = list(ACTION_ATTRIBUTION_RE.finditer(prefix))
    if not matches:
        return False
    tail = prefix[matches[-1].end():]
    return bool(
        re.fullmatch(r"[：:,，\s]*", tail)
        or re.fullmatch(
            r"(?:了)?(?:我|你|他|她|它|自己)?(?:的)?"
            r"(?:手|胳膊|肩膀|桌子|脑袋|头|脸|后背|衣袖|手腕|门|窗|"
            r"椅子|杯子)[：:,，\s]*",
            tail,
        )
        or re.fullmatch(r"(?:示意|催促|制止|阻拦)[：:,，\s]*", tail)
    )


def _nearby_action_after(suffix: str) -> bool:
    matches = list(ACTION_ATTRIBUTION_RE.finditer(suffix))
    if not matches:
        return False
    leading = suffix[:matches[0].start("name")]
    return bool(re.fullmatch(r"[：:,，。.!！?？；;\s]*", leading))


def classify_quote_intent(
    line: str,
    match: re.Match[str],
    matches: list[re.Match[str]],
    match_index: int,
    prior_intents: list[QuoteIntent] | None = None,
) -> QuoteIntent:
    """Conservatively decide whether one quoted span is spoken dialogue.

    Quotation marks also carry titles, labels, citations, terminology and
    scare quotes.  Strong grammatical speech evidence wins, while an embedded
    quote without such evidence stays narration.  The decision intentionally
    precedes all speaker-memory and cast work in ``analyze_chapter``.
    """
    speech = _quote_speech(match).strip()
    prefix = line[:match.start()]
    suffix = line[match.end():]
    previous_end = matches[match_index - 1].end() if match_index else 0
    next_start = (
        matches[match_index + 1].start()
        if match_index + 1 < len(matches)
        else len(line)
    )
    before = line[previous_end:match.start()]
    after = line[match.end():next_start]

    # These are direct-speech grammar, not merely semantic guesses about the
    # quoted words.  They are allowed to override reference-looking content.
    if (
        _leading_explicit_speech_actor(prefix)
        or SPEAKER_RE.search(prefix)
        or STATE_COLON_ACTOR_RE.search(prefix)
        or DESCRIPTIVE_STATE_COLON_ACTOR_RE.search(prefix)
        or DIRECT_SPEECH_FRAME_RE.search(prefix)
        or TRAILING_SPEECH_ATTRIBUTION_RE.match(after)
    ):
        return QuoteIntent(True, 0.99, "explicit-speech")
    if _quote_only_line(line, matches):
        return QuoteIntent(True, 0.97, "standalone-quote")

    # Metalinguistic and referential frames are narration even when the quoted
    # token happens to be a pronoun or resembles a plausible utterance.
    if REFERENCE_QUOTE_PREFIX_RE.search(before) or REFERENCE_QUOTE_SUFFIX_RE.match(
        suffix
    ):
        return QuoteIntent(False, 0.98, "reference-frame")

    if (
        _nearby_action_before(prefix)
        or (
            any(intent.dialogue for intent in prior_intents or [])
            and (_boundary_actor(before) or _trailing_actor_cue(before))
        )
        or _pronoun_backref(after)
        or _boundary_actor(after)
        or _trailing_actor_cue(after)
        or _nearby_action_after(after)
    ):
        return QuoteIntent(True, 0.94, "action-context")
    if prefix.rstrip().endswith(("：", ":")):
        return QuoteIntent(True, 0.88, "colon-context")
    embedded = bool(prefix.strip() or suffix.strip())
    if embedded and QUOTED_TOKEN_RE.fullmatch(speech):
        return QuoteIntent(False, 0.94, "embedded-token")
    if not before.strip() and not after.strip():
        return QuoteIntent(True, 0.9, "standalone-span")
    return QuoteIntent(False, 0.72, "ambiguous-embedded")


def _dialogue_addresses(dialogue: str, candidate: str) -> bool:
    clean = str(candidate or "").strip()
    aliases = {clean}
    if len(clean) > 2:
        aliases.add(clean[-2:])
    return any(
        re.match(rf"^\s*{re.escape(alias)}[，,：:！!？?]", str(dialogue or ""))
        for alias in aliases
        if alias
    )


def analyze_chapter(
    content: str,
    known_characters: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    text = normalize_chapter_text(content)
    paragraphs = text.split("\n")
    _known_profiles, alias_index = _known_character_indexes(known_characters)
    prepared_lines: dict[int, str] = {}
    paragraph_quote_plans: dict[
        int, list[tuple[re.Match[str], QuoteIntent]]
    ] = {}
    for index, paragraph in enumerate(paragraphs):
        prepared = paragraph
        if not CHANNEL_COLON_RE.match(prepared):
            ui_label = UI_LABEL_RE.match(prepared)
            if ui_label:
                prepared = prepared[ui_label.end():].strip()
        matches = list(QUOTE_RE.finditer(prepared))
        prepared_lines[index] = prepared
        quote_plan: list[tuple[re.Match[str], QuoteIntent]] = []
        for match_index, match in enumerate(matches):
            prior_intents = [intent for _match, intent in quote_plan]
            quote_plan.append((
                match,
                classify_quote_intent(
                    prepared,
                    match,
                    matches,
                    match_index,
                    prior_intents,
                ),
            ))
        paragraph_quote_plans[index] = quote_plan
    paragraph_attributions = {
        index: speaker
        for index, line in enumerate(paragraphs)
        if (
            not paragraph_quote_plans[index]
            or any(intent.dialogue for _match, intent in paragraph_quote_plans[index])
        )
        if (speaker := explicit_attribution_speaker(line))
    }
    observed: dict[str, dict[str, Any]] = {}
    mentions: list[dict[str, Any]] = []
    raw_segments: list[dict[str, Any]] = []
    last_dialogue_speaker = ""
    last_dialogue_gender = "unknown"
    last_dialogue_paragraph = -1000

    def remember(
        name: str,
        paragraph_index: int,
        offset: int,
        gender: str,
        confidence: float,
    ) -> None:
        if not name or name in PRONOUN_GENDERS:
            return
        mentions.append({
            "name": name,
            "paragraph_index": paragraph_index,
            "offset": offset,
            "gender": gender,
            "confidence": confidence,
        })
        if len(mentions) > 80:
            del mentions[:-80]

    def known_mentions(fragment: str, paragraph_index: int, base_offset: int = 0) -> None:
        found: list[tuple[int, int, str, dict[str, Any]]] = []
        for alias, candidates in alias_index.items():
            if len(alias) < 2 or len(candidates) != 1:
                continue
            for match in re.finditer(re.escape(alias), fragment):
                found.append((match.start(), match.end(), alias, candidates[0]))
        selected: list[tuple[int, int, str, dict[str, Any]]] = []
        occupied: list[tuple[int, int]] = []
        for candidate in sorted(found, key=lambda item: (item[0], -len(item[2]))):
            start, end, _alias, _profile = candidate
            if any(start < right and end > left for left, right in occupied):
                continue
            selected.append(candidate)
            occupied.append((start, end))
        for offset, _end, alias, profile in sorted(
            selected, key=lambda item: item[0]
        ):
            traits = _traits(alias, fragment)
            if str(traits.get("gender_source") or "unknown") in STRONG_GENDER_SOURCES:
                observe(
                    str(profile["canonical_name"]),
                    alias,
                    fragment,
                    str(traits["gender"]),
                    float(traits["gender_confidence"]),
                    "narrative-gender-evidence",
                    0.99,
                )
            remember(
                str(profile["canonical_name"]),
                paragraph_index,
                base_offset + offset,
                str(profile.get("gender") or "unknown"),
                float(profile.get("gender_confidence") or 0.0),
            )

    def unique_known_actor(fragment: str) -> str:
        """Return one persisted actor mentioned in an attribution fragment.

        This is deliberately limited to unique MySQL aliases and a nearby
        action/state cue.  It recovers prose such as ``小梅……尴尬笑笑``
        without turning the state word ``尴尬`` into a new character.  Two
        different persisted people keep the fragment unresolved.
        """
        narrative = str(fragment or "").strip()
        if not narrative or not (
            ACTION_ATTRIBUTION_RE.search(narrative)
            or STATE_COLON_ACTOR_RE.search(narrative)
            or DESCRIPTIVE_STATE_COLON_ACTOR_RE.search(narrative)
        ):
            return ""
        actors: dict[str, str] = {}
        for alias, candidates in alias_index.items():
            if len(alias) < 2 or len(candidates) != 1:
                continue
            if not re.search(re.escape(alias), narrative):
                continue
            profile = candidates[0]
            canonical = str(profile["canonical_name"])
            current = actors.get(canonical, "")
            if len(alias) > len(current):
                actors[canonical] = alias
        return next(iter(actors.values())) if len(actors) == 1 else ""

    def dialogue_addresses_actor(dialogue: str, candidate: str) -> bool:
        """Recognise persisted aliases as vocatives, not just canonical names.

        A line such as ``“霍大人。”`` addresses 霍奉卿 through a title
        alias.  Generic forward-context attribution must not turn that
        addressee into the speaker or leak the addressee's male voice into an
        otherwise anonymous line.
        """
        clean = str(candidate or "").strip()
        if not clean:
            return False
        aliases = {clean}
        profiles = list(alias_index.get(clean, []))
        canonical_profile = _known_profiles.get(clean)
        if canonical_profile is not None:
            profiles.append(canonical_profile)
        for profile in profiles:
            aliases.add(str(profile.get("canonical_name") or "").strip())
            aliases.update(
                str(alias or "").strip()
                for alias in profile.get("aliases", [])
                if str(alias or "").strip()
            )
        return any(_dialogue_addresses(dialogue, alias) for alias in aliases)

    def known_actor_before_quote(fragment: str) -> str:
        """Return the grammatical named actor leading into a quote.

        Web fiction often uses ``言知时噎了噎，干笑：“……”`` or
        ``小梅在外叩门禀道：“……”`` instead of a plain ``某某说道``.
        Prefer the nearest persisted alias that is not introduced as an
        object.  A leading subject is allowed when the clause ends in a colon;
        this covers expressive/action frames without inventing action words as
        character names.
        """
        narrative = str(fragment or "").strip()
        has_colon = bool(re.search(r"[：:]\s*$", narrative))
        has_spoken_comma = bool(
            re.search(rf"(?:{SPEECH_VERB_PATTERN}|道)[，,]\s*$", narrative)
        )
        if not narrative or not (has_colon or has_spoken_comma):
            return ""
        found: list[tuple[int, int, str, str]] = []
        for alias, candidates in alias_index.items():
            if len(alias) < 2 or len(candidates) != 1:
                continue
            canonical = str(candidates[0]["canonical_name"])
            for match in re.finditer(re.escape(alias), narrative):
                prefix = narrative[max(0, match.start() - 4):match.start()]
                if re.search(r"(?:对|向|冲|朝|看向|望向|告诉|询问|示意)$", prefix):
                    continue
                found.append((match.start(), match.end(), alias, canonical))
        for canonical, profile in observed.items():
            for alias in {
                canonical,
                *(str(value) for value in profile.get("aliases", set()) if str(value)),
            }:
                if len(alias) < 2:
                    continue
                for match in re.finditer(re.escape(alias), narrative):
                    prefix = narrative[max(0, match.start() - 4):match.start()]
                    if re.search(
                        r"(?:对|向|冲|朝|看向|望向|告诉|询问|示意)$",
                        prefix,
                    ):
                        continue
                    found.append((match.start(), match.end(), alias, canonical))
        if not found:
            return ""
        selected: dict[str, tuple[int, int, str, str]] = {}
        for item in found:
            current = selected.get(item[3])
            if current is None or len(item[2]) > len(current[2]) or (
                len(item[2]) == len(current[2]) and item[0] > current[0]
            ):
                selected[item[3]] = item
        ranked = sorted(selected.values(), key=lambda item: (-item[0], -len(item[2])))
        for start, end, alias, _canonical in ranked:
            tail = narrative[end:]
            clause_start = max(
                narrative.rfind("。", 0, start),
                narrative.rfind("！", 0, start),
                narrative.rfind("？", 0, start),
                narrative.rfind("；", 0, start),
            ) + 1
            leading_subject = not narrative[clause_start:start].strip(" ，,")
            actor_cue = bool(
                re.search(
                    rf"(?:{SPEECH_VERB_PATTERN}|道|禀道|嘀咕|干笑|讪笑|笑开|"
                    r"大笑|苦笑|冷笑|脱口而出|说|问|答|喊|打断|解释|"
                    r"诱之以利|追问|打趣|提醒|反驳|吩咐)",
                    tail,
                )
                or ACTION_ATTRIBUTION_RE.search(f"{alias}{tail}")
                or STATE_COLON_ACTOR_RE.search(f"{alias}{tail}")
                or DESCRIPTIVE_STATE_COLON_ACTOR_RE.search(f"{alias}{tail}")
            )
            if actor_cue or (has_colon and leading_subject):
                return alias
        return ""

    def pronoun_actor_before_quote(fragment: str) -> str:
        narrative = str(fragment or "").strip()
        if not narrative or not re.search(r"[：:]\s*$", narrative):
            return ""
        candidates: list[tuple[int, str]] = []
        for match in re.finditer(r"(?P<name>她|他)", narrative):
            prefix = narrative[max(0, match.start() - 3):match.start()]
            if re.search(r"(?:听|见|看|对|向|冲|朝|问|告诉)$", prefix):
                continue
            tail = narrative[match.end():]
            if len(tail) > 72:
                continue
            if re.search(
                r"住嘴|变脸|缩(?:了缩)?脖子|开口|出声|说|问|答|喊|叫|吼|"
                r"嘀咕|干笑|讪笑|大笑|笑开|脱口而出|轻声|低声|沉声|"
                r"拍桌|拍案|扬声|打断|解释|回应|反问|追问",
                tail,
            ):
                candidates.append((match.start(), str(match.group("name"))))
        return max(candidates)[1] if candidates else ""

    def narrative_actor_mentions(
        narrative: str,
        gender: str,
    ) -> list[tuple[int, str, str]]:
        """Return unique persisted and just-observed actors in narration.

        Newly discovered actors such as 言知时 do not exist in MySQL while a
        chapter is being analysed.  They still need to participate in the
        very next pronoun/back-reference resolution, otherwise later lines in
        the same scene drift to a persisted neighbour.
        """
        profiles: list[tuple[str, str, str]] = []
        for alias, candidates in alias_index.items():
            if len(alias) < 2 or len(candidates) != 1:
                continue
            profile = candidates[0]
            profiles.append((
                alias,
                str(profile["canonical_name"]),
                str(profile.get("gender") or "unknown"),
            ))
        for canonical, profile in observed.items():
            profile_gender = str(profile.get("gender") or "unknown")
            for alias in {
                canonical,
                *(str(value) for value in profile.get("aliases", set()) if str(value)),
            }:
                if len(alias) >= 2:
                    profiles.append((alias, canonical, profile_gender))

        found: list[tuple[int, str, str]] = []
        occupied: set[tuple[int, str]] = set()
        for alias, canonical, profile_gender in sorted(
            profiles, key=lambda item: -len(item[0])
        ):
            if profile_gender not in {gender, "unknown"}:
                continue
            for match in re.finditer(re.escape(alias), narrative):
                key = (match.start(), canonical)
                if key in occupied:
                    continue
                occupied.add(key)
                prefix = narrative[max(0, match.start() - 4):match.start()]
                if re.search(r"(?:对|向|冲|朝|看向|望向|告诉|询问|示意)$", prefix):
                    continue
                found.append((match.start(), canonical, alias))
        return found

    def previous_narrative_actor(paragraph_index: int, gender: str) -> str:
        if paragraph_index <= 0:
            return ""
        narrative = QUOTE_RE.sub(" ", paragraphs[paragraph_index - 1]).strip()
        actors = narrative_actor_mentions(narrative, gender)
        if not actors:
            return ""
        actors.sort(key=lambda item: item[0])
        leading = [
            item for item in actors
            if not narrative[:item[0]].strip(" ，,。！？!?；;")
        ]
        if leading:
            return leading[0][1]
        canonical = {item[1] for item in actors}
        if len(canonical) == 1:
            return actors[-1][1]
        return ""

    def recent_narrative_actor(
        paragraph_index: int,
        gender: str,
        *,
        max_distance: int = 8,
    ) -> str:
        """Resolve a pronoun only when the recent scene has one candidate."""
        candidates: set[str] = set()
        lower = max(0, paragraph_index - max_distance)
        for index in range(paragraph_index - 1, lower - 1, -1):
            raw = paragraphs[index].strip()
            if re.fullmatch(r"[—－-]{2,}", raw):
                break
            narrative = QUOTE_RE.sub(" ", raw)
            candidates.update(
                canonical
                for _offset, canonical, _alias in narrative_actor_mentions(
                    narrative, gender
                )
            )
            if len(candidates) > 1:
                return ""
        return next(iter(candidates)) if len(candidates) == 1 else ""

    def continuity_supported(actor: str, gender: str, paragraph_index: int) -> bool:
        if not actor or paragraph_index - last_dialogue_paragraph > 4:
            return False
        if any(
            re.fullmatch(r"[—－-]{2,}", paragraphs[index].strip())
            for index in range(last_dialogue_paragraph + 1, paragraph_index)
        ):
            return False
        profile = _known_profiles.get(actor, {})
        aliases = {
            actor,
            *(str(value) for value in profile.get("aliases", []) if str(value)),
        }
        interstitial = "\n".join(
            QUOTE_RE.sub(" ", paragraphs[index])
            for index in range(last_dialogue_paragraph + 1, paragraph_index)
        )
        if any(alias in interstitial for alias in aliases if len(alias) >= 2):
            return True
        pronoun = "她" if gender == "female" else "他" if gender == "male" else ""
        if pronoun and pronoun in interstitial:
            return True
        competing: set[str] = set()
        for alias, candidates in alias_index.items():
            if len(alias) < 2 or len(candidates) != 1 or alias not in interstitial:
                continue
            competing.add(str(candidates[0]["canonical_name"]))
        return (
            paragraph_index - last_dialogue_paragraph <= 3
            and not (competing - {actor})
        )

    def local_attribution_gender(
        raw_name: str,
        paragraph_index: int,
    ) -> tuple[str, float, str]:
        """Resolve local voice gender without promoting a durable identity.

        A role such as ``小门童`` may be a perfectly good same-line speaker
        while still being unsuitable as a book-wide character fingerprint.
        Likewise, the next narration paragraph can bind a pronoun back to an
        explicitly attributed local role.  These signals decide only the
        voice pool; they do not bypass the full-book identity gate.
        """
        role_gender, role_confidence, role_source = _role_gender(raw_name)
        if role_gender in {"female", "male"} and role_confidence >= 0.9:
            return role_gender, role_confidence, role_source
        if raw_name:
            local_traits = _traits(raw_name, paragraphs[paragraph_index])
            if (
                str(local_traits.get("gender_source") or "unknown")
                == "explicit-identity"
                and str(local_traits.get("gender") or "unknown")
                in {"female", "male"}
            ):
                return (
                    str(local_traits["gender"]),
                    float(local_traits["gender_confidence"]),
                    "explicit-identity",
                )
        if not raw_name or raw_name in PRONOUN_GENDERS:
            return "unknown", 0.0, "unknown"
        next_index = paragraph_index + 1
        if next_index >= len(paragraphs):
            return "unknown", 0.0, "unknown"
        narrative = QUOTE_RE.sub(" ", paragraphs[next_index]).strip()
        # ``小门童……随着她的视线`` and similar object-pronoun frames
        # refer back to the actor who spoke immediately before this paragraph.
        relation = re.search(
            r"(?:随着|跟着|顺着|看向|望向|朝向|转向|对着|冲着|向着)"
            r"(?P<pronoun>她|他)的?(?:视线|目光|脚步|动作|声音|方向|身影)",
            narrative,
        )
        if relation:
            pronoun = str(relation.group("pronoun") or "")
            return PRONOUN_GENDERS[pronoun], 0.97, "adjacent-pronoun-coreference"
        boundary = _boundary_actor(narrative)
        if boundary in PRONOUN_GENDERS:
            return (
                PRONOUN_GENDERS[boundary],
                0.95,
                "adjacent-pronoun-coreference",
            )
        return "unknown", 0.0, "unknown"

    for paragraph_index, quote_plan in paragraph_quote_plans.items():
        line = prepared_lines[paragraph_index]
        for match, intent in quote_plan:
            if not intent.dialogue:
                continue
            actor = (
                pronoun_actor_before_quote(line[:match.start()])
                or known_actor_before_quote(line[:match.start()])
            )
            if actor:
                paragraph_attributions[paragraph_index] = actor
                break

    def subject_object_speech_actor(fragment: str) -> tuple[str, str]:
        """Return the grammatical subject and addressee of ``X 对 Y 说道``.

        The generic nearest-name matcher otherwise selects ``Y`` and assigns
        the addressee's identity, gender, and voice to ``X``'s dialogue.
        """
        narrative = str(fragment or "").strip()
        object_match = OBJECT_SPEECH_RE.search(narrative)
        if not object_match:
            return "", ""
        object_name = str(object_match.group("object") or "").strip()
        subject_region = narrative[:object_match.start()]
        clauses = [
            clause.strip()
            for clause in re.split(r"[。！？!?；;，,]", subject_region)
            if clause.strip()
        ]
        for clause in reversed(clauses):
            direct = DIRECT_SUBJECT_OBJECT_SPEECH_RE.match(
                f"{clause}{narrative[object_match.start():]}"
            )
            if not direct:
                continue
            subject = str(direct.group("subject") or "").strip()
            if subject in {"我", "她", "他"} or plausible_speaker_name(
                subject, strong_attribution=True
            ):
                return subject, object_name
        known: dict[str, tuple[int, str]] = {}
        for alias, candidates in alias_index.items():
            if len(alias) < 2 or len(candidates) != 1:
                continue
            for match in re.finditer(re.escape(alias), subject_region):
                canonical = str(candidates[0]["canonical_name"])
                current = known.get(canonical)
                if current is None or match.start() > current[0]:
                    known[canonical] = (match.start(), alias)
        if len(known) == 1:
            return next(iter(known.values()))[1], object_name
        pronoun = re.match(r"^\s*(她|他)(?:又|正|仍|还|却|没|不|突然|忽然)", subject_region)
        if pronoun:
            return pronoun.group(1), object_name
        return "", object_name

    def response_source_actor(fragment: str) -> str:
        frame = RESPONSE_SOURCE_FRAME_RE.search(str(fragment or "").strip())
        if not frame:
            return ""
        body = str(frame.group("body") or "")
        actors: dict[str, str] = {}
        for alias, candidates in alias_index.items():
            if len(alias) < 2 or len(candidates) != 1 or alias not in body:
                continue
            canonical = str(candidates[0]["canonical_name"])
            if len(alias) > len(actors.get(canonical, "")):
                actors[canonical] = alias
        if len(actors) == 1:
            return next(iter(actors.values()))
        direct = RESPONSE_BODY_ACTOR_RE.match(body)
        if direct:
            name = str(direct.group("name") or "").strip()
            if plausible_speaker_name(name, strong_attribution=True):
                return name
        return ""

    def resolve_actor(
        raw_name: str,
        context: str,
        paragraph_index: int,
        offset: int,
    ) -> tuple[str, str, float, float]:
        del offset
        raw_name = str(raw_name or "").strip(" ·")
        if raw_name == "我":
            candidates = alias_index.get("我", [])
            if len(candidates) == 1:
                profile = candidates[0]
                return (
                    str(profile["canonical_name"]),
                    str(profile.get("gender") or "unknown"),
                    float(profile.get("gender_confidence") or 0.0),
                    0.99,
                )
            traits = _traits(PROTAGONIST_NAME, context)
            return (
                PROTAGONIST_NAME,
                str(traits["gender"]),
                float(traits["gender_confidence"]),
                0.98,
            )
        expected_gender = PRONOUN_GENDERS.get(raw_name)
        if expected_gender:
            previous_actor = (
                previous_narrative_actor(paragraph_index, expected_gender)
                or recent_narrative_actor(paragraph_index, expected_gender)
            )
            if previous_actor:
                profile = _known_profiles.get(previous_actor)
                if profile is not None:
                    return (
                        str(profile["canonical_name"]),
                        expected_gender,
                        max(float(profile.get("gender_confidence") or 0.0), 0.9),
                        0.95,
                    )
                if previous_actor in observed:
                    profile = observed[previous_actor]
                    return (
                        previous_actor,
                        expected_gender,
                        max(float(profile.get("gender_confidence") or 0.0), 0.9),
                        0.94,
                    )
            if (
                last_dialogue_speaker
                and last_dialogue_gender in {expected_gender, "unknown"}
                and paragraph_index - last_dialogue_paragraph <= 4
            ):
                return (
                    last_dialogue_speaker,
                    expected_gender,
                    1.0,
                    0.93,
                )
            recent: list[dict[str, Any]] = []
            seen: set[str] = set()
            for mention in reversed(mentions):
                if paragraph_index - int(mention["paragraph_index"]) > 3:
                    break
                if mention["gender"] != expected_gender or mention["name"] in seen:
                    continue
                recent.append(mention)
                seen.add(str(mention["name"]))
            if not recent:
                fingerprint_candidates = [
                    profile
                    for profile in _known_profiles.values()
                    if str(profile.get("gender") or "unknown") == expected_gender
                ]
                if len(fingerprint_candidates) != 1:
                    return "", expected_gender, 1.0, 0.0
                profile = fingerprint_candidates[0]
                return (
                    str(profile["canonical_name"]),
                    expected_gender,
                    max(float(profile.get("gender_confidence") or 0.0), 0.9),
                    0.76,
                )
            chosen = recent[0]
            distance = paragraph_index - int(chosen["paragraph_index"])
            resolution_confidence = max(0.72, 0.93 - distance * 0.06)
            if len(recent) > 1 and recent[1]["paragraph_index"] == chosen["paragraph_index"]:
                resolution_confidence = min(resolution_confidence, 0.78)
            return (
                str(chosen["name"]),
                expected_gender,
                max(float(chosen.get("confidence") or 0.0), 0.9),
                resolution_confidence,
            )

        prefix_profiles: dict[str, dict[str, Any]] = {}
        if len(alias_index.get(raw_name, [])) != 1:
            for alias, alias_candidates in alias_index.items():
                if len(alias) < 2 or len(alias_candidates) != 1:
                    continue
                if not raw_name.startswith(alias):
                    continue
                suffix = raw_name[len(alias):]
                if suffix not in KNOWN_ACTOR_ACTION_SUFFIXES | PREFIX_ACTION_TAILS:
                    continue
                profile = alias_candidates[0]
                prefix_profiles[str(profile["canonical_name"])] = profile
        if len(prefix_profiles) == 1:
            profile = next(iter(prefix_profiles.values()))
            return (
                str(profile["canonical_name"]),
                str(profile.get("gender") or "unknown"),
                float(profile.get("gender_confidence") or 0.0),
                0.97,
            )

        candidates = alias_index.get(raw_name, [])
        if len(candidates) == 1:
            profile = candidates[0]
            current = observed.get(str(profile["canonical_name"]))
            if current and str(current.get("gender") or "unknown") in {
                "female", "male"
            }:
                return (
                    str(profile["canonical_name"]),
                    str(current["gender"]),
                    float(current["gender_confidence"]),
                    0.99,
                )
            local_traits = _traits(str(profile["canonical_name"]), context)
            if (
                str(local_traits.get("gender_source") or "unknown")
                in STRONG_GENDER_SOURCES
                and str(local_traits.get("gender") or "unknown")
                in {"female", "male"}
            ):
                return (
                    str(profile["canonical_name"]),
                    str(local_traits["gender"]),
                    float(local_traits["gender_confidence"]),
                    0.99,
                )
            return (
                str(profile["canonical_name"]),
                str(profile.get("gender") or "unknown"),
                float(profile.get("gender_confidence") or 0.0),
                0.99,
            )
        raw_name = _clean_extracted_actor_name(raw_name)
        if not plausible_speaker_name(raw_name, strong_attribution=True):
            return "", "unknown", 0.0, 0.0
        traits = _traits(raw_name, context)
        return (
            raw_name,
            str(traits["gender"]),
            float(traits["gender_confidence"]),
            0.92,
        )

    def next_paragraph_actor(
        value: str,
        paragraph_index: int,
    ) -> tuple[str, float]:
        """Find one evidence-backed actor in a following narration paragraph.

        A standalone quote is common in Chinese web fiction.  Its speaker is
        often revealed only by the next paragraph's action or expression.
        Prefer a persisted MySQL character with an adjacent behavior cue, then
        merge matching pronoun evidence.  Competing actors remain unresolved.
        """
        if QUOTE_RE.search(value) or CHANNEL_COLON_RE.match(value):
            return "", 0.0
        narrative = UI_LABEL_RE.sub("", str(value or "")).strip()
        pronoun_backref = _pronoun_backref(narrative)
        if pronoun_backref:
            return pronoun_backref, 0.97
        evidence: list[tuple[str, float, int]] = []
        for alias, candidates in alias_index.items():
            if len(alias) < 2 or len(candidates) != 1:
                continue
            for match in re.finditer(re.escape(alias), narrative):
                tail = narrative[match.end():match.end() + 24]
                score = 0.93 if NARRATIVE_BEHAVIOR_RE.match(tail) else 0.78
                evidence.append((alias, score, match.start()))
        for match in NARRATIVE_ACTOR_RE.finditer(narrative):
            raw_name = str(match.group("name") or "").strip()
            score = 0.88 if raw_name not in {"我", "她", "他"} else 0.84
            evidence.append((raw_name, score, match.start("name")))
        for raw_name, offset in _action_actors(narrative):
            evidence.append((raw_name, 0.9, offset))
        boundary_actor = _boundary_actor(narrative)
        if boundary_actor:
            evidence.append((boundary_actor, 0.86, 0))

        resolved: dict[str, dict[str, Any]] = {}
        for raw_name, score, offset in evidence:
            actor, _gender, _gender_confidence, resolution_confidence = resolve_actor(
                raw_name,
                narrative,
                paragraph_index,
                offset,
            )
            if not actor:
                continue
            effective = min(score, resolution_confidence)
            current = resolved.get(actor)
            if current is None:
                resolved[actor] = {
                    "raw_name": raw_name,
                    "score": effective,
                    "evidence": 1,
                    "position": offset,
                }
                continue
            current["evidence"] = int(current["evidence"]) + 1
            if effective > float(current["score"]):
                current["raw_name"] = raw_name
                current["score"] = effective
                current["position"] = offset
            current["score"] = min(
                0.97,
                float(current["score"]) + 0.025,
            )
        ranked = sorted(
            resolved.items(),
            key=lambda item: (
                -float(item[1]["score"]),
                -int(item[1]["evidence"]),
                int(item[1]["position"]),
                item[0],
            ),
        )
        if not ranked:
            return "", 0.0
        if len(ranked) > 1 and (
            float(ranked[0][1]["score"]) - float(ranked[1][1]["score"]) < 0.08
        ):
            return "", 0.0
        return str(ranked[0][1]["raw_name"]), float(ranked[0][1]["score"])

    def observe(
        canonical: str,
        raw_name: str,
        context: str,
        gender: str,
        gender_confidence: float,
        source: str,
        attribution_confidence: float,
    ) -> None:
        if not canonical:
            return
        entry = observed.setdefault(canonical, {
            "contexts": [],
            "aliases": {canonical},
            "gender": "unknown",
            "gender_confidence": 0.0,
            "gender_source": "unknown",
            "gender_evidence": [],
            "external_gender_evidence": 0,
            "max_attribution_confidence": 0.0,
            "observation_count": 0,
        })
        entry["max_attribution_confidence"] = max(
            float(entry["max_attribution_confidence"]),
            float(attribution_confidence),
        )
        entry["observation_count"] = int(entry["observation_count"]) + 1
        entry["contexts"].append(context)
        polluted_known_suffix = (
            raw_name.startswith(canonical)
            and raw_name[len(canonical):]
            in KNOWN_ACTOR_ACTION_SUFFIXES | PREFIX_ACTION_TAILS
        )
        if (
            raw_name
            and raw_name not in PRONOUN_GENDERS
            and not polluted_known_suffix
        ):
            entry["aliases"].add(raw_name)
        evidence_name = raw_name if raw_name and raw_name not in PRONOUN_GENDERS else canonical
        local_traits = _traits(evidence_name, context)
        if raw_name in PRONOUN_GENDERS:
            evidence = (PRONOUN_GENDERS[raw_name], 1.0, "explicit-pronoun")
        elif str(local_traits["gender"]) in {"female", "male"}:
            evidence = (
                str(local_traits["gender"]),
                float(local_traits["gender_confidence"]),
                str(local_traits["gender_source"]),
            )
        elif gender in {"female", "male"}:
            evidence = (
                gender,
                gender_confidence,
                "persisted" if canonical in _known_profiles else "inferred",
            )
        else:
            evidence = None
        if evidence is not None:
            entry["gender_evidence"].append(evidence)
            selected_gender, selected_confidence, selected_source = (
                _select_gender_evidence(entry["gender_evidence"])
            )
            entry["gender"] = selected_gender
            entry["gender_confidence"] = selected_confidence
            entry["gender_source"] = selected_source
        if (
            source in EXTERNAL_GENDER_ATTRIBUTION_SOURCES
            and attribution_confidence >= 0.9
        ):
            entry["external_gender_evidence"] += 1

    def add_segments(
        value: str,
        *,
        speaker: str,
        kind: str,
        paragraph_index: int,
        context: str,
        source: str = "",
        confidence: float = 0.0,
        gender: str = "unknown",
        gender_confidence: float = 0.0,
        gender_source: str = "unknown",
        quote_intent: QuoteIntent | None = None,
    ) -> None:
        for chunk in split_tts_text(value):
            emotion, emotion_confidence, emotion_source = _infer_emotion(chunk, context)
            segment = {
                "text": chunk,
                "speaker": speaker,
                "kind": kind,
                "paragraph_index": paragraph_index,
                "speaker_source": source if speaker else "anonymous",
                "speaker_confidence": round(confidence if speaker else 0.0, 4),
                "emotion_hint": emotion,
                "emotion_confidence": emotion_confidence,
                "emotion_source": emotion_source,
            }
            if kind == "dialogue":
                segment.update({
                    "gender": gender if gender in {"female", "male"} else "unknown",
                    "gender_confidence": round(float(gender_confidence), 4),
                    "gender_source": gender_source,
                })
            if quote_intent is not None:
                segment.update({
                    "quote_intent": (
                        "dialogue" if quote_intent.dialogue else "narration"
                    ),
                    "quote_intent_confidence": round(
                        quote_intent.confidence, 4
                    ),
                    "quote_intent_source": quote_intent.source,
                })
            raw_segments.append(segment)

    for paragraph_index, line in enumerate(paragraphs):
        original_line = line
        channel_colon = CHANNEL_COLON_RE.match(line)
        if channel_colon:
            quote_matches = []
            quote_intents = []
            colon = channel_colon
        else:
            line = prepared_lines[paragraph_index]
            quote_plan = paragraph_quote_plans[paragraph_index]
            quote_matches = [match for match, _intent in quote_plan]
            quote_intents = [intent for _match, intent in quote_plan]
            colon = None if quote_matches else COLON_RE.match(line)
        if colon:
            raw_name = str(colon.group("name") or "")
            if structured_colon_field(raw_name, str(colon.group("speech") or "")):
                known_mentions(original_line, paragraph_index)
                add_segments(
                    original_line,
                    speaker="",
                    kind="narration",
                    paragraph_index=paragraph_index,
                    context=original_line,
                    source="structured-colon-field",
                )
                continue
            speaker, gender, gender_confidence, resolution_confidence = resolve_actor(
                raw_name, original_line, paragraph_index, 0
            )
            if speaker:
                observe(
                    speaker,
                    raw_name,
                    original_line,
                    gender,
                    gender_confidence,
                    "colon",
                    resolution_confidence,
                )
                remember(speaker, paragraph_index, 0, gender, gender_confidence)
            else:
                # Unknown colon prefixes are not safe speaker evidence.  Keep
                # the complete line as narration instead of silently dropping
                # the prefix and reading only the value after the colon.
                known_mentions(original_line, paragraph_index)
                add_segments(
                    original_line,
                    speaker="",
                    kind="narration",
                    paragraph_index=paragraph_index,
                    context=original_line,
                    source="unresolved-colon-field",
                )
                continue
            add_segments(
                colon.group("speech"),
                speaker=speaker,
                kind="dialogue",
                paragraph_index=paragraph_index,
                context=original_line,
                source="colon",
                confidence=min(0.99, resolution_confidence),
                gender=gender,
                gender_confidence=gender_confidence,
                gender_source=(
                    "explicit-pronoun"
                    if raw_name in PRONOUN_GENDERS
                    else str(_traits(speaker, original_line)["gender_source"])
                ),
            )
            continue

        if not quote_matches:
            known_mentions(line, paragraph_index)
            for raw_name, offset in _action_actors(line):
                speaker, gender, gender_confidence, _ = resolve_actor(
                    raw_name, line, paragraph_index, offset
                )
                if speaker and raw_name not in PRONOUN_GENDERS:
                    remember(speaker, paragraph_index, offset, gender, gender_confidence)
            add_segments(
                line,
                speaker="",
                kind="narration",
                paragraph_index=paragraph_index,
                context=line,
            )
            continue

        masked_line_chars = list(line)
        for match, intent in zip(quote_matches, quote_intents, strict=True):
            if intent.dialogue:
                continue
            masked_line_chars[match.start():match.end()] = " " * (
                match.end() - match.start()
            )
        attribution_line = "".join(masked_line_chars)

        if not any(intent.dialogue for intent in quote_intents):
            known_mentions(attribution_line, paragraph_index)
            for raw_name, offset in _action_actors(attribution_line):
                speaker, gender, gender_confidence, _ = resolve_actor(
                    raw_name, attribution_line, paragraph_index, offset
                )
                if speaker and raw_name not in PRONOUN_GENDERS:
                    remember(speaker, paragraph_index, offset, gender, gender_confidence)
            common_intent = quote_intents[0] if len(quote_intents) == 1 else None
            add_segments(
                line,
                speaker="",
                kind="narration",
                paragraph_index=paragraph_index,
                context=attribution_line,
                quote_intent=common_intent,
            )
            continue

        cursor = 0
        line_last_speaker = ""
        for match_index, (match, quote_intent) in enumerate(
            zip(quote_matches, quote_intents, strict=True)
        ):
            before = line[cursor:match.start()].strip()
            if not quote_intent.dialogue:
                add_segments(
                    f"{before}{match.group(0)}",
                    speaker="",
                    kind="narration",
                    paragraph_index=paragraph_index,
                    context=attribution_line,
                    quote_intent=quote_intent,
                )
                cursor = match.end()
                continue

            attribution_context = attribution_line
            prefix = attribution_line[:match.start()]
            known_mentions(prefix, paragraph_index)
            actor_candidates = _action_actors(prefix)
            between_actor = _boundary_actor(before) or _trailing_actor_cue(before)
            between_candidates = _action_actors(before)
            between_known_actor = unique_known_actor(before)
            between_quote_actor = known_actor_before_quote(before)
            for raw_actor, actor_offset in actor_candidates[:-1]:
                actor, actor_gender, actor_gender_confidence, _ = resolve_actor(
                    raw_actor, prefix, paragraph_index, actor_offset
                )
                if actor and raw_actor not in PRONOUN_GENDERS:
                    remember(
                        actor,
                        paragraph_index,
                        actor_offset,
                        actor_gender,
                        actor_gender_confidence,
                    )

            raw_name = ""
            source = ""
            base_confidence = 0.0
            response_actor = response_source_actor(prefix)
            subject_name, speech_object = subject_object_speech_actor(prefix)
            leading_speech_actor = _leading_explicit_speech_actor(prefix)
            explicit_identity_actor = EXPLICIT_IDENTITY_COLON_ACTOR_RE.search(prefix)
            pronoun_frame = PRONOUN_SPEECH_FRAME_RE.search(prefix)
            named_action_quote = NAMED_ACTION_QUOTE_RE.search(prefix)
            pronoun_action_actor = pronoun_actor_before_quote(prefix)
            known_quote_actor = known_actor_before_quote(prefix)
            expressive_speech = NAMED_EXPRESSIVE_SPEECH_RE.search(prefix)
            bare_dao_speaker = BARE_DAO_SPEAKER_RE.search(prefix)
            speaker_match = SPEAKER_RE.search(prefix)
            state_match = (
                STATE_COLON_ACTOR_RE.search(prefix)
                or DESCRIPTIVE_STATE_COLON_ACTOR_RE.search(prefix)
            )
            if response_actor:
                raw_name = response_actor
                source, base_confidence = "response-source-before", 0.99
            elif subject_name:
                raw_name = subject_name
                source, base_confidence = "speech-subject-before", 0.99
            elif leading_speech_actor:
                raw_name = leading_speech_actor
                source, base_confidence = "leading-speech-subject", 0.99
            elif explicit_identity_actor:
                raw_name = str(explicit_identity_actor.group("name") or "")
                source, base_confidence = "explicit-identity-before", 0.99
            elif pronoun_frame:
                raw_name = str(pronoun_frame.group("name") or "")
                source, base_confidence = "pronoun-speech-frame", 0.99
            elif pronoun_action_actor:
                raw_name = pronoun_action_actor
                source, base_confidence = "pronoun-action-before", 0.96
            elif known_quote_actor:
                raw_name = known_quote_actor
                source, base_confidence = "known-quote-before", 0.96
            elif speaker_match and str(speaker_match.group("name") or "") != speech_object:
                raw_name = str(speaker_match.group("name") or "")
                source, base_confidence = "speech-verb-before", 0.98
            elif bare_dao_speaker and str(
                bare_dao_speaker.group("name") or ""
            ) != speech_object:
                raw_name = str(bare_dao_speaker.group("name") or "")
                source, base_confidence = "speech-verb-before", 0.96
            elif expressive_speech:
                raw_name = str(expressive_speech.group("name") or "")
                source, base_confidence = "expressive-action-before", 0.96
            elif named_action_quote and plausible_speaker_name(
                str(named_action_quote.group("name") or ""),
                strong_attribution=True,
            ):
                # This is deliberately a fallback after known actors and
                # explicit speech verbs.  Its wider action grammar must never
                # manufacture a short name from the object clause before a
                # real speaker, such as ``见小梅……云知意解释道``.
                raw_name = str(named_action_quote.group("name") or "")
                source, base_confidence = "named-action-before", 0.92
            elif state_match:
                raw_name = str(state_match.group("name") or "")
                source, base_confidence = "state-before", 0.94
            elif (
                any(intent.dialogue for intent in quote_intents[:match_index])
                and between_actor
            ):
                raw_name = between_actor
                source, base_confidence = "action-between", 0.94
            elif (
                any(intent.dialogue for intent in quote_intents[:match_index])
                and between_quote_actor
            ):
                raw_name = between_quote_actor
                source, base_confidence = "known-action-between", 0.95
            elif (
                any(intent.dialogue for intent in quote_intents[:match_index])
                and between_candidates
            ):
                raw_name = between_candidates[-1][0]
                source, base_confidence = "action-between", 0.93
            elif (
                any(intent.dialogue for intent in quote_intents[:match_index])
                and between_known_actor
            ):
                raw_name = between_known_actor
                source, base_confidence = "known-action-between", 0.92
            elif actor_candidates:
                raw_name = actor_candidates[-1][0]
                source, base_confidence = "action-before", 0.93
            else:
                next_start = (
                    quote_matches[match_index + 1].start()
                    if match_index + 1 < len(quote_matches)
                    else len(line)
                )
                after_context = line[match.end():next_start]
                after_speaker = TRAILING_SPEECH_ATTRIBUTION_RE.match(after_context)
                boundary_actor = (
                    _pronoun_backref(after_context)
                    or _boundary_actor(after_context)
                    or _trailing_actor_cue(after_context)
                )
                after_candidates = _action_actors(after_context)
                after_known_actor = unique_known_actor(after_context)
                if after_speaker:
                    raw_name = str(after_speaker.group("name") or "")
                    source, base_confidence = "speech-verb-after", 0.98
                elif boundary_actor:
                    raw_name = boundary_actor
                    source = (
                        "pronoun-backref"
                        if boundary_actor in PRONOUN_GENDERS
                        and _pronoun_backref(after_context)
                        else "action-after"
                    )
                    base_confidence = 0.99 if source == "pronoun-backref" else 0.95
                elif after_candidates:
                    raw_name = after_candidates[0][0]
                    source, base_confidence = "action-after", 0.95
                elif after_known_actor:
                    raw_name = after_known_actor
                    source, base_confidence = "known-action-after", 0.92
            speech_text = _quote_speech(match)
            if raw_name and source == "action-after" and _dialogue_addresses(
                speech_text, raw_name
            ):
                raw_name, source, base_confidence = "", "", 0.0
            if (
                not raw_name
                and line_last_speaker
                and re.search(
                    SPEECH_VERB_PATTERN + r"[：:]\s*$",
                    prefix,
                )
            ):
                raw_name = line_last_speaker
                source, base_confidence = "same-paragraph-continuation", 0.91
            if not raw_name:
                prior_index = paragraph_index - 1
                if prior_index >= 0 and not QUOTE_RE.search(paragraphs[prior_index]):
                    prior_pronoun_frame = PRONOUN_SPEECH_FRAME_RE.search(
                        paragraphs[prior_index]
                    )
                    if prior_pronoun_frame:
                        raw_name = str(prior_pronoun_frame.group("name") or "")
                        source, base_confidence = (
                            "previous-paragraph-speech-frame",
                            0.98,
                        )
                        attribution_context = (
                            f"{paragraphs[prior_index]}\n{line}"
                        )
            if not raw_name:
                for distance in (1,):
                    raw_name = paragraph_attributions.get(paragraph_index + distance, "")
                    if raw_name:
                        source, base_confidence = "adjacent-attribution", 0.84
                        break
                    raw_name = paragraph_attributions.get(paragraph_index - distance, "")
                    if raw_name:
                        source, base_confidence = "adjacent-attribution", 0.82
                        break
            if (
                not raw_name
                and last_dialogue_speaker
                and continuity_supported(
                    last_dialogue_speaker,
                    last_dialogue_gender,
                    paragraph_index,
                )
            ):
                raw_name = last_dialogue_speaker
                source, base_confidence = "dialogue-continuity", 0.9
            if not raw_name:
                for distance in (2,):
                    forward_middle = (
                        paragraphs[paragraph_index + 1]
                        if paragraph_index + 1 < len(paragraphs)
                        else ""
                    )
                    forward_blocked = bool(
                        re.search(
                            r"(?:^|[。！？!?；;，,\s])[她他](?:的|又|却|仍|还|正|已|也|只|并|没|不|便|才|就|都)",
                            QUOTE_RE.sub(" ", forward_middle),
                        )
                    )
                    raw_name = (
                        ""
                        if forward_blocked
                        else paragraph_attributions.get(
                            paragraph_index + distance, ""
                        )
                    )
                    if raw_name:
                        source, base_confidence = "adjacent-attribution", 0.8
                        break
                    backward_middle = (
                        paragraphs[paragraph_index - 1]
                        if paragraph_index > 0
                        else ""
                    )
                    backward_blocked = bool(
                        re.search(
                            r"(?:^|[。！？!?；;，,\s])[她他](?:的|又|却|仍|还|正|已|也|只|并|没|不|便|才|就|都)",
                            QUOTE_RE.sub(" ", backward_middle),
                        )
                    )
                    raw_name = (
                        ""
                        if backward_blocked
                        else paragraph_attributions.get(
                            paragraph_index - distance, ""
                        )
                    )
                    if raw_name:
                        source, base_confidence = "adjacent-attribution", 0.78
                        break
            if not raw_name:
                for distance in (1, 2):
                    target_index = paragraph_index + distance
                    if target_index >= len(paragraphs):
                        break
                    raw_name, hint_confidence = next_paragraph_actor(
                        paragraphs[target_index],
                        target_index,
                    )
                    if raw_name:
                        source = "next-paragraph-context"
                        attribution_context = f"{line}\n{paragraphs[target_index]}"
                        base_confidence = max(
                            0.72,
                            hint_confidence - (distance - 1) * 0.08,
                        )
                        break

            if (
                raw_name
                and source in {
                    "action-after",
                    "adjacent-attribution",
                    "next-paragraph-context",
                }
                and dialogue_addresses_actor(speech_text, raw_name)
            ):
                raw_name, source, base_confidence = "", "", 0.0

            if (
                raw_name in PRONOUN_GENDERS
                and line_last_speaker
                and match_index > 0
            ):
                raw_name = line_last_speaker
                source, base_confidence = "same-paragraph-pronoun", 0.99

            speaker, gender, gender_confidence, resolution_confidence = resolve_actor(
                raw_name, line, paragraph_index, match.start()
            )
            if gender not in {"female", "male"}:
                local_gender, local_confidence, local_source = (
                    local_attribution_gender(raw_name, paragraph_index)
                )
                if local_gender in {"female", "male"}:
                    gender = local_gender
                    gender_confidence = local_confidence
                    gender_source = local_source
                fallback_gender, fallback_confidence, fallback_source = (
                    _anonymous_dialogue_gender(
                        speech_text, attribution_context
                    )
                )
                if (
                    gender not in {"female", "male"}
                    and fallback_gender in {"female", "male"}
                ):
                    gender = fallback_gender
                    gender_confidence = fallback_confidence
                    gender_source = fallback_source
                elif gender not in {"female", "male"}:
                    gender_source = "unknown"
            else:
                gender_source = (
                    "explicit-pronoun"
                    if raw_name in PRONOUN_GENDERS
                    else str(_traits(speaker, attribution_context)["gender_source"])
                    if speaker
                    else "inferred"
                )
            if (
                raw_name in PRONOUN_GENDERS
                and source in {
                    "pronoun-speech-frame",
                    "pronoun-action-before",
                    "pronoun-backref",
                    "next-paragraph-context",
                    "previous-paragraph-speech-frame",
                }
                and resolution_confidence < 0.8
            ):
                speaker = ""
                resolution_confidence = 0.0
            attribution_confidence = (
                min(base_confidence, resolution_confidence) if speaker else 0.0
            )
            if speaker:
                observe(
                    speaker,
                    raw_name,
                    attribution_context,
                    gender,
                    gender_confidence,
                    source,
                    attribution_confidence,
                )
                remember(
                    speaker,
                    paragraph_index,
                    match.start(),
                    gender,
                    gender_confidence,
                )
                line_last_speaker = speaker
                last_dialogue_speaker = speaker
                last_dialogue_gender = gender
                last_dialogue_paragraph = paragraph_index
            if before:
                add_segments(
                    before,
                    speaker="",
                    kind="narration",
                    paragraph_index=paragraph_index,
                    context=line,
                )
            add_segments(
                speech_text,
                speaker=speaker,
                kind="dialogue",
                paragraph_index=paragraph_index,
                context=attribution_context,
                source=source,
                confidence=attribution_confidence,
                gender=gender,
                gender_confidence=gender_confidence,
                gender_source=gender_source,
                quote_intent=quote_intent,
            )
            cursor = match.end()
        after = line[cursor:].strip()
        if after:
            add_segments(
                after,
                speaker="",
                kind="narration",
                paragraph_index=paragraph_index,
                context=line,
            )

    remaining = set(observed)
    speaker_map: dict[str, str] = {}
    grouped: list[tuple[str, set[str]]] = []
    for name in sorted(observed, key=lambda item: (-len(item), item)):
        if name not in remaining:
            continue
        related = {
            candidate
            for candidate in remaining
            if candidate == name
            or (
                min(len(name), len(candidate)) >= 2
                and (name.endswith(candidate) or candidate.endswith(name))
            )
        }
        # Prefer an existing current-revision identity, then a surname-valid
        # personal name, then stronger repeated attribution.  The old
        # longest-string rule promoted ``姓名+动作残片`` into the canonical
        # identity and made that pollution permanent.
        canonical = min(
            related,
            key=lambda item: (
                0 if item in _known_profiles else 1,
                0 if is_clean_chinese_name(item) else 1,
                -float(observed[item].get("max_attribution_confidence") or 0.0),
                -int(observed[item].get("observation_count") or 0),
                -len(item),
                item,
            ),
        )
        aliases = {
            alias
            for candidate in related
            for alias in observed[candidate]["aliases"]
        }
        if canonical != PROTAGONIST_NAME:
            aliases.add(canonical[-2:] if len(canonical) > 2 else canonical)
        for candidate in related:
            speaker_map[candidate] = canonical
            remaining.discard(candidate)
        grouped.append((canonical, aliases))

    for segment in raw_segments:
        if segment["speaker"] in speaker_map:
            segment["speaker"] = speaker_map[segment["speaker"]]

    characters = []
    for canonical, aliases in sorted(grouped):
        members = [
            name
            for name in observed
            if speaker_map.get(name) == canonical or name == canonical
        ]
        contexts = [
            context
            for name in members
            for context in observed[name]["contexts"]
        ]
        traits = _traits(canonical, "\n".join(contexts))
        gender, gender_confidence, gender_source = _select_gender_evidence([
            evidence
            for name in members
            for evidence in observed[name].get("gender_evidence", [])
        ])
        if gender != "unknown" or gender_source == "conflict":
            traits["gender"] = gender
            traits["gender_confidence"] = round(gender_confidence, 4)
            traits["gender_source"] = gender_source
        identity_confidence = max(
            float(observed[name].get("max_attribution_confidence") or 0.0)
            for name in members
        )
        character = {
            "canonical_name": canonical,
            "aliases": sorted(aliases, key=lambda item: (-len(item), item)),
            "_identity_confidence": identity_confidence,
            "_external_gender_lookup": (
                is_clean_chinese_name(canonical)
                and canonical not in GENERIC_ROLE_ALIASES
                and identity_confidence >= 0.9
            ),
            **traits,
        }
        character["_scanner_identity_verified"] = bool(
            identity_confidence >= 0.92
            and plausible_speaker_name(canonical, strong_attribution=True)
            and _clean_extracted_actor_name(canonical) == canonical
            and (
                is_clean_chinese_name(canonical)
                or len(canonical) == 2
                or "·" in canonical
            )
        )
        characters.append(character)
    return characters, raw_segments


def deterministic_voice(catalog_id: int, character: dict[str, Any]) -> str:
    gender = str(character.get("gender") or "unknown")
    pool = _voice_pool(gender)
    if not pool:
        return ""
    seed = sha256(f"{catalog_id}\0{character['canonical_name']}".encode()).digest()
    return pool[int.from_bytes(seed[:2], "big") % len(pool)]


def _deterministic_voice_avoiding(
    catalog_id: int, character: dict[str, Any], reserved_voice: str,
) -> str:
    preferred = deterministic_voice(catalog_id, character)
    pool = _voice_pool(str(character.get("gender") or "unknown"))
    if preferred != reserved_voice or len(pool) <= 1:
        return preferred
    choices = [voice for voice in pool if voice != reserved_voice]
    seed = sha256(
        f"{catalog_id}\0{character['canonical_name']}\0reserved".encode()
    ).digest()
    return choices[int.from_bytes(seed[:2], "big") % len(choices)]


def rendition_voice(catalog_id: int, character: dict[str, Any], narrator: str) -> str:
    """Return the book-level fixed character voice.

    ``narrator`` remains in the signature for compatibility, but a user's
    narrator preference must never remap a named character from chapter to
    chapter.  Narration is the flexible channel and is deconflicted separately.
    """
    del narrator
    gender = str(character.get("gender") or "unknown")
    pool = _voice_pool(gender)
    if not pool:
        return ""
    voice = str(character.get("voice_key") or "")
    if not voice or voice not in pool:
        voice = deterministic_voice(catalog_id, character)
    return voice


def effective_narrator_voice(
    catalog_id: int,
    requested_narrator: str,
    cast: dict[str, dict[str, Any]],
) -> str:
    """Keep the user's narration choice immutable.

    A locked book-level actor may legitimately collide with a later user's
    narrator preference.  That cannot silently rewrite the explicit setting;
    only new/unlocked actors are deconflicted while binding.
    """
    del catalog_id, cast
    return requested_narrator if requested_narrator in CAST_VOICES else "mocheng"


def reusable_historical_voice(
    historical: dict[str, Any],
    *,
    canonical_name: str,
    gender: str,
    verified: bool,
) -> str:
    """Keep only an exact, same-gender fixed voice across engine revisions."""
    voice = str(historical.get("voice_key") or "")
    if not (
        verified
        and str(historical.get("canonical_name") or "") == canonical_name
        and str(historical.get("gender") or "unknown") == gender
        and bool(historical.get("voice_locked"))
        and voice in _voice_pool(gender)
    ):
        return ""
    return voice


def apply_authoritative_ai_gender(
    catalog_id: int, character: dict[str, Any],
) -> bool:
    """Apply a completed full-book AI verdict to a persisted cast row."""
    ai_gender = str(character.get("ai_review_gender") or "unknown")
    ai_confidence = float(character.get("ai_review_confidence") or 0.0)
    if ai_gender not in {"female", "male"} or ai_confidence < 0.8:
        return False
    character["gender"] = ai_gender
    character["gender_confidence"] = ai_confidence
    character["voice_locked"] = 1
    if str(character.get("voice_key") or "") not in _voice_pool(ai_gender):
        character["voice_key"] = deterministic_voice(catalog_id, character)
    character["_ai_gender_verified"] = True
    return True


@dataclass
class AudiobookSession:
    session_id: str
    owner_key: str
    manifests: dict[str, dict[str, Any]]
    current_chapter_id: int
    settings: dict[str, Any]
    device_key: str = ""
    created_at: float = field(default_factory=time.time)
    cancelled: bool = False


def has_spoken_content(text: str) -> bool:
    """Return whether Edge TTS can produce speech for this segment."""
    return bool(SPOKEN_CONTENT_RE.search(str(text or "")))


def mp3_duration_ms(audio: bytes) -> int:
    """Measure MPEG audio frames without spawning ffprobe or trusting byte rate."""
    if len(audio) < 4:
        return 0
    offset = 0
    if audio.startswith(b"ID3") and len(audio) >= 10:
        size = 0
        for value in audio[6:10]:
            size = (size << 7) | (value & 0x7F)
        offset = min(len(audio), 10 + size)
    total_seconds = 0.0
    frames = 0
    while offset + 4 <= len(audio):
        header = int.from_bytes(audio[offset : offset + 4], "big")
        if header & 0xFFE00000 != 0xFFE00000:
            offset += 1
            continue
        version_bits = (header >> 19) & 0x3
        layer_bits = (header >> 17) & 0x3
        bitrate_index = (header >> 12) & 0xF
        sample_index = (header >> 10) & 0x3
        padding = (header >> 9) & 0x1
        if version_bits == 1 or layer_bits != 1 or bitrate_index in {0, 15} or sample_index == 3:
            offset += 1
            continue
        if version_bits == 3:
            bitrates = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
            sample_rate = (44100, 48000, 32000)[sample_index]
            samples_per_frame = 1152
            frame_size = (144000 * bitrates[bitrate_index]) // sample_rate + padding
        else:
            bitrates = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
            base_rate = (44100, 48000, 32000)[sample_index]
            sample_rate = base_rate // (2 if version_bits == 2 else 4)
            samples_per_frame = 576
            frame_size = (72000 * bitrates[bitrate_index]) // sample_rate + padding
        if frame_size <= 4 or offset + frame_size > len(audio):
            break
        total_seconds += samples_per_frame / sample_rate
        frames += 1
        offset += frame_size
    return max(0, round(total_seconds * 1000)) if frames else 0


class AudiobookService:
    def __init__(self, repository: LibraryRepository):
        self.repository = repository
        self._sessions: dict[str, AudiobookSession] = {}
        self._lock = threading.RLock()
        self._gender_guess_enabled = os.getenv(
            "OOHSTORY_GENDER_GUESS_ENABLED", ""
        ).strip().casefold() in {"1", "true", "yes"}
        self._gender_guess_threshold = float(
            os.getenv("OOHSTORY_GENDER_GUESS_CONFIDENCE_THRESHOLD", "0.7")
        )
        self._gender_cache: GenderGuessCache | None = None
        self._gender_client: GenderGuessClient | None = None
        if self._gender_guess_enabled:
            mysql = getattr(repository, "_mysql", None)
            if mysql is not None:
                self._gender_cache = GenderGuessCache(mysql.pool)
            self._gender_client = GenderGuessClient()
        self._cast_prewarm = CastPrewarmManager(
            repository,
            analyzer=analyze_chapter,
            existing_cast=lambda catalog_id: self._existing_cast(
                catalog_id, trusted_only=False, published_only=False
            ),
            resolve_cast=lambda catalog_id, characters: self._cast(
                catalog_id, characters, allow_new_identities=True
            ),
            gender_cache=self._gender_cache,
            gender_client=self._gender_client,
            gender_guess_enabled=self._gender_guess_enabled,
            gender_guess_threshold=self._gender_guess_threshold,
        )
        self._cast_prewarm.start()

    def _catalog_id(self, book_id: str) -> int:
        mysql = getattr(self.repository, "_mysql", None)
        if mysql is None:
            return int.from_bytes(sha256(book_id.encode()).digest()[:8], "big") & 0x7FFF_FFFF
        row = mysql.get_book(_decode_public_id(book_id))
        if not row:
            raise KeyError("book not found")
        return int(row["catalog_id"])

    def _existing_cast(
        self,
        catalog_id: int,
        *,
        trusted_only: bool = True,
        published_only: bool = True,
    ) -> list[dict[str, Any]]:
        mysql = getattr(self.repository, "_mysql", None)
        if mysql is None:
            return []
        current_scan_revision = True
        with mysql.pool.connection() as connection:
            with connection.cursor() as cursor:
                if published_only:
                    cursor.execute(
                        "SELECT cast_json FROM audiobook_cast_snapshots "
                        "WHERE catalog_id=%s AND engine_version=%s",
                        (
                            catalog_id,
                            os.getenv("OOHSTORY_CAST_ENGINE_VERSION", ENGINE_VERSION),
                        ),
                    )
                    snapshot = cursor.fetchone()
                    if not snapshot:
                        # A semantic engine upgrade must not erase the last
                        # published cast while the new full-book scan is still
                        # running.  The snapshot table is catalog-scoped, so
                        # the previous engine's immutable cast is the only
                        # safe transition fallback until the replacement is
                        # atomically published.
                        cursor.execute(
                            "SELECT cast_json FROM audiobook_cast_snapshots "
                            "WHERE catalog_id=%s",
                            (catalog_id,),
                        )
                        snapshot = cursor.fetchone()
                        current_scan_revision = False
                    if snapshot:
                        rows = snapshot.get("cast_json") or []
                        if not isinstance(rows, list):
                            rows = json.loads(str(rows or "[]"))
                    else:
                        published_only = False
                if not published_only:
                    cursor.execute(
                        "SELECT v.character_key,v.canonical_name,v.aliases,v.gender,v.age_group,v.tone,"
                        "v.gender_confidence,v.age_confidence,v.tone_confidence,v.voice_key,v.voice_locked,"
                        "p.role_type,p.dialogue_count,p.chapter_count,"
                        "r.gender AS ai_review_gender,r.role_type AS ai_review_role_type,"
                        "r.confidence AS ai_review_confidence "
                        "FROM audiobook_character_voices v "
                        "INNER JOIN audiobook_character_profiles p "
                        "ON p.catalog_id=v.catalog_id AND p.character_key=v.character_key "
                        "INNER JOIN audiobook_cast_scan_jobs j "
                        "ON j.catalog_id=p.catalog_id AND j.content_revision=p.scan_revision "
                        "LEFT JOIN audiobook_cast_ai_reviews r "
                        "ON r.catalog_id=p.catalog_id AND r.character_key=p.character_key "
                        "AND r.scan_revision=("
                        "SELECT r2.scan_revision FROM audiobook_cast_ai_reviews r2 "
                        "WHERE r2.catalog_id=p.catalog_id "
                        "AND r2.character_key=p.character_key "
                        "AND r2.confidence>=0.8 "
                        "ORDER BY r2.created_at DESC LIMIT 1) "
                        "WHERE v.catalog_id=%s AND j.engine_version=%s",
                        (
                            catalog_id,
                            os.getenv("OOHSTORY_CAST_ENGINE_VERSION", ENGINE_VERSION),
                        ),
                    )
                    rows = list(cursor.fetchall())
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            aliases = item.get("aliases")
            if not isinstance(aliases, list):
                try:
                    aliases = json.loads(str(aliases or "[]"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    aliases = []
            item["aliases"] = [
                str(alias).strip() for alias in aliases if str(alias).strip()
            ]
            cleaned = _clean_persisted_actor_name(
                str(item.get("canonical_name") or ""), item["aliases"]
            )
            if cleaned != str(item.get("canonical_name") or ""):
                item["_stored_canonical_name"] = str(
                    item.get("canonical_name") or ""
                )
                item["canonical_name"] = cleaned
            if not cleaned or not plausible_speaker_name(
                cleaned, strong_attribution=True
            ):
                continue
            apply_authoritative_ai_gender(catalog_id, item)
            item["_current_scan_revision"] = current_scan_revision
            result.append(item)
        return trusted_persisted_cast(result) if trusted_only else result

    def _cast_revision(self, catalog_id: int) -> int:
        """Return the revision of the atomically published cast snapshot."""
        mysql = getattr(self.repository, "_mysql", None)
        if mysql is None:
            return 0
        with mysql.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT revision FROM audiobook_cast_snapshots "
                    "WHERE catalog_id=%s AND engine_version=%s",
                    (
                        catalog_id,
                        os.getenv("OOHSTORY_CAST_ENGINE_VERSION", ENGINE_VERSION),
                    ),
                )
                row = cursor.fetchone()
                if row:
                    return int(row["revision"])
                cursor.execute(
                    "SELECT revision FROM audiobook_cast_snapshots "
                    "WHERE catalog_id=%s",
                    (catalog_id,),
                )
                row = cursor.fetchone()
                if row:
                    # Keep manifests stable on the last published cast while
                    # a replacement engine scans.  Once the new snapshot is
                    # published its revision takes over atomically.
                    return int(row["revision"])
                cursor.execute(
                    "SELECT status,processed_chapters FROM audiobook_cast_scan_jobs "
                    "WHERE catalog_id=%s AND engine_version=%s "
                    "AND status IN ('pending','running') "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (
                        catalog_id,
                        os.getenv("OOHSTORY_CAST_ENGINE_VERSION", ENGINE_VERSION),
                    ),
                )
                row = cursor.fetchone()
        if not row:
            return 0
        return 1_000_000_000 + int(row.get("processed_chapters") or 0)

    def _enrich_gender(
        self,
        characters: list[dict[str, Any]],
        existing_index: dict[str, dict[str, Any]],
    ) -> None:
        """Read provider evidence for every qualified name without HTTP calls."""
        if self._gender_cache is None:
            return
        for character in characters:
            name = character["canonical_name"]
            if name == PROTAGONIST_NAME:
                continue
            if not bool(character.get("_external_gender_lookup")):
                continue
            if not is_clean_chinese_name(name):
                continue
            try:
                result = lookup_gender_cache_only(name, cache=self._gender_cache)
            except Exception:
                continue
            if not (
                result
                and result["gender"] in ("male", "female")
                and float(result.get("confidence") or 0)
                >= self._gender_guess_threshold
            ):
                continue
            _merge_external_gender_evidence(character, result)
            existing = existing_index.get(name)
            if (
                existing
                and str(existing.get("gender") or "unknown")
                in {"female", "male"}
                and str(existing.get("gender")) != str(character.get("gender"))
            ):
                character["_gender_review_required"] = True

    def _playback_cast(
        self,
        catalog_id: int,
        characters: list[dict[str, Any]],
        published_cast: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Resolve a chapter against one immutable snapshot without DB writes."""
        by_name = {
            str(item.get("canonical_name") or ""): item
            for item in published_cast
            if str(item.get("canonical_name") or "")
        }
        by_alias: dict[str, list[dict[str, Any]]] = {}
        for item in published_cast:
            for alias in item.get("aliases") or []:
                by_alias.setdefault(str(alias), []).append(item)
        allow_ephemeral = getattr(self.repository, "_mysql", None) is None
        result: dict[str, dict[str, Any]] = {}
        for character in characters:
            name = str(character.get("canonical_name") or "")
            matched = by_name.get(name)
            if matched is None:
                candidates = by_alias.get(name, [])
                if len(candidates) == 1:
                    matched = candidates[0]
            if matched is None and not allow_ephemeral:
                continue
            source = matched or character
            candidate_gender = str(character.get("gender") or "unknown")
            source_gender = str(source.get("gender") or "unknown")
            if (
                matched is not None
                and candidate_gender in {"female", "male"}
                and candidate_gender != source_gender
                and _character_gender_verified(character)
            ):
                source = {
                    **source,
                    "gender": candidate_gender,
                    "gender_confidence": float(
                        character.get("gender_confidence") or 0.0
                    ),
                    "gender_source": str(
                        character.get("gender_source") or "chapter-evidence"
                    ),
                    "voice_key": "",
                    "voice_locked": 0,
                }
            gender = str(source.get("gender") or "unknown")
            voice = str(source.get("voice_key") or "")
            if voice not in _voice_pool(gender):
                voice = deterministic_voice(
                    catalog_id, {**character, "gender": gender},
                )
            result[name] = {
                **character,
                **source,
                "canonical_name": str(source.get("canonical_name") or name),
                "gender": gender,
                "gender_confidence": float(
                    source.get("gender_confidence") or 0.0
                ),
                "gender_source": str(
                    source.get("gender_source") or "published_snapshot"
                ),
                "voice_key": voice,
                "voice_locked": int(bool(source.get("voice_locked"))),
                "unknown_fallback": gender == "unknown" or not bool(voice),
            }
        return result

    def _cast(
        self,
        catalog_id: int,
        characters: list[dict[str, Any]],
        reserved_voice: str = "",
        *,
        allow_new_identities: bool = False,
    ) -> dict[str, dict[str, Any]]:
        mysql = getattr(self.repository, "_mysql", None)
        resolved: dict[str, dict[str, Any]] = {}
        if mysql is not None and characters:
            with mysql.pool.transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT IGNORE INTO audiobook_cast_revisions "
                        "(catalog_id,revision) VALUES (%s,0)",
                        (catalog_id,),
                    )
                    cursor.execute(
                        "SELECT revision FROM audiobook_cast_revisions "
                        "WHERE catalog_id=%s FOR UPDATE",
                        (catalog_id,),
                    )
                    cursor.fetchone()
                    cursor.execute(
                        "SELECT v.character_key,v.canonical_name,v.aliases,v.gender,v.age_group,v.tone,"
                        "v.gender_confidence,v.age_confidence,v.tone_confidence,v.voice_key,v.voice_locked,"
                        "p.role_type,p.dialogue_count,p.chapter_count,"
                        "r.gender AS ai_review_gender,r.confidence AS ai_review_confidence "
                        "FROM audiobook_character_voices v "
                        "INNER JOIN audiobook_character_profiles p "
                        "ON p.catalog_id=v.catalog_id AND p.character_key=v.character_key "
                        "INNER JOIN audiobook_cast_scan_jobs j "
                        "ON j.catalog_id=p.catalog_id AND j.content_revision=p.scan_revision "
                        "LEFT JOIN audiobook_cast_ai_reviews r "
                        "ON r.catalog_id=p.catalog_id AND r.character_key=p.character_key "
                        "AND r.scan_revision=("
                        "SELECT r2.scan_revision FROM audiobook_cast_ai_reviews r2 "
                        "WHERE r2.catalog_id=p.catalog_id "
                        "AND r2.character_key=p.character_key "
                        "AND r2.confidence>=0.8 "
                        "ORDER BY r2.created_at DESC LIMIT 1) "
                        "WHERE v.catalog_id=%s AND j.engine_version=%s FOR UPDATE",
                        (
                            catalog_id,
                            os.getenv("OOHSTORY_CAST_ENGINE_VERSION", ENGINE_VERSION),
                        ),
                    )
                    existing: list[dict[str, Any]] = []
                    ai_corrections: list[dict[str, Any]] = []
                    for row in cursor.fetchall():
                        item = dict(row)
                        aliases = item.get("aliases")
                        if not isinstance(aliases, list):
                            try:
                                aliases = json.loads(str(aliases or "[]"))
                            except (TypeError, ValueError, json.JSONDecodeError):
                                aliases = []
                        item["aliases"] = [str(alias) for alias in aliases if str(alias).strip()]
                        cleaned = _clean_persisted_actor_name(
                            str(item.get("canonical_name") or ""), item["aliases"]
                        )
                        if cleaned != str(item.get("canonical_name") or ""):
                            item["_stored_canonical_name"] = str(
                                item.get("canonical_name") or ""
                            )
                            item["canonical_name"] = cleaned
                        if not plausible_speaker_name(
                            str(item.get("canonical_name") or ""),
                            strong_attribution=True,
                        ):
                            continue
                        if apply_authoritative_ai_gender(catalog_id, item):
                            ai_corrections.append(item)
                        existing.append(item)

                    if not allow_new_identities:
                        existing = trusted_persisted_cast(existing)

                    canonical_index = {
                        str(item["canonical_name"]): item for item in existing
                    }
                    alias_candidates: dict[str, list[dict[str, Any]]] = {}
                    for item in ai_corrections:
                        cursor.execute(
                            "UPDATE audiobook_character_voices SET gender=%s,"
                            "gender_confidence=%s,voice_key=%s,voice_locked=1 "
                            "WHERE catalog_id=%s AND character_key=%s AND "
                            "(gender<>%s OR gender_confidence<>%s OR voice_key<>%s "
                            "OR voice_locked<>1)",
                            (
                                item["gender"], item["gender_confidence"],
                                item["voice_key"], catalog_id, item["character_key"],
                                item["gender"], item["gender_confidence"],
                                item["voice_key"],
                            ),
                        )
                    for item in existing:
                        for alias in item["aliases"]:
                            alias_candidates.setdefault(alias, []).append(item)

                    def available_voice(
                        character: dict[str, Any], gender: str, *, salt: str,
                    ) -> str:
                        pool = _voice_pool(gender)
                        if not pool:
                            return ""
                        occupied = {
                            str(item.get("voice_key") or "")
                            for item in existing
                            if bool(item.get("voice_locked"))
                            and (
                                str(item.get("role_type") or "")
                                in {"protagonist", "supporting"}
                                or int(item.get("chapter_count") or 0) >= 3
                                or int(item.get("dialogue_count") or 0) >= 5
                            )
                            and str(item.get("gender") or "unknown") == gender
                        }
                        occupied.update(
                            str(item.get("voice_key") or "")
                            for item in resolved.values()
                            if str(item.get("gender") or "unknown") == gender
                        )
                        if reserved_voice in pool:
                            occupied.add(reserved_voice)
                        preferred = deterministic_voice(catalog_id, character)
                        if preferred and preferred not in occupied:
                            return preferred
                        choices = [voice for voice in pool if voice not in occupied]
                        if not choices:
                            non_narrator = [
                                voice for voice in pool if voice != reserved_voice
                            ]
                            return non_narrator[0] if non_narrator else preferred
                        seed = sha256(
                            f"{catalog_id}\0{character['canonical_name']}\0{salt}".encode()
                        ).digest()
                        return choices[int.from_bytes(seed[:2], "big") % len(choices)]

                    for character in characters:
                        name = character["canonical_name"]
                        matched = canonical_index.get(name)
                        if matched is None:
                            candidates = alias_candidates.get(name, [])
                            if len(candidates) == 1:
                                matched = candidates[0]
                        if matched is not None:
                            aliases = sorted(
                                set(matched["aliases"]) | set(character["aliases"]) | {name},
                                key=lambda item: (-len(item), item),
                            )
                            candidate_gender = str(
                                character.get("gender") or "unknown"
                            )
                            persisted_gender = str(
                                matched.get("gender") or "unknown"
                            )
                            review_required = not bool(
                                matched.get("_ai_gender_verified")
                            ) and (
                                bool(character.get("_gender_review_required"))
                                or bool(
                                    candidate_gender in {"male", "female"}
                                    and persisted_gender in {"male", "female"}
                                    and candidate_gender != persisted_gender
                                    and _gender_source_is_verified(
                                        str(character.get("gender_source") or "unknown")
                                    )
                                )
                            )
                            if review_required:
                                character["_gender_review_required"] = True
                            gender, gender_confidence, _gender_changed = (
                                reconcile_character_gender(matched, character)
                            )
                            voice_character = {
                                **character,
                                "canonical_name": str(matched["canonical_name"]),
                                "gender": gender,
                            }
                            voice = str(matched.get("voice_key") or "")
                            voice_locked = bool(matched.get("voice_locked"))
                            if review_required:
                                # Keep the stored value recoverable, but remove
                                # its trusted-lock status so the AI worker can
                                # arbitrate the conflict.  The current manifest
                                # uses the stronger chapter evidence below.
                                voice_locked = False
                            elif (
                                gender in {"female", "male"}
                                and float(gender_confidence or 0.0) >= 0.7
                                and voice not in _voice_pool(gender)
                            ):
                                voice_character["gender"] = gender
                                voice = available_voice(
                                    voice_character, gender, salt="repair-existing"
                                )
                                voice_locked = True
                            elif not voice_locked and _character_gender_verified(character):
                                gender = candidate_gender
                                gender_confidence = float(
                                    character.get("gender_confidence") or 0.0
                                )
                                voice_character["gender"] = gender
                                voice = available_voice(
                                    voice_character, gender, salt="verified-existing"
                                )
                                voice_locked = True
                            cursor.execute(
                                "UPDATE audiobook_character_voices SET canonical_name=%s,aliases=%s,gender=%s,"
                                "gender_confidence=%s,voice_key=%s,voice_locked=%s "
                                "WHERE catalog_id=%s AND character_key=%s",
                                (str(matched["canonical_name"]),
                                 json.dumps(aliases, ensure_ascii=False), gender,
                                 gender_confidence, voice, int(voice_locked), catalog_id,
                                 matched["character_key"]),
                            )
                            matched.update({
                                "aliases": aliases,
                                "gender": gender,
                                "gender_confidence": gender_confidence,
                                "voice_key": voice,
                                "voice_locked": int(voice_locked),
                            })
                            resolved[name] = matched
                            continue

                        if (
                            name != PROTAGONIST_NAME
                            and not (
                                (
                                    bool(character.get("_external_gender_lookup"))
                                    and plausible_speaker_name(
                                        name, strong_attribution=True
                                    )
                                    and (
                                        is_clean_chinese_name(name)
                                        or name in GENERIC_ROLE_ALIASES
                                    )
                                )
                                or (
                                    allow_new_identities
                                    and bool(
                                        character.get("_scanner_identity_verified")
                                    )
                                    and 2 <= len(name) <= 4
                                    and name not in SUSPICIOUS_ACTOR_NAMES
                                )
                            )
                        ):
                            continue
                        if not allow_new_identities:
                            continue

                        key = sha256(name.encode()).digest()
                        verified = _character_gender_verified(character)
                        gender = (
                            str(character.get("gender") or "unknown")
                            if verified else "unknown"
                        )
                        gender_confidence = (
                            float(character.get("gender_confidence") or 0.0)
                            if verified else 0.0
                        )
                        # Engine revisions deliberately hide historical profile
                        # statistics, but a verified identity must keep its
                        # already-locked voice across a rescan.  Reuse only an
                        # exact-name, same-gender lock; conflicts remain
                        # unlocked so the review pipeline can repair them.
                        cursor.execute(
                            "SELECT canonical_name,gender,gender_confidence,"
                            "voice_key,voice_locked "
                            "FROM audiobook_character_voices "
                            "WHERE catalog_id=%s AND character_key=%s FOR UPDATE",
                            (catalog_id, key),
                        )
                        historical = cursor.fetchone() or {}
                        voice = (
                            available_voice(
                                {**character, "gender": gender},
                                gender,
                                salt="verified-new",
                            )
                            if verified else ""
                        )
                        voice_locked = int(verified)
                        historical_voice = reusable_historical_voice(
                            historical,
                            canonical_name=name,
                            gender=gender,
                            verified=verified,
                        )
                        if historical_voice:
                            voice = historical_voice
                            voice_locked = 1
                        cursor.execute(
                            "INSERT INTO audiobook_character_voices "
                            "(catalog_id,character_key,canonical_name,aliases,gender,age_group,tone,"
                            "gender_confidence,age_confidence,tone_confidence,voice_key,voice_locked) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                            "ON DUPLICATE KEY UPDATE canonical_name=VALUES(canonical_name),"
                            "aliases=VALUES(aliases),gender=VALUES(gender),"
                            "gender_confidence=VALUES(gender_confidence),"
                            "voice_key=VALUES(voice_key),voice_locked=VALUES(voice_locked)",
                            (catalog_id, key, name, json.dumps(character["aliases"], ensure_ascii=False),
                             gender, character["age_group"], character["tone"],
                             gender_confidence, character["age_confidence"], character["tone_confidence"], voice,
                             voice_locked),
                        )
                        inserted = {
                            **character,
                            "character_key": key,
                            "voice_key": voice,
                            "voice_locked": voice_locked,
                            "gender": gender,
                            "gender_confidence": gender_confidence,
                            "role_type": "unclassified",
                            "dialogue_count": 0,
                            "chapter_count": 0,
                        }
                        existing.append(inserted)
                        canonical_index[name] = inserted
                        for alias in character["aliases"]:
                            alias_candidates.setdefault(alias, []).append(inserted)
                        resolved[name] = inserted

                    # A fixed voice is book-level state, but historic data may
                    # contain two verified actors who actually meet in this
                    # chapter with the same voice.  Repair the weaker binding
                    # once, persist it, and leave it stable thereafter.
                    unique_scene: dict[bytes, dict[str, Any]] = {}
                    for item in resolved.values():
                        raw_key = item.get("character_key")
                        if isinstance(raw_key, memoryview):
                            raw_key = raw_key.tobytes()
                        if isinstance(raw_key, (bytes, bytearray)):
                            unique_scene[bytes(raw_key)] = item
                    ranked_scene = [] if allow_new_identities else sorted(
                        unique_scene.values(),
                        key=lambda item: (
                            0 if str(item.get("role_type") or "") == "protagonist"
                            else 1 if str(item.get("role_type") or "") == "supporting"
                            else 2,
                            -int(item.get("chapter_count") or 0),
                            -int(item.get("dialogue_count") or 0),
                            str(item.get("canonical_name") or ""),
                        ),
                    )
                    scene_used: dict[str, set[str]] = {
                        "female": set(), "male": set(),
                    }
                    for item in ranked_scene:
                        gender = str(item.get("gender") or "unknown")
                        voice = str(item.get("voice_key") or "")
                        pool = _voice_pool(gender)
                        if not pool or not bool(item.get("voice_locked")):
                            continue
                        if voice not in scene_used[gender]:
                            scene_used[gender].add(voice)
                            continue
                        choices = [candidate for candidate in pool if candidate not in scene_used[gender]]
                        if not choices:
                            continue
                        seed = sha256(
                            f"{catalog_id}\0{item['canonical_name']}\0scene".encode()
                        ).digest()
                        replacement = choices[
                            int.from_bytes(seed[:2], "big") % len(choices)
                        ]
                        cursor.execute(
                            "UPDATE audiobook_character_voices SET voice_key=%s "
                            "WHERE catalog_id=%s AND character_key=%s AND voice_locked=1",
                            (replacement, catalog_id, item["character_key"]),
                        )
                        item["voice_key"] = replacement
                        scene_used[gender].add(replacement)
        cast: dict[str, dict[str, Any]] = {}
        for character in characters:
            name = character["canonical_name"]
            if mysql is not None and name not in resolved:
                # A new book must be listenable immediately while its full
                # scan waits in the global queue.  Keep a strong current-
                # chapter identity in this immutable manifest only; do not
                # persist or lock it until the scanner validates the book.
                ephemeral_identity = bool(
                    name == PROTAGONIST_NAME
                    or (
                        (
                            bool(character.get("_external_gender_lookup"))
                            or bool(character.get("_scanner_identity_verified"))
                        )
                        and plausible_speaker_name(
                            name, strong_attribution=True
                        )
                        and _clean_extracted_actor_name(name) == name
                        and CastPrewarmManager._plausible_persisted_name(name)
                    )
                )
                if not ephemeral_identity:
                    continue
            persisted = resolved.get(name, {})
            review_override = bool(character.get("_gender_review_required"))
            effective_gender = str(
                character.get("gender")
                if review_override
                else persisted.get("gender") or character["gender"]
            )
            effective_confidence = float(
                character.get("gender_confidence")
                if review_override
                else persisted.get("gender_confidence")
                if persisted.get("gender_confidence") is not None
                else character.get("gender_confidence") or 0.0
            )
            effective_source = (
                str(character.get("gender_source") or "unknown")
                if effective_gender == str(character.get("gender") or "unknown")
                else "persisted"
            )
            voice_character = {**character, "gender": effective_gender}
            voice = str(persisted.get("voice_key") or "")
            if (
                (review_override or not bool(persisted.get("voice_locked")))
                and (review_override or _character_gender_verified(character))
                and effective_gender in {"female", "male"}
                and voice not in _voice_pool(effective_gender)
            ):
                voice = _deterministic_voice_avoiding(
                    catalog_id, voice_character, reserved_voice
                )
            cast[name] = {
                **character,
                "canonical_name": str(
                    persisted.get("canonical_name") or character["canonical_name"]
                ),
                "character_key": persisted.get("character_key")
                or sha256(name.encode()).digest(),
                "gender": effective_gender,
                "gender_confidence": effective_confidence,
                "gender_source": effective_source,
                "voice_key": voice,
                "voice_locked": int(bool(persisted.get("voice_locked"))),
                "unknown_fallback": effective_gender == "unknown" or not bool(voice),
            }
        return cast

    @staticmethod
    def _manifest_from_row(
        row: dict[str, Any],
        *,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        stored = row["manifest_json"]
        if not isinstance(stored, dict):
            stored = json.loads(stored)
        if not isinstance(stored, dict) or not stored.get("manifest_hash"):
            raise RuntimeError("stored audiobook manifest is invalid")
        validate_manifest_payload(stored, settings=settings)
        return dict(stored)

    def _stored_manifest(
        self,
        catalog_id: int,
        chapter_id: int,
        content_hash: str,
        settings_hash: str,
        cast_revision: int,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        mysql = getattr(self.repository, "_mysql", None)
        if mysql is None:
            return None
        with mysql.pool.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT manifest_json FROM audiobook_chapter_manifests "
                    "WHERE catalog_id=%s AND chapter_id=%s AND content_hash=UNHEX(%s) "
                    "AND settings_hash=UNHEX(%s) AND engine_version=%s "
                    "AND cast_revision=%s FOR UPDATE",
                    (
                        catalog_id, chapter_id, content_hash, settings_hash,
                        ENGINE_VERSION, cast_revision,
                    ),
                )
                row = cursor.fetchone()
                if row:
                    cursor.execute(
                        "UPDATE audiobook_chapter_manifests SET last_accessed_at=UTC_TIMESTAMP(6) "
                        "WHERE catalog_id=%s AND chapter_id=%s AND content_hash=UNHEX(%s) "
                        "AND settings_hash=UNHEX(%s) AND engine_version=%s "
                        "AND cast_revision=%s",
                        (
                            catalog_id, chapter_id, content_hash, settings_hash,
                            ENGINE_VERSION, cast_revision,
                        ),
                    )
        if not row:
            return None
        try:
            return self._manifest_from_row(row, settings=settings)
        except (AudiobookContractError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _persist_manifest(
        self, catalog_id: int, manifest: dict[str, Any]
    ) -> dict[str, Any]:
        mysql = getattr(self.repository, "_mysql", None)
        if mysql is None:
            return manifest
        raw = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
        with mysql.pool.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT IGNORE INTO audiobook_chapter_manifests "
                    "(catalog_id,chapter_id,content_hash,settings_hash,engine_version,cast_revision,manifest_hash,manifest_json,segment_count) "
                    "VALUES (%s,%s,UNHEX(%s),UNHEX(%s),%s,%s,UNHEX(%s),%s,%s)",
                    (catalog_id, manifest["chapter_id"], manifest["content_hash"], manifest["settings_hash"],
                     ENGINE_VERSION, int(manifest.get("cast_revision") or 0),
                     manifest["manifest_hash"], raw, len(manifest["segments"])),
                )
                # The semantic key is immutable. If another worker (or an older
                # active session) already owns it, always return that exact
                # manifest instead of mutating the referenced manifest hash.
                cursor.execute(
                    "SELECT manifest_json FROM audiobook_chapter_manifests "
                    "WHERE catalog_id=%s AND chapter_id=%s AND content_hash=UNHEX(%s) "
                    "AND settings_hash=UNHEX(%s) AND engine_version=%s "
                    "AND cast_revision=%s FOR UPDATE",
                    (catalog_id, manifest["chapter_id"], manifest["content_hash"],
                     manifest["settings_hash"], ENGINE_VERSION,
                     int(manifest.get("cast_revision") or 0)),
                )
                row = cursor.fetchone()
                if not row:
                    raise RuntimeError("audiobook manifest persistence failed")
                cursor.execute(
                    "UPDATE audiobook_chapter_manifests SET last_accessed_at=UTC_TIMESTAMP(6) "
                    "WHERE catalog_id=%s AND chapter_id=%s AND content_hash=UNHEX(%s) "
                    "AND settings_hash=UNHEX(%s) AND engine_version=%s "
                    "AND cast_revision=%s",
                    (catalog_id, manifest["chapter_id"], manifest["content_hash"],
                     manifest["settings_hash"], ENGINE_VERSION,
                     int(manifest.get("cast_revision") or 0)),
                )
        return self._manifest_from_row(row, settings=manifest)

    @staticmethod
    def _manifest_character(character: dict[str, Any]) -> dict[str, Any]:
        rendered = dict(character)
        for key in tuple(rendered):
            if key.startswith("_"):
                rendered.pop(key, None)
        character_key = rendered.get("character_key")
        if isinstance(character_key, memoryview):
            character_key = character_key.tobytes()
        if isinstance(character_key, (bytes, bytearray)):
            rendered["character_key"] = bytes(character_key).hex()
        for key, value in tuple(rendered.items()):
            if isinstance(value, Decimal):
                rendered[key] = float(value)
        return rendered

    def _manifest(self, book_id: str, chapter_id: int, settings: dict[str, Any]) -> dict[str, Any]:
        chapter = self.repository.reader_chapter(book_id, chapter_id)
        content = normalize_chapter_text(chapter.get("content") or "")
        catalog_id = self._catalog_id(book_id)
        content_hash = sha256(content.encode()).hexdigest()
        mode = str(settings.get("mode") or "normal")
        requested_narrator = str(settings.get("narrator") or "mocheng")
        single_voice = str(settings.get("voice") or requested_narrator)
        validate_voice_selection(
            mode=mode, narrator=requested_narrator, voice=single_voice,
        )
        settings_json = json.dumps(settings, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        settings_hash = sha256(
            f"{settings_json}\0{MANIFEST_PIPELINE_VERSION}".encode()
        ).hexdigest()
        cast_revision = self._cast_revision(catalog_id)
        stored_manifest = self._stored_manifest(
            catalog_id, chapter_id, content_hash, settings_hash, cast_revision,
            settings,
        )
        if stored_manifest is not None:
            return stored_manifest
        existing_cast = self._existing_cast(catalog_id)
        characters, raw_segments = analyze_chapter(content, existing_cast)
        raw_segments = [
            item for item in raw_segments if has_spoken_content(str(item.get("text") or ""))
        ]
        if self._gender_guess_enabled:
            existing_index = {
                str(item.get("canonical_name") or ""): item for item in existing_cast
            }
            self._enrich_gender(characters, existing_index)
        cast = self._playback_cast(catalog_id, characters, existing_cast)
        narrator = effective_narrator_voice(
            catalog_id, requested_narrator, cast
        )
        emotion_setting = str(settings.get("emotion") or "auto")
        segments = []
        anonymous_paragraph_voices: dict[tuple[int, str], str] = {}
        dialogue_voice_by_speaker: dict[str, str] = {}
        occupied_dialogue_voices: set[str] = set()

        def reserve_dialogue_voice(speaker: str, character: dict[str, Any]) -> str:
            existing = dialogue_voice_by_speaker.get(speaker)
            if existing:
                return existing
            gender = str(character.get("gender") or "unknown")
            gender_pool = _voice_pool(gender) or CAST_VOICES
            preferred = rendition_voice(
                catalog_id, character, requested_narrator
            )
            if (
                preferred
                and preferred != narrator
                and preferred not in occupied_dialogue_voices
            ):
                selected = preferred
            else:
                candidates = [
                    voice for voice in gender_pool
                    if voice != narrator and voice not in occupied_dialogue_voices
                ]
                if not candidates:
                    candidates = [
                        voice for voice in gender_pool
                        if voice != narrator
                    ]
                if not candidates:
                    raise AudiobookContractError(
                        "no same-gender dialogue voice available outside narrator"
                    )
                seed = sha256(
                    f"{catalog_id}\0{speaker}\0{narrator}\0manifest-dialogue".encode()
                ).digest()
                selected = candidates[int.from_bytes(seed[:2], "big") % len(candidates)]
            dialogue_voice_by_speaker[speaker] = selected
            occupied_dialogue_voices.add(selected)
            return selected

        # Named characters get first claim on distinct dialogue voices.  This
        # is a per-manifest override: a user's narrator choice never mutates a
        # book-level locked voice, but narration and dialogue can never collide.
        if mode == "smart":
            scene_characters = {
                str(raw_item.get("speaker") or ""): cast.get(
                    str(raw_item.get("speaker") or "")
                )
                for raw_item in raw_segments
                if raw_item.get("kind") == "dialogue"
                and str(raw_item.get("speaker") or "")
                and cast.get(str(raw_item.get("speaker") or ""))
            }
            role_rank = {
                "protagonist": 0, "supporting": 1, "cameo": 2,
                "unclassified": 3,
            }
            for speaker, character in sorted(
                scene_characters.items(),
                key=lambda item: (
                    role_rank.get(str(item[1].get("role_type") or ""), 4),
                    -int(item[1].get("chapter_count") or 0),
                    -int(item[1].get("dialogue_count") or 0),
                    item[0],
                ),
            ):
                reserve_dialogue_voice(speaker, character)
        named_cast_voices = set(dialogue_voice_by_speaker.values())
        for index, item in enumerate(raw_segments):
            character = cast.get(item["speaker"])
            if item.get("kind") == "dialogue" and item.get("speaker") and not character:
                # The parser may retain an attribution candidate for context,
                # but playback must not expose or bind it until the full-book
                # identity gate has accepted that person.
                item = {
                    **item,
                    "speaker": "",
                    "identity_candidate_rejected": True,
                }
            if character and item.get("kind") == "dialogue":
                item = {
                    **item,
                    "gender": str(character.get("gender") or "unknown"),
                    "gender_confidence": float(
                        character.get("gender_confidence") or 0.0
                    ),
                    "gender_source": str(
                        character.get("gender_source") or "unknown"
                    ),
                }
            if mode == "smart":
                if character and item.get("kind") == "dialogue":
                    voice = reserve_dialogue_voice(
                        str(item.get("speaker") or ""), character
                    )
                elif item.get("kind") == "dialogue":
                    paragraph_index = int(item.get("paragraph_index") or 0)
                    anonymous_gender = str(item.get("gender") or "unknown")
                    # Identity confidence and voice gender are separate gates.
                    # Once a local gender is known, even a rejected/anonymous
                    # role may never fall back to the mixed-gender pool.
                    source_pool = _voice_pool(anonymous_gender) or CAST_VOICES
                    anonymous_pool = tuple(
                        candidate for candidate in source_pool
                        if candidate != narrator
                        and candidate not in named_cast_voices
                    )
                    if not anonymous_pool:
                        anonymous_pool = tuple(
                            candidate for candidate in source_pool
                            if candidate != narrator
                        ) or tuple(
                            candidate for candidate in CAST_VOICES
                            if candidate != narrator
                        )
                    if not anonymous_pool:  # defensive: CAST_VOICES has >1 entry
                        raise RuntimeError("no dialogue voice available outside narrator")
                    anonymous_key = (paragraph_index, anonymous_gender)
                    if anonymous_key not in anonymous_paragraph_voices:
                        seed = sha256(
                            f"{catalog_id}\0{chapter_id}\0{paragraph_index}\0"
                            f"{anonymous_gender}\0{narrator}".encode()
                        ).digest()
                        anonymous_paragraph_voices[anonymous_key] = anonymous_pool[
                            int.from_bytes(seed[:2], "big") % len(anonymous_pool)
                        ]
                    voice = anonymous_paragraph_voices[anonymous_key]
                else:
                    voice = narrator
                if emotion_setting == "auto":
                    segment_emotion = str(item.get("emotion_hint") or "neutral")
                    segment_confidence = float(item.get("emotion_confidence") or 0.0)
                    emotion = (
                        segment_emotion
                        if segment_emotion != "neutral" and segment_confidence >= 0.7
                        else str(character.get("tone") or "neutral")
                        if character and float(character.get("tone_confidence") or 0.0) >= 0.85
                        else "neutral"
                    )
                else:
                    emotion = emotion_setting
            else:
                voice = single_voice
                emotion = "neutral" if emotion_setting == "auto" else emotion_setting
            segment_hash = sha256(f"{content_hash}\0{settings_hash}\0{ENGINE_VERSION}\0{index}\0{voice}\0{emotion}\0{item['text']}".encode()).hexdigest()
            segments.append({**item, "index": index, "voice": voice, "emotion": emotion,
                             "rate": float(settings.get("rate") or 1), "sha256": segment_hash})
        validate_manifest_contract(
            mode=mode,
            requested_narrator=requested_narrator,
            effective_narrator=narrator,
            selected_voice=single_voice,
            segments=segments,
        )
        manifest_identity = {"book_id": book_id, "chapter_id": chapter_id, "content_hash": content_hash,
                             "settings_hash": settings_hash, "engine_version": ENGINE_VERSION,
                             "cast_revision": cast_revision,
                             "mode": mode,
                             "selected_voice": single_voice,
                             "requested_narrator": requested_narrator,
                             "effective_narrator": narrator,
                             "segments": [{k: v for k, v in item.items() if k != "text"} for item in segments]}
        manifest_hash = sha256(json.dumps(manifest_identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        rendered_characters = [
            self._manifest_character(
                {
                    **character,
                    "voice_key": (
                        dialogue_voice_by_speaker.get(
                            str(character.get("canonical_name") or "")
                        )
                        or rendition_voice(
                            catalog_id, character, requested_narrator
                        )
                        or next(voice for voice in CAST_VOICES if voice != narrator)
                    ),
                }
            )
            for character in cast.values()
        ]
        manifest = {**manifest_identity, "manifest_hash": manifest_hash, "title": chapter.get("title") or "",
                "next_chapter_id": chapter.get("next_id"), "characters": rendered_characters, "segments": segments,
                "complete": True, "cache_key": f"audiobook:{manifest_hash}"}
        return self._persist_manifest(catalog_id, manifest)

    @staticmethod
    def _public_manifest(session_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
        result = {key: value for key, value in manifest.items() if key != "segments"}
        result["stream_endpoint"] = (
            f"/api/v1/audiobook/sessions/{session_id}/chapters/"
            f"{manifest['manifest_hash']}/stream.mp3"
        )
        result["segments"] = [
            {key: value for key, value in segment.items() if key != "text"} | {
                "text": segment["text"],
                "audio_endpoint": f"/api/v1/audiobook/sessions/{session_id}/segments/{manifest['manifest_hash']}/{segment['index']}",
            }
            for segment in manifest["segments"]
        ]
        return result

    def _known_segment_durations_ms(
        self, segments: list[dict[str, Any]]
    ) -> dict[int, int]:
        mysql = getattr(self.repository, "_mysql", None)
        if mysql is None:
            return {}
        indexed_hashes: list[tuple[int, str]] = []
        for fallback_index, segment in enumerate(segments):
            digest = str(segment.get("sha256") or "")
            if re.fullmatch(r"[0-9a-f]{64}", digest):
                indexed_hashes.append(
                    (int(segment.get("index") or fallback_index), digest)
                )
        if not indexed_hashes:
            return {}
        placeholders = ",".join(["UNHEX(%s)"] * len(indexed_hashes))
        with mysql.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT LOWER(HEX(segment_hash)) AS segment_hash,duration_ms "
                    "FROM audiobook_audio_jobs WHERE segment_hash IN ("
                    + placeholders
                    + ") AND status='complete' AND duration_ms>0",
                    tuple(digest for _index, digest in indexed_hashes),
                )
                durations = {
                    str(row["segment_hash"]): int(row["duration_ms"])
                    for row in cursor.fetchall()
                }
        return {
            index: int(durations[digest])
            for index, digest in indexed_hashes
            if digest in durations
        }

    def _normalize_resume_position(
        self, manifest: dict[str, Any], resume: dict[str, Any]
    ) -> None:
        segments = list(manifest.get("segments") or [])
        if not resume or not segments:
            return
        by_index = {
            int(segment.get("index") or fallback_index): fallback_index
            for fallback_index, segment in enumerate(segments)
        }
        requested_index = max(0, int(resume.get("item_index") or 0))
        position = by_index.get(requested_index)
        if position is None:
            paragraph = max(0, int(resume.get("paragraph_index") or 0))
            position = next(
                (
                    fallback_index
                    for fallback_index, segment in enumerate(segments)
                    if int(segment.get("paragraph_index") or 0) >= paragraph
                ),
                0,
            )
        offset_ms = max(0, int(resume.get("audio_offset_ms") or 0))
        if offset_ms > 0:
            durations = self._known_segment_durations_ms(segments[position:])
            original_position = position
            while position < len(segments):
                segment_index = int(segments[position].get("index") or position)
                duration_ms = max(0, int(durations.get(segment_index) or 0))
                if duration_ms <= 0:
                    if position > original_position:
                        offset_ms = 0
                    break
                if offset_ms < max(0, duration_ms - 250):
                    offset_ms = min(offset_ms, max(0, duration_ms - 50))
                    break
                offset_ms -= duration_ms
                position += 1
                if position >= len(segments):
                    position = len(segments) - 1
                    offset_ms = 0
                    break
        segment = segments[position]
        resume["item_index"] = int(segment.get("index") or position)
        resume["paragraph_index"] = max(
            0, int(segment.get("paragraph_index") or resume.get("paragraph_index") or 0)
        )
        resume["audio_offset_ms"] = max(0, int(offset_ms))

    @staticmethod
    def _resolve_start_position(
        manifest: dict[str, Any], paragraph_index: int
    ) -> dict[str, int]:
        segments = manifest.get("segments") or []
        requested = max(0, int(paragraph_index or 0))
        if not segments:
            return {
                "requested_paragraph_index": requested,
                "paragraph_index": requested,
                "item_index": 0,
                "audio_offset_ms": 0,
            }
        candidate = next(
            (
                segment
                for segment in segments
                if int(segment.get("paragraph_index") or 0) >= requested
            ),
            segments[-1],
        )
        return {
            "requested_paragraph_index": requested,
            "paragraph_index": max(0, int(candidate.get("paragraph_index") or 0)),
            "item_index": max(0, int(candidate.get("index") or 0)),
            "audio_offset_ms": 0,
        }

    def create(
        self,
        *,
        owner_key: str,
        book_id: str,
        chapter_id: int,
        settings: dict[str, Any],
        resume_existing: bool = True,
        start_paragraph_index: int = 0,
        device_key: str = "",
    ) -> dict[str, Any]:
        if str(settings.get("mode") or "normal") == "smart":
            # Producer-mode readers persist the current engine/content scan
            # revision synchronously here. Existing-cast reads below can then
            # exclude stale revisions on the first v12 session.
            self._cast_prewarm.request(book_id)
        resume = (
            self.progress(owner_key, book_id, device_key=device_key or owner_key)
            if resume_existing
            else None
        )
        effective_chapter_id = int(chapter_id)
        if resume_existing and resume and int(resume.get("chapter_id") or 0) > 0:
            try:
                current = self._manifest(
                    book_id, int(resume["chapter_id"]), settings
                )
                effective_chapter_id = int(resume["chapter_id"])
            except (KeyError, NotFoundError):
                current = self._manifest(book_id, effective_chapter_id, settings)
                resume = None
        else:
            current = self._manifest(book_id, effective_chapter_id, settings)
        if resume:
            exact = str(resume.get("manifest_hash") or "") == str(
                current["manifest_hash"]
            )
            resume["exact_compatible"] = exact
            if not exact:
                paragraph = max(0, int(resume.get("paragraph_index") or 0))
                candidate = next(
                    (
                        segment
                        for segment in current["segments"]
                        if int(segment.get("paragraph_index") or 0) >= paragraph
                    ),
                    current["segments"][0] if current["segments"] else None,
                )
                resume["item_index"] = int(candidate.get("index") or 0) if candidate else 0
                resume["audio_offset_ms"] = 0
                resume["manifest_hash"] = str(current["manifest_hash"])
            self._normalize_resume_position(current, resume)
        start = self._resolve_start_position(current, start_paragraph_index)
        manifests = {current["manifest_hash"]: current}
        session = AudiobookSession(
            uuid4().hex,
            owner_key,
            manifests,
            current_chapter_id=effective_chapter_id,
            settings=dict(settings),
            device_key=device_key or owner_key,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        mysql = getattr(self.repository, "_mysql", None)
        if mysql is not None:
            catalog_id = self._catalog_id(book_id)
            with mysql.pool.transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO audiobook_sessions "
                        "(session_id,owner_hash,device_hash,catalog_id,book_public_id,current_chapter_id,settings_json,expires_at) "
                        "VALUES (%s,UNHEX(%s),UNHEX(%s),%s,%s,%s,%s,DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 12 HOUR))",
                        (session.session_id, owner_key, device_key or owner_key, catalog_id, book_id, effective_chapter_id,
                         json.dumps(settings, ensure_ascii=False, separators=(",", ":"))),
                    )
                    cursor.execute(
                        "INSERT INTO audiobook_session_manifests (session_id,manifest_hash,priority) "
                        "VALUES (%s,UNHEX(%s),0)", (session.session_id, current["manifest_hash"]),
                    )
        return {"session_id": session.session_id, "engine_version": ENGINE_VERSION,
                "current": self._public_manifest(session.session_id, current), "next": None,
                "resume": resume, "start": start,
                "next_prefetch_endpoint": f"/api/v1/audiobook/sessions/{session.session_id}/next"}

    def session_owner(
        self,
        session_id: str,
        *,
        owner_key: str = "",
        device_key: str = "",
    ) -> str | None:
        """Resolve an active session by account owner or its creating device."""
        fallback_hash = "0" * 64
        owner_match = (
            owner_key
            if re.fullmatch(r"[0-9a-f]{64}", str(owner_key or ""))
            else fallback_hash
        )
        device_match = (
            device_key
            if re.fullmatch(r"[0-9a-f]{64}", str(device_key or ""))
            else fallback_hash
        )
        mysql = getattr(self.repository, "_mysql", None)
        if mysql is not None:
            with mysql.pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT LOWER(HEX(owner_hash)) AS owner_key "
                        "FROM audiobook_sessions WHERE session_id=%s "
                        "AND cancelled=0 AND expires_at>UTC_TIMESTAMP(6) "
                        "AND (owner_hash=UNHEX(%s) OR device_hash=UNHEX(%s)) "
                        "LIMIT 1",
                        (session_id, owner_match, device_match),
                    )
                    row = cursor.fetchone()
            return str(row["owner_key"]) if row else None
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.cancelled:
                return None
            if session.owner_key == owner_match or session.device_key == device_match:
                return session.owner_key
        return None

    def progress(
        self, owner_key: str, book_id: str, *, device_key: str = ""
    ) -> dict[str, Any] | None:
        mysql = getattr(self.repository, "_mysql", None)
        if mysql is None:
            return None
        catalog_id = self._catalog_id(book_id)
        with mysql.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT chapter_id,paragraph_index,item_index,audio_offset_ms,"
                    "LOWER(HEX(manifest_hash)) AS manifest_hash,"
                    "LOWER(HEX(settings_hash)) AS settings_hash,cast_revision,updated_at "
                    "FROM audiobook_device_progress WHERE owner_hash=UNHEX(%s) "
                    "AND catalog_id=%s ORDER BY (device_hash=UNHEX(%s)) DESC,"
                    "updated_at DESC LIMIT 1",
                    (owner_key, catalog_id, device_key or owner_key),
                )
                row = cursor.fetchone()
        if not row:
            return None
        return {
            "book_id": book_id,
            "chapter_id": int(row["chapter_id"]),
            "paragraph_index": int(row["paragraph_index"]),
            "item_index": int(row["item_index"]),
            "audio_offset_ms": int(row["audio_offset_ms"]),
            "manifest_hash": str(row.get("manifest_hash") or ""),
            "settings_hash": str(row.get("settings_hash") or ""),
            "cast_revision": int(row.get("cast_revision") or 0),
            "updated_at": str(row["updated_at"]),
        }

    def save_progress(
        self,
        session_id: str,
        owner_key: str,
        *,
        device_key: str = "",
        chapter_id: int,
        paragraph_index: int,
        item_index: int,
        audio_offset_ms: int,
    ) -> None:
        mysql = getattr(self.repository, "_mysql", None)
        if mysql is None:
            with self._lock:
                session = self._sessions.get(session_id)
                if not session or session.owner_key != owner_key or session.cancelled:
                    raise KeyError("session not found")
            return
        with mysql.pool.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT catalog_id,book_public_id FROM audiobook_sessions "
                    "WHERE session_id=%s AND owner_hash=UNHEX(%s) AND cancelled=0 "
                    "AND expires_at>UTC_TIMESTAMP(6) FOR UPDATE",
                    (session_id, owner_key),
                )
                session_row = cursor.fetchone()
                if not session_row:
                    raise KeyError("session not found")
                cursor.execute(
                    "SELECT LOWER(HEX(m.manifest_hash)) AS manifest_hash,"
                    "LOWER(HEX(m.settings_hash)) AS settings_hash,m.cast_revision,"
                    "m.manifest_json "
                    "FROM audiobook_session_manifests sm "
                    "INNER JOIN audiobook_chapter_manifests m "
                    "ON m.manifest_hash=sm.manifest_hash "
                    "WHERE sm.session_id=%s AND m.chapter_id=%s LIMIT 1",
                    (session_id, int(chapter_id)),
                )
                manifest_row = cursor.fetchone()
                if not manifest_row:
                    raise KeyError("chapter not attached")
                progress_resume = {
                    "chapter_id": int(chapter_id),
                    "paragraph_index": max(0, int(paragraph_index)),
                    "item_index": max(0, int(item_index)),
                    "audio_offset_ms": max(0, int(audio_offset_ms)),
                    "manifest_hash": str(manifest_row["manifest_hash"]),
                }
                try:
                    manifest_payload = json.loads(
                        str(manifest_row.get("manifest_json") or "{}")
                    )
                except (TypeError, ValueError):
                    manifest_payload = {}
                if isinstance(manifest_payload, dict) and manifest_payload.get(
                    "segments"
                ):
                    self._normalize_resume_position(
                        manifest_payload, progress_resume
                    )
                cursor.execute(
                    "UPDATE audiobook_sessions SET expires_at="
                    "DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 12 HOUR) "
                    "WHERE session_id=%s AND owner_hash=UNHEX(%s)",
                    (session_id, owner_key),
                )
                cursor.execute(
                    "INSERT INTO audiobook_device_progress "
                    "(owner_hash,catalog_id,device_hash,book_public_id,chapter_id,"
                    "paragraph_index,item_index,audio_offset_ms,manifest_hash,settings_hash,cast_revision) "
                    "VALUES (UNHEX(%s),%s,UNHEX(%s),%s,%s,%s,%s,%s,UNHEX(%s),UNHEX(%s),%s) "
                    "ON DUPLICATE KEY UPDATE book_public_id=VALUES(book_public_id),"
                    "chapter_id=VALUES(chapter_id),paragraph_index=VALUES(paragraph_index),"
                    "item_index=VALUES(item_index),audio_offset_ms=VALUES(audio_offset_ms),"
                    "manifest_hash=VALUES(manifest_hash),settings_hash=VALUES(settings_hash),"
                    "cast_revision=VALUES(cast_revision)",
                    (
                        owner_key,
                        int(session_row["catalog_id"]),
                        device_key or owner_key,
                        str(session_row["book_public_id"]),
                        int(chapter_id),
                        int(progress_resume["paragraph_index"]),
                        int(progress_resume["item_index"]),
                        int(progress_resume["audio_offset_ms"]),
                        str(manifest_row["manifest_hash"]),
                        str(manifest_row["settings_hash"]),
                        int(manifest_row["cast_revision"]),
                    ),
                )

    def prefetch_next(
        self,
        session_id: str,
        owner_key: str,
        from_chapter_id: int | None = None,
    ) -> dict[str, Any] | None:
        mysql = getattr(self.repository, "_mysql", None)
        if mysql is not None:
            # Read the owner-scoped session snapshot without keeping a row lock
            # while chapter I/O, cast analysis and manifest persistence run.
            # A second short transaction below revalidates the session before
            # attaching the immutable manifest.
            with mysql.pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT book_public_id,current_chapter_id,settings_json FROM audiobook_sessions "
                        "WHERE session_id=%s AND owner_hash=UNHEX(%s) AND cancelled=0 AND expires_at>UTC_TIMESTAMP(6) "
                        "LIMIT 1",
                        (session_id, owner_key),
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise KeyError("session not found")
            book_id = str(row["book_public_id"])
            source_chapter_id = int(
                from_chapter_id
                if from_chapter_id is not None
                else row["current_chapter_id"]
            )
            current = self.repository.reader_chapter(book_id, source_chapter_id)
            following_id = current.get("next_id")
            settings = (
                row["settings_json"]
                if isinstance(row["settings_json"], dict)
                else json.loads(row["settings_json"])
            )
            if not following_id:
                return None
            following = self._manifest(book_id, int(following_id), settings)
            with mysql.pool.transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT 1 FROM audiobook_sessions WHERE session_id=%s "
                        "AND owner_hash=UNHEX(%s) AND cancelled=0 "
                        "AND expires_at>UTC_TIMESTAMP(6) FOR UPDATE",
                        (session_id, owner_key),
                    )
                    if not cursor.fetchone():
                        raise KeyError("session not found")
                    cursor.execute(
                        "INSERT IGNORE INTO audiobook_session_manifests (session_id,manifest_hash,priority) "
                        "VALUES (%s,UNHEX(%s),1)", (session_id, following["manifest_hash"]),
                    )
                    cursor.execute(
                        "UPDATE audiobook_sessions SET expires_at="
                        "DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 12 HOUR) "
                        "WHERE session_id=%s AND owner_hash=UNHEX(%s)",
                        (session_id, owner_key),
                    )
            with self._lock:
                if session_id in self._sessions:
                    self._sessions[session_id].manifests[
                        following["manifest_hash"]
                    ] = following
        else:
            with self._lock:
                session = self._sessions.get(session_id)
                if not session or session.owner_key != owner_key or session.cancelled:
                    raise KeyError("session not found")
                source_chapter_id = int(
                    from_chapter_id
                    if from_chapter_id is not None
                    else session.current_chapter_id
                )
                current_manifest = next(
                    (
                        manifest
                        for manifest in session.manifests.values()
                        if int(manifest["chapter_id"]) == source_chapter_id
                    ),
                    None,
                )
                if current_manifest is None:
                    current_manifest = self._manifest(
                        str(next(iter(session.manifests.values()))["book_id"]),
                        source_chapter_id,
                        session.settings,
                    )
                    session.manifests[current_manifest["manifest_hash"]] = current_manifest
                book_id = str(current_manifest["book_id"])
                following_id = current_manifest.get("next_chapter_id")
                settings = dict(session.settings)
            if not following_id:
                return None
            following = self._manifest(book_id, int(following_id), settings)
            with self._lock:
                session = self._sessions.get(session_id)
                if session:
                    session.manifests = {following["manifest_hash"]: following, **session.manifests}
        return self._public_manifest(session_id, following)

    def activate_chapter(
        self, session_id: str, chapter_id: int, owner_key: str
    ) -> bool:
        """Commit the playback cursor only after the client enters a chapter."""
        mysql = getattr(self.repository, "_mysql", None)
        if mysql is not None:
            with mysql.pool.transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT 1 FROM audiobook_sessions s "
                        "INNER JOIN audiobook_session_manifests sm ON sm.session_id=s.session_id "
                        "INNER JOIN audiobook_chapter_manifests m ON m.manifest_hash=sm.manifest_hash "
                        "WHERE s.session_id=%s AND s.owner_hash=UNHEX(%s) AND s.cancelled=0 "
                        "AND s.expires_at>UTC_TIMESTAMP(6) AND m.chapter_id=%s LIMIT 1 FOR UPDATE",
                        (session_id, owner_key, int(chapter_id)),
                    )
                    if not cursor.fetchone():
                        raise KeyError("chapter is not attached to session")
                    cursor.execute(
                        "UPDATE audiobook_sessions SET current_chapter_id=%s,"
                        "expires_at=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 12 HOUR) "
                        "WHERE session_id=%s AND owner_hash=UNHEX(%s)",
                        (int(chapter_id), session_id, owner_key),
                    )
            return True
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.owner_key != owner_key or session.cancelled:
                raise KeyError("session not found")
            if not any(
                int(manifest["chapter_id"]) == int(chapter_id)
                for manifest in session.manifests.values()
            ):
                raise KeyError("chapter is not attached to session")
            session.current_chapter_id = int(chapter_id)
        return True

    def manifest(
        self, session_id: str, manifest_hash: str, owner_key: str
    ) -> dict[str, Any]:
        mysql = getattr(self.repository, "_mysql", None)
        if mysql is not None:
            with mysql.pool.transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT m.manifest_json,s.settings_json FROM audiobook_sessions s "
                        "INNER JOIN audiobook_session_manifests sm ON sm.session_id=s.session_id "
                        "INNER JOIN audiobook_chapter_manifests m ON m.manifest_hash=sm.manifest_hash "
                        "WHERE s.session_id=%s AND s.owner_hash=UNHEX(%s) AND s.cancelled=0 "
                        "AND s.expires_at>UTC_TIMESTAMP(6) AND sm.manifest_hash=UNHEX(%s)",
                        (session_id, owner_key, manifest_hash),
                    )
                    row = cursor.fetchone()
                    if row:
                        cursor.execute(
                            "UPDATE audiobook_chapter_manifests SET last_accessed_at=UTC_TIMESTAMP(6) "
                            "WHERE manifest_hash=UNHEX(%s)", (manifest_hash,),
                        )
                        cursor.execute(
                            "UPDATE audiobook_sessions SET expires_at="
                            "DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 12 HOUR) "
                            "WHERE session_id=%s AND owner_hash=UNHEX(%s) "
                            "AND expires_at<DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 6 HOUR)",
                            (session_id, owner_key),
                        )
            if not row:
                raise KeyError("session not found")
            settings = (
                row["settings_json"]
                if isinstance(row.get("settings_json"), dict)
                else json.loads(row.get("settings_json") or "{}")
            )
            return self._manifest_from_row(row, settings=settings)
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.cancelled or session.owner_key != owner_key:
                raise KeyError("session not found")
            manifest = session.manifests.get(manifest_hash)
            if manifest is None:
                raise KeyError("session not found")
            validate_manifest_payload(manifest, settings=session.settings)
            return dict(manifest)

    def segment(
        self,
        session_id: str,
        manifest_hash: str,
        segment_index: int,
        owner_key: str,
    ) -> dict[str, Any]:
        manifest = self.manifest(session_id, manifest_hash, owner_key)
        if segment_index < 0 or segment_index >= len(manifest["segments"]):
            raise KeyError("segment not found")
        return dict(manifest["segments"][segment_index])

    def timeline(
        self,
        session_id: str,
        manifest_hash: str,
        owner_key: str,
        *,
        start: int = 0,
        limit: int = 24,
    ) -> dict[str, Any]:
        """Return exact durations already measured for one immutable manifest."""
        manifest = self.manifest(session_id, manifest_hash, owner_key)
        all_segments = list(manifest.get("segments") or [])
        bounded_start = max(0, int(start))
        bounded_limit = max(1, min(int(limit), 64))
        segments = all_segments[bounded_start : bounded_start + bounded_limit]
        mysql = getattr(self.repository, "_mysql", None)
        durations: dict[str, int] = {}
        hashes = [
            str(segment.get("sha256") or "")
            for segment in segments
            if re.fullmatch(r"[0-9a-f]{64}", str(segment.get("sha256") or ""))
        ]
        if mysql is not None and hashes:
            placeholders = ",".join(["UNHEX(%s)"] * len(hashes))
            with mysql.pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT LOWER(HEX(segment_hash)) AS segment_hash,duration_ms "
                        "FROM audiobook_audio_jobs WHERE segment_hash IN ("
                        + placeholders
                        + ") AND status='complete' AND duration_ms>0",
                        tuple(hashes),
                    )
                    durations = {
                        str(row["segment_hash"]): int(row["duration_ms"])
                        for row in cursor.fetchall()
                    }
        result = [
            {
                "index": int(segment.get("index") or index),
                "duration_ms": int(durations.get(str(segment.get("sha256") or ""), 0)),
            }
            for index, segment in enumerate(segments, start=bounded_start)
        ]
        return {
            "manifest_hash": str(manifest.get("manifest_hash") or manifest_hash),
            "start": bounded_start,
            "limit": bounded_limit,
            "complete": bool(result) and all(item["duration_ms"] > 0 for item in result),
            "segments": result,
        }

    def cancel(self, session_id: str, owner_key: str = "") -> bool:
        mysql = getattr(self.repository, "_mysql", None)
        if mysql is not None:
            with mysql.pool.transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE audiobook_sessions SET cancelled=1 WHERE session_id=%s AND owner_hash=UNHEX(%s) AND cancelled=0",
                        (session_id, owner_key),
                    )
                    changed = cursor.rowcount > 0
                    if changed:
                        cursor.execute(
                            "DELETE FROM audiobook_session_manifests WHERE session_id=%s",
                            (session_id,),
                        )
            return changed
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or (owner_key and session.owner_key != owner_key):
                return False
            session.cancelled = True
            self._sessions.pop(session_id, None)
            return True

    def cancel_owner(self, owner_key: str) -> int:
        mysql = getattr(self.repository, "_mysql", None)
        if mysql is not None:
            with mysql.pool.transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE audiobook_sessions SET cancelled=1 WHERE owner_hash=UNHEX(%s) AND cancelled=0",
                        (owner_key,),
                    )
                    return int(cursor.rowcount)
        with self._lock:
            ids = [key for key, value in self._sessions.items() if value.owner_key == owner_key]
            for session_id in ids:
                self._sessions[session_id].cancelled = True
                self._sessions.pop(session_id, None)
            return len(ids)

    def cancel_device(self, owner_key: str, device_key: str) -> int:
        mysql = getattr(self.repository, "_mysql", None)
        if mysql is not None:
            with mysql.pool.transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE audiobook_sessions SET cancelled=1 "
                        "WHERE owner_hash=UNHEX(%s) AND device_hash=UNHEX(%s) "
                        "AND cancelled=0",
                        (owner_key, device_key),
                    )
                    return int(cursor.rowcount)
        # In-memory sessions predate device scoping and are used only in unit
        # tests/development, so owner scoping is the safe compatible fallback.
        return self.cancel_owner(owner_key)


class SharedAudioWork:
    """Deduplicate immutable synthesis within and across Uvicorn workers."""

    def __init__(
        self,
        repository: LibraryRepository | None = None,
        cache_root: Path | None = None,
    ) -> None:
        self.repository = repository
        self.cache_root = Path(cache_root) if cache_root is not None else None
        self._tasks: dict[str, asyncio.Task[bytes]] = {}
        self._waiters: dict[str, int] = {}
        self._guard = asyncio.Lock()
        self._last_prune = 0.0
        self._prune_guard = threading.Lock()

    def _mysql(self):
        return getattr(self.repository, "_mysql", None)

    def _paths(self, key: str) -> tuple[Path, Path]:
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("invalid audio work key")
        if self.cache_root is None:
            raise RuntimeError("shared audio cache is not configured")
        directory = self.cache_root / key[:2]
        return directory / f"{key}.mp3", directory / f"{key}.lock"

    @staticmethod
    def _read_cached(path: Path) -> bytes | None:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        return data if data else None

    def read_cached(self, key: str) -> bytes | None:
        """Read one immutable segment without starting or charging synthesis."""
        if self.cache_root is None:
            return None
        target, _lock_path = self._paths(key)
        return self._read_cached(target)

    @staticmethod
    def _acquire_file_lock(path: Path) -> int:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor

    @staticmethod
    def _release_file_lock(descriptor: int) -> None:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.part")
        try:
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _job_start(self, key: str) -> None:
        mysql = self._mysql()
        if mysql is None:
            return
        with mysql.pool.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO audiobook_audio_jobs "
                    "(segment_hash,status,ref_count,expires_at) "
                    "VALUES (UNHEX(%s),'running',1,DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 6 HOUR)) "
                    "ON DUPLICATE KEY UPDATE ref_count=ref_count+1,"
                    "status=IF(status='complete','complete','running'),"
                    "expires_at=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 6 HOUR)",
                    (key,),
                )

    def _job_finish(self, key: str, path: Path, data: bytes) -> None:
        mysql = self._mysql()
        if mysql is None:
            return
        relative = str(path.relative_to(self.cache_root))
        with mysql.pool.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE audiobook_audio_jobs SET status='complete',"
                    "cache_object_key=%s,audio_hash=%s,byte_count=%s,duration_ms=%s,"
                    "ref_count=GREATEST(ref_count-1,0),"
                    "expires_at=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 6 HOUR) "
                    "WHERE segment_hash=UNHEX(%s)",
                    (relative, sha256(data).digest(), len(data), mp3_duration_ms(data), key),
                )

    def _job_release(self, key: str, *, failed: bool = False) -> None:
        mysql = self._mysql()
        if mysql is None:
            return
        with mysql.pool.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE audiobook_audio_jobs SET "
                    "status=IF(%s=1,'failed',status),"
                    "ref_count=GREATEST(ref_count-1,0),"
                    "expires_at=IF(%s=1,DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 10 MINUTE),expires_at) "
                    "WHERE segment_hash=UNHEX(%s)",
                    (1 if failed else 0, 1 if failed else 0, key),
                )

    def _prune_expired(self) -> None:
        # The resource-capped maintenance timer performs lock-aware cleanup.
        # Request workers must never race a cached read by deleting shared files.
        self._last_prune = time.monotonic()

    async def _build_cached(
        self, key: str, builder: Callable[[], Awaitable[bytes]]
    ) -> bytes:
        if self.cache_root is None:
            return await builder()
        target, lock_path = self._paths(key)
        await asyncio.to_thread(self._prune_expired)
        await asyncio.to_thread(self._job_start, key)
        descriptor: int | None = None
        job_closed = False
        try:
            cached = await asyncio.to_thread(self._read_cached, target)
            if cached is not None:
                await asyncio.to_thread(self._job_finish, key, target, cached)
                job_closed = True
                return cached
            try:
                descriptor = await asyncio.to_thread(
                    self._acquire_file_lock, lock_path
                )
            except OSError:
                # NAS lock outages must not strand ref_count or force a local
                # disk fallback.  Serve this one result from memory only.
                cached = await builder()
                if not cached:
                    raise RuntimeError("empty shared audio result")
                await asyncio.to_thread(self._job_release, key, failed=True)
                job_closed = True
                return cached
            cached = await asyncio.to_thread(self._read_cached, target)
            if cached is None:
                try:
                    cached = await builder()
                    if not cached:
                        raise RuntimeError("empty shared audio result")
                    await asyncio.to_thread(self._write_atomic, target, cached)
                except OSError:
                    # Keep playback available without creating a local copy.
                    await asyncio.to_thread(self._job_release, key, failed=True)
                    job_closed = True
                    return cached
            await asyncio.to_thread(self._job_finish, key, target, cached)
            job_closed = True
            return cached
        except BaseException:
            if not job_closed:
                await asyncio.to_thread(self._job_release, key, failed=True)
            raise
        finally:
            if descriptor is not None:
                await asyncio.to_thread(self._release_file_lock, descriptor)

    async def get(self, key: str, builder: Callable[[], Awaitable[bytes]]) -> bytes:
        async with self._guard:
            task = self._tasks.get(key)
            if task is None:
                task = asyncio.create_task(self._build_cached(key, builder))
                self._tasks[key] = task
            self._waiters[key] = self._waiters.get(key, 0) + 1
        try:
            return await asyncio.shield(task)
        finally:
            async with self._guard:
                remaining = max(0, self._waiters.get(key, 1) - 1)
                if remaining:
                    self._waiters[key] = remaining
                else:
                    self._waiters.pop(key, None)
                    if not task.done():
                        task.cancel()
                    if self._tasks.get(key) is task:
                        self._tasks.pop(key, None)
