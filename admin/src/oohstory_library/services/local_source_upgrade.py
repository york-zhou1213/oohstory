"""Exact-match upgrades for watermarked local TXT80/TXT020 books."""

from __future__ import annotations

from .error_boundaries import RECOVERABLE_OPERATION_ERRORS

import asyncio
import re
import uuid
from typing import Any, Iterable

from oohstory_library.services.authorized_source_recovery import exact_identity, source_fields
from oohstory_library.services.cover_failure_policy import should_generate_ai_fallback
from oohstory_library.services.electronic_library import (
    AUTHORIZED_IXDZS_PROVIDER,
    AUTHORIZED_SHUBAOW_PROVIDER,
    AUTHORIZED_XBIQUGE_PROVIDER,
    ElectronicLibraryService,
    _reader_label_number,
)
from oohstory_library.services.library_covers import (
    prepare_alternate_remote_cover,
    retire_superseded_cover,
    superseded_cover_retiring_path,
    sync_alternate_remote_cover,
)


SOURCE_ORDER = ("ixdzs", "xbiquge", "shubaow")
UNKNOWN_AUTHORS = {"", "作者未知", "未知", "佚名"}


def chapter_number(value: Any) -> int | None:
    """Return a comparable chapter number from a label or title."""

    text = str(value or "")
    chapter_match = re.search(
        r"第\s*([零〇○一二两三四五六七八九]+)\s*[章回节]",
        text,
    )
    if chapter_match:
        digits = {
            "零": "0", "〇": "0", "○": "0", "一": "1", "二": "2",
            "两": "2", "三": "3", "四": "4", "五": "5", "六": "6",
            "七": "7", "八": "8", "九": "9",
        }
        number_text = "".join(digits[char] for char in chapter_match.group(1))
        if number_text and int(number_text) > 0:
            return int(number_text)
    number = _reader_label_number(str(value or ""))
    if number is not None and number > 0:
        return number
    match = re.search(r"(?:^|\D)([1-9]\d{0,5})(?:\D|$)", str(value or ""))
    return int(match.group(1)) if match else None


def text_chapter_progress(content: str) -> tuple[int | None, str, int]:
    """Extract the maximum numbered chapter and unique heading count."""

    headings: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    pattern = re.compile(
        r"^\s*(第\s*[0-9零一二三四五六七八九十百千万两〇○]+\s*[章回节][^\n]{0,120})\s*$"
    )
    for raw_line in str(content or "").splitlines():
        match = pattern.match(raw_line)
        if not match:
            continue
        label = re.sub(r"\s+", " ", match.group(1)).strip()
        number = chapter_number(label)
        if number is None:
            continue
        key = (number, label.casefold())
        if key in seen:
            continue
        seen.add(key)
        headings.append((number, label))
    if not headings:
        return None, "", 0
    maximum = max(number for number, _ in headings)
    latest = next(label for number, label in reversed(headings) if number == maximum)
    return maximum, latest, len(headings)


def reader_chapter_progress(
    chapters: Iterable[dict[str, Any]],
) -> tuple[int | None, str, int]:
    """Use the maximum chapter number, not the last post-volume label."""

    numbered: list[tuple[int, str]] = []
    count = 0
    for item in chapters:
        if str(item.get("kind") or "chapter") == "intro":
            continue
        count += 1
        label = " ".join(
            value
            for value in (
                str(item.get("label") or "").strip(),
                str(item.get("title") or "").strip(),
            )
            if value
        )
        number = chapter_number(label)
        if number is not None:
            numbered.append((number, label))
    if not numbered:
        return None, "", count
    maximum = max(number for number, _ in numbered)
    latest = next(label for number, label in reversed(numbered) if number == maximum)
    return maximum, latest, count


def remote_is_newer(
    *,
    local_number: int | None,
    remote_number: int | None,
    local_count: int,
    remote_count: int,
) -> bool:
    """Use latest-chapter numbers first; counts are a conservative fallback."""

    if local_number is not None and remote_number is not None:
        return remote_number > local_number
    return remote_count > 0 and local_count > 0 and remote_count > local_count


def comparable_chapter_number(number: int | None, count: int) -> int | None:
    """Ignore chapter numbers that clearly reset inside multiple volumes."""

    if number is None:
        return None
    safe_count = max(int(count), 0)
    if safe_count >= 20 and number * 2 < safe_count:
        return None
    return number


