from __future__ import annotations

import os
import subprocess

import pytest

from app.audiobook_cast_review_worker import CLAIM_SQL


@pytest.mark.skipif(
    os.getenv("OOHSTORY_RUN_MYSQL_INTEGRATION") != "1",
    reason="requires the production-compatible MySQL schema",
)
def test_cast_review_claim_sql_executes_in_real_mysql() -> None:
    result = subprocess.run(
        ["mysql", "--batch", "--skip-column-names", "oohstory_library"],
        input=f"START TRANSACTION; {CLAIM_SQL}; ROLLBACK;\n".encode(),
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


@pytest.mark.skipif(
    os.getenv("OOHSTORY_RUN_MYSQL_INTEGRATION") != "1",
    reason="requires the production-compatible MySQL schema",
)
def test_published_cast_snapshot_schema_and_read_query_exist() -> None:
    sql = (
        "SELECT catalog_id,revision,JSON_LENGTH(cast_json) "
        "FROM audiobook_cast_snapshots WHERE engine_version='oohstory-cast-v15-colonfield1' "
        "ORDER BY catalog_id LIMIT 1;"
    )
    result = subprocess.run(
        ["mysql", "--batch", "--skip-column-names", "oohstory_library"],
        input=sql.encode(), capture_output=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
