from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from conftest import login
from oohstory_admin.library_actions import LibraryActionClient, LibraryActionResult


class FakeLibraryActions:
    def __init__(self):
        self.calls = []

    def run(self, action, payload=None, **kwargs):
        request = payload or {}
        self.calls.append((action, request))
        if action in {"cover", "import"}:
            data = {"job_id": "a" * 20}
        elif action == "sync_status":
            data = {
                "local": {
                    "id": "local", "label": "本地书库", "content_enabled": True,
                    "content_pipeline_active": False, "next_run": "明日 02:00",
                    "last_run": "今日 02:00", "pipeline_description": "本地书目与正文增量同步",
                },
                "fanqie": {
                    "id": "fanqie", "label": "番茄书库", "content_enabled": False,
                    "content_pipeline_active": False, "next_run": "", "last_run": "",
                    "pipeline_description": "番茄下载历史与已跟踪作品更新",
                },
                "site_full_sync": {
                    "target_books_per_minute": 100,
                    "authorized_sites_share_slot": False,
                    "execution_lanes": {
                        "txt80": "local",
                        "xbiquge": "http-xbiquge",
                        "ixdzs": "http-ixdzs",
                        "shubaow": "browser-shubaow",
                        "linovelib": "browser-shubaow",
                    },
                    "sites": [
                        {
                            "id": site_id,
                            "label": label,
                            "enabled": False,
                            "active": False,
                            "status": "idle",
                            "message": "尚未启动",
                            "updated_at": "",
                            "configurable": True,
                            "books_per_cycle": 100,
                            "slot_lane": lane,
                            "batch": {"selected": 0, "completed": 0, "failed": 0, "deferred": 0},
                            "cumulative": {"attempted": 0, "completed": 0, "failed": 0, "deferred": 0},
                        }
                        for site_id, label, lane in (
                            ("txt80", "TXT80 本地书库正文", "local"),
                            ("xbiquge", "新笔趣阁授权正文", "http-xbiquge"),
                            ("ixdzs", "爱下授权正文", "http-ixdzs"),
                            ("shubaow", "书宝授权正文", "browser-shubaow"),
                            ("linovelib", "哔哩轻小说授权正文", "browser-shubaow"),
                        )
                    ],
                },
                "serialized_update": {
                    "label": "连载追更",
                    "rate_contract": "每个来源独立 update lane 定时扫描最近更新榜",
                    "sources": [
                        {
                            "id": "ixdzs",
                            "label": "爱下连载追更",
                            "lane": "update-ixdzs",
                            "enabled": False,
                            "service_active": False,
                            "timer_active": False,
                            "status": "completed",
                            "mode": "direct",
                            "checked_at": "2026-08-12 12:00:00",
                            "last_run": "",
                            "totals": {
                                "seen": 20,
                                "local_matches": 3,
                                "refresh_selected": 1,
                                "refresh_applied": 1,
                                "failed": 0,
                            },
                            "tracked": {"applied": 8, "observed": 12, "ongoing_books": 30},
                            "last_errors": [],
                        }
                    ],
                },
                "cover_redraw": {
                    "target_per_hour": 60,
                    "configured_workers": 3,
                    "active_workers": 3,
                    "enabled": True,
                    "active": True,
                    "actual": {
                        "completed_last_hour": 61,
                        "processing": 3,
                        "pending": 400,
                        "failed": 0,
                    },
                },
            }
        elif action == "root_migration_status":
            data = {
                "status": "idle",
                "stage": "idle",
                "message": "尚未执行书库根路径迁移",
                "current_root": "/srv/oohstory/library",
                "updated_at": "2026-08-07T03:00:00+00:00",
            }
        elif action == "root_migration_preflight":
            data = {
                "token": "d" * 32,
                "source": request["source"],
                "destination": request["destination"],
                "same_device": True,
                "source_is_mountpoint": True,
                "top_level": [{"name": "书籍"}, {"name": "全局索引"}],
                "mysql_rows": 384787,
                "config_files": ["/etc/example.env"],
                "units": ["oohstory-reader.service", "webnovel-writer-backend.service"],
                "confirmation": "迁移电子书库",
            }
        elif action == "root_migration_start":
            data = {
                "status": "running",
                "stage": "queued",
                "message": "迁移任务已进入独立系统服务",
            }
        elif action == "plot_adaptations":
            data = {
                "project_root": "/var/lib/oohstory-admin/library-project",
                "project": {"title": "OOHStory 托管项目", "genre": "硬核科幻"},
                "items": [],
            }
        elif action == "plot_query":
            data = {
                "question": request["question"], "answer": "有，候选作品具备相似冲突功能。[1]",
                "index_ready": True, "ai_calls": 1, "evidence_chars_sent": 120,
                "token_strategy": "本地索引优先",
                "results": [{
                    "catalog_id": 42, "title": "星海归途", "author": "测试作者",
                    "category": "科幻", "confirmation_level": "本地正文证据候选",
                    "evidence": [{"content": "资源断供后重建轨道物流。"}],
                }],
            }
        elif action == "plot_plan":
            data = {
                "id": "c" * 12, "status": "planned", "adaptation_title": "轨道补给反击",
                "judgment": "需改造", "reason": "与项目基础设施主线相容",
                "material_summary": "通过物流冗余完成逆转。", "emotional_goal": "绝境→掌控",
                "originality_boundary": "仅抽象功能，不复制来源桥段",
                "recommended_target": {"chapter_start": 12, "chapter_end": 13},
                "chapter_beats": [{"chapter": 12, "role": "加压", "beat": "补给中断", "payoff": "识破", "hook": "备用轨道启动"}],
            }
        elif action == "plot_preview":
            data = {
                "plan_id": request["plan_id"], "status": "ready", "ready_count": 1,
                "unwritten_count": 0,
                "chapters": [{"chapter": 12, "status": "ready", "revision_note": "局部插入", "diff": "+ 新增物流逆转段"}],
            }
        elif action == "plot_apply":
            data = {
                "id": request["plan_id"],
                "status": "applied" if request["operation"] == "bind" else "applied_to_text",
                "adaptation_title": "轨道补给反击",
                "material_path": "剧情素材/电子书库/plan.md",
            }
        else:
            data = {"status": "accepted"}
        return LibraryActionResult(True, action, "操作完成", data)

    def search(self, query, source, limit=24):
        self.calls.append(("search", {"query": query, "source": source, "limit": limit}))
        return {
            "search_source_label": "新笔趣阁全章节",
            "local_count": 0,
            "remote_count": 1,
            "local": [],
            "remote": [
                {
                    "provider": source,
                    "remote_id": "100",
                    "source_ref": "/book/100/",
                    "title": "星海归途",
                    "author": "测试作者",
                    "downloadable": True,
                }
            ],
        }

    def job(self, job_id):
        self.calls.append(("job_status", {"job_id": job_id}))
        return {"job_id": job_id, "kind": "import", "status": "running", "message": "下载中"}

    def task_runners(self):
        return {
            "default_runner": "codex",
            "max_parallel": 8,
            "runners": [
                {
                    "id": "codex",
                    "name": "Codex",
                    "available": True,
                    "default_profile": "gpt-5.4",
                    "profiles": [
                        {
                            "id": "gpt-5.4",
                            "name": "GPT-5.4",
                            "default_reasoning_effort": "high",
                            "reasoning_options": [
                                {"id": "medium", "name": "中"},
                                {"id": "high", "name": "高"},
                            ],
                        }
                    ],
                }
            ],
        }

    def task(self, task_id):
        self.calls.append(("task_detail", {"task_id": task_id}))
        return {
            "id": task_id,
            "title": "星海归途",
            "status": "running",
            "current_stage": "章节分析",
            "progress": 35,
            "log": "worker ready",
        }


