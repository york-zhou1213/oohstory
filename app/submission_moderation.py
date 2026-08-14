"""Fail-closed content evidence for reader-contributed books and analyses."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import re
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


MAX_REVIEW_TEXT_BYTES = 64 * 1024 * 1024
MAX_REVIEW_TEXT_FILES = 20_000
TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".html",
    ".htm",
    ".xhtml",
    ".ncx",
    ".opf",
}
HTML_SUFFIXES = {".html", ".htm", ".xhtml", ".ncx", ".opf"}
ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
TRUSTED_REFERENCE = re.compile(
    r"https?://github\.com/worldwonderer/oh-story-claudecode/?",
    re.IGNORECASE,
)

URL_PATTERNS = (
    re.compile(r"(?:https?|ftp)\s*[:：]\s*/\s*/", re.IGNORECASE),
    re.compile(
        r"h[\W_]*t[\W_]*t[\W_]*p[\W_]*s?[\W_]*[:：]?[\W_]*/?[\W_]*/?", re.IGNORECASE
    ),
    re.compile(r"w[\W_]*w[\W_]*w(?:[\W_]|点|d[\W_]*o[\W_]*t)+", re.IGNORECASE),
    re.compile(
        r"[a-z0-9](?:[a-z0-9\s_-]{1,62})\s*(?:\.|。|点|d\s*o\s*t)\s*"
        r"(?:c\s*o\s*m|c\s*n|n\s*e\s*t|o\s*r\s*g|x\s*y\s*z|t\s*o\s*p|"
        r"v\s*i\s*p|i\s*o|c\s*c|m\s*e|a\s*p\s*p)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b[a-z0-9._%+-]+\s*@\s*[a-z0-9.-]+\s*\.\s*[a-z]{2,}\b", re.IGNORECASE),
)
CONTACT_PATTERN = re.compile(
    r"(?:加\s*(?:微|v|vx|qq)|微信|微\s*信|q\s*q|telegram|电报|飞机)"
    r"\s*(?:号|群|：|:)?\s*[a-z0-9_-]{5,}",
    re.IGNORECASE,
)
QR_PROMOTION_PATTERN = re.compile(
    r"(?:二维码|扫\s*码).{0,24}(?:客服|领取|注册|加入|下载|充值|返利)"
)

RISK_TERMS: dict[str, tuple[str, ...]] = {
    "涉黄": (
        "色情",
        "成人视频",
        "成人影片",
        "黄色网站",
        "黄网",
        "无码",
        "裸聊",
        "约炮",
        "上门服务",
        "性服务",
        "援交",
        "卖淫",
        "嫖娼",
        "迷奸",
        "乱伦",
        "性交",
        "口交",
        "肛交",
        "阴茎",
        "阴道",
        "射精",
        "porn",
        "onlyfans",
    ),
    "涉毒": (
        "冰毒",
        "海洛因",
        "可卡因",
        "摇头丸",
        "麻古",
        "麻果",
        "k粉",
        "芬太尼",
        "大麻",
        "毒品交易",
        "制毒",
        "贩毒",
        "吸毒教程",
    ),
    "涉赌": (
        "博彩",
        "赌博",
        "赌场",
        "下注",
        "押注",
        "赌盘",
        "盘口",
        "在线投注",
        "时时彩",
        "六合彩",
        "北京赛车",
        "外围盘",
        "买球",
        "casino",
        "betting",
    ),
    "涉诈": (
        "刷单",
        "返利",
        "跑分",
        "杀猪盘",
        "代付",
        "套现",
        "保本稳赚",
        "稳赚不赔",
        "高额回报",
        "内幕群",
        "验证码转发",
        "冒充客服",
        "投资带单",
        "网贷下款",
    ),
}
# 小说原文和拆解报告允许出现虚构赌局。这个分类只在出现现实开户、充值、
# 客服、推广等招揽证据时触发硬拒绝；普通剧情词不再作为语义审核风险信号。
FICTION_ALLOWED_SIGNAL_CATEGORIES = {"涉赌"}
FICTION_GAMBLING_MARKERS = (
    "赌狗",
    "赌局",
    "赌盘",
    "下注",
    "押注",
    "投注",
    "全压",
    "筹码",
    "游戏",
    "角色",
    "观众",
)
AMBIGUOUS_FRAUD_TERMS_IN_FICTION = {"保本稳赚", "稳赚不赔"}
PROMOTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "涉黄": re.compile(
        r"(?:裸聊|约炮|成人视频|色情网站|黄网|上门服务|性服务|援交).{0,48}"
        r"(?:联系|客服|价格|地址|网站|扫码|进群|加微|微信)",
        re.IGNORECASE,
    ),
    "涉毒": re.compile(
        r"(?:冰毒|海洛因|可卡因|摇头丸|麻古|麻果|k粉|芬太尼|大麻).{0,48}"
        r"(?:出售|购买|出货|货源|渠道|价格|联系|交易|邮寄|同城)",
        re.IGNORECASE,
    ),
    "涉赌": re.compile(
        r"(?:博彩|赌场|下注|投注|六合彩|时时彩|北京赛车|外围盘|买球).{0,56}"
        r"(?:开户|充值|返利|送彩金|代理|客服|网站|扫码|进群|注册)",
        re.IGNORECASE,
    ),
    "涉诈": re.compile(
        r"(?:刷单|返利|跑分|杀猪盘|投资带单|高额回报|网贷下款).{0,56}"
        r"(?:联系|客服|入群|加微|微信|转账|充值|垫付|验证码)",
        re.IGNORECASE,
    ),
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data)


@dataclass(frozen=True)
class _Document:
    source: str
    text: str
    scan_text: str


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _html_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return unescape("\n".join(parser.parts))


def _document(source: str, data: bytes) -> _Document:
    decoded = _decode(data)
    suffix = PurePosixPath(source).suffix.casefold()
    text = _html_text(decoded) if suffix in HTML_SUFFIXES else decoded
    return _Document(source=source, text=text, scan_text=decoded)


def _novel_documents(path: Path) -> tuple[list[_Document], int, bool]:
    if path.suffix.casefold() != ".epub":
        size = path.stat().st_size
        if size > MAX_REVIEW_TEXT_BYTES:
            return [], size, False
        return [_document(path.name, path.read_bytes())], size, True

    documents: list[_Document] = []
    total = 0
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_REVIEW_TEXT_FILES:
            return [], 0, False
        for info in infos:
            member = PurePosixPath(info.filename)
            if info.is_dir() or member.suffix.casefold() not in TEXT_SUFFIXES:
                continue
            if member.is_absolute() or ".." in member.parts or info.flag_bits & 0x1:
                return [], total, False
            total += max(int(info.file_size), 0)
            if total > MAX_REVIEW_TEXT_BYTES:
                return [], total, False
            documents.append(_document(member.as_posix(), archive.read(info)))
    return documents, total, True


def _deconstruction_documents(root: Path) -> tuple[list[_Document], int, bool]:
    resolved_root = root.resolve()
    documents: list[_Document] = []
    total = 0
    candidates = sorted(path for path in resolved_root.rglob("*") if path.is_file())
    if len(candidates) > MAX_REVIEW_TEXT_FILES:
        return [], 0, False
    for path in candidates:
        resolved = path.resolve()
        if path.is_symlink() or resolved_root not in resolved.parents:
            return [], total, False
        if path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        total += path.stat().st_size
        if total > MAX_REVIEW_TEXT_BYTES:
            return [], total, False
        documents.append(
            _document(path.relative_to(resolved_root).as_posix(), path.read_bytes())
        )
    return documents, total, True


def _normalized(value: str) -> tuple[str, str]:
    visible = ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", value)).casefold()
    compact = re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", visible)
    return visible, compact


def _context(value: str, match: re.Match[str] | None = None) -> str:
    if match is None:
        return " ".join(value[:360].split())
    start = max(0, match.start() - 120)
    end = min(len(value), match.end() + 240)
    return " ".join(value[start:end].split())[:500]


def _line_evidence(value: str, match: re.Match[str]) -> str:
    """Return the matched line so users can fix a rejection without guesswork."""
    start = value.rfind("\n", 0, match.start()) + 1
    end = value.find("\n", match.end())
    if end < 0:
        end = len(value)
    line = " ".join(value[start:end].split())
    if len(line) <= 240:
        return line
    relative = max(0, match.start() - start)
    left = max(0, relative - 90)
    return line[left : left + 240]


def _risk_signals(
    documents: Iterable[_Document],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    counts = {key: 0 for key in RISK_TERMS}
    sources = {key: set() for key in RISK_TERMS}
    contexts: dict[str, list[str]] = {key: [] for key in RISK_TERMS}
    hard_evidence: dict[str, dict[str, Any]] = {}

    def record_hard(
        category: str,
        document: _Document,
        value: str,
        match: re.Match[str],
    ) -> None:
        evidence = hard_evidence.setdefault(
            category,
            {"sources": set(), "contexts": []},
        )
        evidence["sources"].add(document.source)
        context = _line_evidence(value, match)
        if context and context not in evidence["contexts"]:
            evidence["contexts"].append(context)

    for document in documents:
        scan_text = TRUSTED_REFERENCE.sub("", document.scan_text)
        visible, compact = _normalized(scan_text)
        for pattern in URL_PATTERNS:
            match = pattern.search(visible)
            if match:
                record_hard("链接或引流", document, visible, match)
                contexts.setdefault("链接或引流", []).append(_context(visible, match))
                break
        contact = CONTACT_PATTERN.search(visible)
        qr_promotion = QR_PROMOTION_PATTERN.search(visible)
        contact = contact or qr_promotion
        if contact:
            record_hard("联系方式或引流", document, visible, contact)
            contexts.setdefault("联系方式或引流", []).append(_context(visible, contact))
        for category, pattern in PROMOTION_PATTERNS.items():
            match = pattern.search(visible)
            if match:
                record_hard(category, document, visible, match)
                contexts[category].append(_context(visible, match))
        for category, terms in RISK_TERMS.items():
            if category in FICTION_ALLOWED_SIGNAL_CATEGORIES:
                continue
            for term in terms:
                _term_visible, compact_term = _normalized(term)
                count = compact.count(compact_term) if compact_term else 0
                if (
                    count
                    and category == "涉诈"
                    and term in AMBIGUOUS_FRAUD_TERMS_IN_FICTION
                ):
                    positions = [
                        match.start()
                        for match in re.finditer(re.escape(compact_term), compact)
                    ]
                    count = sum(
                        1
                        for position in positions
                        if not any(
                            marker
                            in compact[
                                max(0, position - 80) : position
                                + len(compact_term)
                                + 80
                            ]
                            for marker in FICTION_GAMBLING_MARKERS
                        )
                    )
                if not count:
                    continue
                counts[category] += count
                sources[category].add(document.source)
                if len(contexts[category]) < 3:
                    location = visible.find(_term_visible)
                    fragment = (
                        visible
                        if location < 0
                        else visible[max(0, location - 120) : location + 360]
                    )
                    contexts[category].append(" ".join(fragment.split())[:500])
    signals = [
        {
            "category": category,
            "matches": counts[category],
            "sources": sorted(sources[category])[:20],
            "contexts": contexts[category][:3],
        }
        for category in RISK_TERMS
        if counts[category]
    ]
    for category, evidence in hard_evidence.items():
        signals.append(
            {
                "category": category,
                "matches": len(evidence["contexts"]),
                "sources": sorted(evidence["sources"])[:20],
                "contexts": evidence["contexts"][:3],
                "hard_reject": True,
            }
        )
    normalized_evidence = {
        category: {
            "sources": sorted(evidence["sources"])[:20],
            "contexts": evidence["contexts"][:3],
        }
        for category, evidence in hard_evidence.items()
    }
    return signals, normalized_evidence


def _stratified_sample(documents: list[_Document], window: int = 1_400) -> str:
    combined = "\n\n".join(
        f"[文件:{item.source}]\n{item.text}" for item in documents if item.text.strip()
    )
    if not combined:
        return ""
    if len(combined) <= window * 9:
        return combined
    positions = (0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0)
    segments: list[str] = []
    for position in positions:
        center = int((len(combined) - 1) * position)
        start = min(max(0, center - window // 2), len(combined) - window)
        segments.append(
            f"[全稿位置:{int(position * 100)}%]\n{combined[start : start + window]}"
        )
    return "\n\n".join(segments)


def inspect_submission_content(*, kind: str, path: Path) -> dict[str, Any]:
    if kind == "novel":
        documents, scanned_bytes, complete = _novel_documents(path)
    elif kind == "deconstruction":
        documents, scanned_bytes, complete = _deconstruction_documents(path)
    else:
        raise ValueError("投稿类型无效")
    if not complete:
        return {
            "decision": "reject",
            "reason": "投稿文本超过完整审核安全上限，无法在不遗漏内容的前提下完成审核。",
            "issues": ["全文内容覆盖不完整"],
            "coverage": {
                "complete": False,
                "files_scanned": 0,
                "text_bytes": scanned_bytes,
            },
            "risk_signals": [],
            "sample_text": "",
        }
    nonempty = [item for item in documents if item.text.strip()]
    if not nonempty:
        return {
            "decision": "reject",
            "reason": "投稿中未发现可审核的有效正文。",
            "issues": ["缺少可读正文"],
            "coverage": {
                "complete": True,
                "files_scanned": len(documents),
                "text_bytes": scanned_bytes,
            },
            "risk_signals": [],
            "sample_text": "",
        }
    signals, hard_evidence = _risk_signals(nonempty)
    reason = ""
    issues: list[str] = []
    if hard_evidence:
        hard_category, evidence = next(iter(hard_evidence.items()))
        location = "、".join(evidence["sources"][:5]) or "正文"
        context = str((evidence["contexts"] or ["未能生成命中片段"])[0])
        guidance = {
            "链接或引流": "请删除网址、域名及引导访问其他站点的文案后重新上传",
            "联系方式或引流": "请删除联系方式、二维码及引导联系或扫码的文案后重新上传",
            "涉黄": "请删除带有交易、联系方式或推广意图的色情文案后重新上传",
            "涉毒": "请删除带有购买、出售、渠道或推广意图的涉毒文案后重新上传",
            "涉赌": "请删除带有开户、充值、客服或推广意图的博彩文案后重新上传",
            "涉诈": "请删除带有转账、垫付、入群或推广意图的诈骗文案后重新上传",
        }.get(hard_category, "请删除命中内容后重新上传")
        reason = (
            f"内容安全审核未通过：检测到{hard_category}风险。"
            f"具体文件：{location}。命中片段：{context}。处理建议：{guidance}。"
        )
        issues = [f"{hard_category}风险", f"具体文件：{location}"]
    return {
        "decision": "reject" if reason else "continue",
        "reason": reason,
        "issues": issues,
        "coverage": {
            "complete": True,
            "files_scanned": len(nonempty),
            "text_bytes": scanned_bytes,
            "characters": sum(len(item.text) for item in nonempty),
            "sampling": "全文规则扫描 + 0/12/25/37/50/62/75/87/100% 分层抽样",
        },
        "risk_signals": signals,
        "sample_text": _stratified_sample(nonempty),
        "required_checks": [
            "标题、简介、结构报告与正文主题一致性",
            "涉黄、涉毒、诈骗、现实赌博推广、引流及联系方式",
            "小说原文和拆解中的虚构赌局、下注、押注情节允许，不得据此拒绝",
            "正常书籍外观下隐藏的广告、网址、推广或违法内容",
        ],
    }
