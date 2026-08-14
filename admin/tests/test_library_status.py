from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from oohstory_admin.library_status import LibraryStatusService, _pid_alive
from conftest import login


def _snapshot() -> dict:
    return {
        "health": {
            "ok": True,
            "version": "8.0-test",
            "hostname": "mysql-test",
            "transaction_isolation": "READ-COMMITTED",
        },
        "status_rows": [
            {"library_id": "local", "status": "done", "count": 8},
            {"library_id": "local", "status": "failed", "count": 1},
            {"library_id": "fanqie", "status": "discovered", "count": 2},
            {"library_id": "fanqie", "status": "duplicate", "count": 3},
        ],
        "page_rows": [{"status": "done", "count": 12}],
        "categories": [{"name": "科幻", "count": 4}],
        "assets": {
            "tone_books": 8,
            "plot_books": 7,
            "plot_segments": 99,
            "indexable_books": 8,
        },
        "authorized_deduplicated": 5,
        "download_jobs": {"done": 8},
        "tone_review": {
            "cumulative_reviewed": 6,
            "pending": 2,
            "local_high_confidence": 4,
        },
        "cover_progress": {
            "local_sync": {"total": 8, "done": 7, "pending": 1, "failed": 0},
            "fanqie_sync": {"total": 2, "done": 2, "pending": 0, "failed": 0},
            "ai_redraw": {"total": 1, "done": 1, "pending": 0, "failed": 0},
            "local_source_upgrade": {"total": 8, "done": 8, "pending": 0, "failed": 0},
        },
    }


class FakeDatabase:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result or _snapshot()
        self.error = error
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def _settings(settings, tmp_path: Path):
    root = tmp_path / "electronic-library"
    runtime = root / "全局索引"
    object_root = tmp_path / "electronic-library"
    runtime.mkdir(parents=True)
    object_root.mkdir(parents=True, exist_ok=True)
    return replace(
        settings,
        library_root=root,
        library_runtime_dir=runtime,
        library_object_root=object_root,
        library_mysql_password="not-used-by-fake",
    )


def _systemctl(argv, **kwargs):
    units = [value for value in argv[3:] if not value.startswith("--property=")]
    blocks = []
    for unit in units:
        blocks.append(
            "\n".join(
                (
                    f"Id={unit}",
                    "UnitFileState=enabled",
                    "ActiveState=active",
                    "NextElapseUSecRealtime=Mon 2026-08-03 17:00:00 +08",
                    "LastTriggerUSec=Mon 2026-08-03 16:00:00 +08",
                )
            )
        )
    return subprocess.CompletedProcess(argv, 0, "\n\n".join(blocks), "")


def test_local_library_status_uses_mysql_snapshot_and_status_files(settings, tmp_path):
    local = _settings(settings, tmp_path)
    (local.library_runtime_dir / "electronic_library_index_status.json").write_text(
        json.dumps({"status": "completed", "running": False, "indexed": 8}),
        encoding="utf-8",
    )
    database = FakeDatabase()
    service = LibraryStatusService(local, database=database, systemctl_runner=_systemctl)
    service._redis_status = lambda: {"ok": True, "version": "test"}

    statuses = service.statuses()

    assert database.calls == 1
    assert statuses["health"]["data"]["independent_of_webnovel_http"] is True
    assert statuses["library"]["available"] is True
    library = statuses["library"]["data"]
    assert library["catalog_backend"] == "mysql"
    assert library["catalog_path"] == "mysql://books"
    assert library["books"]["total"] == 11
    assert library["books"]["raw_total"] == 14
    assert library["books"]["intercepted_duplicates"] == 8
    assert library["books"]["indexes_synchronized"] is True
    assert library["plot_index"]["segments"] == 99
    assert statuses["book_index"]["data"]["indexed"] == 8
    assert statuses["sync_controls"]["data"]["local"]["enabled"] is True
    assert statuses["infrastructure"]["data"]["ready_for_mysql_reads"] is True


def test_mysql_unavailable_is_honest_and_never_calls_8080(settings, tmp_path, monkeypatch):
    local = _settings(settings, tmp_path)

    def forbidden_http(*args, **kwargs):
        raise AssertionError("electronic-library status must not call HTTP")

    monkeypatch.setattr("urllib.request.urlopen", forbidden_http)
    database = FakeDatabase(error=TimeoutError("mysql timeout"))
    service = LibraryStatusService(local, database=database, systemctl_runner=_systemctl)
    service._redis_status = lambda: {"ok": False, "error": "unavailable"}

    statuses = service.statuses()

    assert statuses["health"]["data"]["status"] == "degraded"
    assert statuses["library"]["available"] is False
    assert "MySQL" in statuses["library"]["error"]
    assert statuses["infrastructure"]["data"]["mysql"]["ok"] is False
    assert statuses["infrastructure"]["data"]["ready_for_mysql_reads"] is False
    assert statuses["book_index"]["available"] is True


def test_admin_jobs_api_uses_local_status_service_without_8080(
    settings, components, tmp_path, monkeypatch
):
    from oohstory_admin.app import create_app

    local = _settings(settings, tmp_path)
    reader, _old_library, systemd, audit = components

    def forbidden_http(*args, **kwargs):
        raise AssertionError("/api/admin/jobs must not call webnovel-writer HTTP")

    monkeypatch.setattr("urllib.request.urlopen", forbidden_http)
    library = LibraryStatusService(local, database=FakeDatabase(), systemctl_runner=_systemctl)
    library._redis_status = lambda: {"ok": True}
    app = create_app(local, reader=reader, library=library, systemd=systemd, audit=audit)

    with TestClient(app) as client:
        assert login(client).status_code == 303
        response = client.get("/api/admin/jobs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["health"]["data"]["independent_of_webnovel_http"] is True
    assert payload["library"]["data"]["catalog_backend"] == "mysql"


def test_stale_running_status_is_reported_interrupted_without_rewriting(settings, tmp_path):
    local = _settings(settings, tmp_path)
    path = local.library_runtime_dir / "electronic_library_plot_index_status.json"
    original = {"status": "running", "running": True, "pid": 999_999_999, "processed": 4}
    raw = json.dumps(original, ensure_ascii=False)
    path.write_text(raw, encoding="utf-8")
    service = LibraryStatusService(local, database=FakeDatabase(), systemctl_runner=_systemctl)
    service._redis_status = lambda: {"ok": True}

    statuses = service.statuses()

    assert statuses["plot_index"]["data"]["status"] == "interrupted"
    assert statuses["plot_index"]["data"]["running"] is False
    assert path.read_text(encoding="utf-8") == raw


def test_root_owned_worker_permission_error_still_means_alive(monkeypatch):
    def denied(_pid: int, _signal: int) -> None:
        raise PermissionError

    monkeypatch.setattr(os, "kill", denied)
    assert _pid_alive(4321) is True


def test_repository_does_not_publish_a_host_data_link():
    project = Path(__file__).resolve().parents[1]
    link = project / "electronic-library"
    assert not link.exists()
    assert "electronic-library" in (project / ".gitignore").read_text(
        encoding="utf-8"
    )


def test_source_contains_no_webnovel_http_dependency():
    source_root = Path(__file__).resolve().parents[1] / "src" / "oohstory_admin"
    legacy_endpoint = "127.0.0.1:" + "8080"
    legacy_variable = "OOHSTORY_ADMIN_" + "LIBRARY_URL"
    for path in source_root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert legacy_endpoint not in text
        assert legacy_variable not in text
