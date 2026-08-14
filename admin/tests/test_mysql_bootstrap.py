from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MYSQL_ROOT = ROOT / "deploy" / "mysql"


def _migrations() -> list[Path]:
    return sorted(MYSQL_ROOT.glob("[0-9][0-9][0-9]_*.sql"))


def test_generated_initializer_is_current() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(
                ROOT
                / "scripts"
                / "electronic-library"
                / "render_mysql_init_sql.py"
            ),
            "--check",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_initializer_covers_every_migration_with_checksum() -> None:
    initializer = (MYSQL_ROOT / "init.sql").read_text(encoding="utf-8")
    source_names = re.findall(
        r"^SOURCE deploy/mysql/([^;]+);$", initializer, re.MULTILINE
    )
    paths = _migrations()
    assert source_names == [path.name for path in paths]
    for path in paths:
        version = path.name.split("_", 1)[0]
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        assert f"VALUES ('{version}', '{checksum}', '{path.stem}');" in initializer


def test_bootstrap_contains_no_default_credentials() -> None:
    initializer = (MYSQL_ROOT / "init.sql").read_text(encoding="utf-8")
    runtime_users = (MYSQL_ROOT / "runtime-users.sql").read_text(
        encoding="utf-8"
    )
    assert "IDENTIFIED BY '" not in initializer
    assert "CREATE USER" not in initializer
    assert "IDENTIFIED BY '" not in runtime_users
    assert runtime_users.count("IDENTIFIED BY RANDOM PASSWORD") == 2
    assert "@'%' IDENTIFIED" not in runtime_users


def test_bootstrap_is_fresh_only_and_least_privilege() -> None:
    initializer = (MYSQL_ROOT / "init.sql").read_text(encoding="utf-8")
    assert "oohstory_assert_empty_schema" in initializer
    assert "OOHStory init requires a fresh empty database" in initializer
    assert "GRANT SELECT ON `oohstory_library`.*" in initializer
    assert (
        "GRANT SELECT, INSERT, UPDATE, DELETE, CREATE TEMPORARY TABLES"
        in initializer
    )
    assert "GRANT ALL" not in initializer
