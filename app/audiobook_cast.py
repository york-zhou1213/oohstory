from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import logging
import os
import queue
import re
import threading
import time
from typing import Any, Callable
from uuid import uuid4

from .gender_guess import is_clean_chinese_name, lookup_gender_cached
from .audiobook_policy import CAST_ENGINE_VERSION
from .audiobook_snapshot import publish_cast_snapshot


LOGGER = logging.getLogger(__name__)
PROTAGONIST_NAME = "主人公"
STRONG_GENDER_SOURCES = frozenset({
    "explicit-pronoun", "explicit-identity", "first_person", "role_suffix",
})
PROFILE_ACTION_TAILS = frozenset({
    "也", "又", "才", "忙", "便", "却", "在", "没", "把", "将", "只",
    "继", "回", "端", "摊", "垂", "拿", "摆", "越", "浅", "似", "倾",
    "心", "伸", "大", "一", "临", "收", "偏", "满", "翘", "仰", "拎",
    "打", "吃", "早", "则", "怒", "眼", "微", "联", "经", "调",
    "笑", "哭", "问", "答", "喊", "叫", "说", "看", "找", "将",
    "小声", "低声", "高声", "沉声", "轻声", "大声", "冷声", "怒声", "柔声",
    "竖", "双", "点", "苦", "指", "噎", "噗",
})
REJECTED_PROFILE_NAMES = frozenset({
    "解释", "直接", "准备", "回头", "转头", "偏头", "边走",
    "眼睛", "泪水", "多年", "外面", "松开", "果然", "最终",
    "什么", "小声", "轻轻", "微微", "缓缓", "狠狠", "哈哈",
    "宽慰", "调侃", "含混", "径直", "放声", "开玩", "照完相",
    "男孩", "女孩", "男生", "女生", "男子", "女人", "男人",
    "女士", "先生", "老板", "摊主", "司机", "老头", "白痴",
    "叔叔", "婢女", "班主任", "管家",
    "席间", "干脆", "连连", "车子调", "马上联", "马经", "曲终",
    "淡淡", "慢慢", "呵呵", "说完", "问完", "答完", "边点头",
    "一只手", "上来就", "只是微微", "微微一", "忍不住",
    "瞬间就", "突然就",
})


def action_fragment_profile_names(names: list[str]) -> set[str]:
    """Find persisted ``known actor + grammar/action tail`` identities."""
    established = {
        str(name or "").strip() for name in names if str(name or "").strip()
    }
    return {
        name
        for name in established
        if any(
            name.endswith(tail)
            and name != tail
            and name[:-len(tail)] in established
            for tail in PROFILE_ACTION_TAILS
        )
    }


def _merge_external_gender(
    character: dict[str, Any], result: dict[str, Any]
) -> None:
    external_gender = str(result.get("gender") or "unknown")
    external_confidence = float(result.get("confidence") or 0.0)
    if external_gender not in {"male", "female"}:
        return
    current_gender = str(character.get("gender") or "unknown")
    source = str(character.get("gender_source") or "unknown")
    character["_external_gender_checked"] = True
    character["_external_gender"] = external_gender
    character["_external_gender_confidence"] = external_confidence
    if current_gender in {"male", "female"} and current_gender != external_gender:
        character["_gender_review_required"] = True
        if source not in STRONG_GENDER_SOURCES | {"context"}:
            character["gender"] = external_gender
            character["gender_confidence"] = external_confidence
            character["gender_source"] = "external"
        return
    if source not in STRONG_GENDER_SOURCES | {"context", "external"}:
        character["gender"] = external_gender
        character["gender_confidence"] = external_confidence
        character["gender_source"] = "external"
    elif current_gender == external_gender:
        character["gender_confidence"] = max(
            float(character.get("gender_confidence") or 0.0),
            external_confidence,
        )


@dataclass(frozen=True)
class CastScanPlan:
    catalog_id: int
    book_public_id: str
    content_revision: str
    chapter_ids: tuple[int, ...]