def failures_confirm_unavailable(errors: Iterable[str]) -> bool:
    """Return true only when every observed failure is deterministic.

    A timeout, Cloudflare challenge, database error, or other temporary
    failure leaves the source unresolved and must never authorize AI work.
    """

    values = [str(error or "").strip() for error in errors if str(error or "").strip()]
    return bool(values) and all(
        should_generate_ai_fallback(error, attempts=1, max_attempts=5)
        for error in values
    )


def ai_fallback_result(reason: str) -> dict[str, Any]:
    return {
        "ai_fallback": True,
        "note": str(reason or "三站均无可安全使用的真实封面")[:4000],
    }


def exact_candidates(
    service: ElectronicLibraryService,
    *,
    title: str,
    author: str,
    source_names: Iterable[str] = SOURCE_ORDER,
) -> tuple[list[dict[str, Any]], list[str]]:
    target = exact_identity(title, author)
    if not target[0] or str(author or "").strip() in UNKNOWN_AUTHORS or not target[1]:
        return [], ["作者未知，拒绝自动跨站匹配"]
    providers = {
        "ixdzs": service.ixdzs_provider,
        "xbiquge": service.xbiquge_provider,
        "shubaow": service.shubaow_provider,
    }
    matches: list[dict[str, Any]] = []
    errors: list[str] = []
    for source_name in source_names:
        if source_name not in providers:
            raise ValueError(f"未知备用封面来源：{source_name}")
        provider = providers[source_name]
        try:
            results = provider.search(title, limit=12)
        except RECOVERABLE_OPERATION_ERRORS as exc:
            errors.append(f"{source_name}: {type(exc).__name__}: {str(exc)[:240]}")
            continue
        for item in results:
            if exact_identity(item.get("title"), item.get("author")) != target:
                continue
            try:
                candidate = source_fields(service, source_name, item)
            except RECOVERABLE_OPERATION_ERRORS as exc:
                errors.append(
                    f"{source_name}: {type(exc).__name__}: {str(exc)[:240]}"
                )
                continue
            candidate.update(
                {
                    "remote_latest_chapter": str(
                        item.get("remote_latest_chapter") or ""
                    ).strip(),
                    "remote_chapter_number": chapter_number(
                        item.get("remote_latest_chapter")
                    ),
                    "remote_chapter_count": 0,
                }
            )
            matches.append(candidate)
            break
    return matches, errors


