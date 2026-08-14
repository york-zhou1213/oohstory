from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_oohstory_runtime_has_no_webnovel_service_or_project_paths() -> None:
    forbidden = (
        "webnovel-library-",
        "webnovel-shubaow-browser.service",
        "/opt/webnovel-writer",
        "/etc/webnovel-writer",
    )
    files = [
        ROOT / "ops" / "oohstory-admin-library-action-runner",
        ROOT / "ops" / "oohstory-admin-systemctl",
        ROOT / "src" / "oohstory_admin" / "units.py",
        ROOT / "src" / "oohstory_admin" / "script_store.py",
        ROOT / "src" / "oohstory_admin" / "library_status.py",
        ROOT / "src" / "oohstory_admin" / "app.py",
        ROOT / "src" / "oohstory_library" / "services" / "electronic_library.py",
        ROOT / "src" / "oohstory_library" / "services" / "browser_recovery.py",
        ROOT / "src" / "oohstory_library" / "services" / "unit_names.py",
    ]
    files.extend((ROOT / "deploy" / "systemd").glob("oohstory-*.service"))
    offenders: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                offenders.append(f"{path.relative_to(ROOT)}:{token}")
    assert offenders == []


def test_oohstory_browser_service_owns_profile_and_cdp_port() -> None:
    unit = (
        ROOT / "deploy" / "systemd" / "oohstory-shubaow-browser.service"
    ).read_text(encoding="utf-8")
    assert "--remote-debugging-port=9223" in unit
    assert "--user-data-dir=/var/lib/oohstory-admin/shubaow-browser-profile" in unit
    assert "webnovel" not in unit.casefold()

    dependent_units = (
        "oohstory-library-authorized-catalog-sync.service",
        "oohstory-library-authorized-source-recovery.service",
        "oohstory-library-serialized-update-sync.service",
        "oohstory-library-shubaow-cover-sync.service",
        "oohstory-library-linovelib-cover-sync.service",
    )
    for name in dependent_units:
        text = (ROOT / "deploy" / "systemd" / name).read_text(encoding="utf-8")
        assert "oohstory-shubaow-browser.service" in text
        assert "webnovel-shubaow-browser.service" not in text
        assert "9222" not in text
