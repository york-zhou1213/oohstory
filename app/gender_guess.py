"""External gender-guess provider for Chinese personal names.

Client for wuruihong.com/tools/guess-gender with CSRF session handling,
circuit breaker, rate control, MySQL cache, cross-process claim dedup,
and fail-open semantics.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import unicodedata
from hashlib import sha256
from typing import Any
from uuid import uuid4

import requests

LOGGER = logging.getLogger("oohstory.gender_guess")

PROVIDER = "wuruihong-guess-gender"
ALLOWED_ENDPOINT = "https://wuruihong.com/tools/guess-gender"

_TOKEN_RE = re.compile(r"_token:\s*'([^']{20,80})'")
_CHINESE_NAME_RE = re.compile(r"^[㐀-鿿]{2,4}$")

_TITLE_SUFFIXES = (
    "大人", "老板", "老师", "将军", "教授", "师傅", "掌柜", "大夫",
    "长老", "前辈", "道长", "师兄", "师弟", "师姐", "师妹",
    "先生", "小姐", "公子", "陛下", "殿下", "阁下",
)
_NON_PERSON_NAMES = frozenset({
    "作者", "分类", "状态", "书名", "标题", "章节", "章节数", "章节名",
    "正文", "内容",
    "简介", "标签", "类型", "作品", "小说", "角色", "主角", "读者",
    "用户", "系统", "当前", "消息", "网站", "平台", "来源", "更新时间",
    "高声", "低声", "轻声", "大声", "沉声", "柔声", "冷声", "怒声",
    "温柔", "冷冷", "关键字", "关键词", "开怀大",
    "解释", "直接", "准备", "回头", "转头", "偏头", "边走",
    "眼睛", "泪水", "多年", "外面", "松开", "果然", "最终",
    "什么", "小声", "轻轻", "微微", "缓缓", "狠狠", "哈哈",
    "宽慰", "调侃", "含混", "径直", "放声", "开玩", "照完相",
    "席间", "干脆", "连连", "车子调", "马上联", "马经", "曲终",
    "淡淡", "慢慢", "呵呵", "说完", "问完", "答完", "边点头",
})
_COMMON_SINGLE_SURNAMES = frozenset(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华"
    "金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花"
    "方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时"
    "傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝"
    "明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾"
    "路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯"
    "管卢莫经房裘缪干解应宗丁宣邓郁单杭洪包诸左石崔吉龚程嵇"
    "邢滑裴陆荣翁荀羊甄曲封芮储靳汲邴糜松井段富巫乌焦巴弓牧"
    "隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘"
    "景詹束龙叶幸司韶郜黎蓟薄印宿白蒲邰从鄂索咸籍赖卓蔺屠蒙"
    "池乔阴郁胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍郤璩桑"
    "桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习宦艾"
    "鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧战"
    "沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养"
    "鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公冼言肖洛"
)
_COMPOUND_SURNAMES = (
    "欧阳", "太史", "端木", "上官", "司马", "东方", "独孤", "南宫",
    "万俟", "闻人", "夏侯", "诸葛", "尉迟", "公羊", "赫连", "澹台",
    "皇甫", "宗政", "濮阳", "公冶", "太叔", "申屠", "公孙", "慕容",
    "仲孙", "钟离", "长孙", "宇文", "司徒", "鲜于", "司空", "闾丘",
    "子车", "亓官", "司寇", "巫马", "公西", "颛孙", "壤驷", "公良",
    "漆雕", "乐正", "宰父", "谷梁", "拓跋", "夹谷", "轩辕", "令狐",
    "段干", "百里", "呼延", "东郭", "南门", "羊舌", "微生",
    "淳于", "单于", "范姜", "张廖", "张简", "图门", "公叔", "乌孙",
    "完颜", "马佳", "佟佳", "富察", "费莫", "西门", "东门", "左丘",
    "梁丘",
)
_EXTENDED_SAFE_SINGLE_SURNAMES = frozenset(
    "昝酆贲钮於惠麴羿怀溥阳僪殳逮盍冼邝覃辜谌佘佟牟商亓笪谯缑帅禚"
)
_COMMON_GIVEN_NAME_CHARS = frozenset(
    "伟刚勇毅俊杰强峰龙鹏斌涛宇浩轩明健志辉海山波胜武飞彬顺信豪"
    "博凯阳辰晨霖霆泽洋源森林川航远达成安宁平康睿哲文武国华建"
    "军超磊鑫铭锋锐翔卓凡诚彦言知时元英亚文岂郁逸岚"
    "娜妍媛姝芳婷娟莲雪薇秀英慧巧美静淑惠玲芬燕彩兰凤洁梅琳璃"
    "霞香月莺艳佳嘉欣怡倩梦瑶诗琪晴曼雅雯妤婧悦彤蕾思"
)

CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 4.0
MAX_RETRIES = 1
RATE_INTERVAL = 2.0
CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_RESET_SECONDS = 300
FAILURE_RETRY_HOURS = 24
CLAIM_LEASE_SECONDS = 30


def _normalize_name(name: str) -> str:
    return unicodedata.normalize("NFKC", str(name or "")).strip()


def _is_clean_chinese_name(name: str) -> bool:
    clean = _normalize_name(name)
    if not _CHINESE_NAME_RE.match(clean):
        return False
    if clean in _NON_PERSON_NAMES:
        return False
    if clean.endswith(_TITLE_SUFFIXES):
        return False
    compound = next(
        (surname for surname in _COMPOUND_SURNAMES if clean.startswith(surname)),
        "",
    )
    if compound:
        return 3 <= len(clean) <= 4
    if 2 <= len(clean) <= 3 and clean[0] in _COMMON_SINGLE_SURNAMES:
        return True
    if 2 <= len(clean) <= 3 and clean[0] in _EXTENDED_SAFE_SINGLE_SURNAMES:
        given = clean[1:]
        return bool(given and any(char in _COMMON_GIVEN_NAME_CHARS for char in given))
    return False


class _CircuitBreaker:
    __slots__ = ("_threshold", "_reset_seconds", "_failures", "_opened_at", "_lock")

    def __init__(self, threshold: int = CIRCUIT_FAILURE_THRESHOLD,
                 reset_seconds: float = CIRCUIT_RESET_SECONDS) -> None:
        self._threshold = threshold
        self._reset_seconds = reset_seconds
        self._failures = 0
        self._opened_at = 0.0
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._failures < self._threshold:
                return False
            if time.monotonic() - self._opened_at >= self._reset_seconds:
                self._failures = 0
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self._threshold:
                self._opened_at = time.monotonic()


class GenderGuessClient:
    """HTTP client for the wuruihong.com gender-guess endpoint.

    Thread-safe.  One instance per process is sufficient.
    """

    def __init__(self) -> None:
        self._session: requests.Session | None = None
        self._csrf_token: str = ""
        self._session_lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._last_request_at = 0.0
        self._circuit = _CircuitBreaker()
        self._concurrency = threading.Semaphore(1)

    def _ensure_session(self, *, force_refresh: bool = False) -> tuple[requests.Session, str]:
        with self._session_lock:
            if not force_refresh and self._session is not None and self._csrf_token:
                return self._session, self._csrf_token
            session = requests.Session()
            session.headers.update({
                "User-Agent": "OOHStory-Audiobook/1.0",
                "Accept": "text/html",
            })
            response = session.get(
                ALLOWED_ENDPOINT,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                allow_redirects=False,
            )
            if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
                raise ValueError("gender-guess endpoint returned redirect")
            response.raise_for_status()
            match = _TOKEN_RE.search(response.text)
            if not match:
                raise ValueError("CSRF token not found in response")
            self._session = session
            self._csrf_token = match.group(1)
            return self._session, self._csrf_token

    def _rate_wait(self) -> None:
        with self._rate_lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < RATE_INTERVAL:
                time.sleep(RATE_INTERVAL - elapsed)
            self._last_request_at = time.monotonic()

    def lookup(self, name: str) -> dict[str, Any] | None:
        """Look up gender for a clean Chinese personal name.

        Returns {"name": str, "gender": str, "percent": float} on success,
        or None on any failure (fail-open).
        """
        name = _normalize_name(name)
        if not _is_clean_chinese_name(name):
            return None
        if self._circuit.is_open:
            LOGGER.debug("gender guess circuit open, skipping lookup")
            return None
        if not self._concurrency.acquire(timeout=5):
            return None
        try:
            return self._do_lookup(name)
        finally:
            self._concurrency.release()

    def _do_lookup(self, name: str) -> dict[str, Any] | None:
        for attempt in range(1 + MAX_RETRIES):
            try:
                force = attempt > 0
                session, token = self._ensure_session(force_refresh=force)
                self._rate_wait()
                response = session.post(
                    ALLOWED_ENDPOINT,
                    data={"_token": token, "name": name},
                    headers={
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                    allow_redirects=False,
                )
                if response.status_code == 419:
                    continue
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or not data.get("success"):
                    LOGGER.debug("gender guess returned unsuccessful response")
                    self._circuit.record_failure()
                    return None
                result = data.get("data")
                if not isinstance(result, dict):
                    self._circuit.record_failure()
                    return None
                response_name = unicodedata.normalize(
                    "NFKC", str(result.get("name") or "")
                ).strip()
                if response_name != name:
                    LOGGER.debug("gender guess response name mismatch")
                    self._circuit.record_failure()
                    return None
                gender = str(result.get("gender") or "").strip().lower()
                if gender not in ("male", "female"):
                    self._circuit.record_success()
                    return None
                try:
                    percent = float(result.get("percent") or 0)
                except (TypeError, ValueError):
                    self._circuit.record_failure()
                    return None
                if not 0.0 <= percent <= 1.0:
                    self._circuit.record_failure()
                    return None
                self._circuit.record_success()
                return {"name": name, "gender": gender, "percent": round(percent, 4)}
            except requests.RequestException as exc:
                LOGGER.debug("gender guess request failed: %s", type(exc).__name__)
                self._circuit.record_failure()
            except (ValueError, KeyError, TypeError) as exc:
                LOGGER.debug("gender guess parse failed: %s", type(exc).__name__)
                self._circuit.record_failure()
                return None
        LOGGER.debug("gender guess exhausted retries")
        return None


class GenderGuessCache:
    """MySQL-backed global name->gender cache with cross-process claim dedup."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def get(self, name: str) -> dict[str, Any] | None:
        name = _normalize_name(name)
        name_key = sha256(name.encode()).digest()
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT canonical_name,gender,confidence,provider,raw_percent,"
                    "status,failure_reason,lookup_count,created_at,updated_at,expires_at "
                    "FROM gender_guess_cache "
                    "WHERE name_key=%s AND status='success' "
                    "AND (expires_at IS NULL OR expires_at > UTC_TIMESTAMP(6))",
                    (name_key,),
                )
                row = cur.fetchone()
                if row:
                    return dict(row)
        return None

    def get_many(self, names: list[str]) -> dict[str, dict[str, Any]]:
        names = [_normalize_name(name) for name in names]
        if not names:
            return {}
        keys = [(sha256(n.encode()).digest(), n) for n in names]
        result: dict[str, dict[str, Any]] = {}
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                placeholders = ",".join(["%s"] * len(keys))
                cur.execute(
                    "SELECT canonical_name,gender,confidence,provider,raw_percent,"
                    "status,failure_reason "
                    f"FROM gender_guess_cache WHERE name_key IN ({placeholders}) "
                    "AND status='success' "
                    "AND (expires_at IS NULL OR expires_at > UTC_TIMESTAMP(6))",
                    [k for k, _ in keys],
                )
                for row in cur.fetchall():
                    result[str(row["canonical_name"])] = dict(row)
        return result

    def is_recently_failed(self, name: str) -> bool:
        name = _normalize_name(name)
        name_key = sha256(name.encode()).digest()
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM gender_guess_cache "
                    "WHERE name_key=%s AND status='failed' "
                    "AND updated_at > DATE_SUB(UTC_TIMESTAMP(6), INTERVAL %s HOUR)",
                    (name_key, FAILURE_RETRY_HOURS),
                )
                return cur.fetchone() is not None

    def try_claim(self, name: str, owner: str,
                  lease_seconds: float = CLAIM_LEASE_SECONDS) -> bool:
        """Atomically claim a name for external lookup.

        Returns True if this caller now owns the lookup claim.
        Another worker holding an unexpired claim causes immediate False.
        """
        name = _normalize_name(name)
        name_key = sha256(name.encode()).digest()
        with self._pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO gender_guess_cache "
                    "(name_key,canonical_name,gender,confidence,provider,status,"
                    "lookup_count,claim_owner,claim_until) "
                    "VALUES (%s,%s,'unknown',0,%s,'pending',0,%s,"
                    "DATE_ADD(UTC_TIMESTAMP(6),INTERVAL %s SECOND)) "
                    "ON DUPLICATE KEY UPDATE "
                    "claim_owner=IF("
                    "  status='success' OR "
                    "  (claim_owner IS NOT NULL AND claim_until>=UTC_TIMESTAMP(6)),"
                    "  claim_owner, VALUES(claim_owner)),"
                    "claim_until=IF("
                    "  status='success' OR "
                    "  (claim_owner IS NOT NULL AND claim_until>=UTC_TIMESTAMP(6)),"
                    "  claim_until, VALUES(claim_until))",
                    (name_key, name, PROVIDER, owner, lease_seconds),
                )
                cur.execute(
                    "SELECT 1 FROM gender_guess_cache "
                    "WHERE name_key=%s AND claim_owner=%s",
                    (name_key, owner),
                )
                return cur.fetchone() is not None

    def release_claim(self, name: str, owner: str) -> None:
        """Release a lookup claim. Only clears if we still own it."""
        name = _normalize_name(name)
        name_key = sha256(name.encode()).digest()
        with self._pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE gender_guess_cache SET claim_owner=NULL,claim_until=NULL "
                    "WHERE name_key=%s AND claim_owner=%s",
                    (name_key, owner),
                )

    def put(self, name: str, gender: str, confidence: float,
            raw_percent: float | None = None, *, owner: str) -> bool:
        """Commit a success only while ``owner`` still holds the claim."""
        name = _normalize_name(name)
        name_key = sha256(name.encode()).digest()
        with self._pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE gender_guess_cache SET canonical_name=%s,gender=%s,"
                    "confidence=%s,provider=%s,raw_percent=%s,status='success',"
                    "failure_reason=NULL,lookup_count=lookup_count+1,expires_at=NULL,"
                    "claim_owner=NULL,claim_until=NULL "
                    "WHERE name_key=%s AND claim_owner=%s",
                    (name, gender, round(confidence, 4), PROVIDER, raw_percent,
                     name_key, owner),
                )
                return bool(cur.rowcount)

    def put_failure(self, name: str, reason: str, *, owner: str) -> bool:
        """Commit a failure only while ``owner`` still holds the claim."""
        name = _normalize_name(name)
        name_key = sha256(name.encode()).digest()
        with self._pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE gender_guess_cache SET canonical_name=%s,gender='unknown',"
                    "confidence=0,provider=%s,status='failed',failure_reason=%s,"
                    "lookup_count=lookup_count+1,claim_owner=NULL,claim_until=NULL "
                    "WHERE name_key=%s AND claim_owner=%s AND status<>'success'",
                    (name, PROVIDER, str(reason)[:200], name_key, owner),
                )
                return bool(cur.rowcount)


