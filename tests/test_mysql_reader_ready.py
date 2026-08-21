from __future__ import annotations

from contextlib import contextmanager

from app.mysql_catalog import MySQLPublicCatalog


class _Cursor:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, sql: str, params=()) -> None:
        self.executions.append((sql, tuple(params or ())))

    def fetchall(self):
        if len(self.executions) == 1:
            return [{"category": "科幻灵异", "cat_total": 100}]
        return []


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _Cursor:
        return self._cursor


class _Pool:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    @contextmanager
    def connection(self):
        yield _Connection(self._cursor)


def test_category_showcase_selects_only_reader_ready_books() -> None:
    cursor = _Cursor()
    catalog = object.__new__(MySQLPublicCatalog)
    catalog.pool = _Pool(cursor)

    result = catalog.category_books(
        10,
        max_source_bytes=128 * 1024 * 1024,
        max_index_source_bytes=32 * 1024 * 1024,
        reader_index_schema_min=6,
        reader_index_schema_max=7,
        max_chapter_count=10_000,
    )

    assert result == {"科幻灵异": []}
    assert len(cursor.executions) == 2
    category_sql, category_params = cursor.executions[0]
    normalized_category_sql = " ".join(category_sql.split())
    assert "FROM public_catalog_facets" in normalized_category_sql
    assert category_params == ()
    books_sql, books_params = cursor.executions[1]
    normalized_sql = " ".join(books_sql.split())
    assert "idx_books_reader_ready_category_words" in normalized_sql
    assert "b.bytes BETWEEN 1 AND %s" in normalized_sql
    assert "b.bytes <= %s OR EXISTS" in normalized_sql
    assert "reader_meta.reader_source_bytes=b.bytes" in normalized_sql
    assert "reader_meta.reader_schema_version BETWEEN %s AND %s" in normalized_sql
    assert "reader_meta.section_count BETWEEN 1 AND %s" in normalized_sql
    assert "IN ('exact','fallback')" in normalized_sql
    assert "invalid_meta.section_count>%s" in normalized_sql
    assert books_params == (
        "科幻灵异",
        128 * 1024 * 1024,
        32 * 1024 * 1024,
        6,
        7,
        10_000,
        6,
        7,
        10_000,
        40,
    )
