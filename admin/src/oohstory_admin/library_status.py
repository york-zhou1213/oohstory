"""OOHStory-owned, read-only electronic-library status services.

This module deliberately talks to the durable MySQL catalog, Redis and the
shared NAS path directly.  It never proxies the webnovel-writer HTTP API and it
never opens the legacy catalog SQLite files.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from oohstory_library.services.library_cache import RedisHotCache
from .library_catalog import hot_cache_from_settings


STATUS_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("health", "/api/health"),
    ("library", "/api/library/status"),
    ("infrastructure", "/api/library/infrastructure/status"),
    ("sync_controls", "/api/library/sync-controls"),
    ("book_index", "/api/library/index/status"),
    ("plot_index", "/api/library/plot-index/status"),
    ("tone_review", "/api/library/tone-review/status"),
)

SYNC_TIMER_UNITS: dict[str, tuple[str, ...]] = {
    "local": (
        "oohstory-library-local-sync.timer",
        "oohstory-library-catalog-scan.timer",
    ),
    "fanqie": (
        "oohstory-library-fanqie-sync.timer",
        "oohstory-library-authorized-catalog-sync.timer",
    ),
}
SYNC_ON_DEMAND_UNITS = {
    "local": "oohstory-library-local-sync.service",
    "fanqie": "oohstory-library-fanqie-sync.service",
}
SYNC_CONTENT_SERVICE_UNITS: dict[str, tuple[str, ...]] = {
    "local": (
        "oohstory-library-local-sync.service",
        "oohstory-library-catalog-scan.service",
    ),
    "fanqie": (
        "oohstory-library-fanqie-sync.service",
        "oohstory-library-authorized-catalog-sync.service",
    ),
}
SYNC_LIBRARY_ASSET_UNITS: dict[str, tuple[str, ...]] = {
    "local": (
        "oohstory-library-cover-sync.service",
        "oohstory-library-local-source-upgrade.service",
    ),
    "fanqie": (
        "oohstory-library-xbiquge-cover-sync.service",
        "oohstory-library-ixdzs-cover-sync.service",
        "oohstory-library-shubaow-cover-sync.service",
        "oohstory-library-fanqie-cover-sync.service",
    ),
}
SYNC_AI_COVER_UNITS = tuple(
    f"oohstory-clean-cover-worker@{index}.service"
    for index in range(1, 9)
)
SYNC_LABELS = {"local": "同步本地书库", "fanqie": "同步番茄书库"}

_INDEX_DEFAULT = {
    "status": "idle",
    "running": False,
    "processed": 0,
    "total": 0,
    "indexed": 0,
    "skipped": 0,
    "failed": 0,
    "message": "尚未建立派生索引",
}
_PLOT_DEFAULT = {
    **_INDEX_DEFAULT,
    "books": 0,
    "segments": 0,
    "message": "尚未建立剧情语义索引",
}
_TONE_DEFAULT = {
    "status": "idle",
    "running": False,
    "processed": 0,
    "total": 0,
    "reviewed": 0,
    "failed": 0,
    "message": "尚无待复核基调",
}
_DERIVED_DEFAULT = {
    "status": "idle",
    "running": False,
    "stage": "idle",
    "message": "等待书籍基调索引或手动剧情索引任务",
}
_INGESTION_DEFAULT = {
    "status": "idle",
    "running": False,
    "stage": "idle",
    "catalog_ids": [],
    "message": "等待新书入库后的轻量索引任务",
}


def _error(exc: Exception) -> str:
    message = " ".join(str(exc).replace("\x00", "").split())[:400]
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    """Read one known status file without accepting an unbounded payload."""
    try:
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            return dict(default)
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else dict(default)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return dict(default)


def _pid_alive(value: Any) -> bool:
    try:
        pid = int(value)
        if pid <= 1:
            return False
        os.kill(pid, 0)
        return True
    except PermissionError:
        # A non-root administration process cannot signal root-owned workers,
        # but EPERM still proves that the process exists.
        return True
    except (TypeError, ValueError, ProcessLookupError, OSError):
        return False


def _read_process_status(value: dict[str, Any], message: str) -> dict[str, Any]:
    status = dict(value)
    if status.get("running") and not _pid_alive(status.get("pid")):
        status.update({"status": "interrupted", "running": False, "message": message})
    return status


class LibraryDatabase:
    """Small read-only MySQL adapter for the admin status surface."""

    def __init__(self, settings: Settings, connector: Callable[..., Any] | None = None):
        self.settings = settings
        self._connector = connector

    def _connect(self):
        if self._connector is None:
            import pymysql
            from pymysql.cursors import DictCursor

            connector = pymysql.connect
            cursorclass = DictCursor
        else:
            connector = self._connector
            cursorclass = None
        options: dict[str, Any] = {
            "host": self.settings.library_mysql_host,
            "port": self.settings.library_mysql_port,
            "user": self.settings.library_mysql_user,
            "password": self.settings.library_mysql_password,
            "database": self.settings.library_mysql_database,
            "charset": "utf8mb4",
            "autocommit": False,
            "connect_timeout": 4,
            "read_timeout": 8,
            "write_timeout": 8,
        }
        if cursorclass is not None:
            options["cursorclass"] = cursorclass
        return connector(**options)

    @staticmethod
    def _all(cursor, query: str) -> list[dict[str, Any]]:
        cursor.execute(query)
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _one(cursor, query: str) -> dict[str, Any]:
        cursor.execute(query)
        row = cursor.fetchone()
        return dict(row or {})

    def snapshot(self) -> dict[str, Any]:
        """Return all catalog/infrastructure counters in one read transaction."""
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                health = self._one(
                    cursor,
                    "SELECT VERSION() AS version, @@hostname AS hostname, "
                    "@@transaction_isolation AS transaction_isolation",
                )
                status_rows = self._all(
                    cursor,
                    "SELECT library_id,status,book_count AS count FROM catalog_status_counts",
                )
                page_rows = self._all(
                    cursor,
                    "SELECT status,COUNT(*) AS count FROM crawl_pages "
                    "WHERE source_name='txt80' GROUP BY status",
                )
                categories = self._all(
                    cursor,
                    "SELECT category AS name,SUM(book_count) AS count FROM catalog_facets "
                    "WHERE body_available=1 GROUP BY category ORDER BY count DESC",
                )
                assets = self._one(
                    cursor,
                    "SELECT "
                    "(SELECT COUNT(*) FROM book_metadata) AS tone_books,"
                    "(SELECT COUNT(*) FROM plot_index_meta) AS plot_books,"
                    "(SELECT COUNT(*) FROM plot_segments) AS plot_segments,"
                    "(SELECT COUNT(*) FROM books WHERE is_active=1 AND body_available=1) "
                    "AS indexable_books",
                )
                deduplicated = self._one(
                    cursor,
                    "SELECT COALESCE(CAST(JSON_UNQUOTE(JSON_EXTRACT(state_value,'$.count')) "
                    "AS UNSIGNED),0) AS count FROM crawl_state "
                    "WHERE source_name='authorized-catalog' "
                    "AND state_key='deduplicated-total'",
                )
                download_jobs = self._all(
                    cursor,
                    "SELECT status,COUNT(*) AS count FROM download_jobs GROUP BY status",
                )
                tone_review = self._one(
                    cursor,
                    "SELECT SUM(tone_review_status='reviewed') AS cumulative_reviewed,"
                    "SUM(tone_review_status='pending') AS pending,"
                    "SUM(tone_review_status='not_needed') AS local_high_confidence "
                    "FROM book_metadata",
                )
                cover_progress = {
                    "local_sync": self._one(
                        cursor,
                        "SELECT COUNT(*) AS total,SUM(status='done') AS done,"
                        "SUM(status='pending') AS pending,SUM(status='failed') AS failed,"
                        "SUM(status='ai_fallback') AS ai_fallback FROM library_covers "
                        "WHERE detail_url LIKE 'https://www.txt80.cc/%'",
                    ),
                    "fanqie_sync": self._one(
                        cursor,
                        "SELECT COUNT(*) AS total,SUM(status='done') AS done,"
                        "SUM(status='pending') AS pending,SUM(status='failed') AS failed,"
                        "SUM(status='ai_fallback') AS ai_fallback "
                        "FROM library_fanqie_cover_jobs",
                    ),
                    "ai_redraw": self._one(
                        cursor,
                        "SELECT SUM(status IN ('done','manual_pending','generate_pending',"
                        "'processing','failed')) AS total,SUM(status='done') AS done,"
                        "SUM(status IN ('pending','manual_pending','generate_pending','processing')) "
                        "AS pending,SUM(status='failed') AS failed,"
                        "SUM(status='generate_pending') AS generate_pending "
                        "FROM library_clean_cover_jobs",
                    ),
                    "local_source_upgrade": self._one(
                        cursor,
                        "SELECT COUNT(*) AS total,SUM(status='done') AS done,"
                        "SUM(status IN ('pending','processing')) AS pending,"
                        "SUM(status='failed') AS failed,SUM(cover_replaced) AS covers_replaced,"
                        "SUM(body_replaced) AS bodies_replaced,"
                        "SUM(ai_fallback_queued) AS ai_fallbacks FROM local_source_upgrade_jobs",
                    ),
                }
            connection.rollback()
        finally:
            connection.close()
        for row in cover_progress.values():
            for key, value in tuple(row.items()):
                row[key] = int(value or 0)
        return {
            "health": {"ok": True, **health},
            "status_rows": status_rows,
            "page_rows": page_rows,
            "categories": categories,
            "assets": {key: int(value or 0) for key, value in assets.items()},
            "authorized_deduplicated": int(deduplicated.get("count") or 0),
            "download_jobs": {
                str(row["status"]): int(row["count"]) for row in download_jobs
            },
            "tone_review": {
                key: int(value or 0) for key, value in tone_review.items()
            },
            "cover_progress": cover_progress,
        }


class LibraryStatusService:
    """Compatibility facade for the seven former library status endpoints."""

    STATUS_ENDPOINTS = STATUS_ENDPOINTS

    def __init__(
        self,
        settings: Settings,
        *,
        database: LibraryDatabase | Any | None = None,
        cache: RedisHotCache | None = None,
        systemctl_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ):
        self.settings = settings
        self.database = database or LibraryDatabase(settings)
        self.cache = cache or hot_cache_from_settings(settings)
        self.systemctl_runner = systemctl_runner

    @property
    def runtime_dir(self) -> Path:
        return self.settings.library_runtime_dir

    def _file_statuses(self) -> dict[str, dict[str, Any]]:
        index = _read_process_status(
            _read_json(self.runtime_dir / "electronic_library_index_status.json", _INDEX_DEFAULT),
            "索引进程已中断，可点击增量更新继续",
        )
        plot = _read_process_status(
            _read_json(self.runtime_dir / "electronic_library_plot_index_status.json", _PLOT_DEFAULT),
            "剧情索引进程已中断，可点击增量更新继续",
        )
        tone = _read_process_status(
            _read_json(self.runtime_dir / "electronic_library_tone_review_status.json", _TONE_DEFAULT),
            "模型复核进程已中断，可重新启动并续跑 pending 项",
        )
        derived = _read_json(
            self.runtime_dir / "electronic_library_derived_index_refresh_status.json",
            _DERIVED_DEFAULT,
        )
        ingestion = _read_json(
            self.runtime_dir / "electronic_library_ingestion_index_status.json",
            _INGESTION_DEFAULT,
        )
        index["pipeline"] = derived
        index["ingestion_pipeline"] = ingestion
        plot["pipeline"] = derived
        return {"index": index, "plot": plot, "tone": tone, "derived": derived}

    def _object_status(self) -> dict[str, Any]:
        root = self.settings.library_object_root
        try:
            usage = shutil.disk_usage(root)
            return {
                "ok": root.is_dir() and os.access(root, os.R_OK | os.X_OK),
                "root": str(root),
                "free_bytes": usage.free,
                "total_bytes": usage.total,
                "read_only_admin": True,
            }
        except OSError as exc:
            return {"ok": False, "root": str(root), "error": _error(exc)}

    def _redis_status(self) -> dict[str, Any]:
        try:
            import redis

            client = redis.Redis(
                host=self.settings.library_redis_host,
                port=self.settings.library_redis_port,
                db=self.settings.library_redis_db,
                password=self.settings.library_redis_password or None,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=3,
            )
            info = client.info("server")
            stream = f"{self.settings.library_redis_prefix}downloads"
            return {
                "ok": bool(client.ping()),
                "version": str(info.get("redis_version") or ""),
                "db": self.settings.library_redis_db,
                "prefix": self.settings.library_redis_prefix,
                "download_stream_length": int(client.xlen(stream)),
            }
        except Exception as exc:
            return {"ok": False, "error": _error(exc)}

    def _unit_statuses(self) -> dict[str, dict[str, Any]]:
        units = tuple(
            dict.fromkeys(
                unit
                for group in (
                    *SYNC_TIMER_UNITS.values(),
                    tuple(SYNC_ON_DEMAND_UNITS.values()),
                    *SYNC_CONTENT_SERVICE_UNITS.values(),
                    *SYNC_LIBRARY_ASSET_UNITS.values(),
                    SYNC_AI_COVER_UNITS,
                )
                for unit in group
            )
        )
        argv = [
            self.settings.systemctl_path,
            "--no-pager",
            "show",
            *units,
            "--property=Id,UnitFileState,ActiveState,NextElapseUSecRealtime,LastTriggerUSec",
        ]
        try:
            result = self.systemctl_runner(
                argv,
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
                shell=False,
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        raw: dict[str, dict[str, str]] = {}
        if result is not None:
            for block in result.stdout.strip().split("\n\n"):
                fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
                if fields.get("Id"):
                    raw[fields["Id"]] = fields
        enabled_states = {"enabled", "enabled-runtime", "linked", "linked-runtime", "alias"}
        active_states = {"active", "activating", "reloading"}
        return {
            unit: {
                "unit": unit,
                "enabled": raw.get(unit, {}).get("UnitFileState", "disabled") in enabled_states,
                "active": raw.get(unit, {}).get("ActiveState", "inactive") in active_states,
                "active_state": raw.get(unit, {}).get("ActiveState", "unavailable"),
                "next_run": raw.get(unit, {}).get("NextElapseUSecRealtime", "").replace("n/a", ""),
                "last_run": raw.get(unit, {}).get("LastTriggerUSec", "").replace("n/a", ""),
            }
            for unit in units
        }

    def _sync_controls(self) -> dict[str, Any]:
        statuses = self._unit_statuses()
        controls: dict[str, Any] = {}
        for library_id, timer_units in SYNC_TIMER_UNITS.items():
            timers = [statuses[unit] for unit in timer_units]
            content_pipeline = statuses[SYNC_ON_DEMAND_UNITS[library_id]]
            content_services = [statuses[unit] for unit in SYNC_CONTENT_SERVICE_UNITS[library_id]]
            asset_pipeline = [statuses[unit] for unit in SYNC_LIBRARY_ASSET_UNITS[library_id]]
            next_runs = [item["next_run"] for item in timers if item["next_run"]]
            last_runs = [item["last_run"] for item in timers if item["last_run"]]
            controls[library_id] = {
                "id": library_id,
                "label": SYNC_LABELS[library_id],
                "enabled": all(item["enabled"] for item in timers),
                "content_enabled": all(item["enabled"] for item in timers),
                "primary_sync_enabled": all(item["enabled"] for item in timers),
                "active": any(item["active"] for item in timers),
                "content_pipeline": content_pipeline,
                "content_services": content_services,
                "content_pipeline_active": any(item["active"] for item in content_services),
                "next_run": next_runs[0] if next_runs else "",
                "last_run": last_runs[0] if last_runs else "",
                "timers": timers,
                "asset_pipeline": asset_pipeline,
                "asset_pipeline_enabled": all(item["enabled"] for item in asset_pipeline),
                "asset_pipeline_active": all(item["active"] for item in asset_pipeline),
                "cover_enabled": all(item["enabled"] for item in asset_pipeline),
                "cover_active": all(item["active"] for item in asset_pipeline),
                "pipeline_description": (
                    "书目总量 + 新书下载 + 源站更新版本"
                    if library_id == "local"
                    else "番茄下载历史 + 新书导入 + 已跟踪作品更新"
                ),
                "cover_description": (
                    "TXT80/TXT020 水印封面先查三站；无资源/404 才转 AI"
                    if library_id == "local"
                    else "按真实书源同步封面；确定无图或 404 才按书名 AI 生成"
                ),
                "ai_cover_enabled": all(statuses[unit]["enabled"] for unit in SYNC_AI_COVER_UNITS),
            }
        return controls

    def _deconstruction_status(self) -> dict[str, Any]:
        root = self.settings.library_root / "全局拆书库"
        task_root = root / ".tasks"
        counts = {"total": 0, "running": 0, "completed": 0}
        scan_limit = 200
        truncated = False
        try:
            with os.scandir(task_root) as entries:
                for index, entry in enumerate(entries):
                    if index >= scan_limit:
                        truncated = True
                        break
                    if not entry.is_file(follow_symlinks=False) or not entry.name.endswith(".json"):
                        continue
                    counts["total"] += 1
                    item = _read_json(Path(entry.path), {})
                    state = str(item.get("status") or "")
                    if state in {"queued", "running"}:
                        counts["running"] += 1
                    elif state == "completed":
                        counts["completed"] += 1
        except OSError:
            pass
        return {
            "root": str(root),
            **counts,
            "sample_limit": scan_limit,
            "truncated": truncated,
            "source": "shared-filesystem-bounded",
        }

    @staticmethod
    def _libraries(status_rows: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, Any]]:
        counts: dict[str, int] = {}
        libraries: dict[str, Any] = {
            "local": {"id": "local", "name": "本地书库", "total": 0, "downloaded": 0, "pending": 0, "recovery": 0, "failed": 0},
            "fanqie": {"id": "fanqie", "name": "番茄书库", "total": 0, "downloaded": 0, "pending": 0, "recovery": 0, "failed": 0},
        }
        for row in status_rows:
            state = str(row.get("status") or "")
            count = int(row.get("count") or 0)
            counts[state] = counts.get(state, 0) + count
            if state == "duplicate":
                continue
            library = libraries.get(str(row.get("library_id") or ""))
            if library is None:
                continue
            library["total"] += count
            if state == "done":
                library["downloaded"] += count
            elif state in {"discovered", "downloading"}:
                library["pending"] += count
            elif state == "failed":
                library["failed"] += count
        for item in libraries.values():
            item["recovery"] = max(item["total"] - item["downloaded"], 0)
        return counts, libraries

    def _source_status(self, snapshot: dict[str, Any], files: dict[str, dict[str, Any]]) -> dict[str, Any]:
        counts, libraries = self._libraries(snapshot["status_rows"])
        pages = {str(row["status"]): int(row["count"]) for row in snapshot["page_rows"]}
        assets = snapshot["assets"]
        stored_duplicates = int(counts.get("duplicate", 0))
        intercepted = stored_duplicates + snapshot["authorized_deduplicated"]
        tone = {**files["tone"], **snapshot["tone_review"]}
        return {
            "library_root": str(self.settings.library_root),
            "catalog_path": "mysql://books",
            "catalog_backend": "mysql",
            "global_deconstruction_root": str(self.settings.library_root / "全局拆书库"),
            "source_read_only": True,
            "catalog_write_policy": "OOHStory 管理状态层只读；写入由白名单流水线负责",
            "remote_provider": {"id": "authorized_sources", "name": "OOHStory 授权/公版书源"},
            "remote_providers": [],
            "books": {
                "total": sum(value for state, value in counts.items() if state != "duplicate"),
                "raw_total": sum(counts.values()),
                "duplicates": stored_duplicates,
                "intercepted_duplicates": intercepted,
                "duplicate_breakdown": {
                    "stored_catalog_rows": stored_duplicates,
                    "authorized_sources": snapshot["authorized_deduplicated"],
                    "total": intercepted,
                },
                "downloaded": int(counts.get("done", 0)),
                "discovered": int(counts.get("discovered", 0)),
                "failed": int(counts.get("failed", 0)),
                "by_status": counts,
                "libraries": libraries,
                "indexable": assets["indexable_books"],
                "indexes_synchronized": assets["indexable_books"] == assets["tone_books"],
                "tone_index_synchronized": assets["indexable_books"] == assets["tone_books"],
                "plot_index_synchronized": assets["indexable_books"] == assets["plot_books"],
            },
            "pages": {"total": sum(pages.values()), "by_status": pages},
            "categories": [
                {"name": str(row.get("name") or "未分类"), "count": int(row.get("count") or 0)}
                for row in snapshot["categories"]
            ],
            "index": {
                **files["index"],
                "count": assets["tone_books"],
                "path": "mysql://book_metadata",
                "global_shared": True,
                "source_catalog_read_only": True,
            },
            "tone_review": tone,
            "derived_index_refresh": files["derived"],
            "plot_index": {
                **files["plot"],
                "books": assets["plot_books"],
                "segments": assets["plot_segments"],
                "path": "mysql://plot_index_meta+plot_segments",
                "global_shared": True,
                "source_catalog_read_only": True,
                "token_strategy": "本地索引召回 → 少量证据交给 AI",
            },
            "sync": _read_json(
                self.runtime_dir / "library-sync-status.json",
                {"status": "idle", "running": False, "message": "尚未执行书库同步审计"},
            ),
            "deconstruction": self._deconstruction_status(),
            "runners": {"managed_by": "oohstory-backend", "read_only_status": True},
            "cover_progress": snapshot["cover_progress"],
            "download_jobs": snapshot["download_jobs"],
            "tasks": [],
        }

    def statuses(self) -> dict[str, dict[str, Any]]:
        """Return the former HTTP endpoint envelopes using only local resources."""
        files = self._file_statuses()
        try:
            snapshot = self.database.snapshot()
            mysql_error = None
        except Exception as exc:
            snapshot = None
            mysql_error = _error(exc)
        object_status = self._object_status()
        infrastructure = {
            "catalog_backend": "mysql",
            "mysql": snapshot["health"] if snapshot else {"ok": False, "error": mysql_error},
            "redis": self._redis_status(),
            "cache": self.cache.stats(),
            "object_store": object_status,
            "ready_for_mysql_reads": bool(snapshot and object_status.get("ok")),
            "source": "oohstory-backend",
        }
        health = {
            "status": "healthy" if snapshot and self.settings.library_root.is_dir() else "degraded",
            "service": "oohstory-electronic-library",
            "catalog_backend": "mysql",
            "library_root": str(self.settings.library_root),
            "independent_of_webnovel_http": True,
        }
        results: dict[str, dict[str, Any]] = {
            "health": {"available": True, "data": health, "error": None, "endpoint": "/api/health"},
            "infrastructure": {"available": True, "data": infrastructure, "error": None, "endpoint": "/api/library/infrastructure/status"},
            "sync_controls": {"available": True, "data": self._sync_controls(), "error": None, "endpoint": "/api/library/sync-controls"},
            "book_index": {"available": True, "data": files["index"], "error": None, "endpoint": "/api/library/index/status"},
            "plot_index": {"available": True, "data": files["plot"], "error": None, "endpoint": "/api/library/plot-index/status"},
            "tone_review": {"available": True, "data": files["tone"], "error": None, "endpoint": "/api/library/tone-review/status"},
        }
        if snapshot is None:
            results["library"] = {
                "available": False,
                "data": None,
                "error": f"MySQL 书目不可用：{mysql_error}",
                "endpoint": "/api/library/status",
            }
        else:
            results["library"] = {
                "available": True,
                "data": self._source_status(snapshot, files),
                "error": None,
                "endpoint": "/api/library/status",
            }
            results["tone_review"]["data"] = {
                **files["tone"],
                **snapshot["tone_review"],
            }
        return results


# The admin application historically called this facade ``LibraryClient``.
# Keep the name as a local compatibility alias while removing all HTTP usage.
LibraryClient = LibraryStatusService