def build_client(settings, components, catalog):
    from oohstory_admin.app import create_app

    reader, library, systemd, audit = components
    actions = FakeLibraryActions()
    app = create_app(
        settings,
        reader=reader,
        library=library,
        systemd=systemd,
        audit=audit,
        catalog=catalog,
        library_actions=actions,
    )
    return app, actions


def test_root_runner_is_fail_closed_and_reports_capabilities():
    runner = Path(__file__).parents[1] / "ops" / "oohstory-admin-library-action-runner"
    completed = subprocess.run(
        [sys.executable, str(runner)],
        input=json.dumps({"action": "capabilities"}).encode(),
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["data"]["project_tone_matching"] is False
    assert "site_full_sync" in payload["data"]["actions"]
    assert "serialized_update_source" in payload["data"]["actions"]
    assert "delete_catalog_books" in payload["data"]["actions"]
    assert payload["data"]["delete_limit"] == 100
    runner_source = runner.read_text(encoding="utf-8")
    assert ".codespace/workspace" not in runner_source
    assert "/etc/webnovel-writer" not in runner_source
    assert "webnovel-library-" not in runner_source
    assert "/opt/oohstory-admin/scripts/electronic-library/delete_catalog_books.py" in runner_source
    rejected = subprocess.run(
        [sys.executable, str(runner)],
        input=json.dumps({"action": "shell", "command": "id"}).encode(),
        capture_output=True,
        check=False,
    )
    assert rejected.returncode == 64
    assert "允许列表" in json.loads(rejected.stdout)["message"]

    wrapper = runner.with_name("oohstory-admin-library-action").read_text(encoding="utf-8")
    assert "/usr/bin/systemd-run" in wrapper
    assert "--dispatch-stdin" in wrapper
    assert "ReadWritePaths=/srv/oohstory/library /var/lib/oohstory-admin" in wrapper
    assert "ProtectHome=false" in wrapper


def test_helper_client_uses_json_stdin_and_no_user_arguments(monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["request"] = json.loads(kwargs["input"])
        return subprocess.CompletedProcess(
            command, 0, stdout=b'{"ok":true,"message":"ok","data":{"status":"accepted"}}', stderr=b""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = LibraryActionClient("/fixed/helper", use_sudo_helper=True)
    result = client.run("move", {"catalog_ids": [1], "target_library": "local"})
    assert result.ok is True
    assert seen["command"] == ["/usr/bin/sudo", "-n", "/fixed/helper"]
    assert seen["request"]["catalog_ids"] == [1]


def test_root_runner_delegates_delete_to_oohstory_cli(
    monkeypatch, tmp_path
):
    runner = Path(__file__).parents[1] / "ops" / "oohstory-admin-library-action-runner"
    namespace = runpy.run_path(str(runner))
    delete_catalog_books = namespace["delete_catalog_books"]
    python_path = tmp_path / "python"
    script_path = tmp_path / "delete_catalog_books.py"
    python_path.write_text("python", encoding="utf-8")
    script_path.write_text("script", encoding="utf-8")
    delete_catalog_books.__globals__["OOHSTORY_PYTHON"] = python_path
    delete_catalog_books.__globals__["OOHSTORY_CATALOG_DELETE"] = script_path
    seen = {"environment_loaded": 0}
    delete_catalog_books.__globals__["load_environment"] = (
        lambda: seen.__setitem__(
            "environment_loaded", seen["environment_loaded"] + 1
        )
    )

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["request"] = json.loads(kwargs["input"])
        response = {
            "ok": True,
            "status": "deleted",
            "deleted": 2,
            "catalog_ids": [42, 43],
            "archive_id": "archive-test",
            "message": "已删除 2 本书",
        }
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(response).encode(), stderr=b""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = delete_catalog_books(
        {
            "catalog_ids": [43, 42, 43],
            "confirmation": "确认删除2本书",
        }
    )
    assert result["archive_id"] == "archive-test"
    assert seen["environment_loaded"] == 1
    assert seen["command"] == [str(python_path), str(script_path)]
    assert seen["request"] == {
        "catalog_ids": [42, 43],
        "confirmation": "确认删除2本书",
    }

    with pytest.raises(ValueError, match="确认删除2本书"):
        delete_catalog_books(
            {"catalog_ids": [42, 43], "confirmation": "确认删除全部"}
        )
    with pytest.raises(ValueError, match="未允许字段"):
        delete_catalog_books(
            {
                "catalog_ids": [42],
                "confirmation": "确认删除1本书",
                "path": "/tmp/forbidden",
            }
        )
    with pytest.raises(ValueError, match="正整数数组"):
        delete_catalog_books(
            {"catalog_ids": [True], "confirmation": "确认删除1本书"}
        )


def test_scheduler_activation_controls_only_oohstory_timers():
    script = (
        Path(__file__).parents[1] / "ops" / "oohstory-library-scheduler-cutover"
    ).read_text(encoding="utf-8")
    assert "webnovel-library-" not in script
    assert "enable --now $timers" in script
    assert "oohstory-library-local-sync.timer" in script


def test_workbench_write_search_import_and_job_routes(settings, components):
    from test_library_catalog_ui import FakeCatalog

    app, actions = build_client(settings, components, FakeCatalog())
    with TestClient(app) as client:
        assert login(client).status_code == 303
        page = client.get("/admin/books/catalog?view=all")
        assert page.status_code == 200
        assert "书目总量" in page.text
        assert "移入本地书库" in page.text
        csrf = client.get("/api/admin/session").json()["csrf_token"]

        moved = client.post(
            "/admin/books/catalog-action",
            data={"csrf_token": csrf, "view": "all", "catalog_id": "42", "action": "move_local"},
            follow_redirects=False,
        )
        assert moved.status_code == 303
        assert actions.calls[-1] == ("move", {"catalog_ids": [42], "target_library": "local"})

        search = client.get("/admin/books/search?q=星海&source=authorized_xbiquge")
        assert search.status_code == 200
        assert "星海归途" in search.text
        assert "下载并归档" in search.text

        imported = client.post(
            "/admin/books/import",
            data={
                "csrf_token": csrf,
                "provider": "authorized_xbiquge",
                "remote_id": "100",
                "source_ref": "/book/100/",
            },
            follow_redirects=False,
        )
        assert imported.status_code == 303
        assert imported.headers["location"].endswith("/books/jobs/" + "a" * 20)

        job = client.get("/admin/books/jobs/" + "a" * 20)
        assert job.status_code == 200
        assert "下载中" in job.text

        api = client.post(
            "/api/admin/books/catalog/actions",
            headers={"x-csrf-token": csrf},
            json={"action": "cover_sync", "catalog_ids": [42]},
        )
        assert api.status_code == 200
        assert api.json()["data"]["job_id"] == "a" * 20


def test_catalog_delete_is_explicit_bounded_confirmed_and_audited(settings, components):
    from test_library_catalog_ui import FakeCatalog

    app, actions = build_client(settings, components, FakeCatalog())
    with TestClient(app) as client:
        assert login(client).status_code == 303
        page = client.get("/admin/books/catalog?view=all")
        assert page.status_code == 200
        assert "危险区 · 删除所选小说" in page.text
        assert "阅读映射" in page.text
        assert "拆书成果" in page.text
        assert 'value="delete_catalog_books"' in page.text
        assert "确认删除0本书" in page.text
        assert "仅删除本页明确勾选的书目" in page.text
        assert "危险区 · 删除所选小说" not in client.get(
            "/admin/books/catalog?view=local"
        ).text
        csrf = client.get("/api/admin/session").json()["csrf_token"]

        before = len(actions.calls)
        rejected = client.post(
            "/admin/books/catalog-action",
            data={
                "csrf_token": csrf,
                "view": "all",
                "catalog_id": "42",
                "action": "delete_catalog_books",
                "confirmation": "确认删除全部",
            },
        )
        assert rejected.status_code == 400
        assert len(actions.calls) == before
        assert "确认删除1本书" in rejected.text

        malformed = client.post(
            "/admin/books/catalog-action",
            content=urlencode([
                ("csrf_token", csrf),
                ("view", "all"),
                ("catalog_id", "42"),
                ("catalog_id", "../../etc/passwd"),
                ("action", "delete_catalog_books"),
                ("confirmation", "确认删除2本书"),
            ]),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert malformed.status_code == 400
        assert len(actions.calls) == before

        over_limit = client.post(
            "/admin/books/catalog-action",
            content=urlencode([
                ("csrf_token", csrf),
                ("view", "all"),
                *(("catalog_id", str(value)) for value in range(1, 102)),
                ("action", "delete_catalog_books"),
                ("confirmation", "确认删除101本书"),
            ]),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert over_limit.status_code == 400
        assert len(actions.calls) == before

        deleted = client.post(
            "/admin/books/catalog-action",
            content=urlencode([
                ("csrf_token", csrf),
                ("view", "all"),
                ("catalog_id", "42"),
                ("catalog_id", "43"),
                ("catalog_id", "42"),
                ("action", "delete_catalog_books"),
                ("confirmation", "确认删除2本书"),
            ]),
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert deleted.status_code == 303
        assert actions.calls[-1] == (
            "delete_catalog_books",
            {"catalog_ids": [42, 43], "confirmation": "确认删除2本书"},
        )
        audit_rows = app.state.audit.list()
        row = next(item for item in audit_rows if item["action"] == "delete_catalog_books")
        assert row["target"] == "catalog_ids:42,43"
        assert row["result"].startswith("success:")


def test_catalog_delete_api_requires_csrf_and_exact_phrase(settings, components):
    from test_library_catalog_ui import FakeCatalog

    class RecordingCache:
        def __init__(self):
            self.calls = []

        def invalidate(self, *scopes):
            self.calls.append(scopes)

    catalog = FakeCatalog()
    catalog.cache = RecordingCache()
    app, actions = build_client(settings, components, catalog)
    with TestClient(app) as client:
        assert login(client).status_code == 303
        csrf = client.get("/api/admin/session").json()["csrf_token"]
        missing_csrf = client.post(
            "/api/admin/books/catalog/actions",
            json={
                "action": "delete_catalog_books",
                "catalog_ids": [42],
                "confirmation": "确认删除1本书",
            },
        )
        assert missing_csrf.status_code == 403
        rejected = client.post(
            "/api/admin/books/catalog/actions",
            headers={"x-csrf-token": csrf},
            json={
                "action": "delete_catalog_books",
                "catalog_ids": [42],
                "confirmation": "错误短语",
            },
        )
        assert rejected.status_code == 400
        bool_id = client.post(
            "/api/admin/books/catalog/actions",
            headers={"x-csrf-token": csrf},
            json={
                "action": "delete_catalog_books",
                "catalog_ids": [True],
                "confirmation": "确认删除1本书",
            },
        )
        assert bool_id.status_code == 422
        deleted = client.post(
            "/api/admin/books/catalog/actions",
            headers={"x-csrf-token": csrf},
            json={
                "action": "delete_catalog_books",
                "catalog_ids": [43, 42, 43],
                "confirmation": "确认删除2本书",
            },
        )
        assert deleted.status_code == 200
        assert actions.calls[-1] == (
            "delete_catalog_books",
            {"catalog_ids": [42, 43], "confirmation": "确认删除2本书"},
        )
        assert catalog.cache.calls[-1] == (
            "catalog", "book", "cover", "tone", "plot", "deconstruction"
        )


def test_deconstruction_filtered_batch_runner_task_and_cover_upload(settings, components):
    from test_library_catalog_ui import FakeCatalog

    app, actions = build_client(settings, components, FakeCatalog())
    with TestClient(app) as client:
        assert login(client).status_code == 303
        page = client.get("/admin/books?view=deconstruction&state=error&q=星海&category=科幻")
        assert page.status_code == 200
        assert "拆书执行配置" in page.text
        assert 'data-profile="gpt-5.4"' in page.text
        assert "任务详情/日志" not in page.text
        csrf = client.get("/api/admin/session").json()["csrf_token"]

        filtered = client.post(
            "/api/admin/books/catalog/actions",
            headers={"x-csrf-token": csrf},
            json={
                "action": "batch_filtered_full",
                "catalog_ids": [],
                "state": "error",
                "query": "星海",
                "category": "科幻",
                "runner_id": "codex",
                "profile_id": "gpt-5.4",
                "reasoning_effort": "high",
            },
        )
        assert filtered.status_code == 200
        assert actions.calls[-1] == (
            "deconstruction_batch",
            {
                "catalog_ids": [],
                "mode": "full",
                "scope": "filtered",
                "state": "error",
                "query": "星海",
                "category": "科幻",
                "runner_id": "codex",
                "profile_id": "gpt-5.4",
                "reasoning_effort": "high",
            },
        )

        rejected = client.post(
            "/api/admin/books/catalog/actions",
            headers={"x-csrf-token": csrf},
            json={"action": "move_local", "catalog_ids": []},
        )
        assert rejected.status_code == 400

        task_id = "b" * 12
        task = client.get(f"/admin/books/tasks/{task_id}")
        assert task.status_code == 200
        assert "worker ready" in task.text

        detail = client.get("/admin/books/catalog/42")
        assert detail.status_code == 200
        assert "上传并替换封面" in detail.text
        uploaded = client.post(
            "/api/admin/books/catalog/42/cover-upload",
            headers={"x-csrf-token": csrf, "content-type": "image/jpeg"},
            content=b"\xff\xd8" + b"x" * 2048,
        )
        assert uploaded.status_code == 200
        action, payload = actions.calls[-1]
        assert action == "upload_cover"
        assert payload["catalog_id"] == 42
        assert len(payload["upload_token"]) == 32
        assert not list(settings.library_upload_dir.glob("*.bin"))


def test_sync_index_plot_query_and_adaptation_workbench(settings, components):
    from test_library_catalog_ui import FakeCatalog

    app, actions = build_client(settings, components, FakeCatalog())
    with TestClient(app) as client:
        assert login(client).status_code == 303
        csrf = client.get("/api/admin/session").json()["csrf_token"]

        sync_page = client.get("/admin/library/sync")
        assert sync_page.status_code == 200
        assert "书库定时同步" in sync_page.text
        assert "本地书目与正文增量同步" in sync_page.text
        assert "按站点全力同步正文" in sync_page.text
        assert "连载书正式追更" in sync_page.text
        assert "update-ixdzs" in sync_page.text
        assert "封面重绘加速器" in sync_page.text
        assert "书库识别路径与目录迁移" in sync_page.text
        assert "/srv/oohstory/library" in sync_page.text
        assert "每轮目录本数" in sync_page.text
        assert "61" in sync_page.text

        migration_preflight = client.post(
            "/admin/books/root-migration/preflight",
            data={
                "csrf_token": csrf,
                "source": "/srv/oohstory/library",
                "destination": "/srv/oohstory/library-v2",
            },
        )
        assert migration_preflight.status_code == 200
        assert "384,787" in migration_preflight.text
        assert "迁移电子书库" in migration_preflight.text
        assert actions.calls[-3] == (
            "root_migration_preflight",
            {
                "source": "/srv/oohstory/library",
                "destination": "/srv/oohstory/library-v2",
            },
        )

        migration_start = client.post(
            "/admin/books/root-migration/start",
            data={
                "csrf_token": csrf,
                "plan_token": "d" * 32,
                "confirmation": "迁移电子书库",
            },
            follow_redirects=False,
        )
        assert migration_start.status_code == 303
        assert actions.calls[-1] == (
            "root_migration_start",
            {"plan_token": "d" * 32, "confirmation": "迁移电子书库"},
        )

        books = client.get("/admin/books/catalog?view=plot")
        assert books.status_code == 200
        assert "完整重建" in books.text
        assert "剧情问答与改编" in books.text

        sync = client.post(
            "/admin/books/sync-control",
            data={"csrf_token": csrf, "library_id": "fanqie", "enabled": "1"},
            follow_redirects=False,
        )
        assert sync.status_code == 303
        assert actions.calls[-1] == (
            "sync_control",
            {"library_id": "fanqie", "pipeline": "content", "enabled": True},
        )

        site_sync = client.post(
            "/admin/books/site-full-sync",
            data={
                "csrf_token": csrf,
                "site_id": "ixdzs",
                "enabled": "1",
                "books_per_cycle": "72",
            },
            follow_redirects=False,
        )
        assert site_sync.status_code == 303
        assert actions.calls[-1] == (
            "site_full_sync",
            {"site_id": "ixdzs", "enabled": True, "books_per_cycle": 72},
        )

        serialized_update = client.post(
            "/admin/books/serialized-update-sync",
            data={
                "csrf_token": csrf,
                "source_id": "ixdzs",
                "enabled": "1",
            },
            follow_redirects=False,
        )
        assert serialized_update.status_code == 303
        assert actions.calls[-1] == (
            "serialized_update_source",
            {"source_id": "ixdzs", "enabled": True},
        )

        redraw = client.post(
            "/admin/books/cover-redraw-control",
            data={
                "csrf_token": csrf,
                "target_per_hour": "50",
                "operation": "start",
            },
            follow_redirects=False,
        )
        assert redraw.status_code == 303
        assert actions.calls[-1] == (
            "cover_redraw_control",
            {"target_per_hour": 50, "enabled": True},
        )

        full = client.post(
            "/api/admin/books/indexes",
            headers={"x-csrf-token": csrf},
            json={"index_kind": "plot", "force": True},
        )
        assert full.status_code == 200
        assert actions.calls[-1] == (
            "index_refresh", {"index_kind": "plot", "force": True}
        )

        workbench = client.get("/admin/books/plot-workbench")
        assert workbench.status_code == 200
        assert "剧情证据问答" in workbench.text
        assert "/var/lib/oohstory-admin/library-project" in workbench.text
        assert "对标项目基调匹配" in workbench.text

        queried = client.post(
            "/admin/books/plot-query",
            data={"csrf_token": csrf, "question": "资源断供后如何逆转？", "limit": "8"},
        )
        assert queried.status_code == 200
        assert "轨道物流" in queried.text
        assert "生成剧情改编方案" in queried.text

        planned = client.post(
            "/admin/books/plot-plan",
            data={
                "csrf_token": csrf,
                "question": "资源断供后如何逆转？",
                "catalog_id": "42",
                "target_mode": "specified_chapter",
                "target_chapter": "12",
                "requirement": "保持工程逻辑",
            },
        )
        assert planned.status_code == 200
        assert "轨道补给反击" in planned.text
        assert "绑定后续写作" in planned.text

        previewed = client.post(
            "/admin/books/plot-preview",
            data={
                "csrf_token": csrf,
                "plan_id": "c" * 12,
                "apply_mode": "local_insert",
            },
        )
        assert previewed.status_code == 200
        assert "正文差异预览" in previewed.text
        assert "新增物流逆转段" in previewed.text

        applied = client.post(
            "/admin/books/plot-apply",
            data={"csrf_token": csrf, "plan_id": "c" * 12, "operation": "bind"},
        )
        assert applied.status_code == 200
        assert "方案已绑定到后续章节生成链路" in applied.text

        status_api = client.get("/api/admin/books/sync-controls")
        assert status_api.status_code == 200
        assert status_api.json()["local"]["content_enabled"] is True
        migration_status_api = client.get("/api/admin/books/root-migration/status")
        assert migration_status_api.status_code == 200
        assert migration_status_api.json()["data"]["current_root"] == "/srv/oohstory/library"
        sync_api = client.post(
            "/api/admin/books/sync-control",
            headers={"x-csrf-token": csrf},
            json={"library_id": "local", "enabled": False},
        )
        assert sync_api.status_code == 200
        site_sync_api = client.post(
            "/api/admin/books/site-full-sync",
            headers={"x-csrf-token": csrf},
            json={"site_id": "shubaow", "enabled": True, "books_per_cycle": 88},
        )
        assert site_sync_api.status_code == 200
        assert actions.calls[-1] == (
            "site_full_sync",
            {"site_id": "shubaow", "enabled": True, "books_per_cycle": 88},
        )
        serialized_update_api = client.post(
            "/api/admin/books/serialized-update-sync",
            headers={"x-csrf-token": csrf},
            json={"source_id": "ixdzs", "enabled": True},
        )
        assert serialized_update_api.status_code == 200
        assert actions.calls[-1] == (
            "serialized_update_source",
            {"source_id": "ixdzs", "enabled": True},
        )
        redraw_api = client.post(
            "/api/admin/books/cover-redraw-control",
            headers={"x-csrf-token": csrf},
            json={"target_per_hour": 50, "enabled": True},
        )
        assert redraw_api.status_code == 200
        query_api = client.post(
            "/api/admin/books/plot-query",
            headers={"x-csrf-token": csrf},
            json={"question": "基础设施如何逆转战局？", "limit": 6},
        )
        assert query_api.status_code == 200
        plan_api = client.post(
            "/api/admin/books/plot-adapt/plan",
            headers={"x-csrf-token": csrf},
            json={
                "question": "基础设施如何逆转战局？",
                "catalog_ids": [42],
                "target_mode": "ai_recommended",
            },
        )
        assert plan_api.status_code == 200
        preview_api = client.post(
            "/api/admin/books/plot-adapt/preview",
            headers={"x-csrf-token": csrf},
            json={"plan_id": "c" * 12, "apply_mode": "local_insert"},
        )
        assert preview_api.status_code == 200
        apply_api = client.post(
            "/api/admin/books/plot-adapt/apply",
            headers={"x-csrf-token": csrf},
            json={"plan_id": "c" * 12, "operation": "commit"},
        )
        assert apply_api.status_code == 200
        history_api = client.get("/api/admin/books/plot-adaptations")
        assert history_api.status_code == 200
        assert history_api.json()["project"]["title"] == "OOHStory 托管项目"
