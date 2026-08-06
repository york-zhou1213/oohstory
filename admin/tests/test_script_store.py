from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import FakeLibrary, FakeReader, FakeSystemd, login
from oohstory_admin.app import create_app
from oohstory_admin.audit import AuditLog
from oohstory_admin.script_store import (
    ScriptConflictError,
    ScriptNotFoundError,
    ScriptStore,
    ScriptValidationError,
)


SCRIPT_RELATIVE = "scripts/electronic-library/refresh_library_indexes.py"
SCRIPT_ID = "index_refresh"


def make_project(tmp_path: Path, content: str = "#!/usr/bin/env python3\nprint('ok')\n") -> Path:
    target = tmp_path / SCRIPT_RELATIVE
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    target.chmod(0o644)
    return tmp_path


def test_store_reads_and_atomically_saves_allowlisted_script(tmp_path):
    root = make_project(tmp_path)
    store = ScriptStore(root)
    before = store.read(SCRIPT_ID)
    replacement = "#!/usr/bin/env python3\nprint('changed')\n"

    result = store.save(SCRIPT_ID, replacement, before["sha256"])

    assert result.old_sha256 == before["sha256"]
    assert result.new_sha256 == hashlib.sha256(replacement.encode()).hexdigest()
    assert store.read(SCRIPT_ID)["content"] == replacement


def test_store_rejects_unknown_stale_invalid_and_symlink(tmp_path):
    root = make_project(tmp_path)
    store = ScriptStore(root)
    record = store.read(SCRIPT_ID)

    with pytest.raises(ScriptNotFoundError):
        store.read("../../etc/passwd")
    with pytest.raises(ScriptConflictError):
        store.save(SCRIPT_ID, record["content"], "0" * 64)
    with pytest.raises(ScriptValidationError):
        store.save(SCRIPT_ID, "#!/usr/bin/env python3\nif:\n", record["sha256"])

    target = root / SCRIPT_RELATIVE
    real = target.with_suffix(".real.py")
    target.rename(real)
    target.symlink_to(real.name)
    with pytest.raises(ScriptValidationError):
        store.read(SCRIPT_ID)


def test_sudo_helper_invocation_uses_fixed_id_and_stdin(tmp_path):
    root = make_project(tmp_path)
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        payload = {
            "old_sha256": kwargs["input"].hex()[:64].ljust(64, "0"),
            "new_sha256": hashlib.sha256(kwargs["input"]).hexdigest(),
            "backup": "/var/lib/oohstory-admin/script-backups/test.py",
        }
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    store = ScriptStore(root, use_sudo_helper=True, runner=runner)
    record = store.read(SCRIPT_ID)
    store.save(SCRIPT_ID, record["content"], record["sha256"])

    assert calls[0][0] == [
        "/usr/bin/sudo",
        "-n",
        "/usr/local/libexec/oohstory-admin-script-store",
        "write",
        SCRIPT_ID,
        record["sha256"],
    ]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["input"] == record["content"].encode()


def test_authenticated_editor_csrf_validation_conflict_and_audit(tmp_path, settings):
    root = make_project(
        tmp_path / "project",
        "#!/usr/bin/env python3\n# </textarea><script>alert(1)</script>\nprint('ok')\n",
    )
    local_settings = replace(
        settings,
        managed_script_root=root,
        database_path=tmp_path / "audit.db",
    )
    audit = AuditLog(local_settings.database_path)
    app = create_app(
        local_settings,
        reader=FakeReader(),
        library=FakeLibrary(),
        systemd=FakeSystemd(),
        audit=audit,
    )
    with TestClient(app) as client:
        assert login(client).status_code == 303
        page = client.get(f"/admin/pipeline/scripts/{SCRIPT_ID}")
        assert page.status_code == 200
        assert "&lt;/textarea&gt;&lt;script&gt;" in page.text
        assert "</textarea><script>alert(1)</script>" not in page.text
        csrf = client.get("/api/admin/session").json()["csrf_token"]
        sha = ScriptStore(root).read(SCRIPT_ID)["sha256"]

        missing_csrf = client.post(
            f"/admin/pipeline/scripts/{SCRIPT_ID}",
            data={"expected_sha256": sha, "content": "#!/usr/bin/env python3\nprint(1)\n"},
        )
        assert missing_csrf.status_code == 403

        invalid = client.post(
            f"/admin/pipeline/scripts/{SCRIPT_ID}",
            data={"csrf_token": csrf, "expected_sha256": sha, "content": "#!/usr/bin/env python3\nif:\n"},
        )
        assert invalid.status_code == 400

        replacement = "#!/usr/bin/env python3\nprint('saved')\n"
        saved = client.post(
            f"/admin/pipeline/scripts/{SCRIPT_ID}",
            data={"csrf_token": csrf, "expected_sha256": sha, "content": replacement},
            follow_redirects=False,
        )
        assert saved.status_code == 303
        assert (root / SCRIPT_RELATIVE).read_text() == replacement

        stale = client.post(
            f"/admin/pipeline/scripts/{SCRIPT_ID}",
            data={"csrf_token": csrf, "expected_sha256": sha, "content": replacement},
        )
        assert stale.status_code == 409
        assert client.get("/admin/pipeline/scripts/not-allowed").status_code == 404

    rows = audit.list()
    assert any(row["result"].startswith("success:") for row in rows)
    assert all("print('saved')" not in row["result"] for row in rows)
