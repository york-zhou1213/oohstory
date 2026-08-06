"""电子书库服务。

边界约束：
1. MySQL 模式不允许打开任何 SQLite 文件；
2. MySQL 保存书目、派生元数据与对象索引；6379 Redis 只承担可重建队列，
   可选 6380 Redis 只承担可丢弃热缓存；
3. SQLite 仅保留为显式 legacy/migration 模式的兼容输入；
4. 拆书产物与任务记录写入全局拆书库，供所有小说项目复用。
"""

from __future__ import annotations

import asyncio
import copy
import difflib
import fcntl
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import shutil
import unicodedata
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from typing import Callable
from xml.etree import ElementTree
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from oohstory_library.services.cover_failure_policy import should_generate_ai_fallback
from oohstory_library.services.download_security import DownloadSecurityScanner
from oohstory_library.services.authorized_source_recovery import downloaded_text_matches_identity
from oohstory_library.services.fanqie_downloader_bridge import FanqieDownloaderBridge
from oohstory_library.services.ixdzs_provider import AuthorizedIxdzsProvider
from oohstory_library.services.linovelib_provider import LinovelibProvider
from oohstory_library.services.shubaow_provider import AuthorizedShubaowProvider
from oohstory_library.services.library_catalog import normalize_catalog_title
from oohstory_library.services.library_catalog_deletion import (
    CatalogDeletionArchive,
    normalize_catalog_delete_request,
)
from oohstory_library.services.library_catalog_mysql import MySQLCatalogStore
from oohstory_library.services.library_cache import (
    LibraryCacheSettings,
    RedisHotCache,
)
from oohstory_library.services.library_covers import sync_fanqie_cover, sync_remote_cover
from oohstory_library.services.library_database import (
    LibraryInfrastructureSettings,
    MySQLConnectionPool,
    RedisQueueClient,
)
from oohstory_library.services.library_object_store import NasObjectStore
from oohstory_library.services.runtime_controls import (
    COVER_MAX_WORKERS,
    COVER_TARGET_PER_HOUR_DEFAULT,
    OOHStoryRuntimeControls,
    SITE_IDS,
    SITE_BOOKS_PER_CYCLE_DEFAULT,
    cover_worker_count,
    validate_cover_target_per_hour,
    validate_site_books_per_cycle,
)
from oohstory_library.services.library_runtime_mysql import MySQLLibraryRuntime
from oohstory_library.services.library_task_runners import (
    max_parallel_tasks,
    openclaw_session_lineage_state,
    resolve_task_runner,
    rotate_openclaw_session,
)
from oohstory_library.services.unit_names import library_unit_name
from oohstory_library.services.oh_story_contracts import (
    long_contract_failed_stages,
    long_pipeline_stages,
    long_progress_state,
    long_summary_coverage,
    project_long_contract_failures,
    read_short_meta,
    short_pipeline_stages,
    validate_long_output_contract,
    validate_short_output_contract,
)
import aiohttp
from oohstory_library import library_env
from oohstory_library.services.tone_catalog import (
    CATEGORY_TONE_PRIORS,
    DEFAULT_TONE_PRIORS,
    TONE_DESCRIPTIONS,
    TONE_RULES,
    TONE_RULE_VERSION,
    TONE_TAG_CATALOG,
)
from oohstory_library.services.txt80_provider import AuthorizedTxt80Provider
from oohstory_library.services.xbiquge_provider import AuthorizedXbiqugeProvider
from oohstory_library.services.zlibrary_provider import AuthorizedZLibraryProvider


APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LIBRARY_ROOT = APP_ROOT / "electronic-library" / "txt80"
DEFAULT_RUNTIME_DIR = DEFAULT_LIBRARY_ROOT / "全局索引"
WORKER_PATH = Path(__file__).with_name("library_task_worker.py")
BATCH_WORKER_PATH = Path(__file__).with_name("library_batch_worker.py")
TASK_WORKER_SYSTEMD_TEMPLATE = library_unit_name(
    "oohstory-library-task-worker@{task_id}.service"
)
DERIVED_INDEX_SERVICE = library_unit_name(
    "oohstory-library-derived-index.service"
)
INGESTION_INDEX_SERVICE = library_unit_name(
    "oohstory-library-ingestion-index.service"
)
MANUAL_PLOT_REASON_PREFIX = "manual_plot_"
GLOBAL_DECONSTRUCTION_DIRNAME = "全局拆书库"
PROJECT_DECONSTRUCTION_LINKS_FILENAME = ".project-deconstruction-links.json"
DECONSTRUCTION_STALE_SECONDS = 15 * 60
DECONSTRUCTION_CACHE_RUNNING_SECONDS = 30
PUBLIC_BOOK_PROVIDER = "project_gutenberg"
GUTENDEX_API = "https://gutendex.com/books"
AUTHORIZED_ZLIBRARY_PROVIDER = AuthorizedZLibraryProvider.PROVIDER_ID
AUTHORIZED_TXT80_PROVIDER = AuthorizedTxt80Provider.PROVIDER_ID
AUTHORIZED_XBIQUGE_PROVIDER = AuthorizedXbiqugeProvider.PROVIDER_ID
AUTHORIZED_IXDZS_PROVIDER = AuthorizedIxdzsProvider.PROVIDER_ID
AUTHORIZED_SHUBAOW_PROVIDER = AuthorizedShubaowProvider.PROVIDER_ID
LINOVELIB_PROVIDER = LinovelibProvider.PROVIDER_ID
FANQIE_DOWNLOADER_PROVIDER = FanqieDownloaderBridge.PROVIDER_ID
LOCAL_CATALOG_PROVIDER = "local_txt80_catalog"
SERIALIZATION_STATUS_ONGOING = "连载中"
SERIALIZATION_STATUS_COMPLETED = "已完结"
LEGACY_LIBRARY_NOTICE = "本书为八零电子书(txt8080.com)"
PUBLIC_DOMAIN = os.getenv("OOHSTORY_PUBLIC_DOMAIN", "reader.example.com").strip() or "reader.example.com"
LEGACY_REBRANDED_NOTICE = f"本书为八零电子书({PUBLIC_DOMAIN})"
PUBLIC_LIBRARY_NOTICE = f"本书为Ooh！好故事({PUBLIC_DOMAIN})"
IXDZS_PROMOTIONAL_NOTICE = (
    "爱下电子书Txt版阅读,下载和分享更多电子书请访问，"
    "简体:https://ixdzs8.com,繁体:https://ixdzs8.tw,"
    "E-mail:support@ixdzs.com"
)
OOHSTORY_EBOOK_NOTICE = f"{PUBLIC_DOMAIN}，好故事电子书"
LOCAL_MEDIA_MARKER_RE = re.compile(
    r"^\[本地(?:分卷封面|插图)：[^\]\r\n]+\]$"
)
SUMMARY_HEADING_RE = re.compile(
    r"^(?:(?:作品|内容|小说)简介|简介)[：:\s]*(.*)$"
)
CHAPTER_HEADING_TOKEN_RE = re.compile(
    r"(?:^|\s)(?:序章|楔子|引子|前言|"
    r"第\s*[0-9零〇一二三四五六七八九十百千万两]+\s*[章回节]|"
    r"(?:chapter|prologue)\s*[0-9ivxlcdm]*)"
    r"(?:\s|$|[：:])",
    re.IGNORECASE,
)
SUMMARY_METADATA_RE = re.compile(
    r"^(?:作者|分类|来源文库|状态|章节数|分卷数|分卷封面数|插图数)[：:]"
)
LEGACY_LIBRARY_DOMAIN_RE = re.compile(
    r"(?:(?:www\.)?txt80\.cc|(?:www\.)?txt02\.com|ohhstory\.com)",
    re.IGNORECASE,
)


def _automatic_full_pipeline_needs_recovery(task: Dict[str, Any]) -> bool:
    """Return whether an interrupted managed full run must self-resume."""

    analyze_was_running = any(
        step.get("id") == "analyze" and step.get("status") == "running"
        for step in (task.get("steps") or [])
        if isinstance(step, dict)
    )
    full_scope = bool(
        task.get("automatic_full_pipeline")
        or (
            task.get("requested_mode") == "full"
            and analyze_was_running
        )
    )
    if not full_scope or task.get("runner_requested") != "openclaw":
        return False
    if task.get("status") in {"queued", "running"}:
        return True
    return bool(
        task.get("status") == "paused"
        and task.get("pause_reason") in {"stalled_checkpoint", "task_interrupted"}
        and analyze_was_running
    )


READER_HEADING_DECORATION = (
    r"(?:[☆★◆◇●○◎※·•▶▷]+\s*[、.．:：\-—]?\s*)?"
)
READER_HEADING_LINE = re.compile(
    rf"^\s*{READER_HEADING_DECORATION}(?:(?:正文)\s*)?"
    r"(?:0*[1-9]\d{0,5}\s*)?"
    r"(?:(?:(?:第\s*[0-9零一二三四五六七八九十百千万两〇○ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVXLC]+\s*卷"
    r"|卷\s*[0-9零一二三四五六七八九十百千万两〇○ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVXLC]+)"
    r"[^\n]{0,80}?)\s+)?"
    r"(?P<label>第\s*[0-9零一二三四五六七八九十百千万两〇○]+\s*(?P<unit>[章回节]))"
    r"(?P<title>[^\n]{0,120})\s*$",
    re.IGNORECASE,
)
READER_NUMERIC_HEADING_LINE = re.compile(
    rf"^\s*{READER_HEADING_DECORATION}"
    r"(?P<number>0*[1-9]\d{0,5})"
    r"\s*(?:章|回|节)?"
    r"(?:(?:[、.．:：\-—_]\s*|\s+)(?P<title>\S[^\n]{0,119}))?\s*$"
)
READER_BRACKET_NUMERIC_HEADING_LINE = re.compile(
    rf"^\s*{READER_HEADING_DECORATION}"
    r"[【\[(（]\s*(?P<number>0*[1-9]\d{0,5})\s*[】\])）]"
    r"\s*(?P<title>\S[^\n]{0,119})?\s*$"
)
READER_BODY_NUMBER_HEADING_LINE = re.compile(
    rf"^\s*{READER_HEADING_DECORATION}"
    r"(?P<prefix>正文(?:片段|章节?)?|章节?|片段)"
    r"\s*[、.．:：\-—_#]*\s*(?P<number>0*[1-9]\d{0,5})"
    r"(?:(?:[、.．:：\-—_]\s*|\s+)(?P<title>\S[^\n]{0,99}))?\s*$"
)
READER_SUFFIX_NUMBER_HEADING_LINE = re.compile(
    rf"^\s*{READER_HEADING_DECORATION}"
    r"(?P<title>\S[^\n]{0,108}?)"
    r"(?:[【\[(（]\s*(?P<bracket_number>0*[1-9]\d{0,5})\s*[】\])）]"
    r"|(?P<number>0*[1-9]\d{0,5}))\s*$"
)
READER_DECORATED_HEADING_LINE = re.compile(
    r"^\s*(?P<decoration>[☆★◆◇●○◎※▶▷]+)"
    r"\s*[、.．:：\-—]?\s*(?P<title>\S[^\n]{0,119})\s*$"
)
READER_SPECIAL_HEADING_LINE = re.compile(
    rf"^\s*{READER_HEADING_DECORATION}"
    r"(?P<label>序章|楔子|引子|前言|后记|尾声|终章|"
    r"番外(?:篇|章)?(?:\s*[0-9零一二三四五六七八九十百千万两〇○]+)?)"
    r"(?:(?:\s+|[：:、—\-]+)\s*(?P<title>\S[^\n]{0,119}))?\s*$"
)
READER_INDEX_SCHEMA_VERSION = 6
READER_FALLBACK_CHUNK_BYTES = 256 * 1024


def _reader_label_number(label: str) -> Optional[int]:
    token_match = re.search(
        r"第\s*([0-9零一二三四五六七八九十百千万两〇○]+)\s*[章回节]",
        label or "",
    )
    if not token_match:
        return None
    token = token_match.group(1)
    if token.isdigit():
        return int(token)
    digits = {
        "零": 0,
        "〇": 0,
        "○": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    total = 0
    section = 0
    number = 0
    for character in token:
        if character in digits:
            number = digits[character]
            continue
        unit = units.get(character)
        if not unit:
            return None
        if unit == 10000:
            section = (section + number) * unit
            total += section
            section = 0
        else:
            section += (number or 1) * unit
        number = 0
    value = total + section + number
    return value if value > 0 else None


SYNC_TIMER_UNITS: Dict[str, tuple[str, ...]] = {
    "local": (
        library_unit_name("oohstory-library-local-sync.timer"),
        library_unit_name("oohstory-library-catalog-scan.timer"),
    ),
    "fanqie": (
        library_unit_name("oohstory-library-fanqie-sync.timer"),
        library_unit_name("oohstory-library-authorized-catalog-sync.timer"),
    ),
}
SYNC_ON_DEMAND_UNITS: Dict[str, str] = {
    # Opening a content switch must consume the existing pending/update queue
    # immediately.  The much slower full source-catalog discovery remains on
    # its own weekly timer and must not delay an explicit user action.
    "local": library_unit_name("oohstory-library-local-sync.service"),
    "fanqie": library_unit_name("oohstory-library-fanqie-sync.service"),
}
SYNC_CONTENT_SERVICE_UNITS: Dict[str, tuple[str, ...]] = {
    "local": (
        library_unit_name("oohstory-library-local-sync.service"),
        library_unit_name("oohstory-library-catalog-scan.service"),
    ),
    "fanqie": (
        library_unit_name("oohstory-library-fanqie-sync.service"),
        library_unit_name("oohstory-library-authorized-catalog-sync.service"),
    ),
}
SYNC_CONTENT_ON_ENABLE_UNITS: Dict[str, tuple[str, ...]] = {
    # Full local source-catalog discovery remains weekly; opening the switch
    # only drains the already-known local queue immediately.
    "local": (library_unit_name("oohstory-library-local-sync.service"),),
    # The logical Fanqie library also contains authorized-source books, so its
    # visible content switch must actually control and kick both pipelines.
    "fanqie": SYNC_CONTENT_SERVICE_UNITS["fanqie"],
}
SYNC_REAL_COVER_UNITS: tuple[str, ...] = (
    library_unit_name("oohstory-library-cover-sync.service"),
    library_unit_name("oohstory-library-local-source-upgrade.service"),
)
SYNC_AI_COVER_UNITS: tuple[str, ...] = tuple(
    library_unit_name(f"oohstory-clean-cover-worker@{index}.service")
    for index in range(1, COVER_MAX_WORKERS + 1)
)
SYNC_LIBRARY_SOURCE_ASSET_UNITS: Dict[str, tuple[str, ...]] = {
    "local": SYNC_REAL_COVER_UNITS,
    # “番茄书库”是逻辑归属，不是单一来源。授权站作品必须回各自
    # 的详情页同步封面；只有 source_id=fanqie-* 才走番茄官方下载器。
    # 各来源仍先严格抓取真实封面；只有安全终态失败才进入共享的
    # AI 文生图兜底队列，不能把逻辑书库当成真实来源。
    "fanqie": (
        library_unit_name("oohstory-library-xbiquge-cover-sync.service"),
        library_unit_name("oohstory-library-ixdzs-cover-sync.service"),
        library_unit_name("oohstory-library-shubaow-cover-sync.service"),
        library_unit_name("oohstory-library-fanqie-cover-sync.service"),
    ),
}
SYNC_LIBRARY_ASSET_UNITS: Dict[str, tuple[str, ...]] = dict(
    SYNC_LIBRARY_SOURCE_ASSET_UNITS
)
SYNC_CONTROL_LABELS = {
    "local": "同步本地书库",
    "fanqie": "同步番茄书库",
}
SITE_FULL_SYNC_TARGET_BOOKS_PER_MINUTE = SITE_BOOKS_PER_CYCLE_DEFAULT
SITE_FULL_SYNC_CONFIG: Dict[str, Dict[str, str]] = {
    "txt80": {
        "label": "TXT80 本地书库正文",
        "group": "local",
        "unit": library_unit_name(
            "oohstory-library-site-full-sync@txt80.service"
        ),
    },
    "xbiquge": {
        "label": "新笔趣阁授权正文",
        "group": "fanqie",
        "unit": library_unit_name(
            "oohstory-library-site-full-sync@xbiquge.service"
        ),
    },
    "ixdzs": {
        "label": "爱下授权正文",
        "group": "fanqie",
        "unit": library_unit_name(
            "oohstory-library-site-full-sync@ixdzs.service"
        ),
    },
    "shubaow": {
        "label": "书宝授权正文",
        "group": "fanqie",
        "unit": library_unit_name(
            "oohstory-library-site-full-sync@shubaow.service"
        ),
    },
    "linovelib": {
        "label": "哔哩轻小说授权正文",
        "group": "fanqie",
        "unit": library_unit_name(
            "oohstory-library-site-full-sync@linovelib.service"
        ),
    },
}

GENRE_KEYWORDS: Dict[str, tuple[str, ...]] = {
    "科幻": ("科幻", "星际", "星舰", "宇宙", "机甲", "末日", "末世", "废土", "赛博", "基因", "人工智能", "机器人"),
    "玄幻": ("玄幻", "异界", "魔法", "斗气", "血脉", "神魔", "诸天", "万族"),
    "仙侠": ("仙侠", "修仙", "修真", "飞升", "宗门", "灵气", "元婴", "渡劫", "剑修"),
    "武侠": ("武侠", "江湖", "武林", "侠客", "门派", "内功", "刀法", "剑法"),
    "都市": ("都市", "职场", "校园", "娱乐圈", "商战", "豪门", "神医", "兵王"),
    "历史": ("历史", "朝堂", "皇帝", "王朝", "争霸", "穿越古代", "科举", "权谋"),
    "游戏": ("游戏", "玩家", "副本", "网游", "电竞", "升级", "系统面板", "职业"),
    "悬疑": ("悬疑", "推理", "破案", "凶手", "诡异", "规则怪谈", "惊悚", "刑侦"),
    "言情": ("言情", "爱情", "婚恋", "甜宠", "追妻", "重生复仇", "先婚后爱", "暗恋"),
    "耽美": ("耽美", "纯爱", "双男主", "男男", "bl"),
    "同人": ("同人", "衍生", "原著角色", "穿越原著"),
    "现实": ("现实", "世情", "家庭", "社会", "年代", "生活", "职场"),
}

# 兼容项目画像和匹配逻辑使用的平面关键词视图。真正的分级评分规则
# 统一维护在 tone_catalog.py。
TONE_KEYWORDS: Dict[str, tuple[str, ...]] = {
    tone: tuple(
        keyword
        for level in ("strong", "medium", "weak")
        for keyword in rule.get(level, ())
    )
    for tone, rule in TONE_RULES.items()
}
GENRE_KEYWORD_PATTERN = re.compile(
    "|".join(
        re.escape(keyword)
        for keyword in sorted(
            {keyword for terms in GENRE_KEYWORDS.values() for keyword in terms},
            key=len,
            reverse=True,
        )
    )
)
TONE_KEYWORD_PATTERN = re.compile(
    "|".join(
        re.escape(keyword)
        for keyword in sorted(
            {keyword for terms in TONE_KEYWORDS.values() for keyword in terms},
            key=len,
            reverse=True,
        )
    )
)

CATEGORY_GENRES: Dict[str, tuple[str, ...]] = {
    "武侠仙侠": ("仙侠", "武侠"),
    "玄幻奇幻": ("玄幻",),
    "科幻小说": ("科幻",),
    "科幻灵异": ("科幻", "悬疑"),
    "都市小说": ("都市",),
    "历史军事": ("历史",),
    "军事历史": ("历史",),
    "游戏竞技": ("游戏",),
    "网游竞技": ("游戏",),
    "侦探推理": ("悬疑",),
    "恐怖灵异": ("悬疑",),
    "言情小说": ("言情",),
    "女生言情": ("言情",),
    "耽美同人": ("耽美", "同人"),
    "轻小说": ("轻小说", "日译中", "二次元"),
    "文学名著": ("现实",),
}

PROFILE_FILES = (
    "设定集/世界观/世界观.md",
    "设定集/世界观.md",
    "设定集/力量体系.md",
    "设定集/主角卡.md",
    "设定集/金手指设计.md",
    "大纲/总纲.md",
)

GENERIC_PROFILE_TERMS = {
    "世界", "生活", "日常", "现实", "历史", "成长", "势力", "命运", "秘密",
    "环境", "众人", "同伴", "团队", "爱情", "家庭", "社会", "职场", "战争",
}

_index_lock = threading.Lock()
_index_task: Optional[asyncio.Task] = None
_plot_index_task: Optional[asyncio.Task] = None
_combined_index_task: Optional[asyncio.Task] = None
_tone_review_task: Optional[asyncio.Task] = None
_import_lock = threading.Lock()
_tone_review_write_lock = threading.Lock()
_derived_refresh_queue_lock = threading.Lock()
_ingestion_refresh_queue_lock = threading.Lock()
_task_reconcile_lock = threading.Lock()


def _pid_is_alive(pid: Any) -> bool:
    try:
        value = int(pid)
        if value <= 0:
            return False
        os.kill(value, 0)
        proc_stat = Path(f"/proc/{value}/stat")
        if proc_stat.is_file():
            fields = proc_stat.read_text(
                encoding="utf-8", errors="replace"
            ).split()
            if len(fields) >= 3 and fields[2] == "Z":
                return False
        return True
    except (OSError, UnicodeError, TypeError, ValueError):
        return False


def _openclaw_session_exists(
    session_id: Any,
    agent_id: Any = "main",
) -> bool:
    value = str(session_id or "").strip()
    selected_agent = str(agent_id or "main").strip()
    if not value or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", selected_agent):
        return False
    state_root = Path(
        os.environ.get("OPENCLAW_STATE_DIR")
        or (Path.home() / ".openclaw")
    ).expanduser()
    registry = (
        state_root
        / "agents"
        / selected_agent
        / "sessions"
        / "sessions.json"
    )
    sessions = _read_json(registry, {})
    return isinstance(sessions, dict) and (
        f"agent:{selected_agent}:explicit:{value}" in sessions
    )


PLOT_MOTIFS: Dict[str, Dict[str, tuple[str, ...]]] = {
    "恨转爱": {
        "a": ("仇恨", "仇人", "死对头", "宿敌", "敌人", "复仇", "报仇", "恨他", "恨她"),
        "b": ("爱上", "相爱", "心动", "喜欢上", "恋人", "爱情", "动情", "成亲"),
    },
    "相爱相杀": {
        "a": ("相爱相杀", "针锋相对", "势不两立", "宿敌", "死对头"),
        "b": ("心动", "爱上", "喜欢上", "舍不得", "动情"),
    },
    "追妻火葬场": {
        "a": ("离婚", "退婚", "分手", "抛弃", "背叛", "误会"),
        "b": ("后悔", "追回", "复合", "求原谅", "追妻"),
    },
    "复仇反转": {
        "a": ("复仇", "报仇", "灭门", "背叛", "陷害"),
        "b": ("真相", "幕后", "身份", "反转", "误会"),
    },
    "救赎治愈": {
        "a": ("绝望", "崩溃", "创伤", "孤独", "囚禁"),
        "b": ("救赎", "治愈", "陪伴", "温暖", "守护"),
    },
    "身份揭露": {
        "a": ("隐藏身份", "真实身份", "伪装", "冒充", "卧底"),
        "b": ("揭穿", "暴露", "真相", "身份", "认出"),
    },
    "背叛和解": {
        "a": ("背叛", "出卖", "欺骗", "误会"),
        "b": ("和解", "原谅", "赎罪", "道歉", "重归于好"),
    },
    "牺牲守护": {
        "a": ("牺牲", "赴死", "舍命", "献祭"),
        "b": ("守护", "救下", "为了他", "为了她", "众人"),
    },
    "弱者逆袭": {
        "a": ("废物", "弱小", "受辱", "被欺负", "看不起"),
        "b": ("逆袭", "崛起", "反杀", "打脸", "碾压"),
    },
}

PLOT_QUERY_EXPANSIONS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("仇恨", "仇人", "敌人", "宿敌", "死对头"), ("仇恨", "仇人", "死对头", "宿敌", "复仇", "报仇")),
    (("爱情", "爱上", "相爱", "恋人", "心动"), ("爱上", "相爱", "心动", "喜欢上", "恋人", "爱情", "动情")),
    (("背叛", "出卖"), ("背叛", "出卖", "欺骗", "陷害")),
    (("和解", "原谅"), ("和解", "原谅", "赎罪", "道歉", "重归于好")),
    (("复仇", "报仇"), ("复仇", "报仇", "灭门", "陷害", "幕后", "真相")),
    (("救赎", "治愈"), ("救赎", "治愈", "陪伴", "温暖", "守护")),
    (("身份", "卧底", "伪装"), ("隐藏身份", "真实身份", "卧底", "伪装", "揭穿", "暴露")),
    (("逆袭", "打脸"), ("逆袭", "崛起", "反杀", "打脸", "碾压")),
)

PLOT_AI_EXPANSION_MAX_TERMS = 6
PLOT_AI_EXPANSION_MAX_TOKENS = 120
PLOT_AI_SUMMARY_MAX_BOOKS = 4
PLOT_AI_EVIDENCE_MAX_CHARS = 1200
PLOT_AI_SUMMARY_MAX_TOKENS = 360
PLOT_RULE_VERSION = "2026-07-28.1"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _read_json_value(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return default


def _safe_name(value: str, fallback: str = "未命名作品") -> str:
    text = re.sub(r'[\\/:*?"<>|]+', "_", (value or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" ._")
    return (text or fallback)[:100]


def _normalize_book_identity(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


class ElectronicLibraryService:
    def __init__(
        self,
        library_root: Optional[Path] = None,
        runtime_dir: Optional[Path] = None,
    ):
        configured_root = str(
            library_env("WEBNOVEL_LIBRARY_ROOT", "") or ""
        ).strip()
        self.library_root = (
            Path(configured_root).expanduser().resolve()
            if configured_root
            else (library_root or DEFAULT_LIBRARY_ROOT).resolve()
        )
        self.catalog_path = self.library_root / "catalog.sqlite3"
        self.books_root = self.library_root / "书籍"
        configured_deconstruction_root = os.getenv(
            "WEBNOVEL_GLOBAL_DECONSTRUCTION_ROOT", ""
        ).strip()
        canonical_deconstruction_root = (
            self.library_root / GLOBAL_DECONSTRUCTION_DIRNAME
        ).resolve()
        if configured_deconstruction_root:
            configured_deconstruction_path = Path(
                configured_deconstruction_root
            ).expanduser().resolve()
            if configured_deconstruction_path != canonical_deconstruction_root:
                raise ValueError(
                    "WEBNOVEL_GLOBAL_DECONSTRUCTION_ROOT 必须固定为 "
                    f"{canonical_deconstruction_root}"
                )
        # This is deliberately not configurable to another physical store.
        # A second root previously split tasks and artifacts across two trees;
        # fail closed instead of silently recreating that failure mode.
        self.global_deconstruction_root = canonical_deconstruction_root
        self.global_task_root = self.global_deconstruction_root / ".tasks"
        self.global_batch_root = self.global_deconstruction_root / ".batches"
        self.project_deconstruction_links_path = (
            self.global_deconstruction_root
            / PROJECT_DECONSTRUCTION_LINKS_FILENAME
        )
        self.project_deconstruction_links_lock_path = (
            self.global_deconstruction_root
            / f"{PROJECT_DECONSTRUCTION_LINKS_FILENAME}.lock"
        )
        configured_runtime_dir = str(
            library_env("WEBNOVEL_LIBRARY_RUNTIME_DIR", "") or ""
        ).strip()
        self.runtime_dir = (
            Path(configured_runtime_dir).expanduser().resolve()
            if configured_runtime_dir
            else (runtime_dir or DEFAULT_RUNTIME_DIR).resolve()
        )
        self.runtime_controls = OOHStoryRuntimeControls(self.runtime_dir)
        self.index_path = self.runtime_dir / "electronic_library_index.sqlite3"
        self.content_metrics_path = (
            self.runtime_dir / "library_content_metrics.sqlite3"
        )
        self.membership_path = self.runtime_dir / "library_memberships.sqlite3"
        self.index_status_path = self.runtime_dir / "electronic_library_index_status.json"
        self.tone_review_status_path = (
            self.runtime_dir / "electronic_library_tone_review_status.json"
        )
        self.plot_index_status_path = self.runtime_dir / "electronic_library_plot_index_status.json"
        self.derived_index_request_path = (
            self.runtime_dir / "electronic_library_derived_index_request.json"
        )
        self.derived_index_refresh_status_path = (
            self.runtime_dir / "electronic_library_derived_index_refresh_status.json"
        )
        self.derived_index_queue_lock_path = (
            self.runtime_dir / ".electronic_library_derived_index_queue.lock"
        )
        self.ingestion_index_request_path = (
            self.runtime_dir / "electronic_library_ingestion_index_request.json"
        )
        self.ingestion_index_refresh_status_path = (
            self.runtime_dir / "electronic_library_ingestion_index_status.json"
        )
        self.ingestion_index_queue_lock_path = (
            self.runtime_dir / ".electronic_library_ingestion_index_queue.lock"
        )
        self.sync_status_path = self.runtime_dir / "library-sync-status.json"
        self.authorized_catalog_sync_status_path = (
            self.runtime_dir / "authorized-site-catalog-sync.json"
        )
        self.reader_index_root = self.runtime_dir / "阅读目录"
        self.cover_root = self.library_root / "封面"
        configured_cover_index = os.getenv(
            "WEBNOVEL_COVER_INDEX_PATH",
            "",
        ).strip()
        self.cover_index_path = (
            Path(configured_cover_index).expanduser().resolve()
            if configured_cover_index
            else self.runtime_dir / "cover_index.sqlite3"
        )
        self.txt80_provider = AuthorizedTxt80Provider()
        self.xbiquge_provider = AuthorizedXbiqugeProvider()
        self.ixdzs_provider = AuthorizedIxdzsProvider()
        self.shubaow_provider = AuthorizedShubaowProvider()
        self.linovelib_provider = LinovelibProvider()
        self.zlibrary_provider = AuthorizedZLibraryProvider()
        self.fanqie_downloader = FanqieDownloaderBridge(self.library_root)
        self.download_scanner = DownloadSecurityScanner(
            self.runtime_dir / ".download-security-staging"
        )
        self.infrastructure_settings = (
            LibraryInfrastructureSettings.from_env()
        )
        self.mysql_pool: Optional[MySQLConnectionPool] = None
        self.mysql_catalog: Optional[MySQLCatalogStore] = None
        self.redis_queue: Optional[RedisQueueClient] = None
        self.hot_cache = RedisHotCache(
            LibraryCacheSettings.from_infrastructure(
                self.infrastructure_settings
            )
        )
        if self.infrastructure_settings.catalog_backend in {"shadow", "mysql"}:
            self.mysql_pool = MySQLConnectionPool(
                self.infrastructure_settings
            )
            self.redis_queue = RedisQueueClient(
                self.infrastructure_settings
            )
            self.mysql_catalog = MySQLCatalogStore(
                self.infrastructure_settings,
                self.mysql_pool,
                self.hot_cache,
            )
        self.object_store = NasObjectStore(
            self.infrastructure_settings.object_root
        )
        self._authorized_catalog_identity_keys: Optional[
            set[tuple[str, str]]
        ] = None
        self._deconstruction_status_cache: Dict[str, Dict[str, Any]] = {}
        self._deconstruction_cache_refreshing: set[str] = set()
        self._deconstruction_cache_lock = threading.Lock()

    def _require_legacy_sqlite(self, operation: str) -> None:
        if self.infrastructure_settings.catalog_backend == "mysql":
            raise RuntimeError(
                f"MySQL 模式禁止访问 SQLite：{operation}"
            )

    def infrastructure_status(self) -> Dict[str, Any]:
        """Report each final-architecture dependency without changing state."""
        settings = self.infrastructure_settings
        mysql: Dict[str, Any]
        redis_status: Dict[str, Any]
        try:
            pool = self.mysql_pool or MySQLConnectionPool(settings)
            mysql = pool.health()
            with pool.connection(readonly=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT
                            (
                                SELECT COALESCE(SUM(book_count), 0)
                                FROM catalog_status_counts
                            ) AS books,
                            (
                                SELECT COALESCE(SUM(book_count), 0)
                                FROM catalog_status_counts
                                WHERE status<>'duplicate'
                            ) AS active_books,
                            (
                                SELECT COALESCE(SUM(book_count), 0)
                                FROM catalog_facets
                                WHERE body_available=1
                            ) AS readable_books
                        """
                    )
                    catalog_row = dict(cursor.fetchone())
                    mysql["catalog"] = {
                        key: int(value or 0)
                        for key, value in catalog_row.items()
                    }
                    cursor.execute(
                        """
                        SELECT status, COUNT(*) AS count
                        FROM download_jobs
                        GROUP BY status
                        """
                    )
                    mysql["download_jobs"] = {
                        str(row["status"]): int(row["count"])
                        for row in cursor.fetchall()
                    }
        except Exception as exc:
            mysql = {
                "ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            }
        try:
            redis_queue = RedisQueueClient(settings)
            redis_status = redis_queue.health()
            stream = redis_queue.key(redis_queue.DOWNLOAD_STREAM)
            redis_status["download_stream_length"] = int(
                redis_queue.client.xlen(stream)
            )
        except Exception as exc:
            redis_status = {
                "ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            }
        try:
            object_status = self.object_store.health()
        except Exception as exc:
            object_status = {
                "ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            }
        return {
            "catalog_backend": settings.catalog_backend,
            "mysql": mysql,
            "redis": redis_status,
            "cache": self.hot_cache.stats(),
            "object_store": object_status,
            "ready_for_mysql_reads": bool(
                mysql.get("ok") and object_status.get("ok")
            ),
        }

    def _materialize_mysql_catalog_row(
        self,
        row: Dict[str, Any],
    ) -> Dict[str, Any]:
        item = dict(row)
        object_key = str(item.get("body_object_key") or "").strip()
        source_path = str(item.get("source_path") or "").strip()
        legacy_path = Path(source_path).expanduser() if source_path else None
        if object_key and not (legacy_path and legacy_path.is_file()):
            try:
                object_path = self.object_store.resolve(object_key)
                if object_path.is_file():
                    source_path = str(object_path)
            except (OSError, ValueError):
                pass
        item["source_path"] = source_path
        item["output_path"] = source_path
        item["bytes"] = int(item.get("source_bytes") or 0)
        return item

    @staticmethod
    def normalize_book_status(
        value: Any,
        *,
        default: str = SERIALIZATION_STATUS_COMPLETED,
    ) -> str:
        """Collapse every source-specific label to the two product statuses."""
        normalized = re.sub(r"\s+", "", str(value or "")).casefold()
        if normalized:
            if any(
                marker in normalized
                for marker in ("已完结", "完结", "完本", "全本", "completed", "finished")
            ):
                return SERIALIZATION_STATUS_COMPLETED
            if any(
                marker in normalized
                for marker in ("连载中", "连载", "更新中", "ongoing", "serializing")
            ):
                return SERIALIZATION_STATUS_ONGOING
        return (
            default
            if default in {
                SERIALIZATION_STATUS_ONGOING,
                SERIALIZATION_STATUS_COMPLETED,
            }
            else SERIALIZATION_STATUS_COMPLETED
        )

    def _cover_for_catalog_item(self, item: Dict[str, Any]) -> str:
        """Return a real cover or the one shared missing-cover asset."""
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            object_key = str(item.get("cover_object_key") or "").strip()
            if not object_key:
                return "/api/admin/library/default-cover?v=d421cee15a266d25"
            try:
                path = self.object_store.resolve(object_key)
                if (
                    not path.is_file()
                    and Path(object_key).name == object_key
                    and object_key not in {".", ".."}
                ):
                    path = self.object_store.resolve(
                        f"封面/{object_key}"
                    )
                if not path.is_file():
                    return ""
                version = str(
                    item.get("row_version") or path.stat().st_mtime_ns
                )[:20]
            except (OSError, ValueError):
                return "/api/admin/library/default-cover?v=d421cee15a266d25"
            return (
                f"/api/library/covers/{int(item['catalog_id'])}"
                f"?v={version}"
            )
        if not self.cover_index_path.exists():
            return ""
        try:
            with sqlite3.connect(self.cover_index_path, timeout=5) as conn:
                row = conn.execute(
                    """
                    SELECT filename,sha256 FROM covers
                    WHERE catalog_id=? AND source_id=? AND title=? AND author=?
                      AND status='done'
                    """,
                    (
                        int(item["catalog_id"]), str(item.get("source_id") or ""),
                        str(item.get("title") or ""), str(item.get("author") or ""),
                    ),
                ).fetchone()
        except sqlite3.Error:
            return ""
        if not row or not (self.cover_root / str(row[0])).is_file():
            return ""
        version = str(row[1] or "")[:16]
        return (
            f"/api/library/covers/{int(item['catalog_id'])}"
            f"?v={version}"
        )

    def get_cover_path(self, catalog_id: int) -> Path:
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            row = self.mysql_catalog.get_cover_asset(int(catalog_id))
            object_key = str(
                (row or {}).get("object_key")
                or (row or {}).get("cover_object_key")
                or ""
            ).strip()
            if not object_key:
                raise KeyError("封面不存在")
            try:
                path = self.object_store.resolve(object_key)
                if (
                    not path.is_file()
                    and Path(object_key).name == object_key
                    and object_key not in {".", ".."}
                ):
                    path = self.object_store.resolve(
                        f"封面/{object_key}"
                    )
            except ValueError as exc:
                raise KeyError("封面对象键无效") from exc
            if not path.is_file():
                raise KeyError("封面文件不存在")
            return path
        with sqlite3.connect(self.cover_index_path, timeout=15) as conn:
            row = conn.execute(
                "SELECT filename FROM covers WHERE catalog_id=? AND status='done'",
                (int(catalog_id),)
            ).fetchone()
        if not row:
            raise KeyError("封面不存在")
        path = (self.cover_root / str(row[0])).resolve()
        if not _is_within(path, self.cover_root) or not path.is_file():
            raise KeyError("封面文件不存在")
        return path

    async def _sync_fanqie_catalog_cover(
        self,
        *,
        catalog_id: int,
        book_id: str,
        title: str,
        author: str,
        catalog_source_id: str = "",
        force: bool = False,
        allow_title_alias: bool = False,
    ) -> Dict[str, Any]:
        result = await asyncio.to_thread(
            sync_fanqie_cover,
            catalog_id=int(catalog_id),
            book_id=str(book_id),
            title=str(title),
            author=str(author),
            catalog_source_id=str(catalog_source_id),
            cover_root=self.cover_root,
            cover_index_path=self.cover_index_path,
            force=bool(force),
            allow_title_alias=bool(allow_title_alias),
        )
        catalog_item = {
            "catalog_id": int(catalog_id),
            "source_id": f"fanqie-{book_id}",
            "title": str(title),
            "author": str(author),
        }
        return {
            **result,
            "local_url": self._cover_for_catalog_item(catalog_item),
        }

    def _queue_local_cover_redraw(self, item: Dict[str, Any]) -> Dict[str, Any]:
        catalog_id = int(item["catalog_id"])
        cover_path = self.get_cover_path(catalog_id)
        now = _now()
        if self.infrastructure_settings.catalog_backend == "mysql":
            source_id = str(item.get("source_id") or catalog_id)
            if (
                str(item.get("library_id") or "") != "local"
                or not source_id.isdigit()
            ):
                raise ValueError(
                    "AI 重绘不能人工绕过真实书源；请先同步原站封面，"
                    "仅确定无资源或 404 后才会自动转 AI"
                )
            MySQLLibraryRuntime(
                self.infrastructure_settings,
                self.mysql_pool,
                self.redis_queue,
            ).enqueue_local_source_lookup(
                catalog_id=catalog_id,
                source_id=source_id,
                title=str(item.get("title") or ""),
                author=str(item.get("author") or ""),
                original_filename=cover_path.name,
            )
        else:
            with sqlite3.connect(self.cover_index_path, timeout=30) as conn:
                conn.execute(
                """
                CREATE TABLE IF NOT EXISTS clean_cover_jobs (
                  catalog_id INTEGER PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  title TEXT NOT NULL,
                  author TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'pending',
                  original_filename TEXT,
                  replacement_url TEXT,
                  replacement_filename TEXT,
                  verification_source TEXT,
                  last_error TEXT,
                  updated_at TEXT NOT NULL,
                  attempts INTEGER NOT NULL DEFAULT 0,
                  ai_session_id TEXT,
                  source_width INTEGER,
                  source_height INTEGER,
                  generated_width INTEGER,
                  generated_height INTEGER
                )
                """
                )
                conn.execute(
                """
                INSERT INTO clean_cover_jobs (
                  catalog_id,source_id,title,author,status,original_filename,
                  attempts,last_error,updated_at
                ) VALUES (?,?,?,?, 'manual_pending', ?,0,NULL,?)
                ON CONFLICT(catalog_id) DO UPDATE SET
                  source_id=excluded.source_id,title=excluded.title,
                  author=excluded.author,status='manual_pending',
                  original_filename=excluded.original_filename,
                  replacement_url=NULL,replacement_filename=NULL,
                  verification_source=NULL,attempts=0,ai_session_id=NULL,
                  last_error=NULL,updated_at=excluded.updated_at
                """,
                (
                    catalog_id,
                    str(item.get("source_id") or catalog_id),
                    str(item.get("title") or ""),
                    str(item.get("author") or ""),
                    cover_path.name,
                    now,
                ),
                )
                conn.commit()
        subprocess.run(
            [
                "systemctl", "start",
                library_unit_name(
                    "oohstory-library-local-source-upgrade.service"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return {
            "catalog_id": catalog_id,
            "status": "queued",
            "action": "source_lookup",
            "message": "已先加入三站真实封面检索；仅确认无资源后转 AI",
        }

    async def sync_catalog_cover(
        self,
        catalog_id: int,
        *,
        action: str,
    ) -> Dict[str, Any]:
        item = self.get_book(int(catalog_id))
        normalized_action = str(action or "").strip().lower()
        if normalized_action == "redraw":
            return self._queue_local_cover_redraw(item)
        if normalized_action != "sync":
            raise ValueError("封面操作只支持 sync 或 redraw")
        source_id = str(item.get("source_id") or "")
        if source_id.startswith("fanqie-"):
            result = await self._sync_fanqie_catalog_cover(
                catalog_id=int(catalog_id),
                book_id=source_id.removeprefix("fanqie-"),
                title=str(item.get("title") or ""),
                author=str(item.get("author") or ""),
                catalog_source_id=source_id,
                force=True,
                allow_title_alias=True,
            )
        else:
            detail_url = str(item.get("detail_url") or "")
            detail_path = urlparse(detail_url).path
            if source_id.startswith("xbiquge-"):
                remote_id = source_id.removeprefix("xbiquge-")
                detail = await asyncio.to_thread(
                    self.xbiquge_provider.detail,
                    remote_id,
                    detail_path,
                    include_chapters=False,
                )
                result = await self._sync_remote_catalog_cover(
                    catalog_id=int(catalog_id),
                    source_id=source_id,
                    title=str(item.get("title") or ""),
                    author=str(item.get("author") or ""),
                    detail_url=str(detail.get("detail_url") or detail_url),
                    cover_url=str(detail.get("cover_url") or ""),
                    allowed_hosts={"www.xbiquge.info", "xbiquge.info"},
                    max_attempts=3,
                )
            elif source_id.startswith("ixdzs-"):
                remote_id = source_id.removeprefix("ixdzs-")
                detail = await asyncio.to_thread(
                    self.ixdzs_provider.detail,
                    remote_id,
                    detail_path,
                )
                cover_url = str(detail.get("cover_url") or "")
                cover_host = (urlparse(cover_url).hostname or "").lower()
                result = await self._sync_remote_catalog_cover(
                    catalog_id=int(catalog_id),
                    source_id=source_id,
                    title=str(item.get("title") or ""),
                    author=str(item.get("author") or ""),
                    detail_url=str(detail.get("detail_url") or detail_url),
                    cover_url=cover_url,
                    allowed_hosts={cover_host} - {""},
                    allowed_host_suffixes=(".ixdzs.com", ".ixdzs8.com"),
                    max_attempts=3,
                )
            elif source_id.startswith("shubaow-"):
                remote_id = source_id.removeprefix("shubaow-")
                detail = await asyncio.to_thread(
                    self.shubaow_provider.detail,
                    remote_id,
                    detail_path,
                    include_chapters=False,
                )
                result = await self._sync_remote_catalog_cover(
                    catalog_id=int(catalog_id),
                    source_id=source_id,
                    title=str(item.get("title") or ""),
                    author=str(item.get("author") or ""),
                    detail_url=str(detail.get("detail_url") or detail_url),
                    cover_url=str(detail.get("cover_url") or ""),
                    allowed_hosts={"www.shubaow.org", "shubaow.org", "pic.shubaow.org"},
                    request_bytes=self.shubaow_provider.download_cover,
                )
            elif source_id.startswith("linovelib-"):
                remote_id = source_id.removeprefix("linovelib-")
                detail = await asyncio.to_thread(
                    self.linovelib_provider.detail,
                    remote_id,
                    detail_path,
                    include_chapters=False,
                )
                result = await self._sync_remote_catalog_cover(
                    catalog_id=int(catalog_id),
                    source_id=source_id,
                    title=str(item.get("title") or ""),
                    author=str(item.get("author") or ""),
                    detail_url=str(detail.get("detail_url") or detail_url),
                    cover_url=str(detail.get("cover_url") or ""),
                    allowed_hosts={
                        "www.linovelib.com",
                        "linovelib.com",
                        "www.bilinovel.com",
                        "bilinovel.com",
                    },
                    request_bytes=self.linovelib_provider.download_cover,
                    max_attempts=3,
                )
            elif (
                source_id.isdigit()
                and (urlparse(detail_url).hostname or "").lower()
                in {"www.txt80.cc", "txt80.cc"}
            ):
                detail = await asyncio.to_thread(
                    self.txt80_provider.detail,
                    source_id,
                    detail_path,
                )
                result = await self._sync_remote_catalog_cover(
                    catalog_id=int(catalog_id),
                    source_id=source_id,
                    title=str(item.get("title") or ""),
                    author=str(item.get("author") or ""),
                    detail_url=str(detail.get("detail_url") or detail_url),
                    cover_url=str(detail.get("cover_url") or ""),
                    allowed_hosts={"img.txt80.cc", "www.txt80.cc", "txt80.cc"},
                    max_attempts=3,
                )
            elif self._cover_for_catalog_item(item):
                result = {
                    "status": "already_available",
                    "local_url": self._cover_for_catalog_item(item),
                }
            else:
                raise ValueError("当前作品来源没有可用的封面同步适配器")
            if result.get("status") in {"failed", "unavailable"}:
                raise ValueError(str(result.get("error") or "封面同步失败"))
        return {
            **result,
            "catalog_id": int(catalog_id),
            "action": "sync",
        }

    async def batch_catalog_covers(
        self,
        catalog_ids: Iterable[int],
        *,
        action: str,
    ) -> Dict[str, Any]:
        ids = sorted({int(value) for value in catalog_ids if int(value) > 0})
        if not ids:
            raise ValueError("请至少选择一本小说")
        if len(ids) > 200:
            raise ValueError("单次最多处理 200 本封面")
        items: List[Dict[str, Any]] = []
        for catalog_id in ids:
            try:
                result = await self.sync_catalog_cover(
                    catalog_id, action=action
                )
                items.append({"catalog_id": catalog_id, "ok": True, **result})
            except Exception as exc:
                items.append(
                    {
                        "catalog_id": catalog_id,
                        "ok": False,
                        "error": str(exc)[:300],
                    }
                )
        succeeded = sum(1 for item in items if item["ok"])
        return {
            "action": action,
            "requested": len(ids),
            "succeeded": succeeded,
            "failed": len(ids) - succeeded,
            "items": items,
            "message": f"封面任务已提交：成功 {succeeded} 本，失败 {len(ids) - succeeded} 本",
        }

    def upload_catalog_cover(
        self,
        catalog_id: int,
        *,
        data: bytes,
        filename: str,
        content_type: str,
    ) -> Dict[str, Any]:
        item = self.get_book(int(catalog_id))
        if len(data) < 1024 or len(data) > 12 * 1024 * 1024:
            raise ValueError("上传封面大小必须在 1KB 至 12MB 之间")
        signatures = (
            (b"\xff\xd8\xff", ".jpg"),
            (b"\x89PNG\r\n\x1a\n", ".png"),
            (b"GIF87a", ".gif"),
            (b"GIF89a", ".gif"),
        )
        extension = next(
            (suffix for signature, suffix in signatures if data.startswith(signature)),
            "",
        )
        if not extension and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            extension = ".webp"
        if not extension:
            raise ValueError("上传封面仅支持 JPG、PNG、GIF 或 WebP")
        scan = self.download_scanner.scan_bytes(
            data,
            extension=extension,
            source="manual_cover_upload",
        )
        digest = hashlib.sha256(data).hexdigest()
        source_id = str(item.get("source_id") or catalog_id)
        safe_source = re.sub(r"[^A-Za-z0-9_-]+", "-", source_id).strip("-")
        stored_name = (
            f"{int(catalog_id)}-{safe_source[:80]}-manual-{digest[:16]}"
            f"{extension}"
        )
        self.cover_root.mkdir(parents=True, exist_ok=True)
        target = (self.cover_root / stored_name).resolve()
        if not _is_within(target, self.cover_root):
            raise ValueError("上传封面文件名无效")
        temporary = target.with_suffix(target.suffix + ".part")
        temporary.write_bytes(data)
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            try:
                stored = self.object_store.put_file(
                    temporary,
                    asset_type="cover",
                    extension=extension,
                )
            finally:
                temporary.unlink(missing_ok=True)
            self.mysql_catalog.upsert_cover_asset(
                catalog_id=int(catalog_id),
                object_key=stored.object_key,
                bytes_count=stored.bytes,
                sha256=stored.sha256,
                content_type=str(content_type or ""),
            )
            return {
                "catalog_id": int(catalog_id),
                "status": "uploaded",
                "filename": stored.object_key,
                "original_filename": Path(filename or "cover").name,
                "content_type": str(content_type or ""),
                "cover_url": (
                    f"/api/library/covers/{int(catalog_id)}"
                    f"?v={stored.sha256[:16]}"
                ),
                "security_scan": scan,
            }
        temporary.replace(target)
        with sqlite3.connect(self.cover_index_path, timeout=30) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS covers (
                  catalog_id INTEGER PRIMARY KEY, source_id TEXT NOT NULL,
                  title TEXT NOT NULL, author TEXT NOT NULL,
                  detail_url TEXT NOT NULL, cover_url TEXT, filename TEXT,
                  sha256 TEXT, status TEXT NOT NULL DEFAULT 'pending',
                  attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
                  updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                INSERT INTO covers (
                  catalog_id,source_id,title,author,detail_url,cover_url,
                  filename,sha256,status,attempts,last_error,updated_at
                ) VALUES (?,?,?,?,?,'',?,?,'done',1,NULL,?)
                ON CONFLICT(catalog_id) DO UPDATE SET
                  source_id=excluded.source_id,title=excluded.title,
                  author=excluded.author,detail_url=excluded.detail_url,
                  cover_url='',filename=excluded.filename,sha256=excluded.sha256,
                  status='done',attempts=covers.attempts+1,last_error=NULL,
                  updated_at=excluded.updated_at
                """,
                (
                    int(catalog_id),
                    source_id,
                    str(item.get("title") or ""),
                    str(item.get("author") or ""),
                    "manual-upload://local",
                    stored_name,
                    digest,
                    _now(),
                ),
            )
            conn.commit()
        return {
            "catalog_id": int(catalog_id),
            "status": "uploaded",
            "filename": stored_name,
            "original_filename": Path(filename or "cover").name,
            "content_type": str(content_type or ""),
            "cover_url": f"/api/library/covers/{int(catalog_id)}?v={digest[:16]}",
            "security_scan": scan,
        }

    async def _sync_remote_catalog_cover(
        self,
        *,
        catalog_id: int,
        source_id: str,
        title: str,
        author: str,
        detail_url: str,
        cover_url: str,
        allowed_hosts: Iterable[str],
        allowed_host_suffixes: Iterable[str] = (),
        request_bytes: Optional[Callable[[str], tuple[bytes, str]]] = None,
        max_attempts: int = 1,
    ) -> Dict[str, Any]:
        """Attempt the source cover in the same import job as the正文."""
        existing_url = self._cover_for_catalog_item(
            {
                "catalog_id": int(catalog_id),
                "source_id": str(source_id),
                "title": str(title),
                "author": str(author),
            }
        )
        if existing_url:
            return {
                "status": "already_available",
                "local_url": existing_url,
            }

        def queue_ai_fallback(error: str, attempts: int) -> Dict[str, Any]:
            message = str(error or "").strip()
            if not should_generate_ai_fallback(
                message,
                attempts=attempts,
                max_attempts=max_attempts,
            ):
                return {
                    "status": "failed",
                    "local_url": "",
                    "attempts": attempts,
                    "error": message,
                    "ai_fallback_queued": False,
                }
            if (
                self.infrastructure_settings.catalog_backend != "mysql"
                or self.mysql_pool is None
            ):
                return {
                    "status": "ai_fallback",
                    "local_url": "",
                    "attempts": attempts,
                    "error": message,
                    "ai_fallback_queued": False,
                }
            try:
                MySQLLibraryRuntime(
                    self.infrastructure_settings,
                    self.mysql_pool,
                    self.redis_queue,
                ).enqueue_generated_cover_fallback(
                    catalog_id=int(catalog_id),
                    source_id=str(source_id),
                    title=str(title),
                    author=str(author),
                    reason=message,
                )
            except Exception as exc:
                return {
                    "status": "failed",
                    "local_url": "",
                    "attempts": attempts,
                    "error": message,
                    "ai_fallback_queued": False,
                    "ai_fallback_error": (
                        f"{type(exc).__name__}: {str(exc)[:300]}"
                    ),
                }
            return {
                "status": "ai_fallback",
                "local_url": "",
                "attempts": attempts,
                "error": message,
                "ai_fallback_queued": True,
            }

        if not str(cover_url or "").strip():
            return queue_ai_fallback(
                "无可安全使用的源图：书源详情页没有返回封面",
                1,
            )
        attempts = min(max(int(max_attempts), 1), 3)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                result = await asyncio.to_thread(
                    sync_remote_cover,
                    catalog_id=int(catalog_id),
                    source_id=str(source_id),
                    title=str(title),
                    author=str(author),
                    detail_url=str(detail_url),
                    cover_url=str(cover_url),
                    cover_root=self.cover_root,
                    cover_index_path=self.cover_index_path,
                    allowed_hosts=tuple(allowed_hosts),
                    allowed_host_suffixes=tuple(allowed_host_suffixes),
                    request_bytes=request_bytes,
                )
                local_url = self._cover_for_catalog_item(
                    {
                        "catalog_id": int(catalog_id),
                        "source_id": str(source_id),
                        "title": str(title),
                        "author": str(author),
                    }
                )
                if not local_url and result.get("sha256"):
                    local_url = (
                        f"/api/library/covers/{int(catalog_id)}"
                        f"?v={str(result['sha256'])[:16]}"
                    )
                return {**result, "local_url": local_url}
            except Exception as exc:
                last_error = exc
                if attempt < attempts:
                    await asyncio.sleep(0.25 * attempt)
        assert last_error is not None
        return queue_ai_fallback(
            f"{type(last_error).__name__}: {str(last_error)[:300]}",
            attempts,
        )

    def _apply_source_branding_normalizer(
        self,
        catalog_id: int,
        profile: str,
    ) -> Dict[str, Any]:
        """Run the canonical, MySQL-aware source normalizer for one book."""

        profile = str(profile or "").strip().lower()
        if profile not in {"ixdzs", "shubaow"}:
            raise ValueError("当前来源没有正文清洗规则")
        tool = (
            APP_ROOT
            / "scripts"
            / "electronic-library"
            / "replace_ixdzs_branding.py"
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(tool),
                "--apply",
                "--update-mysql",
                "--catalog-id",
                str(int(catalog_id)),
                "--profile",
                profile,
            ],
            cwd=APP_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if completed.returncode:
            detail = (
                completed.stderr
                or completed.stdout
                or "正文来源清洗失败"
            ).strip()
            raise RuntimeError(
                f"replace_ixdzs_branding.py: {detail[-1200:]}"
            )
        try:
            return json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            return {
                "mode": "apply-update-mysql",
                "output": completed.stdout[-500:],
            }

    @staticmethod
    def _run_systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["systemctl", *args],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if check and result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            raise RuntimeError(message or f"systemctl {' '.join(args)} 执行失败")
        return result

    def _sync_units_status(
        self,
        units: Iterable[str],
    ) -> Dict[str, Dict[str, Any]]:
        """Read all requested systemd states with one D-Bus round trip."""
        ordered_units = tuple(dict.fromkeys(str(unit) for unit in units if unit))
        if not ordered_units:
            return {}
        result = self._run_systemctl(
            "show",
            *ordered_units,
            "--property=Id",
            "--property=UnitFileState",
            "--property=ActiveState",
            "--property=NextElapseUSecRealtime",
            "--property=LastTriggerUSec",
            check=False,
        )
        raw_by_unit: Dict[str, Dict[str, str]] = {}
        for block in re.split(r"\n\s*\n", result.stdout.strip()):
            fields: Dict[str, str] = {}
            for line in block.splitlines():
                key, separator, value = line.partition("=")
                if separator:
                    fields[key.strip()] = value.strip()
            unit_id = fields.get("Id", "")
            if unit_id:
                raw_by_unit[unit_id] = fields

        enabled_states = {
            "enabled",
            "enabled-runtime",
            "linked",
            "linked-runtime",
            "alias",
        }
        active_states = {"active", "activating", "reloading"}
        statuses: Dict[str, Dict[str, Any]] = {}
        for unit in ordered_units:
            fields = raw_by_unit.get(unit, {})
            active_state = fields.get("ActiveState", "inactive").lower()
            unit_file_state = fields.get("UnitFileState", "disabled").lower()
            next_run = fields.get("NextElapseUSecRealtime", "")
            last_run = fields.get("LastTriggerUSec", "")
            statuses[unit] = {
                "unit": unit,
                "enabled": unit_file_state in enabled_states,
                "active": active_state in active_states,
                "active_state": active_state,
                "next_run": "" if next_run.lower() == "n/a" else next_run,
                "last_run": "" if last_run.lower() == "n/a" else last_run,
            }
        return statuses

    def _sync_timer_status(self, unit: str) -> Dict[str, Any]:
        return self._sync_units_status((unit,))[unit]

    def sync_controls_status(self) -> Dict[str, Any]:
        runtime_controls = self.runtime_controls.read()
        cover_target_per_hour = validate_cover_target_per_hour(
            runtime_controls["cover_redraw"]["target_per_hour"]
        )
        desired_cover_workers = cover_worker_count(cover_target_per_hour)
        desired_cover_units = SYNC_AI_COVER_UNITS[:desired_cover_workers]
        all_units = [
            unit
            for units in SYNC_TIMER_UNITS.values()
            for unit in units
        ]
        all_units.extend(SYNC_ON_DEMAND_UNITS.values())
        all_units.extend(
            unit
            for units in SYNC_CONTENT_SERVICE_UNITS.values()
            for unit in units
        )
        all_units.extend(
            unit
            for units in SYNC_LIBRARY_ASSET_UNITS.values()
            for unit in units
        )
        all_units.extend(SYNC_AI_COVER_UNITS)
        all_units.extend(
            config["unit"] for config in SITE_FULL_SYNC_CONFIG.values()
        )
        statuses = self._sync_units_status(all_units)
        controls: Dict[str, Any] = {}
        for library_id, units in SYNC_TIMER_UNITS.items():
            timers = [statuses[unit] for unit in units]
            content_pipeline = statuses[SYNC_ON_DEMAND_UNITS[library_id]]
            content_services = [
                statuses[unit]
                for unit in SYNC_CONTENT_SERVICE_UNITS[library_id]
            ]
            asset_pipeline = [
                statuses[unit]
                for unit in SYNC_LIBRARY_ASSET_UNITS[library_id]
            ]
            next_runs = [
                timer["next_run"] for timer in timers if timer["next_run"]
            ]
            last_runs = [
                timer["last_run"] for timer in timers if timer["last_run"]
            ]
            controls[library_id] = {
                "id": library_id,
                "label": SYNC_CONTROL_LABELS[library_id],
                # ``enabled`` remains the compatibility alias for the content
                # switch.  Covers are controlled independently.
                "enabled": all(timer["enabled"] for timer in timers),
                "content_enabled": all(
                    timer["enabled"] for timer in timers
                ),
                "primary_sync_enabled": all(
                    timer["enabled"] for timer in timers
                ),
                "active": any(timer["active"] for timer in timers),
                "content_pipeline": content_pipeline,
                "content_services": content_services,
                "content_pipeline_active": any(
                    unit["active"] for unit in content_services
                ),
                "next_run": next_runs[0] if next_runs else "",
                "last_run": last_runs[0] if last_runs else "",
                "timers": timers,
                "asset_pipeline": asset_pipeline,
                "asset_pipeline_enabled": all(
                    unit["enabled"] for unit in asset_pipeline
                ),
                "asset_pipeline_active": all(
                    unit["active"] for unit in asset_pipeline
                ),
                "cover_enabled": all(
                    unit["enabled"] for unit in asset_pipeline
                ),
                "cover_active": all(
                    unit["active"] for unit in asset_pipeline
                ),
                "pipeline_description": (
                    "书目总量 + 新书下载 + 源站更新版本"
                    if library_id == "local"
                    else "番茄下载历史 + 新书导入 + 已跟踪作品更新"
                ),
                "cover_description": (
                    "TXT80/TXT020 水印封面先查三站；无资源/404 才转 AI"
                    if library_id == "local"
                    else (
                        "按真实书源同步封面（番茄/新笔趣阁/"
                        "爱下/书宝）；确定无图或 404 才按书名 AI 生成"
                    )
                ),
                "ai_cover_enabled": all(
                    statuses[unit]["enabled"]
                    for unit in desired_cover_units
                ),
            }
        cover_operational: Dict[str, Any] = {
            "completed_last_hour": 0,
            "completed_last_six_hours": 0,
            "processing": 0,
            "pending": 0,
            "failed": 0,
            "available": False,
        }
        if self.mysql_pool is not None:
            try:
                cover_operational.update(
                    MySQLLibraryRuntime(
                        self.infrastructure_settings,
                        self.mysql_pool,
                        self.redis_queue,
                    ).clean_cover_operational_status()
                )
                cover_operational["available"] = True
            except Exception as exc:
                cover_operational["error"] = str(exc)[:240]
        cover_units = [statuses[unit] for unit in SYNC_AI_COVER_UNITS]
        controls["cover_redraw"] = {
            "target_per_hour": cover_target_per_hour,
            "minimum_target_per_hour": 50,
            "configured_workers": desired_cover_workers,
            "active_workers": sum(
                1 for unit in desired_cover_units if statuses[unit]["active"]
            ),
            "enabled": all(
                statuses[unit]["enabled"] for unit in desired_cover_units
            ),
            "active": all(
                statuses[unit]["active"] for unit in desired_cover_units
            ),
            "units": cover_units,
            "actual": cover_operational,
            "updated_at": runtime_controls.get("updated_at", ""),
        }
        full_speed_sites: list[dict[str, Any]] = []
        for site_id, config in SITE_FULL_SYNC_CONFIG.items():
            unit_status = statuses[config["unit"]]
            state = _read_json(
                self.runtime_dir / f"oohstory-site-full-sync-{site_id}.json",
                {},
            )
            full_speed_sites.append(
                {
                    "id": site_id,
                    "label": config["label"],
                    "group": config["group"],
                    "unit": config["unit"],
                    "enabled": unit_status["enabled"],
                    "active": unit_status["active"],
                    "active_state": unit_status["active_state"],
                    "target_books_per_minute": (
                        runtime_controls["site_books_per_cycle"].get(
                            site_id,
                            SITE_FULL_SYNC_TARGET_BOOKS_PER_MINUTE,
                        )
                    ),
                    "books_per_cycle": runtime_controls[
                        "site_books_per_cycle"
                    ].get(site_id, SITE_FULL_SYNC_TARGET_BOOKS_PER_MINUTE),
                    "configurable": site_id in SITE_IDS,
                    "status": str(state.get("status") or "idle"),
                    "message": str(state.get("message") or "尚未启动"),
                    "cycle": int(state.get("cycle") or 0),
                    "last_cycle_seconds": state.get("last_cycle_seconds"),
                    "updated_at": str(state.get("updated_at") or ""),
                }
            )
        controls["site_full_sync"] = {
            "target_books_per_minute": SITE_FULL_SYNC_TARGET_BOOKS_PER_MINUTE,
            "rate_contract": (
                "授权站点每轮本数可独立配置，周期不少于 60 秒；"
                "上游较慢时以实际完成速度为准"
            ),
            "authorized_sites_share_slot": True,
            "sites": full_speed_sites,
        }
        return controls

    def set_site_full_sync(
        self,
        site_id: str,
        enabled: bool,
        books_per_cycle: int | None = None,
    ) -> Dict[str, Any]:
        config = SITE_FULL_SYNC_CONFIG.get(str(site_id or "").strip())
        if not config:
            raise ValueError("未知正文同步站点")
        if books_per_cycle is not None:
            self.runtime_controls.update_site(
                site_id,
                validate_site_books_per_cycle(books_per_cycle),
            )
        current = self.sync_controls_status()
        if enabled:
            group = str(config["group"])
            scheduled = current[group]
            if scheduled["content_enabled"] or scheduled["content_pipeline_active"]:
                raise ValueError(
                    "请先停用对应书库的定时正文同步，并等待当前任务结束"
                )
            self._run_systemctl("enable", "--now", config["unit"])
        else:
            self._run_systemctl("disable", "--now", config["unit"])
        return self.sync_controls_status()

    def set_cover_redraw_control(
        self,
        target_per_hour: int,
        enabled: bool | None = None,
    ) -> Dict[str, Any]:
        target = validate_cover_target_per_hour(target_per_hour)
        self.runtime_controls.update_cover_target(target)
        desired_workers = cover_worker_count(target)
        desired_units = SYNC_AI_COVER_UNITS[:desired_workers]
        excess_units = SYNC_AI_COVER_UNITS[desired_workers:]
        if enabled is True:
            if excess_units:
                self._run_systemctl("disable", "--now", *excess_units)
            self._run_systemctl("enable", "--now", *desired_units)
        elif enabled is False:
            self._run_systemctl("disable", "--now", *SYNC_AI_COVER_UNITS)
        else:
            current = self._sync_units_status(SYNC_AI_COVER_UNITS)
            if any(unit["enabled"] for unit in current.values()):
                if excess_units:
                    self._run_systemctl("disable", "--now", *excess_units)
                self._run_systemctl("enable", "--now", *desired_units)
        return self.sync_controls_status()

    def set_sync_control(
        self,
        library_id: str,
        enabled: bool,
        pipeline: str = "content",
    ) -> Dict[str, Any]:
        if library_id not in SYNC_TIMER_UNITS:
            raise ValueError("未知书库同步开关")
        if pipeline not in {"content", "covers"}:
            raise ValueError("未知书库流水线开关")
        units = SYNC_TIMER_UNITS[library_id]
        if pipeline == "covers":
            source_units = SYNC_LIBRARY_SOURCE_ASSET_UNITS[library_id]
            if enabled:
                self._run_systemctl("enable", "--now", *source_units)
            else:
                self._run_systemctl("disable", "--now", *source_units)
            return self.sync_controls_status()
        action = "enable" if enabled else "disable"
        self._run_systemctl(action, "--now", *units)
        if pipeline == "content" and enabled:
            # Starting a timer does not run its target service immediately.
            # The switch is also an explicit request to consume the existing
            # pending queue now, so trigger the serialized one-shot service.
            self._run_systemctl(
                "start",
                "--no-block",
                *SYNC_CONTENT_ON_ENABLE_UNITS[library_id],
            )
        elif pipeline == "content":
            # Disabling timers does not stop a service instance that is
            # already running.  Queue the stop asynchronously so the UI
            # switch returns immediately while systemd drains the worker
            # cgroup in the background.
            self._run_systemctl(
                "stop",
                "--no-block",
                *SYNC_CONTENT_SERVICE_UNITS[library_id],
            )
        return self.sync_controls_status()

    def _membership_connection(self) -> sqlite3.Connection:
        self._require_legacy_sqlite("library memberships")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.membership_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS catalog_library_overrides (
                catalog_id INTEGER PRIMARY KEY,
                target_library TEXT NOT NULL
                    CHECK(target_library IN ('local', 'fanqie')),
                moved_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_catalog_library_overrides_target
                ON catalog_library_overrides(target_library, catalog_id);
            """
        )
        return conn

    @staticmethod
    def _catalog_library_expression(table_alias: str = "books") -> str:
        prefix = f"{table_alias}." if table_alias else ""
        return (
            "COALESCE(("
            "SELECT target_library "
            "FROM membership.catalog_library_overrides AS membership_override "
            f"WHERE membership_override.catalog_id={prefix}id"
            "), CASE "
            f"WHEN LOWER(COALESCE({prefix}source_id, '')) LIKE 'fanqie-%' "
            "THEN 'fanqie' ELSE 'local' END)"
        )

    def _catalog_effective_library_expression(
        self,
        columns: set[str],
        table_alias: str = "books",
    ) -> str:
        """Prefer the million-row materialized library key when available."""
        prefix = f"{table_alias}." if table_alias else ""
        if "library_id" in columns:
            return f"{prefix}library_id"
        return self._catalog_library_expression(table_alias)

    @staticmethod
    def _catalog_readable_condition(
        columns: set[str],
        table_alias: str = "",
    ) -> str:
        prefix = f"{table_alias}." if table_alias else ""
        if "body_available" in columns:
            return f"{prefix}body_available = 1"
        return (
            f"{prefix}status = 'done' AND "
            f"NULLIF(TRIM(COALESCE({prefix}output_path, '')), '') IS NOT NULL"
        )

    def _catalog_connection(self) -> sqlite3.Connection:
        self._require_legacy_sqlite("catalog")
        if not self.catalog_path.exists():
            raise FileNotFoundError(f"电子书库目录数据库不存在：{self.catalog_path}")
        with self._membership_connection() as membership_conn:
            membership_conn.commit()
        uri = f"{self.catalog_path.as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=15)
        conn.row_factory = sqlite3.Row
        membership_uri = f"{self.membership_path.as_uri()}?mode=ro"
        conn.execute(
            "ATTACH DATABASE ? AS membership",
            (membership_uri,),
        )
        # The catalog is read through short-lived request connections.  mmap
        # pages are shared by the kernel across those connections, while WAL
        # snapshots keep discovery/download writers from blocking readers.
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA cache_size=-32768")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA mmap_size=536870912")
        conn.execute("PRAGMA query_only=ON")
        return conn

    @staticmethod
    def _index_list_columns() -> tuple[str, ...]:
        return (
            "source_id",
            "catalog_id",
            "title",
            "author",
            "category",
            "source_path",
            "source_bytes",
            "source_mtime_ns",
            "approx_word_count",
            "approx_chapter_count",
            "word_count",
            "chapter_count",
            "section_count",
            "reader_index_status",
            "reader_schema_version",
            "reader_indexed_at",
            "summary",
            "searchable_text",
            "genre_tags",
            "tone_tags",
            "indexed_at",
        )

    def _index_connection(self) -> sqlite3.Connection:
        self._require_legacy_sqlite("derived index")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.index_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS library_index (
                source_id TEXT PRIMARY KEY,
                catalog_id INTEGER NOT NULL,
                title TEXT,
                author TEXT,
                category TEXT,
                source_path TEXT NOT NULL,
                source_bytes INTEGER NOT NULL DEFAULT 0,
                source_mtime_ns INTEGER NOT NULL DEFAULT 0,
                approx_word_count INTEGER NOT NULL DEFAULT 0,
                approx_chapter_count INTEGER NOT NULL DEFAULT 0,
                word_count INTEGER NOT NULL DEFAULT 0,
                chapter_count INTEGER NOT NULL DEFAULT 0,
                section_count INTEGER NOT NULL DEFAULT 0,
                reader_index_status TEXT NOT NULL DEFAULT '',
                reader_schema_version INTEGER NOT NULL DEFAULT 0,
                reader_indexed_at TEXT,
                summary TEXT,
                searchable_text TEXT,
                genre_tags TEXT NOT NULL DEFAULT '[]',
                tone_tags TEXT NOT NULL DEFAULT '[]',
                primary_tone_tags TEXT NOT NULL DEFAULT '[]',
                secondary_tone_tags TEXT NOT NULL DEFAULT '[]',
                tone_confidence REAL NOT NULL DEFAULT 0,
                tone_source TEXT NOT NULL DEFAULT 'local',
                tone_evidence TEXT NOT NULL DEFAULT '{}',
                tone_review_status TEXT NOT NULL DEFAULT 'pending',
                tone_review_model TEXT NOT NULL DEFAULT '',
                tone_reviewed_at TEXT,
                keyword_counts TEXT NOT NULL DEFAULT '{}',
                indexed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_library_index_category
                ON library_index(category);
            CREATE INDEX IF NOT EXISTS idx_library_index_catalog_id
                ON library_index(catalog_id);
            CREATE TABLE IF NOT EXISTS index_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS plot_index_meta (
                source_id TEXT PRIMARY KEY,
                catalog_id INTEGER NOT NULL,
                source_bytes INTEGER NOT NULL DEFAULT 0,
                source_mtime_ns INTEGER NOT NULL DEFAULT 0,
                segment_count INTEGER NOT NULL DEFAULT 0,
                indexed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS plot_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                catalog_id INTEGER NOT NULL,
                title TEXT,
                author TEXT,
                category TEXT,
                location TEXT,
                motif_tags TEXT NOT NULL DEFAULT '[]',
                content TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_plot_segments_source
                ON plot_segments(source_id);
            CREATE INDEX IF NOT EXISTS idx_plot_segments_catalog
                ON plot_segments(catalog_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS plot_segments_fts USING fts5(
                title,
                author,
                category,
                motif_tags,
                content,
                content='plot_segments',
                content_rowid='id',
                tokenize='trigram'
            );
            CREATE TRIGGER IF NOT EXISTS plot_segments_ai AFTER INSERT ON plot_segments BEGIN
                INSERT INTO plot_segments_fts(
                    rowid, title, author, category, motif_tags, content
                ) VALUES (
                    new.id, new.title, new.author, new.category,
                    new.motif_tags, new.content
                );
            END;
            CREATE TRIGGER IF NOT EXISTS plot_segments_ad AFTER DELETE ON plot_segments BEGIN
                INSERT INTO plot_segments_fts(
                    plot_segments_fts, rowid, title, author, category,
                    motif_tags, content
                ) VALUES (
                    'delete', old.id, old.title, old.author, old.category,
                    old.motif_tags, old.content
                );
            END;
            """
        )
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(library_index)")
        }
        migrations = {
            "primary_tone_tags": "TEXT NOT NULL DEFAULT '[]'",
            "secondary_tone_tags": "TEXT NOT NULL DEFAULT '[]'",
            "tone_confidence": "REAL NOT NULL DEFAULT 0",
            "tone_source": "TEXT NOT NULL DEFAULT 'local'",
            "tone_evidence": "TEXT NOT NULL DEFAULT '{}'",
            "tone_review_status": "TEXT NOT NULL DEFAULT 'pending'",
            "tone_review_model": "TEXT NOT NULL DEFAULT ''",
            "tone_reviewed_at": "TEXT",
            "word_count": "INTEGER NOT NULL DEFAULT 0",
            "chapter_count": "INTEGER NOT NULL DEFAULT 0",
            "section_count": "INTEGER NOT NULL DEFAULT 0",
            "reader_index_status": "TEXT NOT NULL DEFAULT ''",
            "reader_schema_version": "INTEGER NOT NULL DEFAULT 0",
            "reader_indexed_at": "TEXT",
        }
        for name, definition in migrations.items():
            if name not in columns:
                try:
                    conn.execute(
                        f"ALTER TABLE library_index ADD COLUMN {name} {definition}"
                    )
                except sqlite3.OperationalError as exc:
                    # Multiple web/maintenance processes can open the derived
                    # index during deployment.  Another process may add the
                    # same column after our PRAGMA snapshot.
                    if "duplicate column name" not in str(exc).casefold():
                        raise
        conn.commit()
        return conn

    def _content_metrics_connection(self) -> sqlite3.Connection:
        """Open the small exact-content index independently of AI indexes."""

        self._require_legacy_sqlite("content metrics")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.content_metrics_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS book_metrics (
                catalog_id INTEGER PRIMARY KEY,
                source_path TEXT NOT NULL,
                source_bytes INTEGER NOT NULL DEFAULT 0,
                source_mtime_ns INTEGER NOT NULL DEFAULT 0,
                word_count INTEGER NOT NULL DEFAULT 0,
                chapter_count INTEGER NOT NULL DEFAULT 0,
                section_count INTEGER NOT NULL DEFAULT 0,
                index_status TEXT NOT NULL DEFAULT '',
                schema_version INTEGER NOT NULL DEFAULT 0,
                indexed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_book_metrics_fingerprint
                ON book_metrics(source_bytes, source_mtime_ns);
            """
        )
        return conn

    def _content_metrics_by_catalog(
        self,
        catalog_ids: Iterable[int],
    ) -> Dict[int, Dict[str, Any]]:
        normalized = sorted(
            {int(value) for value in catalog_ids if int(value) > 0}
        )
        if (
            normalized
            and self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            rows = self.mysql_catalog.metadata_for_ids(normalized)
            for row in rows.values():
                row["index_status"] = row.get("reader_index_status") or ""
                row["schema_version"] = int(
                    row.get("reader_schema_version") or 0
                )
                row["indexed_at"] = (
                    row.get("reader_indexed_at")
                    or row.get("indexed_at")
                    or ""
                )
            return rows
        if not normalized or not self.content_metrics_path.exists():
            return {}
        placeholders = ",".join("?" for _ in normalized)
        uri = f"{self.content_metrics_path.as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=15) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            rows = conn.execute(
                f"""
                SELECT * FROM book_metrics
                WHERE catalog_id IN ({placeholders})
                """,
                normalized,
            ).fetchall()
        return {int(row["catalog_id"]): dict(row) for row in rows}

    def _apply_content_metrics(
        self,
        items: List[Dict[str, Any]],
        *,
        include_latest_chapter: bool = True,
    ) -> List[Dict[str, Any]]:
        metrics = self._content_metrics_by_catalog(
            int(item.get("catalog_id") or 0) for item in items
        )
        for item in items:
            exact = metrics.get(int(item.get("catalog_id") or 0))
            if not exact:
                continue
            mysql_metrics = (
                self.infrastructure_settings.catalog_backend == "mysql"
            )
            if int(exact.get("source_bytes") or 0) != int(
                item.get("source_bytes") or 0
            ) or (
                not mysql_metrics
                and str(exact.get("source_path") or "")
                != str(item.get("source_path") or "")
            ):
                # Never let stale exact metrics override a newly replaced
                # source file before its incremental rebuild has run.
                continue
            item.update(
                {
                    "word_count": int(exact["word_count"]),
                    "chapter_count": int(exact["chapter_count"]),
                    "section_count": int(exact["section_count"]),
                    "reader_index_status": str(exact["index_status"]),
                    "reader_schema_version": int(exact["schema_version"]),
                    "reader_indexed_at": str(exact["indexed_at"]),
                    # Compatibility aliases for existing API consumers.
                    "approx_word_count": int(exact["word_count"]),
                    "approx_chapter_count": int(exact["chapter_count"]),
                }
            )
            if not include_latest_chapter:
                continue
            reader = _read_json(
                self._reader_index_path(int(item.get("catalog_id") or 0)),
                {},
            )
            latest = next(
                (
                    chapter
                    for chapter in reversed(reader.get("chapters") or [])
                    if chapter.get("kind") != "intro"
                ),
                None,
            )
            if latest:
                item["latest_chapter"] = {
                    "label": str(latest.get("label") or ""),
                    "title": str(latest.get("title") or ""),
                    "chapter_index": latest.get("chapter_index"),
                }
        return items

    def _load_index_status(self) -> Dict[str, Any]:
        return _read_json(
            self.index_status_path,
            {
                "status": "idle",
                "running": False,
                "processed": 0,
                "total": 0,
                "indexed": 0,
                "skipped": 0,
                "failed": 0,
                "message": "尚未建立派生索引",
            },
        )

    def _save_index_status(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        with _index_lock:
            current = self._load_index_status()
            current.update(patch)
            current["updated_at"] = _now()
            _atomic_write_json(self.index_status_path, current)
            return current

    def _load_tone_review_status(self) -> Dict[str, Any]:
        return _read_json(
            self.tone_review_status_path,
            {
                "status": "idle",
                "running": False,
                "processed": 0,
                "total": 0,
                "reviewed": 0,
                "failed": 0,
                "message": "尚无待复核基调",
            },
        )

    def _save_tone_review_status(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        with _index_lock:
            current = self._load_tone_review_status()
            current.update(patch)
            current["updated_at"] = _now()
            _atomic_write_json(self.tone_review_status_path, current)
            return current

    def _tone_review_counts(self) -> Dict[str, int]:
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            return self.mysql_catalog.tone_review_counts()
        if not self.index_path.exists():
            return {
                "cumulative_reviewed": 0,
                "pending": 0,
                "local_high_confidence": 0,
            }
        with self._index_connection() as conn:
            row = conn.execute(
                """
                SELECT
                  SUM(tone_review_status='reviewed') AS reviewed,
                  SUM(tone_review_status='pending') AS pending,
                  SUM(tone_review_status='not_needed') AS not_needed
                FROM library_index
                """
            ).fetchone()
        return {
            "cumulative_reviewed": int(row["reviewed"] or 0),
            "pending": int(row["pending"] or 0),
            "local_high_confidence": int(row["not_needed"] or 0),
        }

    def _load_plot_index_status(self) -> Dict[str, Any]:
        return _read_json(
            self.plot_index_status_path,
            {
                "status": "idle",
                "running": False,
                "processed": 0,
                "total": 0,
                "indexed": 0,
                "skipped": 0,
                "failed": 0,
                "books": 0,
                "segments": 0,
                "message": "尚未建立剧情语义索引",
            },
        )

    def _save_plot_index_status(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        with _index_lock:
            current = self._load_plot_index_status()
            current.update(patch)
            current["updated_at"] = _now()
            _atomic_write_json(self.plot_index_status_path, current)
            return current

    def _load_derived_index_refresh_status(self) -> Dict[str, Any]:
        return _read_json(
            self.derived_index_refresh_status_path,
            {
                "status": "idle",
                "running": False,
                "stage": "idle",
                "message": "等待书籍基调索引或手动剧情索引任务",
            },
        )

    def _load_ingestion_index_refresh_status(self) -> Dict[str, Any]:
        return _read_json(
            self.ingestion_index_refresh_status_path,
            {
                "status": "idle",
                "running": False,
                "stage": "idle",
                "catalog_ids": [],
                "message": "等待新书入库后的轻量索引任务",
            },
        )

    def index_probe_status(self) -> Dict[str, Any]:
        """Return file-backed index state without running catalog queries."""
        status = dict(self._load_index_status())
        status["pipeline"] = self._load_derived_index_refresh_status()
        status["ingestion_pipeline"] = (
            self._load_ingestion_index_refresh_status()
        )
        return status

    def plot_index_probe_status(self) -> Dict[str, Any]:
        """Return file-backed plot state without running catalog queries."""
        status = dict(self._load_plot_index_status())
        status["pipeline"] = self._load_derived_index_refresh_status()
        return status

    def queue_derived_index_refresh(
        self,
        *,
        force: bool = False,
        run_tone: bool = True,
        run_plot: bool = False,
        reason: str = "manual",
    ) -> Dict[str, Any]:
        """Persist work before starting the independent systemd worker.

        The request file is the durable hand-off. Closing the browser or
        restarting the API process cannot cancel the worker. New requests that
        arrive while it is running are merged and picked up in the next loop.
        """
        if not run_tone and not run_plot:
            raise ValueError("至少需要选择一种索引任务")
        reason = str(reason or "manual")[:80]
        if run_plot and not reason.startswith(MANUAL_PLOT_REASON_PREFIX):
            raise ValueError("剧情索引只能由后台手动操作触发")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        now = _now()
        with _derived_refresh_queue_lock:
            with self.derived_index_queue_lock_path.open(
                "a+", encoding="utf-8"
            ) as queue_lock:
                fcntl.flock(queue_lock, fcntl.LOCK_EX)
                request = _read_json(self.derived_index_request_path, {})
                pending = bool(request.get("pending"))
                pending_plot_authorized = bool(
                    pending
                    and request.get("run_plot")
                    and request.get("manual_plot_authorized") is True
                    and str(request.get("plot_reason") or "").startswith(
                        MANUAL_PLOT_REASON_PREFIX
                    )
                )
                merged_plot = bool(run_plot or pending_plot_authorized)
                request = {
                    "schema_version": 1,
                    "revision": int(request.get("revision") or 0) + 1,
                    "pending": True,
                    "run_tone": bool(run_tone or (pending and request.get("run_tone"))),
                    "run_plot": merged_plot,
                    "force_tone": bool(
                        (force and run_tone)
                        or (pending and request.get("force_tone"))
                    ),
                    "force_plot": bool(
                        (force and run_plot)
                        or (
                            pending_plot_authorized
                            and request.get("force_plot")
                        )
                    ),
                    "manual_plot_authorized": merged_plot,
                    "plot_reason": (
                        reason
                        if run_plot
                        else (
                            str(request.get("plot_reason") or "")[:80]
                            if pending_plot_authorized
                            else ""
                        )
                    ),
                    "reason": reason,
                    "requested_at": now,
                    "request_id": uuid.uuid4().hex[:16],
                }
                _atomic_write_json(self.derived_index_request_path, request)
                fcntl.flock(queue_lock, fcntl.LOCK_UN)

        current = self._load_derived_index_refresh_status()
        worker_alive = _pid_is_alive(current.get("pid"))
        pipeline = {
            **current,
            "status": "running" if worker_alive else "queued",
            "running": True,
            "stage": current.get("stage") if worker_alive else "queued",
            "request_id": request["request_id"],
            "request_revision": request["revision"],
            "requested_at": now,
            "reason": request["reason"],
            "run_tone": bool(
                request["run_tone"] or (worker_alive and current.get("run_tone"))
            ),
            "run_plot": bool(
                request["run_plot"] or (worker_alive and current.get("run_plot"))
            ),
            "rerun_requested": worker_alive,
            "message": (
                "已记录新的增量变更，当前轮结束后自动续跑"
                if worker_alive
                else "已提交后台索引任务，等待独立 worker 接管"
            ),
            "updated_at": now,
        }
        _atomic_write_json(self.derived_index_refresh_status_path, pipeline)
        if run_tone and not worker_alive:
            self._save_index_status(
                {
                    "status": "queued",
                    "running": False,
                    "pid": None,
                    "message": "书籍基调索引已进入后台队列",
                }
            )
        if run_plot and not worker_alive:
            self._save_plot_index_status(
                {
                    "status": "queued",
                    "running": False,
                    "pid": None,
                    "message": "剧情索引已进入手动后台队列",
                }
            )
        self._run_systemctl("reset-failed", DERIVED_INDEX_SERVICE, check=False)
        started = self._run_systemctl(
            "start",
            "--no-block",
            DERIVED_INDEX_SERVICE,
            check=False,
        )
        if started.returncode != 0:
            message = (started.stderr or started.stdout).strip()
            failed = {
                **pipeline,
                "status": "error",
                "running": False,
                "message": message or "后台索引服务启动失败",
                "updated_at": _now(),
            }
            _atomic_write_json(self.derived_index_refresh_status_path, failed)
            if run_tone:
                self._save_index_status(
                    {"status": "error", "running": False, "message": failed["message"]}
                )
            if run_plot:
                self._save_plot_index_status(
                    {"status": "error", "running": False, "message": failed["message"]}
                )
            raise RuntimeError(failed["message"])
        return pipeline

    @staticmethod
    def _normalized_catalog_ids(values: Iterable[Any]) -> List[int]:
        normalized: set[int] = set()
        for value in values:
            try:
                catalog_id = int(value)
            except (TypeError, ValueError):
                continue
            if catalog_id > 0:
                normalized.add(catalog_id)
        return sorted(normalized)

    def _start_ingestion_index_service(
        self,
        pipeline: Dict[str, Any],
        *,
        wait: bool,
    ) -> Dict[str, Any]:
        self._run_systemctl(
            "reset-failed",
            INGESTION_INDEX_SERVICE,
            check=False,
        )
        start_args = ["start"]
        if wait:
            start_args.append("--wait")
        else:
            start_args.append("--no-block")
        start_args.append(INGESTION_INDEX_SERVICE)
        started = self._run_systemctl(*start_args, check=False)
        if started.returncode == 0:
            return (
                self._load_ingestion_index_refresh_status()
                if wait
                else pipeline
            )
        message = (started.stderr or started.stdout).strip()
        failed = {
            **pipeline,
            "status": "error",
            "running": False,
            "message": message or "自动入库索引服务启动失败",
            "updated_at": _now(),
        }
        _atomic_write_json(self.ingestion_index_refresh_status_path, failed)
        raise RuntimeError(failed["message"])

    def queue_ingestion_index_refresh(
        self,
        catalog_ids: Iterable[Any],
        *,
        start_worker: bool = True,
        wait: bool = False,
        reason: str = "automatic_ingestion",
    ) -> Dict[str, Any]:
        """Queue exact new-book ids for the plot-free ingestion index.

        This queue is physically separate from the manual tone/plot request
        file.  Its worker has no plot-index branch and never discovers work by
        scanning the full library.
        """

        requested_ids = self._normalized_catalog_ids(catalog_ids)
        if not requested_ids:
            return {
                "status": "skipped",
                "running": False,
                "catalog_ids": [],
                "message": "没有需要更新的入库索引书目",
            }
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        now = _now()
        with _ingestion_refresh_queue_lock:
            with self.ingestion_index_queue_lock_path.open(
                "a+", encoding="utf-8"
            ) as queue_lock:
                fcntl.flock(queue_lock, fcntl.LOCK_EX)
                current = _read_json(self.ingestion_index_request_path, {})
                pending_ids = (
                    current.get("catalog_ids") or []
                    if current.get("pending")
                    else []
                )
                merged_ids = self._normalized_catalog_ids(
                    [*pending_ids, *requested_ids]
                )
                request = {
                    "schema_version": 1,
                    "revision": int(current.get("revision") or 0) + 1,
                    "pending": True,
                    "catalog_ids": merged_ids,
                    "reason": str(reason or "automatic_ingestion")[:80],
                    "requested_at": now,
                    "request_id": uuid.uuid4().hex[:16],
                }
                _atomic_write_json(self.ingestion_index_request_path, request)
                fcntl.flock(queue_lock, fcntl.LOCK_UN)

        current_status = self._load_ingestion_index_refresh_status()
        worker_alive = _pid_is_alive(current_status.get("pid"))
        pipeline = {
            **current_status,
            "status": "running" if worker_alive else "queued",
            "running": worker_alive,
            "stage": current_status.get("stage") if worker_alive else "queued",
            "request_id": request["request_id"],
            "request_revision": request["revision"],
            "catalog_ids": request["catalog_ids"],
            "requested_at": now,
            "reason": request["reason"],
            "rerun_requested": worker_alive,
            "message": (
                "已合并新书索引，当前单任务结束后继续"
                if worker_alive
                else "新书轻量索引已排队"
            ),
            "updated_at": now,
        }
        _atomic_write_json(self.ingestion_index_refresh_status_path, pipeline)
        if not start_worker or worker_alive:
            return pipeline
        return self._start_ingestion_index_service(pipeline, wait=wait)

    def start_queued_ingestion_index_refresh(
        self,
        *,
        wait: bool = False,
    ) -> Dict[str, Any]:
        """Start a previously batched ingestion queue without adding work."""

        request = _read_json(self.ingestion_index_request_path, {})
        catalog_ids = self._normalized_catalog_ids(
            request.get("catalog_ids") or []
        )
        if not request.get("pending") or not catalog_ids:
            return {
                "status": "completed",
                "running": False,
                "catalog_ids": [],
                "message": "没有待处理的新书轻量索引",
            }
        pipeline = self._load_ingestion_index_refresh_status()
        if _pid_is_alive(pipeline.get("pid")):
            return pipeline
        return self._start_ingestion_index_service(pipeline, wait=wait)

    def _mysql_source_status_counts(
        self,
    ) -> tuple[
        Dict[str, int],
        Dict[str, int],
        List[Dict[str, Any]],
        Dict[str, Dict[str, Any]],
    ]:
        if self.mysql_pool is None:
            raise RuntimeError("MySQL catalog is not configured")
        counts: Dict[str, int] = {}
        pages: Dict[str, int] = {}
        categories: List[Dict[str, Any]] = []
        libraries: Dict[str, Dict[str, Any]] = {
            "local": {
                "id": "local",
                "name": "本地书库",
                "total": 0,
                "downloaded": 0,
                "pending": 0,
                "recovery": 0,
                "failed": 0,
            },
            "fanqie": {
                "id": "fanqie",
                "name": "番茄书库",
                "total": 0,
                "downloaded": 0,
                "pending": 0,
                "recovery": 0,
                "failed": 0,
            },
        }
        with self.mysql_pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT library_id, status, book_count AS count
                    FROM catalog_status_counts
                    """
                )
                for row in cursor.fetchall():
                    status = str(row["status"])
                    count = int(row["count"])
                    counts[status] = counts.get(status, 0) + count
                    if status == "duplicate":
                        continue
                    library = libraries[str(row["library_id"])]
                    library["total"] += count
                    if status == "done":
                        library["downloaded"] += count
                    elif status in {"discovered", "downloading"}:
                        library["pending"] += count
                    elif status == "failed":
                        library["failed"] += count
                cursor.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM crawl_pages
                    WHERE source_name='txt80'
                    GROUP BY status
                    """
                )
                pages = {
                    str(row["status"]): int(row["count"])
                    for row in cursor.fetchall()
                }
                cursor.execute(
                    """
                    SELECT category AS name, SUM(book_count) AS count
                    FROM catalog_facets
                    WHERE body_available=1
                    GROUP BY category
                    ORDER BY count DESC
                    """
                )
                categories = [
                    {
                        "name": str(row["name"] or "未分类"),
                        "count": int(row["count"]),
                    }
                    for row in cursor.fetchall()
                ]
        for library in libraries.values():
            library["recovery"] = max(
                int(library["total"]) - int(library["downloaded"]),
                0,
            )
        return counts, pages, categories, libraries

    def source_status(
        self,
        project_root: Optional[Path] = None,
    ) -> Dict[str, Any]:
        global _index_task, _plot_index_task, _tone_review_task
        persisted_index_status = self._load_index_status()
        if (
            persisted_index_status.get("running")
            and (_index_task is None or _index_task.done())
            and not _pid_is_alive(persisted_index_status.get("pid"))
        ):
            persisted_index_status = self._save_index_status(
                {
                    "status": "interrupted",
                    "running": False,
                    "message": "索引进程已中断，可点击增量更新继续",
                }
            )
        persisted_plot_status = self._load_plot_index_status()
        if (
            persisted_plot_status.get("running")
            and (_plot_index_task is None or _plot_index_task.done())
            and not _pid_is_alive(persisted_plot_status.get("pid"))
        ):
            persisted_plot_status = self._save_plot_index_status(
                {
                    "status": "interrupted",
                    "running": False,
                    "message": "剧情索引进程已中断，可点击增量更新继续",
                }
            )
        tone_review_status = self._load_tone_review_status()
        if (
            tone_review_status.get("running")
            and (_tone_review_task is None or _tone_review_task.done())
            and not _pid_is_alive(tone_review_status.get("pid"))
        ):
            tone_review_status = self._save_tone_review_status(
                {
                    "status": "interrupted",
                    "running": False,
                    "message": "模型复核进程已中断，可重新启动并续跑 pending 项",
                }
            )
        counts: Dict[str, int] = {}
        pages: Dict[str, int] = {}
        categories: List[Dict[str, Any]] = []
        libraries = {
            "local": {
                "id": "local",
                "name": "本地书库",
                "total": 0,
                "downloaded": 0,
                "pending": 0,
                "recovery": 0,
                "failed": 0,
            },
            "fanqie": {
                "id": "fanqie",
                "name": "番茄书库",
                "total": 0,
                "downloaded": 0,
                "pending": 0,
                "recovery": 0,
                "failed": 0,
            },
        }
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_pool is not None
        ):
            counts, pages, categories, libraries = (
                self._mysql_source_status_counts()
            )
        else:
            with self._catalog_connection() as conn:
                catalog_tables = {
                    str(row["name"])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                catalog_columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_xinfo(books)")
                }
                library_expression = self._catalog_effective_library_expression(
                    catalog_columns, "books"
                )
                if "catalog_status_counts" in catalog_tables:
                    for row in conn.execute(
                        """
                        SELECT library_id, status, book_count AS count
                        FROM catalog_status_counts
                        """
                    ):
                        status = str(row["status"])
                        count = int(row["count"])
                        counts[status] = counts.get(status, 0) + count
                        if status == "duplicate":
                            continue
                        library = libraries[str(row["library_id"])]
                        library["total"] += count
                        if status == "done":
                            library["downloaded"] += count
                        elif status in {"discovered", "downloading"}:
                            library["pending"] += count
                        elif status == "failed":
                            library["failed"] += count
                    duplicate_count = int(
                        conn.execute(
                            "SELECT COUNT(*) FROM books WHERE status='duplicate'"
                        ).fetchone()[0]
                    )
                    if duplicate_count:
                        counts["duplicate"] = duplicate_count
                else:
                    for row in conn.execute(
                        "SELECT status, COUNT(*) AS count "
                        "FROM books GROUP BY status"
                    ):
                        counts[row["status"]] = row["count"]
                    for row in conn.execute(
                        f"""
                        SELECT
                          {library_expression} AS library_id,
                          status,
                          COUNT(*) AS count
                        FROM books
                        GROUP BY library_id, status
                        """
                    ):
                        if row["status"] == "duplicate":
                            continue
                        library = libraries[str(row["library_id"])]
                        count = int(row["count"])
                        library["total"] += count
                        if row["status"] == "done":
                            library["downloaded"] += count
                        elif row["status"] in {
                            "discovered",
                            "downloading",
                        }:
                            library["pending"] += count
                        elif row["status"] == "failed":
                            library["failed"] += count
                page_table = (
                    "listing_pages"
                    if "listing_pages" in catalog_tables
                    else "pages"
                )
                for row in conn.execute(
                    f"SELECT status, COUNT(*) AS count "
                    f"FROM {page_table} GROUP BY status"
                ):
                    pages[row["status"]] = row["count"]
                if "catalog_facets" in catalog_tables:
                    category_rows = conn.execute(
                        """
                        SELECT category, SUM(book_count) AS count
                        FROM catalog_facets
                        WHERE body_available=1
                        GROUP BY category
                        ORDER BY count DESC
                        """
                    )
                else:
                    category_rows = conn.execute(
                        "SELECT category, COUNT(*) AS count FROM books "
                        "WHERE status='done' GROUP BY category ORDER BY count DESC"
                    )
                categories = [
                    {"name": row["category"] or "未分类", "count": row["count"]}
                    for row in category_rows
                ]
            for library in libraries.values():
                library["recovery"] = max(
                    int(library["total"]) - int(library["downloaded"]),
                    0,
                )

        indexed_count = plot_books = plot_segments = 0
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            try:
                asset_counts = self.mysql_catalog.asset_counts()
                indexed_count = asset_counts["tone_books"]
                plot_books = asset_counts["plot_books"]
                plot_segments = asset_counts["plot_segments"]
            except Exception:
                indexed_count = plot_books = plot_segments = 0
        elif self.index_path.exists():
            try:
                with self._index_connection() as conn:
                    indexed_count = conn.execute("SELECT COUNT(*) FROM library_index").fetchone()[0]
                    plot_books = conn.execute("SELECT COUNT(*) FROM plot_index_meta").fetchone()[0]
                    plot_segments = conn.execute("SELECT COUNT(*) FROM plot_segments").fetchone()[0]
            except Exception:
                indexed_count = plot_books = plot_segments = 0
        unique_total = sum(
            count
            for status, count in counts.items()
            if status != "duplicate"
        )
        downloaded = counts.get("done", 0)
        indexable_books = downloaded
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            try:
                # A completed crawl row is not necessarily readable yet (for
                # example, an interrupted import can still lack its body).
                # Tone visibility must therefore be measured against the same
                # active/body-available population consumed by the indexer.
                indexable_books = self.mysql_catalog.derived_index_catalog_total()
            except Exception:
                indexable_books = downloaded
        stored_duplicates = int(counts.get("duplicate", 0) or 0)
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            authorized_duplicates = (
                self.mysql_catalog.authorized_deduplicated_count()
            )
        else:
            authorized_catalog_sync = _read_json(
                self.authorized_catalog_sync_status_path,
                {"stats": {}},
            )
            authorized_stats = (
                authorized_catalog_sync.get("stats")
                if isinstance(authorized_catalog_sync, dict)
                else {}
            )
            if not isinstance(authorized_stats, dict):
                authorized_stats = {}
            authorized_duplicates = int(
                authorized_stats.get("deduplicated", 0) or 0
            )
        intercepted_duplicates = stored_duplicates + authorized_duplicates
        sync_status = _read_json(
            self.sync_status_path,
            {
                "status": "idle",
                "running": False,
                "message": "尚未执行书库同步审计",
            },
        )
        deconstruction_status = self.list_global_deconstructions_cached(
            project_root
        )
        return {
            "library_root": str(self.library_root),
            "catalog_path": (
                "mysql://books"
                if self.infrastructure_settings.catalog_backend == "mysql"
                else str(self.catalog_path)
            ),
            "global_deconstruction_root": str(self.global_deconstruction_root),
            "source_read_only": True,
            "catalog_write_policy": "仅授权/公版作品导入器可新增记录；索引与分析始终只读",
            "remote_provider": {
                "id": "authorized_sources",
                "name": (
                    "本地书库 + 新笔趣阁全章节 + 爱下电子书 + 书宝网 + "
                    "哔哩轻小说 + txt80.cc + "
                    "番茄小说官方下载器 + Z-Library（已授权）+ "
                    "Project Gutenberg（公版）"
                ),
            },
            "remote_providers": [
                self.xbiquge_provider.availability(),
                self.ixdzs_provider.availability(),
                self.shubaow_provider.availability(),
                self.linovelib_provider.availability(),
                self.txt80_provider.availability(),
                self.zlibrary_provider.availability(),
                self.fanqie_downloader.availability(),
                {
                    "id": PUBLIC_BOOK_PROVIDER,
                    "name": "Project Gutenberg（公版作品）",
                    "enabled": True,
                    "authorization": "public_domain",
                },
            ],
            "books": {
                "total": unique_total,
                "raw_total": sum(counts.values()),
                # ``duplicates`` remains the count of rows physically retained
                # as status=duplicate for backwards compatibility.  Authorized
                # whole-site scans reject duplicate title+author pairs before
                # insertion, so expose their cumulative count separately and
                # provide one accurate UI total.
                "duplicates": stored_duplicates,
                "intercepted_duplicates": intercepted_duplicates,
                "duplicate_breakdown": {
                    "stored_catalog_rows": stored_duplicates,
                    "authorized_sources": authorized_duplicates,
                    "total": intercepted_duplicates,
                },
                "downloaded": downloaded,
                "discovered": counts.get("discovered", 0),
                "failed": counts.get("failed", 0),
                "by_status": counts,
                "libraries": libraries,
                # Tone metadata controls whether a freshly downloaded book is
                # visible in the shared index/catalog views. Plot evidence is
                # intentionally independent and may lag until a manual run.
                "indexable": indexable_books,
                "indexes_synchronized": indexable_books == indexed_count,
                "tone_index_synchronized": indexable_books == indexed_count,
                "plot_index_synchronized": indexable_books == plot_books,
            },
            "pages": {"total": sum(pages.values()), "by_status": pages},
            "categories": categories,
            "index": {
                **persisted_index_status,
                "count": indexed_count,
                "path": (
                    "mysql://book_metadata"
                    if self.infrastructure_settings.catalog_backend == "mysql"
                    else str(self.index_path)
                ),
                "global_shared": True,
                "source_catalog_read_only": True,
            },
            "tone_review": tone_review_status,
            "derived_index_refresh": self._load_derived_index_refresh_status(),
            "plot_index": {
                **persisted_plot_status,
                "books": plot_books,
                "segments": plot_segments,
                "path": (
                    "mysql://plot_index_meta+plot_segments"
                    if self.infrastructure_settings.catalog_backend == "mysql"
                    else str(self.index_path)
                ),
                "global_shared": True,
                "source_catalog_read_only": True,
                "token_strategy": "本地索引召回 → 少量证据交给 AI",
            },
            "sync": sync_status,
            "deconstruction": {
                key: value
                for key, value in deconstruction_status.items()
                if key != "items"
            },
            "runners": {
                "codex_cli_installed": (
                    APP_ROOT / ".tools" / "codex-cli" / "node_modules" / ".bin" / "codex"
                ).exists(),
                "openclaw_available": bool(shutil.which("openclaw")),
                "fallback_order": ["Codex CLI（已登录时）", "OpenClaw Gateway"],
            },
            "cover_progress": (
                MySQLLibraryRuntime(
                    self.infrastructure_settings,
                    self.mysql_pool,
                    self.redis_queue,
                ).cover_progress()
                if self.mysql_pool is not None
                else {}
            ),
        }

    def _iter_downloaded_books(self) -> Iterable[Any]:
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            for row in self.mysql_catalog.list_book_projection():
                item = self._materialize_mysql_catalog_row(row)
                if item.get("source_path"):
                    yield item
            return
        with self._catalog_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, source_id, title, author, category, expected_size,
                       output_path, bytes, sha256, updated_at
                FROM books
                WHERE status='done' AND output_path IS NOT NULL
                ORDER BY id
                """
            ).fetchall()
        yield from rows

    @staticmethod
    def _read_sample(path: Path, chunk_size: int = 10 * 1024) -> str:
        """按全书进度分层采样，避免只看开头/中部/结尾造成章节偏差。"""
        size = path.stat().st_size
        if size <= chunk_size * 10:
            return path.read_text(encoding="utf-8", errors="replace")
        parts: List[str] = []
        ratios = (0.02, 0.10, 0.24, 0.39, 0.57, 0.73, 0.87, 0.97)
        with path.open("rb") as handle:
            # 简介和第一章都位于文件开头。旧逻辑从 2% 起采样，导致大型
            # 轻小说丢失真实简介，并把分卷封面/插图路径误当作摘要。
            leading = handle.read(chunk_size).decode("utf-8", errors="replace")
            last_newline = leading.rfind("\n")
            if last_newline > 0:
                leading = leading[:last_newline]
            parts.append(f"<<<TONE_SAMPLE:00>>>\n{leading}")
            for ratio in ratios:
                center = int(size * ratio)
                handle.seek(max(0, min(size - chunk_size, center - chunk_size // 2)))
                raw = handle.read(chunk_size)
                text = raw.decode("utf-8", errors="replace")
                # 切掉块首尾可能被截断的半行，减少乱码和残句噪声。
                first_newline = text.find("\n")
                last_newline = text.rfind("\n")
                if 0 <= first_newline < last_newline:
                    text = text[first_newline + 1:last_newline]
                parts.append(
                    f"<<<TONE_SAMPLE:{int(ratio * 100):02d}>>>\n{text}"
                )
        return "\n\n".join(parts)

    @staticmethod
    def _tone_sections(sample: str) -> List[str]:
        raw_sections = re.split(r"(?m)^<<<TONE_SAMPLE:\d+>>>\s*$", sample)
        boilerplate = (
            "声明：本书为",
            "用户上传之内容开始",
            "用户上传之内容结束",
            "更多精校小说",
            "手机访问",
            "最新网址",
            "txt80",
        )
        sections: List[str] = []
        for raw in raw_sections:
            lines = [
                re.sub(r"\s+", " ", line).strip()
                for line in raw.splitlines()
            ]
            cleaned = "\n".join(
                line
                for line in lines
                if line and not any(marker.lower() in line.lower() for marker in boilerplate)
            )
            if cleaned:
                sections.append(cleaned)
        return sections or [sample]

    @staticmethod
    def _summary_from_sample(sample: str) -> str:
        """Return a real synopsis or the first chapter's first 300 characters."""

        def normalized_lines(value: str) -> List[str]:
            return [
                re.sub(r"\s+", " ", line).strip()
                for line in value.splitlines()
            ]

        def is_noise(line: str) -> bool:
            return bool(
                not line
                or line.startswith("<<<TONE_SAMPLE:")
                or LOCAL_MEDIA_MARKER_RE.fullmatch(line)
                or line in {"分卷封面", "插画", "插图", "展开", "收起"}
                or re.fullmatch(r"[（(]?插图\s*\d+[）)]?", line)
            )

        # Explicit source synopsis wins. Stop at a volume/chapter/media boundary
        # so local asset references can never leak into public metadata.
        intro_lines: List[str] = []
        capturing = False
        for line in normalized_lines(sample):
            match = SUMMARY_HEADING_RE.match(line)
            if match:
                capturing = True
                remainder = match.group(1).strip()
                if remainder and not is_noise(remainder):
                    intro_lines.append(remainder)
                continue
            if not capturing:
                if (
                    line.startswith("【")
                    or CHAPTER_HEADING_TOKEN_RE.search(line)
                    or LOCAL_MEDIA_MARKER_RE.fullmatch(line)
                    or line in {"分卷封面", "插画", "插图"}
                ):
                    break
                continue
            if (
                line.startswith("<<<TONE_SAMPLE:")
                or line.startswith("【")
                or CHAPTER_HEADING_TOKEN_RE.search(line)
                or LOCAL_MEDIA_MARKER_RE.fullmatch(line)
                or line in {"分卷封面", "插画", "插图"}
            ):
                break
            if not is_noise(line):
                intro_lines.append(line)
            if len("\n".join(intro_lines)) >= 600:
                break
        explicit = "\n".join(intro_lines).strip()[:600].rstrip()
        if explicit:
            return explicit

        # For large files only the 00% section is guaranteed to contain the
        # first chapter. Later tone samples must never become the fallback.
        marked_sections = re.split(
            r"(?m)^<<<TONE_SAMPLE:\d+>>>\s*$",
            sample,
        )
        leading = next((part for part in marked_sections if part.strip()), sample)
        lines = normalized_lines(leading)
        start = None
        for index, line in enumerate(lines):
            if CHAPTER_HEADING_TOKEN_RE.search(line) and "人物介绍" not in line:
                start = index + 1
                break
        candidates = lines[start:] if start is not None else lines
        body_lines: List[str] = []
        for line in candidates:
            if start is not None and CHAPTER_HEADING_TOKEN_RE.search(line):
                break
            if (
                is_noise(line)
                or SUMMARY_METADATA_RE.match(line)
                or SUMMARY_HEADING_RE.match(line)
                or line.startswith("【")
                or line.startswith("声明：本书为")
                or line.startswith("用户上传之内容")
            ):
                continue
            body_lines.append(line)
            compact = re.sub(r"\s+", " ", " ".join(body_lines)).strip()
            if len(compact) >= 300:
                return compact[:300].rstrip()
        return re.sub(r"\s+", " ", " ".join(body_lines)).strip()[:300].rstrip()

    @staticmethod
    def _tone_idf(
        keyword: str,
        document_frequency: Optional[Dict[str, int]],
        corpus_size: int,
    ) -> float:
        if not document_frequency or corpus_size <= 0:
            return 1.0
        frequency = max(int(document_frequency.get(keyword, 0)), 0)
        normalized = math.log((corpus_size + 1) / (frequency + 1)) / max(
            math.log(corpus_size + 1), 1
        )
        return min(2.25, max(0.75, 0.75 + normalized * 1.5))

    @staticmethod
    def _extract_features(
        title: str,
        category: str,
        sample: str,
        tone_document_frequency: Optional[Dict[str, int]] = None,
        corpus_size: int = 0,
    ) -> Dict[str, Any]:
        sections = ElectronicLibraryService._tone_sections(sample)
        clean_sample = "\n".join(sections)
        haystack = f"{title}\n{category}\n{clean_sample}"
        genre_scores: Counter[str] = Counter()
        tone_scores: Counter[str] = Counter()
        keyword_counts: Dict[str, int] = {}
        genre_match_counts = Counter(
            match.group(0) for match in GENRE_KEYWORD_PATTERN.finditer(haystack)
        )
        title_tone_counts = Counter(
            match.group(0) for match in TONE_KEYWORD_PATTERN.finditer(title)
        )
        section_tone_counts = [
            Counter(match.group(0) for match in TONE_KEYWORD_PATTERN.finditer(section))
            for section in sections
        ]
        body_tone_counts: Counter[str] = Counter()
        for counts in section_tone_counts:
            body_tone_counts.update(counts)

        primary_genres = list(CATEGORY_GENRES.get(category, ()))
        for genre in primary_genres:
            genre_scores[genre] += 30
        for genre, keywords in GENRE_KEYWORDS.items():
            score = 0
            for keyword in keywords:
                count = genre_match_counts.get(keyword, 0)
                if count:
                    keyword_counts[keyword] = count
                    score += min(count, 8)
            if score:
                genre_scores[genre] += score
        # 规则证据先用语料逆文档频率降噪，再与题材先验合成候选。
        # 先验只提供低置信度兜底，确保每本小说至少有一个主基调。
        tone_evidence: Dict[str, Dict[str, Any]] = {}
        for tone, rule in TONE_RULES.items():
            evidence_score = 0.0
            distinct_hits = 0
            strong_hits = 0
            title_hits = 0
            matched: Dict[str, int] = {}
            covered_sections: set[int] = set()
            for level, body_weight, title_weight, cap in (
                ("strong", 12.0, 24.0, 3),
                ("medium", 5.0, 18.0, 4),
                ("weak", 2.0, 10.0, 3),
            ):
                for keyword in rule.get(level, ()):
                    title_count = title_tone_counts.get(keyword, 0)
                    body_count = body_tone_counts.get(keyword, 0)
                    total_count = title_count + body_count
                    if not total_count:
                        continue
                    matched[keyword] = total_count
                    keyword_counts[keyword] = max(
                        keyword_counts.get(keyword, 0), total_count
                    )
                    distinct_hits += 1
                    title_hits += title_count
                    if level == "strong":
                        strong_hits += total_count
                    idf = ElectronicLibraryService._tone_idf(
                        keyword, tone_document_frequency, corpus_size
                    )
                    evidence_score += min(body_count, cap) * body_weight * idf
                    if title_count:
                        evidence_score += title_weight * idf
                    for index, section_counts in enumerate(section_tone_counts):
                        if section_counts.get(keyword, 0):
                            covered_sections.add(index)
            if distinct_hits >= 2:
                evidence_score += min(distinct_hits - 1, 4) * 2
            if len(covered_sections) >= 2:
                evidence_score += min(len(covered_sections) - 1, 4) * 2
            eligible = bool(
                evidence_score >= 18
                and (strong_hits or title_hits or distinct_hits >= 2)
            )
            if eligible:
                tone_scores[tone] = round(evidence_score, 2)
                tone_evidence[tone] = {
                    "evidence_score": round(evidence_score, 2),
                    "matched": matched,
                    "section_coverage": len(covered_sections),
                    "title_hits": title_hits,
                }

        inferred_genres = [
            name
            for name, score in genre_scores.most_common()
            if name not in primary_genres and score >= 10
        ]
        genre_tags = [*primary_genres, *inferred_genres[:2]]
        if not genre_tags:
            genre_tags = [category or "网络小说"]
        priors = CATEGORY_TONE_PRIORS.get(category, DEFAULT_TONE_PRIORS)
        candidate_scores: Dict[str, float] = {
            tone: float(score) for tone, score in tone_scores.items()
        }
        for index, tone in enumerate(priors):
            candidate_scores[tone] = candidate_scores.get(tone, 0.0) + max(
                2.0, 8.0 - index * 1.5
            )
        ranked_tones = sorted(
            candidate_scores.items(),
            key=lambda item: (-item[1], list(TONE_RULES).index(item[0])),
        )
        if not ranked_tones:
            ranked_tones = [(DEFAULT_TONE_PRIORS[0], 1.0)]
        top_score = ranked_tones[0][1]
        candidate_names = [name for name, _ in ranked_tones[:5]]
        primary_tones = [candidate_names[0]]
        if (
            len(ranked_tones) > 1
            and ranked_tones[1][1] >= top_score * 0.82
            and ranked_tones[1][0] in tone_scores
        ):
            primary_tones.append(ranked_tones[1][0])
        secondary_floor = max(18.0, float(tone_scores.get(primary_tones[0], 0)) * 0.55)
        secondary_tones = [
            name
            for name in candidate_names
            if (
                name not in primary_tones
                and name in tone_scores
                and float(tone_scores[name]) >= secondary_floor
            )
        ][:3]
        tone_tags = [*primary_tones, *secondary_tones]

        top_name = primary_tones[0]
        top_rule_score = float(tone_scores.get(top_name, 0))
        if top_rule_score >= 50:
            tone_confidence = 0.9
        elif top_rule_score >= 32:
            tone_confidence = 0.8
        elif top_rule_score >= 18:
            tone_confidence = 0.68
        else:
            tone_confidence = 0.38
        if tone_evidence.get(top_name, {}).get("title_hits"):
            tone_confidence = max(tone_confidence, 0.84)
        if tone_evidence.get(top_name, {}).get("section_coverage", 0) >= 3:
            tone_confidence = min(0.95, tone_confidence + 0.05)
        score_gap = (
            top_score - ranked_tones[1][1]
            if len(ranked_tones) > 1
            else top_score
        )
        review_required = bool(
            tone_confidence < 0.72 or score_gap < max(4.0, top_score * 0.12)
        )
        tone_source = "rule_evidence" if top_rule_score else "category_prior"

        # 模型只接收短代表片段，不接收全文。
        representative_fragments: List[str] = []
        for section in sections:
            compact = re.sub(r"\s+", " ", section).strip()
            if len(compact) >= 80:
                representative_fragments.append(compact[:240])
            if len(representative_fragments) >= 4:
                break
        candidate_details = []
        for name, score in ranked_tones[:5]:
            detail = tone_evidence.get(name, {})
            candidate_details.append(
                {
                    "name": name,
                    "score": round(float(score), 2),
                    "matched": detail.get("matched", {}),
                    "section_coverage": detail.get("section_coverage", 0),
                }
            )
        tone_evidence_payload = {
            "primary": primary_tones,
            "secondary": secondary_tones,
            "candidates": candidate_details,
            "fragments": representative_fragments,
            "local_confidence": round(tone_confidence, 3),
            "source": tone_source,
            "review_required": review_required,
        }
        summary = ElectronicLibraryService._summary_from_sample(sample)
        searchable = f"{title} {category} {' '.join(genre_tags)} {' '.join(tone_tags)} {summary}"[:5000]
        return {
            "genre_tags": genre_tags,
            "tone_tags": tone_tags,
            "primary_tone_tags": primary_tones,
            "secondary_tone_tags": secondary_tones,
            "tone_confidence": round(tone_confidence, 3),
            "tone_source": tone_source,
            "tone_evidence": tone_evidence_payload,
            "tone_review_status": "pending" if review_required else "not_needed",
            "keyword_counts": keyword_counts,
            "summary": summary,
            "searchable_text": searchable,
        }

    def _build_index_mysql(
        self,
        rows: List[Dict[str, Any]],
        *,
        force: bool,
        rules_changed: bool,
        catalog_total: int,
        baseline: int,
    ) -> Dict[str, Any]:
        if self.mysql_catalog is None:
            raise RuntimeError("MySQL 书目后端未初始化")
        tone_document_frequency = self.mysql_catalog.metadata_state_get(
            "tone_document_frequency",
            {},
        )
        frequency_version = str(
            self.mysql_catalog.metadata_state_get(
                "tone_document_frequency_version",
                "",
            )
            or ""
        )
        if not isinstance(tone_document_frequency, dict):
            tone_document_frequency = {}
        if (
            force
            or not tone_document_frequency
            or (rules_changed and frequency_version != TONE_RULE_VERSION)
        ):
            document_frequency: Counter[str] = Counter()
            indexed_texts = self.mysql_catalog.metadata_search_texts()
            frequency_sources = indexed_texts or rows
            for position, row in enumerate(frequency_sources, start=1):
                try:
                    if indexed_texts:
                        title = str(row.get("title") or "")
                        clean_sample = str(row.get("searchable_text") or "")
                    else:
                        source_path = Path(
                            str(row.get("output_path") or "")
                        ).expanduser().resolve()
                        title = str(row.get("title") or source_path.stem)
                        clean_sample = "\n".join(
                            self._tone_sections(
                                self._read_sample(source_path)
                            )
                        )
                    document_frequency.update(
                        {
                            match.group(0)
                            for match in TONE_KEYWORD_PATTERN.finditer(
                                f"{title}\n{clean_sample}"
                            )
                        }
                    )
                except Exception:
                    continue
                if (
                    position == len(frequency_sources)
                    or position % 100 == 0
                ):
                    self._save_index_status(
                        {
                            "processed": 0,
                            "message": (
                                "正在从 MySQL 元数据计算关键词逆权重 "
                                f"{position}/{len(frequency_sources)}"
                            ),
                        }
                    )
            tone_document_frequency = dict(document_frequency)
            self.mysql_catalog.metadata_state_set(
                "tone_document_frequency",
                tone_document_frequency,
            )
            self.mysql_catalog.metadata_state_set(
                "tone_document_frequency_version",
                TONE_RULE_VERSION,
            )

        indexed = failed = 0
        pending: List[Dict[str, Any]] = []
        object_root = self.infrastructure_settings.object_root.resolve()

        def flush() -> None:
            nonlocal pending, indexed
            if pending:
                self.mysql_catalog.upsert_metadata_batch(pending)
                indexed += len(pending)
                pending = []

        for candidate_position, row in enumerate(rows, start=1):
            position = baseline + candidate_position
            catalog_id = int(row.get("catalog_id") or row.get("id") or 0)
            try:
                source_path = Path(
                    str(row.get("output_path") or row.get("source_path") or "")
                ).expanduser().resolve()
                if not (
                    _is_within(source_path, self.books_root)
                    or _is_within(source_path, object_root)
                ):
                    raise ValueError("来源文件不在电子书库或对象存储范围内")
                stat = source_path.stat()
                features = self._extract_features(
                    str(row.get("title") or source_path.stem),
                    str(row.get("category") or ""),
                    self._read_sample(source_path),
                    tone_document_frequency,
                    catalog_total,
                )
                reader_index = self._reader_index(catalog_id, source_path)
                pending.append(
                    {
                        "catalog_id": catalog_id,
                        "source_mtime_ns": stat.st_mtime_ns,
                        "source_sha256": str(row.get("sha256") or ""),
                        "tone_rule_version": TONE_RULE_VERSION,
                        "source_path": str(source_path),
                        "source_bytes": stat.st_size,
                        "word_count": int(reader_index["word_count"]),
                        "chapter_count": int(reader_index["chapter_count"]),
                        "section_count": int(reader_index["section_count"]),
                        "reader_index_status": str(reader_index["index_status"]),
                        "reader_schema_version": int(reader_index["schema_version"]),
                        "reader_indexed_at": reader_index["indexed_at"],
                        "indexed_at": _now(),
                        **features,
                    }
                )
            except Exception:
                failed += 1

            if candidate_position == len(rows) or candidate_position % 10 == 0:
                flush()
                self._save_index_status(
                    {
                        "processed": baseline + indexed,
                        "indexed": indexed,
                        "skipped": baseline,
                        "failed": failed,
                        "remaining": max(catalog_total - baseline - indexed, 0),
                        "message": (
                            f"增量处理 {baseline + indexed}/{catalog_total} 本"
                            f"（本轮已检查 {candidate_position}/{len(rows)}）"
                        ),
                    }
                )
        flush()
        if not failed:
            self.mysql_catalog.metadata_state_set(
                "tone_rule_version",
                TONE_RULE_VERSION,
            )
        self.mysql_catalog.remove_unavailable_metadata()
        count = self.mysql_catalog.asset_counts()["tone_books"]
        return self._save_index_status(
            {
                "status": "completed_with_errors" if failed else "completed",
                "running": False,
                "processed": max(catalog_total - failed, 0),
                "total": catalog_total,
                "indexed": indexed,
                "skipped": baseline,
                "failed": failed,
                "remaining": failed,
                "count": count,
                "finished_at": _now(),
                "message": (
                    f"书籍基调与索引完成，共 {count} 本"
                    + (
                        "；基调规则升级已逐书重算"
                        if rules_changed
                        else ""
                    )
                ),
            }
        )

    def build_ingestion_index(
        self,
        catalog_ids: Iterable[Any],
    ) -> Dict[str, Any]:
        """Make newly downloaded books visible without a full-file scan.

        The recommendation page uses ``book_metadata`` as its visibility
        boundary.  Automatic ingestion therefore only samples up to 80 KiB
        per requested book for tone/search metadata.  Exact chapter and word
        metrics remain lazy and are built by the reader or by a manual index
        action.  Plot tables are never read or written here.
        """

        ids = self._normalized_catalog_ids(catalog_ids)
        if not ids:
            return {
                "status": "completed",
                "running": False,
                "processed": 0,
                "total": 0,
                "indexed": 0,
                "skipped": 0,
                "failed": 0,
                "message": "没有待处理的新书轻量索引",
            }
        if (
            self.infrastructure_settings.catalog_backend != "mysql"
            or self.mysql_catalog is None
        ):
            return self.build_index(force=False, catalog_ids=ids)

        started_at = _now()
        catalog_total = self.mysql_catalog.derived_index_catalog_total()
        rows = self.mysql_catalog.list_tone_index_candidates(
            rule_version=TONE_RULE_VERSION,
            force=False,
            catalog_ids=ids,
        )
        skipped = max(len(ids) - len(rows), 0)
        self._save_index_status(
            {
                "status": "running",
                "running": True,
                "processed": skipped,
                "total": len(ids),
                "baseline": skipped,
                "pending_total": len(rows),
                "catalog_total": catalog_total,
                "remaining": len(rows),
                "indexed": 0,
                "skipped": skipped,
                "failed": 0,
                "checkpoint_scope": "ingestion-targets",
                "resumed": False,
                "started_at": started_at,
                "finished_at": None,
                "pid": os.getpid(),
                "message": f"正在轻量更新 {len(rows)} 本新入库作品",
            }
        )

        tone_document_frequency = self.mysql_catalog.metadata_state_get(
            "tone_document_frequency",
            {},
        )
        if not isinstance(tone_document_frequency, dict):
            tone_document_frequency = {}
        object_root = self.infrastructure_settings.object_root.resolve()
        records: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []
        for row in rows:
            catalog_id = int(row.get("catalog_id") or row.get("id") or 0)
            try:
                source_path = Path(
                    str(row.get("output_path") or row.get("source_path") or "")
                ).expanduser().resolve()
                if not (
                    _is_within(source_path, self.books_root)
                    or _is_within(source_path, object_root)
                ):
                    raise ValueError("来源文件不在电子书库或对象存储范围内")
                stat = source_path.stat()
                cached_reader = _read_json(
                    self._reader_index_path(catalog_id),
                    {},
                )
                reader_ready = bool(
                    cached_reader
                    and self._reader_index_cache_valid(
                        cached_reader,
                        source_path,
                    )
                )
                if reader_ready:
                    word_count = int(cached_reader.get("word_count") or 0)
                    chapter_count = int(
                        cached_reader.get("chapter_count") or 0
                    )
                    section_count = int(
                        cached_reader.get("section_count") or 0
                    )
                    reader_status = str(
                        cached_reader.get("index_status") or "exact"
                    )
                    reader_schema_version = int(
                        cached_reader.get("schema_version") or 0
                    )
                    reader_indexed_at = cached_reader.get("indexed_at")
                else:
                    # UTF-8 中文正文通常约三字节/字。这里只给列表页一个
                    # 安全近似值，首次打开阅读器时会用精确目录覆盖。
                    word_count = max(int(stat.st_size // 3), 1)
                    chapter_count = max(
                        int(row.get("approx_chapter_count") or 0),
                        0,
                    )
                    section_count = chapter_count
                    reader_status = "pending"
                    reader_schema_version = 0
                    reader_indexed_at = None
                features = self._extract_features(
                    str(row.get("title") or source_path.stem),
                    str(row.get("category") or ""),
                    self._read_sample(source_path),
                    tone_document_frequency,
                    catalog_total,
                )
                records.append(
                    {
                        "catalog_id": catalog_id,
                        "source_mtime_ns": stat.st_mtime_ns,
                        "source_sha256": str(row.get("sha256") or ""),
                        "tone_rule_version": TONE_RULE_VERSION,
                        "source_path": str(source_path),
                        "source_bytes": stat.st_size,
                        "word_count": word_count,
                        "chapter_count": chapter_count,
                        "section_count": section_count,
                        "reader_index_status": reader_status,
                        "reader_schema_version": reader_schema_version,
                        "reader_indexed_at": reader_indexed_at,
                        "indexed_at": _now(),
                        **features,
                    }
                )
            except Exception as exc:
                failures.append(
                    {
                        "catalog_id": catalog_id,
                        "error": f"{type(exc).__name__}: {str(exc)[:240]}",
                    }
                )

        indexed = self.mysql_catalog.upsert_metadata_batch(records)
        count = self.mysql_catalog.asset_counts()["tone_books"]
        failed = len(failures)
        return self._save_index_status(
            {
                "status": "completed_with_errors" if failed else "completed",
                "running": False,
                "processed": indexed + skipped,
                "total": len(ids),
                "indexed": indexed,
                "skipped": skipped,
                "failed": failed,
                "remaining": failed,
                "count": count,
                "failures": failures[:20],
                "finished_at": _now(),
                "message": (
                    f"新书轻量索引完成：新增/更新 {indexed} 本"
                    + (f"，失败 {failed} 本" if failed else "")
                ),
            }
        )

    def build_index(
        self,
        force: bool = False,
        catalog_ids: Optional[Iterable[Any]] = None,
    ) -> Dict[str, Any]:
        started_at = _now()
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            previous_rule_version = str(
                self.mysql_catalog.metadata_state_get("tone_rule_version", "")
                or ""
            )
            rules_changed = previous_rule_version != TONE_RULE_VERSION
            catalog_total = self.mysql_catalog.derived_index_catalog_total()
            rows = self.mysql_catalog.list_tone_index_candidates(
                rule_version=TONE_RULE_VERSION,
                force=force,
                catalog_ids=(
                    self._normalized_catalog_ids(catalog_ids)
                    if catalog_ids is not None
                    else None
                ),
            )
            baseline = 0 if force else max(catalog_total - len(rows), 0)
            self._save_index_status(
                {
                    "status": "running",
                    "running": True,
                    "processed": baseline,
                    "total": catalog_total,
                    "baseline": baseline,
                    "pending_total": len(rows),
                    "catalog_total": catalog_total,
                    "remaining": len(rows),
                    "indexed": 0,
                    "skipped": baseline,
                    "failed": 0,
                    "checkpoint_scope": f"tone:{TONE_RULE_VERSION}",
                    "resumed": bool(baseline and len(rows)),
                    "started_at": started_at,
                    "finished_at": None,
                    "pid": os.getpid(),
                    "message": (
                        f"从逐书断点继续，待更新 {len(rows)} 本"
                        if baseline and rows
                        else f"待更新 {len(rows)} 本书籍基调与索引"
                    ),
                }
            )
            return self._build_index_mysql(
                rows,
                force=force,
                rules_changed=rules_changed,
                catalog_total=catalog_total,
                baseline=baseline,
            )

        rows = list(self._iter_downloaded_books())
        if catalog_ids is not None:
            selected_ids = set(self._normalized_catalog_ids(catalog_ids))
            rows = [
                row
                for row in rows
                if int(
                    row["id"]
                    if "id" in row.keys()
                    else row["catalog_id"]
                ) in selected_ids
            ]
        self._save_index_status(
            {
                "status": "running",
                "running": True,
                "processed": 0,
                "total": len(rows),
                "indexed": 0,
                "skipped": 0,
                "failed": 0,
                "started_at": started_at,
                "finished_at": None,
                "pid": os.getpid(),
                "message": "正在读取已下载作品并建立派生索引",
            }
        )
        indexed = skipped = failed = 0
        seen_source_ids: set[str] = set()

        with self._index_connection() as index_conn:
            version_row = index_conn.execute(
                "SELECT value FROM index_metadata WHERE key='tone_rule_version'"
            ).fetchone()
            previous_rule_version = version_row["value"] if version_row else ""
            rules_changed = previous_rule_version != TONE_RULE_VERSION
            # A classifier rule change invalidates every persisted tone tag,
            # even when the source file fingerprint itself is unchanged.
            force = force or rules_changed
            df_row = index_conn.execute(
                "SELECT value FROM index_metadata WHERE key='tone_document_frequency'"
            ).fetchone()
            try:
                tone_document_frequency = json.loads(
                    df_row["value"] if df_row else "{}"
                )
            except (TypeError, ValueError):
                tone_document_frequency = {}
            if force or not tone_document_frequency:
                document_frequency: Counter[str] = Counter()
                indexed_texts = index_conn.execute(
                    """
                    SELECT title,searchable_text
                    FROM library_index
                    WHERE NULLIF(searchable_text,'') IS NOT NULL
                    """
                ).fetchall()
                frequency_sources = indexed_texts or rows
                for position, row in enumerate(frequency_sources, start=1):
                    try:
                        if indexed_texts:
                            title = str(row["title"] or "")
                            clean_sample = str(row["searchable_text"] or "")
                        else:
                            source_path = (
                                Path(row["output_path"]).expanduser().resolve()
                            )
                            title = str(row["title"] or source_path.stem)
                            sample = self._read_sample(source_path)
                            clean_sample = "\n".join(
                                self._tone_sections(sample)
                            )
                        found = {
                            match.group(0)
                            for match in TONE_KEYWORD_PATTERN.finditer(
                                f"{title}\n{clean_sample}"
                            )
                        }
                        document_frequency.update(found)
                    except Exception:
                        continue
                    if (
                        position == len(frequency_sources)
                        or position % 100 == 0
                    ):
                        self._save_index_status(
                            {
                                "processed": 0,
                                "message": (
                                    "正在从本地索引计算关键词逆权重 "
                                    f"{position}/{len(frequency_sources)}"
                                ),
                            }
                        )
                tone_document_frequency = dict(document_frequency)
                index_conn.execute(
                    """
                    INSERT INTO index_metadata(key, value)
                    VALUES ('tone_document_frequency', ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (
                        json.dumps(
                            tone_document_frequency,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    ),
                )
                index_conn.commit()
            existing = {
                row["source_id"]: {
                    "catalog_id": row["catalog_id"],
                    "source_path": row["source_path"],
                    "source_bytes": row["source_bytes"],
                    "source_mtime_ns": row["source_mtime_ns"],
                }
                for row in index_conn.execute(
                    """
                    SELECT source_id, catalog_id, source_path,
                           source_bytes, source_mtime_ns
                    FROM library_index
                    """
                )
            }
            for position, row in enumerate(rows, start=1):
                source_id = str(row["source_id"] or row["id"])
                seen_source_ids.add(source_id)
                try:
                    resolved_source_path = (
                        Path(row["output_path"]).expanduser().resolve()
                    )
                    if not _is_within(resolved_source_path, self.books_root):
                        raise ValueError("来源文件不在电子书库书籍目录内")
                    relative_source_path = resolved_source_path.relative_to(
                        self.books_root.resolve()
                    )
                    source_path = self.books_root / relative_source_path
                    stat = source_path.stat()
                    fingerprint = (stat.st_size, stat.st_mtime_ns)
                    previous = existing.get(source_id)
                    previous_fingerprint = (
                        (
                            int(previous["source_bytes"]),
                            int(previous["source_mtime_ns"]),
                        )
                        if previous
                        else None
                    )
                    metadata_changed = bool(
                        previous
                        and (
                            int(previous["catalog_id"]) != int(row["id"])
                            or str(previous["source_path"]) != str(source_path)
                        )
                    )
                    if not force and previous_fingerprint == fingerprint:
                        if metadata_changed:
                            # 目录迁移不会改变正文大小和 mtime。旧逻辑只比指纹，
                            # 会把已经失效的旧路径永久留在派生索引中。
                            index_conn.execute(
                                """
                                UPDATE library_index
                                SET catalog_id=?, title=?, author=?, category=?,
                                    source_path=?, source_bytes=?,
                                    source_mtime_ns=?, indexed_at=?
                                WHERE source_id=?
                                """,
                                (
                                    row["id"],
                                    row["title"] or source_path.stem,
                                    row["author"] or "",
                                    row["category"] or "",
                                    str(source_path),
                                    stat.st_size,
                                    stat.st_mtime_ns,
                                    _now(),
                                    source_id,
                                ),
                            )
                            indexed += 1
                        else:
                            skipped += 1
                    else:
                        sample = self._read_sample(source_path)
                        features = self._extract_features(
                            row["title"] or source_path.stem,
                            row["category"] or "",
                            sample,
                            tone_document_frequency,
                            len(rows),
                        )
                        reader_index = self._reader_index(int(row["id"]), source_path)
                        exact_words = int(reader_index["word_count"])
                        exact_chapters = int(reader_index["chapter_count"])
                        exact_sections = int(reader_index["section_count"])
                        index_conn.execute(
                            """
                            INSERT INTO library_index (
                                source_id, catalog_id, title, author, category,
                                source_path, source_bytes, source_mtime_ns,
                                approx_word_count, approx_chapter_count, summary,
                                word_count, chapter_count, section_count,
                                reader_index_status, reader_schema_version,
                                reader_indexed_at,
                                searchable_text, genre_tags, tone_tags,
                                primary_tone_tags, secondary_tone_tags,
                                tone_confidence, tone_source, tone_evidence,
                                tone_review_status, tone_review_model,
                                tone_reviewed_at,
                                keyword_counts, indexed_at
                            ) VALUES (
                                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                            )
                            ON CONFLICT(source_id) DO UPDATE SET
                                catalog_id=excluded.catalog_id,
                                title=excluded.title,
                                author=excluded.author,
                                category=excluded.category,
                                source_path=excluded.source_path,
                                source_bytes=excluded.source_bytes,
                                source_mtime_ns=excluded.source_mtime_ns,
                                approx_word_count=excluded.approx_word_count,
                                approx_chapter_count=excluded.approx_chapter_count,
                                word_count=excluded.word_count,
                                chapter_count=excluded.chapter_count,
                                section_count=excluded.section_count,
                                reader_index_status=excluded.reader_index_status,
                                reader_schema_version=excluded.reader_schema_version,
                                reader_indexed_at=excluded.reader_indexed_at,
                                summary=excluded.summary,
                                searchable_text=excluded.searchable_text,
                                genre_tags=excluded.genre_tags,
                                tone_tags=excluded.tone_tags,
                                primary_tone_tags=excluded.primary_tone_tags,
                                secondary_tone_tags=excluded.secondary_tone_tags,
                                tone_confidence=excluded.tone_confidence,
                                tone_source=excluded.tone_source,
                                tone_evidence=excluded.tone_evidence,
                                tone_review_status=excluded.tone_review_status,
                                tone_review_model=excluded.tone_review_model,
                                tone_reviewed_at=excluded.tone_reviewed_at,
                                keyword_counts=excluded.keyword_counts,
                                indexed_at=excluded.indexed_at
                            """,
                            (
                                source_id,
                                row["id"],
                                row["title"] or source_path.stem,
                                row["author"] or "",
                                row["category"] or "",
                                str(source_path),
                                stat.st_size,
                                stat.st_mtime_ns,
                                exact_words,
                                exact_chapters,
                                features["summary"],
                                exact_words,
                                exact_chapters,
                                exact_sections,
                                reader_index["index_status"],
                                READER_INDEX_SCHEMA_VERSION,
                                reader_index["indexed_at"],
                                features["searchable_text"],
                                json.dumps(features["genre_tags"], ensure_ascii=False),
                                json.dumps(features["tone_tags"], ensure_ascii=False),
                                json.dumps(
                                    features["primary_tone_tags"],
                                    ensure_ascii=False,
                                ),
                                json.dumps(
                                    features["secondary_tone_tags"],
                                    ensure_ascii=False,
                                ),
                                features["tone_confidence"],
                                features["tone_source"],
                                json.dumps(
                                    features["tone_evidence"],
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                                features["tone_review_status"],
                                "",
                                None,
                                json.dumps(features["keyword_counts"], ensure_ascii=False),
                                _now(),
                            ),
                        )
                        indexed += 1
                    if position % 25 == 0:
                        index_conn.commit()
                except Exception:
                    failed += 1

                if position == len(rows) or position % 10 == 0:
                    self._save_index_status(
                        {
                            "processed": position,
                            "indexed": indexed,
                            "skipped": skipped,
                            "failed": failed,
                            "message": f"已处理 {position}/{len(rows)} 本",
                        }
                    )

            if seen_source_ids:
                placeholders = ",".join("?" for _ in seen_source_ids)
                index_conn.execute(
                    f"DELETE FROM library_index WHERE source_id NOT IN ({placeholders})",
                    tuple(seen_source_ids),
                )
            elif force:
                index_conn.execute("DELETE FROM library_index")
            index_conn.commit()
            index_conn.execute(
                """
                INSERT INTO index_metadata(key, value)
                VALUES ('tone_rule_version', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (TONE_RULE_VERSION,),
            )
            index_conn.commit()
            count = index_conn.execute("SELECT COUNT(*) FROM library_index").fetchone()[0]

        return self._save_index_status(
            {
                "status": "completed",
                "running": False,
                "processed": len(rows),
                "total": len(rows),
                "indexed": indexed,
                "skipped": skipped,
                "failed": failed,
                "count": count,
                "finished_at": _now(),
                "message": (
                    f"派生索引完成，共 {count} 本"
                    + (
                        "；基调规则升级已全量重算"
                        if rules_changed
                        else ""
                    )
                ),
            }
        )

    async def start_index(self, force: bool = False) -> Dict[str, Any]:
        return self.queue_derived_index_refresh(
            force=force,
            run_tone=True,
            run_plot=False,
            reason="manual_tone_full" if force else "manual_tone_incremental",
        )

    @staticmethod
    def _tone_review_config() -> Dict[str, str]:
        base_url = os.getenv("TONE_REVIEW_BASE_URL", "").strip()
        api_key = os.getenv("TONE_REVIEW_API_KEY", "").strip()
        model = os.getenv("TONE_REVIEW_MODEL", "").strip()
        if base_url and api_key and model:
            return {"base_url": base_url, "api_key": api_key, "model": model}

        config_path = Path(
            os.getenv(
                "OPENCLAW_CONFIG_PATH",
                str(Path.home() / ".openclaw" / "openclaw.json"),
            )
        ).expanduser()
        config = _read_json(config_path, {})
        provider_id = os.getenv(
            "TONE_REVIEW_OPENCLAW_PROVIDER", "tencent-hy-token-plan"
        )
        provider = (
            config.get("models", {}).get("providers", {}).get(provider_id, {})
            if isinstance(config, dict)
            else {}
        )
        models = provider.get("models") or []
        configured_model = (
            str(models[0].get("id") or "")
            if models and isinstance(models[0], dict)
            else ""
        )
        return {
            "base_url": str(provider.get("baseUrl") or "").strip(),
            "api_key": str(provider.get("apiKey") or "").strip(),
            "model": model or configured_model,
        }

    @staticmethod
    def _tone_review_payload(row: Any) -> Dict[str, Any]:
        evidence = _read_json_value(row["tone_evidence"], {})
        return {
            "source_id": str(row["source_id"]),
            "title": str(row["title"] or ""),
            "author": str(row["author"] or ""),
            "category": str(row["category"] or ""),
            "genres": _read_json_value(row["genre_tags"], []),
            "candidates": [
                item.get("name")
                for item in evidence.get("candidates", [])
                if isinstance(item, dict) and item.get("name")
            ][:5],
            "local_primary": _read_json_value(row["primary_tone_tags"], []),
            "local_secondary": _read_json_value(row["secondary_tone_tags"], []),
            "local_confidence": float(row["tone_confidence"] or 0),
            "summary": str(row["summary"] or "")[:350],
            "fragments": [
                str(fragment)[:180]
                for fragment in evidence.get("fragments", [])
                if str(fragment).strip()
            ][:3],
        }

    def _apply_tone_reviews(
        self,
        reviews: List[Dict[str, Any]],
        *,
        model: str,
    ) -> int:
        valid_tags = set(TONE_TAG_CATALOG)
        applied = 0
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            evidence_by_source = self.mysql_catalog.tone_evidence_for_sources(
                str(review.get("source_id") or "")
                for review in reviews
            )
            prepared: List[Dict[str, Any]] = []
            for review in reviews:
                source_id = str(review.get("source_id") or "")
                primary = [
                    str(tag)
                    for tag in review.get("primary_tones", [])
                    if str(tag) in valid_tags
                ][:2]
                secondary = [
                    str(tag)
                    for tag in review.get("secondary_tones", [])
                    if str(tag) in valid_tags and str(tag) not in primary
                ][:3]
                if (
                    not source_id
                    or not primary
                    or source_id not in evidence_by_source
                ):
                    continue
                evidence = _read_json_value(
                    evidence_by_source[source_id],
                    {},
                )
                confidence = round(
                    min(
                        max(
                            float(review.get("confidence") or 0.6),
                            0.45,
                        ),
                        0.99,
                    ),
                    3,
                )
                evidence["model_review"] = {
                    "primary": primary,
                    "secondary": secondary,
                    "confidence": confidence,
                    "reason": str(review.get("reason") or "")[:240],
                    "model": model,
                }
                prepared.append(
                    {
                        "source_id": source_id,
                        "tone_tags": [*primary, *secondary],
                        "primary_tone_tags": primary,
                        "secondary_tone_tags": secondary,
                        "tone_confidence": confidence,
                        "tone_evidence": evidence,
                        "tone_review_model": model,
                        "tone_reviewed_at": _now(),
                    }
                )
            return self.mysql_catalog.apply_tone_review_rows(prepared)
        with _tone_review_write_lock, self._index_connection() as conn:
            for review in reviews:
                source_id = str(review.get("source_id") or "")
                primary = [
                    str(tag)
                    for tag in review.get("primary_tones", [])
                    if str(tag) in valid_tags
                ][:2]
                secondary = [
                    str(tag)
                    for tag in review.get("secondary_tones", [])
                    if str(tag) in valid_tags and str(tag) not in primary
                ][:3]
                if not source_id or not primary:
                    continue
                row = conn.execute(
                    "SELECT tone_evidence FROM library_index WHERE source_id=?",
                    (source_id,),
                ).fetchone()
                if not row:
                    continue
                evidence = _read_json_value(row["tone_evidence"], {})
                evidence["model_review"] = {
                    "primary": primary,
                    "secondary": secondary,
                    "confidence": round(
                        min(max(float(review.get("confidence") or 0.6), 0.45), 0.99),
                        3,
                    ),
                    "reason": str(review.get("reason") or "")[:240],
                    "model": model,
                }
                confidence = evidence["model_review"]["confidence"]
                combined = [*primary, *secondary]
                conn.execute(
                    """
                    UPDATE library_index
                    SET tone_tags=?, primary_tone_tags=?,
                        secondary_tone_tags=?, tone_confidence=?,
                        tone_source='model_review', tone_evidence=?,
                        tone_review_status='reviewed',
                        tone_review_model=?, tone_reviewed_at=?,
                        searchable_text=TRIM(
                            COALESCE(title, '') || ' ' ||
                            COALESCE(category, '') || ' ' ||
                            COALESCE(genre_tags, '') || ' ' || ? || ' ' ||
                            COALESCE(summary, '')
                        )
                    WHERE source_id=?
                    """,
                    (
                        json.dumps(combined, ensure_ascii=False),
                        json.dumps(primary, ensure_ascii=False),
                        json.dumps(secondary, ensure_ascii=False),
                        confidence,
                        json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
                        model,
                        _now(),
                        " ".join(combined),
                        source_id,
                    ),
                )
                applied += 1
            conn.commit()
        return applied

    async def review_pending_tones(
        self,
        *,
        limit: int = 0,
        batch_size: int = 20,
        concurrency: int = 4,
    ) -> Dict[str, Any]:
        config = self._tone_review_config()
        if not all(config.values()):
            return self._save_tone_review_status(
                {
                    "status": "unavailable",
                    "running": False,
                    "message": "模型复核通道未配置；本地题材先验结果仍保持全覆盖",
                }
            )
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            rows = self.mysql_catalog.pending_tone_rows(limit=limit)
        else:
            with self._index_connection() as conn:
                query = """
                    SELECT source_id, title, author, category, summary,
                           genre_tags, primary_tone_tags,
                           secondary_tone_tags, tone_confidence, tone_evidence
                    FROM library_index
                    WHERE tone_review_status='pending'
                    ORDER BY tone_confidence ASC, catalog_id
                """
                params: List[Any] = []
                if limit > 0:
                    query += " LIMIT ?"
                    params.append(int(limit))
                rows = conn.execute(query, params).fetchall()
        total = len(rows)
        self._save_tone_review_status(
            {
                "status": "running",
                "running": True,
                "processed": 0,
                "total": total,
                "reviewed": 0,
                "failed": 0,
                "started_at": _now(),
                "pid": os.getpid(),
                "message": f"正在复核 {total} 本低置信度作品",
            }
        )
        if not rows:
            counts = self._tone_review_counts()
            return self._save_tone_review_status(
                {
                    "status": "completed",
                    "running": False,
                    "finished_at": _now(),
                    **counts,
                    "processed": counts["cumulative_reviewed"],
                    "total": (
                        counts["cumulative_reviewed"] + counts["pending"]
                    ),
                    "reviewed": counts["cumulative_reviewed"],
                    "failed": counts["pending"],
                    "message": "全部低置信度作品均已完成模型复核",
                }
            )

        definitions = {
            tag: TONE_DESCRIPTIONS[tag] for tag in TONE_TAG_CATALOG
        }
        batches = [
            rows[index:index + max(1, min(batch_size, 40))]
            for index in range(0, total, max(1, min(batch_size, 40)))
        ]
        semaphore = asyncio.Semaphore(max(1, min(concurrency, 8)))
        reviewed = failed = processed = 0
        status_lock = asyncio.Lock()

        timeout = aiohttp.ClientTimeout(total=120, connect=20, sock_read=120)
        connector = aiohttp.TCPConnector(limit=max(4, concurrency * 2))
        async with aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            trust_env=False,
        ) as client:
            async def review_batch(batch: List[Any]) -> None:
                nonlocal reviewed, failed, processed
                books = [self._tone_review_payload(row) for row in batch]
                prompt = (
                    "你是网络小说基调分类员。根据书名、分类、摘要、分层代表片段"
                    "和本地候选，只判断整部作品的长期主导阅读体验，不把单个场景"
                    "误当全书基调。每本必须给1-2个主基调，可给0-3个辅助基调；"
                    "标签只能来自给定目录，reason不超过30个汉字。严格返回JSON对象"
                    "{\"items\":[{\"source_id\":\"...\",\"primary_tones\":[],"
                    "\"secondary_tones\":[],\"confidence\":0.0,\"reason\":\"\"}]}。\n"
                    f"基调定义：{json.dumps(definitions, ensure_ascii=False)}\n"
                    f"作品：{json.dumps(books, ensure_ascii=False)}"
                )
                async with semaphore:
                    try:
                        async with client.post(
                            config["base_url"].rstrip("/") + "/chat/completions",
                            headers={
                                "Authorization": f"Bearer {config['api_key']}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": config["model"],
                                "messages": [
                                    {
                                        "role": "system",
                                        "content": "只输出可解析JSON，不输出Markdown。",
                                    },
                                    {"role": "user", "content": prompt},
                                ],
                                "temperature": 0.1,
                                "max_tokens": 3200,
                            },
                        ) as response:
                            if response.status >= 400:
                                raise RuntimeError(
                                    f"模型复核接口返回 {response.status}"
                                )
                            data = await response.json()
                        content = data["choices"][0]["message"]["content"]
                        parsed = self._extract_json_object(str(content)) or {}
                        items = parsed.get("items") or []
                        applied = await asyncio.to_thread(
                            self._apply_tone_reviews,
                            items,
                            model=config["model"],
                        )
                        batch_failed = len(batch) - applied
                    except Exception:
                        applied = 0
                        batch_failed = len(batch)
                async with status_lock:
                    reviewed += applied
                    failed += batch_failed
                    processed += len(batch)
                    self._save_tone_review_status(
                        {
                            "processed": processed,
                            "reviewed": reviewed,
                            "failed": failed,
                            "message": f"模型复核 {processed}/{total}",
                        }
                    )

            await asyncio.gather(*(review_batch(batch) for batch in batches))

        counts = self._tone_review_counts()
        return self._save_tone_review_status(
            {
                "status": "completed" if counts["pending"] == 0 else "partial",
                "running": False,
                "finished_at": _now(),
                **counts,
                "processed": counts["cumulative_reviewed"],
                "total": counts["cumulative_reviewed"] + counts["pending"],
                "reviewed": counts["cumulative_reviewed"],
                "failed": counts["pending"],
                "message": (
                    f"模型复核累计完成 {counts['cumulative_reviewed']} 本，"
                    f"待重试 {counts['pending']} 本"
                ),
            }
        )

    async def start_tone_review(
        self,
        *,
        limit: int = 0,
        batch_size: int = 20,
        concurrency: int = 4,
    ) -> Dict[str, Any]:
        global _tone_review_task
        if _tone_review_task and not _tone_review_task.done():
            return self._load_tone_review_status()

        async def runner() -> None:
            try:
                await self.review_pending_tones(
                    limit=limit,
                    batch_size=batch_size,
                    concurrency=concurrency,
                )
            except Exception as exc:
                self._save_tone_review_status(
                    {
                        "status": "error",
                        "running": False,
                        "finished_at": _now(),
                        "message": str(exc)[:500],
                    }
                )

        _tone_review_task = asyncio.create_task(runner())
        await asyncio.sleep(0)
        return self._load_tone_review_status()

    @staticmethod
    def _segment_text(text: str, center: int, radius: int = 1500) -> tuple[str, str]:
        start = max(0, center - radius)
        end = min(len(text), center + radius)
        if start:
            newline = text.find("\n", start, min(center, start + 300))
            if newline >= 0:
                start = newline + 1
        if end < len(text):
            newline = text.rfind("\n", max(center, end - 300), end)
            if newline >= 0:
                end = newline
        content = re.sub(r"\n{3,}", "\n\n", text[start:end]).strip()
        location = f"{round(start / max(len(text), 1) * 100)}%"
        return content[:3600], location

    @classmethod
    def _extract_plot_segments(cls, path: Path) -> List[Dict[str, Any]]:
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            return []
        segments: List[Dict[str, Any]] = []
        seen_buckets: set[int] = set()

        # 均匀抽取全书结构切片，保证任何题材都有基础召回面。
        for ratio in (0.02, 0.16, 0.32, 0.50, 0.68, 0.84, 0.98):
            center = int(len(text) * ratio)
            content, location = cls._segment_text(text, center)
            bucket = center // 1800
            if len(content) >= 120 and bucket not in seen_buckets:
                seen_buckets.add(bucket)
                segments.append(
                    {"content": content, "location": location, "motif_tags": []}
                )

        # 对常用网文母题扫描全书，仅保留能证明“前态→后态”的邻近证据窗。
        for motif, groups in PLOT_MOTIFS.items():
            pattern_a = re.compile("|".join(re.escape(term) for term in groups["a"]))
            pattern_b = re.compile("|".join(re.escape(term) for term in groups["b"]))
            positions_a = [match.start() for match in pattern_a.finditer(text)]
            positions_b = [match.start() for match in pattern_b.finditer(text)]
            if not positions_a or not positions_b:
                continue
            best_pair: Optional[tuple[int, int]] = None
            best_distance = 20_001
            b_cursor = 0
            for pos_a in positions_a[:240]:
                while b_cursor + 1 < len(positions_b) and positions_b[b_cursor + 1] <= pos_a:
                    b_cursor += 1
                for pos_b in positions_b[max(0, b_cursor - 1) : b_cursor + 3]:
                    distance = abs(pos_a - pos_b)
                    if distance < best_distance:
                        best_distance = distance
                        best_pair = (pos_a, pos_b)
            if not best_pair or best_distance > 20_000:
                continue
            center = sum(best_pair) // 2
            content, location = cls._segment_text(text, center, radius=2200)
            bucket = center // 1800
            if bucket in seen_buckets:
                for segment in segments:
                    if segment["location"] == location:
                        segment["motif_tags"] = sorted(
                            set(segment["motif_tags"]) | {motif}
                        )
                        break
                else:
                    segments.append(
                        {
                            "content": content,
                            "location": location,
                            "motif_tags": [motif],
                        }
                    )
            elif len(content) >= 120:
                seen_buckets.add(bucket)
                segments.append(
                    {
                        "content": content,
                        "location": location,
                        "motif_tags": [motif],
                    }
                )
        return segments[:16]

    def _build_plot_index_mysql(
        self,
        rows: List[Dict[str, Any]],
        *,
        force: bool,
        catalog_total: int,
        baseline: int,
    ) -> Dict[str, Any]:
        if self.mysql_catalog is None:
            raise RuntimeError("MySQL 书目后端未初始化")
        if force:
            self.mysql_catalog.clear_plot_index()
        indexed = failed = 0
        object_root = self.infrastructure_settings.object_root.resolve()
        for candidate_position, row in enumerate(rows, start=1):
            position = baseline + candidate_position
            source_id = str(
                row.get("source_id") or row.get("catalog_id") or row.get("id")
            )
            try:
                source_path = Path(
                    str(row.get("output_path") or row.get("source_path") or "")
                ).expanduser().resolve()
                if not (
                    _is_within(source_path, self.books_root)
                    or _is_within(source_path, object_root)
                ):
                    raise ValueError("来源文件不在电子书库或对象存储范围内")
                stat = source_path.stat()
                segments = self._extract_plot_segments(source_path)
                self.mysql_catalog.replace_plot_book(
                    catalog_id=int(row.get("catalog_id") or row.get("id")),
                    source_id=source_id,
                    source_bytes=stat.st_size,
                    source_mtime_ns=stat.st_mtime_ns,
                    source_sha256=str(row.get("sha256") or ""),
                    plot_rule_version=PLOT_RULE_VERSION,
                    segments=segments,
                    indexed_at=_now(),
                )
                indexed += 1
            except Exception:
                failed += 1
            if candidate_position == len(rows) or candidate_position % 10 == 0:
                self._save_plot_index_status(
                    {
                        "processed": baseline + indexed,
                        "indexed": indexed,
                        "skipped": baseline,
                        "failed": failed,
                        "remaining": max(catalog_total - baseline - indexed, 0),
                        "message": (
                            f"增量处理 {baseline + indexed}/{catalog_total} 本"
                            f"（本轮已检查 {candidate_position}/{len(rows)}）"
                        ),
                    }
                )
        counts = self.mysql_catalog.asset_counts()
        return self._save_plot_index_status(
            {
                "status": "completed_with_errors" if failed else "completed",
                "running": False,
                "processed": max(catalog_total - failed, 0),
                "total": catalog_total,
                "indexed": indexed,
                "skipped": baseline,
                "failed": failed,
                "remaining": failed,
                "books": counts["plot_books"],
                "segments": counts["plot_segments"],
                "finished_at": _now(),
                "message": (
                    "剧情索引完成："
                    f"{counts['plot_books']} 本，"
                    f"{counts['plot_segments']} 个证据片段"
                ),
            }
        )

    def build_plot_index(self, force: bool = False) -> Dict[str, Any]:
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            catalog_total = self.mysql_catalog.derived_index_catalog_total()
            rows = self.mysql_catalog.list_plot_index_candidates(
                rule_version=PLOT_RULE_VERSION,
                force=force,
            )
            baseline = 0 if force else max(catalog_total - len(rows), 0)
            self._save_plot_index_status(
                {
                    "status": "running",
                    "running": True,
                    "processed": baseline,
                    "total": catalog_total,
                    "baseline": baseline,
                    "pending_total": len(rows),
                    "catalog_total": catalog_total,
                    "remaining": len(rows),
                    "indexed": 0,
                    "skipped": baseline,
                    "failed": 0,
                    "checkpoint_scope": f"plot:{PLOT_RULE_VERSION}",
                    "resumed": bool(baseline and len(rows)),
                    "started_at": _now(),
                    "finished_at": None,
                    "pid": os.getpid(),
                    "message": (
                        f"从逐书断点继续，待更新 {len(rows)} 本"
                        if baseline and rows
                        else f"待更新 {len(rows)} 本书籍剧情与剧情索引"
                    ),
                }
            )
            return self._build_plot_index_mysql(
                rows,
                force=force,
                catalog_total=catalog_total,
                baseline=baseline,
            )

        rows = list(self._iter_downloaded_books())
        self._save_plot_index_status(
            {
                "status": "running",
                "running": True,
                "processed": 0,
                "total": len(rows),
                "indexed": 0,
                "skipped": 0,
                "failed": 0,
                "started_at": _now(),
                "finished_at": None,
                "pid": os.getpid(),
                "message": "正在建立本地剧情片段与母题索引",
            }
        )
        indexed = skipped = failed = 0
        seen_source_ids: set[str] = set()
        with self._index_connection() as conn:
            if force:
                conn.execute("DELETE FROM plot_segments")
                conn.execute("DELETE FROM plot_index_meta")
                conn.commit()
            existing = {
                row["source_id"]: (row["source_bytes"], row["source_mtime_ns"])
                for row in conn.execute(
                    "SELECT source_id, source_bytes, source_mtime_ns FROM plot_index_meta"
                )
            }
            for position, row in enumerate(rows, start=1):
                source_id = str(row["source_id"] or row["id"])
                seen_source_ids.add(source_id)
                try:
                    source_path = Path(row["output_path"]).expanduser().resolve()
                    if not _is_within(source_path, self.books_root):
                        raise ValueError("来源文件不在电子书库书籍目录内")
                    stat = source_path.stat()
                    fingerprint = (stat.st_size, stat.st_mtime_ns)
                    if not force and existing.get(source_id) == fingerprint:
                        skipped += 1
                    else:
                        segments = self._extract_plot_segments(source_path)
                        conn.execute(
                            "DELETE FROM plot_segments WHERE source_id=?", (source_id,)
                        )
                        for segment in segments:
                            conn.execute(
                                """
                                INSERT INTO plot_segments (
                                    source_id, catalog_id, title, author, category,
                                    location, motif_tags, content
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    source_id,
                                    row["id"],
                                    row["title"] or source_path.stem,
                                    row["author"] or "",
                                    row["category"] or "",
                                    segment["location"],
                                    json.dumps(
                                        segment["motif_tags"], ensure_ascii=False
                                    ),
                                    segment["content"],
                                ),
                            )
                        conn.execute(
                            """
                            INSERT INTO plot_index_meta (
                                source_id, catalog_id, source_bytes,
                                source_mtime_ns, segment_count, indexed_at
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(source_id) DO UPDATE SET
                                catalog_id=excluded.catalog_id,
                                source_bytes=excluded.source_bytes,
                                source_mtime_ns=excluded.source_mtime_ns,
                                segment_count=excluded.segment_count,
                                indexed_at=excluded.indexed_at
                            """,
                            (
                                source_id,
                                row["id"],
                                stat.st_size,
                                stat.st_mtime_ns,
                                len(segments),
                                _now(),
                            ),
                        )
                        indexed += 1
                    if position % 10 == 0:
                        conn.commit()
                except Exception:
                    failed += 1
                if position == len(rows) or position % 10 == 0:
                    self._save_plot_index_status(
                        {
                            "processed": position,
                            "indexed": indexed,
                            "skipped": skipped,
                            "failed": failed,
                            "message": f"已处理 {position}/{len(rows)} 本",
                        }
                    )

            stale_ids = [
                row["source_id"]
                for row in conn.execute("SELECT source_id FROM plot_index_meta")
                if row["source_id"] not in seen_source_ids
            ]
            for source_id in stale_ids:
                conn.execute("DELETE FROM plot_segments WHERE source_id=?", (source_id,))
                conn.execute("DELETE FROM plot_index_meta WHERE source_id=?", (source_id,))
            conn.commit()
            book_count = conn.execute("SELECT COUNT(*) FROM plot_index_meta").fetchone()[0]
            segment_count = conn.execute("SELECT COUNT(*) FROM plot_segments").fetchone()[0]

        return self._save_plot_index_status(
            {
                "status": "completed",
                "running": False,
                "processed": len(rows),
                "total": len(rows),
                "indexed": indexed,
                "skipped": skipped,
                "failed": failed,
                "books": book_count,
                "segments": segment_count,
                "finished_at": _now(),
                "message": f"剧情索引完成：{book_count} 本，{segment_count} 个证据片段",
            }
        )

    async def start_plot_index(self, force: bool = False) -> Dict[str, Any]:
        return self.queue_derived_index_refresh(
            force=force,
            run_tone=False,
            run_plot=True,
            reason="manual_plot_full" if force else "manual_plot_incremental",
        )

    async def start_ingestion_index_refresh(
        self,
        catalog_ids: Iterable[Any],
        *,
        start_worker: bool = True,
        wait: bool = False,
    ) -> Dict[str, Any]:
        """Queue exact ids in the plot-free lightweight ingestion worker."""

        return self.queue_ingestion_index_refresh(
            catalog_ids,
            start_worker=start_worker,
            wait=wait,
            reason="automatic_ingestion_targeted",
        )

    async def start_combined_index_refresh(
        self,
        catalog_ids: Iterable[Any] = (),
    ) -> Dict[str, Any]:
        """Compatibility alias; automatic callers must provide exact ids."""

        return await self.start_ingestion_index_refresh(catalog_ids)

    @staticmethod
    def project_profile(project_root: Path) -> Dict[str, Any]:
        state = _read_json(project_root / ".webnovel" / "state.json", {})
        info = state.get("project_info", {}) if isinstance(state, dict) else {}
        genre = info.get("genre") or state.get("genre") or ""
        substyle = info.get("substyle") or state.get("substyle") or ""
        tone_tags = info.get("tone_tags") or []
        tone = info.get("tone") or ""
        logline = info.get("logline") or info.get("description") or state.get("description") or ""
        parts = [genre, substyle, tone, logline, " ".join(tone_tags)]
        files_used: List[str] = []
        for relative in PROFILE_FILES:
            path = project_root / relative
            if not path.exists():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")[:12000]
                parts.append(content)
                files_used.append(relative)
            except Exception:
                continue
        profile_text = "\n".join(part for part in parts if part)
        detected_terms = sorted(
            {
                keyword
                for keywords in (*GENRE_KEYWORDS.values(), *TONE_KEYWORDS.values())
                for keyword in keywords
                if keyword in profile_text and keyword not in GENERIC_PROFILE_TERMS
            }
        )
        return {
            "title": info.get("title") or state.get("title") or project_root.name,
            "genre": genre,
            "substyle": substyle,
            "tone": tone,
            "tone_tags": tone_tags,
            "logline": logline,
            "keywords": detected_terms[:40],
            "files_used": files_used,
        }

    @staticmethod
    def _score_book(book: Dict[str, Any], profile: Dict[str, Any]) -> tuple[int, List[str]]:
        score = 0
        reasons: List[str] = []
        genre_tags = set(book.get("genre_tags") or [])
        tone_tags = set(book.get("tone_tags") or [])
        searchable = book.get("searchable_text") or ""
        project_genre = profile.get("genre") or ""
        project_substyle = profile.get("substyle") or ""

        matched_genres = {
            genre
            for genre, keywords in GENRE_KEYWORDS.items()
            if genre in project_genre
            or any(keyword in project_genre for keyword in keywords)
        }
        primary_genres = set(CATEGORY_GENRES.get(book.get("category") or "", ()))
        primary_overlap = matched_genres & primary_genres
        inferred_overlap = (matched_genres & genre_tags) - primary_genres
        if primary_overlap:
            score += min(48, 42 + 6 * len(primary_overlap))
            reasons.append("书库分类题材匹配：" + "、".join(sorted(primary_overlap)))
        elif inferred_overlap:
            score += min(22, 14 + 4 * len(inferred_overlap))
            reasons.append("正文题材特征：" + "、".join(sorted(inferred_overlap)))
        elif project_genre and project_genre in searchable:
            score += 12
            reasons.append(f"题材文本命中：{project_genre}")

        if project_substyle:
            candidates = {
                keyword
                for keywords in GENRE_KEYWORDS.values()
                for keyword in keywords
                if keyword in project_substyle
            }
            candidates.update(
                keyword
                for keyword in re.split(r"[/、·+\s-]+", project_substyle)
                if 2 <= len(keyword) <= 8
            )
            substyle_hits = [keyword for keyword in candidates if keyword in searchable]
            if substyle_hits:
                score += min(18, 8 + len(substyle_hits) * 4)
                reasons.append("子风格命中：" + "、".join(substyle_hits[:3]))

        requested_tones = set(profile.get("tone_tags") or [])
        if profile.get("tone"):
            tone_text = profile["tone"]
            requested_tones.update(
                tone for tone in TONE_KEYWORDS if tone in tone_text
            )
        tone_overlap = requested_tones & tone_tags
        if tone_overlap:
            score += min(28, len(tone_overlap) * 9)
            reasons.append("基调匹配：" + "、".join(sorted(tone_overlap)))

        keyword_hits = [
            keyword for keyword in profile.get("keywords", []) if keyword in searchable
        ]
        if keyword_hits:
            score += min(16, len(keyword_hits) * 2)
            reasons.append("设定关键词：" + "、".join(keyword_hits[:4]))

        if not reasons and book.get("category"):
            reasons.append(f"书库分类：{book['category']}")
        return min(score, 100), reasons

    def _indexed_books(
        self,
        *,
        include_reader_metadata: bool = True,
    ) -> List[Dict[str, Any]]:
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            catalog_rows = [
                self._materialize_mysql_catalog_row(row)
                for row in self.mysql_catalog.list_book_projection()
            ]
            metadata = self.mysql_catalog.metadata_for_ids(
                int(row["catalog_id"]) for row in catalog_rows
            )
            result: List[Dict[str, Any]] = []
            for catalog_item in catalog_rows:
                item = dict(catalog_item)
                feature = metadata.get(int(item["catalog_id"]))
                if feature:
                    item.update(feature)
                    for key in (
                        "genre_tags",
                        "tone_tags",
                        "primary_tone_tags",
                        "secondary_tone_tags",
                    ):
                        item[key] = _read_json_text(item.get(key), [])
                    item["keyword_counts"] = _read_json_value(
                        item.get("keyword_counts"),
                        {},
                    )
                    item["searchable_text"] = " ".join(
                        [
                            str(item.get("title") or ""),
                            str(item.get("category") or ""),
                            *item["genre_tags"],
                            *item["tone_tags"],
                            str(item.get("summary") or ""),
                        ]
                    )
                    item["indexed"] = True
                result.append(item)
            return (
                self._apply_content_metrics(result)
                if include_reader_metadata
                else result
            )
        if not self.index_path.exists():
            return []
        columns = ", ".join(self._index_list_columns())
        with self._index_connection() as conn:
            rows = conn.execute(
                f"SELECT {columns} FROM library_index"
            ).fetchall()
        # 派生索引只保存特征；正文状态与对象位置必须回查当前书目真值。
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            catalog_by_id = {
                int(item["catalog_id"]): self._materialize_mysql_catalog_row(
                    item
                )
                for item in self.mysql_catalog.list_book_projection(
                    include_unavailable=True,
                    catalog_ids=[
                        int(row["catalog_id"])
                        for row in rows
                    ],
                )
            }
        else:
            with self._catalog_connection() as conn:
                catalog_by_id = {
                    int(row["catalog_id"]): dict(row)
                    for row in conn.execute(
                        """
                        SELECT id AS catalog_id,
                               COALESCE(source_id, id) AS source_id,
                               title, author, category,
                               output_path AS source_path,
                               COALESCE(bytes, 0) AS source_bytes,
                               status AS download_status
                        FROM books
                        WHERE status != 'duplicate'
                        """
                    )
                }
        result: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in ("genre_tags", "tone_tags"):
                item[key] = _read_json_text(item.get(key), [])
            item["keyword_counts"] = _read_json_text(item.get("keyword_counts"), {})
            catalog_item = catalog_by_id.get(int(item["catalog_id"]))
            if catalog_item:
                item.update(
                    {
                        "source_id": catalog_item["source_id"],
                        "title": catalog_item["title"],
                        "author": catalog_item["author"],
                        "category": catalog_item["category"],
                        "source_path": catalog_item["source_path"],
                        "source_bytes": catalog_item["source_bytes"],
                        "download_status": catalog_item["download_status"],
                    }
                )
            else:
                item["download_status"] = "missing"
            item["indexed"] = True
            result.append(item)
        return (
            self._apply_content_metrics(result)
            if include_reader_metadata
            else result
        )

    def _catalog_books_fallback(
        self,
        include_unavailable: bool = False,
        *,
        include_reader_metadata: bool = True,
    ) -> List[Dict[str, Any]]:
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            rows = [
                self._materialize_mysql_catalog_row(row)
                for row in self.mysql_catalog.list_book_projection(
                    include_unavailable=include_unavailable
                )
            ]
        else:
            with self._catalog_connection() as conn:
                columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_xinfo(books)")
                }
                book_status_select = (
                    "book_status"
                    if "book_status" in columns
                    else (
                        "CASE WHEN LOWER(COALESCE(source_id, '')) "
                        "LIKE 'fanqie-%' "
                        "OR LOWER(COALESCE(source_id, '')) LIKE 'xbiquge-%' "
                        "OR LOWER(COALESCE(source_id, '')) LIKE 'shubaow-%' "
                        "OR LOWER(COALESCE(source_id, '')) LIKE 'linovelib-%' "
                        "THEN '连载中' ELSE '已完结' END AS book_status"
                    )
                )
                status_clause = (
                    "WHERE status!='duplicate'"
                    if include_unavailable
                    else "WHERE status='done'"
                )
                rows = conn.execute(
                    f"""
                    SELECT id AS catalog_id,
                           COALESCE(source_id, id) AS source_id,
                           title, author, category,
                           output_path AS source_path,
                           COALESCE(bytes, 0) AS source_bytes,
                           status AS download_status,
                           {book_status_select}
                    FROM books {status_clause} ORDER BY id
                    """
                ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["book_status"] = self.normalize_book_status(
                item.get("book_status"),
                default=(
                    SERIALIZATION_STATUS_ONGOING
                    if str(item.get("source_id") or "").lower().startswith(
                        ("fanqie-", "xbiquge-", "shubaow-", "linovelib-")
                    )
                    else SERIALIZATION_STATUS_COMPLETED
                ),
            )
            category_genres = list(CATEGORY_GENRES.get(item.get("category") or "", ()))
            item.update(
                {
                    "genre_tags": category_genres,
                    "tone_tags": [],
                    "summary": "",
                    "searchable_text": " ".join(
                        [
                            item.get("title") or "",
                            item.get("author") or "",
                            item.get("category") or "",
                            *category_genres,
                        ]
                    ),
                    "approx_word_count": max(int(item.get("source_bytes") or 0) // 3, 0),
                    "approx_chapter_count": 0,
                    "indexed": False,
                    "available_for_analysis": item.get("download_status") == "done",
                }
            )
            result.append(item)
        return (
            self._apply_content_metrics(result)
            if include_reader_metadata
            else result
        )

    def _browse_catalog_mysql(
        self,
        *,
        library: str,
        query: str,
        category: str,
        availability: str,
        page: int,
        page_size: int,
    ) -> Dict[str, Any]:
        if self.mysql_catalog is None:
            raise RuntimeError("MySQL catalog is not configured")
        payload = self.mysql_catalog.browse_catalog(
            library=library,
            query=query,
            category=category,
            availability=availability,
            page=page,
            page_size=page_size,
        )
        rows = payload.pop("rows")
        metadata_by_catalog_id: Dict[int, Dict[str, Any]] = {}
        if rows:
            catalog_ids = [int(row["catalog_id"]) for row in rows]
            metadata_by_catalog_id = self.mysql_catalog.metadata_for_ids(
                catalog_ids
            )

        items: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            indexed_metadata = metadata_by_catalog_id.get(
                int(item["catalog_id"]), {}
            )
            item_library = str(item.pop("library_id") or "local")
            object_key = str(item.pop("body_object_key") or "").strip()
            if object_key:
                try:
                    source_path = str(self.object_store.resolve(object_key))
                except ValueError:
                    source_path = ""
            else:
                source_path = str(item.get("source_path") or "")
            item["source_path"] = source_path
            default_book_status = (
                SERIALIZATION_STATUS_ONGOING
                if str(item.get("source_id") or "").lower().startswith(
                    ("fanqie-", "xbiquge-", "shubaow-", "linovelib-")
                )
                else SERIALIZATION_STATUS_COMPLETED
            )
            item["book_status"] = self.normalize_book_status(
                item.get("book_status"),
                default=default_book_status,
            )
            source_exists = bool(
                source_path and Path(source_path).is_file()
            )
            readable = bool(
                item.get("download_status") == "done" and source_exists
            )
            status = str(item.get("download_status") or "")
            published_at = ""
            for source_url in (item.get("detail_url"), item.get("file_url")):
                published_match = re.search(
                    r"/((?:19|20)\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)",
                    str(source_url or ""),
                )
                if published_match:
                    year, month, day = published_match.groups()
                    published_at = (
                        f"{year}-{int(month):02d}-{int(day):02d}"
                    )
                    break
            if readable:
                recovery_reason = ""
            elif status == "done":
                recovery_reason = (
                    "目录已标记完成，但正文对象暂不可访问，请同步书库恢复"
                )
            elif item.get("last_error"):
                last_error = str(item["last_error"]).casefold()
                if "404" in last_error or "link not found" in last_error:
                    recovery_reason = "源站正文链接已失效，等待书库同步恢复"
                elif "timeout" in last_error or "timed out" in last_error:
                    recovery_reason = "源站暂时不可访问，等待下次同步恢复"
                elif "encoding" in last_error or "decode" in last_error:
                    recovery_reason = "正文编码异常，等待重新转换"
                else:
                    recovery_reason = "正文下载异常，等待书库同步恢复"
            elif status == "discovered":
                recovery_reason = "书目已收录，正文等待下载"
            elif status in {"pending", "refresh"}:
                recovery_reason = "正文已进入恢复队列"
            else:
                recovery_reason = "正文下载失败，等待书库同步恢复"
            item.update(
                {
                    "library": item_library,
                    "provider": (
                        "fanqie_desktop_bridge"
                        if item_library == "fanqie"
                        else "local_txt80_catalog"
                    ),
                    "provider_label": (
                        "番茄小说官方下载器"
                        if item_library == "fanqie"
                        else "本地 txt80 镜像"
                    ),
                    "source_exists": source_exists,
                    "available_for_reading": readable,
                    "availability": (
                        "readable" if readable else "recovery"
                    ),
                    "availability_label": (
                        "正文可读" if readable else "待恢复"
                    ),
                    "recovery_reason": recovery_reason,
                    "published_at": published_at,
                    "summary": str(
                        indexed_metadata.get("summary") or ""
                    ).strip(),
                    "word_count": int(
                        item.get("approx_word_count")
                        or 0
                    ),
                    "chapter_count": int(
                        item.get("approx_chapter_count")
                        or 0
                    ),
                    "section_count": int(
                        indexed_metadata.get("section_count") or 0
                    ),
                    "reader_index_status": str(
                        indexed_metadata.get("reader_index_status") or ""
                    ),
                    "approx_word_count": int(
                        item.get("approx_word_count")
                        or max(
                            int(item.get("source_bytes") or 0) // 3,
                            0,
                        )
                    ),
                    "approx_chapter_count": int(
                        item.get("approx_chapter_count") or 0
                    ),
                }
            )
            item["cover_url"] = self._cover_for_catalog_item(item)
            item.pop("cover_object_key", None)
            item.pop("last_error", None)
            items.append(item)

        self._apply_content_metrics(
            items,
            include_latest_chapter=False,
        )
        return {
            "library": library,
            "library_name": (
                "全部书目"
                if library == "all"
                else ("番茄书库" if library == "fanqie" else "本地书库")
            ),
            "items": items,
            **payload,
        }

    def browse_catalog(
        self,
        *,
        library: str,
        query: str = "",
        category: str = "",
        availability: str = "all",
        page: int = 1,
        page_size: int = 24,
        snapshot_id: int = 0,
    ) -> Dict[str, Any]:
        """分页浏览来源目录。

        与基调匹配列表不同，这里直接查询只读 catalog，默认展示所选书库的
        全部唯一书目。筛选、计数和分页都在 SQLite 中完成，避免把三万多条
        记录一次性搬到前端或 Python 内存。
        """
        if library not in {"all", "local", "fanqie"}:
            raise ValueError("书库来源必须是 all、local 或 fanqie")
        if availability not in {"all", "readable", "recovery"}:
            raise ValueError("正文状态必须是 all、readable 或 recovery")

        page = max(int(page), 1)
        page_size = min(max(int(page_size), 1), 60)
        requested_snapshot_id = max(int(snapshot_id or 0), 0)
        query = query.strip()
        category = category.strip()
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            return self._browse_catalog_mysql(
                library=library,
                query=query,
                category=category,
                availability=availability,
                page=page,
                page_size=page_size,
            )
        base_conditions = ["status != 'duplicate'"]
        offset = (page - 1) * page_size

        with self._catalog_connection() as conn:
            if requested_snapshot_id:
                snapshot_id = requested_snapshot_id
            else:
                snapshot_id = int(
                    conn.execute(
                        "SELECT COALESCE(MAX(id), 0) FROM books"
                    ).fetchone()[0]
                )
            if snapshot_id:
                base_conditions.append("id <= ?")
            catalog_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_xinfo(books)")
            }
            catalog_tables = {
                str(row["name"])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            source_expression = self._catalog_effective_library_expression(
                catalog_columns, "books"
            )
            readable_condition = self._catalog_readable_condition(catalog_columns)
            base_params: List[Any] = []
            if snapshot_id:
                base_params.append(snapshot_id)
            if library != "all":
                base_conditions.append(f"{source_expression} = ?")
                base_params.append(library)
            availability_conditions: List[str] = []
            if availability == "readable":
                availability_conditions.append(readable_condition)
            elif availability == "recovery":
                availability_conditions.append(f"NOT ({readable_condition})")

            item_conditions = [*base_conditions, *availability_conditions]
            item_params = [*base_params]
            if category:
                item_conditions.append("COALESCE(category, '未分类') = ?")
                item_params.append(category)
            if query:
                if "catalog_search" in catalog_tables and len(query) >= 3:
                    item_conditions.append(
                        "id IN (SELECT rowid FROM catalog_search "
                        "WHERE catalog_search MATCH ?)"
                    )
                    item_params.append('"' + query.replace('"', '""') + '"')
                else:
                    escaped_query = (
                        query.replace("\\", "\\\\")
                        .replace("%", "\\%")
                        .replace("_", "\\_")
                    )
                    like_query = f"%{escaped_query}%"
                    item_conditions.append(
                        "(COALESCE(title, '') LIKE ? ESCAPE '\\' COLLATE NOCASE "
                        "OR COALESCE(author, '') LIKE ? ESCAPE '\\' COLLATE NOCASE)"
                    )
                    item_params.extend([like_query, like_query])

            where_sql = " AND ".join(item_conditions)
            category_where_sql = " AND ".join(
                [*base_conditions, *availability_conditions]
            )
            category_params = [*base_params]
            last_error_select = (
                "last_error" if "last_error" in catalog_columns
                else "NULL AS last_error"
            )
            discovered_at_select = (
                "discovered_at" if "discovered_at" in catalog_columns
                else "NULL AS discovered_at"
            )
            detail_url_select = (
                "detail_url" if "detail_url" in catalog_columns
                else "NULL AS detail_url"
            )
            file_url_select = (
                "file_url" if "file_url" in catalog_columns
                else "NULL AS file_url"
            )
            book_status_select = (
                "book_status"
                if "book_status" in catalog_columns
                else (
                    "CASE "
                    "WHEN LOWER(COALESCE(source_id, '')) LIKE 'fanqie-%' "
                    "OR LOWER(COALESCE(source_id, '')) LIKE 'xbiquge-%' "
                    "OR LOWER(COALESCE(source_id, '')) LIKE 'shubaow-%' "
                    "OR LOWER(COALESCE(source_id, '')) LIKE 'linovelib-%' "
                    "THEN '连载中' ELSE '已完结' END AS book_status"
                )
            )
            use_facets = (
                "catalog_facets" in catalog_tables
                and "library_id" in catalog_columns
                and "body_available" in catalog_columns
            )
            # Pre-aggregated facets are exact for the connection snapshot on
            # the first page load.  A caller-supplied snapshot freezes later
            # pages while discovery keeps appending, so total must then honor
            # ``id <= snapshot_id`` instead of including newly added rows.
            use_facets_for_total = use_facets and not requested_snapshot_id
            if use_facets:
                facet_conditions = ["1=1"]
                facet_params: List[Any] = []
                if library != "all":
                    facet_conditions.append("library_id=?")
                    facet_params.append(library)
                status_row = conn.execute(
                    f"""
                    SELECT COALESCE(SUM(book_count), 0) AS total,
                           COALESCE(SUM(CASE WHEN body_available=1
                                       THEN book_count ELSE 0 END), 0) AS readable,
                           COALESCE(SUM(CASE WHEN body_available=0
                                       THEN book_count ELSE 0 END), 0) AS recovery
                    FROM catalog_facets
                    WHERE {" AND ".join(facet_conditions)}
                    """,
                    facet_params,
                ).fetchone()
                if availability == "readable":
                    facet_conditions.append("body_available=1")
                elif availability == "recovery":
                    facet_conditions.append("body_available=0")
                categories = [
                    {
                        "name": row["category_name"],
                        "count": int(row["count"]),
                    }
                    for row in conn.execute(
                        f"""
                        SELECT category AS category_name, SUM(book_count) AS count
                        FROM catalog_facets
                        WHERE {" AND ".join(facet_conditions)}
                        GROUP BY category
                        ORDER BY count DESC, category
                        """,
                        facet_params,
                    )
                ]
                if query or not use_facets_for_total:
                    total = int(
                        conn.execute(
                            f"SELECT COUNT(*) FROM books WHERE {where_sql}",
                            item_params,
                        ).fetchone()[0]
                    )
                else:
                    total_conditions = list(facet_conditions)
                    total_params = list(facet_params)
                    if category:
                        total_conditions.append("category=?")
                        total_params.append(category)
                    total = int(
                        conn.execute(
                            f"""
                            SELECT COALESCE(SUM(book_count), 0)
                            FROM catalog_facets
                            WHERE {" AND ".join(total_conditions)}
                            """,
                            total_params,
                        ).fetchone()[0]
                    )
            else:
                total = int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM books WHERE {where_sql}",
                        item_params,
                    ).fetchone()[0]
                )
                status_row = conn.execute(
                    f"""
                    SELECT
                      COUNT(*) AS total,
                      SUM(CASE WHEN {readable_condition} THEN 1 ELSE 0 END) AS readable,
                      SUM(CASE WHEN NOT ({readable_condition}) THEN 1 ELSE 0 END) AS recovery
                    FROM books
                    WHERE {" AND ".join(base_conditions)}
                    """,
                    base_params,
                ).fetchone()
                categories = [
                    {
                        "name": row["category_name"],
                        "count": int(row["count"]),
                    }
                    for row in conn.execute(
                        f"""
                        SELECT COALESCE(category, '未分类') AS category_name,
                               COUNT(*) AS count
                        FROM books
                        WHERE {category_where_sql}
                        GROUP BY category_name
                        ORDER BY count DESC, category_name
                        """,
                        category_params,
                    )
                ]
            order_sql = "id DESC"
            order_params: List[Any] = []
            if query:
                order_sql = (
                    "CASE "
                    "WHEN COALESCE(title, '') = ? COLLATE NOCASE THEN 0 "
                    "WHEN COALESCE(author, '') = ? COLLATE NOCASE THEN 1 "
                    "WHEN COALESCE(title, '') LIKE ? ESCAPE '\\' COLLATE NOCASE THEN 2 "
                    "ELSE 3 END, id DESC"
                )
                escaped_query = (
                    query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                )
                order_params = [query, query, f"{escaped_query}%"]
            rows = conn.execute(
                f"""
                SELECT id AS catalog_id,
                       COALESCE(source_id, id) AS source_id,
                       {source_expression} AS library_id,
                       title, author, COALESCE(category, '未分类') AS category,
                       expected_size, output_path AS source_path,
                       COALESCE(bytes, 0) AS source_bytes,
                       status AS download_status, {last_error_select},
                       {discovered_at_select}, updated_at,
                       {detail_url_select}, {file_url_select},
                       {book_status_select}
                FROM books
                WHERE {where_sql}
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?
                """,
                [*item_params, *order_params, page_size, offset],
            ).fetchall()

        metadata_by_catalog_id: Dict[int, Dict[str, Any]] = {}
        if rows and self.index_path.exists():
            index_uri = f"{self.index_path.as_uri()}?mode=ro"
            with sqlite3.connect(index_uri, uri=True, timeout=15) as index_conn:
                index_conn.row_factory = sqlite3.Row
                placeholders = ",".join("?" for _ in rows)
                catalog_ids = [int(row["catalog_id"]) for row in rows]
                metadata_by_catalog_id = {
                    int(index_row["catalog_id"]): dict(index_row)
                    for index_row in index_conn.execute(
                        f"""
                        SELECT catalog_id, summary,
                               COALESCE(NULLIF(word_count, 0),
                                        approx_word_count, 0) AS word_count,
                               COALESCE(NULLIF(chapter_count, 0),
                                        approx_chapter_count, 0) AS chapter_count,
                               COALESCE(section_count, 0) AS section_count,
                               reader_index_status
                        FROM library_index
                        WHERE catalog_id IN ({placeholders})
                        """,
                        catalog_ids,
                    )
                }

        items: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            indexed_metadata = metadata_by_catalog_id.get(
                int(item["catalog_id"]), {}
            )
            item_library = str(item.pop("library_id") or "local")
            default_book_status = (
                SERIALIZATION_STATUS_ONGOING
                if str(item.get("source_id") or "").lower().startswith(
                    ("fanqie-", "xbiquge-", "shubaow-", "linovelib-")
                )
                else SERIALIZATION_STATUS_COMPLETED
            )
            item["book_status"] = self.normalize_book_status(
                item.get("book_status"),
                default=default_book_status,
            )
            source_path = str(item.get("source_path") or "")
            source_exists = bool(source_path and Path(source_path).is_file())
            readable = bool(
                item.get("download_status") == "done" and source_exists
            )
            status = str(item.get("download_status") or "")
            published_at = ""
            for source_url in (item.get("detail_url"), item.get("file_url")):
                published_match = re.search(
                    r"/((?:19|20)\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)",
                    str(source_url or ""),
                )
                if published_match:
                    year, month, day = published_match.groups()
                    published_at = f"{year}-{int(month):02d}-{int(day):02d}"
                    break
            if readable:
                recovery_reason = ""
            elif status == "done":
                recovery_reason = "目录已标记完成，但正文文件暂不可访问，请同步书库恢复"
            elif item.get("last_error"):
                last_error = str(item["last_error"]).casefold()
                if "404" in last_error or "link not found" in last_error:
                    recovery_reason = "源站正文链接已失效，等待书库同步恢复"
                elif "timeout" in last_error or "timed out" in last_error:
                    recovery_reason = "源站暂时不可访问，等待下次同步恢复"
                elif "encoding" in last_error or "decode" in last_error:
                    recovery_reason = "正文编码异常，等待重新转换"
                else:
                    recovery_reason = "正文下载异常，等待书库同步恢复"
            elif status == "discovered":
                recovery_reason = "书目已收录，正文等待下载"
            elif status in {"pending", "refresh"}:
                recovery_reason = "正文已进入恢复队列"
            else:
                recovery_reason = "正文下载失败，等待书库同步恢复"
            item.update(
                {
                    "library": item_library,
                    "provider": (
                        "fanqie_desktop_bridge"
                        if item_library == "fanqie"
                        else "local_txt80_catalog"
                    ),
                    "provider_label": (
                        "番茄小说官方下载器"
                        if item_library == "fanqie"
                        else "本地 txt80 镜像"
                    ),
                    "source_exists": source_exists,
                    "available_for_reading": readable,
                    "availability": "readable" if readable else "recovery",
                    "availability_label": "正文可读" if readable else "待恢复",
                    "recovery_reason": recovery_reason,
                    "published_at": published_at,
                    "summary": str(indexed_metadata.get("summary") or "").strip(),
                    "word_count": int(indexed_metadata.get("word_count") or 0),
                    "chapter_count": int(
                        indexed_metadata.get("chapter_count") or 0
                    ),
                    "section_count": int(
                        indexed_metadata.get("section_count") or 0
                    ),
                    "reader_index_status": str(
                        indexed_metadata.get("reader_index_status") or ""
                    ),
                    # Compatibility aliases for existing book cards.
                    "approx_word_count": int(
                        indexed_metadata.get("word_count")
                        or max(int(item.get("source_bytes") or 0) // 3, 0)
                    ),
                    "approx_chapter_count": int(
                        indexed_metadata.get("chapter_count") or 0
                    ),
                }
            )
            item["cover_url"] = self._cover_for_catalog_item(item)
            item.pop("last_error", None)
            items.append(item)

        self._apply_content_metrics(
            items,
            include_latest_chapter=False,
        )
        status_counts = {
            "all": int(status_row["total"] or 0),
            "readable": int(status_row["readable"] or 0),
            "recovery": int(status_row["recovery"] or 0),
        }
        return {
            "library": library,
            "library_name": (
                "全部书目"
                if library == "all"
                else ("番茄书库" if library == "fanqie" else "本地书库")
            ),
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "query": query,
            "category": category,
            "availability": availability,
            "snapshot_id": snapshot_id,
            "status_counts": status_counts,
            "categories": categories,
        }

    def move_catalog_books(
        self,
        *,
        catalog_ids: Iterable[int],
        target_library: str,
    ) -> Dict[str, Any]:
        """在本地/番茄书库之间迁移逻辑归属，不改写来源目录真值。"""
        if target_library not in {"local", "fanqie"}:
            raise ValueError("目标书库必须是 local 或 fanqie")
        normalized_ids = sorted(
            {
                int(catalog_id)
                for catalog_id in catalog_ids
                if int(catalog_id) > 0
            }
        )
        if not normalized_ids:
            raise ValueError("请至少选择一本小说")
        if len(normalized_ids) > 500:
            raise ValueError("单次最多移动 500 本小说")

        placeholders = ",".join("?" for _ in normalized_ids)
        moved_at = _now()
        catalog_columns: set[str] = set()
        using_mysql = (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        )
        if using_mysql:
            rows = self.mysql_catalog.move_books(
                catalog_ids=normalized_ids,
                target_library=target_library,
                moved_at=moved_at,
            )
        else:
            with self._catalog_connection() as conn:
                catalog_columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_info(books)")
                }
                library_expression = self._catalog_effective_library_expression(
                    catalog_columns, "books"
                )
                rows = conn.execute(
                    f"""
                    SELECT id AS catalog_id, COALESCE(source_id, id) AS source_id,
                           title, author,
                           {library_expression} AS library_id
                    FROM books
                    WHERE id IN ({placeholders}) AND status != 'duplicate'
                    """,
                    normalized_ids,
                ).fetchall()
        found_ids = {int(row["catalog_id"]) for row in rows}
        missing_ids = [
            catalog_id
            for catalog_id in normalized_ids
            if catalog_id not in found_ids
        ]
        if missing_ids:
            raise ValueError(
                "以下书目不存在或已去重："
                + "、".join(str(item) for item in missing_ids[:20])
            )

        if not using_mysql:
            with self._membership_connection() as conn:
                for row in rows:
                    catalog_id = int(row["catalog_id"])
                    inferred_library = (
                        "fanqie"
                        if str(row["source_id"]).casefold().startswith(
                            "fanqie-"
                        )
                        else "local"
                    )
                    if target_library == inferred_library:
                        conn.execute(
                            """
                            DELETE FROM catalog_library_overrides
                            WHERE catalog_id=?
                            """,
                            (catalog_id,),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO catalog_library_overrides
                                (catalog_id, target_library, moved_at)
                            VALUES (?, ?, ?)
                            ON CONFLICT(catalog_id) DO UPDATE SET
                                target_library=excluded.target_library,
                                moved_at=excluded.moved_at
                            """,
                            (catalog_id, target_library, moved_at),
                        )
                conn.commit()

            if (
                self.infrastructure_settings.catalog_backend == "shadow"
                and self.mysql_catalog is not None
            ):
                self.mysql_catalog.move_books(
                    catalog_ids=normalized_ids,
                    target_library=target_library,
                    moved_at=moved_at,
                )

        # Keep the indexed materialized key in sync when the scalable schema
        # has been installed.  The separate override database remains the
        # backwards-compatible source of truth for legacy catalogs.
        if (
            not using_mysql and "library_id" in catalog_columns
        ):
            with sqlite3.connect(self.catalog_path, timeout=30) as conn:
                conn.executemany(
                    "UPDATE books SET library_id=? WHERE id=?",
                    [
                        (target_library, int(row["catalog_id"]))
                        for row in rows
                    ],
                )
                conn.commit()

        # Keep the indexed materialized key in sync when the scalable schema
        # has been installed.  The separate override database remains the
        # backwards-compatible source of truth for legacy catalogs.
        if "library_id" in catalog_columns:
            with sqlite3.connect(self.catalog_path, timeout=30) as conn:
                conn.executemany(
                    "UPDATE books SET library_id=? WHERE id=?",
                    [
                        (target_library, int(row["catalog_id"]))
                        for row in rows
                    ],
                )
                conn.commit()

        cover_policy_updated = 0
        if not using_mysql and self.cover_index_path.exists():
            with sqlite3.connect(self.cover_index_path, timeout=30) as conn:
                clean_table = conn.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type='table' AND name='clean_cover_jobs'
                    """
                ).fetchone()
                if clean_table:
                    before = conn.total_changes
                    if target_library == "fanqie":
                        conn.execute(
                            f"""
                            UPDATE clean_cover_jobs
                            SET status='not_required_fanqie',
                                last_error=NULL, updated_at=?
                            WHERE catalog_id IN ({placeholders})
                              AND status!='done'
                            """,
                            [moved_at, *normalized_ids],
                        )
                    else:
                        numeric_source_ids = [
                            int(row["catalog_id"])
                            for row in rows
                            if str(row["source_id"]).isdigit()
                        ]
                        if numeric_source_ids:
                            numeric_placeholders = ",".join(
                                "?" for _ in numeric_source_ids
                            )
                            conn.execute(
                                f"""
                                UPDATE clean_cover_jobs
                                SET status='pending', attempts=0,
                                    last_error=NULL, updated_at=?
                                WHERE catalog_id IN ({numeric_placeholders})
                                  AND status='not_required_fanqie'
                                """,
                                [moved_at, *numeric_source_ids],
                            )
                    cover_policy_updated = conn.total_changes - before
                    conn.commit()

        return {
            "target_library": target_library,
            "target_library_name": (
                "番茄书库" if target_library == "fanqie" else "本地书库"
            ),
            "moved_count": len(rows),
            "catalog_ids": normalized_ids,
            "cover_policy_updated": cover_policy_updated,
            "message": (
                f"已将 {len(rows)} 本小说移动到"
                f"{'番茄书库' if target_library == 'fanqie' else '本地书库'}"
            ),
        }

    def _catalog_deconstruction_delete_plan(
        self,
        catalog_ids: set[int],
    ) -> dict[str, Any]:
        """Resolve terminal OOHStory task and artifact records for selected IDs."""

        task_paths: list[Path] = []
        selected_task_ids: set[str] = set()
        selected_outputs: set[Path] = set()
        output_owners: dict[Path, set[int]] = {}
        if self.global_task_root.is_dir():
            for task_path in self.global_task_root.glob("*.json"):
                task = _read_json(task_path, {})
                if not isinstance(task, dict):
                    continue
                try:
                    book_id = int(task.get("book_id") or 0)
                except (TypeError, ValueError):
                    book_id = 0
                output_value = str(task.get("output_dir") or "").strip()
                output = (
                    Path(output_value).expanduser().resolve()
                    if output_value
                    else None
                )
                if output and _is_within(output, self.global_deconstruction_root):
                    output_owners.setdefault(output, set()).add(book_id)
                if book_id not in catalog_ids:
                    continue
                if task.get("status") in {"queued", "running", "dispatching"} or (
                    _pid_is_alive(task.get("pid"))
                    or _pid_is_alive(task.get("codex_pid"))
                ):
                    raise ValueError("所选书目仍有运行中的拆书任务，请先等待或停止任务")
                task_paths.append(task_path)
                task_id = str(task.get("id") or task_path.stem)
                selected_task_ids.add(task_id)
                for suffix in (".log", ".last.md", ".launcher.log"):
                    companion = self.global_task_root / f"{task_id}{suffix}"
                    if companion.exists():
                        task_paths.append(companion)
                if output:
                    selected_outputs.add(output)

        registry = self._read_project_deconstruction_links()
        retained_links: list[dict[str, Any]] = []
        removed_links: list[dict[str, Any]] = []
        for link in registry.get("links") or []:
            try:
                link_catalog_id = int(link.get("catalog_id") or 0)
            except (TypeError, ValueError):
                link_catalog_id = 0
            output_value = str(link.get("global_output_dir") or "").strip()
            if output_value:
                output = Path(output_value).expanduser().resolve()
                if _is_within(output, self.global_deconstruction_root):
                    output_owners.setdefault(output, set()).add(link_catalog_id)
                    if link_catalog_id in catalog_ids:
                        selected_outputs.add(output)
            if link_catalog_id in catalog_ids:
                removed_links.append(dict(link))
            else:
                retained_links.append(dict(link))

        output_paths = [
            output
            for output in selected_outputs
            if output.exists()
            and not (output_owners.get(output, set()) - catalog_ids - {0})
        ]
        batch_updates: list[tuple[Path, dict[str, Any]]] = []
        batch_paths: list[Path] = []
        if self.global_batch_root.is_dir():
            for batch_path in self.global_batch_root.glob("*.json"):
                batch = _read_json(batch_path, {})
                if not isinstance(batch, dict):
                    continue
                book_ids = self._normalized_catalog_ids(batch.get("book_ids") or [])
                if not (set(book_ids) & catalog_ids):
                    continue
                if batch.get("status") in {"queued", "running", "dispatching", "waiting"}:
                    raise ValueError("所选书目仍属于运行中的批量拆书任务")
                batch_paths.append(batch_path)
                retained_book_ids = [value for value in book_ids if value not in catalog_ids]
                if retained_book_ids:
                    updated = dict(batch)
                    updated["book_ids"] = retained_book_ids
                    updated["child_task_ids"] = [
                        value
                        for value in (batch.get("child_task_ids") or [])
                        if str(value) not in selected_task_ids
                    ]
                    updated["total"] = len(retained_book_ids)
                    updated["updated_at"] = _now()
                    batch_updates.append((batch_path, updated))

        control_paths = [*task_paths, *output_paths, *batch_paths]
        if removed_links and self.project_deconstruction_links_path.exists():
            control_paths.append(self.project_deconstruction_links_path)
        return {
            "paths": control_paths,
            "registry_changed": bool(removed_links),
            "registry": {
                **registry,
                "links": retained_links,
                "updated_at": _now(),
            },
            "removed_links": removed_links,
            "batch_updates": batch_updates,
            "task_count": len(selected_task_ids),
            "artifact_count": len(output_paths),
        }

    def delete_catalog_books(
        self,
        *,
        catalog_ids: Iterable[int],
        confirmation: str,
    ) -> Dict[str, Any]:
        """Recoverably delete an explicit bounded MySQL catalog selection."""

        ids, expected_confirmation = normalize_catalog_delete_request(
            catalog_ids,
            confirmation,
        )
        if (
            self.infrastructure_settings.catalog_backend != "mysql"
            or self.mysql_catalog is None
        ):
            raise RuntimeError("批量删除仅允许在 MySQL 真值书库中执行")

        plan = self.mysql_catalog.prepare_book_deletion(ids)
        selected_ids = set(ids)
        deconstruction = self._catalog_deconstruction_delete_plan(selected_ids)
        paths: list[Path] = []
        for object_key in plan.get("exclusive_object_keys") or []:
            try:
                path = self.object_store.resolve(str(object_key))
            except ValueError as exc:
                raise ValueError("书目对象键越过对象存储根目录") from exc
            if not path.exists() and Path(str(object_key)).name == str(object_key):
                legacy_cover = self.object_store.resolve(f"封面/{object_key}")
                if legacy_cover.exists():
                    path = legacy_cover
            paths.append(path)
        for value in plan.get("exclusive_legacy_paths") or []:
            path = Path(str(value)).expanduser().resolve()
            if not (
                _is_within(path, self.books_root)
                or _is_within(path, self.infrastructure_settings.object_root)
            ):
                raise ValueError("书目正文路径越过电子书库范围")
            paths.append(path)

        for book in plan.get("books") or []:
            catalog_id = int(book["catalog_id"])
            reader_index = self._reader_index_path(catalog_id)
            if reader_index.exists():
                paths.append(reader_index)
            source_id = str(book.get("source_id") or "")
            if source_id.startswith("xbiquge-"):
                paths.append(
                    self.xbiquge_provider.chapter_cache_root
                    / source_id.removeprefix("xbiquge-")
                )
            elif source_id.startswith("shubaow-"):
                paths.append(
                    self.shubaow_provider.chapter_cache_root
                    / source_id.removeprefix("shubaow-")
                )
            elif source_id.startswith("linovelib-"):
                source_value = str(book.get("legacy_output_path") or "").strip()
                if source_value:
                    source_path = Path(source_value).expanduser().resolve()
                    if (
                        source_path.name == "总文件.txt"
                        and _is_within(source_path.parent, self.books_root)
                    ):
                        paths.append(source_path.parent)

        paths.extend(deconstruction["paths"])
        archive = CatalogDeletionArchive(
            archive_root=self.library_root / ".deleted-catalog",
            allowed_roots=(
                self.library_root,
                self.infrastructure_settings.object_root,
                self.runtime_dir,
            ),
        )
        manifest_payload = {
            "catalog_ids": ids,
            "confirmation": expected_confirmation,
            "books": plan.get("books") or [],
            "object_assets": plan.get("object_assets") or [],
            "deconstruction": {
                "tasks": deconstruction["task_count"],
                "artifacts": deconstruction["artifact_count"],
                "project_links": len(deconstruction["removed_links"]),
            },
        }
        try:
            archive.stage(paths)
            archive.write_manifest({"status": "staged", **manifest_payload})
            if deconstruction["registry_changed"]:
                _atomic_write_json(
                    self.project_deconstruction_links_path,
                    deconstruction["registry"],
                )
            for batch_path, payload in deconstruction["batch_updates"]:
                _atomic_write_json(batch_path, payload)
            database_result = self.mysql_catalog.delete_books(ids)
        except Exception as exc:
            archive.restore()
            try:
                archive.write_manifest(
                    {
                        "status": "rolled_back",
                        "error": str(exc)[:2000],
                        **manifest_payload,
                    }
                )
            except Exception:
                pass
            raise

        unlinked_project_references = 0
        for link in deconstruction["removed_links"]:
            project_value = str(link.get("project_root") or "").strip()
            link_value = str(link.get("project_link_path") or "").strip()
            output_value = str(link.get("global_output_dir") or "").strip()
            if not (project_value and link_value and output_value):
                continue
            project_root = Path(project_value).expanduser().resolve()
            link_path = Path(link_value).expanduser().absolute()
            expected_link_root = (project_root / "拆文库").resolve()
            if not _is_within(link_path, expected_link_root) or not link_path.is_symlink():
                continue
            try:
                if link_path.resolve() == Path(output_value).expanduser().resolve():
                    link_path.unlink()
                    unlinked_project_references += 1
            except OSError:
                pass
        final_payload = {
            "status": "deleted",
            **manifest_payload,
            "database": database_result,
            "unlinked_project_references": unlinked_project_references,
        }
        try:
            manifest_path = archive.write_manifest(final_payload)
        except OSError:
            manifest_path = archive.batch_root / "manifest.json"
        with self._deconstruction_cache_lock:
            self._deconstruction_status_cache.clear()

        warmed: list[str] = []
        for library in ("all", "local", "fanqie"):
            try:
                self.browse_catalog(library=library, page=1, page_size=28)
                warmed.append(library)
            except Exception:
                pass
        return {
            "status": "deleted",
            "deleted": len(ids),
            "catalog_ids": ids,
            "archive_id": archive.batch_id,
            "archive_manifest": str(manifest_path),
            "related_counts": database_result["related_counts"],
            "archived_assets": len(archive.entries),
            "unlinked_project_references": unlinked_project_references,
            "redis_cache": {
                "invalidated": True,
                "warmed_libraries": warmed,
            },
            "message": f"已删除 {len(ids)} 本小说，关联资产已进入可恢复归档",
        }

    def _asset_connection(self) -> sqlite3.Connection:
        """打开派生索引，并只读挂载 catalog 作为作品真值。"""
        self._require_legacy_sqlite("asset index")
        if not self.index_path.exists():
            raise FileNotFoundError("电子书库派生索引尚未建立")
        index_uri = f"{self.index_path.as_uri()}?mode=ro"
        catalog_uri = f"{self.catalog_path.as_uri()}?mode=ro"
        conn = sqlite3.connect(index_uri, uri=True, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("ATTACH DATABASE ? AS catalog", (catalog_uri,))
        conn.execute("PRAGMA query_only=ON")
        return conn

    @staticmethod
    def _json_list(value: Any) -> List[str]:
        try:
            parsed = json.loads(value or "[]")
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed if str(item).strip()]

    def list_tone_tag_stats(self) -> List[Dict[str, Any]]:
        """返回全局作品基调索引中的真实标签统计，不写入派生索引。"""
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            return self.mysql_catalog.tone_tag_stats()
        if not self.index_path.exists():
            return []
        index_uri = f"{self.index_path.as_uri()}?mode=ro"
        with sqlite3.connect(index_uri, uri=True, timeout=15) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            rows = conn.execute(
                """
                SELECT TRIM(tag.value) AS name,
                       COUNT(DISTINCT li.catalog_id) AS count
                FROM library_index li
                JOIN json_each(COALESCE(li.tone_tags, '[]')) tag
                WHERE NULLIF(TRIM(tag.value), '') IS NOT NULL
                GROUP BY TRIM(tag.value)
                ORDER BY count DESC, name
                """
            ).fetchall()
        return [
            {"name": str(row["name"]), "count": int(row["count"])}
            for row in rows
        ]

    def browse_asset_index(
        self,
        *,
        asset: str,
        query: str = "",
        category: str = "",
        tag: str = "",
        source: str = "all",
        page: int = 1,
        page_size: int = 24,
    ) -> Dict[str, Any]:
        """分页浏览作品级基调/剧情资产，catalog 元数据优先于派生索引。"""
        if asset not in {"tone", "plot"}:
            raise ValueError("索引类型必须是 tone 或 plot")
        if source not in {"all", "local", "fanqie"}:
            raise ValueError("来源必须是 all、local 或 fanqie")
        page = max(int(page), 1)
        page_size = min(max(int(page_size), 1), 60)
        query, category, tag = query.strip(), category.strip(), tag.strip()
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            payload = self.mysql_catalog.browse_assets(
                asset=asset,
                query=query,
                category=category,
                tag=tag,
                source=source,
                page=page,
                page_size=page_size,
            )
            rows = payload.pop("rows")
            items: List[Dict[str, Any]] = []
            for row in rows:
                item = self._materialize_mysql_catalog_row(dict(row))
                item["genre_tags"] = _read_json_text(
                    item.get("genre_tags"), []
                )
                item["tone_tags"] = _read_json_text(
                    item.get("tone_tags"), []
                )
                item["primary_tone_tags"] = _read_json_text(
                    item.get("primary_tone_tags"), []
                )
                item["secondary_tone_tags"] = _read_json_text(
                    item.get("secondary_tone_tags"), []
                )
                item["tone_confidence"] = round(
                    float(item.get("tone_confidence") or 0), 3
                )
                item["tone_evidence"] = _read_json_value(
                    item.get("tone_evidence"), {}
                )
                if asset == "plot":
                    item["indexed_at"] = item.pop(
                        "plot_indexed_at", item.get("indexed_at")
                    )
                item["available_for_reading"] = bool(
                    item.pop("readable", 0)
                )
                item["library"] = str(
                    item.pop("library_id", "local") or "local"
                )
                item["cover_url"] = self._cover_for_catalog_item(item)
                item.pop("body_object_key", None)
                item.pop("cover_object_key", None)
                items.append(item)
            self._apply_content_metrics(
                items,
                include_latest_chapter=False,
            )
            return {
                "asset": asset,
                "items": items,
                "query": query,
                "category": category,
                "tag": tag,
                "source": source,
                **payload,
            }
        offset = (page - 1) * page_size
        source_expression = (
            "CASE WHEN LOWER(COALESCE(b.source_id, '')) LIKE 'fanqie-%' "
            "THEN 'fanqie' ELSE 'local' END"
        )
        readable_condition = (
            "b.status = 'done' AND "
            "NULLIF(TRIM(COALESCE(b.output_path, '')), '') IS NOT NULL"
        )
        from_sql = (
            "library_index li JOIN catalog.books b ON b.id = li.catalog_id"
            if asset == "tone"
            else "plot_index_meta pm JOIN catalog.books b ON b.id = pm.catalog_id "
                 "LEFT JOIN library_index li ON li.catalog_id = pm.catalog_id"
        )
        conditions = ["b.status != 'duplicate'"]
        params: List[Any] = []
        category_scope_conditions = ["b.status != 'duplicate'"]
        category_scope_params: List[Any] = []
        if source != "all":
            conditions.append(f"{source_expression} = ?")
            params.append(source)
            category_scope_conditions.append(f"{source_expression} = ?")
            category_scope_params.append(source)
        if category:
            conditions.append("COALESCE(b.category, '未分类') = ?")
            params.append(category)
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            search_fields = [
                "COALESCE(b.title, '')",
                "COALESCE(b.author, '')",
                "COALESCE(li.summary, '')",
            ]
            if asset == "tone":
                search_fields.extend(
                    ["COALESCE(li.genre_tags, '')", "COALESCE(li.tone_tags, '')"]
                )
            conditions.append(
                "(" + " OR ".join(f"{field} LIKE ? ESCAPE '\\' COLLATE NOCASE" for field in search_fields) + ")"
            )
            params.extend([like] * len(search_fields))
        if tag:
            if asset == "tone":
                conditions.append(
                    "(EXISTS (SELECT 1 FROM json_each(COALESCE(li.genre_tags, '[]')) WHERE value = ?) "
                    "OR EXISTS (SELECT 1 FROM json_each(COALESCE(li.tone_tags, '[]')) WHERE value = ?))"
                )
                params.extend([tag, tag])
            else:
                raise ValueError("剧情母题请在单书证据详情中查看")
        where_sql = " AND ".join(conditions)

        with self._asset_connection() as conn:
            total = int(
                conn.execute(f"SELECT COUNT(*) FROM {from_sql} WHERE {where_sql}", params).fetchone()[0]
            )
            categories = [
                {"name": row["name"], "count": int(row["count"])}
                for row in conn.execute(
                    f"""
                    SELECT COALESCE(b.category, '未分类') AS name, COUNT(*) AS count
                    FROM {from_sql}
                    WHERE {" AND ".join(category_scope_conditions)}
                    GROUP BY name ORDER BY count DESC, name LIMIT 100
                    """,
                    category_scope_params,
                )
            ]
            if asset == "tone":
                rows = conn.execute(
                    f"""
                    SELECT li.source_id, li.catalog_id,
                           COALESCE(b.title, li.title) AS title,
                           COALESCE(b.author, li.author) AS author,
                           COALESCE(b.category, li.category, '未分类') AS category,
                           b.source_id AS catalog_source_id, b.output_path AS source_path,
                           b.status AS download_status, COALESCE(b.bytes, li.source_bytes, 0) AS source_bytes,
                           li.approx_word_count, li.approx_chapter_count, li.summary,
                           li.genre_tags, li.tone_tags, li.primary_tone_tags,
                           li.secondary_tone_tags, li.tone_confidence,
                           li.tone_source, li.tone_evidence,
                           li.tone_review_status, li.tone_review_model,
                           li.tone_reviewed_at, li.indexed_at,
                           CASE WHEN {readable_condition} THEN 1 ELSE 0 END AS readable
                    FROM {from_sql} WHERE {where_sql}
                    ORDER BY li.indexed_at DESC, li.catalog_id DESC
                    LIMIT ? OFFSET ?
                    """,
                    [*params, page_size, offset],
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT pm.source_id, pm.catalog_id,
                           COALESCE(b.title, li.title) AS title,
                           COALESCE(b.author, li.author) AS author,
                           COALESCE(b.category, li.category, '未分类') AS category,
                           b.source_id AS catalog_source_id, b.output_path AS source_path,
                           b.status AS download_status, COALESCE(b.bytes, pm.source_bytes, 0) AS source_bytes,
                           pm.segment_count, pm.indexed_at, li.summary, li.genre_tags, li.tone_tags,
                           li.approx_word_count, li.approx_chapter_count,
                           CASE WHEN {readable_condition} THEN 1 ELSE 0 END AS readable
                    FROM {from_sql} WHERE {where_sql}
                    ORDER BY pm.indexed_at DESC, pm.segment_count DESC, pm.catalog_id DESC
                    LIMIT ? OFFSET ?
                    """,
                    [*params, page_size, offset],
                ).fetchall()

            tag_rows = (
                conn.execute(
                    f"""
                    SELECT name, COUNT(*) AS count
                    FROM (
                      SELECT DISTINCT catalog_id, name FROM (
                        SELECT li.catalog_id, genre.value AS name
                        FROM {from_sql}
                        JOIN json_each(COALESCE(li.genre_tags, '[]')) genre
                        WHERE {" AND ".join(category_scope_conditions)}
                        UNION ALL
                        SELECT li.catalog_id, tone.value AS name
                        FROM {from_sql}
                        JOIN json_each(COALESCE(li.tone_tags, '[]')) tone
                        WHERE {" AND ".join(category_scope_conditions)}
                      )
                    )
                    WHERE NULLIF(TRIM(name), '') IS NOT NULL
                    GROUP BY name ORDER BY count DESC, name LIMIT 100
                    """,
                    [*category_scope_params, *category_scope_params],
                )
                .fetchall()
                if asset == "tone"
                else []
            )

        items = []
        for row in rows:
            item = dict(row)
            item["genre_tags"] = self._json_list(item.get("genre_tags"))
            item["tone_tags"] = self._json_list(item.get("tone_tags"))
            item["primary_tone_tags"] = self._json_list(
                item.get("primary_tone_tags")
            )
            item["secondary_tone_tags"] = self._json_list(
                item.get("secondary_tone_tags")
            )
            item["tone_confidence"] = round(
                float(item.get("tone_confidence") or 0), 3
            )
            item["tone_evidence"] = _read_json_value(
                item.get("tone_evidence"), {}
            )
            item["available_for_reading"] = bool(item.pop("readable", 0)) and Path(
                item.get("source_path") or ""
            ).is_file()
            item["library"] = (
                "fanqie"
                if str(item.get("catalog_source_id") or "").lower().startswith("fanqie-")
                else "local"
            )
            items.append(item)
        self._apply_content_metrics(items)
        return {
            "asset": asset,
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "query": query,
            "category": category,
            "tag": tag,
            "source": source,
            "categories": categories,
            "tags": [{"name": row["name"], "count": int(row["count"])} for row in tag_rows],
        }

    def plot_evidence(
        self,
        catalog_id: int,
        *,
        page: int = 1,
        page_size: int = 8,
    ) -> Dict[str, Any]:
        """按单书限量返回剧情证据，禁止一次性导出全部片段。"""
        page = max(int(page), 1)
        page_size = min(max(int(page_size), 1), 20)
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            payload = self.mysql_catalog.plot_evidence_page(
                catalog_id,
                page=page,
                page_size=page_size,
            )
            rows = payload.pop("rows")
            return {
                **payload,
                "items": [
                    {
                        **dict(row),
                        "motif_tags": _read_json_text(
                            row.get("motif_tags"), []
                        ),
                    }
                    for row in rows
                ],
            }
        offset = (page - 1) * page_size
        with self._asset_connection() as conn:
            book = conn.execute(
                """
                SELECT pm.source_id, pm.catalog_id, pm.segment_count, pm.indexed_at,
                       COALESCE(b.title, li.title) AS title,
                       COALESCE(b.author, li.author) AS author,
                       COALESCE(b.category, li.category, '未分类') AS category
                FROM plot_index_meta pm
                JOIN catalog.books b ON b.id=pm.catalog_id
                LEFT JOIN library_index li ON li.catalog_id=pm.catalog_id
                WHERE pm.catalog_id=?
                """,
                (catalog_id,),
            ).fetchone()
            if not book:
                raise KeyError(f"未找到作品 {catalog_id} 的剧情索引")
            rows = conn.execute(
                """
                SELECT id, location, motif_tags, content
                FROM plot_segments
                WHERE catalog_id=?
                ORDER BY id
                LIMIT ? OFFSET ?
                """,
                (catalog_id, page_size, offset),
            ).fetchall()
        return {
            "book": dict(book),
            "items": [
                {**dict(row), "motif_tags": self._json_list(row["motif_tags"])}
                for row in rows
            ],
            "total": int(book["segment_count"] or 0),
            "page": page,
            "page_size": page_size,
        }

    def list_books(
        self,
        project_root: Path,
        *,
        query: str = "",
        category: str = "",
        min_score: int = 70,
        page: int = 1,
        page_size: int = 24,
    ) -> Dict[str, Any]:
        profile = self.project_profile(project_root)
        effective_min_score = min(100, max(0, int(min_score)))
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            payload = self.mysql_catalog.browse_recommendations(
                profile=profile,
                query=query,
                category=category,
                min_score=effective_min_score,
                page=page,
                page_size=page_size,
            )
            rows = payload.pop("rows")
            deconstruction_by_catalog = self.deconstruction_lookup(
                project_root
            )
            items: List[Dict[str, Any]] = []
            query_lower = query.strip().lower()
            for row in rows:
                item = self._materialize_mysql_catalog_row(dict(row))
                item["genre_tags"] = _read_json_text(
                    item.get("genre_tags"), []
                )
                item["tone_tags"] = _read_json_text(
                    item.get("tone_tags"), []
                )
                item["keyword_counts"] = _read_json_value(
                    item.get("keyword_counts"), {}
                )
                item["searchable_text"] = " ".join(
                    [
                        str(item.get("title") or ""),
                        str(item.get("author") or ""),
                        str(item.get("category") or ""),
                        str(item.get("summary") or ""),
                        *item["genre_tags"],
                        *item["tone_tags"],
                    ]
                )
                _, reasons = self._score_book(item, profile)
                if query_lower:
                    title = str(item.get("title") or "").lower()
                    author = str(item.get("author") or "").lower()
                    if query_lower == title:
                        reasons.insert(0, "书名精确命中")
                    elif query_lower in title:
                        reasons.insert(0, "书名命中")
                    elif query_lower in author:
                        reasons.insert(0, "作者命中")
                    else:
                        reasons.insert(0, "正文/标签关键词命中")
                item["match_score"] = int(item.get("match_score") or 0)
                item["match_reasons"] = reasons
                item["indexed"] = True
                item["source_exists"] = bool(
                    item.get("source_path")
                    and Path(str(item["source_path"])).is_file()
                )
                item["available_for_analysis"] = bool(
                    item.get("download_status") == "done"
                    and item["source_exists"]
                )
                item["cover_url"] = self._cover_for_catalog_item(item)
                deconstruction = deconstruction_by_catalog.get(
                    int(item.get("catalog_id") or 0)
                )
                item["deconstruction"] = (
                    {
                        "id": deconstruction.get("id"),
                        "status": deconstruction.get("status"),
                        "progress": deconstruction.get("progress", 0),
                        "current_stage": deconstruction.get(
                            "current_stage"
                        ),
                        "artifact_level": deconstruction.get(
                            "artifact_level"
                        ),
                        "has_quick_preview": deconstruction.get(
                            "has_quick_preview", False
                        ),
                        "has_full_report": deconstruction.get(
                            "has_full_report", False
                        ),
                        "completed_chapters": deconstruction.get(
                            "completed_chapters", 0
                        ),
                        "total_chapters": deconstruction.get(
                            "total_chapters", 0
                        ),
                        "updated_at": deconstruction.get("updated_at"),
                    }
                    if deconstruction
                    else None
                )
                item.pop("body_object_key", None)
                item.pop("cover_object_key", None)
                items.append(item)
            self._apply_content_metrics(
                items,
                include_latest_chapter=False,
            )
            return {
                "profile": profile,
                "items": items,
                "indexed": True,
                "min_score": effective_min_score,
                **payload,
            }
        # The match score needs the compact feature rows, but chapter JSON,
        # cover checks and source-file stats are needed only for the visible
        # page.  Doing those network reads before pagination turned a 28-book
        # request into a full-library SMB scan.
        indexed_books = self._indexed_books(include_reader_metadata=False)
        deconstruction_by_catalog = self.deconstruction_lookup(project_root)
        query_lower = query.strip().lower()
        if query_lower:
            indexed_by_id = {
                int(item["catalog_id"]): item for item in indexed_books
            }
            books = []
            for catalog_item in self._catalog_books_fallback(
                include_unavailable=True,
                include_reader_metadata=False,
            ):
                item = indexed_by_id.get(int(catalog_item["catalog_id"]), catalog_item)
                item["download_status"] = catalog_item.get("download_status", "done")
                item["available_for_analysis"] = (
                    item["download_status"] == "done"
                    and bool(item.get("source_path"))
                )
                books.append(item)
        else:
            books = indexed_books or self._catalog_books_fallback()
        filtered: List[Dict[str, Any]] = []
        for item in books:
            if category and item.get("category") != category:
                continue
            if query_lower:
                haystack = " ".join(
                    [
                        item.get("title") or "",
                        item.get("author") or "",
                        item.get("category") or "",
                        item.get("searchable_text") or "",
                    ]
                ).lower()
                if query_lower not in haystack:
                    continue
            score, reasons = self._score_book(item, profile)
            if query_lower:
                title = (item.get("title") or "").lower()
                author = (item.get("author") or "").lower()
                if query_lower == title:
                    score = max(score, 100)
                    reasons.insert(0, "书名精确命中")
                elif query_lower in title:
                    score = max(score, 85)
                    reasons.insert(0, "书名命中")
                elif query_lower in author:
                    score = max(score, 70)
                    reasons.insert(0, "作者命中")
                else:
                    score = max(score, 35)
                    reasons.insert(0, "正文/标签关键词命中")
            if score < effective_min_score:
                continue
            item["match_score"] = score
            item["match_reasons"] = reasons
            filtered.append(item)

        filtered.sort(
            key=lambda item: (
                item.get("match_score", 0),
                item.get("source_bytes", 0),
                item.get("catalog_id", 0),
            ),
            reverse=True,
        )
        total = len(filtered)
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        start = (page - 1) * page_size
        items = filtered[start : start + page_size]
        self._apply_content_metrics(
            items,
            include_latest_chapter=False,
        )
        for item in items:
            item["cover_url"] = self._cover_for_catalog_item(item)
            item["source_exists"] = Path(item.get("source_path") or "").is_file()
            item.setdefault("download_status", "done")
            item["available_for_analysis"] = bool(
                item["download_status"] == "done" and item["source_exists"]
            )
            deconstruction = deconstruction_by_catalog.get(
                int(item.get("catalog_id") or 0)
            )
            item["deconstruction"] = (
                {
                    "id": deconstruction.get("id"),
                    "status": deconstruction.get("status"),
                    "progress": deconstruction.get("progress", 0),
                    "current_stage": deconstruction.get("current_stage"),
                    "artifact_level": deconstruction.get("artifact_level"),
                    "has_quick_preview": deconstruction.get(
                        "has_quick_preview", False
                    ),
                    "has_full_report": deconstruction.get(
                        "has_full_report", False
                    ),
                    "completed_chapters": deconstruction.get(
                        "completed_chapters", 0
                    ),
                    "total_chapters": deconstruction.get(
                        "total_chapters", 0
                    ),
                    "updated_at": deconstruction.get("updated_at"),
                }
                if deconstruction
                else None
            )
        return {
            "profile": profile,
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "indexed": bool(indexed_books),
            "min_score": effective_min_score,
        }

    def search_local_catalog(
        self, query: str, *, limit: int = 0
    ) -> List[Dict[str, Any]]:
        """按作品名或作者名检索全局本地目录，不受基调阈值影响。"""
        query = query.strip()
        if len(query) < 2:
            raise ValueError("作品名或作者名至少需要 2 个字符")
        lowered = query.casefold()
        matches: List[Dict[str, Any]] = []
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            source_items = [
                self._materialize_mysql_catalog_row(row)
                for row in self.mysql_catalog.search_book_projection(
                    query,
                    limit=limit,
                )
            ]
        else:
            source_items = self._catalog_books_fallback(
                include_unavailable=True
            )
        for item in source_items:
            title = str(item.get("title") or "")
            author = str(item.get("author") or "")
            title_lower = title.casefold()
            author_lower = author.casefold()
            if lowered not in title_lower and lowered not in author_lower:
                continue
            if lowered == title_lower:
                rank = 400
                reason = "书名精确命中"
            elif lowered == author_lower:
                rank = 350
                reason = "作者精确命中"
            elif title_lower.startswith(lowered):
                rank = 300
                reason = "书名前缀命中"
            elif lowered in title_lower:
                rank = 250
                reason = "书名命中"
            else:
                rank = 200
                reason = "作者命中"
            matches.append(
                {
                    "catalog_id": item["catalog_id"],
                    "source_id": item.get("source_id"),
                    "title": title,
                    "author": author,
                    "category": item.get("category") or "未分类",
                    "download_status": item.get("download_status") or "done",
                    "available_for_analysis": bool(
                        item.get("available_for_analysis")
                        or (
                            item.get("download_status") == "done"
                            and item.get("source_path")
                        )
                    ),
                    "provider": "local_txt80_catalog",
                    "provider_label": "本地 txt80 镜像",
                    "source": "local",
                    "match_reason": reason,
                    "cover_url": self._cover_for_catalog_item(item),
                    "_rank": rank,
                }
            )
        matches.sort(
            key=lambda item: (
                item["_rank"],
                item["available_for_analysis"],
                item["catalog_id"],
            ),
            reverse=True,
        )
        for item in matches:
            item.pop("_rank", None)
        return matches if limit <= 0 else matches[:limit]

    @staticmethod
    def _fetch_json(url: str, *, timeout: int = 30) -> Dict[str, Any]:
        request = Request(
            url,
            headers={
                "User-Agent": "WebnovelWriterAuthorizedLibrary/1.0",
                "Accept": "application/json",
            },
        )
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _public_plain_text_url(formats: Dict[str, Any]) -> str:
        candidates: List[tuple[int, str]] = []
        for content_type, value in (formats or {}).items():
            url = str(value or "")
            if not content_type.startswith("text/plain") or not url:
                continue
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            if (
                parsed.scheme != "https"
                or host not in {"www.gutenberg.org", "gutenberg.org"}
            ):
                continue
            score = 3 if "utf-8" in content_type.lower() else 2
            if url.lower().endswith(".zip"):
                score = 0
            candidates.append((score, url))
        candidates.sort(reverse=True)
        return candidates[0][1] if candidates and candidates[0][0] else ""

    @staticmethod
    def _public_cover_url(formats: Dict[str, Any]) -> str:
        for content_type, value in (formats or {}).items():
            if not str(content_type).lower().startswith("image/"):
                continue
            url = str(value or "").strip()
            parsed = urlparse(url)
            if (
                parsed.scheme == "https"
                and (parsed.hostname or "").lower()
                in {"www.gutenberg.org", "gutenberg.org"}
            ):
                return url
        return ""

    def search_public_catalog(
        self, query: str, *, limit: int = 12
    ) -> List[Dict[str, Any]]:
        """检索 Project Gutenberg 公版目录，仅返回有可验证 TXT 的作品。"""
        query = query.strip()
        if len(query) < 2:
            raise ValueError("作品名或作者名至少需要 2 个字符")
        payload = self._fetch_json(
            f"{GUTENDEX_API}/?search={quote(query)}", timeout=30
        )
        results: List[Dict[str, Any]] = []
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            text_url = self._public_plain_text_url(item.get("formats") or {})
            if not text_url:
                continue
            authors = [
                str(author.get("name") or "")
                for author in (item.get("authors") or [])
                if isinstance(author, dict) and author.get("name")
            ]
            results.append(
                {
                    "provider": PUBLIC_BOOK_PROVIDER,
                    "provider_label": "Project Gutenberg 公版",
                    "remote_id": int(item.get("id") or 0),
                    "title": str(item.get("title") or "未命名作品"),
                    "author": "、".join(authors) or "作者未知",
                    "languages": item.get("languages") or [],
                    "subjects": (item.get("subjects") or [])[:12],
                    "cover_url": self._public_cover_url(
                        item.get("formats") or {}
                    ),
                    "download_count": int(item.get("download_count") or 0),
                    "book_status": SERIALIZATION_STATUS_COMPLETED,
                    "license": "Public Domain / Project Gutenberg",
                    "downloadable": True,
                    "source": "remote_public_domain",
                }
            )
            if limit > 0 and len(results) >= limit:
                break
        return results

    def search_shubaow_catalog(
        self, query: str, *, limit: int = 0
    ) -> List[Dict[str, Any]]:
        """Merge Shubaow's title-only search with the durable author index."""
        remote = self.shubaow_provider.search(query, limit=limit)
        query = query.strip().strip("《》")
        indexed: List[Dict[str, Any]] = []
        try:
            limit_sql = " LIMIT %s" if limit > 0 else ""
            params: List[Any] = [f"%{query}%", f"%{query}%", query, query]
            if limit > 0:
                params.append(int(limit))
            with self.mysql_pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        SELECT id,source_id,detail_url,title,author,category,
                               book_status,cover_object_key
                        FROM books
                        WHERE source_id LIKE 'shubaow-%%'
                          AND (title LIKE %s OR author LIKE %s)
                        ORDER BY (author=%s) DESC,(title=%s) DESC,id DESC
                        {limit_sql}
                        """,
                        params,
                    )
                    rows = [dict(row) for row in cursor.fetchall()]
            for row in rows:
                remote_id = str(row.get("source_id") or "").removeprefix(
                    "shubaow-"
                )
                source_ref = urlparse(str(row.get("detail_url") or "")).path
                try:
                    source_ref = self.shubaow_provider.validate_source_ref(
                        remote_id, source_ref
                    )
                except ValueError:
                    continue
                indexed.append(
                    {
                        "provider": AUTHORIZED_SHUBAOW_PROVIDER,
                        "provider_label": "书宝网全章节",
                        "remote_id": remote_id,
                        "source_ref": source_ref,
                        "title": str(row.get("title") or "未命名作品"),
                        "author": str(row.get("author") or "作者未知"),
                        "category": str(row.get("category") or "未分类"),
                        "extension": "txt",
                        "book_status": str(row.get("book_status") or ""),
                        "license": "站点所有者授权来源",
                        "authorization": "site_owner_authorized",
                        "downloadable": True,
                        "download_mode": "抓取全章节并合并为单本 TXT",
                        "source": "remote_authorized",
                        "local_catalog_id": int(row.get("id") or 0),
                        "cover_url": (
                            f"/api/library/covers/{int(row['id'])}"
                            if row.get("cover_object_key")
                            else ""
                        ),
                    }
                )
        except Exception:
            indexed = []
        merged: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in [*remote, *indexed]:
            remote_id = str(item.get("remote_id") or "")
            if not remote_id or remote_id in seen:
                continue
            seen.add(remote_id)
            merged.append(item)
        return merged if limit <= 0 else merged[:limit]

    async def global_book_search(
        self,
        query: str,
        *,
        limit: int = 0,
        source: str = LOCAL_CATALOG_PROVIDER,
    ) -> Dict[str, Any]:
        query = query.strip()
        per_source_limit = max(int(limit), 0)
        provider_names = {
            LOCAL_CATALOG_PROVIDER: "本地电子书库",
            AUTHORIZED_XBIQUGE_PROVIDER: "新笔趣阁全章节",
            AUTHORIZED_IXDZS_PROVIDER: "爱下电子书",
            AUTHORIZED_SHUBAOW_PROVIDER: "书宝网全章节",
            LINOVELIB_PROVIDER: "哔哩轻小说全章节",
            AUTHORIZED_TXT80_PROVIDER: "txt80.cc 在线",
            FANQIE_DOWNLOADER_PROVIDER: "番茄小说（官方下载器）",
            AUTHORIZED_ZLIBRARY_PROVIDER: "Z-Library（站点所有者授权）",
            PUBLIC_BOOK_PROVIDER: "Project Gutenberg（公版作品）",
        }
        allowed_sources = {*provider_names, "all"}
        if source not in allowed_sources:
            raise ValueError("未知的搜索下载方式")

        local = (
            self.search_local_catalog(query, limit=per_source_limit)
            if source in {"all", LOCAL_CATALOG_PROVIDER}
            else []
        )
        all_provider_jobs = {
            AUTHORIZED_XBIQUGE_PROVIDER: lambda: asyncio.to_thread(
                self.xbiquge_provider.search,
                query,
                limit=per_source_limit,
            ),
            AUTHORIZED_IXDZS_PROVIDER: lambda: asyncio.to_thread(
                self.ixdzs_provider.search,
                query,
                limit=per_source_limit,
            ),
            AUTHORIZED_SHUBAOW_PROVIDER: lambda: asyncio.to_thread(
                self.search_shubaow_catalog,
                query,
                limit=per_source_limit,
            ),
            LINOVELIB_PROVIDER: lambda: asyncio.to_thread(
                self.linovelib_provider.search,
                query,
                limit=per_source_limit,
            ),
            AUTHORIZED_TXT80_PROVIDER: lambda: asyncio.to_thread(
                self.txt80_provider.search,
                query,
                limit=per_source_limit,
            ),
            FANQIE_DOWNLOADER_PROVIDER: lambda: asyncio.to_thread(
                self.fanqie_downloader.search,
                query,
                limit=per_source_limit,
            ),
            AUTHORIZED_ZLIBRARY_PROVIDER: lambda: asyncio.to_thread(
                self.zlibrary_provider.search,
                query,
                limit=per_source_limit,
            ),
            PUBLIC_BOOK_PROVIDER: lambda: asyncio.to_thread(
                self.search_public_catalog,
                query,
                limit=per_source_limit,
            ),
        }
        provider_ids = (
            list(all_provider_jobs)
            if source == "all"
            else ([source] if source in all_provider_jobs else [])
        )
        responses = await asyncio.gather(
            *(all_provider_jobs[provider_id]() for provider_id in provider_ids),
            return_exceptions=True,
        )
        remote: List[Dict[str, Any]] = []
        remote_by_provider: Dict[str, List[Dict[str, Any]]] = {}
        remote_errors: Dict[str, str] = {}
        for provider_id, response in zip(provider_ids, responses):
            if isinstance(response, Exception):
                remote_errors[provider_id] = str(response)[:300]
            else:
                items = list(response)
                for item in items:
                    provider_default = (
                        SERIALIZATION_STATUS_ONGOING
                        if provider_id
                        in {
                            FANQIE_DOWNLOADER_PROVIDER,
                            AUTHORIZED_XBIQUGE_PROVIDER,
                            AUTHORIZED_IXDZS_PROVIDER,
                            AUTHORIZED_SHUBAOW_PROVIDER,
                            LINOVELIB_PROVIDER,
                        }
                        else SERIALIZATION_STATUS_COMPLETED
                    )
                    item["book_status"] = self.normalize_book_status(
                        item.get("book_status"),
                        default=provider_default,
                    )
                remote_by_provider[provider_id] = items
                remote.extend(items)
        remote_error = "；".join(
            f"{provider_names.get(provider_id, provider_id)}：{message}"
            for provider_id, message in remote_errors.items()
        )
        return {
            "query": query,
            "local": local,
            "remote": remote,
            "local_count": len(local),
            "remote_count": len(remote),
            "search_source": source,
            "search_source_label": (
                "全部来源（兼容模式）"
                if source == "all"
                else provider_names[source]
            ),
            "remote_provider": provider_names.get(source, "全部来源"),
            "remote_error": remote_error,
            "remote_errors": remote_errors,
            "remote_by_provider": remote_by_provider,
            "per_source_limit": per_source_limit,
            "unlimited_results": per_source_limit == 0,
        }

    @staticmethod
    def _safe_import_name(value: str, fallback: str, limit: int = 100) -> str:
        text = unicodedata.normalize("NFKC", (value or "").strip())
        text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
        text = re.sub(r"\s+", " ", text).strip(" ._")
        return (text or fallback)[:limit]

    @staticmethod
    def _decode_public_text(raw: bytes) -> str:
        candidates: List[tuple[float, str]] = []
        for encoding in (
            "utf-8-sig",
            "utf-8",
            "gb18030",
            "big5",
            "utf-16",
            "latin-1",
        ):
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                continue
            sample = text[:200000]
            if not sample:
                continue
            printable = sum(
                char.isprintable() or char in "\r\n\t" for char in sample
            ) / len(sample)
            control = sum(
                ord(char) < 32 and char not in "\r\n\t" for char in sample
            ) / len(sample)
            cjk = sum("\u3400" <= char <= "\u9fff" for char in sample) / len(sample)
            mojibake = sum(
                token in sample
                for token in ("Ã", "Â", "¤", "锟斤拷", "\ufffd")
            )
            score = printable * 4 + min(cjk * 8, 3) - control * 20 - mojibake
            if encoding.startswith("utf-8"):
                score += 0.5
            candidates.append((score, text))
        if not candidates:
            raise ValueError("远程文本编码无法识别")
        text = max(candidates, key=lambda item: item[0])[1]
        return (
            text.replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace(IXDZS_PROMOTIONAL_NOTICE, OOHSTORY_EBOOK_NOTICE)
        )

    @staticmethod
    def _epub_to_text(raw: bytes) -> str:
        """Convert an in-memory EPUB to ordered plain text without extracting files."""
        try:
            archive = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise ValueError("远程 EPUB 文件损坏") from exc
        with archive:
            names = set(archive.namelist())
            ordered_names: List[str] = []
            try:
                container = ElementTree.fromstring(
                    archive.read("META-INF/container.xml")
                )
                rootfile = next(
                    node
                    for node in container.iter()
                    if node.tag.endswith("rootfile")
                )
                opf_name = str(rootfile.attrib["full-path"])
                opf = ElementTree.fromstring(archive.read(opf_name))
                manifest = {
                    str(node.attrib.get("id") or ""): str(
                        node.attrib.get("href") or ""
                    )
                    for node in opf.iter()
                    if node.tag.endswith("item")
                    and (
                        "html" in str(node.attrib.get("media-type") or "")
                        or str(node.attrib.get("href") or "").lower().endswith(
                            (".xhtml", ".html", ".htm")
                        )
                    )
                }
                opf_parent = Path(opf_name).parent
                for node in opf.iter():
                    if not node.tag.endswith("itemref"):
                        continue
                    href = manifest.get(str(node.attrib.get("idref") or ""))
                    if not href:
                        continue
                    candidate = (opf_parent / href).as_posix()
                    if candidate in names and candidate not in ordered_names:
                        ordered_names.append(candidate)
            except (KeyError, StopIteration, ElementTree.ParseError):
                ordered_names = []
            if not ordered_names:
                ordered_names = sorted(
                    name
                    for name in names
                    if name.lower().endswith((".xhtml", ".html", ".htm"))
                )
            sections: List[str] = []
            for name in ordered_names:
                page = archive.read(name)
                soup = BeautifulSoup(page, "html.parser")
                for node in soup(["script", "style", "nav", "svg"]):
                    node.decompose()
                text = "\n".join(
                    line.strip()
                    for line in soup.get_text("\n").splitlines()
                    if line.strip()
                )
                if text:
                    sections.append(text)
            content = "\n\n".join(sections).strip()
            if len(content) < 128:
                raise ValueError("远程 EPUB 未提取到有效正文")
            return content

    @classmethod
    def _remote_bytes_to_text(cls, raw: bytes, extension: str) -> str:
        extension = extension.lower().strip(".")
        if extension == "txt":
            return cls._decode_public_text(raw)
        if extension == "epub":
            return cls._epub_to_text(raw)
        raise ValueError("当前仅支持导入 TXT 或 EPUB")

    @staticmethod
    def _download_public_text(url: str) -> str:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or host not in {"www.gutenberg.org", "gutenberg.org"}
        ):
            raise ValueError("远程下载地址不属于已授权书源")
        request = Request(
            url,
            headers={
                "User-Agent": "WebnovelWriterAuthorizedLibrary/1.0",
                "Accept": "text/plain",
            },
        )
        with urlopen(request, timeout=90) as response:
            raw = response.read(50 * 1024 * 1024 + 1)
        if len(raw) > 50 * 1024 * 1024:
            raise ValueError("远程作品超过 50MB 安全上限")
        if len(raw) < 128:
            raise ValueError("远程作品正文为空或过短")
        return ElectronicLibraryService._decode_public_text(raw)

    async def _classify_imported_book(
        self,
        *,
        title: str,
        author: str,
        subjects: List[str],
        sample: str,
        ai_service: Any,
    ) -> tuple[str, bool, str]:
        categories = [
            item["name"]
            for item in self.source_status().get("categories", [])[:80]
            if item.get("name")
        ]
        if "未分类" not in categories:
            categories.append("未分类")
        prompt = f"""请把一部合法导入的电子书归入现有书库分类。

书名：{title}
作者：{author}
来源主题：{'；'.join(subjects[:12]) or '无'}
可选分类：{'、'.join(categories)}
正文开头样本：
{sample[:5000]}

严格只返回 JSON：{{"category":"必须来自可选分类","reason":"一句话理由"}}。
不要根据国籍或语言臆测题材；无法判断时选“未分类”。"""
        try:
            configured_wait = float(
                os.getenv("WEBNOVEL_LIBRARY_AI_WAIT_SECONDS", "300")
            )
        except (TypeError, ValueError):
            configured_wait = 300.0
        wait_seconds = min(max(configured_wait, 1.0), 300.0)
        try:
            reply = await asyncio.wait_for(
                ai_service.chat(
                    [
                        {
                            "role": "system",
                            "content": "你是电子书馆藏分类员，只做保守的题材分类。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.1,
                    max_tokens=300,
                ),
                timeout=wait_seconds,
            )
            parsed = self._extract_json_object(reply) or {}
            category = str(parsed.get("category") or "")
            if category not in categories:
                category = "未分类"
            return category, True, str(parsed.get("reason") or "")[:300]
        except Exception as exc:
            failure = (
                f"AI 分类等待超过 {int(wait_seconds)} 秒，已终止子进程并继续队列"
                if isinstance(exc, asyncio.TimeoutError)
                else str(exc)
            )
            normalized_subjects = {
                str(subject).strip().casefold()
                for subject in subjects
                if str(subject).strip()
            }
            fallback = "未分类"
            for candidate in categories:
                if candidate.strip().casefold() in normalized_subjects:
                    fallback = candidate
                    break
            subject_text = " ".join(subjects).lower()
            fallback_map = (
                ("science fiction", "科幻小说"),
                ("fantasy", "玄幻奇幻"),
                ("detective", "侦探推理"),
                ("history", "历史军事"),
                ("romance", "言情小说"),
            )
            if fallback == "未分类":
                for keyword, candidate in fallback_map:
                    if keyword in subject_text and candidate in categories:
                        fallback = candidate
                        break
            return fallback, False, f"AI 分类不可用，使用保守回退：{failure[:160]}"

    def _insert_imported_catalog_row(
        self,
        *,
        source_id: str,
        title: str,
        author: str,
        category: str,
        detail_url: str,
        file_url: str,
        output_path: Path,
        sha256: str,
        book_status: str = SERIALIZATION_STATUS_COMPLETED,
        catalog_id_override: Optional[int] = None,
        rebind_source: bool = False,
    ) -> int:
        title = normalize_catalog_title(title)
        stamp = _now()
        library_id = (
            "fanqie"
            if source_id.casefold().startswith(
                ("fanqie-", "xbiquge-", "ixdzs-", "shubaow-", "linovelib-")
            )
            else "local"
        )
        canonical_output_path = output_path.expanduser().resolve(strict=True)
        canonical_books_root = self.books_root.resolve(strict=True)
        canonical_library_root = self.library_root.resolve(strict=True)
        if (
            not canonical_output_path.is_file()
            or not _is_within(canonical_output_path, canonical_books_root)
        ):
            raise ValueError("正文必须存放在 txt80/书籍/ 下的分类目录")
        try:
            canonical_body_key = canonical_output_path.relative_to(
                canonical_library_root
            ).as_posix()
        except ValueError as exc:
            raise ValueError("正文路径不在电子书库根目录内") from exc
        if (
            not canonical_body_key.startswith("书籍/")
            or canonical_body_key in {"书籍", "书籍/"}
        ):
            raise ValueError("正文对象键必须直接引用 txt80/书籍/ 分类文件")
        if self.infrastructure_settings.catalog_backend == "mysql":
            if self.mysql_catalog is None:
                raise RuntimeError("MySQL 书目后端未初始化")
            return self.mysql_catalog.mirror_imported_book(
                catalog_id=(
                    int(catalog_id_override)
                    if catalog_id_override
                    else None
                ),
                source_id=source_id,
                detail_url=detail_url,
                title=title,
                author=author,
                category=category,
                file_url=file_url,
                legacy_output_path=str(canonical_output_path),
                body_object_key=canonical_body_key,
                bytes_count=canonical_output_path.stat().st_size,
                sha256=sha256,
                book_status=self.normalize_book_status(book_status),
                library_id=library_id,
                stamp=stamp,
                rebind_source=rebind_source,
            )
        with sqlite3.connect(self.catalog_path, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(books)")
            }
            existing = None
            if "source_id" in columns:
                existing = conn.execute(
                    "SELECT id FROM books WHERE source_id=? LIMIT 1",
                    (source_id,),
                ).fetchone()
            values: Dict[str, Any] = {
                "source_id": source_id,
                "identity_key": self._catalog_identity_key(title, author),
                "title_key": _normalize_book_identity(title),
                "library_id": (
                    "fanqie"
                    if source_id.casefold().startswith(
                        ("fanqie-", "xbiquge-", "ixdzs-", "shubaow-", "linovelib-")
                    )
                    else "local"
                ),
                "detail_url": detail_url,
                "title": title,
                "author": author,
                "category": category,
                "expected_size": f"{output_path.stat().st_size / 1024:.0f} KB",
                "download_page_url": detail_url,
                "file_url": file_url,
                "output_path": str(output_path),
                "status": "done",
                "attempts": 1,
                "next_retry_at": None,
                "download_claimed_at": None,
                "bytes": output_path.stat().st_size,
                "sha256": sha256,
                "last_error": None,
                "discovered_at": stamp,
                "updated_at": stamp,
                "book_status": self.normalize_book_status(book_status),
            }
            usable = {key: value for key, value in values.items() if key in columns}
            if existing:
                assignments = ", ".join(f"{key}=?" for key in usable if key != "source_id")
                update_values = [
                    usable[key] for key in usable if key != "source_id"
                ]
                conn.execute(
                    f"UPDATE books SET {assignments} WHERE id=?",
                    (*update_values, int(existing["id"])),
                )
                catalog_id = int(existing["id"])
            else:
                names = list(usable)
                placeholders = ", ".join("?" for _ in names)
                cursor = conn.execute(
                    f"INSERT INTO books ({', '.join(names)}) VALUES ({placeholders})",
                    [usable[name] for name in names],
                )
                catalog_id = int(cursor.lastrowid)
            conn.commit()
        if self.mysql_catalog is not None:
            self.mysql_catalog.mirror_imported_book(
                catalog_id=catalog_id,
                source_id=source_id,
                detail_url=detail_url,
                title=title,
                author=author,
                category=category,
                file_url=file_url,
                legacy_output_path=str(canonical_output_path),
                body_object_key=canonical_body_key,
                bytes_count=canonical_output_path.stat().st_size,
                sha256=sha256,
                book_status=self.normalize_book_status(book_status),
                library_id=library_id,
                stamp=stamp,
                rebind_source=rebind_source,
            )
        return catalog_id

    @staticmethod
    def _catalog_identity_key(title: Any, author: Any) -> str:
        title_key = _normalize_book_identity(title)
        author_key = _normalize_book_identity(author)
        return f"{title_key}\x1f{author_key}" if title_key else ""

    def register_authorized_catalog_items(
        self,
        items: Iterable[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Register remote catalog metadata without downloading book bodies.

        The public catalog is the source of truth for the "书目总量" metric.
        A remote title therefore enters ``books`` as ``discovered`` as soon as
        it is seen.  Exact normalized title+author matches are mapped to the
        existing catalog row instead of inflating the unique-book total.
        """

        prepared: List[Dict[str, Any]] = []
        invalid = 0
        seen_remote: set[tuple[str, str]] = set()
        for raw in items:
            provider = str(raw.get("provider") or "").strip()
            remote_id = str(raw.get("remote_id") or "").strip()
            source_ref = str(raw.get("source_ref") or "").strip()
            title = normalize_catalog_title(raw.get("title"))
            author = str(raw.get("author") or "").strip() or "作者未知"
            try:
                if provider == AUTHORIZED_XBIQUGE_PROVIDER:
                    source_ref = self.xbiquge_provider.validate_source_ref(
                        remote_id, source_ref
                    )
                    source_id = f"xbiquge-{remote_id}"
                    detail_url = (
                        self.xbiquge_provider.base_url + source_ref
                    )
                    default_book_status = SERIALIZATION_STATUS_ONGOING
                elif provider == AUTHORIZED_IXDZS_PROVIDER:
                    source_ref = self.ixdzs_provider.validate_source_ref(
                        remote_id, source_ref
                    )
                    source_id = f"ixdzs-{remote_id}"
                    detail_url = self.ixdzs_provider.base_url + source_ref
                    default_book_status = SERIALIZATION_STATUS_COMPLETED
                    library_id = "fanqie"
                elif provider == AUTHORIZED_SHUBAOW_PROVIDER:
                    source_ref = self.shubaow_provider.validate_source_ref(
                        remote_id, source_ref
                    )
                    source_id = f"shubaow-{remote_id}"
                    detail_url = self.shubaow_provider.base_url + source_ref
                    default_book_status = SERIALIZATION_STATUS_ONGOING
                    library_id = "fanqie"
                elif provider == LINOVELIB_PROVIDER:
                    source_ref = self.linovelib_provider.validate_source_ref(
                        remote_id, source_ref
                    )
                    source_id = f"linovelib-{remote_id}"
                    detail_url = self.linovelib_provider.base_url + source_ref
                    default_book_status = SERIALIZATION_STATUS_ONGOING
                    library_id = "fanqie"
                elif provider == AUTHORIZED_TXT80_PROVIDER:
                    source_ref = self.txt80_provider.validate_source_ref(
                        remote_id, source_ref
                    )
                    source_id = remote_id
                    detail_url = (
                        self.txt80_provider.base_url
                        + "/"
                        + source_ref.lstrip("/")
                    )
                    default_book_status = SERIALIZATION_STATUS_COMPLETED
                    library_id = "local"
                else:
                    raise ValueError("未知的授权全站目录来源")
                if provider != AUTHORIZED_TXT80_PROVIDER:
                    library_id = "fanqie"
                if not title:
                    raise ValueError("线上书目缺少书名")
            except (TypeError, ValueError):
                invalid += 1
                continue
            remote_key = (provider, remote_id)
            if remote_key in seen_remote:
                continue
            seen_remote.add(remote_key)
            prepared.append(
                {
                    "source_id": source_id,
                    "detail_url": detail_url,
                    "title": title,
                    "author": author,
                    "category": str(raw.get("category") or "未分类").strip()
                    or "未分类",
                    "book_status": self.normalize_book_status(
                        raw.get("book_status"),
                        default=default_book_status,
                    ),
                    "library_id": library_id,
                    "expected_size": str(raw.get("expected_size") or ""),
                    "remote_revision": str(raw.get("remote_revision") or ""),
                    "remote_latest_chapter": str(
                        raw.get("remote_latest_chapter") or ""
                    ),
                    "remote_updated_at": str(raw.get("remote_updated_at") or ""),
                }
            )

        if not prepared:
            return {
                "seen": 0,
                "added": 0,
                "updated": 0,
                "known": 0,
                "duplicates": 0,
                "invalid": invalid,
            }

        stamp = _now()
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            return self.mysql_catalog.register_authorized_items(
                prepared,
                stamp=stamp,
                invalid=invalid,
            )
        added = 0
        updated = 0
        known = 0
        duplicates = 0
        fanqie_catalog_ids: set[int] = set()
        with sqlite3.connect(self.catalog_path, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=30000")
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(books)")
            }
            required_columns = {
                "detail_url": "TEXT",
                "download_page_url": "TEXT",
                "attempts": "INTEGER NOT NULL DEFAULT 0",
                "discovered_at": "TEXT",
                "book_status": "TEXT",
            }
            for column, declaration in required_columns.items():
                if column not in columns:
                    conn.execute(
                        f"ALTER TABLE books ADD COLUMN {column} {declaration}"
                    )
                    columns.add(column)
            source_ids = [item["source_id"] for item in prepared]
            detail_urls = [item["detail_url"] for item in prepared]
            source_placeholders = ", ".join("?" for _ in source_ids)
            detail_placeholders = ", ".join("?" for _ in detail_urls)
            existing_rows = conn.execute(
                f"""
                SELECT id, source_id, detail_url, title, author, category,
                       status
                FROM books
                WHERE source_id IN ({source_placeholders})
                   OR detail_url IN ({detail_placeholders})
                """,
                (*source_ids, *detail_urls),
            ).fetchall()
            by_source_id = {
                str(row["source_id"]): row
                for row in existing_rows
                if row["source_id"]
            }
            by_detail_url = {
                str(row["detail_url"]): row
                for row in existing_rows
                if row["detail_url"]
            }
            prepared_identity_keys = {
                self._catalog_identity_key(item["title"], item["author"])
                for item in prepared
            }
            prepared_identity_keys.discard("")
            identity_keys: set[str] = set()
            if "identity_key" in columns and prepared_identity_keys:
                identity_placeholders = ",".join(
                    "?" for _ in prepared_identity_keys
                )
                identity_keys.update(
                    str(row["identity_key"])
                    for row in conn.execute(
                        f"""
                        SELECT identity_key
                        FROM books
                        WHERE status!='duplicate'
                          AND identity_key IN ({identity_placeholders})
                        """,
                        sorted(prepared_identity_keys),
                    )
                )

            # Legacy schemas and an online backfill can have rows without the
            # persisted key.  Resolve only the incoming titles rather than
            # loading every catalog identity into Python.
            prepared_titles = sorted({item["title"] for item in prepared})
            if prepared_titles:
                title_placeholders = ",".join("?" for _ in prepared_titles)
                legacy_condition = (
                    "(identity_key IS NULL OR identity_key='') AND "
                    if "identity_key" in columns
                    else ""
                )
                identity_keys.update(
                    self._catalog_identity_key(row["title"], row["author"])
                    for row in conn.execute(
                        f"""
                        SELECT title, author
                        FROM books
                        WHERE status!='duplicate'
                          AND {legacy_condition}title IN ({title_placeholders})
                        """,
                        prepared_titles,
                    )
                )
                identity_keys.discard("")

            for item in prepared:
                existing = (
                    by_source_id.get(item["source_id"])
                    or by_detail_url.get(item["detail_url"])
                )
                if existing:
                    catalog_id = int(existing["id"])
                    if existing["status"] != "duplicate":
                        fanqie_catalog_ids.add(catalog_id)
                    if existing["status"] in {"done", "duplicate"}:
                        known += 1
                        continue
                    update_fields: Dict[str, Any] = {
                        "title": item["title"],
                        "author": item["author"],
                        "identity_key": self._catalog_identity_key(
                            item["title"], item["author"]
                        ),
                        "title_key": _normalize_book_identity(item["title"]),
                        "library_id": "fanqie",
                        "category": item["category"],
                        "detail_url": item["detail_url"],
                        "download_page_url": item["detail_url"],
                        "updated_at": stamp,
                        "book_status": item["book_status"],
                    }
                    unchanged = (
                        str(existing["title"] or "") == item["title"]
                        and str(existing["author"] or "") == item["author"]
                        and str(existing["category"] or "") == item["category"]
                        and str(existing["detail_url"] or "") == item["detail_url"]
                    )
                    if unchanged:
                        known += 1
                        continue
                    usable_updates = {
                        key: value
                        for key, value in update_fields.items()
                        if key in columns
                    }
                    assignments = [
                        f"{key}=?" for key in usable_updates
                    ]
                    values: List[Any] = list(usable_updates.values())
                    conn.execute(
                        f"UPDATE books SET {', '.join(assignments)} WHERE id=?",
                        (*values, catalog_id),
                    )
                    updated += 1
                    continue

                identity_key = self._catalog_identity_key(
                    item["title"], item["author"]
                )
                if identity_key in identity_keys:
                    duplicates += 1
                    continue

                values: Dict[str, Any] = {
                    "source_id": item["source_id"],
                    "identity_key": identity_key,
                    "title_key": _normalize_book_identity(item["title"]),
                    "library_id": "fanqie",
                    "detail_url": item["detail_url"],
                    "title": item["title"],
                    "author": item["author"],
                    "category": item["category"],
                    "download_page_url": item["detail_url"],
                    "status": "discovered",
                    "attempts": 0,
                    "discovered_at": stamp,
                    "updated_at": stamp,
                    "book_status": item["book_status"],
                }
                usable = {
                    key: value for key, value in values.items()
                    if key in columns
                }
                names = list(usable)
                cursor = conn.execute(
                    f"""
                    INSERT INTO books ({', '.join(names)})
                    VALUES ({', '.join('?' for _ in names)})
                    """,
                    [usable[name] for name in names],
                )
                catalog_id = int(cursor.lastrowid)
                fanqie_catalog_ids.add(catalog_id)
                added += 1
                identity_keys.add(identity_key)
                inserted = {
                    "id": catalog_id,
                    "source_id": item["source_id"],
                    "detail_url": item["detail_url"],
                    "title": item["title"],
                    "author": item["author"],
                    "category": item["category"],
                    "status": "discovered",
                }
                by_source_id[item["source_id"]] = inserted
                by_detail_url[item["detail_url"]] = inserted
            conn.commit()

        if fanqie_catalog_ids:
            with self._membership_connection() as conn:
                conn.executemany(
                    """
                    INSERT INTO catalog_library_overrides
                        (catalog_id, target_library, moved_at)
                    VALUES (?, 'fanqie', ?)
                    ON CONFLICT(catalog_id) DO UPDATE SET
                        target_library=excluded.target_library,
                        moved_at=excluded.moved_at
                    """,
                    [
                        (catalog_id, stamp)
                        for catalog_id in sorted(fanqie_catalog_ids)
                    ],
                )
                conn.commit()

        result = {
            "seen": len(prepared),
            "added": added,
            "updated": updated,
            "known": known,
            "duplicates": duplicates,
            "invalid": invalid,
        }
        if (
            self.infrastructure_settings.catalog_backend == "shadow"
            and self.mysql_catalog is not None
        ):
            source_ids = [item["source_id"] for item in prepared]
            placeholders = ",".join("?" for _ in source_ids)
            with sqlite3.connect(self.catalog_path, timeout=30) as conn:
                conn.row_factory = sqlite3.Row
                ids_by_source = {
                    str(row["source_id"]): int(row["id"])
                    for row in conn.execute(
                        f"""
                        SELECT id, source_id
                        FROM books
                        WHERE source_id IN ({placeholders})
                        """,
                        source_ids,
                    )
                }
            shadow_items = [
                {
                    **item,
                    "catalog_id": ids_by_source.get(item["source_id"], 0),
                }
                for item in prepared
                if ids_by_source.get(item["source_id"])
            ]
            result["mysql_shadow"] = (
                self.mysql_catalog.register_authorized_items(
                    shadow_items,
                    stamp=stamp,
                    invalid=invalid,
                )
            )
        return result

    def _update_catalog_book_status(
        self,
        catalog_id: int,
        book_status: Any,
        *,
        default: str,
    ) -> str:
        normalized = self.normalize_book_status(book_status, default=default)
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            self.mysql_catalog.update_book_status(
                int(catalog_id),
                normalized,
                stamp=_now(),
            )
            return normalized
        with sqlite3.connect(self.catalog_path, timeout=30) as conn:
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(books)")
            }
            if "book_status" in columns:
                conn.execute(
                    "UPDATE books SET book_status=?, updated_at=? WHERE id=?",
                    (normalized, _now(), int(catalog_id)),
                )
                conn.commit()
        return normalized

    def _existing_done_catalog(self, source_id: str) -> Optional[Dict[str, Any]]:
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            row = self.mysql_catalog.existing_done_source(source_id)
            if not row:
                return None
            item = self._materialize_mysql_catalog_row(row)
            path = Path(str(item.get("source_path") or "")).expanduser()
            if not path.is_file():
                return None
            return {
                **item,
                "id": int(item["catalog_id"]),
            }
        with self._catalog_connection() as conn:
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(books)")
            }
            book_status_select = (
                "book_status"
                if "book_status" in columns
                else "NULL AS book_status"
            )
            row = conn.execute(
                f"""
                SELECT id, source_id, title, author, category, output_path,
                       bytes, sha256, {book_status_select}
                FROM books
                WHERE source_id=? AND status='done'
                LIMIT 1
                """,
                (source_id,),
            ).fetchone()
        if not row or not row["output_path"]:
            return None
        path = Path(str(row["output_path"])).expanduser().resolve()
        if not _is_within(path, self.books_root) or not path.is_file():
            return None
        return dict(row)

    def _refresh_target_output_path(self, catalog_id: int) -> Optional[Path]:
        """Return the existing canonical legacy TXT for an in-place refresh."""

        if int(catalog_id or 0) <= 0 or self.mysql_catalog is None:
            return None
        rows = self.mysql_catalog.list_book_projection(
            catalog_ids=[int(catalog_id)]
        )
        if not rows:
            return None
        item = self._materialize_mysql_catalog_row(rows[0])
        candidate = Path(str(item.get("source_path") or "")).expanduser()
        if not candidate.is_file():
            return None
        resolved = candidate.resolve()
        return resolved if _is_within(resolved, self.books_root) else None

    def _existing_catalog_identity(
        self,
        title: str,
        author: str,
    ) -> Optional[Dict[str, Any]]:
        title_key = _normalize_book_identity(title)
        author_key = _normalize_book_identity(author)
        if not title_key:
            return None
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            row = self.mysql_catalog.existing_done_identity(title, author)
            if not row:
                return None
            item = self._materialize_mysql_catalog_row(row)
            return {
                **item,
                "id": int(item["catalog_id"]),
            }
        with self._catalog_connection() as conn:
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(books)")
            }
            status_select = (
                "book_status" if "book_status" in columns else "NULL AS book_status"
            )
            if "identity_key" in columns:
                rows = conn.execute(
                    f"""
                    SELECT id,source_id,title,author,category,output_path,bytes,
                           sha256,{status_select}
                    FROM books
                    WHERE status='done' AND identity_key=?
                    LIMIT 1
                    """,
                    (self._catalog_identity_key(title, author),),
                ).fetchall()
            elif "title_key" in columns:
                rows = conn.execute(
                    f"""
                    SELECT id,source_id,title,author,category,output_path,bytes,
                           sha256,{status_select}
                    FROM books
                    WHERE status='done' AND title_key=?
                    """,
                    (title_key,),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT id,source_id,title,author,category,output_path,bytes,
                           sha256,{status_select}
                    FROM books
                    WHERE status='done' AND title=?
                    """,
                    (str(title).strip(),),
                ).fetchall()
        for row in rows:
            if (
                _normalize_book_identity(row["title"]) == title_key
                and (
                    not author_key
                    or _normalize_book_identity(row["author"]) == author_key
                )
            ):
                return dict(row)
        return None

    def _rebuild_imported_metadata_tool(self, catalog_id: int) -> Dict[str, Any]:
        tool = (
            APP_ROOT
            / "scripts"
            / "electronic-library"
            / "rebuild_library_metadata.py"
        )
        result = subprocess.run(
            [
                sys.executable,
                str(tool),
                "--book-id",
                str(int(catalog_id)),
                "--library-root",
                str(self.library_root),
                "--runtime-dir",
                str(self.runtime_dir),
                "--force",
                "--strict",
            ],
            cwd=APP_ROOT,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout or "章节元数据整理失败").strip()
            raise RuntimeError(detail[-800:])
        return {
            "status": "rebuilt",
            "tool": "scripts/electronic-library/rebuild_library_metadata.py",
            "catalog_id": int(catalog_id),
        }

    async def import_public_book(
        self,
        *,
        provider: str,
        remote_id: Any,
        source_ref: str = "",
        book_status: str = "",
        category_hint: str = "",
        defer_postprocess: bool = False,
        refresh_existing_catalog_id: int = 0,
        rebind_existing_source: bool = False,
        expected_latest_chapter: str = "",
        ai_service: Any,
    ) -> Dict[str, Any]:
        if provider == PUBLIC_BOOK_PROVIDER:
            try:
                gutenberg_id = int(remote_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("Project Gutenberg 作品 ID 无效") from exc
            return await self._import_gutenberg_book(
                remote_id=gutenberg_id,
                ai_service=ai_service,
            )
        if provider == AUTHORIZED_ZLIBRARY_PROVIDER:
            return await self._import_authorized_zlibrary_book(
                remote_id=remote_id,
                source_ref=source_ref,
                ai_service=ai_service,
            )
        if provider == AUTHORIZED_TXT80_PROVIDER:
            txt80_kwargs = {
                "remote_id": remote_id,
                "source_ref": source_ref,
                "defer_postprocess": defer_postprocess,
                "ai_service": ai_service,
            }
            if refresh_existing_catalog_id:
                txt80_kwargs["refresh_existing_catalog_id"] = int(
                    refresh_existing_catalog_id
                )
            if rebind_existing_source:
                txt80_kwargs["rebind_existing_source"] = True
            return await self._import_authorized_txt80_book(
                **txt80_kwargs
            )
        if provider == AUTHORIZED_XBIQUGE_PROVIDER:
            xbiquge_kwargs = {
                "remote_id": remote_id,
                "source_ref": source_ref,
                "book_status": book_status,
                "category_hint": category_hint,
                "defer_postprocess": defer_postprocess,
                "ai_service": ai_service,
            }
            if refresh_existing_catalog_id:
                xbiquge_kwargs["refresh_existing_catalog_id"] = int(
                    refresh_existing_catalog_id
                )
            if rebind_existing_source:
                xbiquge_kwargs["rebind_existing_source"] = True
            if expected_latest_chapter:
                xbiquge_kwargs["expected_latest_chapter"] = str(
                    expected_latest_chapter
                )
            return await self._import_authorized_xbiquge_book(**xbiquge_kwargs)
        if provider == LINOVELIB_PROVIDER:
            linovelib_kwargs = {
                "remote_id": remote_id,
                "source_ref": source_ref,
                "book_status": book_status,
                "category_hint": category_hint,
                "defer_postprocess": defer_postprocess,
                "ai_service": ai_service,
            }
            if refresh_existing_catalog_id:
                linovelib_kwargs["refresh_existing_catalog_id"] = int(
                    refresh_existing_catalog_id
                )
            if rebind_existing_source:
                linovelib_kwargs["rebind_existing_source"] = True
            return await self._import_linovelib_book(**linovelib_kwargs)
        if provider == AUTHORIZED_SHUBAOW_PROVIDER:
            shubaow_kwargs = {
                "remote_id": remote_id,
                "source_ref": source_ref,
                "book_status": book_status,
                "category_hint": category_hint,
                "defer_postprocess": defer_postprocess,
                "ai_service": ai_service,
            }
            if refresh_existing_catalog_id:
                shubaow_kwargs["refresh_existing_catalog_id"] = int(
                    refresh_existing_catalog_id
                )
            if rebind_existing_source:
                shubaow_kwargs["rebind_existing_source"] = True
            if expected_latest_chapter:
                shubaow_kwargs["expected_latest_chapter"] = str(
                    expected_latest_chapter
                )
            return await self._import_authorized_shubaow_book(**shubaow_kwargs)
        if provider == AUTHORIZED_IXDZS_PROVIDER:
            ixdzs_kwargs = {
                "remote_id": remote_id,
                "source_ref": source_ref,
                "book_status": book_status,
                "category_hint": category_hint,
                "defer_postprocess": defer_postprocess,
                "ai_service": ai_service,
            }
            if refresh_existing_catalog_id:
                ixdzs_kwargs["refresh_existing_catalog_id"] = int(
                    refresh_existing_catalog_id
                )
            if rebind_existing_source:
                ixdzs_kwargs["rebind_existing_source"] = True
            return await self._import_authorized_ixdzs_book(**ixdzs_kwargs)
        if provider == FANQIE_DOWNLOADER_PROVIDER:
            book_id = self.fanqie_downloader.validate_book_id(remote_id)
            existing = self._existing_done_catalog(f"fanqie-{book_id}")
            if existing:
                normalized_book_status = self._update_catalog_book_status(
                    int(existing["id"]),
                    book_status or existing.get("book_status"),
                    default=SERIALIZATION_STATUS_ONGOING,
                )
                cover = await self._sync_fanqie_catalog_cover(
                    catalog_id=int(existing["id"]),
                    book_id=book_id,
                    title=str(existing.get("title") or ""),
                    author=str(existing.get("author") or ""),
                )
                return {
                    "catalog_id": int(existing["id"]),
                    "provider": FANQIE_DOWNLOADER_PROVIDER,
                    "book_id": book_id,
                    "title": existing.get("title") or "",
                    "author": existing.get("author") or "",
                    "category": existing.get("category") or "",
                    "output_path": str(
                        Path(str(existing["output_path"])).relative_to(
                            self.library_root
                        )
                    ),
                    "sha256": existing.get("sha256") or "",
                    "status": "already_available",
                    "deduplicated_before_download": True,
                    "cover_status": cover["status"],
                    "cover_url": cover["local_url"],
                    "book_status": normalized_book_status,
                }
            downloaded = await asyncio.to_thread(
                self.fanqie_downloader.download,
                book_id,
                file_format="txt",
                start_chapter=1,
                end_chapter=None,
            )
            full_download = self._validate_fanqie_full_export(
                Path(str(downloaded["path"]))
            )
            imported = await self.import_fanqie_export(
                book_id=book_id,
                source_path=Path(str(downloaded["path"])),
                title=str(downloaded.get("title") or ""),
                author=str(downloaded.get("author") or ""),
                book_status=book_status,
                ai_service=ai_service,
            )
            return {
                **imported,
                "download_mode": "从第 1 章完整下载至最新/完结章节",
                "chapter_start": 1,
                "chapter_end": "latest",
                "downloaded_chapter_count": full_download["chapter_count"],
                "first_chapter_verified": True,
                "last_chapter_label": full_download["last_chapter_label"],
            }
        raise ValueError("未知或未授权的远程书源")

    async def _import_gutenberg_book(
        self,
        *,
        remote_id: int,
        ai_service: Any,
    ) -> Dict[str, Any]:
        if remote_id <= 0:
            raise ValueError("远程作品 ID 无效")
        detail_url = f"{GUTENDEX_API}/{remote_id}"
        detail = await asyncio.to_thread(self._fetch_json, detail_url)
        text_url = self._public_plain_text_url(detail.get("formats") or {})
        if not text_url:
            raise ValueError("该公版作品没有可用的纯文本版本")
        authors = [
            str(item.get("name") or "")
            for item in (detail.get("authors") or [])
            if isinstance(item, dict) and item.get("name")
        ]
        title = str(detail.get("title") or f"Project Gutenberg {remote_id}")
        author = "、".join(authors) or "作者未知"
        content = await asyncio.to_thread(self._download_public_text, text_url)
        category, ai_classified, classification_reason = (
            await self._classify_imported_book(
                title=title,
                author=author,
                subjects=[
                    str(value) for value in (detail.get("subjects") or [])
                ],
                sample=content,
                ai_service=ai_service,
            )
        )
        filename = (
            f"{self._safe_import_name(title, f'未命名-{remote_id}')}"
            f"__{self._safe_import_name(author, '作者未知', 60)}"
            f"__gutenberg-{remote_id}.txt"
        )
        output_path = self.books_root / self._safe_import_name(
            category, "未分类", 60
        ) / filename
        data = content.encode("utf-8")
        security_scan = await asyncio.to_thread(
            self.download_scanner.scan_bytes,
            data,
            extension="txt",
            source=PUBLIC_BOOK_PROVIDER,
        )
        digest = hashlib.sha256(data).hexdigest()
        with _import_lock:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = output_path.with_suffix(output_path.suffix + ".part")
            temp_path.write_bytes(data)
            temp_path.replace(output_path)
            catalog_id = self._insert_imported_catalog_row(
                source_id=f"gutenberg-{remote_id}",
                title=title,
                author=author,
                category=category,
                detail_url=f"https://www.gutenberg.org/ebooks/{remote_id}",
                file_url=text_url,
                output_path=output_path,
                sha256=digest,
            )
        refresh = await self.start_ingestion_index_refresh([catalog_id])
        cover = await self._sync_remote_catalog_cover(
            catalog_id=catalog_id,
            source_id=f"gutenberg-{remote_id}",
            title=title,
            author=author,
            detail_url=f"https://www.gutenberg.org/ebooks/{remote_id}",
            cover_url=self._public_cover_url(detail.get("formats") or {}),
            allowed_hosts={"www.gutenberg.org", "gutenberg.org"},
        )
        return {
            "catalog_id": catalog_id,
            "provider": PUBLIC_BOOK_PROVIDER,
            "remote_id": remote_id,
            "title": title,
            "author": author,
            "category": category,
            "ai_classified": ai_classified,
            "classification_reason": classification_reason,
            "output_path": str(output_path.relative_to(self.library_root)),
            "sha256": digest,
            "license": "Public Domain / Project Gutenberg",
            "status": "downloaded",
            "cover_status": cover["status"],
            "cover_url": cover["local_url"],
            "security_scan": security_scan,
            "index_refresh": refresh,
        }

    async def _import_authorized_zlibrary_book(
        self,
        *,
        remote_id: Any,
        source_ref: str = "",
        ai_service: Any,
    ) -> Dict[str, Any]:
        detail = await asyncio.to_thread(
            self.zlibrary_provider.detail, remote_id, source_ref
        )
        raw = await asyncio.to_thread(
            self.zlibrary_provider.download, detail
        )
        extension = str(detail.get("extension") or "").lower()
        security_scan = await asyncio.to_thread(
            self.download_scanner.scan_bytes,
            raw,
            extension=extension,
            source=AUTHORIZED_ZLIBRARY_PROVIDER,
        )
        content = await asyncio.to_thread(
            self._remote_bytes_to_text, raw, extension
        )
        title = str(detail.get("title") or f"Z-Library {remote_id}")
        author = str(detail.get("author") or "作者未知")
        categories = [
            str(value) for value in (detail.get("categories") or [])
        ]
        category, ai_classified, classification_reason = (
            await self._classify_imported_book(
                title=title,
                author=author,
                subjects=categories,
                sample=content,
                ai_service=ai_service,
            )
        )
        source_book_id = str(
            detail.get("source_book_id")
            or self.zlibrary_provider.validate_remote_id(remote_id)
        )
        filename = (
            f"{self._safe_import_name(title, f'未命名-{source_book_id}')}"
            f"__{self._safe_import_name(author, '作者未知', 60)}"
            f"__zlibrary-{self._safe_import_name(source_book_id, 'unknown', 40)}.txt"
        )
        output_path = self.books_root / self._safe_import_name(
            category, "未分类", 60
        ) / filename
        data = content.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        with _import_lock:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = output_path.with_suffix(output_path.suffix + ".part")
            temp_path.write_bytes(data)
            temp_path.replace(output_path)
            catalog_id = self._insert_imported_catalog_row(
                source_id=f"zlibrary-{source_book_id}",
                title=title,
                author=author,
                category=category,
                detail_url=str(detail["detail_url"]),
                file_url=urlparse(self.zlibrary_provider.base_url)._replace(
                    path=str(detail["download_path"]),
                    query="",
                    fragment="",
                ).geturl(),
                output_path=output_path,
                sha256=digest,
            )
        refresh = await self.start_ingestion_index_refresh([catalog_id])
        cover_url = str(detail.get("cover_url") or "")
        cover_host = (urlparse(cover_url).hostname or "").lower()
        cover = await self._sync_remote_catalog_cover(
            catalog_id=catalog_id,
            source_id=f"zlibrary-{source_book_id}",
            title=title,
            author=author,
            detail_url=str(detail["detail_url"]),
            cover_url=cover_url,
            allowed_hosts={cover_host, "z-library.im"} - {""},
            allowed_host_suffixes=getattr(
                self.zlibrary_provider, "download_host_suffixes", ()
            ),
        )
        return {
            "catalog_id": catalog_id,
            "provider": AUTHORIZED_ZLIBRARY_PROVIDER,
            "remote_id": str(remote_id),
            "source_book_id": source_book_id,
            "title": title,
            "author": author,
            "category": category,
            "source_extension": extension,
            "ai_classified": ai_classified,
            "classification_reason": classification_reason,
            "output_path": str(output_path.relative_to(self.library_root)),
            "sha256": digest,
            "license": "站点所有者授权测试来源",
            "authorization": "site_owner_authorized",
            "status": "downloaded",
            "cover_status": cover["status"],
            "cover_url": cover["local_url"],
            "security_scan": security_scan,
            "index_refresh": refresh,
        }

    async def _import_authorized_txt80_book(
        self,
        *,
        remote_id: Any,
        source_ref: str,
        defer_postprocess: bool = False,
        refresh_existing_catalog_id: int = 0,
        rebind_existing_source: bool = False,
        ai_service: Any,
    ) -> Dict[str, Any]:
        detail = await asyncio.to_thread(
            self.txt80_provider.detail, remote_id, source_ref
        )
        title = str(detail.get("title") or f"txt80 {remote_id}")
        author = str(detail.get("author") or "作者未知")
        source_book_id = str(detail.get("source_book_id") or remote_id)
        identity_match = self._existing_catalog_identity(title, author)
        if identity_match and not refresh_existing_catalog_id:
            cover = await self._sync_remote_catalog_cover(
                catalog_id=int(identity_match["id"]),
                source_id=source_book_id,
                title=title,
                author=author,
                detail_url=str(detail["detail_url"]),
                cover_url=str(detail.get("cover_url") or ""),
                allowed_hosts={"img.txt80.cc", "www.txt80.cc", "txt80.cc"},
                max_attempts=3,
            )
            return {
                "catalog_id": int(identity_match["id"]),
                "provider": AUTHORIZED_TXT80_PROVIDER,
                "remote_id": str(remote_id),
                "source_book_id": source_book_id,
                "title": title,
                "author": author,
                "status": "already_available",
                "deduplicated_before_download": True,
                "deduplicated_by": "global_title_author",
                "cover_status": cover["status"],
                "cover_url": cover["local_url"],
            }
        raw = await asyncio.to_thread(self.txt80_provider.download, detail)
        security_scan = await asyncio.to_thread(
            self.download_scanner.scan_bytes,
            raw,
            extension="txt",
            source=AUTHORIZED_TXT80_PROVIDER,
        )
        content = await asyncio.to_thread(
            self._remote_bytes_to_text, raw, "txt"
        )
        content = LEGACY_LIBRARY_DOMAIN_RE.sub(
            PUBLIC_DOMAIN,
            content.replace(
                LEGACY_LIBRARY_NOTICE,
                PUBLIC_LIBRARY_NOTICE,
            ),
        ).replace(LEGACY_REBRANDED_NOTICE, PUBLIC_LIBRARY_NOTICE)
        subjects = [
            str(value) for value in (detail.get("categories") or [])
        ]
        category, ai_classified, classification_reason = (
            await self._classify_imported_book(
                title=title,
                author=author,
                subjects=subjects,
                sample=content,
                ai_service=ai_service,
            )
        )
        filename = (
            f"{self._safe_import_name(title, f'未命名-{source_book_id}')}"
            f"__{self._safe_import_name(author, '作者未知', 60)}"
            f"__{self._safe_import_name(source_book_id, 'unknown', 40)}.txt"
        )
        output_path = self.books_root / self._safe_import_name(
            category, "未分类", 60
        ) / filename
        refresh_path = self._refresh_target_output_path(
            refresh_existing_catalog_id
        )
        if refresh_path:
            output_path = refresh_path
        data = content.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        with _import_lock:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = output_path.with_suffix(output_path.suffix + ".part")
            temp_path.write_bytes(data)
            temp_path.replace(output_path)
            catalog_id = self._insert_imported_catalog_row(
                source_id=source_book_id,
                title=title,
                author=author,
                category=category,
                detail_url=str(detail["detail_url"]),
                file_url=str((detail.get("file_urls") or [""])[0]),
                output_path=output_path,
                sha256=digest,
                book_status=str(detail.get("book_status") or "已完结"),
                catalog_id_override=refresh_existing_catalog_id or None,
                rebind_source=rebind_existing_source,
            )
        refresh = await self.start_ingestion_index_refresh(
            [catalog_id],
            start_worker=not defer_postprocess,
        )
        cover = await self._sync_remote_catalog_cover(
            catalog_id=catalog_id,
            source_id=source_book_id,
            title=title,
            author=author,
            detail_url=str(detail["detail_url"]),
            cover_url=str(detail.get("cover_url") or ""),
            allowed_hosts={"img.txt80.cc", "www.txt80.cc", "txt80.cc"},
            max_attempts=3,
        )
        return {
            "catalog_id": catalog_id,
            "provider": AUTHORIZED_TXT80_PROVIDER,
            "remote_id": str(remote_id),
            "source_book_id": source_book_id,
            "title": title,
            "author": author,
            "category": category,
            "source_extension": "txt",
            "ai_classified": ai_classified,
            "classification_reason": classification_reason,
            "output_path": str(output_path.relative_to(self.library_root)),
            "sha256": digest,
            "license": "站点所有者授权测试来源",
            "authorization": "site_owner_authorized",
            "status": "downloaded",
            "cover_status": cover["status"],
            "cover_url": cover["local_url"],
            "security_scan": security_scan,
            "index_refresh": refresh,
            "postprocess_deferred": bool(defer_postprocess),
        }

    async def _import_authorized_xbiquge_book(
        self,
        *,
        remote_id: Any,
        source_ref: str,
        book_status: str = "",
        category_hint: str = "",
        defer_postprocess: bool = False,
        refresh_existing_catalog_id: int = 0,
        rebind_existing_source: bool = False,
        expected_latest_chapter: str = "",
        ai_service: Any,
    ) -> Dict[str, Any]:
        source_ref = self.xbiquge_provider.validate_source_ref(
            remote_id, source_ref
        )
        source_book_id = str(remote_id)
        source_id = f"xbiquge-{source_book_id}"
        existing = self._existing_done_catalog(source_id)
        if existing and not refresh_existing_catalog_id:
            existing_detail = await asyncio.to_thread(
                self.xbiquge_provider.detail,
                remote_id,
                source_ref,
                include_chapters=False,
            )
            cover = await self._sync_remote_catalog_cover(
                catalog_id=int(existing["id"]),
                source_id=source_id,
                title=str(existing.get("title") or ""),
                author=str(existing.get("author") or ""),
                detail_url=str(
                    existing_detail.get("detail_url")
                    or self.xbiquge_provider.base_url + source_ref
                ),
                cover_url=str(existing_detail.get("cover_url") or ""),
                allowed_hosts={"www.xbiquge.info", "xbiquge.info"},
                max_attempts=3,
            )
            normalized_book_status = self._update_catalog_book_status(
                int(existing["id"]),
                book_status or existing.get("book_status"),
                default=SERIALIZATION_STATUS_ONGOING,
            )
            return {
                "catalog_id": int(existing["id"]),
                "provider": AUTHORIZED_XBIQUGE_PROVIDER,
                "source_book_id": source_book_id,
                "title": existing.get("title") or "",
                "author": existing.get("author") or "",
                "category": existing.get("category") or "",
                "output_path": str(
                    Path(str(existing["output_path"])).relative_to(
                        self.library_root
                    )
                ),
                "sha256": existing.get("sha256") or "",
                "status": "already_available",
                "deduplicated_before_download": True,
                "cover_status": cover["status"],
                "cover_url": cover["local_url"],
                "book_status": normalized_book_status,
            }
        detail = await asyncio.to_thread(
            self.xbiquge_provider.detail, remote_id, source_ref
        )
        expected_latest = re.sub(
            r"\s+",
            " ",
            unicodedata.normalize("NFKC", expected_latest_chapter or ""),
        ).strip()
        detail_chapters = list(detail.get("chapters") or [])
        normalized_chapter_titles = {
            re.sub(
                r"\s+",
                " ",
                unicodedata.normalize(
                    "NFKC", str(chapter.get("title") or "")
                ),
            ).strip()
            for chapter in detail_chapters
        }
        actual_latest = re.sub(
            r"\s+",
            " ",
            unicodedata.normalize(
                "NFKC",
                str(detail_chapters[-1].get("title") or "")
                if detail_chapters
                else "",
            ),
        ).strip()
        if expected_latest and expected_latest not in normalized_chapter_titles:
            raise RuntimeError(
                "新笔趣阁更新榜章节尚未出现在完整目录："
                f"目录末章={actual_latest or '空'}；"
                f"更新榜末章={expected_latest}"
            )
        detail_title = str(detail.get("title") or f"新笔趣阁 {remote_id}")
        detail_author = str(detail.get("author") or "作者未知")
        identity_match = self._existing_catalog_identity(
            detail_title, detail_author
        )
        if identity_match and not refresh_existing_catalog_id:
            cover = await self._sync_remote_catalog_cover(
                catalog_id=int(identity_match["id"]),
                source_id=source_id,
                title=detail_title,
                author=detail_author,
                detail_url=str(detail["detail_url"]),
                cover_url=str(detail.get("cover_url") or ""),
                allowed_hosts={"www.xbiquge.info", "xbiquge.info"},
                max_attempts=3,
            )
            return {
                "catalog_id": int(identity_match["id"]),
                "provider": AUTHORIZED_XBIQUGE_PROVIDER,
                "source_book_id": source_book_id,
                "title": detail_title,
                "author": detail_author,
                "status": "already_available",
                "deduplicated_before_download": True,
                "deduplicated_by": "title_author",
                "cover_status": cover["status"],
                "cover_url": cover["local_url"],
            }
        raw = await asyncio.to_thread(self.xbiquge_provider.download, detail)
        security_scan = await asyncio.to_thread(
            self.download_scanner.scan_bytes,
            raw,
            extension="txt",
            source=AUTHORIZED_XBIQUGE_PROVIDER,
        )
        content = await asyncio.to_thread(
            self._remote_bytes_to_text, raw, "txt"
        )
        title = detail_title
        author = detail_author
        subjects = [
            str(value) for value in (detail.get("categories") or [])
        ]
        normalized_hint = str(category_hint or "").strip()
        if normalized_hint and normalized_hint != "未分类":
            category = normalized_hint
            ai_classified = False
            classification_reason = "沿用授权源站目录的标准分类"
        else:
            category, ai_classified, classification_reason = (
                await self._classify_imported_book(
                    title=title,
                    author=author,
                    subjects=subjects,
                    sample=content,
                    ai_service=ai_service,
                )
            )
        filename = (
            f"{self._safe_import_name(title, f'未命名-{source_book_id}')}"
            f"__{self._safe_import_name(author, '作者未知', 60)}"
            f"__{source_id}.txt"
        )
        output_path = self.books_root / self._safe_import_name(
            category, "未分类", 60
        ) / filename
        refresh_path = self._refresh_target_output_path(
            refresh_existing_catalog_id
        )
        if refresh_path:
            output_path = refresh_path
        data = content.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        with _import_lock:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = output_path.with_suffix(output_path.suffix + ".part")
            temp_path.write_bytes(data)
            temp_path.replace(output_path)
            catalog_id = self._insert_imported_catalog_row(
                source_id=source_id,
                title=title,
                author=author,
                category=category,
                detail_url=str(detail["detail_url"]),
                file_url=str(detail["detail_url"]),
                output_path=output_path,
                sha256=digest,
                book_status=str(detail.get("book_status") or book_status),
                catalog_id_override=refresh_existing_catalog_id or None,
                rebind_source=rebind_existing_source,
            )
        self.move_catalog_books(
            catalog_ids=[catalog_id],
            target_library="fanqie",
        )
        if defer_postprocess:
            metadata_rebuild = {"status": "deferred", "catalog_id": catalog_id}
        else:
            metadata_rebuild = await asyncio.to_thread(
                self._rebuild_imported_metadata_tool,
                catalog_id,
            )
        refresh = await self.start_ingestion_index_refresh(
            [catalog_id],
            start_worker=not defer_postprocess,
        )
        cover = await self._sync_remote_catalog_cover(
            catalog_id=catalog_id,
            source_id=source_id,
            title=title,
            author=author,
            detail_url=str(detail["detail_url"]),
            cover_url=str(detail.get("cover_url") or ""),
            allowed_hosts={"www.xbiquge.info", "xbiquge.info"},
            max_attempts=3,
        )
        return {
            "catalog_id": catalog_id,
            "provider": AUTHORIZED_XBIQUGE_PROVIDER,
            "remote_id": source_book_id,
            "source_book_id": source_book_id,
            "title": title,
            "author": author,
            "category": category,
            "source_extension": "txt",
            "chapter_count": int(detail.get("chapter_count") or 0),
            "latest_chapter": actual_latest,
            "download_mode": "抓取全章节并合并为单本 TXT",
            "ai_classified": ai_classified,
            "classification_reason": classification_reason,
            "output_path": str(output_path.relative_to(self.library_root)),
            "sha256": digest,
            "license": "站点所有者授权来源",
            "authorization": "site_owner_authorized",
            "status": "downloaded",
            "cover_status": cover["status"],
            "cover_url": cover["local_url"],
            "book_status": self.normalize_book_status(
                detail.get("book_status") or book_status,
                default=SERIALIZATION_STATUS_ONGOING,
            ),
            "security_scan": security_scan,
            "metadata_rebuild": metadata_rebuild,
            "index_refresh": refresh,
            "postprocess_deferred": bool(defer_postprocess),
        }

    async def _import_linovelib_book(
        self,
        *,
        remote_id: Any,
        source_ref: str,
        book_status: str = "",
        category_hint: str = "",
        defer_postprocess: bool = False,
        refresh_existing_catalog_id: int = 0,
        rebind_existing_source: bool = False,
        ai_service: Any,
    ) -> Dict[str, Any]:
        del ai_service
        source_ref = self.linovelib_provider.validate_source_ref(
            remote_id, source_ref
        )
        source_book_id = str(remote_id)
        source_id = f"linovelib-{source_book_id}"
        allowed_hosts = {
            "www.linovelib.com",
            "linovelib.com",
            "www.bilinovel.com",
            "bilinovel.com",
        }
        existing = self._existing_done_catalog(source_id)
        if existing and not refresh_existing_catalog_id:
            existing_detail = await asyncio.to_thread(
                self.linovelib_provider.detail,
                remote_id,
                source_ref,
                include_chapters=False,
            )
            cover = await self._sync_remote_catalog_cover(
                catalog_id=int(existing["id"]),
                source_id=source_id,
                title=str(existing.get("title") or ""),
                author=str(existing.get("author") or ""),
                detail_url=str(
                    existing_detail.get("detail_url")
                    or self.linovelib_provider.base_url + source_ref
                ),
                cover_url=str(existing_detail.get("cover_url") or ""),
                allowed_hosts=allowed_hosts,
                request_bytes=self.linovelib_provider.download_cover,
                max_attempts=3,
            )
            normalized_status = self._update_catalog_book_status(
                int(existing["id"]),
                book_status or existing.get("book_status"),
                default=SERIALIZATION_STATUS_ONGOING,
            )
            return {
                "catalog_id": int(existing["id"]),
                "provider": LINOVELIB_PROVIDER,
                "source_book_id": source_book_id,
                "title": existing.get("title") or "",
                "author": existing.get("author") or "",
                "category": existing.get("category") or "轻小说",
                "output_path": str(
                    Path(str(existing["output_path"])).relative_to(
                        self.library_root
                    )
                ),
                "sha256": existing.get("sha256") or "",
                "status": "already_available",
                "deduplicated_before_download": True,
                "cover_status": cover["status"],
                "cover_url": cover["local_url"],
                "book_status": normalized_status,
            }

        detail = await asyncio.to_thread(
            self.linovelib_provider.detail, remote_id, source_ref
        )
        title = str(detail.get("title") or f"轻小说文库 {remote_id}")
        author = str(detail.get("author") or "作者未知")
        identity_match = self._existing_catalog_identity(title, author)
        if identity_match and not refresh_existing_catalog_id:
            cover = await self._sync_remote_catalog_cover(
                catalog_id=int(identity_match["id"]),
                source_id=source_id,
                title=title,
                author=author,
                detail_url=str(detail["detail_url"]),
                cover_url=str(detail.get("cover_url") or ""),
                allowed_hosts=allowed_hosts,
                request_bytes=self.linovelib_provider.download_cover,
                max_attempts=3,
            )
            return {
                "catalog_id": int(identity_match["id"]),
                "provider": LINOVELIB_PROVIDER,
                "source_book_id": source_book_id,
                "title": title,
                "author": author,
                "status": "already_available",
                "deduplicated_before_download": True,
                "deduplicated_by": "title_author",
                "cover_status": cover["status"],
                "cover_url": cover["local_url"],
            }

        raw = await asyncio.to_thread(self.linovelib_provider.download, detail)
        security_scan = await asyncio.to_thread(
            self.download_scanner.scan_bytes,
            raw,
            extension="txt",
            source=LINOVELIB_PROVIDER,
        )
        content = await asyncio.to_thread(
            self._remote_bytes_to_text, raw, "txt"
        )
        normalized_hint = str(category_hint or "").strip()
        category = (
            normalized_hint
            if normalized_hint and normalized_hint != "未分类"
            else "轻小说"
        )
        book_directory = self.linovelib_provider.book_directory(detail).resolve()
        output_path = book_directory / "总文件.txt"
        if not _is_within(output_path, self.books_root):
            raise ValueError("轻小说分卷总目录不在电子书库范围内")
        data = content.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        with _import_lock:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = output_path.with_suffix(output_path.suffix + ".part")
            temp_path.write_bytes(data)
            temp_path.replace(output_path)
            catalog_id = self._insert_imported_catalog_row(
                source_id=source_id,
                title=title,
                author=author,
                category=category,
                detail_url=str(detail["detail_url"]),
                file_url=str(detail["detail_url"]),
                output_path=output_path,
                sha256=digest,
                book_status=str(detail.get("book_status") or book_status),
                catalog_id_override=refresh_existing_catalog_id or None,
                rebind_source=rebind_existing_source,
            )
        self.move_catalog_books(
            catalog_ids=[catalog_id], target_library="fanqie"
        )
        if defer_postprocess:
            metadata_rebuild = {
                "status": "deferred",
                "catalog_id": catalog_id,
            }
        else:
            metadata_rebuild = await asyncio.to_thread(
                self._rebuild_imported_metadata_tool, catalog_id
            )
        refresh = await self.start_ingestion_index_refresh(
            [catalog_id], start_worker=not defer_postprocess
        )
        cover = await self._sync_remote_catalog_cover(
            catalog_id=catalog_id,
            source_id=source_id,
            title=title,
            author=author,
            detail_url=str(detail["detail_url"]),
            cover_url=str(detail.get("cover_url") or ""),
            allowed_hosts=allowed_hosts,
            request_bytes=self.linovelib_provider.download_cover,
            max_attempts=3,
        )
        return {
            "catalog_id": catalog_id,
            "provider": LINOVELIB_PROVIDER,
            "remote_id": source_book_id,
            "source_book_id": source_book_id,
            "title": title,
            "author": author,
            "category": category,
            "source_category": detail.get("source_category") or "",
            "source_extension": "txt",
            "chapter_count": int(detail.get("chapter_count") or 0),
            "volume_count": int(detail.get("volume_count") or 1),
            "volume_cover_count": int(
                detail.get("volume_cover_count") or 0
            ),
            "illustration_count": int(detail.get("illustration_count") or 0),
            "volume_directory": str(book_directory.relative_to(self.library_root)),
            "download_mode": "按分卷保存封面、章节和插画，并合并总文件 TXT",
            "ai_classified": False,
            "classification_reason": "轻小说来源固定归入轻小说分类",
            "output_path": str(output_path.relative_to(self.library_root)),
            "sha256": digest,
            "authorization": "user_configured_remote_source",
            "status": "downloaded",
            "cover_status": cover["status"],
            "cover_url": cover["local_url"],
            "book_status": self.normalize_book_status(
                detail.get("book_status") or book_status,
                default=SERIALIZATION_STATUS_ONGOING,
            ),
            "security_scan": security_scan,
            "metadata_rebuild": metadata_rebuild,
            "index_refresh": refresh,
            "postprocess_deferred": bool(defer_postprocess),
        }

    async def _import_authorized_shubaow_book(
        self,
        *,
        remote_id: Any,
        source_ref: str,
        book_status: str = "",
        category_hint: str = "",
        defer_postprocess: bool = False,
        refresh_existing_catalog_id: int = 0,
        rebind_existing_source: bool = False,
        expected_latest_chapter: str = "",
        ai_service: Any,
    ) -> Dict[str, Any]:
        source_ref = self.shubaow_provider.validate_source_ref(remote_id, source_ref)
        source_book_id = str(remote_id)
        source_id = f"shubaow-{source_book_id}"
        existing = self._existing_done_catalog(source_id)
        if existing and not refresh_existing_catalog_id:
            branding = await asyncio.to_thread(
                self._apply_source_branding_normalizer,
                int(existing["id"]),
                "shubaow",
            )
            normalized_status = self._update_catalog_book_status(
                int(existing["id"]),
                book_status or existing.get("book_status"),
                default=SERIALIZATION_STATUS_ONGOING,
            )
            return {
                "catalog_id": int(existing["id"]),
                "provider": AUTHORIZED_SHUBAOW_PROVIDER,
                "source_book_id": source_book_id,
                "title": existing.get("title") or "",
                "author": existing.get("author") or "",
                "category": existing.get("category") or "",
                "output_path": str(
                    Path(str(existing["output_path"])).relative_to(self.library_root)
                ),
                "sha256": existing.get("sha256") or "",
                "status": "already_available",
                "deduplicated_before_download": True,
                "book_status": normalized_status,
                "branding": branding,
            }
        detail = await asyncio.to_thread(
            self.shubaow_provider.detail, remote_id, source_ref
        )
        expected_latest = re.sub(
            r"\s+", " ", unicodedata.normalize("NFKC", expected_latest_chapter or "")
        ).strip()
        detail_chapters = list(detail.get("chapters") or [])
        normalized_titles = {
            re.sub(
                r"\s+", " ",
                unicodedata.normalize("NFKC", str(chapter.get("title") or "")),
            ).strip()
            for chapter in detail_chapters
        }
        actual_latest = re.sub(
            r"\s+", " ",
            unicodedata.normalize(
                "NFKC",
                str(detail_chapters[-1].get("title") or "") if detail_chapters else "",
            ),
        ).strip()
        if expected_latest and expected_latest not in normalized_titles:
            raise RuntimeError(
                "书宝网更新榜章节尚未出现在完整目录："
                f"目录末章={actual_latest or '空'}；更新榜末章={expected_latest}"
            )
        title = str(detail.get("title") or f"书宝网 {remote_id}")
        author = str(detail.get("author") or "作者未知")
        identity_match = self._existing_catalog_identity(title, author)
        if identity_match and not refresh_existing_catalog_id:
            return {
                "catalog_id": int(identity_match["id"]),
                "provider": AUTHORIZED_SHUBAOW_PROVIDER,
                "source_book_id": source_book_id,
                "title": title,
                "author": author,
                "status": "already_available",
                "deduplicated_before_download": True,
                "deduplicated_by": "title_author",
            }
        raw = await asyncio.to_thread(self.shubaow_provider.download, detail)
        security_scan = await asyncio.to_thread(
            self.download_scanner.scan_bytes,
            raw,
            extension="txt",
            source=AUTHORIZED_SHUBAOW_PROVIDER,
        )
        content = await asyncio.to_thread(self._remote_bytes_to_text, raw, "txt")
        subjects = [str(value) for value in (detail.get("categories") or [])]
        normalized_hint = str(category_hint or "").strip()
        if normalized_hint and normalized_hint != "未分类":
            category = normalized_hint
            ai_classified = False
            classification_reason = "沿用授权源站目录的标准分类"
        else:
            category, ai_classified, classification_reason = (
                await self._classify_imported_book(
                    title=title,
                    author=author,
                    subjects=subjects,
                    sample=content,
                    ai_service=ai_service,
                )
            )
        filename = (
            f"{self._safe_import_name(title, f'未命名-{source_book_id}')}"
            f"__{self._safe_import_name(author, '作者未知', 60)}"
            f"__{source_id}.txt"
        )
        output_path = self.books_root / self._safe_import_name(
            category, "未分类", 60
        ) / filename
        refresh_path = self._refresh_target_output_path(refresh_existing_catalog_id)
        if refresh_path:
            output_path = refresh_path
        data = content.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        with _import_lock:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = output_path.with_suffix(output_path.suffix + ".part")
            temp_path.write_bytes(data)
            temp_path.replace(output_path)
            catalog_id = self._insert_imported_catalog_row(
                source_id=source_id,
                title=title,
                author=author,
                category=category,
                detail_url=str(detail["detail_url"]),
                file_url=str(detail["detail_url"]),
                output_path=output_path,
                sha256=digest,
                book_status=str(detail.get("book_status") or book_status),
                catalog_id_override=refresh_existing_catalog_id or None,
                rebind_source=rebind_existing_source,
            )
        self.move_catalog_books(catalog_ids=[catalog_id], target_library="fanqie")
        branding = await asyncio.to_thread(
            self._apply_source_branding_normalizer,
            catalog_id,
            "shubaow",
        )
        data = output_path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if defer_postprocess:
            metadata_rebuild = {"status": "deferred", "catalog_id": catalog_id}
        else:
            metadata_rebuild = await asyncio.to_thread(
                self._rebuild_imported_metadata_tool, catalog_id
            )
        refresh = await self.start_ingestion_index_refresh(
            [catalog_id],
            start_worker=not defer_postprocess,
        )
        cover = await self._sync_remote_catalog_cover(
            catalog_id=catalog_id,
            source_id=source_id,
            title=title,
            author=author,
            detail_url=str(detail["detail_url"]),
            cover_url=str(detail.get("cover_url") or ""),
            allowed_hosts={"www.shubaow.org", "shubaow.org", "pic.shubaow.org"},
            request_bytes=self.shubaow_provider.download_cover,
            max_attempts=3,
        )
        return {
            "catalog_id": catalog_id,
            "provider": AUTHORIZED_SHUBAOW_PROVIDER,
            "remote_id": source_book_id,
            "source_book_id": source_book_id,
            "title": title,
            "author": author,
            "category": category,
            "source_extension": "txt",
            "chapter_count": int(detail.get("chapter_count") or 0),
            "latest_chapter": actual_latest,
            "download_mode": "抓取全章节并合并为单本 TXT",
            "ai_classified": ai_classified,
            "classification_reason": classification_reason,
            "output_path": str(output_path.relative_to(self.library_root)),
            "sha256": digest,
            "license": "站点所有者授权来源",
            "authorization": "site_owner_authorized",
            "status": "downloaded",
            "cover_status": cover["status"],
            "cover_url": cover["local_url"],
            "book_status": self.normalize_book_status(
                detail.get("book_status") or book_status,
                default=SERIALIZATION_STATUS_ONGOING,
            ),
            "security_scan": security_scan,
            "metadata_rebuild": metadata_rebuild,
            "index_refresh": refresh,
            "postprocess_deferred": bool(defer_postprocess),
            "branding": branding,
        }

    async def _import_authorized_ixdzs_book(
        self,
        *,
        remote_id: Any,
        source_ref: str,
        book_status: str = "",
        category_hint: str = "",
        defer_postprocess: bool = False,
        refresh_existing_catalog_id: int = 0,
        rebind_existing_source: bool = False,
        ai_service: Any,
    ) -> Dict[str, Any]:
        source_ref = self.ixdzs_provider.validate_source_ref(remote_id, source_ref)
        detail = await asyncio.to_thread(
            self.ixdzs_provider.detail, remote_id, source_ref
        )
        title = str(detail.get("title") or f"爱下电子书 {remote_id}")
        author = str(detail.get("author") or "作者未知")
        existing = self._existing_catalog_identity(title, author)
        if existing and not refresh_existing_catalog_id:
            cover_url = str(detail.get("cover_url") or "")
            cover_host = (urlparse(cover_url).hostname or "").lower()
            cover = await self._sync_remote_catalog_cover(
                catalog_id=int(existing["id"]),
                source_id=f"ixdzs-{remote_id}",
                title=title,
                author=author,
                detail_url=str(detail["detail_url"]),
                cover_url=cover_url,
                allowed_hosts={cover_host} - {""},
                allowed_host_suffixes=(".ixdzs.com", ".ixdzs8.com"),
                max_attempts=3,
            )
            return {
                "catalog_id": int(existing["id"]),
                "provider": AUTHORIZED_IXDZS_PROVIDER,
                "remote_id": str(remote_id),
                "title": title,
                "author": author,
                "status": "already_available",
                "deduplicated_before_download": True,
                "deduplicated_by": "title_author",
                "cover_status": cover["status"],
                "cover_url": cover["local_url"],
            }
        archive = await asyncio.to_thread(self.ixdzs_provider.download, detail)
        security_scan = await asyncio.to_thread(
            self.download_scanner.scan_bytes,
            archive,
            extension="zip",
            source=AUTHORIZED_IXDZS_PROVIDER,
        )
        content = await asyncio.to_thread(
            self.ixdzs_provider.extract_text, archive
        )
        if not downloaded_text_matches_identity(content, title, author):
            raise ValueError("爱下电子书下载正文身份与详情不匹配")
        content = content.replace(
            IXDZS_PROMOTIONAL_NOTICE,
            OOHSTORY_EBOOK_NOTICE,
        )
        normalized_hint = str(category_hint or "").strip()
        if normalized_hint and normalized_hint != "未分类":
            category = normalized_hint
            ai_classified = False
            classification_reason = "沿用授权源站目录的标准分类"
        else:
            category, ai_classified, classification_reason = (
                await self._classify_imported_book(
                    title=title,
                    author=author,
                    subjects=[
                        str(value) for value in detail.get("categories") or []
                    ],
                    sample=content,
                    ai_service=ai_service,
                )
            )
        source_id = f"ixdzs-{remote_id}"
        filename = (
            f"{self._safe_import_name(title, f'未命名-{remote_id}')}"
            f"__{self._safe_import_name(author, '作者未知', 60)}"
            f"__{source_id}.txt"
        )
        output_path = self.books_root / self._safe_import_name(
            category, "未分类", 60
        ) / filename
        refresh_path = self._refresh_target_output_path(
            refresh_existing_catalog_id
        )
        if refresh_path:
            output_path = refresh_path
        data = content.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        with _import_lock:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = output_path.with_suffix(output_path.suffix + ".part")
            temporary.write_bytes(data)
            temporary.replace(output_path)
            catalog_id = self._insert_imported_catalog_row(
                source_id=source_id,
                title=title,
                author=author,
                category=category,
                detail_url=str(detail["detail_url"]),
                file_url=str(detail["download_url"]),
                output_path=output_path,
                sha256=digest,
                book_status=str(detail.get("book_status") or book_status),
                catalog_id_override=refresh_existing_catalog_id or None,
                rebind_source=rebind_existing_source,
            )
        self.move_catalog_books(
            catalog_ids=[catalog_id],
            target_library="fanqie",
        )
        if defer_postprocess:
            metadata_rebuild = {"status": "deferred", "catalog_id": catalog_id}
        else:
            metadata_rebuild = await asyncio.to_thread(
                self._rebuild_imported_metadata_tool,
                catalog_id,
            )
        cover_url = str(detail.get("cover_url") or "")
        cover_host = (urlparse(cover_url).hostname or "").lower()
        cover = await self._sync_remote_catalog_cover(
            catalog_id=catalog_id,
            source_id=source_id,
            title=title,
            author=author,
            detail_url=str(detail["detail_url"]),
            cover_url=cover_url,
            allowed_hosts={cover_host} - {""},
            allowed_host_suffixes=(".ixdzs.com", ".ixdzs8.com"),
            max_attempts=3,
        )
        refresh = await self.start_ingestion_index_refresh(
            [catalog_id],
            start_worker=not defer_postprocess,
        )
        return {
            "catalog_id": catalog_id,
            "provider": AUTHORIZED_IXDZS_PROVIDER,
            "remote_id": str(remote_id),
            "title": title,
            "author": author,
            "category": category,
            "status": "downloaded",
            "library": "fanqie",
            "book_status": self.normalize_book_status(
                detail.get("book_status") or book_status,
                default=SERIALIZATION_STATUS_ONGOING,
            ),
            "ai_classified": ai_classified,
            "classification_reason": classification_reason,
            "output_path": str(output_path.relative_to(self.library_root)),
            "sha256": digest,
            "cover_status": cover["status"],
            "cover_url": cover["local_url"],
            "security_scan": security_scan,
            "metadata_rebuild": metadata_rebuild,
            "index_refresh": refresh,
            "postprocess_deferred": bool(defer_postprocess),
        }

    async def import_fanqie_export(
        self,
        *,
        book_id: Any,
        source_path: Path,
        title: str = "",
        author: str = "",
        book_status: str = "",
        ai_service: Any,
        refresh_existing: bool = False,
    ) -> Dict[str, Any]:
        """Normalize one completed official Fanqie desktop export into the library."""
        book_id = self.fanqie_downloader.validate_book_id(book_id)
        existing = self._existing_done_catalog(f"fanqie-{book_id}")
        if existing and not refresh_existing:
            normalized_book_status = self._update_catalog_book_status(
                int(existing["id"]),
                book_status or existing.get("book_status"),
                default=SERIALIZATION_STATUS_ONGOING,
            )
            cover = await self._sync_fanqie_catalog_cover(
                catalog_id=int(existing["id"]),
                book_id=book_id,
                title=str(existing.get("title") or ""),
                author=str(existing.get("author") or ""),
            )
            return {
                "catalog_id": int(existing["id"]),
                "provider": FANQIE_DOWNLOADER_PROVIDER,
                "book_id": book_id,
                "title": existing.get("title") or "",
                "author": existing.get("author") or "",
                "category": existing.get("category") or "",
                "output_path": str(
                    Path(str(existing["output_path"])).relative_to(
                        self.library_root
                    )
                ),
                "sha256": existing.get("sha256") or "",
                "status": "already_available",
                "deduplicated_before_import": True,
                "cover_status": cover["status"],
                "cover_url": cover["local_url"],
                "book_status": normalized_book_status,
            }
        source_path = Path(source_path).expanduser().resolve()
        export_root = self.fanqie_downloader.export_root.resolve()
        if (
            not source_path.is_file()
            or not source_path.is_relative_to(export_root)
        ):
            raise ValueError("番茄导入文件必须来自受控下载器导出目录")
        extension = source_path.suffix.lower().lstrip(".")
        if extension not in {"txt", "epub"}:
            raise ValueError("番茄导入仅支持 TXT 或 EPUB")
        if source_path.stat().st_size > 120 * 1024 * 1024:
            raise ValueError("番茄导入文件超过 120MB 安全上限")
        raw = await asyncio.to_thread(source_path.read_bytes)
        security_scan = await asyncio.to_thread(
            self.download_scanner.scan_bytes,
            raw,
            extension=extension,
            source=FANQIE_DOWNLOADER_PROVIDER,
        )
        content = await asyncio.to_thread(
            self._remote_bytes_to_text, raw, extension
        )
        if not title or not author:
            stem = source_path.stem
            parsed_title, separator, parsed_author = stem.rpartition(" - ")
            if not title:
                title = parsed_title if separator else stem
            if not author:
                author = parsed_author if separator else "作者未知"
        title = (
            str(existing.get("title") or "").strip()
            if existing
            else title.strip()
        ) or f"番茄作品 {book_id}"
        author = (
            str(existing.get("author") or "").strip()
            if existing
            else author.strip()
        ) or "作者未知"
        if existing:
            category = str(existing.get("category") or "未分类")
            ai_classified = False
            classification_reason = "更新现有番茄正文，保留原分类"
        else:
            category, ai_classified, classification_reason = (
                await self._classify_imported_book(
                    title=title,
                    author=author,
                    subjects=["番茄小说", "线上扫榜选中作品"],
                    sample=content,
                    ai_service=ai_service,
                )
            )
        source_id = f"fanqie-{book_id}"
        filename = (
            f"{self._safe_import_name(title, f'番茄作品-{book_id}')}"
            f"__{self._safe_import_name(author, '作者未知', 60)}"
            f"__{source_id}.txt"
        )
        if existing and existing.get("output_path"):
            output_path = Path(
                str(existing["output_path"])
            ).expanduser().resolve()
            if not _is_within(output_path, self.books_root):
                raise ValueError("现有番茄正文路径超出电子书库范围")
        else:
            output_path = self.books_root / self._safe_import_name(
                category, "未分类", 60
            ) / filename
        data = content.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        detail_url = f"https://fanqienovel.com/page/{book_id}"
        with _import_lock:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = output_path.with_suffix(
                output_path.suffix + ".part"
            )
            temp_path.write_bytes(data)
            temp_path.replace(output_path)
            catalog_id = self._insert_imported_catalog_row(
                source_id=source_id,
                title=title,
                author=author,
                category=category,
                detail_url=detail_url,
                file_url=detail_url,
                output_path=output_path,
                sha256=digest,
                book_status=self.normalize_book_status(
                    book_status or existing.get("book_status") if existing else book_status,
                    default=SERIALIZATION_STATUS_ONGOING,
                ),
            )
        refresh = await self.start_ingestion_index_refresh([catalog_id])
        cover = await self._sync_fanqie_catalog_cover(
            catalog_id=catalog_id,
            book_id=book_id,
            title=title,
            author=author,
        )
        return {
            "catalog_id": catalog_id,
            "provider": FANQIE_DOWNLOADER_PROVIDER,
            "book_id": book_id,
            "title": title,
            "author": author,
            "category": category,
            "source_extension": extension,
            "ai_classified": ai_classified,
            "classification_reason": classification_reason,
            "output_path": str(output_path.relative_to(self.library_root)),
            "sha256": digest,
            "source_export_retained": str(source_path),
            "status": "updated" if existing else "downloaded",
            "cover_status": cover["status"],
            "cover_url": cover["local_url"],
            "book_status": self.normalize_book_status(
                book_status or existing.get("book_status") if existing else book_status,
                default=SERIALIZATION_STATUS_ONGOING,
            ),
            "security_scan": security_scan,
            "index_refresh": refresh,
        }

    @staticmethod
    def _fanqie_incremental_body(
        content: str,
        *,
        expected_start: int,
    ) -> tuple[str, str]:
        """Return only the chapter body from a range export.

        Fanqie range exports can still contain a metadata preface.  Never
        append that preface to the existing novel, and mechanically reject a
        non-contiguous first numeric chapter when the label exposes a number.
        """
        lines = str(content or "").replace("\r\n", "\n").splitlines()
        first_index = -1
        first_label = ""
        for index, line in enumerate(lines):
            if len(line) - len(line.lstrip(" \t")) > 2:
                continue
            match = READER_HEADING_LINE.fullmatch(line.strip())
            if not match:
                continue
            first_index = index
            first_label = re.sub(r"\s+", "", match.group("label"))
            break
        if first_index < 0:
            raise ValueError("番茄增量下载未识别到新增章节")
        numeric = re.search(r"\d+", first_label)
        if numeric and int(numeric.group()) != int(expected_start):
            raise ValueError(
                "番茄增量章节不连续："
                f"本地应从第 {expected_start} 章继续，"
                f"下载结果却从{first_label}开始"
            )
        body = "\n".join(lines[first_index:]).strip()
        if len(body.encode("utf-8")) < 128:
            raise ValueError("番茄增量章节正文过短，拒绝追加")
        return body + "\n", first_label

    async def refresh_fanqie_incremental(
        self,
        *,
        catalog_id: int,
        book_id: Any,
        title: str,
        author: str,
        book_status: str = "",
    ) -> Dict[str, Any]:
        """Append only chapters after the exact local last chapter."""
        book_id = self.fanqie_downloader.validate_book_id(book_id)
        existing = self._existing_done_catalog(f"fanqie-{book_id}")
        if not existing or int(existing["id"]) != int(catalog_id):
            raise KeyError("番茄跟踪作品不存在或正文尚未入库")
        output_path = Path(str(existing["output_path"])).expanduser().resolve()
        if not _is_within(output_path, self.books_root) or not output_path.is_file():
            raise ValueError("现有番茄正文路径无效")

        reader = self.get_reader_catalog(int(catalog_id))
        local_chapter_count = int(reader.get("chapter_count") or 0)
        if local_chapter_count < 1:
            raise ValueError("本地正文没有可验证章节，不能执行增量追加")
        expected_start = local_chapter_count + 1
        normalized_status = self._update_catalog_book_status(
            int(catalog_id),
            book_status or existing.get("book_status"),
            default=SERIALIZATION_STATUS_ONGOING,
        )
        downloaded = await asyncio.to_thread(
            self.fanqie_downloader.download,
            book_id,
            file_format="txt",
            start_chapter=expected_start,
            end_chapter=None,
        )
        source_path = Path(str(downloaded["path"])).expanduser().resolve()
        export_root = self.fanqie_downloader.export_root.resolve()
        if not source_path.is_file() or not source_path.is_relative_to(export_root):
            raise ValueError("番茄增量文件不在受控导出目录")
        raw = await asyncio.to_thread(source_path.read_bytes)
        security_scan = await asyncio.to_thread(
            self.download_scanner.scan_bytes,
            raw,
            extension="txt",
            source="fanqie_incremental",
        )
        incremental_text = await asyncio.to_thread(
            self._remote_bytes_to_text, raw, "txt"
        )
        body, first_label = self._fanqie_incremental_body(
            incremental_text,
            expected_start=expected_start,
        )

        old_content = output_path.read_text(encoding="utf-8", errors="replace")
        combined = old_content.rstrip() + "\n\n" + body
        data = combined.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        with _import_lock:
            temporary = output_path.with_suffix(output_path.suffix + ".incremental")
            temporary.write_bytes(data)
            temporary.replace(output_path)
            self._insert_imported_catalog_row(
                source_id=f"fanqie-{book_id}",
                title=str(existing.get("title") or title),
                author=str(existing.get("author") or author),
                category=str(existing.get("category") or "未分类"),
                detail_url=f"https://fanqienovel.com/page/{book_id}",
                file_url=f"https://fanqienovel.com/page/{book_id}",
                output_path=output_path,
                sha256=digest,
                book_status=normalized_status,
            )

        rebuilt, _ = self.rebuild_reader_index(
            int(catalog_id),
            output_path,
            force=True,
        )
        self.sync_reader_metrics(int(catalog_id), rebuilt)
        chapters = list(rebuilt.get("chapters") or [])
        latest = next(
            (
                chapter
                for chapter in reversed(chapters)
                if chapter.get("kind") != "intro"
            ),
            {},
        )
        final_count = int(rebuilt.get("chapter_count") or 0)
        if final_count < expected_start:
            raise ValueError("增量追加后的章节索引未增长，拒绝报告成功")
        return {
            "catalog_id": int(catalog_id),
            "book_id": book_id,
            "status": "updated",
            "book_status": normalized_status,
            "previous_chapter_count": local_chapter_count,
            "chapter_count": final_count,
            "added_chapter_count": final_count - local_chapter_count,
            "first_added_chapter": first_label,
            "latest_chapter": {
                "label": str(latest.get("label") or ""),
                "title": str(latest.get("title") or ""),
                "chapter_index": latest.get("chapter_index"),
            },
            "security_scan": security_scan,
        }

    @classmethod
    def _validate_fanqie_full_export(
        cls,
        source_path: Path,
    ) -> Dict[str, Any]:
        """Reject partial/empty desktop exports before they enter the catalog."""
        source_path = Path(source_path).expanduser().resolve()
        if not source_path.is_file() or source_path.suffix.lower() != ".txt":
            raise ValueError("番茄完整下载没有生成可用的 TXT 文件")
        if source_path.stat().st_size < 1024:
            raise ValueError("番茄完整下载正文过短，拒绝将残本加入书库")

        labels: List[str] = []
        metadata_chapter_count = 0
        with source_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle):
                text = line.rstrip("\r\n")
                if line_number < 80 and not metadata_chapter_count:
                    count_match = re.fullmatch(
                        r"\s*章节数[：:]\s*(\d+)\s*章?\s*",
                        text,
                    )
                    if count_match:
                        metadata_chapter_count = int(count_match.group(1))
                if len(text) - len(text.lstrip(" \t")) > 2:
                    continue
                heading = READER_HEADING_LINE.fullmatch(text.strip())
                if heading:
                    labels.append(
                        re.sub(r"\s+", "", heading.group("label"))
                    )
        if not labels:
            raise ValueError("番茄完整下载未识别到章节，拒绝将残本加入书库")
        has_first_chapter = any(
            re.fullmatch(r"第(?:0*1|一|壹)[章回节]", label)
            for label in labels[:5]
        )
        if not has_first_chapter:
            raise ValueError("番茄完整下载缺少第 1 章，拒绝将残本加入书库")
        numeric_chapters = [
            int(match.group())
            for label in labels
            if (match := re.search(r"\d+", label))
        ]
        if numeric_chapters:
            missing = sorted(
                set(range(1, max(numeric_chapters) + 1))
                - set(numeric_chapters)
            )
            if missing:
                sample = "、".join(str(value) for value in missing[:8])
                raise ValueError(
                    f"番茄完整下载章节序号不连续，缺少第 {sample} 章"
                )
        if (
            metadata_chapter_count
            and len(labels) < metadata_chapter_count
        ):
            raise ValueError(
                "番茄完整下载章节不全："
                f"文件头标记 {metadata_chapter_count} 章，"
                f"实际仅识别 {len(labels)} 章"
            )
        return {
            "chapter_count": len(labels),
            "metadata_chapter_count": metadata_chapter_count,
            "last_chapter_label": labels[-1],
        }

    @staticmethod
    def _plot_query_terms(question: str) -> tuple[List[str], List[str]]:
        normalized = re.sub(
            r"[，。！？、,.!?：:；;（）()\[\]《》“”\"'\s]+", "", question
        )
        terms: List[str] = []
        motif_tags: List[str] = []
        for motif, groups in PLOT_MOTIFS.items():
            hit_a = any(term in normalized for term in groups["a"])
            hit_b = any(term in normalized for term in groups["b"])
            if motif in normalized or (hit_a and hit_b):
                motif_tags.append(motif)
                terms.extend((*groups["a"], *groups["b"]))
        for triggers, expansions in PLOT_QUERY_EXPANSIONS:
            if any(trigger in normalized for trigger in triggers):
                terms.extend(expansions)
        stripped = normalized
        for stop in (
            "书库中", "有没有", "是否有", "类似", "这类", "这种", "剧情",
            "情节", "桥段", "小说", "作品", "哪些", "寻找", "查询", "参考",
            "写法", "存在", "关于", "请问", "帮我",
        ):
            stripped = stripped.replace(stop, "")
        if len(stripped) >= 3:
            terms.append(stripped[:12])
            if len(stripped) > 6:
                terms.extend(
                    stripped[index : index + 4]
                    for index in range(0, min(len(stripped) - 3, 20), 3)
                )
        return list(dict.fromkeys(term for term in terms if len(term) >= 2))[:32], motif_tags

    @staticmethod
    def _evidence_excerpt(content: str, terms: List[str], limit: int = 520) -> str:
        if len(content) <= limit:
            return content
        positions = [
            content.find(term) for term in terms if term and content.find(term) >= 0
        ]
        center = min(positions) if positions else len(content) // 2
        start = max(0, center - limit // 3)
        end = min(len(content), start + limit)
        prefix = "…" if start else ""
        suffix = "…" if end < len(content) else ""
        return prefix + content[start:end].strip() + suffix

    def search_plot_index(
        self,
        project_root: Path,
        question: str,
        limit: int = 8,
        extra_terms: Optional[Iterable[str]] = None,
        extra_term_groups: Optional[Iterable[Iterable[str]]] = None,
    ) -> Dict[str, Any]:
        question = question.strip()
        if len(question) < 2:
            raise ValueError("提问至少需要 2 个字符")
        mysql_plot = bool(
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        )
        if not mysql_plot and not self.index_path.exists():
            return {
                "question": question,
                "terms": [],
                "motif_tags": [],
                "results": [],
                "index_ready": False,
                "message": "尚未建立剧情索引",
            }
        terms, motif_tags = self._plot_query_terms(question)
        expanded_terms = self._normalize_plot_expansion_terms(extra_terms or [])
        expanded_groups = self._normalize_plot_expansion_groups(
            extra_term_groups or []
        )
        # trigram FTS 不索引两个汉字的词。组内二字词仍用于候选二次校验，
        # 同时补充少量“动作+对象”组合，避免为支持二字词而扫描 47 万段正文。
        group_pairs = (
            [
                f"{left}{right}"
                for left in expanded_groups[0][:4]
                for right in expanded_groups[1][:4]
            ]
            if len(expanded_groups) >= 2
            else []
        )
        group_fts_terms = [
            term
            for group in expanded_groups
            for term in group
            if len(term) >= 3
        ]
        terms = list(
            dict.fromkeys(
                [*terms, *expanded_terms, *group_pairs, *group_fts_terms]
            )
        )[:48]
        rows: Dict[int, Dict[str, Any]] = {}
        if mysql_plot:
            plot_books = self.mysql_catalog.asset_counts()["plot_books"]
            if not plot_books:
                return {
                    "question": question,
                    "terms": terms,
                    "motif_tags": motif_tags,
                    "results": [],
                    "index_ready": False,
                    "message": "尚未建立剧情索引",
                }
            candidate_limit = 600 if expanded_groups else 120
            for row in self.mysql_catalog.search_plot_candidates(
                terms=terms,
                motif_tags=motif_tags,
                limit=candidate_limit,
            ):
                rows[int(row["id"])] = dict(row)
        else:
            with self._index_connection() as conn:
                plot_books = conn.execute(
                    "SELECT COUNT(*) FROM plot_index_meta"
                ).fetchone()[0]
                if not plot_books:
                    return {
                        "question": question,
                        "terms": terms,
                        "motif_tags": motif_tags,
                        "results": [],
                        "index_ready": False,
                        "message": "尚未建立剧情索引",
                    }

                for motif in motif_tags:
                    for row in conn.execute(
                        """
                        SELECT *, -1.0 AS fts_rank
                        FROM plot_segments
                        WHERE motif_tags LIKE ?
                        LIMIT 80
                        """,
                        (f'%"{motif}"%',),
                    ):
                        rows[int(row["id"])] = dict(row)

                fts_terms = [term for term in terms if len(term) >= 3][:20]
                if fts_terms:
                    candidate_limit = 600 if expanded_groups else 120
                    match_query = " OR ".join(
                        f'"{term.replace(chr(34), chr(34) * 2)}"'
                        for term in fts_terms
                    )
                    try:
                        for row in conn.execute(
                            f"""
                            SELECT s.*, bm25(
                              plot_segments_fts, 3.0, 1.0, 1.0, 5.0, 1.0
                            ) AS fts_rank
                            FROM plot_segments_fts
                            JOIN plot_segments AS s
                              ON s.id = plot_segments_fts.rowid
                            WHERE plot_segments_fts MATCH ?
                            ORDER BY fts_rank
                            LIMIT {candidate_limit}
                            """,
                            (match_query,),
                        ):
                            rows.setdefault(int(row["id"]), dict(row))
                    except sqlite3.OperationalError:
                        pass

        if expanded_groups:
            rows = {
                row_id: row
                for row_id, row in rows.items()
                if self._matches_plot_term_groups(
                    str(row.get("content") or ""), expanded_groups
                )
            }

        grouped: Dict[int, Dict[str, Any]] = {}
        for position, row in enumerate(rows.values()):
            catalog_id = int(row["catalog_id"])
            tags = _read_json_text(row.get("motif_tags"), [])
            motif_overlap = [tag for tag in motif_tags if tag in tags]
            item = grouped.setdefault(
                catalog_id,
                {
                    "catalog_id": catalog_id,
                    "source_id": row["source_id"],
                    "title": row["title"],
                    "author": row["author"],
                    "category": row["category"],
                    "local_score": 0,
                    "motif_tags": [],
                    "evidence": [],
                },
            )
            item["local_score"] += max(2, 20 - position // 8)
            if motif_overlap:
                item["local_score"] += 35 * len(motif_overlap)
            item["motif_tags"] = sorted(
                set(item["motif_tags"]) | set(tags)
            )
            if len(item["evidence"]) < 2:
                item["evidence"].append(
                    {
                        "location": row["location"],
                        "motif_tags": tags,
                        "excerpt": self._evidence_excerpt(
                            row["content"], terms
                        ),
                    }
                )

        deconstruction_roots = [
            self.global_deconstruction_root,
            project_root / "拆文库",
        ]
        results = sorted(
            grouped.values(),
            key=lambda item: (
                item["local_score"],
                len(item["evidence"]),
                item["catalog_id"],
            ),
            reverse=True,
        )[: min(max(limit, 1), 12)]
        for item in results:
            safe_title = _safe_name(item["title"])
            full_confirmed = False
            for deconstruction_root in deconstruction_roots:
                if not deconstruction_root.exists():
                    continue
                for directory in deconstruction_root.iterdir():
                    if (
                        directory.is_dir()
                        and safe_title in directory.name
                        and (
                            (directory / "剧情" / "节奏.md").exists()
                            or any(directory.glob("*拆文报告*.md"))
                        )
                    ):
                        full_confirmed = True
                        break
                if full_confirmed:
                    break
            item["confirmation_level"] = (
                "完整拆书确认" if full_confirmed else "本地正文证据候选"
            )
            item["local_score"] = min(100, item["local_score"])
        return {
            "question": question,
            "terms": terms,
            "ai_expanded_terms": expanded_terms,
            "ai_expanded_groups": expanded_groups,
            "motif_tags": motif_tags,
            "results": results,
            "index_ready": True,
            "indexed_books": int(plot_books),
            "message": (
                f"本地索引召回 {len(results)} 本候选"
                if results
                else "本地剧情索引中未找到足够证据"
            ),
        }

    @staticmethod
    def _normalize_plot_expansion_terms(terms: Iterable[Any]) -> List[str]:
        """只接受少量可用于 trigram FTS 的短词，避免模型输出扩大检索负担。"""
        normalized: List[str] = []
        for value in terms:
            if not isinstance(value, str):
                continue
            term = re.sub(
                r"[，。！？、,.!?：:；;（）()\[\]《》“”\"'`\s]+",
                "",
                value,
            ).strip()
            if 3 <= len(term) <= 12 and term not in normalized:
                normalized.append(term)
            if len(normalized) >= PLOT_AI_EXPANSION_MAX_TERMS:
                break
        return normalized

    @staticmethod
    def _normalize_plot_expansion_groups(
        groups: Iterable[Iterable[Any]],
    ) -> List[List[str]]:
        normalized_groups: List[List[str]] = []
        for values in groups:
            if not isinstance(values, (list, tuple)):
                continue
            normalized: List[str] = []
            for value in values:
                if not isinstance(value, str):
                    continue
                term = re.sub(
                    r"[，。！？、,.!?：:；;（）()\[\]《》“”\"'`\s]+",
                    "",
                    value,
                ).strip()
                if 2 <= len(term) <= 8 and term not in normalized:
                    normalized.append(term)
                if len(normalized) >= 4:
                    break
            if normalized:
                normalized_groups.append(normalized)
            if len(normalized_groups) >= 3:
                break
        return normalized_groups

    @staticmethod
    def _matches_plot_term_groups(
        content: str,
        groups: List[List[str]],
        max_distance: int = 700,
    ) -> bool:
        positions: List[List[int]] = []
        for group in groups:
            group_positions = [
                content.find(term) for term in group if term in content
            ]
            group_positions = [position for position in group_positions if position >= 0]
            if not group_positions:
                return False
            positions.append(group_positions)
        if len(positions) < 2:
            return True
        return any(
            abs(left - right) <= max_distance
            for left in positions[0]
            for right in positions[1]
        )

    async def _expand_plot_query_with_ai(
        self, question: str, ai_service: Any
    ) -> Dict[str, Any]:
        prompt = (
            "把下面的中文网文剧情意图拆成必须同时满足的概念组，并给出正文短语。"
            "只返回 JSON："
            "{\"groups\":[[\"动作近义词\"],[\"对象近义词\"]],"
            "\"phrases\":[\"正文短语\"]}。"
            "每组最多 4 词，允许 2 字词；phrases 最多 6 个、每个 3～12 字，"
            "用于粗召回。phrases 要覆盖剧情前因、动作、后果的常见正文表达，"
            "不要只机械拼接两组词；例如可写“仙子受伤”“救命之恩”“带回住处”。"
            "不要解释、不要书名、不要回答问题。\n"
            f"检索意图：{question[:160]}"
        )
        try:
            raw = await ai_service.chat(
                [
                    {
                        "role": "system",
                        "content": "你只做中文剧情检索词扩展，严格输出短 JSON。",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=PLOT_AI_EXPANSION_MAX_TOKENS,
            )
            parsed = self._extract_json_object(raw)
            groups = self._normalize_plot_expansion_groups(
                parsed.get("groups", []) if isinstance(parsed, dict) else []
            )
            terms = self._normalize_plot_expansion_terms(
                parsed.get("phrases", []) if isinstance(parsed, dict) else []
            )
            usable = bool(terms and len(groups) >= 2)
            return {
                "attempted": True,
                "completed": True,
                "used": usable,
                "terms": terms,
                "groups": groups,
                "input_chars": len(prompt),
                "error": "" if usable else "AI 未返回可用的概念组和短检索词",
            }
        except Exception as exc:
            return {
                "attempted": True,
                "completed": False,
                "used": False,
                "terms": [],
                "groups": [],
                "input_chars": len(prompt),
                "error": str(exc)[:160],
            }

    @staticmethod
    def _plot_evidence_text(results: List[Dict[str, Any]]) -> str:
        blocks: List[str] = []
        used_chars = 0
        for index, item in enumerate(
            results[:PLOT_AI_SUMMARY_MAX_BOOKS], start=1
        ):
            evidence = (item.get("evidence") or [{}])[0]
            excerpt = str(evidence.get("excerpt") or "")[:220]
            block = (
                f"[{index}]《{item['title']}》/作者：{item['author'] or '未知'}/"
                f"分类：{item['category']}/级别：{item['confirmation_level']}/"
                f"位置：{evidence.get('location') or '未知'}\n{excerpt}"
            )
            remaining = PLOT_AI_EVIDENCE_MAX_CHARS - used_chars
            if remaining <= 80:
                break
            block = block[:remaining]
            blocks.append(block)
            used_chars += len(block) + 2
        return "\n\n".join(blocks)

    async def answer_plot_question(
        self,
        project_root: Path,
        question: str,
        ai_service: Any,
        limit: int = 8,
    ) -> Dict[str, Any]:
        local = self.search_plot_index(project_root, question, limit=limit)
        search_stages = [
            {
                "stage": "local_exact",
                "label": "本地索引直接召回",
                "ai_used": False,
                "result_count": len(local.get("results") or []),
            }
        ]
        ai_calls = 0
        ai_succeeded_calls = 0
        ai_used = False
        ai_input_chars = 0
        expansion_error = ""
        expansion_applied = False

        if local["index_ready"] and not local["results"]:
            expansion = await self._expand_plot_query_with_ai(question, ai_service)
            ai_calls += 1
            ai_succeeded_calls += int(bool(expansion["completed"]))
            ai_input_chars += int(expansion["input_chars"])
            ai_used = bool(ai_succeeded_calls)
            expansion_applied = bool(expansion["used"])
            expansion_error = expansion["error"]
            if expansion["terms"]:
                local = self.search_plot_index(
                    project_root,
                    question,
                    limit=limit,
                    extra_terms=expansion["terms"],
                    extra_term_groups=expansion["groups"],
                )
            search_stages.append(
                {
                    "stage": "ai_query_expansion",
                    "label": "AI 低 Token 意图扩展后回查本地索引",
                    "ai_used": bool(expansion["completed"]),
                    "query_expansion_applied": expansion_applied,
                    "terms": expansion["terms"],
                    "groups": expansion["groups"],
                    "result_count": len(local.get("results") or []),
                    "fts_candidate_cap": 600,
                    "error": expansion_error,
                }
            )

        if not local["index_ready"] or not local["results"]:
            if not local["index_ready"]:
                answer = local["message"]
            elif expansion_applied:
                answer = "AI 已扩展剧情同义词并回查本地索引，仍未找到足够证据。"
            elif ai_used:
                answer = (
                    "AI 已调用，但没有返回可用于本地索引的概念组；"
                    "未发送任何书库正文。"
                )
            else:
                answer = (
                    "本地索引未命中；AI 意图扩展暂时不可用，未发送任何书库正文。"
                    + (f"（{expansion_error}）" if expansion_error else "")
                )
            return {
                **local,
                "answer": answer,
                "ai_used": ai_used,
                "ai_calls": ai_calls,
                "ai_succeeded_calls": ai_succeeded_calls,
                "search_stages": search_stages,
                "token_strategy": "本地零 Token 优先；未命中时仅用 AI 扩展短检索词",
                "evidence_chars_sent": 0,
                "ai_input_chars_sent": ai_input_chars,
                "estimated_input_tokens": max(1, ai_input_chars // 2)
                if ai_input_chars
                else 0,
                "max_output_tokens": (
                    PLOT_AI_EXPANSION_MAX_TOKENS if ai_calls else 0
                ),
            }

        evidence_text = self._plot_evidence_text(local["results"])
        prompt = f"""用户问题：{question}

以下内容是本地电子书库索引先行召回的少量候选证据。请只依据证据回答，不得把候选推断成确定事实。

{evidence_text}

请用中文简洁回答：
1. 先给结论：有、可能有，或证据不足；
2. 推荐 2～4 本，每本用一句话说明为什么相似；
3. 引用候选编号，例如 [1]；
4. “本地正文证据候选”必须明确建议进一步执行黄金三章扫描或完整拆书确认；
5. 禁止复述大段原文，禁止编造未出现在证据中的角色关系与结局。"""
        ai_calls += 1
        ai_input_chars += len(prompt)
        summary_succeeded = False
        try:
            answer = await ai_service.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是网文剧情检索编辑。你的职责是基于本地召回证据做保守归纳，"
                            "区分关键词相似、母题相似和完整拆书确认。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=PLOT_AI_SUMMARY_MAX_TOKENS,
            )
            summary_succeeded = True
            ai_succeeded_calls += 1
            ai_used = True
        except Exception as exc:
            titles = "、".join(
                f"《{item['title']}》" for item in local["results"][:6]
            )
            answer = (
                f"本地索引找到了 {len(local['results'])} 本候选：{titles}。"
                f"AI 归纳暂时不可用（{str(exc)[:120]}），可先查看证据并执行拆书确认。"
            )
        search_stages.append(
            {
                "stage": "ai_evidence_summary",
                "label": "AI 归纳少量本地证据",
                "ai_used": summary_succeeded,
                "result_count": len(local["results"]),
                "evidence_chars": len(evidence_text),
            }
        )

        evidence_chars = len(evidence_text)
        payload = {
            **local,
            "answer": answer,
            "ai_used": ai_used,
            "ai_calls": ai_calls,
            "ai_succeeded_calls": ai_succeeded_calls,
            "search_stages": search_stages,
            "token_strategy": "本地零 Token 优先；AI 仅扩展短词，并最多归纳 4 本各 1 段证据",
            "evidence_chars_sent": evidence_chars,
            "ai_input_chars_sent": ai_input_chars,
            "estimated_input_tokens": max(1, ai_input_chars // 2),
            "max_output_tokens": (
                PLOT_AI_SUMMARY_MAX_TOKENS
                + (
                    PLOT_AI_EXPANSION_MAX_TOKENS
                    if ai_calls > 1
                    else 0
                )
            ),
            "asked_at": _now(),
        }
        history_dir = project_root / ".webnovel" / "library_questions"
        history_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(
            f"{payload['asked_at']}:{question}".encode("utf-8")
        ).hexdigest()[:12]
        _atomic_write_json(history_dir / f"{digest}.json", payload)
        return payload

    @staticmethod
    def _extract_json_object(raw: str) -> Optional[Dict[str, Any]]:
        """从模型回复中提取单个 JSON 对象。"""
        if not raw:
            return None
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        try:
            value = json.loads(text)
            return value if isinstance(value, dict) else None
        except Exception:
            pass
        start = text.find("{")
        if start < 0:
            return None
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if escaped:
                escaped = False
                continue
            if char == "\\" and in_string:
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start : index + 1])
                        return value if isinstance(value, dict) else None
                    except Exception:
                        return None
        return None

    @staticmethod
    def _project_chapter_blocks(project_root: Path) -> List[Dict[str, Any]]:
        """提取紧凑章节目录和细纲片段，供本地筛选后再交给 AI。"""
        outline_root = project_root / "大纲"
        if not outline_root.exists():
            return []
        blocks: Dict[int, Dict[str, Any]] = {}
        heading_pattern = re.compile(
            r"(?m)^(?P<header>\s*(?:#{1,6}\s*|[-*]\s*|\*{1,2})?"
            r"第\s*0*(?P<chapter>\d+)\s*章[^\n]{0,150})$"
        )
        for path in sorted(outline_root.glob("*.md")):
            if path.name.endswith(".en.md"):
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")[:180000]
            except Exception:
                continue
            matches = list(heading_pattern.finditer(content))
            for position, match in enumerate(matches):
                chapter = int(match.group("chapter"))
                if chapter <= 0:
                    continue
                end = matches[position + 1].start() if position + 1 < len(matches) else len(content)
                block = content[match.start() : min(end, match.start() + 2400)].strip()
                title = re.sub(
                    r"^\s*(?:#{1,6}\s*|[-*]\s*|\*{1,2})?",
                    "",
                    match.group("header"),
                ).strip(" *")
                candidate = {
                    "chapter": chapter,
                    "title": title,
                    "content": block,
                    "source": str(path.relative_to(project_root)),
                }
                current = blocks.get(chapter)
                if current is None or len(block) > len(current["content"]):
                    blocks[chapter] = candidate
        return [blocks[key] for key in sorted(blocks)]

    @staticmethod
    def _written_chapter_max(project_root: Path) -> int:
        chapter_root = project_root / "正文"
        maximum = 0
        if not chapter_root.exists():
            return maximum
        for path in chapter_root.rglob("*.md"):
            match = re.search(r"第0*(\d+)章", path.stem)
            if match:
                maximum = max(maximum, int(match.group(1)))
        return maximum

    def _plot_adaptation_context(
        self,
        project_root: Path,
        question: str,
        selected: List[Dict[str, Any]],
        target_mode: str,
        target_chapter: Optional[int],
        start_chapter: Optional[int],
        end_chapter: Optional[int],
    ) -> Dict[str, Any]:
        terms, motif_tags = self._plot_query_terms(question)
        chapters = self._project_chapter_blocks(project_root)
        written_max = self._written_chapter_max(project_root)
        forced = {
            chapter
            for chapter in (target_chapter, start_chapter, end_chapter)
            if isinstance(chapter, int) and chapter > 0
        }
        scored: List[tuple[int, int, Dict[str, Any]]] = []
        for item in chapters:
            searchable = item["title"] + "\n" + item["content"]
            score = sum(5 for term in terms if term in searchable)
            score += sum(8 for tag in motif_tags if tag in searchable)
            if item["chapter"] in forced:
                score += 1000
            if item["chapter"] > written_max:
                score += 6
            distance = (
                min(abs(item["chapter"] - value) for value in forced)
                if forced
                else abs(item["chapter"] - max(1, written_max + 1))
            )
            scored.append((score, -distance, item))
        scored.sort(key=lambda row: (row[0], row[1], -row[2]["chapter"]), reverse=True)
        detail_items = [row[2] for row in scored[:16]]
        detail_items.sort(key=lambda item: item["chapter"])

        catalog = "\n".join(
            f"- 第{item['chapter']}章：{item['title'][:80]}"
            for item in chapters[:400]
        )
        details = "\n\n".join(
            f"### 第{item['chapter']}章（{item['source']}）\n"
            f"{item['content'][:650]}"
            for item in detail_items
        )
        evidence = "\n\n".join(
            (
                f"《{item['title']}》｜{item['confirmation_level']}｜"
                f"母题：{'、'.join(item.get('motif_tags') or []) or '未标注'}\n"
                + "\n".join(
                    f"- {entry['location']}：{entry['excerpt']}"
                    for entry in (item.get("evidence") or [])[:2]
                )
            )
            for item in selected[:3]
        )
        return {
            "profile": self.project_profile(project_root),
            "terms": terms,
            "motif_tags": motif_tags,
            "written_max": written_max,
            "chapter_catalog": catalog[:18000],
            "chapter_details": details[:13000],
            "evidence": evidence[:7000],
            "target_mode": target_mode,
        }

    @staticmethod
    def _normalize_plot_plan(
        raw: Optional[Dict[str, Any]],
        *,
        question: str,
        requirement: str,
        target_mode: str,
        target_chapter: Optional[int],
        start_chapter: Optional[int],
        end_chapter: Optional[int],
        written_max: int,
    ) -> Dict[str, Any]:
        raw = raw or {}
        recommended = raw.get("recommended_target")
        if not isinstance(recommended, dict):
            recommended = {}

        def safe_int(value: Any, fallback: int) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return fallback

        fallback_chapter = max(1, written_max + 1)
        if target_mode == "specified_chapter":
            chapter_start = chapter_end = safe_int(
                target_chapter, fallback_chapter
            )
        elif target_mode == "plot_arc" and start_chapter:
            chapter_start = safe_int(start_chapter, fallback_chapter)
            chapter_end = safe_int(end_chapter, chapter_start)
        else:
            chapter_start = safe_int(
                recommended.get("chapter_start")
                or recommended.get("chapter")
                or fallback_chapter,
                fallback_chapter,
            )
            chapter_end = safe_int(
                recommended.get("chapter_end"), chapter_start
            )
        chapter_start = max(1, chapter_start)
        chapter_end = max(chapter_start, chapter_end)
        chapter_end = min(chapter_end, chapter_start + 11)

        beats = raw.get("chapter_beats")
        if not isinstance(beats, list):
            beats = []
        normalized_beats = []
        for item in beats[:16]:
            if not isinstance(item, dict):
                continue
            chapter = safe_int(item.get("chapter"), chapter_start)
            if not chapter_start <= chapter <= chapter_end:
                continue
            normalized_beats.append(
                {
                    "chapter": chapter,
                    "role": str(item.get("role") or "推进")[:40],
                    "beat": str(item.get("beat") or "")[:800],
                    "hook": str(item.get("hook") or "")[:500],
                    "payoff": str(item.get("payoff") or "")[:500],
                }
            )
        if not normalized_beats:
            span = list(range(chapter_start, chapter_end + 1))
            for index, chapter in enumerate(span):
                if len(span) == 1:
                    role = "铺垫—转折—兑现"
                elif index == 0:
                    role = "铺垫与期待"
                elif index == len(span) - 1:
                    role = "兑现与新钩子"
                else:
                    role = "加压与关系转折"
                normalized_beats.append(
                    {
                        "chapter": chapter,
                        "role": role,
                        "beat": requirement or question,
                        "hook": "留下具体信息差、选择压力或下一步行动",
                        "payoff": "用当前项目角色与设定完成可见情绪兑现",
                    }
                )

        technique = raw.get("technique_route")
        if not isinstance(technique, dict):
            technique = {}
        return {
            "judgment": str(raw.get("judgment") or "可作为候选剧情素材，但需彻底换素材重组")[:200],
            "reason": str(raw.get("reason") or "按当前项目设定迁移母题和功能位，不复刻来源情节。")[:1200],
            "adaptation_title": str(raw.get("adaptation_title") or f"剧情素材：{question[:30]}")[:120],
            "material_summary": str(raw.get("material_summary") or requirement or question)[:1800],
            "emotional_goal": str(raw.get("emotional_goal") or "形成铺垫、转折、兑现与新期待")[:800],
            "originality_boundary": str(
                raw.get("originality_boundary")
                or "只学习母题、节奏和功能位；禁止复制来源作品的人名、设定、专名、句子及具体桥段。"
            )[:1000],
            "recommended_target": {
                "mode": target_mode,
                "chapter_start": chapter_start,
                "chapter_end": chapter_end,
                "label": str(
                    recommended.get("label")
                    or (
                        f"第{chapter_start}章"
                        if chapter_start == chapter_end
                        else f"第{chapter_start}～{chapter_end}章剧情段"
                    )
                )[:120],
            },
            "technique_route": {
                "hook": str(technique.get("hook") or "用具体信息差或选择压力收尾")[:700],
                "payoff": str(technique.get("payoff") or "可指认铺垫 → 明确释放 → 角色与局势余波")[:700],
                "expectation": str(technique.get("expectation") or "新增一笔可等待的期待债")[:700],
                "rhythm": str(technique.get("rhythm") or "铺垫—加压—转折—兑现—冷却/新钩子")[:700],
            },
            "chapter_beats": normalized_beats,
        }

    @staticmethod
    def _plot_adoption_dir(project_root: Path) -> Path:
        return project_root / ".webnovel" / "library_plot_adoptions"

    @staticmethod
    def _plot_preview_path(project_root: Path, plan_id: str) -> Path:
        return (
            ElectronicLibraryService._plot_adoption_dir(project_root)
            / f"{plan_id}.preview.json"
        )

    @staticmethod
    def _find_project_chapter(
        project_root: Path, chapter: int
    ) -> Optional[Path]:
        """只在当前项目正文中定位中文主章节，避开隐藏目录和翻译副本。"""
        chapter_root = project_root / "正文"
        if not chapter_root.exists():
            return None
        matches: List[Path] = []
        for path in chapter_root.rglob("*.md"):
            try:
                relative = path.relative_to(chapter_root)
            except ValueError:
                continue
            if any(part.startswith(".") for part in relative.parts):
                continue
            if path.name.endswith(".en.md"):
                continue
            match = re.search(r"第0*(\d+)章", path.stem)
            if match and int(match.group(1)) == chapter:
                matches.append(path)
        return sorted(matches, key=lambda item: (len(item.parts), str(item)))[0] if matches else None

    @staticmethod
    def _chapter_content_hash(content: str) -> str:
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _chapter_diff(chapter: int, before: str, after: str) -> str:
        return "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"第{chapter}章（原文）",
                tofile=f"第{chapter}章（融入后）",
                n=3,
            )
        )

    @staticmethod
    def _insert_after_anchor(
        original: str, anchor: str, insert_text: str
    ) -> str:
        anchor = (anchor or "").strip()
        insert_text = (insert_text or "").strip()
        if not anchor or not insert_text:
            raise ValueError("AI 未返回可验证的插入锚点或新增正文")
        positions = [match.start() for match in re.finditer(re.escape(anchor), original)]
        if len(positions) != 1:
            raise ValueError("AI 返回的插入锚点无法在原文中唯一定位，请重新生成预览")
        end = positions[0] + len(anchor)
        separator_before = "\n\n"
        separator_after = "\n\n" if end < len(original) and not original[end:].startswith("\n") else ""
        return (
            original[:end]
            + separator_before
            + insert_text
            + separator_after
            + original[end:]
        )

    async def plan_plot_adaptation(
        self,
        project_root: Path,
        *,
        question: str,
        catalog_ids: List[int],
        requirement: str,
        target_mode: str,
        ai_service: Any,
        target_chapter: Optional[int] = None,
        start_chapter: Optional[int] = None,
        end_chapter: Optional[int] = None,
    ) -> Dict[str, Any]:
        project_root = project_root.expanduser().resolve()
        if not (project_root / ".webnovel").exists():
            raise ValueError("当前路径不是有效的小说项目")
        if target_mode not in {"specified_chapter", "ai_recommended", "plot_arc"}:
            raise ValueError("未知的剧情融入模式")
        if target_mode == "specified_chapter" and not target_chapter:
            raise ValueError("请填写要融入的章节")
        if target_mode == "plot_arc" and start_chapter and end_chapter and end_chapter < start_chapter:
            raise ValueError("剧情段结束章节不能早于开始章节")

        local = self.search_plot_index(project_root, question, limit=12)
        selected_ids = {int(value) for value in catalog_ids[:3]}
        selected = [
            item for item in local.get("results", [])
            if not selected_ids or int(item["catalog_id"]) in selected_ids
        ][:3]
        if not selected:
            raise ValueError("当前提问结果中没有可用于融入的剧情证据")

        context = self._plot_adaptation_context(
            project_root,
            question,
            selected,
            target_mode,
            target_chapter,
            start_chapter,
            end_chapter,
        )
        target_instruction = {
            "specified_chapter": f"必须落在第{int(target_chapter or 1)}章，只规划这一章。",
            "ai_recommended": "请根据已写进度与章节细纲，判断最适合的一个章节或连续小区间。",
            "plot_arc": (
                f"规划一段连续剧情；用户范围为第{start_chapter or '待判断'}"
                f"～{end_chapter or '待判断'}章。"
            ),
        }[target_mode]
        prompt = f"""你正在执行 oh-story / story-long-write 的“剧情素材重组与章节定位”步骤。

用户问题：{question}
用户额外需求：{requirement or '未补充，由你按项目基调判断'}
目标模式：{target_mode}
目标约束：{target_instruction}

当前项目：
- 书名：{context['profile'].get('title')}
- 题材：{context['profile'].get('genre')}
- 子风格：{context['profile'].get('substyle')}
- 基调：{'、'.join(context['profile'].get('tone_tags') or [])}
- 已完成正文最大章节：{context['written_max']}

章节目录：
{context['chapter_catalog'] or '暂无可解析章节目录'}

本地筛选后的相关细纲：
{context['chapter_details'] or '暂无详细细纲'}

电子书库的有限证据：
{context['evidence']}

任务：
1. 只抽象母题、冲突功能、节奏、情绪兑现、钩子与期待债；禁止复制来源作品的人名、设定、专名、句子及具体桥段。
2. 判断该素材是否适合当前项目，说明理由；如果会破坏人物动机、力量体系、既有伏笔或阶段节奏，应给出改造边界。
3. 选择章节位置时，优先匹配当前项目已有细纲；不得把后期底牌和关系结论提前释放。
4. 每章只给功能节拍，使用当前项目的角色/设定占位描述，不代写来源剧情。
5. 严格只返回一个 JSON 对象，不要代码块和解释。结构：
{{
  "judgment": "适合/需改造/不建议 + 简短结论",
  "reason": "项目适配判断",
  "adaptation_title": "重组后的剧情素材标题",
  "material_summary": "换成本项目逻辑后的剧情方案",
  "emotional_goal": "读者情绪前状态→后状态",
  "originality_boundary": "反抄袭与设定边界",
  "recommended_target": {{
    "chapter_start": 12,
    "chapter_end": 14,
    "label": "为什么放在这里"
  }},
  "technique_route": {{
    "hook": "钩子怎么做",
    "payoff": "爽点/情绪兑现怎么做",
    "expectation": "期待债怎么建立",
    "rhythm": "节奏链"
  }},
  "chapter_beats": [
    {{"chapter": 12, "role": "铺垫/加压/转折/兑现", "beat": "本章功能节拍", "hook": "章尾钩子", "payoff": "本章兑现"}}
  ]
}}"""
        raw_plan: Optional[Dict[str, Any]] = None
        ai_used = False
        ai_error = ""
        try:
            reply = await ai_service.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是 oh-story 长篇网文剧情架构编辑。先保护读者契约、人物动机、"
                            "设定一致性与原创边界，再把检索素材重组为可执行章节功能位。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.25,
                max_tokens=2400,
            )
            raw_plan = self._extract_json_object(reply)
            ai_used = raw_plan is not None
            if raw_plan is None:
                ai_error = "AI 返回格式无法解析，已生成保守兜底方案"
        except Exception as exc:
            ai_error = f"AI 暂不可用，已生成保守兜底方案：{str(exc)[:160]}"

        normalized = self._normalize_plot_plan(
            raw_plan,
            question=question,
            requirement=requirement,
            target_mode=target_mode,
            target_chapter=target_chapter,
            start_chapter=start_chapter,
            end_chapter=end_chapter,
            written_max=context["written_max"],
        )
        plan_id = uuid.uuid4().hex[:12]
        created_at = _now()
        payload = {
            "id": plan_id,
            "status": "planned",
            "skill": "story-long-write",
            "skill_stage": "剧情素材重组与章节定位",
            "question": question,
            "requirement": requirement,
            "selected_sources": [
                {
                    "catalog_id": item["catalog_id"],
                    "title": item["title"],
                    "author": item["author"],
                    "confirmation_level": item["confirmation_level"],
                    "motif_tags": item.get("motif_tags") or [],
                    "evidence": (item.get("evidence") or [])[:2],
                }
                for item in selected
            ],
            **normalized,
            "ai_used": ai_used,
            "ai_message": ai_error,
            "token_strategy": "本地索引先筛选，AI 仅接收最多 3 本×2 段证据及压缩后的相关细纲",
            "estimated_input_tokens": max(
                1,
                (
                    len(context["evidence"])
                    + len(context["chapter_catalog"])
                    + len(context["chapter_details"])
                )
                // 2,
            ),
            "created_at": created_at,
        }
        _atomic_write_json(self._plot_adoption_dir(project_root) / f"{plan_id}.json", payload)
        return payload

    async def preview_plot_adaptation(
        self,
        project_root: Path,
        plan_id: str,
        apply_mode: str,
        ai_service: Any,
    ) -> Dict[str, Any]:
        """为已有正文生成可审阅差异，不在预览阶段写正文。"""
        project_root = project_root.expanduser().resolve()
        if apply_mode not in {"local_insert", "replace_chapter"}:
            raise ValueError("未知的正文融入方式")
        if not re.fullmatch(r"[a-f0-9]{12}", plan_id):
            raise KeyError("剧情融入方案不存在")
        plan_path = self._plot_adoption_dir(project_root) / f"{plan_id}.json"
        plan = _read_json(plan_path, {})
        if not plan:
            raise KeyError("剧情融入方案不存在")

        target = plan.get("recommended_target") or {}
        start = int(target.get("chapter_start") or 1)
        end = int(target.get("chapter_end") or start)
        beats_by_chapter = {
            int(item.get("chapter")): item
            for item in (plan.get("chapter_beats") or [])
            if isinstance(item, dict) and item.get("chapter")
        }
        previews: List[Dict[str, Any]] = []
        for chapter in range(start, end + 1):
            chapter_path = self._find_project_chapter(project_root, chapter)
            if chapter_path is None:
                previews.append(
                    {
                        "chapter": chapter,
                        "status": "unwritten",
                        "message": "该章尚未写作，将绑定到后续章节生成/重写链路",
                    }
                )
                continue

            original = chapter_path.read_text(encoding="utf-8", errors="replace")
            if len(original) > 50000:
                previews.append(
                    {
                        "chapter": chapter,
                        "status": "skipped",
                        "message": "章节超过 5 万字符，为避免截断覆盖，未生成正文预览",
                    }
                )
                continue
            beat = beats_by_chapter.get(chapter, {})
            mode_instruction = (
                """只生成一段需要新增的正文，并从原文中原样复制一个唯一的插入锚点。
返回 JSON：{"insert_after":"原文中连续 20～120 个字符的唯一原句",
"insert_text":"要插入的新正文","revision_note":"改动理由"}。
除 insert_text 外不要重写原文。"""
                if apply_mode == "local_insert"
                else """重写整章正文，但必须保留原章已经成立的事实、人物动机、物理规则、
专名、伏笔与章间承接，只把本次素材重组后的功能位自然融入。
返回 JSON：{"revised_content":"完整 Markdown 章节正文","revision_note":"改动理由"}。"""
            )
            prompt = f"""你正在执行 story-long-write 的“剧情素材融入已有正文”步骤。

项目：{plan.get('adaptation_title')}
用户需求：{plan.get('requirement') or plan.get('question')}
重组方案：{plan.get('material_summary')}
情绪目标：{plan.get('emotional_goal')}
原创边界：{plan.get('originality_boundary')}
本章功能节拍：{json.dumps(beat, ensure_ascii=False)}
节奏/兑现/期待/钩子：{json.dumps(plan.get('technique_route') or {}, ensure_ascii=False)}

硬规则：
1. 只能学习抽象母题、冲突功能和节奏，禁止复制来源作品的人名、设定、专名、句子和具体桥段。
2. 不得改变当前项目既有人物身份、力量体系、时间线、物品归属和已埋伏笔。
3. 新内容必须由角色当下目标与现场判断推动，不能用作者结论硬转折。
4. 保持原文语言、Markdown 结构、叙事视角和章节标题。
5. 严格只返回一个 JSON 对象，不要代码块或解释。

处理方式：
{mode_instruction}

第{chapter}章原文：
{original}
"""
            try:
                reply = await ai_service.chat(
                    [
                        {
                            "role": "system",
                            "content": (
                                "你是 story-long-write 的长篇网文正文编辑。"
                                "先保护连续性、原创边界和人物动机，再做最小必要改写。"
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.25,
                    max_tokens=16000 if apply_mode == "replace_chapter" else 5000,
                )
                result = self._extract_json_object(reply)
                if not result:
                    raise ValueError("AI 返回格式无法解析")
                if apply_mode == "local_insert":
                    revised = self._insert_after_anchor(
                        original,
                        str(result.get("insert_after") or ""),
                        str(result.get("insert_text") or ""),
                    )
                else:
                    revised = str(result.get("revised_content") or "").strip()
                    if not revised:
                        raise ValueError("AI 未返回完整重写正文")
                if revised.strip() == original.strip():
                    raise ValueError("AI 未生成有效改动")
                previews.append(
                    {
                        "chapter": chapter,
                        "status": "ready",
                        "path": str(chapter_path.relative_to(project_root)),
                        "source_hash": self._chapter_content_hash(original),
                        "revised_content": revised,
                        "revision_note": str(result.get("revision_note") or "")[:800],
                        "word_count_before": len(original),
                        "word_count_after": len(revised),
                        "diff": self._chapter_diff(chapter, original, revised),
                    }
                )
            except Exception as exc:
                previews.append(
                    {
                        "chapter": chapter,
                        "status": "error",
                        "message": str(exc)[:500],
                    }
                )

        ready_count = sum(item["status"] == "ready" for item in previews)
        preview_payload = {
            "plan_id": plan_id,
            "apply_mode": apply_mode,
            "status": "ready" if ready_count else "binding_only",
            "ready_count": ready_count,
            "unwritten_count": sum(
                item["status"] == "unwritten" for item in previews
            ),
            "chapters": previews,
            "created_at": _now(),
        }
        _atomic_write_json(
            self._plot_preview_path(project_root, plan_id), preview_payload
        )
        plan.update(
            {
                "status": "previewed",
                "preview_mode": apply_mode,
                "previewed_at": preview_payload["created_at"],
                "preview_ready_count": ready_count,
            }
        )
        _atomic_write_json(plan_path, plan)

        public_preview = json.loads(json.dumps(preview_payload, ensure_ascii=False))
        for item in public_preview["chapters"]:
            item.pop("revised_content", None)
            if len(item.get("diff") or "") > 120000:
                item["diff"] = item["diff"][:120000] + "\n\n……差异过长，已截断显示"
        return public_preview

    def commit_plot_adaptation(
        self, project_root: Path, plan_id: str
    ) -> Dict[str, Any]:
        """确认预览后写入正文；写入前校验哈希并创建可恢复备份。"""
        project_root = project_root.expanduser().resolve()
        if not re.fullmatch(r"[a-f0-9]{12}", plan_id):
            raise KeyError("剧情融入方案不存在")
        preview = _read_json(
            self._plot_preview_path(project_root, plan_id), {}
        )
        if not preview:
            raise ValueError("请先生成正文差异预览")
        ready = [
            item
            for item in (preview.get("chapters") or [])
            if item.get("status") == "ready"
        ]

        resolved: List[tuple[Dict[str, Any], Path, str]] = []
        for item in ready:
            relative = Path(str(item.get("path") or ""))
            chapter_path = (project_root / relative).resolve()
            if not _is_within(chapter_path, project_root / "正文"):
                raise ValueError("正文预览路径越界，已拒绝写入")
            if not chapter_path.exists():
                raise ValueError(f"第{item.get('chapter')}章已不存在，请重新预览")
            current = chapter_path.read_text(encoding="utf-8", errors="replace")
            if self._chapter_content_hash(current) != item.get("source_hash"):
                raise ValueError(
                    f"第{item.get('chapter')}章在预览后已被修改，请重新生成差异预览"
                )
            revised = str(item.get("revised_content") or "")
            if not revised.strip():
                raise ValueError(f"第{item.get('chapter')}章预览正文为空")
            resolved.append((item, chapter_path, current))

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_root = (
            project_root
            / ".webnovel"
            / "backups"
            / "library_plot_adaptations"
            / f"{timestamp}-{plan_id}"
        )
        written: List[Dict[str, Any]] = []
        try:
            for item, chapter_path, current in resolved:
                relative = chapter_path.relative_to(project_root / "正文")
                backup_path = backup_root / relative
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                backup_path.write_text(current, encoding="utf-8")
                temp_path = chapter_path.with_suffix(
                    chapter_path.suffix + f".{plan_id}.tmp"
                )
                temp_path.write_text(
                    str(item["revised_content"]), encoding="utf-8"
                )
                temp_path.replace(chapter_path)
                written.append(
                    {
                        "chapter": item.get("chapter"),
                        "path": str(chapter_path.relative_to(project_root)),
                        "backup_path": str(backup_path.relative_to(project_root)),
                    }
                )
        except Exception:
            for item, chapter_path, current in resolved:
                if any(
                    entry["chapter"] == item.get("chapter") for entry in written
                ):
                    chapter_path.write_text(current, encoding="utf-8")
            raise

        applied = self.apply_plot_adaptation(project_root, plan_id)
        applied.update(
            {
                "status": "applied_to_text",
                "text_applied_at": _now(),
                "apply_mode": preview.get("apply_mode"),
                "written_chapters": written,
                "binding_only_chapters": [
                    item.get("chapter")
                    for item in (preview.get("chapters") or [])
                    if item.get("status") == "unwritten"
                ],
            }
        )
        plan_path = self._plot_adoption_dir(project_root) / f"{plan_id}.json"
        _atomic_write_json(plan_path, applied)
        return applied

    def apply_plot_adaptation(self, project_root: Path, plan_id: str) -> Dict[str, Any]:
        project_root = project_root.expanduser().resolve()
        if not re.fullmatch(r"[a-f0-9]{12}", plan_id):
            raise KeyError("剧情融入方案不存在")
        plan_path = self._plot_adoption_dir(project_root) / f"{plan_id}.json"
        payload = _read_json(plan_path, {})
        if not payload:
            raise KeyError("剧情融入方案不存在")
        if payload.get("status") in {"applied", "applied_to_text"}:
            return payload

        target = payload.get("recommended_target") or {}
        start = int(target.get("chapter_start") or 1)
        end = int(target.get("chapter_end") or start)
        material_root = project_root / "剧情素材" / "电子书库"
        material_root.mkdir(parents=True, exist_ok=True)
        material_path = material_root / (
            f"{plan_id}_{_safe_name(payload.get('adaptation_title') or '剧情素材')}.md"
        )
        beats = "\n".join(
            (
                f"### 第{item.get('chapter')}章｜{item.get('role')}\n"
                f"- 功能节拍：{item.get('beat')}\n"
                f"- 兑现：{item.get('payoff')}\n"
                f"- 钩子：{item.get('hook')}"
            )
            for item in payload.get("chapter_beats") or []
        )
        source_lines = "\n".join(
            f"- 《{item.get('title')}》：{item.get('confirmation_level')}"
            for item in payload.get("selected_sources") or []
        )
        technique = payload.get("technique_route") or {}
        material_path.write_text(
            f"""# {payload.get('adaptation_title')}

> 状态：已绑定到第{start}～{end}章的 oh-story 章节生成链路
> 技能路由：story-long-write / 剧情素材重组与章节定位

## 用户需求

{payload.get('requirement') or payload.get('question')}

## AI 辅助判断

{payload.get('judgment')}

{payload.get('reason')}

## 重组后的剧情方案

{payload.get('material_summary')}

## 情绪与技法

- 情绪目标：{payload.get('emotional_goal')}
- 节奏：{technique.get('rhythm')}
- 爽感/情绪兑现：{technique.get('payoff')}
- 期待债：{technique.get('expectation')}
- 钩子：{technique.get('hook')}

## 章节功能节拍

{beats}

## 原创边界

{payload.get('originality_boundary')}

## 证据来源（仅供追溯，不得复制）

{source_lines}
""",
            encoding="utf-8",
        )
        payload.update(
            {
                "status": "applied",
                "applied_at": _now(),
                "material_path": str(material_path.relative_to(project_root)),
            }
        )
        _atomic_write_json(plan_path, payload)
        return payload

    def list_plot_adaptations(self, project_root: Path) -> List[Dict[str, Any]]:
        root = self._plot_adoption_dir(project_root)
        if not root.exists():
            return []
        items = [
            value
            for path in root.glob("*.json")
            if not path.name.endswith(".preview.json")
            if (value := _read_json(path, {}))
        ]
        items.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return items

    def get_book(self, book_id: int) -> Dict[str, Any]:
        using_mysql = (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        )
        if using_mysql:
            row = self.mysql_catalog.get_done_book(book_id)
            if not row:
                raise KeyError("作品不存在或尚未下载完成")
            catalog_item = self._materialize_mysql_catalog_row(row)
        else:
            with self._catalog_connection() as conn:
                columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_info(books)")
                }
                book_status_select = (
                    "book_status"
                    if "book_status" in columns
                    else (
                        "CASE WHEN LOWER(COALESCE(source_id, '')) "
                        "LIKE 'fanqie-%' "
                        "OR LOWER(COALESCE(source_id, '')) LIKE 'xbiquge-%' "
                        "OR LOWER(COALESCE(source_id, '')) LIKE 'shubaow-%' "
                        "OR LOWER(COALESCE(source_id, '')) LIKE 'linovelib-%' "
                        "THEN '连载中' ELSE '已完结' END AS book_status"
                    )
                )
                detail_url_select = (
                    "detail_url"
                    if "detail_url" in columns
                    else "'' AS detail_url"
                )
                row = conn.execute(
                    f"""
                    SELECT id AS catalog_id,
                           COALESCE(source_id, id) AS source_id,
                           {detail_url_select},
                           title, author, category, expected_size,
                           output_path AS source_path,
                           COALESCE(bytes, 0) AS source_bytes,
                           sha256, updated_at, {book_status_select}
                    FROM books WHERE id=? AND status='done'
                    """,
                    (book_id,),
                ).fetchone()
            if not row:
                raise KeyError("作品不存在或尚未下载完成")
            catalog_item = dict(row)
        catalog_item["book_status"] = self.normalize_book_status(
            catalog_item.get("book_status"),
            default=(
                SERIALIZATION_STATUS_ONGOING
                if str(catalog_item.get("source_id") or "").lower().startswith(
                    ("fanqie-", "xbiquge-", "shubaow-", "linovelib-")
                )
                else SERIALIZATION_STATUS_COMPLETED
            ),
        )
        item = dict(catalog_item)
        if using_mysql:
            indexed = self.mysql_catalog.metadata_for_ids([book_id]).get(
                int(book_id)
            )
            if indexed:
                indexed_data = dict(indexed)
                for key in (
                    "genre_tags",
                    "tone_tags",
                    "primary_tone_tags",
                    "secondary_tone_tags",
                ):
                    indexed_data[key] = _read_json_text(
                        indexed_data.get(key),
                        [],
                    )
                indexed_data["keyword_counts"] = _read_json_value(
                    indexed_data.get("keyword_counts"),
                    {},
                )
                item.update(indexed_data)
                for key in (
                    "catalog_id",
                    "source_id",
                    "detail_url",
                    "title",
                    "author",
                    "category",
                    "source_path",
                    "source_bytes",
                    "sha256",
                    "updated_at",
                    "book_status",
                    "body_object_key",
                    "cover_object_key",
                    "row_version",
                ):
                    item[key] = catalog_item.get(key)
                item["indexed"] = True
        elif self.index_path.exists():
            with self._index_connection() as conn:
                indexed = conn.execute(
                    "SELECT * FROM library_index WHERE catalog_id=?", (book_id,)
                ).fetchone()
            if indexed:
                indexed_data = dict(indexed)
                for key in ("genre_tags", "tone_tags"):
                    indexed_data[key] = _read_json_text(indexed_data.get(key), [])
                item.update(indexed_data)
                # 特征来自派生索引；正文路径、文件大小与目录元数据必须仍以
                # 当前书目后端为准，避免旧索引路径让真实下载文件失效。
                for key in (
                    "catalog_id",
                    "source_id",
                    "detail_url",
                    "title",
                    "author",
                    "category",
                    "source_path",
                    "source_bytes",
                    "sha256",
                    "updated_at",
                    "book_status",
                ):
                    item[key] = catalog_item.get(key)
                item["indexed"] = True
        self._apply_content_metrics([item])
        source_path = Path(item["source_path"]).expanduser().resolve()
        if not (
            _is_within(source_path, self.books_root)
            or _is_within(
                source_path,
                self.infrastructure_settings.object_root.resolve(),
            )
        ):
            raise ValueError("作品路径不在电子书库范围内")
        item["source_exists"] = source_path.is_file()
        if item["source_exists"]:
            reader_index = self._reader_index(book_id, source_path)
            latest = next(
                (
                    chapter
                    for chapter in reversed(reader_index.get("chapters") or [])
                    if chapter.get("kind") != "intro"
                ),
                None,
            )
            if latest:
                item["latest_chapter"] = {
                    "label": str(latest.get("label") or ""),
                    "title": str(latest.get("title") or ""),
                    "chapter_index": latest.get("chapter_index"),
                }
        item["cover_url"] = self._cover_for_catalog_item(item)
        return item

    def _reader_index_path(self, book_id: int) -> Path:
        return self.reader_index_root / f"{int(book_id)}.json"

    @staticmethod
    def _clean_reader_title(value: str) -> str:
        return re.sub(r"^[\s:：、，,；;。—\-·.]+", "", value or "").strip()[:120]

    @staticmethod
    def _reader_word_count(content: str) -> int:
        """Return the exact count used by the reader UI.

        The reader historically labels this value as ``字`` and counts every
        visible non-whitespace character.  Keeping that contract makes the
        per-chapter value and the catalog total mechanically consistent.
        """

        return len(re.sub(r"\s+", "", content))

    @classmethod
    def _clean_reader_section_content(cls, content: str) -> str:
        content = content.lstrip("\ufeff\r\n")
        # Some sources repeat the structural heading once with indentation.
        # Strip at most two heading lines, exactly as the reader presents it.
        for _ in range(2):
            first_line, separator, remainder = content.partition("\n")
            stripped = first_line.strip()
            if not separator or not (
                READER_HEADING_LINE.fullmatch(stripped)
                or READER_NUMERIC_HEADING_LINE.fullmatch(stripped)
                or READER_BRACKET_NUMERIC_HEADING_LINE.fullmatch(stripped)
                or READER_BODY_NUMBER_HEADING_LINE.fullmatch(stripped)
                or READER_SUFFIX_NUMBER_HEADING_LINE.fullmatch(stripped)
                or READER_DECORATED_HEADING_LINE.fullmatch(stripped)
                or READER_SPECIAL_HEADING_LINE.fullmatch(stripped)
            ):
                break
            content = remainder.lstrip("\r\n")
        return content.rstrip()

    @classmethod
    def _reader_heading_candidates(
        cls,
        source_path: Path,
    ) -> tuple[List[Dict[str, Any]], str, List[int]]:
        """识别正文真实章节行，并避免把正文内重复标题当成新章节。

        txt80 历史文件至少存在两类目录格式：
        1. ``第一章 标题`` / ``第1章 标题``；
        2. ``001 标题``，部分文件会在后段额外插入一份 ``第738章`` 标题。

        旧实现只识别第一类，而且允许任意缩进，导致数字目录整段漏失、正文
        内四空格重复标题又被二次切章。这里先分别收集两类候选，再用连续性
        判断主目录格式；可靠的数字序列优先覆盖源站后加的零散标准标题。
        """

        named: List[Dict[str, Any]] = []
        numeric_by_style: Dict[str, List[Dict[str, Any]]] = {
            "numeric": [],
            "bracket_number": [],
            "body_number": [],
            "suffix_number": [],
        }
        decorated_unnumbered: List[Dict[str, Any]] = []
        with source_path.open("rb") as handle:
            while True:
                offset = handle.tell()
                raw_line = handle.readline()
                if not raw_line:
                    break
                decoded = raw_line.decode("utf-8", errors="replace").lstrip("\ufeff")
                line = decoded.rstrip("\r\n")
                stripped = line.strip()
                if not stripped or len(stripped) > 220:
                    continue

                # 站点整理文本常把章节名在正文开头再缩进复述一遍。结构标题
                # 通常顶格或至多两个空格，四空格以上的行按正文处理。
                leading_spaces = len(line) - len(line.lstrip(" \t"))
                if leading_spaces > 2:
                    continue

                match = READER_HEADING_LINE.fullmatch(stripped)
                if match:
                    title_prefix = stripped[match.end("label") :]
                    if (
                        match.group("unit") == "节"
                        and title_prefix
                        and not (
                            title_prefix[0].isspace()
                            or title_prefix[0] in ":：、，,；;。—-·."
                        )
                    ):
                        # ``第一节课她就……`` is normal prose, not a
                        # section heading.  Chapter/回 forms without a
                        # separator remain supported for legacy TXT files.
                        continue
                    named.append(
                        {
                            "offset": offset,
                            "label": re.sub(r"\s+", "", match.group("label")),
                            "title": cls._clean_reader_title(match.group("title")),
                        }
                    )
                    continue

                match = READER_SPECIAL_HEADING_LINE.fullmatch(stripped)
                if match:
                    named.append(
                        {
                            "offset": offset,
                            "label": re.sub(r"\s+", "", match.group("label")),
                            "title": cls._clean_reader_title(match.group("title")),
                            "special": True,
                        }
                    )
                    continue

                match = READER_BODY_NUMBER_HEADING_LINE.fullmatch(stripped)
                if match:
                    chapter_number = int(match.group("number"))
                    prefix = cls._clean_reader_title(match.group("prefix"))
                    extra_title = cls._clean_reader_title(match.group("title"))
                    numeric_by_style["body_number"].append(
                        {
                            "offset": offset,
                            "number": chapter_number,
                            "label": f"第{chapter_number}章",
                            "title": extra_title or f"{prefix}{chapter_number}",
                            "high_confidence": True,
                        }
                    )
                    continue

                match = READER_BRACKET_NUMERIC_HEADING_LINE.fullmatch(stripped)
                if match:
                    chapter_number = int(match.group("number"))
                    numeric_by_style["bracket_number"].append(
                        {
                            "offset": offset,
                            "number": chapter_number,
                            "label": f"第{chapter_number}章",
                            "title": cls._clean_reader_title(
                                match.group("title")
                            ),
                            "high_confidence": True,
                        }
                    )
                    continue

                match = READER_NUMERIC_HEADING_LINE.fullmatch(stripped)
                if match:
                    chapter_number = int(match.group("number"))
                    title = cls._clean_reader_title(match.group("title"))
                    structural_marker = bool(
                        re.search(r"(?:章|回|节|[☆★◆◇●○◎※▶▷])", stripped)
                    )
                    numeric_by_style["numeric"].append(
                        {
                            "offset": offset,
                            "number": chapter_number,
                            "label": f"第{chapter_number}章",
                            "title": title,
                            "high_confidence": bool(
                                structural_marker or title
                            ),
                        }
                    )
                    continue

                match = READER_SUFFIX_NUMBER_HEADING_LINE.fullmatch(stripped)
                if match:
                    number_text = (
                        match.group("bracket_number")
                        or match.group("number")
                    )
                    chapter_number = int(number_text)
                    base_title = cls._clean_reader_title(match.group("title"))
                    if base_title and not base_title.isdigit():
                        display_title = (
                            f"{base_title}【{chapter_number}】"
                            if match.group("bracket_number")
                            else f"{base_title}{chapter_number}"
                        )
                        numeric_by_style["suffix_number"].append(
                            {
                                "offset": offset,
                                "number": chapter_number,
                                "label": f"第{chapter_number}章",
                                "title": display_title,
                                "base_title": base_title,
                                "high_confidence": bool(
                                    match.group("bracket_number")
                                ),
                            }
                        )
                    continue

                match = READER_DECORATED_HEADING_LINE.fullmatch(stripped)
                if match:
                    title = cls._clean_reader_title(match.group("title"))
                    if title:
                        decorated_unnumbered.append(
                            {
                                "offset": offset,
                                "title": title,
                            }
                        )

        deduped_named: List[Dict[str, Any]] = []
        for item in named:
            previous = deduped_named[-1] if deduped_named else None
            if (
                previous
                and previous["label"] == item["label"]
                and previous["title"] == item["title"]
                and int(item["offset"]) - int(previous["offset"]) <= 4096
            ):
                continue
            deduped_named.append(item)

        def candidate_runs(
            candidates: List[Dict[str, Any]],
        ) -> List[List[Dict[str, Any]]]:
            """Return plausible monotonic chapter runs for one source style."""

            runs: List[List[Dict[str, Any]]] = []
            for start_index, first in enumerate(candidates):
                if int(first["number"]) > 5:
                    continue
                run = [first]
                previous_number = int(first["number"])
                tail = candidates[start_index + 1 :]
                for index, item in enumerate(tail):
                    number = int(item["number"])
                    if number <= previous_number:
                        # A reset normally means a duplicated table of
                        # contents or another volume.  Score runs separately
                        # instead of silently keeping a dense TOC.
                        if number <= 5 and len(run) >= 2:
                            break
                        continue
                    if number - previous_number > 50:
                        continue
                    if number - previous_number > 1:
                        # 更新公告偶尔被误标成后续章节号，例如第817章后先出现
                        # “828.今天更新晚一点”，随后才是真正的第818章。
                        has_closer_candidate = any(
                            previous_number
                            < int(candidate["number"])
                            < number
                            for candidate in tail[index + 1 : index + 65]
                        )
                        if has_closer_candidate:
                            continue
                    run.append(item)
                    previous_number = number
                runs.append(run)
            return runs

        def eligible_run(
            run: List[Dict[str, Any]],
        ) -> tuple[bool, float, int]:
            if not run:
                return False, 0.0, 0
            numbers = [int(item["number"]) for item in run]
            adjacent_steps = sum(
                1
                for previous, current in zip(numbers, numbers[1:])
                if current - previous == 1
            )
            continuity = adjacent_steps / max(len(numbers) - 1, 1)
            span = int(run[-1]["offset"]) - int(run[0]["offset"])
            if len(run) >= 8:
                return continuity >= 0.55, continuity, span
            if len(run) >= 3:
                return (
                    numbers[0] <= 3
                    and continuity >= 0.8
                    and span >= 512
                ), continuity, span
            if len(run) == 2:
                return (
                    numbers == [1, 2]
                    and span >= 512
                ), continuity, span
            stat_size = source_path.stat().st_size
            return (
                numbers[0] == 1
                and bool(run[0].get("high_confidence"))
                and int(run[0]["offset"]) <= 64 * 1024
                and stat_size - int(run[0]["offset"]) >= 512
            ), continuity, span

        numeric_sequence: List[Dict[str, Any]] = []
        numeric_style = "numeric"
        numeric_score: tuple[int, float, int] = (0, 0.0, 0)
        # Some legacy romance exports use local part numbers in the title,
        # e.g. ``一夜风流1`` / ``一夜风流2`` followed by another title that
        # restarts at 1.  Qualify each repeated base title independently,
        # then combine those structural groups in file order.
        suffix_groups: Dict[str, List[Dict[str, Any]]] = {}
        for item in numeric_by_style["suffix_number"]:
            suffix_groups.setdefault(str(item["base_title"]), []).append(item)
        qualified_suffixes: List[Dict[str, Any]] = []
        for items in suffix_groups.values():
            runs = candidate_runs(items)
            best_run = max(runs, key=len, default=[])
            eligible, _continuity, _span = eligible_run(best_run)
            if eligible or (
                len(best_run) >= 2
                and [int(item["number"]) for item in best_run[:2]] == [1, 2]
            ):
                qualified_suffixes.extend(best_run)
        qualified_suffixes.sort(key=lambda item: int(item["offset"]))
        if len(qualified_suffixes) >= 2:
            numeric_sequence = [
                {
                    **item,
                    "number": index,
                    "label": f"第{index}章",
                }
                for index, item in enumerate(qualified_suffixes, start=1)
            ]
            numeric_style = "suffix_number"
            numeric_score = (
                len(numeric_sequence),
                1.0,
                int(numeric_sequence[-1]["offset"])
                - int(numeric_sequence[0]["offset"]),
            )

        for style, candidates in numeric_by_style.items():
            for run in candidate_runs(candidates):
                eligible, continuity, span = eligible_run(run)
                if not eligible:
                    continue
                score = (len(run), continuity, span)
                if score > numeric_score:
                    numeric_sequence = run
                    numeric_style = style
                    numeric_score = score

        # Some exports use a decoration as the only chapter marker, for
        # example ``☆、时医生``.  Accept it only when multiple markers span
        # meaningful body content; this keeps ordinary bullet lists out.
        if len(decorated_unnumbered) >= 2:
            decorated_span = (
                int(decorated_unnumbered[-1]["offset"])
                - int(decorated_unnumbered[0]["offset"])
            )
            unique_titles = {
                str(item["title"]) for item in decorated_unnumbered
            }
            minimum_span = 512 if len(decorated_unnumbered) == 2 else 1024
            if (
                decorated_span >= minimum_span
                and len(unique_titles) >= min(len(decorated_unnumbered), 3)
                and len(decorated_unnumbered) > len(numeric_sequence)
            ):
                numeric_sequence = [
                    {
                        **item,
                        "number": index,
                        "label": f"第{index}章",
                    }
                    for index, item in enumerate(
                        decorated_unnumbered,
                        start=1,
                    )
                ]
                numeric_style = "decorated"
                numeric_score = (
                    len(numeric_sequence),
                    1.0,
                    decorated_span,
                )

        # Standard ``第X章`` headings are stronger evidence than isolated
        # numeric body lines.  Long Fanqie exports can contain hundreds of
        # standalone numbers (stats, dates, coordinates); when the named
        # sequence overwhelmingly dominates, do not let those false numeric
        # candidates replace the real chapter catalog.
        if (
            len(deduped_named) >= 8
            and len(deduped_named) >= max(len(numeric_sequence) * 2, 8)
        ):
            return (
                deduped_named,
                "named",
                [int(item["offset"]) for item in deduped_named],
            )

        if numeric_sequence:
            numeric_numbers = {
                int(item["number"]) for item in numeric_sequence
            }
            first_number = int(numeric_sequence[0]["number"])
            last_number = int(numeric_sequence[-1]["number"])
            merged = list(numeric_sequence)
            for item in deduped_named:
                if item.get("special"):
                    merged.append(item)
                    continue
                chapter_number = _reader_label_number(item["label"])
                if chapter_number is None:
                    continue
                if (
                    chapter_number in numeric_numbers
                    or chapter_number < first_number
                    or chapter_number > last_number + 50
                ):
                    continue
                merged.append({**item, "number": chapter_number})
                numeric_numbers.add(chapter_number)
            merged.sort(key=lambda item: int(item["offset"]))
            boundaries = sorted(
                {
                    int(item["offset"])
                    for item in [*merged, *deduped_named]
                }
            )
            return merged, numeric_style, boundaries

        return (
            deduped_named,
            "named",
            [int(item["offset"]) for item in deduped_named],
        )

    def _build_reader_index(
        self,
        book_id: int,
        source_path: Path,
    ) -> Dict[str, Any]:
        stat = source_path.stat()
        headings, heading_style, boundary_offsets = (
            self._reader_heading_candidates(source_path)
        )

        sections: List[Dict[str, Any]] = []
        if headings:
            first_offset = int(headings[0]["offset"])
            if first_offset:
                with source_path.open("rb") as handle:
                    intro = handle.read(first_offset).decode("utf-8", errors="replace")
                if len(re.sub(r"\s+", "", intro)) >= 80:
                    sections.append(
                        {
                            "start": 0,
                            "end": first_offset,
                            "label": "序",
                            "title": "作品信息",
                            "kind": "intro",
                        }
                    )
            for index, heading in enumerate(headings):
                next_heading_offset = (
                    int(headings[index + 1]["offset"])
                    if index + 1 < len(headings)
                    else stat.st_size
                )
                next_boundary_offset = next(
                    (
                        offset
                        for offset in boundary_offsets
                        if int(heading["offset"]) < offset
                    ),
                    next_heading_offset,
                )
                sections.append(
                    {
                        "start": int(heading["offset"]),
                        "end": min(next_heading_offset, next_boundary_offset),
                        "label": heading["label"],
                        "title": heading["title"] or heading["label"],
                        "kind": "chapter",
                    }
                )
        else:
            chunk_start = 0
            chunk_number = 1
            with source_path.open("rb") as handle:
                while True:
                    raw_line = handle.readline()
                    if not raw_line:
                        break
                    line_end = handle.tell()
                    if line_end - chunk_start < READER_FALLBACK_CHUNK_BYTES:
                        continue
                    sections.append(
                        {
                            "start": chunk_start,
                            "end": line_end,
                            "label": "正文",
                            "title": f"正文片段 {chunk_number}",
                            "kind": "fallback",
                        }
                    )
                    chunk_start = line_end
                    chunk_number += 1
            if chunk_start < stat.st_size or not sections:
                sections.append(
                    {
                        "start": chunk_start,
                        "end": stat.st_size,
                        "label": "正文",
                        "title": f"正文片段 {chunk_number}",
                        "kind": "fallback",
                    }
                )

        chapters = []
        chapter_index = 0
        with source_path.open("rb") as handle:
            for index, section in enumerate(sections, start=1):
                start = int(section["start"])
                end = int(section["end"])
                handle.seek(start)
                content = handle.read(max(end - start, 0)).decode(
                    "utf-8", errors="replace"
                )
                displayed_content = self._clean_reader_section_content(content)
                if section["kind"] == "chapter":
                    chapter_index += 1
                    actual_chapter_index: Optional[int] = chapter_index
                else:
                    actual_chapter_index = None
                chapters.append(
                    {
                        # ``id`` remains the stable reader route id.  The
                        # separate chapter_index excludes the metadata intro.
                        "id": index,
                        "chapter_index": actual_chapter_index,
                        "kind": section["kind"],
                        "label": section["label"],
                        "title": section["title"],
                        "start": start,
                        "end": end,
                        "byte_count": max(end - start, 0),
                        "word_count": self._reader_word_count(displayed_content),
                    }
                )
        content_chapters = [
            chapter for chapter in chapters if chapter["kind"] != "intro"
        ]
        exact_word_count = sum(
            int(chapter["word_count"]) for chapter in content_chapters
        )
        payload = {
            "schema_version": READER_INDEX_SCHEMA_VERSION,
            "catalog_id": int(book_id),
            "source_path": str(source_path),
            "source_bytes": stat.st_size,
            "source_mtime_ns": stat.st_mtime_ns,
            "word_count": exact_word_count,
            "display_word_count": sum(
                int(chapter["word_count"]) for chapter in chapters
            ),
            "chapter_count": chapter_index,
            "section_count": len(chapters),
            "index_status": "exact" if headings else "fallback",
            "heading_style": heading_style,
            "chapters": chapters,
            "indexed_at": _now(),
        }
        _atomic_write_json(self._reader_index_path(book_id), payload)
        return payload

    @staticmethod
    def _reader_index_cache_valid(
        cached: Dict[str, Any],
        source_path: Path,
    ) -> bool:
        stat = source_path.stat()
        return bool(
            cached.get("schema_version") == READER_INDEX_SCHEMA_VERSION
            and cached.get("source_path") == str(source_path)
            and int(cached.get("source_bytes") or -1) == stat.st_size
            and int(cached.get("source_mtime_ns") or -1) == stat.st_mtime_ns
            and cached.get("chapters")
            and "word_count" in cached
            and "chapter_count" in cached
        )

    def _reader_index(self, book_id: int, source_path: Path) -> Dict[str, Any]:
        cached = _read_json(self._reader_index_path(book_id), {})
        if self._reader_index_cache_valid(cached, source_path):
            return cached
        return self._build_reader_index(book_id, source_path)

    def rebuild_reader_index(
        self,
        book_id: int,
        source_path: Optional[Path] = None,
        *,
        force: bool = False,
    ) -> tuple[Dict[str, Any], bool]:
        """Build or reuse one exact reader index.

        Returns ``(payload, rebuilt)`` so the maintenance CLI can distinguish
        incremental cache hits from files that were actually rescanned.
        """

        if source_path is None:
            book = self.get_book(book_id)
            source_path = Path(book["source_path"]).expanduser().resolve()
        else:
            source_path = Path(source_path).expanduser().resolve()
        if not _is_within(source_path, self.books_root):
            raise ValueError("作品路径不在电子书库范围内")
        if not source_path.is_file():
            raise FileNotFoundError(f"作品正文不存在：{source_path}")
        cached = _read_json(self._reader_index_path(book_id), {})
        if not force and self._reader_index_cache_valid(cached, source_path):
            return cached, False
        return self._build_reader_index(book_id, source_path), True

    def sync_reader_metrics(
        self,
        book_id: int,
        reader_index: Dict[str, Any],
        *,
        metrics_connection: Optional[sqlite3.Connection] = None,
        connection: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """Persist exact reader metrics into the derived catalog index.

        The source ``catalog.sqlite3`` remains read-only.  The authoritative
        metrics live in a small independent database so long-running tone/plot
        indexing cannot block chapter maintenance.  A supplied legacy index
        connection is updated as a compatibility best effort.
        """
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            self.mysql_catalog.sync_reader_metrics(
                int(book_id),
                reader_index,
            )
            return True

        owns_metrics_connection = metrics_connection is None
        metrics_conn = metrics_connection or self._content_metrics_connection()
        try:
            metrics_conn.execute(
                """
                INSERT INTO book_metrics (
                    catalog_id, source_path, source_bytes, source_mtime_ns,
                    word_count, chapter_count, section_count, index_status,
                    schema_version, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(catalog_id) DO UPDATE SET
                    source_path=excluded.source_path,
                    source_bytes=excluded.source_bytes,
                    source_mtime_ns=excluded.source_mtime_ns,
                    word_count=excluded.word_count,
                    chapter_count=excluded.chapter_count,
                    section_count=excluded.section_count,
                    index_status=excluded.index_status,
                    schema_version=excluded.schema_version,
                    indexed_at=excluded.indexed_at
                """,
                (
                    int(book_id),
                    str(reader_index["source_path"]),
                    int(reader_index["source_bytes"]),
                    int(reader_index["source_mtime_ns"]),
                    int(reader_index["word_count"]),
                    int(reader_index["chapter_count"]),
                    int(reader_index["section_count"]),
                    str(reader_index["index_status"]),
                    int(reader_index["schema_version"]),
                    str(reader_index["indexed_at"]),
                ),
            )
            if owns_metrics_connection:
                metrics_conn.commit()
        finally:
            if owns_metrics_connection:
                metrics_conn.close()

        if connection is not None:
            connection.execute(
                """
                UPDATE library_index
                SET approx_word_count=?, approx_chapter_count=?,
                    word_count=?, chapter_count=?, section_count=?,
                    reader_index_status=?, reader_schema_version=?,
                    reader_indexed_at=?
                WHERE catalog_id=?
                """,
                (
                    int(reader_index["word_count"]),
                    int(reader_index["chapter_count"]),
                    int(reader_index["word_count"]),
                    int(reader_index["chapter_count"]),
                    int(reader_index["section_count"]),
                    str(reader_index["index_status"]),
                    int(reader_index["schema_version"]),
                    str(reader_index["indexed_at"]),
                    int(book_id),
                ),
            )
        return True

    def get_reader_catalog(self, book_id: int) -> Dict[str, Any]:
        book = self.get_book(book_id)
        source_path = Path(book["source_path"]).expanduser().resolve()
        if not book.get("source_exists"):
            raise KeyError("作品正文不存在")
        reader_index = self._reader_index(book_id, source_path)
        try:
            self.sync_reader_metrics(book_id, reader_index)
        except sqlite3.Error:
            # The chapter itself is already readable.  A transient metrics
            # write lock must not turn a reader GET into an error.
            pass
        return {
            "book": {
                "catalog_id": int(book_id),
                "title": book.get("title") or source_path.stem,
                "author": book.get("author") or "",
                "category": book.get("category") or "",
                "source_bytes": int(book.get("source_bytes") or 0),
            },
            "word_count": int(reader_index["word_count"]),
            "chapter_count": reader_index["chapter_count"],
            "section_count": reader_index["section_count"],
            "index_status": reader_index["index_status"],
            "chapters": [
                {
                    "id": chapter["id"],
                    "chapter_index": chapter.get("chapter_index"),
                    "kind": chapter.get("kind", "chapter"),
                    "label": chapter["label"],
                    "title": chapter["title"],
                    "byte_count": chapter["byte_count"],
                    "word_count": int(chapter.get("word_count") or 0),
                }
                for chapter in reader_index["chapters"]
            ],
        }

    def get_reader_chapter(self, book_id: int, chapter_id: int) -> Dict[str, Any]:
        book = self.get_book(book_id)
        source_path = Path(book["source_path"]).expanduser().resolve()
        reader_index = self._reader_index(book_id, source_path)
        chapter = next(
            (
                item
                for item in reader_index["chapters"]
                if int(item["id"]) == int(chapter_id)
            ),
            None,
        )
        if not chapter:
            raise KeyError("阅读章节不存在")
        with source_path.open("rb") as handle:
            handle.seek(int(chapter["start"]))
            content = handle.read(
                int(chapter["end"]) - int(chapter["start"])
            ).decode("utf-8", errors="replace")
        content = self._clean_reader_section_content(content)
        return {
            "id": int(chapter["id"]),
            "chapter_index": chapter.get("chapter_index"),
            "kind": chapter.get("kind", "chapter"),
            "label": chapter["label"],
            "title": chapter["title"],
            "content": content,
            "word_count": self._reader_word_count(content),
            "book": {
                "catalog_id": int(book_id),
                "title": book.get("title") or source_path.stem,
                "author": book.get("author") or "",
            },
        }

    def _task_dir(self, project_root: Path) -> Path:
        del project_root
        return self.global_task_root

    @staticmethod
    def _project_deconstruction_identity(
        project_root: Path,
    ) -> Dict[str, str]:
        project_id = ""
        project_name = project_root.name
        try:
            from oohstory_library.services.projects_manager import find_project_by_path

            project = find_project_by_path(project_root) or {}
            project_id = str(project.get("id") or "")
            project_name = str(project.get("name") or project_name)
        except Exception:
            state = _read_json(project_root / ".webnovel" / "state.json", {})
            info = state.get("project_info") if isinstance(state, dict) else {}
            if isinstance(info, dict):
                project_name = str(info.get("title") or project_name)
        return {
            "project_id": project_id,
            "project_name": project_name,
            "project_directory_name": project_root.name,
        }

    def _read_project_deconstruction_links(self) -> Dict[str, Any]:
        payload = _read_json(
            self.project_deconstruction_links_path,
            {},
        )
        if not isinstance(payload, dict):
            payload = {}
        links = payload.get("links")
        if not isinstance(links, list):
            links = []
        return {
            "schema_version": 1,
            "updated_at": str(payload.get("updated_at") or ""),
            "links": [
                item for item in links if isinstance(item, dict)
            ],
        }

    def _register_project_deconstruction_link(
        self,
        project_root: Path,
        output_dir: Path,
        book: Dict[str, Any],
        *,
        task_id: Optional[str] = None,
        mode: str = "",
        association_reason: str = "task_created",
    ) -> Dict[str, Any]:
        """Persist one project→global-book association and its book-level link."""
        project_root = project_root.expanduser().resolve()
        if not project_root.is_dir() or not (
            project_root / ".webnovel"
        ).is_dir():
            raise ValueError("当前路径不是有效的小说项目")

        output_dir = output_dir.expanduser().resolve()
        if not _is_within(output_dir, self.global_deconstruction_root):
            raise ValueError("项目拆文软链目标必须位于全局拆书库中")
        if output_dir.name.startswith("."):
            raise ValueError("项目拆文软链不能指向全局拆书库内部控制目录")

        project_reference_root = project_root / "拆文库"
        if project_reference_root.is_symlink():
            raise ValueError(
                "项目拆文库不能整目录软链到全局库，必须使用书级软链接"
            )
        project_reference_root.mkdir(parents=True, exist_ok=True)
        if not _is_within(project_reference_root, project_root):
            raise ValueError("项目拆文库越过当前小说项目边界")

        link_path = project_reference_root / output_dir.name
        if link_path.is_symlink():
            try:
                same = os.path.samefile(link_path, output_dir)
            except OSError:
                same = link_path.resolve() == output_dir
            if not same:
                link_path.unlink()
                link_path.symlink_to(output_dir, target_is_directory=True)
        elif link_path.exists():
            raise ValueError(
                f"项目拆文目录存在同名实体，拒绝覆盖：{link_path.name}"
            )
        else:
            try:
                link_path.symlink_to(output_dir, target_is_directory=True)
            except FileExistsError:
                if not link_path.is_symlink():
                    raise ValueError(
                        f"项目拆文链接发生并发冲突：{link_path.name}"
                    )
                try:
                    same = os.path.samefile(link_path, output_dir)
                except OSError:
                    same = link_path.resolve() == output_dir
                if not same:
                    link_path.unlink()
                    link_path.symlink_to(output_dir, target_is_directory=True)

        identity = self._project_deconstruction_identity(project_root)
        association_id = hashlib.sha256(
            f"{project_root}\0{output_dir}".encode("utf-8")
        ).hexdigest()[:20]
        current_time = _now()
        self.global_deconstruction_root.mkdir(parents=True, exist_ok=True)
        with self.project_deconstruction_links_lock_path.open(
            "a+",
            encoding="utf-8",
        ) as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                registry = self._read_project_deconstruction_links()
                existing = next(
                    (
                        item
                        for item in registry["links"]
                        if item.get("id") == association_id
                    ),
                    None,
                )
                task_ids = [
                    str(value)
                    for value in (
                        (existing or {}).get("task_ids") or []
                    )
                    if str(value).strip()
                ]
                if task_id and task_id not in task_ids:
                    task_ids.append(task_id)
                record = {
                    "id": association_id,
                    **identity,
                    "project_root": str(project_root),
                    "catalog_id": (
                        int(book.get("catalog_id") or book.get("book_id"))
                        if book.get("catalog_id") or book.get("book_id")
                        else None
                    ),
                    "source_id": str(book.get("source_id") or ""),
                    "title": str(book.get("title") or output_dir.name),
                    "author": str(book.get("author") or ""),
                    "global_output_dir": str(output_dir),
                    "global_book_key": output_dir.name,
                    "project_link_path": str(link_path),
                    "task_ids": task_ids,
                    "last_mode": str(mode or ""),
                    "last_association_reason": association_reason,
                    "first_linked_at": str(
                        (existing or {}).get("first_linked_at")
                        or current_time
                    ),
                    "last_linked_at": current_time,
                    "link_status": "linked",
                }
                registry["links"] = [
                    item
                    for item in registry["links"]
                    if item.get("id") != association_id
                ]
                registry["links"].append(record)
                registry["links"].sort(
                    key=lambda item: (
                        str(item.get("project_name") or ""),
                        str(item.get("title") or ""),
                        str(item.get("global_book_key") or ""),
                    )
                )
                registry["updated_at"] = current_time
                _atomic_write_json(
                    self.project_deconstruction_links_path,
                    registry,
                )
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        return record

    def list_project_deconstruction_links(
        self,
        project_root: Optional[Path] = None,
        *,
        include_paths: bool = True,
    ) -> Dict[str, Any]:
        selected_root = (
            str(project_root.expanduser().resolve())
            if project_root is not None
            else ""
        )
        registry = self._read_project_deconstruction_links()
        items: List[Dict[str, Any]] = []
        for stored in registry["links"]:
            if selected_root and stored.get("project_root") != selected_root:
                continue
            item = dict(stored)
            link_path = Path(str(item.get("project_link_path") or ""))
            output_dir = Path(str(item.get("global_output_dir") or ""))
            item["link_status"] = (
                "linked"
                if (
                    link_path.is_symlink()
                    and link_path.resolve() == output_dir.resolve()
                )
                else "missing"
            )
            item["global_output_exists"] = output_dir.is_dir()
            if not include_paths:
                item.pop("project_root", None)
                item.pop("global_output_dir", None)
                item.pop("project_link_path", None)
            items.append(item)
        return {
            "schema_version": 1,
            "total": len(items),
            "project_root": (
                selected_root or None
                if include_paths
                else None
            ),
            "items": items,
            "updated_at": registry.get("updated_at") or "",
        }

    def repair_project_deconstruction_links(self) -> Dict[str, Any]:
        """Backfill associations and book-level links from durable task records."""
        repaired = 0
        already_linked = 0
        skipped = 0
        errors: List[Dict[str, str]] = []
        for task in reversed(self._list_managed_tasks(
            self.global_deconstruction_root
        )):
            task_id = str(task.get("id") or "")
            try:
                project_root = Path(
                    str(task.get("project_root") or "")
                ).expanduser().resolve()
                output_dir = Path(
                    str(task.get("output_dir") or "")
                ).expanduser().resolve()
                if (
                    not task_id
                    or not project_root.is_dir()
                    or not (project_root / ".webnovel").is_dir()
                    or not output_dir.is_dir()
                    or not _is_within(
                        output_dir,
                        self.global_deconstruction_root,
                    )
                ):
                    skipped += 1
                    continue
                link_path = project_root / "拆文库" / output_dir.name
                was_linked = bool(
                    link_path.is_symlink()
                    and link_path.resolve() == output_dir
                )
                self._register_project_deconstruction_link(
                    project_root,
                    output_dir,
                    task,
                    task_id=task_id,
                    mode=str(
                        task.get("entry_mode")
                        or task.get("requested_mode")
                        or ""
                    ),
                    association_reason="historical_task_backfill",
                )
                if was_linked:
                    already_linked += 1
                else:
                    repaired += 1
            except (OSError, TypeError, ValueError) as exc:
                errors.append(
                    {
                        "task_id": task_id,
                        "error": str(exc),
                    }
                )
        return {
            "repaired": repaired,
            "already_linked": already_linked,
            "skipped": skipped,
            "errors": errors,
            "registry": self.list_project_deconstruction_links(),
        }

    def _list_managed_tasks(
        self,
        project_root: Path,
        *,
        enrich_artifacts: bool = True,
    ) -> List[Dict[str, Any]]:
        task_dir = self._task_dir(project_root)
        if not task_dir.exists():
            return []
        tasks = []
        for path in task_dir.glob("*.json"):
            task = _read_json(path, {})
            if task:
                tasks.append(
                    self._project_terminal_task_artifact(task)
                    if enrich_artifacts
                    else task
                )
        tasks.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return tasks

    def _deconstruction_cache_key(
        self,
        project_root: Optional[Path],
    ) -> str:
        selected = (
            project_root.expanduser().resolve()
            if project_root is not None
            else self.global_deconstruction_root
        )
        return hashlib.sha1(str(selected).encode("utf-8")).hexdigest()[:12]

    def _deconstruction_cache_path(
        self,
        project_root: Optional[Path],
    ) -> Path:
        return (
            self.runtime_dir
            / f"deconstruction-status-{self._deconstruction_cache_key(project_root)}.json"
        )

    @staticmethod
    def _cache_stat_token(path: Path) -> str:
        try:
            stat = path.stat()
        except OSError:
            return f"{path.name}:missing"
        return f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}"

    def _deconstruction_artifact_signatures(self) -> Dict[str, str]:
        signatures: Dict[str, str] = {}
        if not self.global_deconstruction_root.exists():
            return signatures
        for output_dir in sorted(
            (
                path
                for path in self.global_deconstruction_root.iterdir()
                if path.is_dir() and not path.name.startswith(".")
            ),
            key=lambda path: path.name,
        ):
            tokens = [self._cache_stat_token(output_dir)]
            for relative in (
                "_progress.md",
                "_meta.json",
                "快速预览.md",
                "拆文报告.md",
                "情节节点.md",
                "写作手法.md",
                "章节",
            ):
                tokens.append(
                    f"{relative}:{self._cache_stat_token(output_dir / relative)}"
                )
            signatures[str(output_dir.expanduser().resolve())] = (
                hashlib.sha256("\n".join(tokens).encode("utf-8")).hexdigest()
            )
        return signatures

    def _deconstruction_cache_signature(
        self,
        project_root: Optional[Path],
    ) -> str:
        """Build a cheap change token without validating every artifact.

        Full OH-Story contract validation opens many chapter/report files and
        is intentionally reserved for the background refresh.  The dashboard
        only stats the small set of files that signal task/progress changes.
        """

        task_root = self._task_dir(
            project_root or self.global_deconstruction_root
        )
        tokens = [
            f"project:{project_root.expanduser().resolve() if project_root else ''}",
            self._cache_stat_token(self.project_deconstruction_links_path),
        ]
        if task_root.exists():
            tokens.extend(
                f"task:{path.name}:{self._cache_stat_token(path)}"
                for path in sorted(task_root.glob("*.json"))
            )
        tokens.extend(
            f"artifact:{path}:{signature}"
            for path, signature in self._deconstruction_artifact_signatures().items()
        )
        return hashlib.sha256("\n".join(tokens).encode("utf-8")).hexdigest()

    def _store_deconstruction_cache(
        self,
        project_root: Optional[Path],
        result: Dict[str, Any],
        *,
        artifact_items: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        key = self._deconstruction_cache_key(project_root)
        payload = {
            "schema_version": 1,
            "project_root": (
                str(project_root.expanduser().resolve())
                if project_root is not None
                else ""
            ),
            "signature": self._deconstruction_cache_signature(project_root),
            "cached_at_epoch": time.time(),
            "result": result,
            "artifact_signatures": self._deconstruction_artifact_signatures(),
            "artifact_items": artifact_items or [],
        }
        with self._deconstruction_cache_lock:
            self._deconstruction_status_cache[key] = copy.deepcopy(payload)
            _atomic_write_json(
                self._deconstruction_cache_path(project_root),
                payload,
            )
        # The NAS snapshot is durable before the disposable epoch advances.
        self.hot_cache.invalidate("deconstruction")
        self.hot_cache.set_json(
            "deconstruction",
            "status-summary",
            {"project": key},
            {
                field: result.get(field)
                for field in (
                    "root",
                    "total",
                    "running",
                    "completed",
                    "scan_completed",
                    "updated_at",
                )
            },
            ttl_seconds=60,
        )

    def _load_deconstruction_cache(
        self,
        project_root: Optional[Path],
    ) -> Dict[str, Any]:
        key = self._deconstruction_cache_key(project_root)
        with self._deconstruction_cache_lock:
            cached = self._deconstruction_status_cache.get(key)
        if cached:
            return copy.deepcopy(cached)
        cached = _read_json(self._deconstruction_cache_path(project_root), {})
        if not isinstance(cached, dict) or not isinstance(
            cached.get("result"), dict
        ):
            return {}
        with self._deconstruction_cache_lock:
            self._deconstruction_status_cache[key] = copy.deepcopy(cached)
        return cached

    def _schedule_deconstruction_cache_refresh(
        self,
        project_root: Optional[Path],
    ) -> None:
        key = self._deconstruction_cache_key(project_root)
        with self._deconstruction_cache_lock:
            if key in self._deconstruction_cache_refreshing:
                return
            self._deconstruction_cache_refreshing.add(key)

        def refresh() -> None:
            try:
                self.list_global_deconstructions(project_root)
            except Exception:
                # The stale snapshot remains authoritative until a later poll
                # successfully refreshes it; never fail the dashboard thread.
                pass
            finally:
                with self._deconstruction_cache_lock:
                    self._deconstruction_cache_refreshing.discard(key)

        threading.Thread(
            target=refresh,
            name=f"library-deconstruction-cache-{key}",
            daemon=True,
        ).start()

    def list_global_deconstructions_cached(
        self,
        project_root: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Return a fast snapshot and refresh stale artifacts in background."""

        signature = self._deconstruction_cache_signature(project_root)
        cached = self._load_deconstruction_cache(project_root)
        if not cached:
            # Cold starts are rare because the snapshot survives backend
            # restarts.  Build it once synchronously so callers never receive
            # invented task/artifact state.
            return self.list_global_deconstructions(project_root)

        result = copy.deepcopy(cached["result"])
        running = bool(
            any(
                item.get("status") in {"queued", "running"}
                for item in result.get("items", [])
            )
        )
        running_expired = bool(
            running
            and time.time() - float(cached.get("cached_at_epoch") or 0)
            >= DECONSTRUCTION_CACHE_RUNNING_SECONDS
        )
        signature_changed = cached.get("signature") != signature
        if signature_changed:
            cached_artifact_signatures = (
                cached.get("artifact_signatures") or {}
            )
            current_artifact_signatures = (
                self._deconstruction_artifact_signatures()
            )
            same_artifact_set = (
                set(cached_artifact_signatures)
                == set(current_artifact_signatures)
            )
            existing_artifact_changed = bool(
                same_artifact_set
                and cached_artifact_signatures
                != current_artifact_signatures
            )
            if existing_artifact_changed:
                self._schedule_deconstruction_cache_refresh(project_root)
                result["cache"] = "stale"
                result["refreshing"] = True
                return result
            # The fresh path reuses every unchanged artifact projection, so a
            # newly created/removed task or artifact remains immediately
            # consistent for catalog filters and task actions.
            return self.list_global_deconstructions(project_root)
        if running_expired:
            self._schedule_deconstruction_cache_refresh(project_root)
        result["cache"] = "stale" if running_expired else "snapshot"
        result["refreshing"] = running_expired
        return result

    def _project_terminal_task_artifact(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Render terminal full-task status from artifacts, not stale JSON.

        Older workers cached a blanket ``completed`` stage snapshot while the
        durable OH-Story checkpoint still contained pending/running stages.
        Keep task JSON immutable here, but make every read use the validated
        artifact projection so task details cannot repeat that stale claim.
        """
        projected = dict(task)
        task_status = str(task.get("status") or "")
        validation_paused = bool(
            task_status in {"paused", "error"}
            and task.get("pause_reason") == "artifact_validation_failed"
        )
        if not (
            task_status == "completed"
            or validation_paused
        ):
            return projected
        entry_mode = str(
            task.get("entry_mode")
            or task.get("requested_mode")
            or ""
        )
        if entry_mode and entry_mode != "full":
            return projected
        output_raw = str(task.get("output_dir") or "").strip()
        if not output_raw:
            return projected
        output_dir = Path(output_raw).expanduser()
        if not output_dir.is_dir():
            return projected
        preferred_pipeline = str(task.get("resolved_pipeline") or "")
        try:
            artifact = self._deconstruction_artifact_item(
                output_dir,
                preferred_pipeline=(
                    preferred_pipeline
                    if preferred_pipeline in {"short", "long"}
                    else None
                ),
            )
        except (OSError, UnicodeError, ValueError):
            return projected
        for key in (
            "status",
            "progress",
            "current_stage",
            "message",
            "completion_label",
            "pipeline_stages",
            "steps",
            "artifact_level",
            "has_quick_preview",
            "has_full_report",
            "completed_chapters",
            "total_chapters",
            "coverage_scope",
            "structure_beat_count",
            "plot_node_count",
            "progress_path",
            "progress_source",
            "global_reuse",
            "can_resume",
            "pause_reason",
            "contract_validation",
        ):
            if key in artifact:
                projected[key] = artifact[key]
        if validation_paused and projected.get("status") != "completed":
            projected["status"] = "paused"
            projected["can_resume"] = True
            projected["pause_reason"] = "artifact_validation_failed"
            projected["current_stage"] = (
                task.get("current_stage")
                or projected.get("current_stage")
            )
            projected["message"] = (
                task.get("message")
                or projected.get("message")
            )
        return projected

    def _reconcile_stalled_managed_task(
        self,
        project_root: Path,
        task: Dict[str, Any],
        *,
        persist: bool = False,
    ) -> Dict[str, Any]:
        """Let a verified OH-Story checkpoint override a dead task process.

        Gateway restarts can leave the managed JSON at ``running`` even though
        both worker PIDs are gone.  In that case `_progress.md` / `_meta.json`
        is the durable source of truth and the original managed task/session
        must become resumable instead of blocking the catalog forever.
        """
        if _pid_is_alive(task.get("pid")) or _pid_is_alive(task.get("codex_pid")):
            return task
        automatic_full_recovery = _automatic_full_pipeline_needs_recovery(task)
        ai_session_id = str(task.get("ai_session_id") or "").strip()
        lineage = openclaw_session_lineage_state(
            agent_id=str(task.get("openclaw_agent_id") or ""),
            session_id=ai_session_id,
            session_store=str(task.get("openclaw_session_store") or ""),
        )
        if lineage.get("active"):
            # A yielded parent can have no local CLI/worker PID while its
            # Gateway children continue.  Never downgrade that live tree to a
            # failed/paused task or offer a duplicate resume request.
            reconciled = dict(task)
            reconciled.update(
                {
                    "status": "running",
                    "can_resume": False,
                    "message": (
                        "OpenClaw 主会话正在等待同模型子会话完成并回传"
                    ),
                    "pid": None,
                    "codex_pid": None,
                    "openclaw_lineage": {
                        "active": True,
                        "root_status": lineage.get("root_status") or "",
                        "session_count": len(
                            lineage.get("lineage_keys") or []
                        ),
                        "active_count": len(
                            lineage.get("active_keys") or []
                        ),
                        "checked_at": _now(),
                    },
                    "updated_at": _now(),
                }
            )
            if persist:
                task_id = str(reconciled.get("id") or "")
                if re.fullmatch(r"[a-f0-9]{12}", task_id):
                    task_path = (
                        self._task_dir(project_root) / f"{task_id}.json"
                    )
                    # Status polling can be concurrent.  Serialize the
                    # supervisor launch and re-read the PID under the lock so
                    # one live Gateway lineage gets exactly one local watcher.
                    with _task_reconcile_lock:
                        latest = _read_json(task_path, reconciled)
                        if (
                            _pid_is_alive(latest.get("pid"))
                            or _pid_is_alive(latest.get("codex_pid"))
                        ):
                            return latest
                        _atomic_write_json(task_path, reconciled)
                        return self._launch_task_worker(
                            task_path,
                            reconciled,
                        )
            return reconciled
        checkpoint_only_resume = bool(
            task.get("resume_strategy") == "adopt_checkpoint"
            or task.get("session_rotation_required")
            or task.get("pause_reason") == "artifact_validation_failed"
        )
        same_session = bool(
            ai_session_id
            and not checkpoint_only_resume
            and task.get("ai_session_cleanup") != "deleted"
            and _openclaw_session_exists(
                ai_session_id,
                task.get("openclaw_agent_id") or "main",
            )
        )
        if (
            task.get("status") in {"paused", "error"}
            and task.get("can_resume")
            and not automatic_full_recovery
        ):
            reconciled = dict(task)
            reconciled["resume_strategy"] = (
                "same_session" if same_session else "adopt_checkpoint"
            )
            if checkpoint_only_resume:
                reconciled["message"] = (
                    "当前 AI 会话已停止；OH-Story 产物与进度断点均已保留，"
                    "继续拆书时将由新会话接管断点"
                )
            if persist and reconciled != task:
                task_id = str(reconciled.get("id") or "")
                if re.fullmatch(r"[a-f0-9]{12}", task_id):
                    _atomic_write_json(
                        self._task_dir(project_root) / f"{task_id}.json",
                        reconciled,
                    )
            return reconciled
        if (
            task.get("status") not in {"queued", "running", "error"}
            and not automatic_full_recovery
        ):
            return task
        output_value = str(task.get("output_dir") or "").strip()
        if not output_value:
            return task
        output_dir = Path(output_value).expanduser().resolve()
        if (
            not output_dir.is_dir()
            or not _is_within(output_dir, self.global_deconstruction_root)
        ):
            return task
        preferred_pipeline = str(task.get("resolved_pipeline") or "")
        try:
            checkpoint = self._deconstruction_artifact_item(
                output_dir,
                preferred_pipeline=(
                    preferred_pipeline
                    if preferred_pipeline in {"short", "long"}
                    else None
                ),
            )
        except (OSError, UnicodeError):
            return task
        if (
            checkpoint.get("status") != "paused"
            or not checkpoint.get("can_resume")
        ):
            return task

        if automatic_full_recovery and persist:
            task_id = str(task.get("id") or "")
            if re.fullmatch(r"[a-f0-9]{12}", task_id):
                task_path = self._task_dir(project_root) / f"{task_id}.json"
                with _task_reconcile_lock:
                    latest = _read_json(task_path, task)
                    if (
                        _pid_is_alive(latest.get("pid"))
                        or _pid_is_alive(latest.get("codex_pid"))
                    ):
                        return latest
                    if not _automatic_full_pipeline_needs_recovery(latest):
                        return latest
                    recovered = dict(latest)
                    previous_session, next_session = rotate_openclaw_session(
                        recovered,
                        reason="interrupted_full_pipeline_recovery",
                    )
                    recovered.update(
                        {
                            "status": "queued",
                            "can_resume": False,
                            "automatic_full_pipeline": True,
                            "resume_count": int(
                                recovered.get("resume_count") or 0
                            )
                            + 1,
                            "automatic_recovery_count": int(
                                recovered.get("automatic_recovery_count") or 0
                            )
                            + 1,
                            "current_stage": "完整拆书后台自动接力中",
                            "message": (
                                "上一管道会话意外中断；后台已保留产物并"
                                "自动创建新会话继续完整拆书"
                            ),
                            "pid": None,
                            "codex_pid": None,
                            "resumed_at": _now(),
                            "updated_at": _now(),
                        }
                    )
                    recovered.pop("pause_reason", None)
                    recovered.pop("finished_at", None)
                    recovered.pop("session_rotation_required", None)
                    _atomic_write_json(task_path, recovered)
                    log_value = str(recovered.get("log_path") or "").strip()
                    if log_value:
                        try:
                            with Path(log_value).open("a", encoding="utf-8") as log:
                                log.write(
                                    f"[{_now()}] automatic full-pipeline recovery; "
                                    f"previous_session={previous_session}; "
                                    f"next_session={next_session}\n"
                                )
                        except OSError:
                            pass
                    return self._launch_task_worker(task_path, recovered)

        reconciled = dict(task)
        reconciled.update(
            {
                "status": "paused",
                "progress": max(
                    int(task.get("progress") or 0),
                    int(checkpoint.get("progress") or 0),
                ),
                "current_stage": checkpoint.get("current_stage")
                or "拆书进程已停止，可从 OH-Story 断点继续",
                "message": checkpoint.get("message")
                or "原执行进程已退出，已保留现有产物与会话断点",
                "can_resume": True,
                "pause_reason": checkpoint.get("pause_reason")
                or "stalled_checkpoint",
                "resume_strategy": (
                    "same_session" if same_session else "adopt_checkpoint"
                ),
                "completed_chapters": checkpoint.get("completed_chapters", 0),
                "total_chapters": checkpoint.get("total_chapters", 0),
                "pipeline_stages": checkpoint.get("pipeline_stages") or [],
                "progress_path": checkpoint.get("progress_path") or "",
                "progress_source": checkpoint.get("progress_source")
                or "artifacts",
                "pid": None,
                "codex_pid": None,
                "updated_at": _now(),
            }
        )
        if persist:
            task_id = str(reconciled.get("id") or "")
            if re.fullmatch(r"[a-f0-9]{12}", task_id):
                _atomic_write_json(
                    self._task_dir(project_root) / f"{task_id}.json",
                    reconciled,
                )
        return reconciled

    @staticmethod
    def _progress_stage_rows(progress_text: str) -> List[Dict[str, Any]]:
        return long_pipeline_stages(progress_text)

    @staticmethod
    def _deconstruction_progress(
        stages: List[Dict[str, Any]],
        *,
        completed_chapters: int,
        total_chapters: int,
        full_completed: bool,
    ) -> int:
        if full_completed:
            return 100
        weights = {0: 8.0, 1: 12.0, 2: 50.0, 3: 7.5, 4: 7.5, 5: 7.5, 6: 7.5}
        progress = 0.0
        for stage in stages:
            number = int(stage["stage"])
            weight = weights.get(number, 0.0)
            if stage["status"] == "completed":
                progress += weight
            elif stage["status"] == "running":
                if number == 2 and total_chapters:
                    progress += weight * min(
                        max(completed_chapters / total_chapters, 0.0), 1.0
                    )
                else:
                    progress += weight * 0.25
        return min(99, max(0, round(progress)))

    def _deconstruction_artifact_item(
        self,
        output_dir: Path,
        preferred_pipeline: Optional[str] = None,
    ) -> Dict[str, Any]:
        progress_path = output_dir / "_progress.md"
        short_meta_path = output_dir / "_meta.json"
        progress_text = ""
        if progress_path.is_file():
            progress_text = progress_path.read_text(
                encoding="utf-8", errors="replace"
            )
        name = output_dir.name
        source_id = ""
        title = name
        if "__" in name:
            title, source_id = name.rsplit("__", 1)
        heading = re.search(r"^#\s*拆解进度[：:]\s*(.+?)\s*$", progress_text, re.M)
        if heading:
            title = heading.group(1).strip()

        short_meta = read_short_meta(output_dir)
        use_short_meta = bool(short_meta)
        if short_meta and preferred_pipeline == "long":
            use_short_meta = False
        elif short_meta and preferred_pipeline is None and progress_path.is_file():
            try:
                use_short_meta = (
                    short_meta_path.stat().st_mtime
                    >= progress_path.stat().st_mtime
                )
            except OSError:
                pass
        if use_short_meta:
            stages = short_pipeline_stages(short_meta)
            contract_errors = validate_short_output_contract(output_dir)
            full_completed = not contract_errors
            structure_counts = (
                short_meta.get("structure_counts")
                if isinstance(short_meta.get("structure_counts"), dict)
                else {}
            )
            structure_beat_count = int(
                structure_counts.get("beats") or 0
            )
            plot_node_count = 0
            plot_nodes_path = output_dir / "情节节点.md"
            if plot_nodes_path.is_file():
                try:
                    plot_node_count = len(
                        re.findall(
                            r"(?m)^N\d+\b",
                            plot_nodes_path.read_text(
                                encoding="utf-8",
                                errors="replace",
                            ),
                        )
                    )
                except OSError:
                    plot_node_count = 0
            completed_stage_count = sum(
                item.get("status") == "completed" for item in stages
            )
            current = next(
                (item for item in stages if item.get("status") == "running"),
                None,
            ) or next(
                (item for item in stages if item.get("status") == "pending"),
                None,
            )
            tracked_paths = [
                path
                for path in (
                    short_meta_path,
                    output_dir / "拆文报告.md",
                    output_dir / "情节节点.md",
                    output_dir / "写作手法.md",
                )
                if path.exists()
            ]
            updated_timestamp = max(
                (path.stat().st_mtime for path in tracked_paths),
                default=output_dir.stat().st_mtime,
            )
            return {
                "id": f"global-{hashlib.sha1(str(output_dir.resolve()).encode()).hexdigest()[:12]}",
                "origin": "command_line",
                "managed_task_id": None,
                "book_id": None,
                "catalog_id": None,
                "source_id": source_id,
                "title": title,
                "author": "",
                "category": "",
                "output_dir": str(output_dir.resolve()),
                "requested_mode": "full",
                "resolved_pipeline": "short",
                "skill": "story-short-analyze",
                "status": "completed" if full_completed else "paused",
                "progress": (
                    100
                    if full_completed
                    else min(95, 12 + completed_stage_count * 16)
                ),
                "current_stage": (
                    "全篇结构拆解已完成"
                    if full_completed
                    else (
                        f"等待继续 Stage {current['stage']} · {current['name']}"
                        if current
                        else "短篇产物验收未通过"
                    )
                ),
                "message": (
                    (
                        "story-short-analyze Stage 2–6 已覆盖完整原文；"
                        f"{structure_beat_count} 段主结构不是章节数"
                    )
                    if full_completed
                    else "已有短篇拆书断点，但产物契约尚未全部通过"
                ),
                "completion_label": (
                    "story-short-analyze Stage 2–6 全篇结构拆解已完成"
                    if full_completed
                    else ""
                ),
                "created_at": datetime.fromtimestamp(
                    output_dir.stat().st_ctime
                ).isoformat(timespec="seconds"),
                "updated_at": datetime.fromtimestamp(
                    updated_timestamp
                ).isoformat(timespec="seconds"),
                "pid": None,
                "steps": [
                    {
                        "id": f"stage-{item['stage']}",
                        "name": f"Stage {item['stage']} · {item['name']}",
                        "status": item["status"],
                    }
                    for item in stages
                ],
                "pipeline_stages": stages,
                "artifact_level": "full" if full_completed else "in_progress",
                "has_quick_preview": False,
                "has_full_report": full_completed,
                "completed_chapters": 0,
                "total_chapters": 0,
                "coverage_scope": "whole_text",
                "structure_beat_count": structure_beat_count,
                "plot_node_count": plot_node_count,
                "progress_path": str(short_meta_path.resolve()),
                "progress_source": "_meta.json",
                "global_reuse": full_completed,
                "can_resume": not full_completed,
                "pause_reason": (
                    None if full_completed else "artifact_validation_failed"
                ),
                "contract_validation": {
                    "ok": full_completed,
                    "errors": contract_errors,
                    "skill": "story-short-analyze",
                },
                "word_count": int(short_meta.get("word_count") or 0),
            }

        stages = self._progress_stage_rows(progress_text)
        stage_two = next(
            (item for item in stages if item.get("stage") == 2), {}
        )
        chapter_match = re.search(
            r"(\d+)\s*/\s*(\d+)", str(stage_two.get("status_text") or "")
        )
        completed_chapters = int(chapter_match.group(1)) if chapter_match else 0
        total_chapters = int(chapter_match.group(2)) if chapter_match else 0
        chapter_summary_count, boundary_total, _ = long_summary_coverage(
            output_dir,
            progress_text,
        )
        if not boundary_total:
            # Compatibility for legacy CLI artifacts created before schema v2
            # introduced the immutable boundary table. New/background runs
            # always use exact boundary-to-file matching above.
            chapter_summary_count = sum(
                1
                for path in (output_dir / "章节").glob("第*章_摘要.md")
                if path.is_file()
            )
        completed_chapters = max(completed_chapters, chapter_summary_count)
        if boundary_total:
            total_chapters = boundary_total
        elif not total_chapters:
            scope_match = re.search(r"Stage 2 只做第\s*1-(\d+)\s*章", progress_text)
            count_match = re.search(r"^- 章节数[：:]\s*(\d+)", progress_text, re.M)
            total_chapters = int(
                (scope_match or count_match).group(1)
            ) if (scope_match or count_match) else chapter_summary_count

        _, final_state = long_progress_state(progress_text)
        has_quick_preview = (output_dir / "快速预览.md").is_file()
        has_full_report = (output_dir / "拆文报告.md").is_file()
        full_contract_errors = validate_long_output_contract(output_dir, "full")
        full_completed = not full_contract_errors
        completion_claimed = final_state in {
            "completed",
            "completed_with_errors",
        }
        failed_artifact_stages = (
            long_contract_failed_stages(full_contract_errors)
            if completion_claimed
            else set()
        )
        if failed_artifact_stages:
            stages = project_long_contract_failures(
                stages,
                full_contract_errors,
            )
        tracked_paths = [
            path
            for path in (
                progress_path,
                output_dir / "快速预览.md",
                output_dir / "拆文报告.md",
            )
            if path.exists()
        ]
        updated_timestamp = max(
            (path.stat().st_mtime for path in tracked_paths),
            default=output_dir.stat().st_mtime,
        )
        has_running_stage = any(item.get("status") == "running" for item in stages)
        stale_running_stage = bool(
            has_running_stage
            and datetime.now().timestamp() - updated_timestamp
            >= DECONSTRUCTION_STALE_SECONDS
        )
        can_resume = False
        pause_reason: Optional[str] = None
        if full_completed:
            status = "completed"
            current_stage = "完整拆书已完成"
            message = "全局完整拆书成果可供所有项目复用"
        elif final_state == "paused_after_stage1":
            status = "paused"
            current_stage = "黄金三章已完成"
            message = "已停靠在黄金三章，可继续完整拆书"
        elif failed_artifact_stages:
            status = "paused"
            can_resume = True
            pause_reason = "artifact_validation_failed"
            failed_stage = min(failed_artifact_stages)
            failed_row = next(
                (
                    item
                    for item in stages
                    if int(item.get("stage", -1)) == failed_stage
                ),
                {},
            )
            current_stage = (
                f"Stage {failed_stage} · "
                f"{failed_row.get('name') or '产物'}验收未通过"
            )
            message = "；".join(full_contract_errors[:3])
        elif stale_running_stage:
            status = "paused"
            can_resume = True
            pause_reason = "stalled_checkpoint"
            current = next(
                (item for item in stages if item.get("status") == "running"),
                None,
            )
            current_stage = (
                f"已停滞于 Stage {current['stage']} · {current['name']}"
                if current
                else "拆书任务已停滞"
            )
            message = (
                f"已有 {completed_chapters}/{total_chapters} 章成果，"
                "当前无执行进程，可从现有断点接管继续"
                if total_chapters
                else "当前无执行进程，可从现有 OH-Story 断点接管继续"
            )
        elif has_running_stage:
            status = "running"
            current = next(
                (item for item in stages if item.get("status") == "running"),
                None,
            )
            current_stage = (
                f"Stage {current['stage']} · {current['name']}"
                if current
                else "拆书流程进行中"
            )
            message = (
                f"已完成 {completed_chapters}/{total_chapters} 章"
                if total_chapters
                else "命令行 / 外部任务正在持续写入全局拆书库"
            )
        elif final_state == "pending":
            status = "paused"
            can_resume = True
            pause_reason = "stalled_checkpoint"
            current = next(
                (item for item in stages if item.get("status") == "pending"),
                None,
            )
            current_stage = (
                f"等待继续 Stage {current['stage']} · {current['name']}"
                if current
                else "拆书已暂停，可继续完整拆书"
            )
            message = (
                f"已有 {completed_chapters}/{total_chapters} 章成果，可从现有断点继续"
                if total_chapters
                else "已有部分拆书产物，可从现有断点继续"
            )
        elif has_quick_preview:
            status = "paused"
            can_resume = True
            pause_reason = "paused_after_stage1"
            current_stage = "黄金三章已完成"
            message = "已有黄金三章成果，等待继续完整拆书"
        else:
            status = "discovered"
            current_stage = "已发现拆书目录"
            message = "等待可识别的 oh-story 拆书产物"

        progress = self._deconstruction_progress(
            stages,
            completed_chapters=completed_chapters,
            total_chapters=total_chapters,
            full_completed=full_completed,
        )
        artifact_level = (
            "full"
            if full_completed
            else ("in_progress" if status == "running" else ("scan" if has_quick_preview else "none"))
        )
        return {
            "id": f"global-{hashlib.sha1(str(output_dir.resolve()).encode()).hexdigest()[:12]}",
            "origin": "command_line",
            "managed_task_id": None,
            "book_id": None,
            "catalog_id": None,
            "source_id": source_id,
            "title": title,
            "author": "",
            "category": "",
            "output_dir": str(output_dir.resolve()),
            "requested_mode": "full" if status == "running" else artifact_level,
            "resolved_pipeline": "long",
            "skill": "story-long-analyze",
            "status": status,
            "progress": progress,
            "current_stage": current_stage,
            "message": message,
            "completion_label": (
                "story-long-analyze Stage 0–6 已验收完成"
                if full_completed
                else ""
            ),
            "created_at": datetime.fromtimestamp(
                output_dir.stat().st_ctime
            ).isoformat(timespec="seconds"),
            "updated_at": datetime.fromtimestamp(
                updated_timestamp
            ).isoformat(timespec="seconds"),
            "pid": None,
            "steps": [
                {
                    "id": f"stage-{item['stage']}",
                    "name": f"Stage {item['stage']} · {item['name']}",
                    "status": item["status"],
                }
                for item in stages
            ],
            "pipeline_stages": stages,
            "artifact_level": artifact_level,
            "has_quick_preview": has_quick_preview,
            "has_full_report": full_completed,
            "completed_chapters": completed_chapters,
            "total_chapters": total_chapters,
            "progress_path": str(progress_path.resolve()) if progress_path.exists() else "",
            "progress_source": "_progress.md" if progress_path.exists() else "artifacts",
            "global_reuse": full_completed or has_quick_preview,
            "can_resume": can_resume,
            "pause_reason": pause_reason,
            "contract_validation": {
                "ok": full_completed,
                "errors": full_contract_errors,
                "skill": "story-long-analyze",
            },
            "resume_strategy": (
                "adopt_checkpoint" if can_resume else None
            ),
        }

    def list_global_deconstructions(
        self, project_root: Optional[Path] = None
    ) -> Dict[str, Any]:
        self.global_deconstruction_root.mkdir(parents=True, exist_ok=True)
        task_project_root = project_root or self.global_deconstruction_root
        managed_tasks = [
            self._reconcile_stalled_managed_task(
                task_project_root,
                task,
                persist=True,
            )
            for task in self._list_managed_tasks(
                task_project_root,
                enrich_artifacts=False,
            )
        ]
        latest_task_by_output: Dict[str, Dict[str, Any]] = {}
        for task in managed_tasks:
            output_key = str(
                Path(task.get("output_dir") or self.global_deconstruction_root)
                .expanduser()
                .resolve()
            )
            latest_task_by_output.setdefault(output_key, task)

        cached = self._load_deconstruction_cache(project_root)
        cached_artifact_signatures = cached.get("artifact_signatures") or {}
        current_artifact_signatures = self._deconstruction_artifact_signatures()
        cached_artifacts_by_output = {
            str(item.get("output_dir") or ""): item
            for item in cached.get("artifact_items") or []
            if isinstance(item, dict) and item.get("output_dir")
        }
        items: List[Dict[str, Any]] = []
        artifact_items: List[Dict[str, Any]] = []
        for output_dir in self.global_deconstruction_root.iterdir():
            if not output_dir.is_dir() or output_dir.name.startswith("."):
                continue
            try:
                output_key = str(output_dir.expanduser().resolve())
                preferred_pipeline = str(
                    latest_task_by_output.get(output_key, {}).get(
                        "resolved_pipeline"
                    )
                    or ""
                )
                cached_artifact = cached_artifacts_by_output.get(output_key)
                preferred_matches = bool(
                    not preferred_pipeline
                    or not cached_artifact
                    or cached_artifact.get("resolved_pipeline")
                    == preferred_pipeline
                )
                if (
                    cached_artifact
                    and preferred_matches
                    and cached_artifact_signatures.get(output_key)
                    == current_artifact_signatures.get(output_key)
                ):
                    artifact = copy.deepcopy(cached_artifact)
                else:
                    artifact = self._deconstruction_artifact_item(
                        output_dir,
                        preferred_pipeline=(
                            preferred_pipeline
                            if preferred_pipeline in {"short", "long"}
                            else None
                        ),
                    )
                artifact_items.append(copy.deepcopy(artifact))
                items.append(artifact)
            except (OSError, UnicodeError):
                continue

        by_output = {
            str(Path(item["output_dir"]).expanduser().resolve()): item
            for item in items
            if item.get("output_dir")
        }
        merged_task_outputs: set[str] = set()
        for task in managed_tasks:
            output_key = str(
                Path(task.get("output_dir") or self.global_deconstruction_root)
                .expanduser()
                .resolve()
            )
            # _list_managed_tasks is newest-first.  Only the latest task owns
            # the current status projection for one shared artifact directory.
            if output_key in merged_task_outputs:
                continue
            merged_task_outputs.add(output_key)
            artifact = by_output.get(output_key)
            if artifact:
                artifact["origin"] = (
                    "frontend_and_command_line"
                    if artifact.get("progress_source") == "_progress.md"
                    else "frontend"
                )
                artifact["managed_task_id"] = task.get("id")
                artifact["id"] = task.get("id") or artifact["id"]
                artifact["book_id"] = task.get("book_id")
                artifact["catalog_id"] = task.get("book_id")
                for key in (
                    "author", "category", "source_id", "log_path",
                    "last_message_path", "runner_requested", "runner_name",
                    "model_requested", "model_name", "reasoning_requested",
                    "reasoning_name", "ai_session_id", "can_resume",
                    "pause_reason", "resume_count", "resumed_at",
                    "word_count", "analysis_band", "entry_mode",
                    "execution_mode", "requested_mode",
                    "resolved_pipeline", "skill",
                    "resume_strategy",
                    "openclaw_agent_id", "model_contract",
                    "automatic_full_pipeline", "ai_session_generation",
                ):
                    if key in task:
                        artifact[key] = task[key]
                entry_mode = str(
                    task.get("entry_mode")
                    or task.get("requested_mode")
                    or ""
                )
                if entry_mode == "scan":
                    physical_full = bool(artifact.get("has_full_report"))
                    physical_level = artifact.get("artifact_level")
                    scan_accepted = bool(
                        artifact.get("has_quick_preview")
                        or physical_full
                        or (
                            isinstance(task.get("contract_validation"), dict)
                            and task["contract_validation"].get("ok")
                        )
                    )
                    artifact["physical_has_full_report"] = physical_full
                    artifact["physical_artifact_level"] = physical_level
                    artifact["has_full_report"] = False
                    artifact["has_quick_preview"] = scan_accepted
                    artifact["artifact_level"] = (
                        "scan" if scan_accepted else "in_progress"
                    )
                    artifact["global_reuse"] = scan_accepted
                    if (
                        scan_accepted
                        and task.get("status")
                        not in {"queued", "running", "error"}
                    ):
                        artifact["status"] = "paused"
                        artifact["progress"] = 40
                        artifact["current_stage"] = "黄金三章已完成"
                        artifact["message"] = (
                            "本次只授权黄金三章；磁盘上的额外产物"
                            "不计为完整拆书，需另行启动完整拆书"
                        )
                        artifact["can_resume"] = False
                        artifact["pause_reason"] = "paused_after_stage1"
                # 完整成果是最终事实，不能被残留的前端 task JSON 回写成“运行中”。
                if (
                    artifact.get("status") != "completed"
                    and task.get("status") in {"queued", "running", "error"}
                ):
                    artifact["status"] = task["status"]
                    artifact["progress"] = max(
                        int(artifact.get("progress") or 0),
                        int(task.get("progress") or 0),
                    )
                    artifact["current_stage"] = (
                        task.get("current_stage")
                        or artifact.get("current_stage")
                    )
                    artifact["message"] = task.get("message") or artifact.get("message")
                elif (
                    artifact.get("status") != "completed"
                    and task.get("status") == "paused"
                ):
                    artifact["status"] = "paused"
                    artifact["can_resume"] = bool(task.get("can_resume"))
                    artifact["pause_reason"] = (
                        task.get("pause_reason")
                        or artifact.get("pause_reason")
                    )
                    artifact["message"] = (
                        task.get("message")
                        or artifact.get("message")
                    )
                artifact["updated_at"] = max(
                    str(artifact.get("updated_at") or ""),
                    str(task.get("updated_at") or ""),
                )
                continue
            managed = dict(task)
            task_claimed_completed = task.get("status") == "completed"
            managed.update(
                {
                    "origin": "frontend",
                    "managed_task_id": task.get("id"),
                    "catalog_id": task.get("book_id"),
                    "status": "error" if task_claimed_completed else task.get("status"),
                    "progress": (
                        min(int(task.get("progress") or 0), 99)
                        if task_claimed_completed
                        else task.get("progress")
                    ),
                    "message": (
                        "任务声称完成，但未发现可验收的完整拆书产物"
                        if task_claimed_completed
                        else task.get("message")
                    ),
                    "artifact_level": "in_progress",
                    "has_quick_preview": False,
                    "has_full_report": False,
                    "completed_chapters": 0,
                    "total_chapters": 0,
                    "progress_source": "task_json",
                }
            )
            items.append(managed)

        requested_source_ids = sorted(
            {
                str(item.get("source_id") or "")
                for item in items
                if item.get("source_id") not in (None, "")
            }
        )
        requested_titles = sorted(
            {
                str(item.get("title") or "")
                for item in items
                if str(item.get("title") or "").strip()
            }
        )
        requested_title_keys = sorted(
            {
                _normalize_book_identity(title)
                for title in requested_titles
                if _normalize_book_identity(title)
            }
        )
        catalog_rows: List[Dict[str, Any]] = []
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            catalog_rows = [
                self._materialize_mysql_catalog_row(row)
                for row in self.mysql_catalog.find_book_identities(
                    source_ids=requested_source_ids,
                    titles=requested_titles,
                )
            ]
        elif requested_source_ids or requested_titles:
            with self._catalog_connection() as conn:
                catalog_columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_info(books)")
                }
                lookup_conditions: List[str] = []
                lookup_params: List[Any] = []
                if requested_source_ids:
                    placeholders = ",".join("?" for _ in requested_source_ids)
                    lookup_conditions.append(f"source_id IN ({placeholders})")
                    lookup_params.extend(requested_source_ids)
                if "title_key" in catalog_columns and requested_title_keys:
                    placeholders = ",".join("?" for _ in requested_title_keys)
                    lookup_conditions.append(f"title_key IN ({placeholders})")
                    lookup_params.extend(requested_title_keys)
                elif requested_titles:
                    placeholders = ",".join("?" for _ in requested_titles)
                    lookup_conditions.append(f"title IN ({placeholders})")
                    lookup_params.extend(requested_titles)
                catalog_rows = [
                    dict(row)
                    for row in conn.execute(
                        f"""
                        SELECT id AS catalog_id,
                               COALESCE(source_id, id) AS source_id,
                               title, author, category,
                               output_path AS source_path,
                               bytes AS source_bytes
                        FROM books
                        WHERE status != 'duplicate'
                          AND ({" OR ".join(lookup_conditions)})
                        """,
                        lookup_params,
                    )
                ]
        by_source_id = {
            str(item.get("source_id") or ""): item
            for item in catalog_rows
            if item.get("source_id") is not None
        }
        by_title = {
            _normalize_book_identity(item.get("title")): item
            for item in catalog_rows
            if item.get("title")
        }
        for item in items:
            catalog = (
                by_source_id.get(str(item.get("source_id") or ""))
                or by_title.get(_normalize_book_identity(item.get("title")))
            )
            if catalog:
                item["catalog_id"] = int(catalog["catalog_id"])
                item["book_id"] = int(catalog["catalog_id"])
                item["source_id"] = str(catalog["source_id"])
                item["title"] = catalog.get("title") or item.get("title")
                item["author"] = catalog.get("author") or item.get("author") or ""
                item["category"] = catalog.get("category") or item.get("category") or ""
                item["source_path"] = (
                    catalog.get("source_path") or item.get("source_path") or ""
                )
                item["source_bytes"] = int(
                    catalog.get("source_bytes") or item.get("source_bytes") or 0
                )

        metric_items = [
            item for item in items if int(item.get("catalog_id") or 0) > 0
        ]
        self._apply_content_metrics(
            metric_items,
            include_latest_chapter=False,
        )
        for item in metric_items:
            if str(item.get("resolved_pipeline") or "") != "short":
                continue
            source_chapter_count = int(
                item.get("chapter_count")
                or item.get("approx_chapter_count")
                or 0
            )
            item["source_chapter_count"] = source_chapter_count
            if (
                item.get("coverage_scope") == "whole_text"
                and item.get("status") == "completed"
            ):
                chapter_text = (
                    f"覆盖 {source_chapter_count} 章完整原文"
                    if source_chapter_count
                    else "覆盖完整原文"
                )
                node_count = int(item.get("plot_node_count") or 0)
                node_text = (
                    f" · {node_count} 个情节节点"
                    if node_count
                    else ""
                )
                item["coverage_label"] = (
                    f"全篇结构拆解已完成 · {chapter_text}{node_text}"
                )

        link_registry = self.list_project_deconstruction_links()
        links_by_output: Dict[str, List[Dict[str, Any]]] = {}
        for link in link_registry["items"]:
            output_key = str(
                Path(str(link.get("global_output_dir") or ""))
                .expanduser()
                .resolve()
            )
            links_by_output.setdefault(output_key, []).append(link)
        current_project_root = (
            str(project_root.expanduser().resolve())
            if project_root is not None
            else ""
        )
        for item in items:
            output_key = str(
                Path(str(item.get("output_dir") or ""))
                .expanduser()
                .resolve()
            )
            source_links = links_by_output.get(output_key, [])
            item["source_projects"] = [
                {
                    "project_id": link.get("project_id") or "",
                    "project_name": link.get("project_name") or "",
                    "project_directory_name": (
                        link.get("project_directory_name") or ""
                    ),
                    "link_status": link.get("link_status") or "missing",
                }
                for link in source_links
            ]
            current_link = next(
                (
                    link
                    for link in source_links
                    if link.get("project_root") == current_project_root
                ),
                None,
            )
            item["linked_to_current_project"] = bool(current_link)
            item["current_project_link"] = (
                {
                    "association_id": current_link.get("id"),
                    "link_name": current_link.get("global_book_key"),
                    "link_status": current_link.get("link_status"),
                }
                if current_link
                else None
            )

        status_order = {
            "running": 0,
            "queued": 1,
            "paused": 2,
            "error": 3,
            "completed": 4,
            "discovered": 5,
        }
        # 同一状态内优先展示最近更新的任务；稳定排序再保证运行中置顶。
        items.sort(
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )
        items.sort(
            key=lambda item: status_order.get(str(item.get("status")), 9),
        )
        running = sum(
            item.get("status") in {"queued", "running"} for item in items
        )
        completed = sum(item.get("status") == "completed" for item in items)
        scan_completed = sum(
            bool(
                item.get("has_quick_preview")
                or item.get("has_full_report")
            )
            for item in items
        )
        result = {
            "root": str(self.global_deconstruction_root.resolve()),
            "total": len(items),
            "running": running,
            "completed": completed,
            "scan_completed": scan_completed,
            "items": items,
            "updated_at": _now(),
            "progress_source": "全局拆书库产物 + _progress.md + 前端任务 JSON",
        }
        self._store_deconstruction_cache(
            project_root,
            result,
            artifact_items=artifact_items,
        )
        return result

    def deconstruction_lookup(
        self,
        project_root: Optional[Path] = None,
        *,
        fresh: bool = False,
    ) -> Dict[int, Dict[str, Any]]:
        state = (
            self.list_global_deconstructions(project_root)
            if fresh
            else self.list_global_deconstructions_cached(project_root)
        )
        return {
            int(item["catalog_id"]): item
            for item in state["items"]
            if item.get("catalog_id") is not None
        }

    @staticmethod
    def _deconstruction_catalog_state(
        deconstruction: Optional[Dict[str, Any]],
    ) -> str:
        if not deconstruction:
            return "unstarted"
        if deconstruction.get("status") in {"queued", "running"}:
            return "running"
        if (
            deconstruction.get("status") == "completed"
            or deconstruction.get("has_full_report")
        ):
            return "full"
        if (
            deconstruction.get("can_resume")
            and not deconstruction.get("has_quick_preview")
        ):
            return "error"
        if deconstruction.get("has_quick_preview"):
            return "scan"
        if deconstruction.get("status") == "error":
            return "error"
        return "unstarted"

    @staticmethod
    def _deconstruction_matches_catalog_state(
        deconstruction: Optional[Dict[str, Any]],
        state: str,
    ) -> bool:
        """Match both exclusive task states and completed artifact milestones."""
        if state == "all":
            return True
        current_state = ElectronicLibraryService._deconstruction_catalog_state(
            deconstruction
        )
        if state == "scan":
            item = deconstruction or {}
            # “黄金三章”是累计里程碑：完整拆书成果也必须计入。
            # 短篇 Stage 2-6 没有长篇专属的 快速预览.md，因此还要
            # 通过已经严格验收的 has_full_report 识别。
            return bool(
                item.get("has_quick_preview")
                or item.get("has_full_report")
            )
        if state == "full":
            item = deconstruction or {}
            return bool(
                item.get("has_full_report")
                or item.get("status") == "completed"
            )
        return current_state == state

    def _deconstruction_catalog_rows(
        self,
        project_root: Path,
    ) -> tuple[List[Dict[str, Any]], Dict[int, Dict[str, Any]]]:
        deconstruction_by_catalog = self.deconstruction_lookup(project_root)
        # The workbench needs only catalog identity/status to count and filter
        # all books.  Loading exact reader metadata here would open one reader
        # JSON per downloaded title before pagination (tens of thousands of
        # files), making the modal appear empty until the request eventually
        # completed.  Exact metrics are applied only to the visible page below.
        rows = self._catalog_books_fallback(
            include_unavailable=True,
            include_reader_metadata=False,
        )
        for item in rows:
            catalog_id = int(item.get("catalog_id") or 0)
            deconstruction = deconstruction_by_catalog.get(catalog_id)
            source_path = str(item.get("source_path") or "")
            item["source_exists"] = bool(
                source_path and item.get("download_status") == "done"
            )
            item["available_for_analysis"] = bool(
                item.get("download_status") == "done"
                and item["source_exists"]
            )
            item["deconstruction_state"] = self._deconstruction_catalog_state(
                deconstruction
            )
            item["deconstruction"] = deconstruction
        return rows, deconstruction_by_catalog

    def _decorate_deconstruction_catalog_items(
        self,
        items: List[Dict[str, Any]],
    ) -> None:
        self._apply_content_metrics(items, include_latest_chapter=False)
        indexed: Dict[int, Dict[str, Any]] = {}
        if (
            items
            and self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            indexed = self.mysql_catalog.metadata_for_ids(
                int(item["catalog_id"]) for item in items
            )
        elif items and self.index_path.exists():
            index_uri = f"{self.index_path.as_uri()}?mode=ro"
            with sqlite3.connect(index_uri, uri=True, timeout=15) as conn:
                conn.row_factory = sqlite3.Row
                placeholders = ",".join("?" for _ in items)
                catalog_ids = [int(item["catalog_id"]) for item in items]
                indexed = {
                    int(row["catalog_id"]): dict(row)
                    for row in conn.execute(
                        f"""
                        SELECT catalog_id, summary, approx_word_count,
                               approx_chapter_count, genre_tags, tone_tags
                        FROM library_index
                        WHERE catalog_id IN ({placeholders})
                        """,
                        catalog_ids,
                    )
                }
        for item in items:
            feature = indexed.get(int(item["catalog_id"]), {})
            item["summary"] = str(feature.get("summary") or "")
            item["approx_word_count"] = int(
                feature.get("word_count")
                or feature.get("approx_word_count")
                or item.get("approx_word_count")
                or 0
            )
            item["approx_chapter_count"] = int(
                feature.get("chapter_count")
                or feature.get("approx_chapter_count")
                or item.get("approx_chapter_count")
                or 0
            )
            item["genre_tags"] = _read_json_text(
                feature.get("genre_tags"), item.get("genre_tags") or []
            )
            item["tone_tags"] = _read_json_text(
                feature.get("tone_tags"), []
            )

        for item in items:
            deconstruction = item.get("deconstruction")
            if not int(item.get("approx_chapter_count") or 0) and deconstruction:
                item["approx_chapter_count"] = int(
                    deconstruction.get("total_chapters")
                    or deconstruction.get("completed_chapters")
                    or 0
                )
            item["deconstruction"] = (
                {
                    key: deconstruction.get(key)
                    for key in (
                        "id", "status", "progress", "current_stage", "message",
                        "artifact_level", "has_quick_preview", "has_full_report",
                        "completed_chapters", "total_chapters", "updated_at",
                        "runner_name", "model_name", "reasoning_name",
                        "ai_session_id", "managed_task_id", "can_resume",
                        "pause_reason", "resume_count", "word_count",
                        "analysis_band", "entry_mode", "execution_mode",
                        "resolved_pipeline", "skill", "resume_strategy",
                    )
                }
                if deconstruction
                else None
            )

    def _list_deconstruction_catalog_mysql(
        self,
        project_root: Path,
        *,
        state: str,
        query: str,
        category: str,
        page: int,
        page_size: int,
    ) -> Dict[str, Any]:
        if self.mysql_catalog is None:
            raise RuntimeError("MySQL 书目后端未初始化")
        deconstruction_by_catalog = self.deconstruction_lookup(project_root)
        active_deconstruction_rows = self.mysql_catalog.list_book_projection(
            include_unavailable=True,
            catalog_ids=deconstruction_by_catalog.keys(),
        )
        active_ids = {
            int(row["catalog_id"])
            for row in active_deconstruction_rows
        }
        state_order = {
            "running": 0,
            "scan": 1,
            "full": 2,
            "error": 3,
            "unstarted": 4,
        }
        priority_by_id = {
            catalog_id: rank
            for catalog_id in active_ids
            if (
                rank := state_order.get(
                    self._deconstruction_catalog_state(
                        deconstruction_by_catalog.get(catalog_id)
                    ),
                    9,
                )
            )
            < 4
        }
        non_unstarted_ids = {
            catalog_id
            for catalog_id in active_ids
            if not self._deconstruction_matches_catalog_state(
                deconstruction_by_catalog.get(catalog_id),
                "unstarted",
            )
        }
        include_ids: set[int] | None = None
        exclude_ids: set[int] = set()
        if state == "unstarted":
            exclude_ids = non_unstarted_ids
        elif state != "all":
            include_ids = {
                catalog_id
                for catalog_id in active_ids
                if self._deconstruction_matches_catalog_state(
                    deconstruction_by_catalog.get(catalog_id),
                    state,
                )
            }
        result = self.mysql_catalog.browse_deconstruction_projection(
            query=query,
            category=category,
            page=page,
            page_size=page_size,
            include_ids=include_ids,
            exclude_ids=exclude_ids,
            priority_by_id=priority_by_id,
        )
        items: List[Dict[str, Any]] = []
        for raw in result["rows"]:
            item = self._materialize_mysql_catalog_row(raw)
            catalog_id = int(item["catalog_id"])
            deconstruction = deconstruction_by_catalog.get(catalog_id)
            item["source_exists"] = bool(
                item.get("source_path")
                and item.get("download_status") == "done"
            )
            item["available_for_analysis"] = bool(
                item.get("download_status") == "done"
                and item["source_exists"]
            )
            item["deconstruction_state"] = (
                self._deconstruction_catalog_state(deconstruction)
            )
            item["cover_url"] = self._cover_for_catalog_item(item)
            item["deconstruction"] = deconstruction
            items.append(item)
        self._decorate_deconstruction_catalog_items(items)

        totals = self.mysql_catalog.active_totals()
        state_counts = {
            "all": totals["all"],
            "unstarted": totals["all"] - len(non_unstarted_ids),
            "running": 0,
            "scan": 0,
            "full": 0,
            "error": 0,
            "readable": totals["readable"],
        }
        for count_state in ("running", "scan", "full", "error"):
            state_counts[count_state] = sum(
                self._deconstruction_matches_catalog_state(
                    deconstruction_by_catalog.get(catalog_id),
                    count_state,
                )
                for catalog_id in active_ids
            )
        return {
            "items": items,
            "total": int(result["total"]),
            "page": page,
            "page_size": page_size,
            "state": state,
            "query": query,
            "category": category,
            "state_counts": state_counts,
            "categories": result["categories"],
            "batches": self.list_deconstruction_batches(project_root),
            "updated_at": _now(),
        }

    def list_deconstruction_catalog(
        self,
        project_root: Path,
        *,
        state: str = "all",
        query: str = "",
        category: str = "",
        page: int = 1,
        page_size: int = 24,
    ) -> Dict[str, Any]:
        if state not in {
            "all", "unstarted", "running", "scan", "full", "error"
        }:
            raise ValueError("未知拆书状态筛选")
        page = max(int(page), 1)
        page_size = min(max(int(page_size), 1), 60)
        query = query.strip().casefold()
        category = category.strip()
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            return self._list_deconstruction_catalog_mysql(
                project_root,
                state=state,
                query=query,
                category=category,
                page=page,
                page_size=page_size,
            )
        rows, _ = self._deconstruction_catalog_rows(project_root)

        state_counts = {
            "all": len(rows),
            "unstarted": 0,
            "running": 0,
            "scan": 0,
            "full": 0,
            "error": 0,
            "readable": 0,
        }
        for item in rows:
            deconstruction = item.get("deconstruction")
            for count_state in ("unstarted", "running", "scan", "full", "error"):
                if self._deconstruction_matches_catalog_state(
                    deconstruction, count_state
                ):
                    state_counts[count_state] += 1
            if item["available_for_analysis"]:
                state_counts["readable"] += 1

        filtered: List[Dict[str, Any]] = []
        category_counts: Counter[str] = Counter()
        for item in rows:
            if not self._deconstruction_matches_catalog_state(
                item.get("deconstruction"), state
            ):
                continue
            if query:
                haystack = " ".join(
                    str(item.get(key) or "")
                    for key in ("title", "author", "category")
                ).casefold()
                if query not in haystack:
                    continue
            category_counts[str(item.get("category") or "未分类")] += 1
            if category and str(item.get("category") or "未分类") != category:
                continue
            filtered.append(item)

        state_order = {
            "running": 0,
            "scan": 1,
            "full": 2,
            "error": 3,
            "unstarted": 4,
        }
        filtered.sort(
            key=lambda item: (
                state_order.get(item["deconstruction_state"], 9),
                int(item.get("catalog_id") or 0),
            ),
            reverse=False,
        )
        total = len(filtered)
        offset = (page - 1) * page_size
        items = filtered[offset : offset + page_size]
        self._apply_content_metrics(items, include_latest_chapter=False)

        if items and self.index_path.exists():
            index_uri = f"{self.index_path.as_uri()}?mode=ro"
            with sqlite3.connect(index_uri, uri=True, timeout=15) as conn:
                conn.row_factory = sqlite3.Row
                placeholders = ",".join("?" for _ in items)
                catalog_ids = [int(item["catalog_id"]) for item in items]
                indexed = {
                    int(row["catalog_id"]): dict(row)
                    for row in conn.execute(
                        f"""
                        SELECT catalog_id, summary, approx_word_count,
                               approx_chapter_count, genre_tags, tone_tags
                        FROM library_index
                        WHERE catalog_id IN ({placeholders})
                        """,
                        catalog_ids,
                    )
                }
            for item in items:
                feature = indexed.get(int(item["catalog_id"]), {})
                item["summary"] = str(feature.get("summary") or "")
                item["approx_word_count"] = int(
                    item.get("word_count")
                    or feature.get("approx_word_count")
                    or item.get("approx_word_count")
                    or 0
                )
                item["approx_chapter_count"] = int(
                    item.get("chapter_count")
                    or feature.get("approx_chapter_count")
                    or item.get("approx_chapter_count")
                    or 0
                )
                item["genre_tags"] = _read_json_text(
                    feature.get("genre_tags"), item.get("genre_tags") or []
                )
                item["tone_tags"] = _read_json_text(
                    feature.get("tone_tags"), []
                )

        for item in items:
            deconstruction = item.get("deconstruction")
            if not int(item.get("approx_chapter_count") or 0) and deconstruction:
                item["approx_chapter_count"] = int(
                    deconstruction.get("total_chapters")
                    or deconstruction.get("completed_chapters")
                    or 0
                )
            if (
                deconstruction
                and str(deconstruction.get("resolved_pipeline") or "") == "short"
            ):
                source_chapter_count = int(
                    item.get("chapter_count")
                    or item.get("approx_chapter_count")
                    or 0
                )
                deconstruction["source_chapter_count"] = source_chapter_count
                if (
                    deconstruction.get("coverage_scope") == "whole_text"
                    and deconstruction.get("status") == "completed"
                ):
                    chapter_text = (
                        f"覆盖 {source_chapter_count} 章完整原文"
                        if source_chapter_count
                        else "覆盖完整原文"
                    )
                    node_count = int(
                        deconstruction.get("plot_node_count") or 0
                    )
                    node_text = (
                        f" · {node_count} 个情节节点"
                        if node_count
                        else ""
                    )
                    deconstruction["coverage_label"] = (
                        f"全篇结构拆解已完成 · {chapter_text}{node_text}"
                    )
            item["deconstruction"] = (
                {
                    key: deconstruction.get(key)
                    for key in (
                        "id", "status", "progress", "current_stage", "message",
                        "artifact_level", "has_quick_preview", "has_full_report",
                        "completed_chapters", "total_chapters", "updated_at",
                        "runner_name", "model_name", "reasoning_name",
                        "ai_session_id", "managed_task_id", "can_resume",
                        "pause_reason", "resume_count", "word_count",
                        "analysis_band", "entry_mode", "execution_mode",
                        "resolved_pipeline", "skill", "resume_strategy",
                        "coverage_scope", "coverage_label",
                        "source_chapter_count", "structure_beat_count",
                        "plot_node_count",
                    )
                }
                if deconstruction
                else None
            )

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "state": state,
            "query": query,
            "category": category,
            "state_counts": state_counts,
            "categories": [
                {"name": name, "count": count}
                for name, count in sorted(
                    category_counts.items(),
                    key=lambda pair: (-pair[1], pair[0]),
                )
            ],
            "batches": self.list_deconstruction_batches(project_root),
            "updated_at": _now(),
        }

    def _deconstruction_batch_dir(self, project_root: Path) -> Path:
        del project_root
        return self.global_batch_root

    def list_deconstruction_batches(
        self, project_root: Path
    ) -> List[Dict[str, Any]]:
        batch_dir = self._deconstruction_batch_dir(project_root)
        if not batch_dir.exists():
            return []
        items: List[Dict[str, Any]] = []
        for path in batch_dir.glob("*.json"):
            item = _read_json(path, {})
            if not item:
                continue
            public_item = {
                key: item.get(key)
                for key in (
                    "id", "mode", "status", "state_filter", "query", "category",
                    "total", "cursor", "started", "reused", "failed", "finished",
                    "runner_requested", "runner_name", "model_requested",
                    "model_name", "reasoning_requested", "reasoning_name",
                    "session_strategy", "session_total", "session_ids",
                    "parallel_limit", "current_stage", "message",
                    "created_at", "updated_at", "pid",
                )
            }
            public_item["progress"] = (
                round(
                    min(
                        int(item.get("finished") or 0),
                        int(item.get("total") or 0),
                    )
                    * 100
                    / max(int(item.get("total") or 0), 1)
                )
                if item.get("total")
                else 100
            )
            items.append(public_item)
        items.sort(
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )
        return items[:30]

    def _batch_catalog_ids(
        self,
        project_root: Path,
        *,
        mode: str,
        state: str,
        query: str,
        category: str,
        catalog_ids: Optional[List[int]],
    ) -> List[int]:
        rows, _ = self._deconstruction_catalog_rows(project_root)
        explicit = {
            int(value) for value in (catalog_ids or []) if int(value) > 0
        }
        query = query.strip().casefold()
        category = category.strip()
        selected: List[int] = []
        for item in rows:
            catalog_id = int(item.get("catalog_id") or 0)
            if explicit and catalog_id not in explicit:
                continue
            if not item.get("available_for_analysis"):
                continue
            if not explicit:
                if state != "all" and item["deconstruction_state"] != state:
                    continue
                if category and str(item.get("category") or "未分类") != category:
                    continue
                if query:
                    haystack = " ".join(
                        str(item.get(key) or "")
                        for key in ("title", "author", "category")
                    ).casefold()
                    if query not in haystack:
                        continue
            deconstruction = item.get("deconstruction") or {}
            if mode == "scan" and (
                deconstruction.get("has_quick_preview")
                or deconstruction.get("has_full_report")
                or (
                    deconstruction.get("status") in {"queued", "running"}
                    and not deconstruction.get("can_resume")
                )
            ):
                continue
            if mode == "full" and (
                deconstruction.get("has_full_report")
                or (
                    deconstruction.get("status") in {"queued", "running"}
                    and not deconstruction.get("can_resume")
                )
            ):
                continue
            selected.append(catalog_id)
        return selected

    def create_deconstruction_batch(
        self,
        project_root: Path,
        *,
        mode: str,
        runner_id: str,
        profile_id: str,
        reasoning_effort: Optional[str] = None,
        state: str = "unstarted",
        query: str = "",
        category: str = "",
        catalog_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        project_root = project_root.expanduser().resolve()
        if not project_root.exists() or not (project_root / ".webnovel").exists():
            raise ValueError("当前路径不是有效的小说项目")
        if mode not in {"scan", "full"}:
            raise ValueError("批量拆书仅支持黄金三章或完整拆书")
        runner = resolve_task_runner(
            runner_id, profile_id, reasoning_effort
        )
        selected = self._batch_catalog_ids(
            project_root,
            mode=mode,
            state=state,
            query=query,
            category=category,
            catalog_ids=catalog_ids,
        )
        if not selected:
            raise ValueError("当前选择中没有可启动的作品")
        deconstruction_lookup = self.deconstruction_lookup(
            project_root,
            fresh=True,
        )
        resume_task_ids = {
            str(book_id): str(
                deconstruction_lookup[book_id].get("managed_task_id") or ""
            )
            for book_id in selected
            if (
                book_id in deconstruction_lookup
                and deconstruction_lookup[book_id].get("can_resume")
                and str(
                    deconstruction_lookup[book_id].get("managed_task_id") or ""
                ).strip()
                and str(
                    deconstruction_lookup[book_id].get("ai_session_id") or ""
                ).strip()
                and deconstruction_lookup[book_id].get("resume_strategy")
                == "same_session"
            )
        }

        batch_id = uuid.uuid4().hex[:12]
        batch_dir = self._deconstruction_batch_dir(project_root)
        batch_dir.mkdir(parents=True, exist_ok=True)
        batch_path = batch_dir / f"{batch_id}.json"
        batch = {
            "id": batch_id,
            "mode": mode,
            "status": "queued",
            "state_filter": state,
            "query": query.strip(),
            "category": category.strip(),
            "book_ids": selected,
            "resume_task_ids": resume_task_ids,
            "child_task_ids": [],
            "session_strategy": "one_book_one_session",
            "session_total": len(selected),
            "session_ids": [],
            "total": len(selected),
            "cursor": 0,
            "started": 0,
            "reused": 0,
            "failed": 0,
            "finished": 0,
            "project_root": str(project_root),
            "runner_requested": runner["runner_id"],
            "runner_name": runner["runner_name"],
            "model_requested": runner["profile_id"],
            "model_name": runner["profile_name"],
            "reasoning_requested": runner.get("reasoning_effort", "default"),
            "reasoning_name": runner.get("reasoning_name", "跟随工具默认"),
            "parallel_limit": max_parallel_tasks(),
            "current_stage": "等待批次调度器启动",
            "message": f"已选择 {len(selected)} 本作品，将按并行空位持续补位",
            "created_at": _now(),
            "updated_at": _now(),
            "pid": None,
        }
        _atomic_write_json(batch_path, batch)
        launcher_log = (batch_dir / f"{batch_id}.launcher.log").open("ab")
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(BATCH_WORKER_PATH),
                    "--batch-file",
                    str(batch_path),
                ],
                cwd=str(APP_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=launcher_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            launcher_log.close()
        batch["pid"] = process.pid
        batch["status"] = "dispatching"
        batch["current_stage"] = "批次调度器已启动"
        batch["updated_at"] = _now()
        _atomic_write_json(batch_path, batch)
        return next(
            item
            for item in self.list_deconstruction_batches(project_root)
            if item.get("id") == batch_id
        )

    def list_tasks(self, project_root: Path) -> List[Dict[str, Any]]:
        return self._list_managed_tasks(
            project_root,
            enrich_artifacts=False,
        )

    def get_task(self, project_root: Path, task_id: str) -> Dict[str, Any]:
        if not re.fullmatch(r"[a-f0-9]{12}", task_id):
            raise KeyError("任务不存在")
        path = self._task_dir(project_root) / f"{task_id}.json"
        task = _read_json(path, {})
        if not task:
            raise KeyError("任务不存在")
        return self._project_terminal_task_artifact(task)

    def _launch_task_worker(
        self,
        task_path: Path,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:
        task_id = str(task["id"])
        task_dir = task_path.parent
        worker_unit = ""
        process_pid = 0
        use_systemd = str(
            os.getenv("LIBRARY_TASK_USE_SYSTEMD") or ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        if use_systemd:
            worker_unit = TASK_WORKER_SYSTEMD_TEMPLATE.format(task_id=task_id)
            subprocess.run(
                ["systemctl", "reset-failed", worker_unit],
                cwd=str(APP_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            started = subprocess.run(
                ["systemctl", "start", worker_unit],
                cwd=str(APP_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            if started.returncode != 0:
                detail = (started.stderr or started.stdout or "").strip()
                raise RuntimeError(
                    f"独立拆书 worker 启动失败：{detail or worker_unit}"
                )
            shown = subprocess.run(
                ["systemctl", "show", "--property=MainPID", "--value", worker_unit],
                cwd=str(APP_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            try:
                process_pid = int((shown.stdout or "0").strip())
            except ValueError:
                process_pid = 0
            if process_pid <= 0:
                raise RuntimeError(f"独立拆书 worker 未产生有效 PID：{worker_unit}")
        else:
            launcher_log = (task_dir / f"{task_id}.launcher.log").open("ab")
            try:
                process = subprocess.Popen(
                    [sys.executable, str(WORKER_PATH), "--task-file", str(task_path)],
                    cwd=str(APP_ROOT),
                    stdin=subprocess.DEVNULL,
                    stdout=launcher_log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                process_pid = process.pid
            finally:
                launcher_log.close()
        latest = _read_json(task_path, task)
        latest["pid"] = process_pid
        if worker_unit:
            latest["worker_unit"] = worker_unit
        if latest.get("status") == "queued":
            latest["status"] = "running"
            latest["message"] = (
                "原 AI 会话断点续跑进程已启动"
                if int(latest.get("resume_count") or 0) > 0
                else "独立任务进程已启动"
            )
        latest["updated_at"] = _now()
        _atomic_write_json(task_path, latest)
        return latest

    def create_task(
        self,
        project_root: Path,
        book_id: int,
        mode: str,
        *,
        output_dir: Optional[str] = None,
        parent_task_id: Optional[str] = None,
        runner_id: Optional[str] = None,
        profile_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> Dict[str, Any]:
        project_root = project_root.expanduser().resolve()
        if not project_root.exists() or not (project_root / ".webnovel").exists():
            raise ValueError("当前路径不是有效的小说项目")
        if mode not in {"auto", "scan", "full"}:
            raise ValueError("未知任务模式")
        book = self.get_book(book_id)
        source_path = Path(book["source_path"]).expanduser().resolve()
        if not source_path.exists():
            raise FileNotFoundError("电子书源文件不存在")

        source_id = str(book.get("source_id") or book_id)
        if output_dir:
            target = Path(output_dir).expanduser().resolve()
            if not _is_within(target, self.global_deconstruction_root):
                raise ValueError("拆书输出目录必须位于全局拆书库中")
        else:
            target = (
                self.global_deconstruction_root
                / f"{_safe_name(book.get('title') or '')}__{_safe_name(source_id, str(book_id))}"
            ).resolve()

        global_state = self.list_global_deconstructions(project_root)
        normalized_title = _normalize_book_identity(book.get("title"))
        existing_global = next(
            (
                item
                for item in global_state["items"]
                if (
                    item.get("catalog_id") == book_id
                    or str(item.get("source_id") or "") == source_id
                    or _normalize_book_identity(item.get("title"))
                    == normalized_title
                )
            ),
            None,
        )
        if existing_global:
            existing_status = existing_global.get("status")
            if existing_status in {"queued", "running"}:
                project_link = self._register_project_deconstruction_link(
                    project_root,
                    Path(existing_global["output_dir"]),
                    book,
                    task_id=str(
                        existing_global.get("managed_task_id") or ""
                    ) or None,
                    mode=mode,
                    association_reason="reused_running_task",
                )
                existing_global["global_reuse"] = True
                existing_global["message"] = (
                    "已匹配全局拆书库中的进行中任务，未重复启动拆书"
                )
                existing_global["project_link"] = project_link
                return existing_global
            if existing_status == "completed" or (
                mode == "scan" and existing_global.get("has_quick_preview")
            ):
                project_link = self._register_project_deconstruction_link(
                    project_root,
                    Path(existing_global["output_dir"]),
                    book,
                    task_id=str(
                        existing_global.get("managed_task_id") or ""
                    ) or None,
                    mode=mode,
                    association_reason="reused_global_artifact",
                )
                existing_global["global_reuse"] = True
                existing_global["message"] = (
                    "全局拆书库已有可用成果，已直接复用，未重复消耗 Token"
                )
                existing_global["project_link"] = project_link
                return existing_global
            if existing_global.get("output_dir"):
                target = Path(existing_global["output_dir"]).expanduser().resolve()

        active_count = sum(
            1
            for existing in global_state["items"]
            if existing.get("status") in {"queued", "running"}
        )
        parallel_limit = max_parallel_tasks()
        if active_count >= parallel_limit:
            raise ValueError(
                f"全局拆书库已有 {active_count} 个任务运行中，"
                f"当前并行上限为 {parallel_limit}"
            )
        runner = (
            resolve_task_runner(runner_id, profile_id, reasoning_effort)
            if runner_id or profile_id
            else {
                "runner_id": "auto",
                "runner_name": "自动选择可用工具",
                "profile_id": "default",
                "profile_name": "工具默认模型",
                "reasoning_effort": "default",
                "reasoning_name": "跟随工具默认",
            }
        )

        task_id = uuid.uuid4().hex[:12]
        ai_session_id = f"library-{task_id}"
        task_dir = self._task_dir(project_root)
        task_dir.mkdir(parents=True, exist_ok=True)
        task_path = task_dir / f"{task_id}.json"
        validated_artifact = existing_global
        if (
            not validated_artifact
            and target.is_dir()
            and _is_within(target, self.global_deconstruction_root)
        ):
            validated_artifact = self._deconstruction_artifact_item(target)
        has_full_result = bool(
            validated_artifact and validated_artifact.get("has_full_report")
        )
        has_scan_result = bool(
            has_full_result
            or (
                validated_artifact
                and validated_artifact.get("has_quick_preview")
            )
        )
        reused = has_full_result or (mode == "scan" and has_scan_result)
        task = {
            "id": task_id,
            "book_id": book_id,
            "source_id": source_id,
            "title": book.get("title") or source_path.stem,
            "author": book.get("author") or "",
            "category": book.get("category") or "",
            "source_path": str(source_path),
            "project_root": str(project_root),
            "output_dir": str(target),
            "requested_mode": mode,
            "entry_mode": mode,
            "execution_mode": None,
            "analysis_band": None,
            "resolved_pipeline": None,
            "skill": None,
            "status": "completed" if reused else "queued",
            "progress": 100 if reused else 0,
            "current_stage": "全局拆书成果复用" if reused else "等待启动",
            "message": (
                "全局拆书库已有可用成果，已直接复用，未重复消耗 Token"
                if reused
                else "任务已入队"
            ),
            "created_at": _now(),
            "updated_at": _now(),
            "parent_task_id": parent_task_id,
            "runner_requested": runner["runner_id"],
            "runner_name": runner["runner_name"],
            "model_requested": runner["profile_id"],
            "model_name": runner["profile_name"],
            "reasoning_requested": runner.get("reasoning_effort", "default"),
            "reasoning_name": runner.get("reasoning_name", "跟随工具默认"),
            "ai_session_id": None if reused else ai_session_id,
            "ai_session_generation": 0,
            "automatic_full_pipeline": mode == "full",
            "parallel_limit": parallel_limit,
            "pid": None,
            "steps": [
                {"id": "validate", "name": "验证只读来源与全局库边界", "status": "pending"},
                {"id": "copy", "name": "链接电子书库只读原文", "status": "pending"},
                {"id": "route", "name": "按字数路由 oh-story 技能", "status": "pending"},
                {"id": "analyze", "name": "执行扫描/拆书阶段", "status": "pending"},
                {"id": "verify", "name": "校验项目内产物", "status": "pending"},
            ],
            "pipeline_stages": [],
            "log_path": str(task_dir / f"{task_id}.log"),
            "last_message_path": str(task_dir / f"{task_id}.last.md"),
            "global_reuse": reused,
        }
        task["project_link"] = self._register_project_deconstruction_link(
            project_root,
            target,
            task,
            task_id=task_id,
            mode=mode,
            association_reason=(
                "reused_global_artifact" if reused else "task_created"
            ),
        )
        _atomic_write_json(task_path, task)
        if reused:
            return task

        return self._launch_task_worker(task_path, task)

    def continue_task(self, project_root: Path, task_id: str) -> Dict[str, Any]:
        previous = self.get_task(project_root, task_id)
        previous = self._reconcile_stalled_managed_task(
            project_root,
            previous,
            persist=True,
        )
        if previous.get("status") not in {"paused", "error"}:
            raise ValueError("只有已暂停或失败的任务可以续跑")
        checkpoint_only_resume = bool(
            previous.get("resume_strategy") == "adopt_checkpoint"
            or previous.get("session_rotation_required")
            or previous.get("pause_reason") == "artifact_validation_failed"
        )
        same_session = bool(
            not checkpoint_only_resume
            and previous.get("ai_session_cleanup") != "deleted"
            and _openclaw_session_exists(
                previous.get("ai_session_id"),
                previous.get("openclaw_agent_id") or "main",
            )
        )
        if previous.get("can_resume") and same_session:
            if _pid_is_alive(previous.get("pid")):
                raise ValueError("原拆书进程仍在运行，无需重复续跑")
            if not str(previous.get("ai_session_id") or "").strip():
                raise ValueError("原 AI 会话标识缺失，不能安全断点续跑")
            if previous.get("ai_session_cleanup") == "deleted":
                raise ValueError("原 AI 会话已被删除，不能冒充原会话续跑")

            global_state = self.list_global_deconstructions(project_root)
            active_count = sum(
                1
                for existing in global_state["items"]
                if existing.get("status") in {"queued", "running"}
            )
            parallel_limit = max_parallel_tasks()
            if active_count >= parallel_limit:
                raise ValueError(
                    f"全局拆书库已有 {active_count} 个任务运行中，"
                    f"当前并行上限为 {parallel_limit}"
                )

            task_path = self._task_dir(project_root) / f"{task_id}.json"
            resumed = dict(previous)
            resumed["status"] = "queued"
            resumed["requested_mode"] = previous.get("requested_mode") or "full"
            resumed["current_stage"] = "等待从原会话断点继续"
            resumed["message"] = "从原 AI 会话与已有 OH-Story 产物断点继续"
            resumed["can_resume"] = False
            resumed["resume_count"] = int(previous.get("resume_count") or 0) + 1
            resumed["resumed_at"] = _now()
            resumed["updated_at"] = _now()
            resumed["pid"] = None
            resumed.pop("finished_at", None)
            resumed.pop("ai_session_cleanup_error", None)
            resumed["project_link"] = (
                self._register_project_deconstruction_link(
                    project_root,
                    Path(str(resumed["output_dir"])),
                    resumed,
                    task_id=task_id,
                    mode=str(resumed.get("requested_mode") or "full"),
                    association_reason="same_session_resume",
                )
            )
            for step in resumed.get("steps", []):
                if step.get("status") == "error":
                    step["status"] = "pending"
                    step.pop("finished_at", None)
            _atomic_write_json(task_path, resumed)
            return self._launch_task_worker(task_path, resumed)
        if previous.get("can_resume"):
            return self.create_task(
                project_root,
                int(previous["book_id"]),
                "full",
                output_dir=previous["output_dir"],
                parent_task_id=task_id,
                runner_id=previous.get("runner_requested"),
                profile_id=previous.get("model_requested"),
                reasoning_effort=previous.get("reasoning_requested"),
            )
        if previous.get("status") == "error":
            raise ValueError(
                "该失败不是 Token/额度耗尽，需先查看日志修复原因，不能直接续跑"
            )
        return self.create_task(
            project_root,
            int(previous["book_id"]),
            "full",
            output_dir=previous["output_dir"],
            parent_task_id=task_id,
            runner_id=previous.get("runner_requested"),
            profile_id=previous.get("model_requested"),
            reasoning_effort=previous.get("reasoning_requested"),
        )

    def read_task_log(
        self, project_root: Path, task_id: str, max_chars: int = 12000
    ) -> str:
        task = self.get_task(project_root, task_id)
        path = Path(task.get("log_path") or "")
        if not path.exists() or not _is_within(path, self._task_dir(project_root)):
            return ""
        with path.open("rb") as handle:
            size = path.stat().st_size
            handle.seek(max(0, size - max_chars * 2))
            return handle.read().decode("utf-8", errors="replace")[-max_chars:]


def _read_json_text(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except Exception:
        return default


def get_electronic_library_service() -> ElectronicLibraryService:
    return ElectronicLibraryService()
