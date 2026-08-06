from __future__ import annotations

import subprocess

from oohstory_admin.audit import AuditLog
from oohstory_admin.systemd import SystemdController
from conftest import login


def test_systemctl_uses_safe_argv_and_rejects_unknown_unit():
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "done\n", "")

    controller = SystemdController(runner=runner)
    result = controller.action("restart", "oohstory-reader.service")
    assert result.ok
    assert calls[0][0] == ["/usr/bin/systemctl", "--no-pager", "restart", "oohstory-reader.service"]
    assert calls[0][1]["shell"] is False
    try:
        controller.action("restart;reboot", "oohstory-reader.service")
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe action accepted")
    try:
        controller.action("restart", "oohstory-reader.service;reboot")
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe unit accepted")
    assert len(calls) == 1


def test_allowlist_rejection_is_audited(client):
    assert login(client).status_code == 303
    csrf = client.get("/api/admin/session").json()["csrf_token"]
    response = client.post(
        "/api/admin/pipeline/actions",
        json={"action": "restart", "target": "ssh.service"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 400
    entries = client.audit_log.list()
    assert entries[0]["target"] == "ssh.service"
    assert entries[0]["result"] == "rejected:not_allowlisted"
    assert client.fake_systemd.calls == []


def test_status_reports_unit_working_and_runtime_script_paths():
    output = "\n".join(
        (
            "Id=oohstory-library-index-refresh.service",
            "LoadState=loaded",
            "ActiveState=inactive",
            "SubState=dead",
            "UnitFileState=static",
            "MainPID=0",
            "MemoryCurrent=[not set]",
            "NRestarts=0",
            "FragmentPath=/etc/systemd/system/oohstory-library-index-refresh.service",
            "WorkingDirectory=/opt/oohstory-admin",
            "ExecStart={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 scripts/electronic-library/refresh_library_indexes.py ; ignore_errors=no ; }",
        )
    )

    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, output, "")

    status = SystemdController(runner=runner).status("oohstory-library-index-refresh.service")
    assert status["unit_path"] == "/etc/systemd/system/oohstory-library-index-refresh.service"
    assert status["working_directory"] == "/opt/oohstory-admin"
    assert status["runtime_path"] == (
        "/opt/oohstory-admin/"
        "scripts/electronic-library/refresh_library_indexes.py"
    )


def test_audit_log_is_durable_and_parameterized(tmp_path):
    path = tmp_path / "audit.db"
    first = AuditLog(path)
    first.initialize()
    first.record("operator", "start", "unit' OR 1=1 --", "success")
    second = AuditLog(path)
    rows = second.list()
    assert rows[0]["target"] == "unit' OR 1=1 --"
    assert rows[0]["actor"] == "operator"
