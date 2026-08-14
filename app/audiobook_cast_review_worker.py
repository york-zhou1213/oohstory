from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from uuid import uuid4

from .audiobook import PROTAGONIST_NAME, _voice_pool, deterministic_voice
from .audiobook_policy import CAST_ENGINE_VERSION
from .audiobook_snapshot import publish_cast_snapshot
from .main import repository


BATCH_SIZE = 12
MIN_APPLY_CONFIDENCE = 0.8


CLAIM_SQL = (
    "SELECT j.catalog_id,j.book_public_id,j.content_revision,j.attempt_count "
    "FROM audiobook_cast_ai_review_jobs j "
    "INNER JOIN audiobook_cast_scan_jobs s ON s.catalog_id=j.catalog_id "
    "AND s.content_revision=j.content_revision AND s.status='complete' WHERE "
    "(((j.status='pending' OR j.status='deferred') "
    "AND j.next_attempt_at<=UTC_TIMESTAMP(6)) "
    "OR (j.status='running' AND j.lease_until<UTC_TIMESTAMP(6))) "
    "AND j.attempt_count<3 ORDER BY j.updated_at,j.catalog_id "
    "LIMIT 1 FOR UPDATE SKIP LOCKED"
)


def _revision_hex(value: Any) -> str:
    if isinstance(value, memoryview):
        value = value.tobytes()
    return bytes(value).hex() if isinstance(value, (bytes, bytearray)) else str(value or "")


def _reviewed_voice(catalog_id: int, row: dict[str, Any], gender: str) -> str:
    current = str(row.get("voice_key") or "")
    if bool(row.get("voice_locked")):
        return current
    return deterministic_voice(
        catalog_id,
        {"canonical_name": str(row["canonical_name"]), "gender": gender},
    )


def _claim() -> dict[str, Any] | None:
    mysql = getattr(repository(), "_mysql", None)
    if mysql is None:
        return None
    token = uuid4().hex
    with mysql.pool.transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(CLAIM_SQL)
            row = cursor.fetchone()
            if not row:
                return None
            cursor.execute(
                "UPDATE audiobook_cast_ai_review_jobs SET status='running',"
                "attempt_count=IF(status='deferred',0,attempt_count),"
                "lease_token=%s,lease_until=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 6 MINUTE) "
                "WHERE catalog_id=%s",
                (token, int(row["catalog_id"])),
            )
    return {**dict(row), "lease_token": token}


def _candidate_rows(job: dict[str, Any]) -> list[dict[str, Any]]:
    mysql = repository()._mysql
    with mysql.pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT p.character_key,p.canonical_name,p.role_type,p.mention_count,"
                "p.dialogue_count,p.chapter_count,p.first_chapter_id,p.last_chapter_id,"
                "v.aliases,v.gender,v.gender_confidence,v.voice_key,v.voice_locked "
                "FROM audiobook_character_profiles p "
                "INNER JOIN audiobook_character_voices v "
                "ON v.catalog_id=p.catalog_id AND v.character_key=p.character_key "
                "LEFT JOIN audiobook_cast_ai_reviews r "
                "ON r.catalog_id=p.catalog_id AND r.character_key=p.character_key "
                "AND r.scan_revision=p.scan_revision "
                "WHERE p.catalog_id=%s AND p.scan_revision=%s AND r.character_key IS NULL "
                "AND (p.role_type IN ('protagonist','supporting') "
                "OR v.gender='unknown' OR v.gender_confidence<0.7 "
                "OR v.voice_locked=0 "
                "OR (p.role_type='protagonist' AND p.canonical_name<>%s)) "
                "ORDER BY p.chapter_count DESC,p.dialogue_count DESC,p.mention_count DESC "
                "LIMIT %s",
                (
                    int(job["catalog_id"]),
                    job["content_revision"],
                    PROTAGONIST_NAME,
                    BATCH_SIZE,
                ),
            )
            return [dict(row) for row in cursor.fetchall()]


def _aliases(row: dict[str, Any]) -> list[str]:
    raw = row.get("aliases")
    if not isinstance(raw, list):
        try:
            raw = json.loads(str(raw or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = []
    values = [str(row.get("canonical_name") or ""), *(str(item) for item in raw)]
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))[:12]


def _snippets(book_id: str, row: dict[str, Any]) -> list[str]:
    aliases = _aliases(row)
    chapter_ids = list(dict.fromkeys([
        int(row.get("first_chapter_id") or 0),
        int(row.get("last_chapter_id") or 0),
    ]))
    result: list[str] = []
    for chapter_id in chapter_ids:
        if chapter_id <= 0:
            continue
        try:
            content = str(repository().reader_chapter(book_id, chapter_id).get("content") or "")
        except Exception:
            continue
        for alias in aliases:
            for match in list(re.finditer(re.escape(alias), content))[:2]:
                start = max(0, match.start() - 100)
                end = min(len(content), match.end() + 100)
                excerpt = re.sub(r"\s+", " ", content[start:end]).strip()
                if excerpt and excerpt not in result:
                    result.append(excerpt[:260])
                if len(result) >= 4:
                    return result
    return result