class CastPrewarmManager:
    """Build one reusable, resumable cast profile per book in MySQL."""

    LEASE_SECONDS = 120
    MAX_ERRORS = 5

    def __init__(
        self,
        repository: Any,
        *,
        analyzer: Callable[[str, list[dict[str, Any]]], tuple[list[dict[str, Any]], list[dict[str, Any]]]],
        existing_cast: Callable[[int], list[dict[str, Any]]],
        resolve_cast: Callable[[int, list[dict[str, Any]]], dict[str, dict[str, Any]]],
        gender_cache: Any | None = None,
        gender_client: Any | None = None,
        gender_guess_enabled: bool = False,
        gender_guess_threshold: float = 0.7,
    ) -> None:
        self.repository = repository
        self.mysql = getattr(repository, "_mysql", None)
        self.analyzer = analyzer
        self.existing_cast = existing_cast
        self.resolve_cast = resolve_cast
        self._gender_cache = gender_cache
        self._gender_client = gender_client
        self._gender_guess_enabled = gender_guess_enabled
        self._gender_guess_threshold = gender_guess_threshold
        self.enabled = self.mysql is not None and os.getenv(
            "OOHSTORY_CAST_PREWARM_ENABLED", "1"
        ).strip().casefold() not in {"0", "false", "no", "off"}
        self.worker_enabled = self.enabled and os.getenv(
            "OOHSTORY_CAST_PREWARM_WORKER", "1"
        ).strip().casefold() not in {"0", "false", "no", "off"}
        self._requests: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._queued: set[str] = set()
        self._queued_lock = threading.Lock()
        self._wake = threading.Event()
        self._start_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.worker_enabled:
            return
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="oohstory-cast-prewarm",
                daemon=True,
            )
            self._thread.start()

    def request(self, book_public_id: str) -> None:
        if not self.enabled:
            return
        book_id = str(book_public_id or "").strip()
        if not book_id:
            return
        if not self.worker_enabled:
            # Reader processes are producers only.  Persist the deduplicated
            # job and let the resource-capped scanner service claim it.
            try:
                self._ensure_job(book_id)
            except Exception:
                LOGGER.exception("cast prewarm enqueue failed for book=%s", book_id)
            return
        self.start()
        with self._queued_lock:
            if book_id in self._queued:
                return
            self._queued.add(book_id)
        self._requests.put(book_id)
        self._wake.set()

    def join(self) -> None:
        """Keep a dedicated scanner process attached to its worker thread."""
        self.start()
        while self._thread is not None:
            self._thread.join(timeout=60)
            if not self._thread.is_alive():
                raise RuntimeError("audiobook cast worker stopped unexpectedly")

    def _worker_loop(self) -> None:
        while True:
            self._drain_requests()
            try:
                job = self._claim_job()
            except Exception:
                LOGGER.exception("cast prewarm job claim failed")
                self._wake.wait(10)
                self._wake.clear()
                continue
            if job is None:
                self._wake.wait(5)
                self._wake.clear()
                continue
            try:
                self._run_job(job)
            except Exception as exc:
                LOGGER.exception(
                    "cast prewarm failed for catalog_id=%s", job.get("catalog_id")
                )
                self._record_failure(job, exc)

    def _drain_requests(self) -> None:
        while True:
            try:
                book_id = self._requests.get_nowait()
            except queue.Empty:
                return
            try:
                self._ensure_job(book_id)
            except Exception:
                LOGGER.exception("cast prewarm enqueue failed for book=%s", book_id)
            finally:
                with self._queued_lock:
                    self._queued.discard(book_id)

    def _scan_plan(self, book_public_id: str) -> CastScanPlan:
        payload = self.repository.audiobook_cast_scan_plan(book_public_id)
        chapter_ids = tuple(int(value) for value in payload["chapter_ids"])
        content_revision = str(payload["content_revision"])
        if not chapter_ids or len(content_revision) != 64:
            raise ValueError("invalid audiobook cast scan plan")
        engine_version = os.getenv(
            "OOHSTORY_CAST_ENGINE_VERSION", CAST_ENGINE_VERSION
        )
        # Profile rows are keyed by catalog and character, not by engine.
        # Fold the analyzer version into scan_revision so a rule upgrade resets
        # counters instead of adding a second full scan onto the old totals.
        revision = sha256(
            f"{content_revision}\0{engine_version}".encode("utf-8")
        ).hexdigest()
        return CastScanPlan(
            catalog_id=int(payload["catalog_id"]),
            book_public_id=str(payload["book_public_id"]),
            content_revision=revision,
            chapter_ids=chapter_ids,
        )

    def _ensure_job(self, book_public_id: str) -> CastScanPlan:
        plan = self._scan_plan(book_public_id)
        with self.mysql.pool.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO audiobook_cast_scan_jobs "
                    "(catalog_id,book_public_id,content_revision,engine_version,status,"
                    "total_chapters,processed_chapters,next_attempt_at) "
                    "VALUES (%s,%s,UNHEX(%s),%s,'pending',%s,0,UTC_TIMESTAMP(6)) "
                    "ON DUPLICATE KEY UPDATE "
                    "book_public_id=VALUES(book_public_id),"
                    "status=IF(content_revision=VALUES(content_revision) "
                    "AND engine_version=VALUES(engine_version) "
                    "AND status IN ('complete','running'),status,'pending'),"
                    "processed_chapters=IF(content_revision=VALUES(content_revision) "
                    "AND engine_version=VALUES(engine_version),"
                    "LEAST(processed_chapters,VALUES(total_chapters)),0),"
                    "last_chapter_id=IF(content_revision=VALUES(content_revision) "
                    "AND engine_version=VALUES(engine_version),"
                    "last_chapter_id,NULL),"
                    "completed_at=IF(content_revision=VALUES(content_revision) "
                    "AND engine_version=VALUES(engine_version) "
                    "AND status='complete',completed_at,NULL),"
                    "error_count=IF(content_revision=VALUES(content_revision) "
                    "AND engine_version=VALUES(engine_version) "
                    "AND status IN ('complete','running'),error_count,0),"
                    "last_error=IF(status='running',last_error,NULL),"
                    "lease_token=IF(status='running',lease_token,NULL),"
                    "lease_until=IF(status='running',lease_until,NULL),"
                    "next_attempt_at=IF(status='running',next_attempt_at,UTC_TIMESTAMP(6)),"
                    "total_chapters=VALUES(total_chapters),"
                    "engine_version=VALUES(engine_version),"
                    "content_revision=VALUES(content_revision)",
                    (
                        plan.catalog_id,
                        plan.book_public_id,
                        plan.content_revision,
                        os.getenv("OOHSTORY_CAST_ENGINE_VERSION", CAST_ENGINE_VERSION),
                        len(plan.chapter_ids),
                    ),
                )
        return plan

    def _claim_job(self) -> dict[str, Any] | None:
        token = uuid4().hex
        engine_version = os.getenv(
            "OOHSTORY_CAST_ENGINE_VERSION", CAST_ENGINE_VERSION
        )
        with self.mysql.pool.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT catalog_id,book_public_id,content_revision,total_chapters,"
                    "processed_chapters,last_chapter_id,error_count "
                    "FROM audiobook_cast_scan_jobs "
                    "WHERE ((status='pending' AND next_attempt_at<=UTC_TIMESTAMP(6)) "
                    "OR (status='running' AND lease_until<UTC_TIMESTAMP(6))) "
                    "AND error_count<%s AND engine_version=%s "
                    "ORDER BY updated_at,catalog_id "
                    "LIMIT 1 FOR UPDATE SKIP LOCKED",
                    (self.MAX_ERRORS, engine_version),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                cursor.execute(
                    "UPDATE audiobook_cast_scan_jobs SET status='running',"
                    "lease_token=%s,lease_until=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL %s SECOND),"
                    "started_at=COALESCE(started_at,UTC_TIMESTAMP(6)) "
                    "WHERE catalog_id=%s",
                    (token, self.LEASE_SECONDS, int(row["catalog_id"])),
                )
        return {**dict(row), "lease_token": token}

    @staticmethod
    def _revision_hex(value: Any) -> str:
        if isinstance(value, memoryview):
            value = value.tobytes()
        if isinstance(value, (bytes, bytearray)):
            return bytes(value).hex()
        return str(value or "")

    def _run_job(self, job: dict[str, Any]) -> None:
        plan = self._scan_plan(str(job["book_public_id"]))
        expected_revision = self._revision_hex(job["content_revision"])
        if plan.content_revision != expected_revision:
            self._ensure_job(plan.book_public_id)
            return
        position = min(max(int(job.get("processed_chapters") or 0), 0), len(plan.chapter_ids))
        token = str(job["lease_token"])
        while position < len(plan.chapter_ids):
            chapter_id = plan.chapter_ids[position]
            chapter = self.repository.reader_chapter(plan.book_public_id, chapter_id)
            existing = self.existing_cast(plan.catalog_id)
            characters, segments = self.analyzer(
                str(chapter.get("content") or ""),
                existing,
            )
            characters = self._eligible_characters(characters, existing)
            if self._gender_guess_enabled:
                self._enrich_unknown_genders(characters)
            cast = self.resolve_cast(plan.catalog_id, characters)
            rows = self._profile_rows(cast, segments, chapter_id)
            self._checkpoint(
                plan,
                token=token,
                processed_chapters=position + 1,
                chapter_id=chapter_id,
                profiles=rows,
            )
            position += 1
            time.sleep(0.01)
        latest_plan = self._scan_plan(plan.book_public_id)
        if latest_plan.content_revision != plan.content_revision:
            self._ensure_job(plan.book_public_id)
            return
        self._complete(plan, token)

    @staticmethod
    def _eligible_characters(
        characters: list[dict[str, Any]],
        existing: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Keep strong dialogue actors and already-established cast members."""
        known_names = {
            str(value).strip()
            for item in existing
            if bool(item.get("_current_scan_revision"))
            for value in (
                item.get("canonical_name"),
                *(item.get("aliases") or []),
            )
            if str(value or "").strip()
        }
        return [
            character
            for character in characters
            if (
                str(character.get("canonical_name") or "") == PROTAGONIST_NAME
                or (
                    bool(character.get("_external_gender_lookup"))
                    and CastPrewarmManager._plausible_persisted_name(
                        str(character.get("canonical_name") or "")
                    )
                )
                or (
                    bool(character.get("_scanner_identity_verified"))
                    and 2 <= len(
                        str(character.get("canonical_name") or "").strip()
                    ) <= 4
                    and CastPrewarmManager._plausible_persisted_name(
                        str(character.get("canonical_name") or "")
                    )
                )
                or (
                    str(character.get("canonical_name") or "") in known_names
                    and CastPrewarmManager._plausible_persisted_name(
                        str(character.get("canonical_name") or "")
                    )
                )
            )
        ]

    @staticmethod
    def _plausible_persisted_name(name: str) -> bool:
        clean = str(name or "").strip(" ·")
        if not 1 <= len(clean) <= 20:
            return False
        if clean in REJECTED_PROFILE_NAMES:
            return False
        if is_clean_chinese_name(clean):
            return True
        return bool(
            "·" in clean
            or any("A" <= char <= "Z" or "a" <= char <= "z" for char in clean)
            or re.fullmatch(r"(?:小|阿|老)[\u3400-\u9fff]{1,2}", clean)
            or re.fullmatch(
                r"[\u3400-\u9fff]{1,3}(?:姐|哥|弟|叔|婶|妈|姨|爷|"
                r"总|少|队长|律师|太太|夫人)",
                clean,
            )
            or re.fullmatch(r"[\u3400-\u9fff]{1,3}儿", clean)
            or (len(clean) == 2 and clean[0] == clean[1])
        )

    def _enrich_unknown_genders(
        self, characters: list[dict[str, Any]], *, max_new_lookups: int | None = None,
    ) -> None:
        # ``max_new_lookups`` is retained for call compatibility.  A hard
        # per-chapter cap permanently starved one-off cameo names; the scanner
        # is already a single resource-capped service and the HTTP client is
        # rate limited, so every eligible name now reaches this evidence step.
        del max_new_lookups
        for character in characters:
            name = str(character.get("canonical_name") or "")
            if name == PROTAGONIST_NAME:
                continue
            if not bool(character.get("_external_gender_lookup")):
                continue
            if not is_clean_chinese_name(name):
                continue
            if self._gender_cache is not None:
                try:
                    cached = self._gender_cache.get(name)
                except Exception:
                    LOGGER.debug("gender guess cache read failed", exc_info=True)
                    cached = None
                if cached and cached.get("gender") in ("male", "female"):
                    conf = float(cached.get("confidence") or 0.5)
                    if conf >= self._gender_guess_threshold:
                        _merge_external_gender(character, {
                            "gender": cached["gender"],
                            "confidence": conf,
                        })
                    continue
            try:
                result = lookup_gender_cached(
                    name,
                    cache=self._gender_cache,
                    client=self._gender_client,
                )
            except Exception:
                LOGGER.debug("gender guess failed for prewarm character", exc_info=True)
                continue
            if result and result["gender"] in ("male", "female"):
                if float(result.get("confidence") or 0) >= self._gender_guess_threshold:
                    _merge_external_gender(character, result)

    @staticmethod
    def _profile_rows(
        cast: dict[str, dict[str, Any]],
        segments: list[dict[str, Any]],
        chapter_id: int,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for source_name, character in cast.items():
            canonical = str(character.get("canonical_name") or source_name).strip()
            aliases = {
                canonical,
                source_name,
                *(
                    str(value).strip()
                    for value in character.get("aliases", [])
                    if str(value).strip()
                ),
            }
            dialogue_count = sum(
                1
                for segment in segments
                if segment.get("kind") == "dialogue"
                and str(segment.get("speaker") or "") in aliases
            )
            mention_count = 0
            for segment in segments:
                text = str(segment.get("text") or "")
                mention_count += sum(text.count(alias) for alias in aliases if alias)
            raw_key = character.get("character_key")
            if isinstance(raw_key, memoryview):
                raw_key = raw_key.tobytes()
            if not isinstance(raw_key, (bytes, bytearray)) or len(raw_key) != 32:
                continue
            rows.append(
                {
                    "character_key": bytes(raw_key),
                    "canonical_name": canonical,
                    "role_type": "protagonist"
                    if canonical == PROTAGONIST_NAME
                    else "unclassified",
                    "mention_count": max(mention_count, dialogue_count, 1),
                    "dialogue_count": dialogue_count,
                    "chapter_count": 1,
                    "chapter_id": int(chapter_id),
                    "voice_locked": int(bool(character.get("voice_locked"))),
                }
            )
        return rows

    def _checkpoint(
        self,
        plan: CastScanPlan,
        *,
        token: str,
        processed_chapters: int,
        chapter_id: int,
        profiles: list[dict[str, Any]],
    ) -> None:
        with self.mysql.pool.transaction() as connection:
            with connection.cursor() as cursor:
                for profile in profiles:
                    cursor.execute(
                        "INSERT INTO audiobook_character_profiles "
                        "(catalog_id,character_key,canonical_name,scan_revision,"
                        "role_type,mention_count,dialogue_count,chapter_count,"
                        "first_chapter_id,last_chapter_id,voice_locked) "
                        "VALUES (%s,%s,%s,UNHEX(%s),%s,%s,%s,%s,%s,%s,%s) "
                        "ON DUPLICATE KEY UPDATE "
                        "canonical_name=VALUES(canonical_name),"
                        "role_type=IF(VALUES(role_type)='protagonist','protagonist',"
                        "IF(scan_revision=VALUES(scan_revision),role_type,'unclassified')) ,"
                        "mention_count=IF(scan_revision=VALUES(scan_revision),"
                        "mention_count+VALUES(mention_count),VALUES(mention_count)),"
                        "dialogue_count=IF(scan_revision=VALUES(scan_revision),"
                        "dialogue_count+VALUES(dialogue_count),VALUES(dialogue_count)),"
                        "chapter_count=IF(scan_revision=VALUES(scan_revision),"
                        "chapter_count+1,1),"
                        "first_chapter_id=IF(scan_revision=VALUES(scan_revision),"
                        "LEAST(first_chapter_id,VALUES(first_chapter_id)),VALUES(first_chapter_id)),"
                        "last_chapter_id=IF(scan_revision=VALUES(scan_revision),"
                        "GREATEST(last_chapter_id,VALUES(last_chapter_id)),VALUES(last_chapter_id)),"
                        "voice_locked=VALUES(voice_locked),scan_revision=VALUES(scan_revision)",
                        (
                            plan.catalog_id,
                            profile["character_key"],
                            profile["canonical_name"],
                            plan.content_revision,
                            profile["role_type"],
                            profile["mention_count"],
                            profile["dialogue_count"],
                            profile["chapter_count"],
                            profile["chapter_id"],
                            profile["chapter_id"],
                            profile["voice_locked"],
                        ),
                    )
                cursor.execute(
                    "UPDATE audiobook_cast_scan_jobs SET processed_chapters=%s,"
                    "last_chapter_id=%s,lease_until=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL %s SECOND),"
                    "last_error=NULL WHERE catalog_id=%s AND content_revision=UNHEX(%s) "
                    "AND status='running' AND lease_token=%s",
                    (
                        processed_chapters,
                        chapter_id,
                        self.LEASE_SECONDS,
                        plan.catalog_id,
                        plan.content_revision,
                        token,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("audiobook cast scan lease lost")

    def _complete(self, plan: CastScanPlan, token: str) -> None:
        with self.mysql.pool.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT character_key,canonical_name "
                    "FROM audiobook_character_profiles WHERE catalog_id=%s "
                    "AND scan_revision=UNHEX(%s)",
                    (plan.catalog_id, plan.content_revision),
                )
                profile_rows = [dict(row) for row in cursor.fetchall()]
                contaminated_names = action_fragment_profile_names([
                    str(row.get("canonical_name") or "")
                    for row in profile_rows
                ])
                contaminated_names.update(
                    str(row.get("canonical_name") or "")
                    for row in profile_rows
                    if not self._plausible_persisted_name(
                        str(row.get("canonical_name") or "")
                    )
                )
                contaminated_keys = [
                    row["character_key"]
                    for row in profile_rows
                    if str(row.get("canonical_name") or "")
                    in contaminated_names
                ]
                if contaminated_keys:
                    placeholders = ",".join(["%s"] * len(contaminated_keys))
                    cursor.execute(
                        "DELETE FROM audiobook_character_voices "
                        f"WHERE catalog_id=%s AND character_key IN ({placeholders})",
                        (plan.catalog_id, *contaminated_keys),
                    )
                    cursor.execute(
                        "DELETE FROM audiobook_character_profiles "
                        f"WHERE catalog_id=%s AND scan_revision=UNHEX(%s) "
                        f"AND character_key IN ({placeholders})",
                        (
                            plan.catalog_id,
                            plan.content_revision,
                            *contaminated_keys,
                        ),
                    )
                cursor.execute(
                    "DELETE v FROM audiobook_character_voices v "
                    "LEFT JOIN audiobook_character_profiles p "
                    "ON p.catalog_id=v.catalog_id "
                    "AND p.character_key=v.character_key "
                    "AND p.scan_revision=UNHEX(%s) "
                    "WHERE v.catalog_id=%s AND p.character_key IS NULL",
                    (plan.content_revision, plan.catalog_id),
                )
                cursor.execute(
                    "UPDATE audiobook_character_profiles SET role_type=CASE "
                    "WHEN canonical_name=%s THEN 'protagonist' "
                    "WHEN chapter_count>=3 OR dialogue_count>=5 THEN 'supporting' "
                    "ELSE 'cameo' END "
                    "WHERE catalog_id=%s AND scan_revision=UNHEX(%s)",
                    (PROTAGONIST_NAME, plan.catalog_id, plan.content_revision),
                )
                cursor.execute(
                    "SELECT COUNT(*) AS amount FROM audiobook_character_profiles "
                    "WHERE catalog_id=%s AND scan_revision=UNHEX(%s) "
                    "AND role_type='protagonist'",
                    (plan.catalog_id, plan.content_revision),
                )
                amount = int((cursor.fetchone() or {}).get("amount") or 0)
                if amount == 0:
                    cursor.execute(
                        "SELECT character_key FROM audiobook_character_profiles "
                        "WHERE catalog_id=%s AND scan_revision=UNHEX(%s) "
                        "ORDER BY chapter_count DESC,dialogue_count DESC,"
                        "mention_count DESC,first_chapter_id LIMIT 1",
                        (plan.catalog_id, plan.content_revision),
                    )
                    protagonist = cursor.fetchone()
                    if protagonist:
                        cursor.execute(
                            "UPDATE audiobook_character_profiles SET role_type='protagonist' "
                            "WHERE catalog_id=%s AND character_key=%s",
                            (plan.catalog_id, protagonist["character_key"]),
                        )
                # Main characters must not share one same-gender voice when a
                # free voice exists. Keep the strongest/protagonist binding
                # trusted and route later collisions through AI, which chooses
                # from the remaining full-book pool.
                cursor.execute(
                    "SELECT p.character_key,p.role_type,p.chapter_count,"
                    "p.dialogue_count,v.gender,v.voice_key "
                    "FROM audiobook_character_profiles p "
                    "INNER JOIN audiobook_character_voices v "
                    "ON v.catalog_id=p.catalog_id AND v.character_key=p.character_key "
                    "WHERE p.catalog_id=%s AND p.scan_revision=UNHEX(%s) "
                    "AND p.role_type IN ('protagonist','supporting') "
                    "AND v.gender IN ('female','male') AND v.voice_key<>'' "
                    "ORDER BY (p.role_type='protagonist') DESC,"
                    "p.chapter_count DESC,p.dialogue_count DESC,p.character_key",
                    (plan.catalog_id, plan.content_revision),
                )
                occupied: set[tuple[str, str]] = set()
                duplicate_keys: list[Any] = []
                for row in cursor.fetchall():
                    signature = (
                        str(row.get("gender") or "unknown"),
                        str(row.get("voice_key") or ""),
                    )
                    if signature in occupied:
                        duplicate_keys.append(row["character_key"])
                    else:
                        occupied.add(signature)
                for character_key in duplicate_keys:
                    cursor.execute(
                        "UPDATE audiobook_character_voices SET voice_locked=0 "
                        "WHERE catalog_id=%s AND character_key=%s",
                        (plan.catalog_id, character_key),
                    )
                    cursor.execute(
                        "UPDATE audiobook_character_profiles SET voice_locked=0 "
                        "WHERE catalog_id=%s AND character_key=%s "
                        "AND scan_revision=UNHEX(%s)",
                        (plan.catalog_id, character_key, plan.content_revision),
                    )
                cursor.execute(
                    "UPDATE audiobook_cast_scan_jobs SET status='complete',"
                    "processed_chapters=total_chapters,lease_token=NULL,lease_until=NULL,"
                    "completed_at=UTC_TIMESTAMP(6),error_count=0,last_error=NULL "
                    "WHERE catalog_id=%s AND content_revision=UNHEX(%s) "
                    "AND status='running' AND lease_token=%s",
                    (plan.catalog_id, plan.content_revision, token),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("audiobook cast completion lease lost")
                publish_cast_snapshot(
                    cursor,
                    catalog_id=plan.catalog_id,
                    content_revision=bytes.fromhex(plan.content_revision),
                    engine_version=os.getenv(
                        "OOHSTORY_CAST_ENGINE_VERSION", CAST_ENGINE_VERSION
                    ),
                )
                cursor.execute(
                    "SELECT COUNT(*) AS amount FROM audiobook_character_profiles p "
                    "INNER JOIN audiobook_character_voices v "
                    "ON v.catalog_id=p.catalog_id AND v.character_key=p.character_key "
                    "WHERE p.catalog_id=%s AND p.scan_revision=UNHEX(%s) "
                    "AND (v.gender='unknown' OR v.gender_confidence<%s "
                    "OR v.voice_locked=0 "
                    "OR (p.role_type='protagonist' AND p.canonical_name<>%s))",
                    (
                        plan.catalog_id,
                        plan.content_revision,
                        self._gender_guess_threshold,
                        PROTAGONIST_NAME,
                    ),
                )
                if int((cursor.fetchone() or {}).get("amount") or 0) > 0:
                    cursor.execute(
                        "INSERT INTO audiobook_cast_ai_review_jobs "
                        "(catalog_id,book_public_id,content_revision,status,next_attempt_at) "
                        "VALUES (%s,%s,UNHEX(%s),'pending',UTC_TIMESTAMP(6)) "
                        "ON DUPLICATE KEY UPDATE "
                        "book_public_id=VALUES(book_public_id),"
                        "status=IF(content_revision=VALUES(content_revision) "
                        "AND status='complete','complete','pending'),"
                        "lease_token=NULL,lease_until=NULL,"
                        "attempt_count=IF(content_revision=VALUES(content_revision),"
                        "attempt_count,0),last_error=NULL,"
                        "next_attempt_at=UTC_TIMESTAMP(6),"
                        "completed_at=IF(content_revision=VALUES(content_revision) "
                        "AND status='complete',completed_at,NULL),"
                        "content_revision=VALUES(content_revision)",
                        (
                            plan.catalog_id,
                            plan.book_public_id,
                            plan.content_revision,
                        ),
                    )

    def _record_failure(self, job: dict[str, Any], exc: Exception) -> None:
        if self.mysql is None:
            return
        try:
            with self.mysql.pool.transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE audiobook_cast_scan_jobs SET "
                        "error_count=error_count+1,"
                        "status=IF(error_count+1>=%s,'failed','pending'),"
                        "last_error=%s,lease_token=NULL,lease_until=NULL,"
                        "next_attempt_at=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 60 SECOND) "
                        "WHERE catalog_id=%s AND lease_token=%s",
                        (
                            self.MAX_ERRORS,
                            str(exc)[:1000],
                            int(job["catalog_id"]),
                            str(job["lease_token"]),
                        ),
                    )
        except Exception:
            LOGGER.exception("cast prewarm failure checkpoint could not be saved")