class LocalSourceUpgradeRuntime:
    """Durable seed/lease/result operations for the background worker."""

    def __init__(self, service: ElectronicLibraryService):
        if service.mysql_pool is None:
            raise RuntimeError("本地书源升级要求 MySQL 目录后端")
        self.service = service
        self.pool = service.mysql_pool

    def seed(self) -> int:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT IGNORE INTO local_source_upgrade_jobs (
                      catalog_id,original_source_id,status,available_at
                    )
                    SELECT b.id,b.source_id,'pending',UTC_TIMESTAMP(6)
                    FROM books b
                    JOIN library_clean_cover_jobs clean
                      ON clean.catalog_id=b.id
                    LEFT JOIN library_covers c ON c.catalog_id=b.id
                    WHERE b.is_active=1 AND b.library_id='local'
                      AND b.status='done' AND b.body_available=1
                      AND b.source_id REGEXP '^[0-9]+$'
                      AND clean.status IN (
                        'source_lookup_pending','source_lookup_missing_cover'
                      )
                      AND (
                        clean.status='source_lookup_missing_cover'
                        OR (c.status='done' AND c.filename IS NOT NULL)
                      )
                    ON DUPLICATE KEY UPDATE
                      original_source_id=VALUES(original_source_id),
                      status=IF(
                        local_source_upgrade_jobs.completed_at IS NOT NULL,
                        'pending',local_source_upgrade_jobs.status
                      ),
                      attempts=IF(
                        local_source_upgrade_jobs.completed_at IS NOT NULL,
                        0,local_source_upgrade_jobs.attempts
                      ),
                      available_at=IF(
                        local_source_upgrade_jobs.completed_at IS NOT NULL,
                        UTC_TIMESTAMP(6),
                        local_source_upgrade_jobs.available_at
                      ),
                      completed_at=IF(
                        local_source_upgrade_jobs.completed_at IS NOT NULL,
                        NULL,local_source_upgrade_jobs.completed_at
                      )
                    """
                )
                return int(cursor.rowcount or 0)

    def claim(self, *, limit: int, worker_id: str) -> list[dict[str, Any]]:
        safe_limit = min(max(int(limit), 1), 20)
        token = str(uuid.uuid4())
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE local_source_upgrade_jobs
                    SET status='failed',lease_owner=NULL,lease_token=NULL,
                        lease_expires_at=NULL,
                        last_error=COALESCE(last_error,'处理租约过期，已恢复')
                    WHERE status='processing'
                      AND lease_expires_at<UTC_TIMESTAMP(6)
                    """
                )
                cursor.execute(
                    """
                    SELECT j.*,b.source_id,b.detail_url,b.title,b.author,
                           b.category,b.book_status,b.approx_chapter_count,
                           COALESCE(m.chapter_count,0) AS chapter_count
                    FROM local_source_upgrade_jobs j
                    JOIN books b ON b.id=j.catalog_id
                    LEFT JOIN book_metadata m ON m.catalog_id=b.id
                    WHERE j.status IN ('pending','failed')
                      AND j.attempts<j.max_attempts
                      AND j.available_at<=UTC_TIMESTAMP(6)
                      AND b.is_active=1 AND b.status='done'
                      AND b.body_available=1 AND b.library_id='local'
                    ORDER BY j.attempts,j.catalog_id
                    LIMIT %s FOR UPDATE SKIP LOCKED
                    """,
                    (safe_limit,),
                )
                rows = [dict(row) for row in cursor.fetchall()]
                if rows:
                    cursor.executemany(
                        """
                        UPDATE local_source_upgrade_jobs
                        SET status='processing',attempts=attempts+1,
                            lease_owner=%s,lease_token=%s,
                            lease_expires_at=UTC_TIMESTAMP(6)+INTERVAL 45 MINUTE,
                            last_error=NULL
                        WHERE catalog_id=%s
                        """,
                        [
                            (worker_id, token, int(row["catalog_id"]))
                            for row in rows
                        ],
                    )
        for row in rows:
            row["lease_token"] = token
        return rows

    def finish(
        self,
        row: dict[str, Any],
        *,
        result: dict[str, Any] | None = None,
        error: str = "",
        retry: bool = False,
    ) -> None:
        result = result or {}
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                if error:
                    cursor.execute(
                        """
                        UPDATE local_source_upgrade_jobs
                        SET status=%s,last_error=%s,
                            available_at=UTC_TIMESTAMP(6)+INTERVAL 30 MINUTE,
                            lease_owner=NULL,lease_token=NULL,
                            lease_expires_at=NULL
                        WHERE catalog_id=%s AND lease_token=%s
                        """,
                        (
                            "failed" if retry else "done",
                            error[:4000],
                            int(row["catalog_id"]),
                            str(row["lease_token"]),
                        ),
                    )
                    return
                cursor.execute(
                    """
                    UPDATE local_source_upgrade_jobs
                    SET status='done',matched_source_name=%s,
                        matched_source_id=%s,matched_detail_url=%s,
                        local_latest_chapter=%s,remote_latest_chapter=%s,
                        local_chapter_number=%s,remote_chapter_number=%s,
                        cover_replaced=%s,body_replaced=%s,
                        ai_fallback_queued=%s,last_error=%s,
                        completed_at=UTC_TIMESTAMP(6),lease_owner=NULL,
                        lease_token=NULL,lease_expires_at=NULL
                    WHERE catalog_id=%s AND lease_token=%s
                    """,
                    (
                        result.get("source_name") or None,
                        result.get("source_id") or None,
                        result.get("detail_url") or None,
                        result.get("local_latest") or None,
                        result.get("remote_latest") or None,
                        result.get("local_number"),
                        result.get("remote_number"),
                        int(bool(result.get("cover_replaced"))),
                        int(bool(result.get("body_replaced"))),
                        int(bool(result.get("ai_fallback"))),
                        str(result.get("note") or "")[:4000] or None,
                        int(row["catalog_id"]),
                        str(row["lease_token"]),
                    ),
                )
                if result.get("ai_fallback"):
                    cursor.execute(
                        """
                        UPDATE library_clean_cover_jobs
                        SET status=IF(
                              original_filename IS NULL,
                              'generate_pending','manual_pending'
                            ),attempts=0,
                            replacement_url=NULL,replacement_filename=NULL,
                            verification_source=NULL,ai_session_id=NULL,
                            last_error=%s,lease_owner=NULL,lease_token=NULL,
                            lease_expires_at=NULL
                        WHERE catalog_id=%s
                          AND status IN (
                            'source_lookup_pending',
                            'source_lookup_missing_cover'
                          )
                        """,
                        (
                            "三站已确认无可安全使用的真实封面，允许 AI 重绘："
                            + str(result.get("note") or "")[:1800],
                            int(row["catalog_id"]),
                        ),
                    )

    def stats(self) -> dict[str, int]:
        with self.pool.connection(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT status,COUNT(*) AS count
                    FROM local_source_upgrade_jobs GROUP BY status
                    """
                )
                result = {str(row["status"]): int(row["count"]) for row in cursor.fetchall()}
                cursor.execute(
                    """
                    SELECT COALESCE(SUM(cover_replaced),0) covers,
                           COALESCE(SUM(body_replaced),0) bodies,
                           COALESCE(SUM(ai_fallback_queued),0) ai_fallbacks
                    FROM local_source_upgrade_jobs
                    """
                )
                totals = dict(cursor.fetchone() or {})
        result["covers_replaced"] = int(totals.get("covers") or 0)
        result["bodies_replaced"] = int(totals.get("bodies") or 0)
        result["ai_fallbacks"] = int(totals.get("ai_fallbacks") or 0)
        return result


def retire_recorded_source_covers(
    service: ElectronicLibraryService,
    runtime: Any,
    *,
    limit: int = 5000,
) -> dict[str, int]:
    """Retry exact post-commit predecessor cleanup without directory scans."""

    candidates = runtime.source_replaced_cover_deletion_rows(limit=limit)
    same_name: list[dict[str, Any]] = []
    distinct_name: list[dict[str, Any]] = []
    for row in candidates:
        target = (
            same_name
            if str(row.get("original_filename") or "")
            == str(row.get("replacement_filename") or "")
            else distinct_name
        )
        target.append(row)
    rows = [
        *same_name,
        *runtime.unreferenced_source_cover_rows(distinct_name),
    ]
    result = {
        "selected": len(candidates),
        "deleted": 0,
        "already_missing": 0,
        "retained": len(candidates) - len(rows),
        "failed": 0,
    }
    already_missing: list[dict[str, Any]] = []
    for row in rows:
        try:
            original_filename = str(row.get("original_filename") or "")
            original_path = (service.cover_root / original_filename).resolve()
            retiring_path = superseded_cover_retiring_path(
                service.cover_root,
                int(row["catalog_id"]),
                original_filename,
            )
            if (
                original_path.parent == service.cover_root.resolve()
                and not original_path.exists()
                and not retiring_path.exists()
            ):
                already_missing.append(row)
                continue
            cleanup = retire_superseded_cover(
                runtime=runtime,
                cover_root=service.cover_root,
                catalog_id=int(row["catalog_id"]),
                original_filename=original_filename,
                replacement_filename=str(row.get("replacement_filename") or ""),
            )
            status = str(cleanup.get("status") or "")
            if status in {"deleted", "deleted_recovered_staging"}:
                result["deleted"] += 1
            elif status in {"already_missing", "no_superseded_original"}:
                result["already_missing"] += 1
            else:
                result["retained"] += 1
        except RECOVERABLE_OPERATION_ERRORS:
            result["failed"] += 1
    marked_missing = runtime.mark_clean_covers_deleted_batch(already_missing)
    result["already_missing"] += int(marked_missing)
    result["failed"] += max(len(already_missing) - int(marked_missing), 0)
    return result


def _candidate_detail(
    service: ElectronicLibraryService,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    source_name = str(candidate["source_name"])
    provider = candidate["provider_object"] = {
        "ixdzs": service.ixdzs_provider,
        "xbiquge": service.xbiquge_provider,
        "shubaow": service.shubaow_provider,
    }[source_name]
    include_chapters = source_name in {"xbiquge", "shubaow"}
    if source_name == "ixdzs":
        detail = provider.detail(candidate["remote_id"], candidate["source_ref"])
    else:
        detail = provider.detail(
            candidate["remote_id"],
            candidate["source_ref"],
            include_chapters=include_chapters,
        )
    if exact_identity(detail.get("title"), detail.get("author")) != exact_identity(
        candidate.get("title"), candidate.get("author")
    ):
        raise ValueError("搜索结果与详情页书名/作者不一致")
    chapters = list(detail.get("chapters") or [])
    if chapters:
        number, latest, count = reader_chapter_progress(chapters)
        candidate["remote_latest_chapter"] = latest
        candidate["remote_chapter_number"] = number
        candidate["remote_chapter_count"] = count
    candidate["detail"] = detail
    return candidate


def _ixdzs_progress(
    service: ElectronicLibraryService,
    candidate: dict[str, Any],
) -> None:
    detail = candidate["detail"]
    archive = service.ixdzs_provider.download(detail)
    service.download_scanner.scan_bytes(
        archive,
        extension="zip",
        source=AUTHORIZED_IXDZS_PROVIDER,
    )
    content = service.ixdzs_provider.extract_text(archive)
    number, latest, count = text_chapter_progress(content)
    candidate["remote_chapter_number"] = number
    candidate["remote_latest_chapter"] = latest
    candidate["remote_chapter_count"] = count


def _cover_hosts(candidate: dict[str, Any]) -> tuple[set[str], tuple[str, ...], Any]:
    source_name = str(candidate["source_name"])
    if source_name == "ixdzs":
        return set(), (".ixdzs.com", ".ixdzs8.com"), None
    if source_name == "xbiquge":
        return {"www.xbiquge.info", "xbiquge.info"}, (), None
    provider = candidate["provider_object"]
    return (
        {"www.shubaow.org", "shubaow.org", "pic.shubaow.org"},
        (),
        provider.download_cover,
    )


async def process_job(
    service: ElectronicLibraryService,
    row: dict[str, Any],
) -> dict[str, Any]:
    title = str(row.get("title") or "")
    author = str(row.get("author") or "")
    detailed: list[dict[str, Any]] = []
    search_errors: list[str] = []
    detail_errors: list[str] = []
    cover_errors: list[str] = []
    cover_candidate: dict[str, Any] | None = None
    prepared_cover: dict[str, Any] | None = None

    async def populate_ixdzs_progress(candidate: dict[str, Any]) -> None:
        if candidate["source_name"] != "ixdzs":
            return
        try:
            await asyncio.to_thread(_ixdzs_progress, service, candidate)
        except RECOVERABLE_OPERATION_ERRORS as exc:
            # A body-progress probe may fail while the exact-match detail and
            # cover remain usable. Keep the cover and only skip the newer-body
            # decision for this source.
            detail_errors.append(
                f"{candidate['source_name']}正文进度: "
                f"{type(exc).__name__}: {str(exc)[:300]}"
            )

    # Search one site at a time.  A verified clean cover is a terminal success
    # for source discovery, so later providers must not receive any request.
    for source_name in SOURCE_ORDER:
        candidates, errors = await asyncio.to_thread(
            exact_candidates,
            service,
            title=title,
            author=author,
            source_names=(source_name,),
        )
        search_errors.extend(errors)
        if not candidates:
            continue
        candidate = candidates[0]
        try:
            await asyncio.to_thread(_candidate_detail, service, candidate)
        except RECOVERABLE_OPERATION_ERRORS as exc:
            detail_errors.append(
                f"{candidate['source_name']}: "
                f"{type(exc).__name__}: {str(exc)[:300]}"
            )
            continue
        detailed.append(candidate)
        cover_url = str(candidate["detail"].get("cover_url") or "").strip()
        if not cover_url:
            cover_errors.append(f"{candidate['source_name']}: 没有真实封面")
            await populate_ixdzs_progress(candidate)
            continue
        allowed_hosts, suffixes, request_bytes = _cover_hosts(candidate)
        try:
            prepared_cover = await asyncio.to_thread(
                prepare_alternate_remote_cover,
                origin_detail_url=str(
                    candidate["detail"].get("detail_url") or ""
                ),
                cover_url=cover_url,
                cover_index_path=service.cover_index_path,
                allowed_hosts=allowed_hosts,
                allowed_host_suffixes=suffixes,
                request_bytes=request_bytes,
                reject_known_watermarks=True,
            )
        except RECOVERABLE_OPERATION_ERRORS as exc:
            cover_errors.append(
                f"{candidate['source_name']}: "
                f"{type(exc).__name__}: {str(exc)[:300]}"
            )
            await populate_ixdzs_progress(candidate)
            continue
        cover_candidate = candidate
        await populate_ixdzs_progress(candidate)
        break

    if not detailed:
        unresolved = [*search_errors, *detail_errors]
        if unresolved:
            if failures_confirm_unavailable(unresolved):
                return ai_fallback_result("；".join(unresolved))
            raise RuntimeError("；".join(unresolved))
        return ai_fallback_result("三站均无书名+作者完全一致的作品")

    if cover_candidate is None and not cover_errors:
        # Defensive guard: every detailed candidate should produce either a
        # prepared cover or an explicit rejection reason.
        cover_errors.append("三站均无可安全使用的真实封面")

    reader = await asyncio.to_thread(
        service.get_reader_catalog,
        int(row["catalog_id"]),
    )
    local_chapters = list(reader.get("chapters") or [])
    local_number, local_latest, counted_chapters = reader_chapter_progress(
        local_chapters
    )
    local_count = max(
        int(reader.get("chapter_count") or 0),
        int(counted_chapters),
    )

    def rank(candidate: dict[str, Any]) -> tuple[int, int]:
        return (
            int(candidate.get("remote_chapter_number") or 0),
            int(candidate.get("remote_chapter_count") or 0),
        )

    freshest = max(detailed, key=rank)
    remote_number = freshest.get("remote_chapter_number")
    remote_count = int(freshest.get("remote_chapter_count") or 0)
    body_replaced = False
    if remote_is_newer(
        local_number=comparable_chapter_number(local_number, local_count),
        remote_number=comparable_chapter_number(remote_number, remote_count),
        local_count=local_count,
        remote_count=remote_count,
    ):
        provider_id = {
            "ixdzs": AUTHORIZED_IXDZS_PROVIDER,
            "xbiquge": AUTHORIZED_XBIQUGE_PROVIDER,
            "shubaow": AUTHORIZED_SHUBAOW_PROVIDER,
        }[str(freshest["source_name"])]
        await service.import_public_book(
            provider=provider_id,
            remote_id=freshest["remote_id"],
            source_ref=freshest["source_ref"],
            book_status=str(freshest["detail"].get("book_status") or ""),
            category_hint=str(row.get("category") or "未分类"),
            defer_postprocess=True,
            refresh_existing_catalog_id=int(row["catalog_id"]),
            rebind_existing_source=True,
            expected_latest_chapter=str(
                freshest.get("remote_latest_chapter") or ""
            ),
            ai_service=None,
        )
        service.move_catalog_books(
            catalog_ids=[int(row["catalog_id"])], target_library="local"
        )
        body_replaced = True

    cover_replaced = False
    if cover_candidate is not None and prepared_cover is not None:
        allowed_hosts, suffixes, request_bytes = _cover_hosts(cover_candidate)
        try:
            current = service.get_book(int(row["catalog_id"]))
            await asyncio.to_thread(
                sync_alternate_remote_cover,
                catalog_id=int(row["catalog_id"]),
                catalog_source_id=str(current.get("source_id") or ""),
                origin_source_id=str(cover_candidate["source_id"]),
                title=title,
                author=author,
                origin_detail_url=str(
                    cover_candidate["detail"].get("detail_url") or ""
                ),
                cover_url=str(cover_candidate["detail"].get("cover_url") or ""),
                cover_root=service.cover_root,
                cover_index_path=service.cover_index_path,
                allowed_hosts=allowed_hosts,
                allowed_host_suffixes=suffixes,
                request_bytes=request_bytes,
                prepared=prepared_cover,
            )
            cover_replaced = True
        except RECOVERABLE_OPERATION_ERRORS as exc:
            # The image already passed source, format, malware and watermark
            # validation. Persistence failures are infrastructure failures;
            # probing another website cannot repair them.
            raise RuntimeError(
                f"{cover_candidate['source_name']}封面落库失败: "
                f"{type(exc).__name__}: {str(exc)[:300]}"
            ) from exc

    if not cover_replaced:
        failures = [*cover_errors, *detail_errors, *search_errors]
        message = "匹配到作品但三站均无可安全替换的封面：" + "；".join(failures)
        if not failures or failures_confirm_unavailable(failures):
            fallback = ai_fallback_result(message)
            fallback.update(
                {
                    "local_latest": local_latest,
                    "remote_latest": freshest.get("remote_latest_chapter"),
                    "local_number": local_number,
                    "remote_number": remote_number,
                    "body_replaced": body_replaced,
                }
            )
            return fallback
        raise RuntimeError(message)

    selected = cover_candidate or freshest
    return {
        "source_name": selected["source_name"],
        "source_id": selected["source_id"],
        "detail_url": selected["detail"].get("detail_url"),
        "local_latest": local_latest,
        "remote_latest": freshest.get("remote_latest_chapter"),
        "local_number": local_number,
        "remote_number": remote_number,
        "cover_replaced": cover_replaced,
        "body_replaced": body_replaced,
        "note": "；".join([*search_errors, *detail_errors, *cover_errors])[-4000:],
    }
