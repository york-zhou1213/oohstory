#!/usr/bin/env python3
"""Find exact alternate sources for terminal whole-book download failures."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from project_paths import APP_ROOT  # noqa: E402


BACKEND_ROOT = APP_ROOT / "src"
sys.path.insert(0, str(BACKEND_ROOT))

from oohstory_library.services.authorized_source_recovery import (  # noqa: E402
    find_exact_fallback_candidates,
    is_permanent_source_failure,
    source_name_for_id,
)
from oohstory_library.services.electronic_library import ElectronicLibraryService  # noqa: E402
from oohstory_library.services.library_download_queue import LibraryDownloadQueue  # noqa: E402


STATE_PATH = (
    APP_ROOT
    / "electronic-library"
    / "全局索引"
    / "authorized-source-recovery.json"
)


def write_state(payload: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, STATE_PATH)


def recovery_rows(service: ElectronicLibraryService, limit: int) -> list[dict[str, Any]]:
    with service.mysql_pool.connection(readonly=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT b.id AS catalog_id,b.source_id,b.title,b.author,
                       b.detail_url,b.status,b.last_error,
                       j.id AS job_id,j.status AS job_status,
                       j.source_name,j.attempts,j.max_attempts,j.payload
                FROM books AS b
                LEFT JOIN download_jobs AS j ON j.catalog_id=b.id
                WHERE b.is_active=1 AND b.body_available=0
                  AND b.status IN ('failed','quarantined')
                  AND (
                    b.source_id REGEXP '^[0-9]+$'
                    OR b.source_id LIKE 'xbiquge-%%'
                    OR b.source_id LIKE 'ixdzs-%%'
                    OR b.source_id LIKE 'shubaow-%%'
                    OR b.source_id LIKE 'linovelib-%%'
                  )
                  AND (j.id IS NULL OR j.status='dead')
                  AND COALESCE(b.last_error,j.last_error,'')<>''
                ORDER BY b.updated_at,b.id
                LIMIT %s
                """,
                (min(max(int(limit) * 4, 20), 400),),
            )
            return [dict(row) for row in cursor.fetchall()]


def run(limit: int) -> dict[str, Any]:
    service = ElectronicLibraryService()
    if service.infrastructure_settings.catalog_backend != "mysql":
        raise RuntimeError("跨源恢复要求 WEBNOVEL_CATALOG_BACKEND=mysql")
    queue = LibraryDownloadQueue(service.mysql_pool, service.redis_queue)
    stats = {
        "seen": 0,
        "terminal": 0,
        "scheduled": 0,
        "no_match": 0,
        "conflicts": 0,
        "skipped": 0,
    }
    recent: list[dict[str, Any]] = []
    for row in recovery_rows(service, limit):
        if stats["scheduled"] >= limit:
            break
        stats["seen"] += 1
        error = str(row.get("last_error") or "")
        if not is_permanent_source_failure(
            error,
            attempts=int(row.get("attempts") or 0),
            max_attempts=int(row.get("max_attempts") or 1),
        ):
            stats["skipped"] += 1
            continue
        stats["terminal"] += 1
        try:
            current_source = source_name_for_id(row.get("source_id"))
        except ValueError:
            stats["skipped"] += 1
            continue
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        recovery = payload.get("source_recovery")
        if not isinstance(recovery, dict):
            recovery = {}
        history = recovery.get("history")
        if not isinstance(history, list):
            history = []
        excluded = {
            str(row.get("source_id") or ""),
            *{
                str(item.get("source_id") or "")
                for item in history
                if isinstance(item, dict)
            },
        } - {""}
        candidates, errors = find_exact_fallback_candidates(
            service,
            title=str(row.get("title") or ""),
            author=str(row.get("author") or ""),
            current_source_name=current_source,
            excluded_source_ids=excluded,
        )
        scheduled = None
        conflicts: list[str] = []
        for candidate in candidates:
            try:
                scheduled = queue.schedule_source_recovery(
                    catalog_id=int(row["catalog_id"]),
                    candidate=candidate,
                    reason=error,
                    job_id=(int(row["job_id"]) if row.get("job_id") else None),
                )
                break
            except Exception as exc:
                conflicts.append(
                    f"{candidate.get('source_name')}: "
                    f"{type(exc).__name__}: {str(exc)[:300]}"
                )
        if scheduled:
            stats["scheduled"] += 1
            recent.append(
                {
                    "catalog_id": int(row["catalog_id"]),
                    "title": row.get("title"),
                    "author": row.get("author"),
                    "from": row.get("source_id"),
                    "to": scheduled.get("source_id"),
                }
            )
        else:
            stats["no_match"] += 1
            if conflicts:
                stats["conflicts"] += 1
            recent.append(
                {
                    "catalog_id": int(row["catalog_id"]),
                    "title": row.get("title"),
                    "outcome": "no_safe_match",
                    "errors": [*conflicts, *errors][-6:],
                }
            )
    result = {
        "status": "completed",
        "stats": stats,
        "recent": recent[-30:],
        "state_path": str(STATE_PATH),
    }
    write_state(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    result = run(min(max(int(args.limit), 1), 100))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
