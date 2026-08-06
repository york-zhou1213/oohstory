from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from unittest.mock import patch

from oohstory_library.services.library_runtime_mysql import MySQLLibraryRuntime


SCRIPT = (
    Path(__file__).parents[1]
    / "scripts"
    / "electronic-library"
    / "clean_cover_worker.py"
)
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("oohstory_clean_cover_worker", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class StopWorker(RuntimeError):
    pass


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeRuntime:
    def __init__(self) -> None:
        self.recovery_times: list[float] = []
        self.claim_worker_ids: list[str] = []
        self.claims = 0
        self.clock: FakeClock | None = None

    def worker_id(self, kind: str) -> str:
        assert kind == "clean-cover"
        return "clean-cover:test-worker"

    def recover_clean_cover_jobs(self) -> int:
        assert self.clock is not None
        self.recovery_times.append(self.clock.monotonic())
        return 0

    def claim_clean_cover_job(
        self,
        *,
        worker_id: str,
        max_attempts: int,
    ) -> None:
        assert max_attempts == MODULE.MAX_ATTEMPTS
        self.claim_worker_ids.append(worker_id)
        self.claims += 1
        if self.claims == 4:
            raise StopWorker
        return None


def test_mysql_worker_recovers_expired_leases_periodically_while_idle() -> None:
    clock = FakeClock()
    runtime = FakeRuntime()
    runtime.clock = clock

    with pytest.raises(StopWorker):
        MODULE.run_mysql(
            {},
            runtime=runtime,
            recovery_interval_seconds=60,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            enabled_check=lambda: True,
        )

    assert runtime.recovery_times == [0.0, 60.0]
    assert runtime.claim_worker_ids == ["clean-cover:test-worker"] * 4


def test_mysql_worker_ids_are_unique_per_process_runtime() -> None:
    first = MySQLLibraryRuntime.worker_id("clean-cover")
    second = MySQLLibraryRuntime.worker_id("clean-cover")

    assert first.startswith("clean-cover:")
    assert second.startswith("clean-cover:")
    assert first != second


def test_systemd_template_scales_with_independent_worker_processes() -> None:
    unit = (
        Path(__file__).parents[1]
        / "deploy"
        / "systemd"
        / "oohstory-clean-cover-worker@.service"
    ).read_text(encoding="utf-8")

    assert "instance %i" in unit
    assert "clean_cover_worker.py" in unit
    assert "Environment=OOHSTORY_LIBRARY_CATALOG_BACKEND=mysql" in unit
    assert "Environment=OOHSTORY_COVER_WORKER_SLOT=%i" in unit
    assert "ExecStart=" in unit


def test_worker_slot_reads_latest_runtime_target(monkeypatch) -> None:
    class FakeControls:
        def __init__(self, _runtime_dir):
            pass

        def read(self):
            return {"cover_redraw": {"target_per_hour": 50}}

    monkeypatch.setenv("OOHSTORY_COVER_WORKER_SLOT", "3")
    with patch.object(MODULE, "OOHStoryRuntimeControls", FakeControls):
        assert MODULE.worker_enabled_by_runtime_config() is True

    monkeypatch.setenv("OOHSTORY_COVER_WORKER_SLOT", "4")
    with patch.object(MODULE, "OOHStoryRuntimeControls", FakeControls):
        assert MODULE.worker_enabled_by_runtime_config() is False
