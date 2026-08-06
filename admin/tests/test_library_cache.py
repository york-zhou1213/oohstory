from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from oohstory_admin.library_catalog import GlobalDeconstructionReader
from oohstory_library.services.library_cache import (
    LibraryCacheSettings,
    RedisHotCache,
)
from oohstory_library.services.library_catalog_mysql import MySQLCatalogStore
from oohstory_library.services.library_download_queue import LibraryDownloadQueue


class FakeRedis:
    def __init__(self):
        self.values: dict[str, bytes | int] = {}
        self.set_calls: list[tuple[str, int, bytes]] = []
        self.fail = False

    def get(self, key):
        if self.fail:
            raise ConnectionError("redis unavailable")
        return self.values.get(key)

    def setex(self, key, ttl, value):
        if self.fail:
            raise ConnectionError("redis unavailable")
        self.values[key] = value
        self.set_calls.append((key, ttl, value))

    def incr(self, key):
        if self.fail:
            raise ConnectionError("redis unavailable")
        value = int(self.values.get(key) or 0) + 1
        self.values[key] = value
        return value

    def ping(self):
        if self.fail:
            raise ConnectionError("redis unavailable")
        return True

    def info(self, section):
        assert section == "memory"
        return {
            "used_memory": 1024,
            "maxmemory": 2 * 1024**3,
            "maxmemory_policy": "allkeys-lfu",
        }

    def config_get(self, _name):
        return {"maxmemory-policy": "allkeys-lfu"}


def cache(client: FakeRedis | None = None, **overrides) -> RedisHotCache:
    options = {
        "enabled": True,
        "prefix": "test-cache:",
        "warm_workers": 1,
        "warm_queue_size": 1,
    }
    options.update(overrides)
    return RedisHotCache(LibraryCacheSettings(**options), client=client or FakeRedis())


def test_hit_miss_corruption_and_unavailable_are_fail_open():
    redis = FakeRedis()
    hot = cache(redis)
    query = {"q": "  星   海 ", "tags": {"b", "a"}}

    assert hot.get_json("catalog", "browse", query) is None
    assert hot.set_json("catalog", "browse", query, {"items": [1]}, ttl_seconds=30)
    assert hot.get_json("catalog", "browse", {"tags": {"a", "b"}, "q": "星 海"}) == {
        "items": [1]
    }

    redis.values[hot.key_for("catalog", "browse", query)] = b"{corrupt"
    assert hot.get_json("catalog", "browse", query) is None
    redis.fail = True
    assert hot.get_json("catalog", "browse", query) is None
    stats = hot.stats()
    assert stats["hits"] == 1
    assert stats["misses"] >= 3
    assert stats["errors"] >= 2


def test_epoch_invalidation_changes_namespace_without_scanning_keys():
    redis = FakeRedis()
    hot = cache(redis)
    query = {"page": 1}
    before = hot.key_for("tone", "facets", query)
    assert hot.invalidate("tone") == {"tone": 1}
    after = hot.key_for("tone", "facets", query)
    assert before != after
    assert ":tone:0:" in before
    assert ":tone:1:" in after


def test_sensitive_or_oversized_content_is_never_cached():
    redis = FakeRedis()
    hot = cache(redis, max_payload_bytes=16 * 1024)
    assert not hot.set_json(
        "book", "detail", {"id": 1}, {"password": "secret"}, ttl_seconds=30
    )
    assert not hot.set_json(
        "book", "detail", {"id": 1}, {"chapters": ["body"]}, ttl_seconds=30
    )
    assert not hot.set_json(
        "plot", "search", {"q": "x"}, {"content": "x" * 40_000}, ttl_seconds=30
    )
    assert redis.set_calls == []


def test_plot_result_cache_accepts_small_pages_and_rejects_large_pages():
    redis = FakeRedis()
    hot = cache(redis, max_payload_bytes=16 * 1024)
    query = {"terms": ["星海"], "motif_tags": ["启航"], "limit": 20}
    small = [{"catalog_id": 1, "location": "第一章", "content": "证据"}]
    assert hot.set_json(
        "plot", "normalized-search", query, small, ttl_seconds=60
    )
    assert hot.get_json(
        "plot", "normalized-search", query, expected_type=list
    ) == small
    assert not hot.set_json(
        "plot",
        "normalized-search",
        {**query, "limit": 100},
        [{"content": "证据" * 20_000}],
        ttl_seconds=60,
    )


def test_bounded_coalesced_warm_does_not_block_writer():
    hot = cache()
    started = threading.Event()
    release = threading.Event()

    def load():
        started.set()
        release.wait(1)
        return {"value": 1}

    before = time.monotonic()
    assert hot.schedule_warm("book", "detail", {"id": 1}, load, ttl_seconds=30)
    assert time.monotonic() - before < 0.1
    assert started.wait(0.5)
    assert hot.schedule_warm("book", "detail", {"id": 1}, load, ttl_seconds=30)
    assert not hot.schedule_warm("book", "detail", {"id": 2}, load, ttl_seconds=30)
    assert hot.stats()["warm_queue_depth"] == 2
    release.set()


