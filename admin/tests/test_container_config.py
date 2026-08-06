from __future__ import annotations

import pytest

from oohstory_admin.config import Settings


def test_reader_container_host_requires_explicit_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("OOHSTORY_ADMIN_READER_URL", "http://reader:8091")
    with pytest.raises(ValueError, match="allowed internal host"):
        Settings.from_env()

    monkeypatch.setenv("OOHSTORY_ADMIN_READER_ALLOWED_HOSTS", "reader")
    assert Settings.from_env().reader_url == "http://reader:8091"


def test_reader_container_allowlist_rejects_url_syntax(monkeypatch) -> None:
    monkeypatch.setenv("OOHSTORY_ADMIN_READER_ALLOWED_HOSTS", "reader/path")
    with pytest.raises(ValueError, match="READER_ALLOWED_HOSTS"):
        Settings.from_env()
