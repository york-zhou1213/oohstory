from __future__ import annotations

import ast
import importlib
import importlib.util
import os
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = ROOT / "src" / "oohstory_library" / "services"
SCRIPT_ROOT = ROOT / "scripts" / "electronic-library"


def test_library_entry_points_import_from_oohstory_package() -> None:
    modules = (
        "electronic_library",
        "library_task_runners",
        "ai_service",
        "library_download_queue",
        "local_source_upgrade",
    )
    for module in modules:
        imported = importlib.import_module(
            f"oohstory_library.services.{module}"
        )
        assert Path(imported.__file__).is_relative_to(ROOT / "src")

    library = importlib.import_module(
        "oohstory_library.services.electronic_library"
    )
    assert library.APP_ROOT == ROOT
    assert library.DEFAULT_LIBRARY_ROOT == ROOT / "electronic-library"
    assert library.WORKER_PATH.is_file()
    assert library.BATCH_WORKER_PATH.is_file()


def test_scripts_resolve_oohstory_root_and_owned_service_modules(monkeypatch) -> None:
    path = SCRIPT_ROOT / "project_paths.py"
    spec = importlib.util.spec_from_file_location("oohstory_project_paths", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.APP_ROOT == ROOT

    # Scripts may read configuration before importing the engine package.
    # project_paths must therefore install the OOHStory-first compatibility
    # aliases on its own.
    monkeypatch.setenv("OOHSTORY_LIBRARY_RUNTIME_DIR", "/tmp/ooh-runtime-test")
    module.install_legacy_environment_aliases()
    assert os.environ["WEBNOVEL_LIBRARY_RUNTIME_DIR"] == "/tmp/ooh-runtime-test"

    missing: list[str] = []
    for script in sorted(SCRIPT_ROOT.glob("*.py")):
        tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            prefix = "oohstory_library.services."
            if not node.module.startswith(prefix):
                continue
            service = node.module.removeprefix(prefix).split(".", 1)[0]
            if not (SERVICE_ROOT / f"{service}.py").is_file():
                missing.append(f"{script.name}:{service}")
    assert missing == []


def test_vendor_tree_has_no_legacy_service_imports() -> None:
    legacy_import = re.compile(r"(?<!oohstory_library\.)\bservices\.")
    offenders: list[str] = []
    for root in (
        SERVICE_ROOT,
        SCRIPT_ROOT,
        ROOT / "deploy" / "mysql",
        ROOT / "deploy" / "systemd",
    ):
        for path in sorted(root.iterdir()):
            if not path.is_file() or path.suffix not in {
                ".py",
                ".mjs",
                ".md",
                ".sql",
                ".cnf",
                ".example",
                ".service",
                ".timer",
                ".conf",
            }:
                continue
            text = path.read_text(encoding="utf-8")
            if legacy_import.search(text):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_oohstory_unit_names_reject_cross_project_units() -> None:
    unit_names = importlib.import_module(
        "oohstory_library.services.unit_names"
    )
    assert unit_names.library_unit_name(
        "oohstory-library-sync.service"
    ) == "oohstory-library-sync.service"
    with pytest.raises(ValueError, match="only manage oohstory"):
        unit_names.library_unit_name("webnovel-library-sync.service")


def test_oohstory_infrastructure_environment_has_priority(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database = importlib.import_module(
        "oohstory_library.services.library_database"
    )
    password_file = tmp_path / "mysql-password"
    password_file.write_text("ooh-secret\n", encoding="utf-8")
    monkeypatch.setenv("WEBNOVEL_MYSQL_HOST", "legacy.invalid")
    monkeypatch.setenv("WEBNOVEL_MYSQL_PASSWORD", "legacy-secret")
    monkeypatch.setenv("OOHSTORY_LIBRARY_MYSQL_HOST", "127.0.0.9")
    monkeypatch.setenv(
        "OOHSTORY_LIBRARY_MYSQL_PASSWORD_FILE",
        str(password_file),
    )
    monkeypatch.setenv("OOHSTORY_LIBRARY_OBJECT_ROOT", str(tmp_path))
    settings = database.LibraryInfrastructureSettings.from_env()
    assert settings.mysql_host == "127.0.0.9"
    assert settings.mysql_password == "ooh-secret"
    assert settings.object_root == tmp_path
