from __future__ import annotations

from contextlib import contextmanager

from app.mysql_catalog import MySQLPublicCatalog


class _Cursor:
    def __init__(self, row: dict[str, object]):
        self.row = row
        self.sql = ""
        self.params: tuple[object, ...] = ()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params: tuple[object, ...]) -> None:
        self.sql = sql
        self.params = params

    def fetchone(self) -> dict[str, object]:
        return self.row


class _Connection:
    def __init__(self, cursor: _Cursor):
        self._cursor = cursor

    def cursor(self) -> _Cursor:
        return self._cursor


class _Pool:
    def __init__(self, cursor: _Cursor):
        self._cursor = cursor

    @contextmanager
    def connection(self):
        yield _Connection(self._cursor)


def test_book_detail_prefers_active_source_url_and_honors_deleted_tombstone() -> None:
    cursor = _Cursor(
        {
            "catalog_id": 7,
            "source_name": "wenku8",
            "source_url": "https://www.wenku8.net/book/1.htm",
            "source_url_state": "active",
        }
    )
    catalog = object.__new__(MySQLPublicCatalog)
    catalog.pool = _Pool(cursor)

    result = catalog.get_book(b"opaque-public-id")

    assert result is not None
    assert result["source_url"] == "https://www.wenku8.net/book/1.htm"
    assert "LEFT JOIN authorized_source_updates su" in cursor.sql
    assert "su.source_url_state" in cursor.sql
    assert "THEN ''" in cursor.sql
    assert cursor.params == (b"opaque-public-id",)