def test_inflight_warm_cannot_publish_stale_value_into_new_generation():
    redis = FakeRedis()
    hot = cache(redis)
    started = threading.Event()
    release = threading.Event()
    query = {"id": 9}

    def load():
        started.set()
        release.wait(1)
        return {"value": "before-write"}

    assert hot.schedule_warm("book", "detail", query, load, ttl_seconds=30)
    assert started.wait(0.5)
    assert hot.invalidate("book") == {"book": 1}
    release.set()
    deadline = time.monotonic() + 1
    while hot.stats()["warm_queue_depth"] and time.monotonic() < deadline:
        time.sleep(0.01)

    assert hot.get_json("book", "detail", query) is None
    assert any(":book:0:" in key for key, _ttl, _value in redis.set_calls)
    assert not any(":book:1:" in key for key, _ttl, _value in redis.set_calls)


def test_update_during_warm_coalesces_and_refills_latest_generation():
    redis = FakeRedis()
    hot = cache(redis)
    started = threading.Event()
    release = threading.Event()
    query = {"id": 10}

    def stale_load():
        started.set()
        release.wait(1)
        return {"value": "stale"}

    assert hot.schedule_warm("book", "detail", query, stale_load, ttl_seconds=30)
    assert started.wait(0.5)
    assert hot.invalidate("book") == {"book": 1}
    assert hot.schedule_warm(
        "book",
        "detail",
        query,
        lambda: {"value": "latest"},
        ttl_seconds=30,
    )
    release.set()
    deadline = time.monotonic() + 1
    while hot.stats()["warm_queue_depth"] and time.monotonic() < deadline:
        time.sleep(0.01)

    assert hot.get_json("book", "detail", query) == {"value": "latest"}
    assert any(":book:0:" in key for key, _ttl, _value in redis.set_calls)
    assert any(":book:1:" in key for key, _ttl, _value in redis.set_calls)


class Cursor:
    def __init__(self, row=None):
        self.row = row
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args, **_kwargs):
        return None

    def fetchone(self):
        return self.row


class Pool:
    def __init__(self, row=None, events=None):
        self.row = row
        self.events = events

    @contextmanager
    def connection(self, readonly=False):
        del readonly
        try:
            yield SimpleNamespace(cursor=lambda: Cursor(self.row))
        finally:
            if self.events is not None:
                self.events.append("commit")


def test_cover_negative_lookup_is_cached():
    redis = FakeRedis()
    hot = cache(redis)
    store = MySQLCatalogStore(
        SimpleNamespace(mysql_database="test"), pool=Pool(None), cache_client=hot
    )
    assert store.get_cover_asset(42) is None
    first_sets = len(redis.set_calls)
    assert store.get_cover_asset(42) is None
    assert len(redis.set_calls) == first_sets
    raw = redis.set_calls[-1][2]
    assert json.loads(raw) == {"found": False, "value": None}


def test_cache_outage_falls_back_to_mysql_cover_mapping():
    redis = FakeRedis()
    redis.fail = True
    hot = cache(redis)
    durable = {"catalog_id": 42, "cover_object_key": "covers/42.jpg"}
    store = MySQLCatalogStore(
        SimpleNamespace(mysql_database="test"),
        pool=Pool(durable),
        cache_client=hot,
    )
    assert store.get_cover_asset(42) == durable


def test_post_commit_invalidation_order_and_queue_separation():
    events: list[str] = []

    class RecordingCache:
        def invalidate(self, *scopes):
            events.append("invalidate:" + ",".join(scopes))

        def schedule_warm(self, *_args, **_kwargs):
            events.append("warm")
            return True

    store = MySQLCatalogStore(
        SimpleNamespace(mysql_database="test"),
        pool=Pool({}, events),
        cache_client=RecordingCache(),
    )
    assert store.update_book_status(1, "finished", stamp="now")
    assert events[0] == "commit"
    assert events[1].startswith("invalidate:")

    queue_redis = SimpleNamespace(
        settings=SimpleNamespace(
            cache_redis_enabled=False,
            cache_redis_host="127.0.0.1",
            cache_redis_port=6380,
            cache_redis_db=0,
            cache_redis_password="",
            cache_redis_prefix="cache:",
            cache_redis_connect_timeout=0.2,
            cache_redis_socket_timeout=0.4,
            cache_redis_max_payload_bytes=262144,
        ),
        client=SimpleNamespace(incr=lambda *_args: (_ for _ in ()).throw(AssertionError())),
    )
    invalidated = []
    queue = LibraryDownloadQueue(
        SimpleNamespace(),
        queue_redis,
        cache=SimpleNamespace(invalidate=lambda *scopes: invalidated.extend(scopes)),
    )
    queue._invalidate_catalog_cache()
    assert invalidated == ["catalog", "book", "cover"]


def test_deconstruction_manifest_cache_contains_metadata_only(tmp_path: Path):
    root = tmp_path / "全局拆书库"
    book = root / "星海猎人__42"
    book.mkdir(parents=True)
    (book / "快速预览.md").write_text("summary", encoding="utf-8")
    redis = FakeRedis()
    reader = GlobalDeconstructionReader(root, cache=cache(redis))

    first = reader.snapshot()
    (book / "正文.txt").write_text("NEVER-CACHED-BODY", encoding="utf-8")
    second = reader.snapshot()

    assert first["total"] == second["total"] == 1
    assert second["cache"] == "redis"
    assert b"NEVER-CACHED-BODY" not in b"".join(call[2] for call in redis.set_calls)
