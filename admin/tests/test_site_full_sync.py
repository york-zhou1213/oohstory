from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from oohstory_library.services.electronic_library import ElectronicLibraryService


SCRIPT = (
    Path(__file__).parents[1]
    / "scripts"
    / "electronic-library"
    / "site_full_sync.py"
)
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("oohstory_site_full_sync", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_site_commands_apply_validated_per_cycle_book_count():
    local = MODULE.build_command("txt80", 100)
    assert local[local.index("--limit-books") + 1] == "100"
    assert local[local.index("--workers") + 1] == "8"

    ixdzs = MODULE.build_command("ixdzs", 100)
    assert ixdzs[ixdzs.index("--sources") + 1] == "ixdzs"
    assert ixdzs[ixdzs.index("--download-limit") + 1] == "100"
    assert ixdzs[ixdzs.index("--ixdzs-workers") + 1] == "8"

    smaller = MODULE.build_command("ixdzs", 37)
    assert smaller[smaller.index("--download-limit") + 1] == "37"
    shubaow = MODULE.build_command("shubaow", 37)
    assert shubaow[shubaow.index("--download-limit") + 1] == "1"
    assert shubaow[shubaow.index("--shubaow-workers") + 1] == "1"
    with pytest.raises(ValueError, match="1–500"):
        MODULE.build_command("ixdzs", 501)


def test_authorized_sites_use_transport_lanes_and_local_has_its_own():
    assert MODULE.slot_path("xbiquge") != MODULE.slot_path("ixdzs")
    assert MODULE.slot_path("shubaow") == MODULE.slot_path("linovelib")
    assert MODULE.slot_path("txt80") != MODULE.slot_path("ixdzs")
    assert MODULE.slot_lane("xbiquge") == "http-xbiquge"
    assert MODULE.slot_lane("ixdzs") == "http-ixdzs"
    assert MODULE.slot_lane("shubaow") == "browser-shubaow"
    assert set(MODULE.SITE_LABELS) == {
        "txt80", "xbiquge", "ixdzs", "shubaow", "linovelib"
    }


def test_systemd_template_uses_allowlisted_site_and_runtime_config():
    unit = (
        Path(__file__).parents[1]
        / "deploy"
        / "systemd"
        / "oohstory-library-site-full-sync@.service"
    ).read_text(encoding="utf-8")
    assert "site_full_sync.py --site %i" in unit
    assert "--target-books-per-minute" not in unit
    assert "EnvironmentFile=/etc/oohstory-admin/library.env" in unit
    assert "Restart=on-failure" in unit


def test_service_starts_only_allowlisted_site_unit(tmp_path):
    service = ElectronicLibraryService(
        tmp_path / "electronic-library",
        tmp_path / "runtime",
    )
    calls = []

    def fake_systemctl(*args, check=True):
        calls.append((args, check))
        if args[0] == "show":
            units = [unit for unit in args[1:] if not unit.startswith("--property=")]
            return SimpleNamespace(
                returncode=0,
                stdout="\n\n".join(
                    f"Id={unit}\nUnitFileState=disabled\nActiveState=inactive\n"
                    "NextElapseUSecRealtime=\nLastTriggerUSec="
                    for unit in units
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch.object(service, "_run_systemctl", side_effect=fake_systemctl):
        controls = service.set_site_full_sync(
            "ixdzs",
            True,
            books_per_cycle=37,
        )
    assert (
        (
            "enable", "--now",
            "oohstory-library-site-full-sync@ixdzs.service",
        ),
        True,
    ) in calls
    site = next(
        item for item in controls["site_full_sync"]["sites"]
        if item["id"] == "ixdzs"
    )
    assert site["books_per_cycle"] == 37
    assert site["slot_lane"] == "http-ixdzs"
    assert controls["site_full_sync"]["authorized_sites_share_slot"] is False
    assert controls["site_full_sync"]["execution_lanes"]["xbiquge"] == "http-xbiquge"
    saved = service.runtime_controls.path.read_text(encoding="utf-8")
    assert '"ixdzs": 37' in saved
    with pytest.raises(ValueError, match="未知正文同步站点"):
        service.set_site_full_sync("../../shell", True)


def test_serialized_update_source_uses_oohstory_template_units(tmp_path):
    service = ElectronicLibraryService(
        tmp_path / "electronic-library",
        tmp_path / "runtime",
    )
    calls = []

    def fake_systemctl(*args, check=True):
        calls.append((args, check))
        if args[0] == "show":
            units = [unit for unit in args[1:] if not unit.startswith("--property=")]
            return SimpleNamespace(
                returncode=0,
                stdout="\n\n".join(
                    f"Id={unit}\nUnitFileState=enabled\nActiveState=active\n"
                    "NextElapseUSecRealtime=\nLastTriggerUSec="
                    for unit in units
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch.object(service, "_run_systemctl", side_effect=fake_systemctl):
        controls = service.set_serialized_update_source("ixdzs", True)

    assert (
        (
            "enable", "--now",
            "oohstory-library-serialized-update-sync@ixdzs.timer",
        ),
        True,
    ) in calls
    assert (
        (
            "start", "--no-block",
            "oohstory-library-serialized-update-sync@ixdzs.service",
        ),
        True,
    ) in calls
    source = next(
        item for item in controls["serialized_update"]["sources"]
        if item["id"] == "ixdzs"
    )
    assert source["lane"] == "update-ixdzs"
    assert all(
        "webnovel" not in argument
        for call, _ in calls
        for argument in call
    )
    with pytest.raises(ValueError, match="未知连载追更来源"):
        service.set_serialized_update_source("../../shell", True)


def test_each_cycle_reads_latest_atomic_site_count(tmp_path):
    controls = MODULE.OOHStoryRuntimeControls(tmp_path)
    controls.update_site("txt80", 21)
    controls.update_site("linovelib", 48)

    with patch.object(MODULE, "STATE_ROOT", tmp_path):
        assert MODULE.configured_books_per_cycle("txt80", 100) == 21
        assert MODULE.configured_books_per_cycle("linovelib", 100) == 48


def test_cover_redraw_control_uses_three_oohstory_workers_for_fifty_per_hour(
    tmp_path,
):
    service = ElectronicLibraryService(
        tmp_path / "electronic-library",
        tmp_path / "runtime",
    )
    calls = []

    def fake_systemctl(*args, check=True):
        calls.append((args, check))
        if args[0] == "show":
            units = [unit for unit in args[1:] if not unit.startswith("--property=")]
            return SimpleNamespace(
                returncode=0,
                stdout="\n\n".join(
                    f"Id={unit}\nUnitFileState=disabled\nActiveState=inactive\n"
                    "NextElapseUSecRealtime=\nLastTriggerUSec="
                    for unit in units
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch.object(service, "_run_systemctl", side_effect=fake_systemctl):
        controls = service.set_cover_redraw_control(50, True)

    assert (
        (
            "enable",
            "--now",
            "oohstory-clean-cover-worker@1.service",
            "oohstory-clean-cover-worker@2.service",
            "oohstory-clean-cover-worker@3.service",
        ),
        True,
    ) in calls
    assert all(
        "webnovel" not in argument
        for call, _ in calls
        for argument in call
    )
    assert controls["cover_redraw"]["target_per_hour"] == 50
    assert controls["cover_redraw"]["configured_workers"] == 3