def _payload(job: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "contract": "oohstory-audiobook-cast-review-v1",
        "book_id": str(job["book_public_id"]),
        "candidates": [
            {
                "canonical_name": str(row["canonical_name"]),
                "aliases": _aliases(row),
                "existing_gender": str(row.get("gender") or "unknown"),
                "existing_gender_confidence": float(row.get("gender_confidence") or 0),
                "existing_role_type": str(row.get("role_type") or "cameo"),
                "mention_count": int(row.get("mention_count") or 0),
                "dialogue_count": int(row.get("dialogue_count") or 0),
                "chapter_count": int(row.get("chapter_count") or 0),
                "evidence_snippets": _snippets(str(job["book_public_id"]), row),
            }
            for row in rows
        ],
    }


def _call(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "review_audiobook_cast_with_openclaw.py"),
    ]
    result = subprocess.run(
        command,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=300,
        check=False,
        env={**os.environ, "LC_ALL": "C.UTF-8"},
    )
    if result.returncode != 0:
        diagnostic = (result.stderr or "").strip().splitlines()
        detail = " | ".join(diagnostic[-3:]) if diagnostic else "AI cast review failed"
        raise RuntimeError(detail[:1000])
    output = json.loads(result.stdout)
    if not isinstance(output, dict) or not isinstance(output.get("results"), list):
        raise RuntimeError("AI cast review output invalid")
    return output


