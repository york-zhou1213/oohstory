from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import login
from oohstory_admin.app import create_app
from oohstory_admin.maintenance import MaintenanceController, MaintenanceError


ROOT = Path(__file__).resolve().parents[1]


class FakeMaintenance:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self.calls: list[bool] = []

    def status(self) -> dict[str, object]:
        return {
            "available": True,
            "enabled": self.enabled,
            "changed_at": "2026-08-08 11:45:00" if self.enabled else "",
            "error": "",
        }

    def set_enabled(self, enabled: bool) -> dict[str, object]:
        self.calls.append(enabled)
        self.enabled = enabled
        return self.status()


def _csrf(page: str) -> str:
    marker = 'name="csrf_token" value="'
    return page.split(marker, 1)[1].split('"', 1)[0]


def test_maintenance_admin_page_and_one_click_switch(settings, components) -> None:
    reader, library, systemd, audit = components
    maintenance = FakeMaintenance()
    app = create_app(
        settings,
        reader=reader,
        library=library,
        systemd=systemd,
        audit=audit,
        maintenance=maintenance,
    )
    with TestClient(app) as client:
        assert login(client).status_code == 303
        page = client.get("/admin/maintenance")

        assert page.status_code == 200
        assert "一键切换面向读者的全静态维护页面" in page.text
        assert "站点正常开放" in page.text
        assert "开启维护页面" in page.text

        response = client.post(
            "/admin/maintenance",
            data={"csrf_token": _csrf(page.text), "action": "enable"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/admin/maintenance?result=enabled"
        assert maintenance.calls == [True]

        enabled_page = client.get("/admin/maintenance")
        assert "维护页生效中" in enabled_page.text
        assert "恢复正常访问" in enabled_page.text

        response = client.post(
            "/admin/maintenance",
            data={"csrf_token": _csrf(enabled_page.text), "action": "disable"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert maintenance.calls == [True, False]

    actions = [entry["action"] for entry in audit.list(20, 0)]
    assert "maintenance_enable" in actions
    assert "maintenance_disable" in actions


def test_maintenance_switch_requires_csrf(settings, components) -> None:
    reader, library, systemd, audit = components
    maintenance = FakeMaintenance()
    app = create_app(
        settings,
        reader=reader,
        library=library,
        systemd=systemd,
        audit=audit,
        maintenance=maintenance,
    )
    with TestClient(app) as client:
        assert login(client).status_code == 303
        response = client.post(
            "/admin/maintenance",
            data={"csrf_token": "wrong", "action": "enable"},
        )
    assert response.status_code == 403
    assert maintenance.calls == []


def test_maintenance_controller_uses_fixed_sudo_helper() -> None:
    calls: list[list[str]] = []

    def runner(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout='{"enabled":true,"changed_at":"2026-08-08 11:45:00"}\n',
            stderr="",
        )

    controller = MaintenanceController(
        "/usr/local/libexec/oohstory-admin-maintenance",
        use_sudo_helper=True,
        runner=runner,
    )

    assert controller.status()["enabled"] is True
    assert controller.set_enabled(False)["enabled"] is True
    assert calls == [
        [
            "/usr/bin/sudo",
            "-n",
            "/usr/local/libexec/oohstory-admin-maintenance",
            "status",
        ],
        [
            "/usr/bin/sudo",
            "-n",
            "/usr/local/libexec/oohstory-admin-maintenance",
            "disable",
        ],
    ]


def test_maintenance_controller_rejects_unknown_action() -> None:
    controller = MaintenanceController("/fixed/helper")
    try:
        controller._argv("restart")
    except MaintenanceError as exc:
        assert "不在允许列表" in str(exc)
    else:
        raise AssertionError("unknown action was accepted")


def test_maintenance_deployment_and_design_contracts() -> None:
    helper = (ROOT / "ops" / "oohstory-admin-maintenance").read_text(encoding="utf-8")
    sudoers = (ROOT / "ops" / "oohstory-admin.sudoers").read_text(encoding="utf-8")
    service = (ROOT / "deploy" / "oohstory-admin.service").read_text(encoding="utf-8")
    template = (ROOT / "src" / "oohstory_admin" / "templates" / "maintenance.html").read_text(encoding="utf-8")
    styles = (ROOT / "src" / "oohstory_admin" / "static" / "admin.css").read_text(encoding="utf-8")

    assert "status|enable|disable" not in helper
    assert 'case "$action"' in helper
    assert "action is not allowlisted" in helper
    assert "/var/lib/oohstory-reader/maintenance.enabled" in helper
    for action in ("status", "enable", "disable"):
        assert f"oohstory-admin-maintenance {action}" in sudoers
    assert "OOHSTORY_ADMIN_MAINTENANCE_HELPER_PATH" in service
    assert "maintenance-console" in template
    assert "维护页生效中" in template
    assert ".maintenance-console" in styles
    assert "@media (max-width: 600px)" in styles
