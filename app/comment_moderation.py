"""Paragraph comment validation and deterministic chapter paragraph identities."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass


ILLUSTRATION_LINE = re.compile(r"^\[illustration:.+\]$", re.IGNORECASE)
ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")


@dataclass(frozen=True)
class ModerationResult:
    allowed: bool
    category: str = ""
    detail: str = ""


CORE_FILTER_TERMS: dict[str, tuple[str, ...]] = {
    "辱骂": (
        "傻逼", "傻比", "傻屌", "煞笔", "脑残", "智障", "废物", "垃圾人",
        "草泥马", "操你妈", "艹你妈", "去你妈", "你妈死", "妈了个逼", "狗日的",
        "狗东西", "贱人", "婊子", "畜生", "nmsl", "cnm", "fuckyou", "bitch",
        "妈逼", "马勒戈壁", "死全家", "全家死", "孤儿东西", "臭狗", "狗杂种",
        "杂种", "人渣", "败类", "弱智", "蠢货", "蠢猪", "脑瘫", "死妈", "滚你妈",
        "王八蛋", "龟孙", "贱货", "骚货", "烂人", "垃圾玩意", "废物东西",
        "motherfucker", "shithead", "asshole", "sonofabitch", "idiot",
    ),
    "涉黄": (
        "色情", "成人视频", "成人影片", "黄色网站", "无码", "有码成人视频", "约炮", "裸聊",
        "上门服务", "性服务", "卖淫", "嫖娼", "援交", "迷奸", "强奸", "成人视频",
        "porn", "onlyfans", "成人视频", "成人直播", "情色直播", "裸照", "裸视频",
        "成人视频资源", "成人视频下载", "成人视频网站", "看片", "看黄", "福利姬",
        "招嫖", "外围女", "同城约", "一夜情", "包夜", "全套服务", "特殊服务",
        "楼凤", "莞式服务", "按摩上门", "学生妹服务", "萝莉资源", "自拍偷拍",
        "成人视频群", "成人视频会员", "成人视频导航", "成人交友", "性交易", "卖春",
        "嫖资", "裸播", "激情视频", "激情图片", "成人视频app", "hentai", "xxxvideo",
    ),
    "涉诈": (
        "刷单", "返利", "跑分", "杀猪盘", "代付", "套现", "保本稳赚", "稳赚不赔",
        "高额回报", "内幕群", "验证码转发", "冒充客服", "网贷下款", "投资带单",
        "加微信", "加微", "加v", "加vx", "加qq", "qq群", "电报群", "飞机群",
        "兼职刷单", "垫付返现", "先垫后返", "做任务返佣", "日赚千元", "轻松赚钱",
        "躺赚", "网赚", "副业赚钱", "资金盘", "投资群", "股票群", "荐股群",
        "内部消息", "内幕消息", "导师带单", "老师带单", "充值返现", "红包返利",
        "冒充公检法", "安全账户", "解冻账户", "注销网贷", "征信修复", "贷款包装",
        "无抵押贷款", "黑户下款", "强开借呗", "代办信用卡", "信用卡提额",
        "验证码", "共享屏幕", "远程协助", "退款理赔", "百万保障", "快递理赔",
        "虚假投资", "虚拟币投资", "数字货币带单", "区块链项目", "高收益理财",
        "代充值", "低价充值", "低价代购", "中奖通知", "领取奖金", "保证金解冻",
        "洗钱", "洗分", "卡农", "收卡", "收银行卡", "收电话卡", "四件套",
    ),
    "博彩": (
        "博彩", "赌博", "赌场", "下注", "押注", "赌盘", "盘口", "开盘投注", "在线投注",
        "真人娱乐", "时时彩", "六合彩", "北京赛车", "注册送彩", "充值送彩金", "买球",
        "外围盘", "棋牌赌博", "casino", "betting", "玩球", "赌球", "球盘", "球彩",
        "体育博彩", "体育竞猜", "足球投注", "篮球投注", "电竞投注", "真人荷官",
        "百家乐", "老虎机", "德州扑克赌博", "炸金花赌博", "棋牌游戏赢钱", "棋牌赢钱",
        "彩票代购", "彩票合买", "私彩", "地下六合彩", "六合彩资料", "特码", "特肖",
        "澳洲幸运", "幸运飞艇", "极速赛车", "快乐十分", "快三投注", "彩票计划群",
        "精准计划", "精准计划群", "稳赚计划", "上分", "下分", "洗码", "返水", "送彩金",
        "首充送彩金", "送彩金平台", "博彩代理", "博彩客服", "赌场开户", "在线赌场",
        "bet365", "1xbet", "pokerbet", "sportsbook", "slotmachine",
    ),
    "涉毒": (
        "毒品", "贩毒", "吸毒", "制毒", "卖毒", "买毒", "冰毒", "海洛因", "可卡因",
        "摇头丸", "麻古", "麻果", "k粉", "氯胺酮", "大麻交易", "大麻购买",
        "致幻剂", "迷幻药", "芬太尼", "杜冷丁", "吗啡买卖", "罂粟壳", "罂粟种子",
        "笑气配送", "上头电子烟", "依托咪酯", "聪明药买卖", "蓝精灵药",
        "麦角酸", "lsd邮票", "毒邮票", "开心水", "神仙水", "浴盐毒品",
        "冰糖毒品", "溜冰毒品", "飞叶子", "飞大麻", "叶子交易", "草料毒品",
        "毒品货源", "毒品渠道", "毒品配送", "毒品暗号", "毒品交易", "毒品价格",
        "drugdealer", "buycocaine", "buymarijuana", "methdealer",
    ),
}


_EXPANSION_PREFIXES = (
    "在线", "同城", "附近", "免费", "低价", "专业", "私人", "内部", "独家", "最新",
    "高清", "无码", "快速", "稳定", "安全", "匿名", "海外", "手机", "全网", "官方",
    "真人", "一对一", "包教包会", "长期", "高薪", "兼职", "代理", "平台", "福利", "资源",
)
_EXPANSION_SUFFIXES = (
    "服务", "平台", "网站", "网址", "入口", "群", "群聊", "客服", "代理", "推广",
    "资源", "教程", "项目", "渠道", "合作", "咨询", "下载", "注册", "开户", "会员",
    "福利", "返利", "赚钱", "联系", "加群", "邀请码", "导航", "社区", "工作室", "团队",
)


def _expanded_filter_terms() -> dict[str, tuple[str, ...]]:
    """Build an auditable 1000+ phrase lexicon from curated risk roots.

    Matching still checks the curated roots and dedicated behavior detectors, so
    attackers cannot evade a phrase merely by using an unseen prefix/suffix.
    """
    result: dict[str, tuple[str, ...]] = {}
    for category, roots in CORE_FILTER_TERMS.items():
        terms = set(roots)
        for root in roots:
            terms.update(f"{prefix}{root}" for prefix in _EXPANSION_PREFIXES)
            terms.update(f"{root}{suffix}" for suffix in _EXPANSION_SUFFIXES)
        result[category] = tuple(sorted(terms))
    return result


FILTER_TERMS = _expanded_filter_terms()
FILTER_TERM_COUNT = sum(len(terms) for terms in FILTER_TERMS.values())
if FILTER_TERM_COUNT < 1000:  # Contract guard: do not silently shrink the lexicon.
    raise RuntimeError("内容过滤词库不得少于 1000 条")


_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "к": "k", "м": "m", "т": "t", "в": "b", "н": "h", "і": "i", "ј": "j",
    "ɑ": "a", "ο": "o", "ϲ": "c", "ν": "v", "ѕ": "s", "Ⅰ": "i", "Ⅴ": "v",
})
_LINK_SEPARATORS = re.compile(r"[\s_+\-—·•,，/\\|:：;；'\"`~!！?？()（）\[\]{}<>《》]+")
_NAMED_DOT = re.compile(
    r"(?:\.|。|．|点|點|丶|句号|小数点|d\W*[o0]\W*t|d\W*i\W*a\W*n)",
    re.IGNORECASE,
)
_ASCII_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "9": "g"})
_TLDS = (
    "com|cn|net|org|xyz|top|vip|io|cc|me|app|site|club|live|shop|pro|info|biz|"
    "online|wang|link|work|fun|bet|casino|pw|tv|co|us|uk|jp|hk|tw|ru"
)

_URL_PATTERNS = (
    re.compile(r"(?:h\W*[t7]\W*[t7]\W*p|h\W*x\W*x\W*p|ftp)\W*s?\W*[:：]?\W*/?\W*/?", re.IGNORECASE),
    re.compile(r"w\W*w\W*w(?:\W|点|點|d\W*o\W*t)+", re.IGNORECASE),
    re.compile(r"\b[a-z0-9._%+-]+\s*@\s*[a-z0-9.-]+\s*\.\s*[a-z]{2,}\b", re.IGNORECASE),
)

_CONTACT_MARKERS = (
    "微信", "微号", "微聊", "薇信", "威信", "维信", "v信", "vx", "wx", "weixin",
    "qq", "扣扣", "企鹅号", "电报", "电报群", "telegram", "tg群", "飞机群", "纸飞机",
    "whatsapp", "line群", "联系方式", "联系我", "私聊我", "私信我", "加好友", "扫码",
    "二维码", "群号", "进群", "入群", "加群", "客服号", "代理号",
)
_STRONG_CONTACT_MARKERS = (
    "二维码", "扫码", "群号", "进群", "入群", "加群", "加好友", "联系方式",
    "联系我", "私聊我", "私信我", "客服号", "代理号",
)
_PROMOTION_MARKERS = (
    "赚钱", "网赚", "副业", "兼职", "高薪", "日结", "返现", "返佣", "推广", "引流",
    "招代理", "招募代理", "诚招代理", "招商", "加盟", "开户", "带单", "导师", "内部群",
    "资源群", "福利群", "项目群", "交流群", "稳赚", "躺赚", "暴利", "玩球", "买球",
)
_HANDLE_AFTER_CONTACT = re.compile(
    r"(?:加|添加|联系|私聊|私信|找|搜索|搜|关注|扫码|进群|我)?"
    r"(?:微信|微号|薇信|威信|维信|v信|vx|wx|qq|扣扣|电报|telegram|tg|飞机|whatsapp|line)"
    r"(?:号|群|我)?[a-z0-9-]{3,32}",
    re.IGNORECASE,
)
_V_HANDLE = re.compile(r"(?:加|找|联系|私聊|私信|我)v[a-z0-9-]{4,32}", re.IGNORECASE)
_PHONE_NUMBER = re.compile(r"(?<!\d)1[3-9](?:[\s_+\-·•()（）]*\d){9}(?!\d)")
_SEPARATED_ASCII = re.compile(
    r"(?<![a-z0-9])(?:[a-z0-9][\s_+\-·•.,，。．/\\|:：;；]+){5,}[a-z0-9](?![a-z0-9])",
    re.IGNORECASE,
)
_IDENTITY_ONLY_TERMS = (
    *_CONTACT_MARKERS,
    *_PROMOTION_MARKERS,
    "加我", "找我", "私聊", "私信", "联系", "客服", "代理", "渠道", "邀请码",
)


def _normalized(value: str) -> tuple[str, str]:
    visible = ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", str(value or ""))).casefold()
    visible = visible.translate(_CONFUSABLES)
    compact = re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", visible)
    return visible, compact


_NORMALIZED_CORE_TERMS = {
    category: tuple(
        normalized
        for term in terms
        if (normalized := _normalized(term)[1])
    )
    for category, terms in CORE_FILTER_TERMS.items()
}


def _link_or_contact_category(visible: str, compact: str, *, identity: bool) -> str:
    for pattern in _URL_PATTERNS:
        if pattern.search(visible):
            return "链接"
    domain_ready = _LINK_SEPARATORS.sub("", _NAMED_DOT.sub(".", visible))
    leet_domain_ready = domain_ready.translate(_ASCII_LEET)
    if re.search(rf"(?:^|[^a-z0-9])(?:[a-z0-9][a-z0-9-]{{1,62}}\.)+(?:{_TLDS})(?:$|[^a-z0-9])", leet_domain_ready):
        return "链接"
    if re.search(r"(?:^|[^0-9])(?:\d{1,3}\.){3}\d{1,3}(?::\d{2,5})?(?:$|[^0-9])", domain_ready):
        return "链接"
    separated_source = _NAMED_DOT.sub(" ", visible)
    for separated in _SEPARATED_ASCII.findall(separated_source):
        collapsed = re.sub(r"[^a-z0-9]", "", separated.casefold()).translate(_ASCII_LEET)
        if re.search(rf"[a-z0-9]{{3,}}(?:{_TLDS})$", collapsed):
            return "链接"
    if re.search(rf"(?:https?|hxxps?|www)[a-z0-9-]{{3,}}(?:{_TLDS})", compact.translate(_ASCII_LEET)):
        return "链接"
    if _PHONE_NUMBER.search(visible):
        return "引流"
    if any(marker in compact for marker in _STRONG_CONTACT_MARKERS):
        return "引流"
    if _HANDLE_AFTER_CONTACT.search(compact) or _V_HANDLE.search(compact):
        return "引流"
    if any(marker in compact for marker in _CONTACT_MARKERS) and re.search(
        r"[a-z0-9]{4,}", compact
    ):
        return "引流"
    if identity and any(marker in compact for marker in _CONTACT_MARKERS):
        return "引流"
    if any(marker in compact for marker in _PROMOTION_MARKERS) and re.search(
        r"[a-z0-9]{4,}", compact
    ):
        return "引流"
    return ""


def _reject(category: str, *, identity: bool) -> ModerationResult:
    if identity:
        return ModerationResult(
            False,
            category,
            "这个昵称暂时无法使用。请去掉联系方式、广告引流或不合适的内容后，再试一个更纯粹的名字。",
        )
    if category in {"链接", "引流"}:
        return ModerationResult(
            False,
            category,
            "这条评论需要修改。评论里似乎包含网站、联系方式或推广内容，请删除相关内容后再发布，让「字里行间」只留下阅读交流。",
        )
    return ModerationResult(
        False,
        category,
        "这条评论暂时不能发布。内容可能不符合社区交流规范，请调整措辞、去掉不合适的内容后再发布。",
    )


def _moderate(value: str, *, identity: bool) -> ModerationResult:
    visible, compact = _normalized(value)
    category = _link_or_contact_category(visible, compact, identity=identity)
    if category:
        return _reject(category, identity=identity)
    if identity and any(term in compact for term in _IDENTITY_ONLY_TERMS):
        return _reject("引流", identity=True)
    for category, terms in _NORMALIZED_CORE_TERMS.items():
        for normalized_term in terms:
            if normalized_term and normalized_term in compact:
                return _reject(category, identity=identity)
    return ModerationResult(True)


def moderate_comment(value: str) -> ModerationResult:
    return _moderate(value, identity=False)


def moderate_display_name(value: str) -> ModerationResult:
    return _moderate(value, identity=True)


def moderation_stats() -> dict[str, object]:
    return {
        "term_count": FILTER_TERM_COUNT,
        "categories": tuple(FILTER_TERMS),
        "normalization": "NFKC+zero-width+bidi+separator+confusable",
    }


def chapter_paragraphs(content: str) -> list[dict[str, object]]:
    paragraphs: list[dict[str, object]] = []
    for line in str(content or "").replace("\r\n", "\n").split("\n"):
        if not line.strip() or ILLUSTRATION_LINE.fullmatch(line.strip()):
            continue
        index = len(paragraphs)
        normalized = unicodedata.normalize("NFKC", line).strip()
        digest = hashlib.sha256(f"{index}\0{normalized}".encode("utf-8")).hexdigest()[:16]
        paragraphs.append({
            "index": index,
            "key": f"p{index}-{digest}",
            "text": line,
            "excerpt": normalized[:160],
        })
    return paragraphs