def _apply(job: dict[str, Any], rows: list[dict[str, Any]], output: dict[str, Any]) -> None:
    mysql = repository()._mysql
    by_name = {str(row["canonical_name"]): row for row in rows}
    model = os.getenv(
        "OOHSTORY_CAST_REVIEW_MODEL",
        os.getenv("OOHSTORY_SUBMISSION_REVIEW_MODEL", "openai/gpt-5.6-sol"),
    )[:160]
    with mysql.pool.transaction() as connection:
        with connection.cursor() as cursor:
            for decision in output["results"]:
                row = by_name.get(str(decision.get("canonical_name") or ""))
                if row is None:
                    continue
                confidence = min(1.0, max(0.0, float(decision.get("confidence") or 0)))
                gender = str(decision.get("gender") or "unknown")
                role = str(decision.get("role_type") or "cameo")
                if gender not in {"male", "female", "unknown"}:
                    gender = "unknown"
                if role not in {"protagonist", "supporting", "cameo"}:
                    role = "cameo"
                cursor.execute(
                    "INSERT IGNORE INTO audiobook_cast_ai_reviews "
                    "(catalog_id,character_key,scan_revision,gender,role_type,confidence,reason,model_key) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        int(job["catalog_id"]), row["character_key"], job["content_revision"],
                        gender, role, confidence, str(decision.get("reason") or "")[:500], model,
                    ),
                )
                if confidence < MIN_APPLY_CONFIDENCE:
                    continue
                current_gender = str(row.get("gender") or "unknown")
                current_confidence = float(row.get("gender_confidence") or 0)
                if gender in {"male", "female"} and (
                    current_gender == "unknown"
                    or current_confidence < 0.7
                    or not bool(row.get("voice_locked"))
                ):
                    voice = _reviewed_voice(int(job["catalog_id"]), row, gender)
                    if not bool(row.get("voice_locked")):
                        cursor.execute(
                            "SELECT v.voice_key FROM audiobook_character_profiles p "
                            "INNER JOIN audiobook_character_voices v "
                            "ON v.catalog_id=p.catalog_id AND v.character_key=p.character_key "
                            "WHERE p.catalog_id=%s AND p.scan_revision=%s "
                            "AND p.role_type IN ('protagonist','supporting') "
                            "AND v.gender=%s AND v.voice_locked=1 "
                            "AND p.character_key<>%s",
                            (
                                int(job["catalog_id"]), job["content_revision"],
                                gender, row["character_key"],
                            ),
                        )
                        occupied = {
                            str(item.get("voice_key") or "")
                            for item in cursor.fetchall()
                        }
                        available = [
                            candidate for candidate in _voice_pool(gender)
                            if candidate not in occupied
                        ]
                        if voice in occupied and available:
                            voice = available[
                                int.from_bytes(
                                    bytes(row["character_key"])[:2], "big"
                                ) % len(available)
                            ]
                    cursor.execute(
                        "UPDATE audiobook_character_voices SET gender=%s,gender_confidence=%s,"
                        "voice_key=%s,voice_locked=1 "
                        "WHERE catalog_id=%s AND character_key=%s",
                        (gender, confidence, voice, int(job["catalog_id"]), row["character_key"]),
                    )
                if str(row["canonical_name"]) == PROTAGONIST_NAME:
                    role = "protagonist"
                elif role == "protagonist":
                    cursor.execute(
                        "SELECT COUNT(*) AS amount FROM audiobook_character_profiles "
                        "WHERE catalog_id=%s AND scan_revision=%s AND canonical_name=%s",
                        (int(job["catalog_id"]), job["content_revision"], PROTAGONIST_NAME),
                    )
                    if int((cursor.fetchone() or {}).get("amount") or 0) > 0:
                        role = "supporting"
                    else:
                        cursor.execute(
                            "UPDATE audiobook_character_profiles SET role_type='supporting' "
                            "WHERE catalog_id=%s AND scan_revision=%s "
                            "AND role_type='protagonist' AND character_key<>%s",
                            (int(job["catalog_id"]), job["content_revision"], row["character_key"]),
                        )
                cursor.execute(
                    "UPDATE audiobook_character_profiles SET role_type=%s "
                    "WHERE catalog_id=%s AND character_key=%s AND scan_revision=%s",
                    (role, int(job["catalog_id"]), row["character_key"], job["content_revision"]),
                )
            publish_cast_snapshot(
                cursor,
                catalog_id=int(job["catalog_id"]),
                content_revision=job["content_revision"],
                engine_version=os.getenv(
                    "OOHSTORY_CAST_ENGINE_VERSION", CAST_ENGINE_VERSION
                ),
            )
            cursor.execute(
                "SELECT COUNT(*) AS amount FROM audiobook_character_profiles p "
                "INNER JOIN audiobook_character_voices v "
                "ON v.catalog_id=p.catalog_id AND v.character_key=p.character_key "
                "LEFT JOIN audiobook_cast_ai_reviews r ON r.catalog_id=p.catalog_id "
                "AND r.character_key=p.character_key AND r.scan_revision=p.scan_revision "
                "WHERE p.catalog_id=%s AND p.scan_revision=%s AND r.character_key IS NULL "
                "AND (p.role_type IN ('protagonist','supporting') "
                "OR v.gender='unknown' OR v.gender_confidence<0.7 "
                "OR v.voice_locked=0 "
                "OR (p.role_type='protagonist' AND p.canonical_name<>%s))",
                (int(job["catalog_id"]), job["content_revision"], PROTAGONIST_NAME),
            )
            remaining = int((cursor.fetchone() or {}).get("amount") or 0)
            cursor.execute(
                "UPDATE audiobook_cast_ai_review_jobs SET status=%s,lease_token=NULL,lease_until=NULL,"
                "attempt_count=0,last_error=NULL,"
                "next_attempt_at=IF(%s>0,DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 5 SECOND),next_attempt_at),"
                "completed_at=IF(%s=0,UTC_TIMESTAMP(6),NULL) "
                "WHERE catalog_id=%s AND lease_token=%s",
                (
                    "pending" if remaining else "complete",
                    remaining,
                    remaining,
                    int(job["catalog_id"]),
                    str(job["lease_token"]),
                ),
            )


def _fail(job: dict[str, Any], exc: Exception) -> None:
    mysql = repository()._mysql
    with mysql.pool.transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE audiobook_cast_ai_review_jobs SET "
                "status=IF(attempt_count+1>=3,'deferred','pending'),"
                "next_attempt_at=DATE_ADD(UTC_TIMESTAMP(6),"
                "INTERVAL IF(attempt_count+1>=3,1440,5) MINUTE),"
                "attempt_count=IF(attempt_count+1>=3,0,attempt_count+1),last_error=%s,"
                "lease_token=NULL,lease_until=NULL "
                "WHERE catalog_id=%s AND lease_token=%s",
                (str(exc)[:1000], int(job["catalog_id"]), str(job["lease_token"])),
            )


def main() -> None:
    job = _claim()
    if job is None:
        print("cast_ai_review=idle")
        return
    try:
        rows = _candidate_rows(job)
        if not rows:
            _apply(job, [], {"results": []})
            print(f"cast_ai_review=complete catalog_id={job['catalog_id']} reviewed=0")
            return
        output = _call(_payload(job, rows))
        _apply(job, rows, output)
        print(f"cast_ai_review=ok catalog_id={job['catalog_id']} reviewed={len(rows)}")
    except RuntimeError as exc:
        _fail(job, exc)
        print(
            f"cast_ai_review=deferred catalog_id={job['catalog_id']} error={str(exc)[:240]}",
            file=sys.stderr,
        )
    except Exception as exc:
        _fail(job, exc)
        raise


if __name__ == "__main__":
    main()