_default_client: GenderGuessClient | None = None
_client_lock = threading.Lock()


def _get_client() -> GenderGuessClient:
    global _default_client
    with _client_lock:
        if _default_client is None:
            _default_client = GenderGuessClient()
        return _default_client


def _claim_owner_id() -> str:
    return f"{os.getpid()}-{threading.current_thread().ident}-{uuid4().hex[:12]}"[:40]


def lookup_gender_cached(
    name: str,
    *,
    cache: GenderGuessCache | None = None,
    client: GenderGuessClient | None = None,
) -> dict[str, Any] | None:
    """Look up gender for a name, checking cache first.

    When cache is available, uses cross-process claim dedup to ensure
    at most one worker performs the external HTTP lookup for a given name.

    Returns {"gender": str, "confidence": float, "source": "wuruihong-guess-gender"}
    or None on cache miss + lookup failure (fail-open).
    """
    name = _normalize_name(name)
    if not _is_clean_chinese_name(name):
        return None
    owner: str | None = None
    if cache is not None:
        try:
            cached = cache.get(name)
            if cached and cached.get("gender") in ("male", "female"):
                return {
                    "gender": cached["gender"],
                    "confidence": float(cached.get("confidence") or 0.5),
                    "source": PROVIDER,
                }
            if cache.is_recently_failed(name):
                return None
            owner = _claim_owner_id()
            if not cache.try_claim(name, owner):
                return None
        except Exception:
            LOGGER.debug("gender guess cache claim failed", exc_info=True)
            cache = None
            owner = None
    if client is None:
        client = _get_client()
    try:
        result = client.lookup(name)
        if result is None:
            if cache is not None and owner is not None:
                cache.put_failure(name, "lookup returned None", owner=owner)
            return None
        confidence = float(result.get("percent") or 0.5)
        if cache is not None and owner is not None:
            if not cache.put(
                name, result["gender"], confidence, result.get("percent"), owner=owner
            ):
                cached = cache.get(name)
                if cached and cached.get("gender") in ("male", "female"):
                    return {
                        "gender": cached["gender"],
                        "confidence": float(cached.get("confidence") or 0.5),
                        "source": PROVIDER,
                    }
                return None
        return {
            "gender": result["gender"],
            "confidence": confidence,
            "source": PROVIDER,
        }
    except Exception:
        if cache is not None and owner is not None:
            try:
                cache.release_claim(name, owner)
            except Exception:
                LOGGER.debug("gender guess claim release failed", exc_info=True)
        LOGGER.debug("gender guess lookup failed open", exc_info=True)
        return None


