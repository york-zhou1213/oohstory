from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import json
from typing import Any

from .audiobook_policy import FEMALE_VOICES, MALE_VOICES


LATEST_AI_REVIEW_JOIN = (
    "LEFT JOIN audiobook_cast_ai_reviews r "
    "ON r.catalog_id=p.catalog_id AND r.character_key=p.character_key "
    "AND r.scan_revision=("
    "SELECT r2.scan_revision FROM audiobook_cast_ai_reviews r2 "
    "WHERE r2.catalog_id=p.catalog_id AND r2.character_key=p.character_key "
    "AND r2.confidence>=0.8 "
    "ORDER BY r2.created_at DESC LIMIT 1)"
)


def _aliases(value: Any) -> list[str]:
    if not isinstance(value, list):
        try:
            value = json.loads(str(value or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            value = []
    return list(dict.fromkeys(
        str(item).strip() for item in value if str(item).strip()
    ))


def _character_key(value: Any) -> str:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    return str(value or "")


def _stable_snapshot_voices(catalog_id: int, rows: list[dict[str, Any]]) -> None:
    role_rank = {"protagonist": 0, "supporting": 1, "cameo": 2, "unclassified": 3}
    for gender, pool in (("female", FEMALE_VOICES), ("male", MALE_VOICES)):
        actors = sorted(
            (row for row in rows if row["gender"] == gender),
            key=lambda row: (
                role_rank.get(str(row.get("role_type") or ""), 4),
                -int(row.get("chapter_count") or 0),
                -int(row.get("dialogue_count") or 0),
                str(row.get("canonical_name") or ""),
            ),
        )
        occupied: set[str] = set()
        for row in actors:
            preferred = str(row.get("voice_key") or "")
            if preferred in pool and preferred not in occupied:
                voice = preferred
            else:
                free = [voice for voice in pool if voice not in occupied]
                candidates = free or list(pool)
                seed = sha256(
                    f"{catalog_id}\0{row['canonical_name']}\0published-cast".encode()
                ).digest()
                voice = candidates[int.from_bytes(seed[:2], "big") % len(candidates)]
            row["voice_key"] = voice
            occupied.add(voice)


def snapshot_rows(
    cursor: Any, *, catalog_id: int, content_revision: Any,
) -> list[dict[str, Any]]:
    cursor.execute(
        "SELECT v.character_key,v.canonical_name,v.aliases,v.gender,v.age_group,v.tone,"
        "v.gender_confidence,v.age_confidence,v.tone_confidence,v.voice_key,v.voice_locked,"
        "p.role_type,p.dialogue_count,p.chapter_count,p.mention_count,"
        "r.gender AS ai_review_gender,r.role_type AS ai_review_role_type,"
        "r.confidence AS ai_review_confidence "
        "FROM audiobook_character_voices v "
        "INNER JOIN audiobook_character_profiles p "
        "ON p.catalog_id=v.catalog_id AND p.character_key=v.character_key "
        f"{LATEST_AI_REVIEW_JOIN} "
        "WHERE p.catalog_id=%s AND p.scan_revision=%s",
        (catalog_id, content_revision),
    )
    rows: list[dict[str, Any]] = []
    for raw in cursor.fetchall():
        row = dict(raw)
        review_confidence = float(row.get("ai_review_confidence") or 0.0)
        review_gender = str(row.get("ai_review_gender") or "unknown")
        if review_confidence >= 0.8 and review_gender in {"female", "male"}:
            row["gender"] = review_gender
            row["gender_confidence"] = review_confidence
        elif review_confidence >= 0.8 and review_gender == "unknown":
            row["gender"] = "unknown"
            row["gender_confidence"] = review_confidence
            row["voice_key"] = ""
            row["voice_locked"] = 0
        review_role = str(row.get("ai_review_role_type") or "")
        if review_confidence >= 0.8 and review_role in {
            "protagonist", "supporting", "cameo",
        }:
            row["role_type"] = review_role
        row["character_key"] = _character_key(row.get("character_key"))
        row["aliases"] = _aliases(row.get("aliases"))
        for key, value in tuple(row.items()):
            if isinstance(value, Decimal):
                row[key] = float(value)
        rows.append(row)
    _stable_snapshot_voices(catalog_id, rows)
    return rows


def _sync_snapshot_voice_rows(
    cursor: Any, *, catalog_id: int, rows: list[dict[str, Any]]
) -> None:
    for row in rows:
        gender = str(row.get("gender") or "unknown")
        pool = FEMALE_VOICES if gender == "female" else MALE_VOICES if gender == "male" else ()
        voice = str(row.get("voice_key") or "")
        confidence = float(row.get("gender_confidence") or 0.0)
        ai_gender = str(row.get("ai_review_gender") or "")
        ai_confidence = float(row.get("ai_review_confidence") or 0.0)
        character_key = str(row.get("character_key") or "")
        if (
            ai_gender == "unknown"
            and ai_confidence >= 0.8
            and len(character_key) == 64
        ):
            cursor.execute(
                "UPDATE audiobook_character_voices SET gender='unknown',"
                "gender_confidence=GREATEST(gender_confidence,%s),"
                "voice_key='',voice_locked=0 "
                "WHERE catalog_id=%s AND character_key=UNHEX(%s) AND "
                "(gender<>'unknown' OR gender_confidence<%s "
                "OR voice_key<>'' OR voice_locked<>0)",
                (ai_confidence, catalog_id, character_key, ai_confidence),
            )
            row["voice_key"] = ""
            row["voice_locked"] = 0
            continue
        if (
            gender not in {"female", "male"}
            or voice not in pool
            or confidence < 0.7
            or len(character_key) != 64
        ):
            continue
        cursor.execute(
            "UPDATE audiobook_character_voices SET gender=%s,"
            "gender_confidence=GREATEST(gender_confidence,%s),"
            "voice_key=%s,voice_locked=1 "
            "WHERE catalog_id=%s AND character_key=UNHEX(%s) AND "
            "(gender<>%s OR gender_confidence<%s OR voice_key<>%s "
            "OR voice_locked<>1)",
            (
                gender,
                confidence,
                voice,
                catalog_id,
                character_key,
                gender,
                confidence,
                voice,
            ),
        )
        row["voice_locked"] = 1


def _clear_ai_rejected_voice_rows(cursor: Any, *, catalog_id: int) -> None:
    cursor.execute(
        "SELECT HEX(v.character_key) AS character_key,r.confidence "
        "FROM audiobook_character_voices v "
        "INNER JOIN audiobook_cast_ai_reviews r "
        "ON r.catalog_id=v.catalog_id AND r.character_key=v.character_key "
        "WHERE v.catalog_id=%s AND r.gender='unknown' AND r.confidence>=0.8 "
        "AND r.scan_revision=("
        "SELECT r2.scan_revision FROM audiobook_cast_ai_reviews r2 "
        "WHERE r2.catalog_id=v.catalog_id AND r2.character_key=v.character_key "
        "AND r2.confidence>=0.8 ORDER BY r2.created_at DESC LIMIT 1) "
        "AND (v.gender<>'unknown' OR v.voice_key<>'' OR v.voice_locked<>0)",
        (catalog_id,),
    )
    for row in cursor.fetchall():
        character_key = str(row.get("character_key") or "")
        confidence = float(row.get("confidence") or 0.0)
        if len(character_key) != 64:
            continue
        cursor.execute(
            "UPDATE audiobook_character_voices SET gender='unknown',"
            "gender_confidence=GREATEST(gender_confidence,%s),"
            "voice_key='',voice_locked=0 "
            "WHERE catalog_id=%s AND character_key=UNHEX(%s)",
            (confidence, catalog_id, character_key),
        )


def _assign_missing_confirmed_voice_rows(cursor: Any, *, catalog_id: int) -> None:
    cursor.execute(
        "SELECT HEX(v.character_key) AS character_key,v.canonical_name,v.gender "
        "FROM audiobook_character_voices v "
        "WHERE v.catalog_id=%s AND v.gender IN ('female','male') "
        "AND v.gender_confidence>=0.7 AND v.voice_key='' "
        "AND NOT EXISTS ("
        "SELECT 1 FROM audiobook_cast_ai_reviews r "
        "WHERE r.catalog_id=v.catalog_id AND r.character_key=v.character_key "
        "AND r.gender='unknown' AND r.confidence>=0.8 "
        "AND r.scan_revision=("
        "SELECT r2.scan_revision FROM audiobook_cast_ai_reviews r2 "
        "WHERE r2.catalog_id=v.catalog_id AND r2.character_key=v.character_key "
        "AND r2.confidence>=0.8 ORDER BY r2.created_at DESC LIMIT 1))",
        (catalog_id,),
    )
    for row in cursor.fetchall():
        character_key = str(row.get("character_key") or "")
        gender = str(row.get("gender") or "unknown")
        pool = FEMALE_VOICES if gender == "female" else MALE_VOICES if gender == "male" else ()
        if len(character_key) != 64 or not pool:
            continue
        seed = sha256(
            f"{catalog_id}\0{row.get('canonical_name') or ''}\0mysql-fingerprint".encode()
        ).digest()
        voice = pool[int.from_bytes(seed[:2], "big") % len(pool)]
        cursor.execute(
            "UPDATE audiobook_character_voices SET voice_key=%s,voice_locked=1 "
            "WHERE catalog_id=%s AND character_key=UNHEX(%s) AND voice_key=''",
            (voice, catalog_id, character_key),
        )


def publish_cast_snapshot(
    cursor: Any,
    *,
    catalog_id: int,
    content_revision: Any,
    engine_version: str,
) -> int:
    rows = snapshot_rows(
        cursor, catalog_id=catalog_id, content_revision=content_revision,
    )
    _sync_snapshot_voice_rows(cursor, catalog_id=catalog_id, rows=rows)
    _clear_ai_rejected_voice_rows(cursor, catalog_id=catalog_id)
    _assign_missing_confirmed_voice_rows(cursor, catalog_id=catalog_id)
    payload = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    cursor.execute(
        "SELECT revision,cast_json FROM audiobook_cast_snapshots "
        "WHERE catalog_id=%s FOR UPDATE",
        (catalog_id,),
    )
    existing = cursor.fetchone()
    existing_json = existing.get("cast_json") if existing else None
    if not isinstance(existing_json, str):
        existing_json = json.dumps(
            existing_json, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ) if existing_json is not None else ""
    if existing_json == payload:
        return int(existing.get("revision") or 0)
    cursor.execute(
        "INSERT INTO audiobook_cast_revisions (catalog_id,revision) VALUES (%s,1) "
        "ON DUPLICATE KEY UPDATE revision=revision+1",
        (catalog_id,),
    )
    cursor.execute(
        "SELECT revision FROM audiobook_cast_revisions WHERE catalog_id=%s",
        (catalog_id,),
    )
    revision = int(cursor.fetchone()["revision"])
    cursor.execute(
        "INSERT INTO audiobook_cast_snapshots "
        "(catalog_id,content_revision,engine_version,revision,cast_json,published_at) "
        "VALUES (%s,%s,%s,%s,%s,UTC_TIMESTAMP(6)) "
        "ON DUPLICATE KEY UPDATE content_revision=VALUES(content_revision),"
        "engine_version=VALUES(engine_version),revision=VALUES(revision),"
        "cast_json=VALUES(cast_json),published_at=VALUES(published_at)",
        (catalog_id, content_revision, engine_version, revision, payload),
    )
    return revision