def lookup_gender_cache_only(
    name: str,
    *,
    cache: GenderGuessCache,
) -> dict[str, Any] | None:
    """Check cache only; never makes HTTP calls. Safe for use inside transactions."""
    name = _normalize_name(name)
    if not _is_clean_chinese_name(name):
        return None
    try:
        cached = cache.get(name)
    except Exception:
        LOGGER.debug("gender guess cache read failed open", exc_info=True)
        return None
    if cached and cached.get("gender") in ("male", "female"):
        return {
            "gender": cached["gender"],
            "confidence": float(cached.get("confidence") or 0.5),
            "source": PROVIDER,
        }
    return None


def batch_lookup_cached(
    names: list[str],
    *,
    cache: GenderGuessCache | None = None,
    client: GenderGuessClient | None = None,
) -> dict[str, dict[str, Any]]:
    """Look up gender for multiple names, cache-first, sequential with rate control."""
    clean = [n for n in names if _is_clean_chinese_name(n)]
    if not clean:
        return {}
    results: dict[str, dict[str, Any]] = {}
    uncached: list[str] = []
    if cache is not None:
        cached_batch = cache.get_many(clean)
        for name in clean:
            cached = cached_batch.get(name)
            if cached and cached.get("gender") in ("male", "female"):
                results[name] = {
                    "gender": cached["gender"],
                    "confidence": float(cached.get("confidence") or 0.5),
                    "source": PROVIDER,
                }
            elif not (cache.is_recently_failed(name)):
                uncached.append(name)
    else:
        uncached = clean
    if client is None:
        client = _get_client()
    for name in uncached:
        result = lookup_gender_cached(name, cache=cache, client=client)
        if result:
            results[name] = result
    return results


def is_clean_chinese_name(name: str) -> bool:
    return _is_clean_chinese_name(name)
