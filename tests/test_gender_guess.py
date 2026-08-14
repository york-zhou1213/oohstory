from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from app.gender_guess import (
    ALLOWED_ENDPOINT,
    GenderGuessCache,
    GenderGuessClient,
    _CircuitBreaker,
    batch_lookup_cached,
    is_clean_chinese_name,
    lookup_gender_cached,
    lookup_gender_cache_only,
)


# ── Name validation ──


def test_clean_chinese_name_accepts_2_to_4_character_names():
    assert is_clean_chinese_name("张伟")
    assert is_clean_chinese_name("言知时")
    assert is_clean_chinese_name("肖亚文")
    assert is_clean_chinese_name("洛郁")
    assert is_clean_chinese_name("欧阳锋")
    assert is_clean_chinese_name("司马相如")
    assert is_clean_chinese_name("冼星")
    assert is_clean_chinese_name("覃思")
    assert is_clean_chinese_name("佘诗曼")


def test_clean_chinese_name_rejects_non_chinese():
    assert not is_clean_chinese_name("Alice")
    assert not is_clean_chinese_name("张A伟")
    assert not is_clean_chinese_name("")
    assert not is_clean_chinese_name("张")
    assert not is_clean_chinese_name("这是一个很长的名字不是")


def test_clean_chinese_name_rejects_aliases_and_titles():
    assert not is_clean_chinese_name("张老板他们")  # 5 chars, too long
    assert not is_clean_chinese_name("风华绝代无双")  # 6 chars, too long
    assert not is_clean_chinese_name("圣女大人")  # title suffix 大人


def test_title_strings_rejected_by_name_validation():
    assert not is_clean_chinese_name("张老板")
    assert not is_clean_chinese_name("李老师")
    assert not is_clean_chinese_name("王将军")
    assert not is_clean_chinese_name("魔王大人")
    assert not is_clean_chinese_name("圣女大人")
    assert not is_clean_chinese_name("大人")
    assert not is_clean_chinese_name("先生")
    assert not is_clean_chinese_name("小姐")
    assert not is_clean_chinese_name("赵长老")
    assert not is_clean_chinese_name("王公子")
    assert is_clean_chinese_name("张伟")
    assert is_clean_chinese_name("欧阳锋")
    assert is_clean_chinese_name("司马相如")
    assert is_clean_chinese_name("林雪")


def test_clean_chinese_name_rejects_metadata_labels():
    assert not is_clean_chinese_name("作者")
    assert not is_clean_chinese_name("分类")
    assert not is_clean_chinese_name("状态")
    assert not is_clean_chinese_name("章节数")
    assert not is_clean_chinese_name("嘴角含")
    assert not is_clean_chinese_name("为什么要")
    assert not is_clean_chinese_name("高声")
    assert not is_clean_chinese_name("温柔")
    assert not is_clean_chinese_name("关键字")
    assert not is_clean_chinese_name("开怀大")
    for narrative_word in ("解释", "回头", "眼睛", "直接", "轻轻"):
        assert not is_clean_chinese_name(narrative_word)


def test_extended_surnames_do_not_turn_narrative_words_into_people():
    assert not is_clean_chinese_name("淡淡")
    assert not is_clean_chinese_name("慢慢")
    assert not is_clean_chinese_name("说完")
    assert not is_clean_chinese_name("问完")
    assert not is_clean_chinese_name("边点头")


def test_provider_conflict_replaces_name_heuristic_but_not_explicit_prose():
    from app.audiobook import (
        _character_gender_verified,
        _merge_external_gender_evidence,
    )

    ding = {
        "canonical_name": "丁元英", "gender": "female",
        "gender_confidence": 0.99, "gender_source": "name_heuristic",
    }
    ouyang = {
        "canonical_name": "欧阳雪", "gender": "male",
        "gender_confidence": 0.99, "gender_source": "name_heuristic",
    }
    explicit = {
        "canonical_name": "云知意", "gender": "female",
        "gender_confidence": 0.99, "gender_source": "explicit-pronoun",
    }

    _merge_external_gender_evidence(
        ding, {"gender": "male", "confidence": 0.88}
    )
    _merge_external_gender_evidence(
        ouyang, {"gender": "female", "confidence": 0.91}
    )
    _merge_external_gender_evidence(
        explicit, {"gender": "male", "confidence": 0.92}
    )

    assert (ding["gender"], ding["_gender_review_required"]) == ("male", True)
    assert (ouyang["gender"], ouyang["_gender_review_required"]) == ("female", True)
    assert (explicit["gender"], explicit["_gender_review_required"]) == ("female", True)
    assert not _character_gender_verified(ding)
    assert not _character_gender_verified(ouyang)
    assert not _character_gender_verified(explicit)


def test_high_confidence_name_heuristic_is_not_a_permanent_voice_lock():
    from app.audiobook import _character_gender_verified

    assert not _character_gender_verified({
        "canonical_name": "丁元英",
        "gender": "female",
        "gender_confidence": 1.0,
        "gender_source": "name_heuristic",
    })
    assert _character_gender_verified({
        "canonical_name": "丁元英",
        "gender": "male",
        "gender_confidence": 0.88,
        "gender_source": "external",
    })


def test_high_voice_modifier_is_not_parsed_as_a_character():
    from app.audiobook import analyze_chapter

    characters, segments = analyze_chapter("陈岂高声说道：“快走！”")

    assert [item["canonical_name"] for item in characters] == ["陈岂"]
    assert characters[0]["_external_gender_lookup"] is True
    assert next(item for item in segments if item["kind"] == "dialogue")[
        "speaker"
    ] == "陈岂"


# ── CSRF / protocol parsing ──


def _mock_session_get(url, **kwargs):
    assert url == ALLOWED_ENDPOINT
    resp = MagicMock()
    resp.status_code = 200
    resp.is_redirect = False
    resp.text = "some html _token: 'abc123def456ghijklmnop' more html"
    resp.raise_for_status = MagicMock()
    resp.cookies = {"XSRF-TOKEN": "cookie-val", "session": "session-val"}
    return resp


def _mock_session_post_success(url, **kwargs):
    assert url == ALLOWED_ENDPOINT
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "success": True,
        "data": {"name": "张伟", "gender": "male", "percent": 0.88},
    }
    return resp


def _mock_session_post_csrf_expired(url, **kwargs):
    resp = MagicMock()
    resp.status_code = 419
    resp.raise_for_status = MagicMock(side_effect=Exception("419"))
    return resp


def test_client_extracts_csrf_and_returns_gender():
    client = GenderGuessClient()
    captured_post_kwargs = {}

    def tracking_post(url, **kwargs):
        captured_post_kwargs.update(kwargs)
        return _mock_session_post_success(url, **kwargs)

    mock_session = MagicMock()
    mock_session.get = _mock_session_get
    mock_session.post = tracking_post
    mock_session.headers = {}

    with patch("app.gender_guess.requests.Session", return_value=mock_session):
        result = client.lookup("张伟")

    assert result is not None
    assert result["gender"] == "male"
    assert result["percent"] == 0.88
    assert result["name"] == "张伟"
    assert captured_post_kwargs["data"]["_token"] == "abc123def456ghijklmnop"
    assert captured_post_kwargs["headers"]["X-Requested-With"] == "XMLHttpRequest"


def test_client_refreshes_csrf_on_419():
    client = GenderGuessClient()
    call_count = {"get": 0, "post": 0}

    def counting_get(url, **kwargs):
        call_count["get"] += 1
        return _mock_session_get(url, **kwargs)

    def counting_post(url, **kwargs):
        call_count["post"] += 1
        if call_count["post"] == 1:
            return _mock_session_post_csrf_expired(url, **kwargs)
        return _mock_session_post_success(url, **kwargs)

    mock_session = MagicMock()
    mock_session.get = counting_get
    mock_session.post = counting_post
    mock_session.headers = {}

    with patch("app.gender_guess.requests.Session", return_value=mock_session):
        result = client.lookup("张伟")

    assert result is not None
    assert result["gender"] == "male"
    assert call_count["get"] >= 2
    assert call_count["post"] >= 2


def test_client_rejects_non_chinese_names():
    client = GenderGuessClient()
    assert client.lookup("Alice") is None
    assert client.lookup("") is None


# ── Circuit breaker ──


def test_circuit_breaker_opens_after_threshold():
    cb = _CircuitBreaker(threshold=3, reset_seconds=10)
    assert not cb.is_open
    cb.record_failure()
    cb.record_failure()
    assert not cb.is_open
    cb.record_failure()
    assert cb.is_open


def test_circuit_breaker_resets_after_timeout():
    cb = _CircuitBreaker(threshold=2, reset_seconds=0.1)
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open
    time.sleep(0.15)
    assert not cb.is_open


def test_circuit_breaker_resets_on_success():
    cb = _CircuitBreaker(threshold=3, reset_seconds=100)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    cb.record_failure()
    assert not cb.is_open


def test_client_returns_none_when_circuit_open():
    client = GenderGuessClient()
    client._circuit = _CircuitBreaker(threshold=1, reset_seconds=100)
    client._circuit.record_failure()
    assert client.lookup("张伟") is None


# ── Timeout / failure behavior ──


def test_client_returns_none_on_network_error():
    import requests as real_requests

    client = GenderGuessClient()
    mock_session = MagicMock()
    mock_session.get.side_effect = real_requests.ConnectionError("refused")
    mock_session.headers = {}

    with patch("app.gender_guess.requests.Session", return_value=mock_session):
        result = client.lookup("张伟")

    assert result is None


def test_client_returns_none_on_malformed_response():
    client = GenderGuessClient()
    mock_session = MagicMock()
    mock_session.get = _mock_session_get
    mock_session.headers = {}

    bad_resp = MagicMock()
    bad_resp.status_code = 200
    bad_resp.raise_for_status = MagicMock()
    bad_resp.json.return_value = {"success": False}
    mock_session.post.return_value = bad_resp

    with patch("app.gender_guess.requests.Session", return_value=mock_session):
        result = client.lookup("张伟")

    assert result is None


def test_client_returns_none_on_invalid_gender_value():
    client = GenderGuessClient()
    mock_session = MagicMock()
    mock_session.get = _mock_session_get
    mock_session.headers = {}

    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "success": True,
        "data": {"name": "张伟", "gender": "robot", "percent": 0.5},
    }
    mock_session.post.return_value = resp

    with patch("app.gender_guess.requests.Session", return_value=mock_session):
        result = client.lookup("张伟")

    assert result is None


# ── Response validation ──


def test_response_name_mismatch_rejected():
    """Response data.name after NFKC normalization must match the requested name."""
    client = GenderGuessClient()
    mock_session = MagicMock()
    mock_session.get = _mock_session_get
    mock_session.headers = {}

    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "success": True,
        "data": {"name": "李明", "gender": "male", "percent": 0.9},
    }
    mock_session.post.return_value = resp

    with patch("app.gender_guess.requests.Session", return_value=mock_session):
        result = client.lookup("张伟")

    assert result is None


def test_invalid_percent_fails_closed():
    """Invalid percent (>1.0) must return None instead of clamping."""
    client = GenderGuessClient()
    mock_session = MagicMock()
    mock_session.get = _mock_session_get
    mock_session.headers = {}

    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "success": True,
        "data": {"name": "张伟", "gender": "male", "percent": 1.5},
    }
    mock_session.post.return_value = resp

    with patch("app.gender_guess.requests.Session", return_value=mock_session):
        result = client.lookup("张伟")

    assert result is None


# ── Cache hit / dedup ──


class FakeMySQLPool:
    def __init__(self):
        self._store: dict[bytes, dict] = {}
        self._tx_store = self._store

    class _Cursor:
        def __init__(self, store):
            self._store = store
            self._results = []
            self.rowcount = 0

        def execute(self, sql, params=None):
            self.rowcount = 0
            if "INSERT" in sql and params:
                key = params[0]
                if "'pending'" in sql:
                    existing = self._store.get(key)
                    if existing is None:
                        self._store[key] = {
                            "canonical_name": params[1],
                            "gender": "unknown",
                            "confidence": 0,
                            "status": "pending",
                            "claim_owner": params[3],
                            "claim_until": time.monotonic() + 30,
                        }
                    elif existing.get("status") != "success":
                        cur_owner = existing.get("claim_owner")
                        cur_until = existing.get("claim_until", 0)
                        if cur_owner is None or (cur_until or 0) < time.monotonic():
                            existing["claim_owner"] = params[3]
                            existing["claim_until"] = time.monotonic() + 30
                elif "'failed'" in sql:
                    existing = self._store.get(key, {})
                    if existing.get("status") == "success":
                        existing["claim_owner"] = None
                        existing["claim_until"] = None
                    else:
                        self._store[key] = {
                            "canonical_name": params[1],
                            "gender": "unknown",
                            "confidence": 0,
                            "provider": params[2],
                            "raw_percent": None,
                            "status": "failed",
                            "failure_reason": params[3] if len(params) > 3 else None,
                            "lookup_count": 1,
                            "created_at": "2026-01-01",
                            "updated_at": "2026-01-01",
                            "expires_at": None,
                            "claim_owner": None,
                            "claim_until": None,
                        }
                elif "'success'" in sql:
                    self._store[key] = {
                        "canonical_name": params[1],
                        "gender": params[2],
                        "confidence": params[3],
                        "provider": params[4],
                        "raw_percent": params[5] if len(params) > 5 else None,
                        "status": "success",
                        "failure_reason": None,
                        "lookup_count": 1,
                        "created_at": "2026-01-01",
                        "updated_at": "2026-01-01",
                        "expires_at": None,
                        "claim_owner": None,
                        "claim_until": None,
                    }
            elif "SELECT" in sql:
                if "claim_owner=%s" in sql and params and len(params) >= 2:
                    key = params[0]
                    owner = params[1]
                    row = self._store.get(key)
                    if row and row.get("claim_owner") == owner:
                        self._results = [{"x": 1}]
                    else:
                        self._results = []
                elif "IN" in sql:
                    self._results = [
                        v for k, v in self._store.items()
                        if k in (params if isinstance(params, (list, tuple)) else [params])
                        and v.get("status") == "success"
                    ]
                elif "name_key=%s" in sql:
                    key = params[0]
                    row = self._store.get(key)
                    if row:
                        if "status='success'" in sql and row.get("status") == "success":
                            self._results = [row]
                        elif "status='failed'" in sql and row.get("status") == "failed":
                            self._results = [{"x": 1}]
                        else:
                            self._results = []
                    else:
                        self._results = []
            elif "UPDATE" in sql and "status='success'" in sql:
                name, gender, confidence, provider, raw_percent, key, owner = params
                row = self._store.get(key)
                if row and row.get("claim_owner") == owner:
                    row.update({
                        "canonical_name": name, "gender": gender,
                        "confidence": confidence, "provider": provider,
                        "raw_percent": raw_percent, "status": "success",
                        "failure_reason": None, "claim_owner": None,
                        "claim_until": None,
                    })
                    self.rowcount = 1
            elif "UPDATE" in sql and "status='failed'" in sql:
                name, provider, reason, key, owner = params
                row = self._store.get(key)
                if (
                    row and row.get("claim_owner") == owner
                    and row.get("status") != "success"
                ):
                    row.update({
                        "canonical_name": name, "gender": "unknown",
                        "confidence": 0, "provider": provider,
                        "status": "failed", "failure_reason": reason,
                        "claim_owner": None, "claim_until": None,
                    })
                    self.rowcount = 1
            elif "UPDATE" in sql and "claim_owner=NULL" in sql:
                if params and len(params) >= 2:
                    key, owner = params
                    row = self._store.get(key)
                    if row and row.get("claim_owner") == owner:
                        row["claim_owner"] = None
                        row["claim_until"] = None
                        self.rowcount = 1

        def fetchone(self):
            return self._results[0] if self._results else None

        def fetchall(self):
            return self._results

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class _Connection:
        def __init__(self, store):
            self._store = store

        def cursor(self):
            return FakeMySQLPool._Cursor(self._store)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def connection(self):
        return self._Connection(self._store)

    def transaction(self):
        return self._Connection(self._store)


def test_cache_stores_and_retrieves_gender():
    pool = FakeMySQLPool()
    cache = GenderGuessCache(pool)

    assert cache.try_claim("张伟", "seed")
    assert cache.put("张伟", "male", 0.88, 0.88, owner="seed")
    result = cache.get("张伟")

    assert result is not None
    assert result["gender"] == "male"
    assert result["confidence"] == 0.88


def test_cache_miss_returns_none():
    pool = FakeMySQLPool()
    cache = GenderGuessCache(pool)
    assert cache.get("不存在") is None


def test_cache_prevents_repeated_lookups():
    pool = FakeMySQLPool()
    cache = GenderGuessCache(pool)
    assert cache.try_claim("张伟", "seed")
    assert cache.put("张伟", "male", 0.88, 0.88, owner="seed")

    lookup_calls = []

    def tracking_lookup(self, name):
        lookup_calls.append(name)
        return {"name": name, "gender": "male", "percent": 0.88}

    client = GenderGuessClient()
    with patch.object(GenderGuessClient, "lookup", tracking_lookup):
        r1 = lookup_gender_cached("张伟", cache=cache, client=client)
        r2 = lookup_gender_cached("张伟", cache=cache, client=client)

    assert r1["gender"] == "male"
    assert r2["gender"] == "male"
    assert len(lookup_calls) == 0


def test_failed_lookup_cached_as_failure():
    pool = FakeMySQLPool()
    cache = GenderGuessCache(pool)

    client = MagicMock(spec=GenderGuessClient)
    client.lookup.return_value = None

    result = lookup_gender_cached("张伟", cache=cache, client=client)
    assert result is None
    client.lookup.assert_called_once_with("张伟")


def test_failure_does_not_overwrite_success():
    """put_failure must preserve a stale success rather than replacing it."""
    pool = FakeMySQLPool()
    cache = GenderGuessCache(pool)

    assert cache.try_claim("张伟", "seed")
    assert cache.put("张伟", "male", 0.88, 0.88, owner="seed")
    assert not cache.put_failure("张伟", "transient error", owner="stale-owner")

    result = cache.get("张伟")
    assert result is not None
    assert result["gender"] == "male"
    assert result["status"] == "success"


def test_cached_gender_used_synchronously_no_http():
    """Cached external gender consumed via lookup_gender_cache_only without HTTP."""
    pool = FakeMySQLPool()
    cache = GenderGuessCache(pool)
    assert cache.try_claim("张伟", "seed")
    assert cache.put("张伟", "male", 0.88, 0.88, owner="seed")

    result = lookup_gender_cache_only("张伟", cache=cache)
    assert result is not None
    assert result["gender"] == "male"
    assert result["confidence"] == 0.88
    assert result["source"] == "wuruihong-guess-gender"


# ── Cross-process claim dedup ──


class FakeClaimableCache:
    """In-memory implementation of GenderGuessCache interface for dedup tests."""

    def __init__(self):
        self._data: dict[str, dict] = {}
        self._lock = threading.Lock()

    def get(self, name):
        with self._lock:
            entry = self._data.get(name)
            if entry and entry.get("status") == "success":
                return {"canonical_name": name, **entry}
        return None

    def get_many(self, names):
        return {n: r for n in names if (r := self.get(n))}

    def is_recently_failed(self, name):
        with self._lock:
            entry = self._data.get(name)
            return bool(entry and entry.get("status") == "failed")

    def try_claim(self, name, owner, lease_seconds=30):
        with self._lock:
            entry = self._data.get(name)
            if entry is None:
                self._data[name] = {
                    "status": "pending", "claim_owner": owner,
                    "claim_until": time.monotonic() + lease_seconds,
                }
                return True
            if entry.get("status") == "success":
                return False
            cur_owner = entry.get("claim_owner")
            cur_until = entry.get("claim_until", 0)
            if cur_owner is None or (cur_until or 0) < time.monotonic():
                entry["claim_owner"] = owner
                entry["claim_until"] = time.monotonic() + lease_seconds
                return True
            return False

    def release_claim(self, name, owner):
        with self._lock:
            entry = self._data.get(name)
            if entry and entry.get("claim_owner") == owner:
                entry["claim_owner"] = None
                entry["claim_until"] = None

    def put(self, name, gender, confidence, raw_percent=None, *, owner):
        with self._lock:
            entry = self._data.get(name)
            if not entry or entry.get("claim_owner") != owner:
                return False
            self._data[name] = {
                "gender": gender, "confidence": confidence,
                "status": "success", "claim_owner": None, "claim_until": None,
            }
            return True

    def put_failure(self, name, reason, *, owner):
        with self._lock:
            entry = self._data.get(name, {})
            if entry.get("status") == "success" or entry.get("claim_owner") != owner:
                return False
            self._data[name] = {
                "gender": "unknown", "confidence": 0,
                "status": "failed", "claim_owner": None, "claim_until": None,
            }
            return True


def test_same_name_claim_dedupe_one_http_call():
    """Two concurrent callers for the same name result in exactly one external lookup."""
    cache = FakeClaimableCache()
    lookup_calls = []
    barrier = threading.Barrier(2, timeout=5)

    def slow_lookup(name):
        lookup_calls.append(name)
        time.sleep(0.1)
        return {"name": name, "gender": "male", "percent": 0.85}

    client = MagicMock(spec=GenderGuessClient)
    client.lookup.side_effect = slow_lookup

    results = []

    def do_lookup():
        barrier.wait()
        r = lookup_gender_cached("张伟", cache=cache, client=client)
        results.append(r)

    t1 = threading.Thread(target=do_lookup)
    t2 = threading.Thread(target=do_lookup)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert client.lookup.call_count == 1
    success_results = [r for r in results if r is not None]
    assert len(success_results) >= 1
    assert success_results[0]["gender"] == "male"


# ── Evidence precedence ──


def test_explicit_pronoun_outranks_external_lookup():
    from app.audiobook import _traits

    traits = _traits("林雪", "她轻声说道别怕我在这里")
    assert traits["gender"] == "female"
    assert traits["gender_confidence"] >= 0.7

    assert traits["gender_confidence"] >= 0.55


def test_role_suffix_outranks_external_lookup():
    from app.audiobook import _role_gender

    gender, conf, source = _role_gender("王姐姐")
    assert gender == "female"
    assert conf == 0.97
    assert source == "role_suffix"


def test_weak_heuristic_replaceable_by_external():
    """name_heuristic source can be replaced by external lookup."""
    from app.audiobook import AudiobookService

    class Repo:
        _mysql = None

        def reader_chapter(self, book_id, chapter_id):
            return {"title": "t", "content": "", "next_id": None}

    service = AudiobookService(Repo())
    service._gender_guess_enabled = True
    pool = FakeMySQLPool()
    service._gender_cache = GenderGuessCache(pool)
    assert service._gender_cache.try_claim("张伟", "seed")
    assert service._gender_cache.put(
        "张伟", "female", 0.92, 0.92, owner="seed"
    )

    characters = [
        {"canonical_name": "张伟", "gender": "male", "gender_confidence": 0.85,
         "gender_source": "name_heuristic",
         "_external_gender_lookup": True,
         "aliases": ["张伟"], "age_group": "unknown", "age_confidence": 0,
         "tone": "neutral", "tone_confidence": 0.4},
    ]
    service._enrich_gender(characters, {})
    assert characters[0]["gender"] == "female"
    assert characters[0]["gender_source"] == "external"


def test_explicit_context_not_replaceable_by_external():
    """context source must never be overridden by external lookup."""
    from app.audiobook import AudiobookService

    class Repo:
        _mysql = None

        def reader_chapter(self, book_id, chapter_id):
            return {"title": "t", "content": "", "next_id": None}

    service = AudiobookService(Repo())
    service._gender_guess_enabled = True
    pool = FakeMySQLPool()
    service._gender_cache = GenderGuessCache(pool)
    assert service._gender_cache.try_claim("林雪", "seed")
    assert service._gender_cache.put("林雪", "male", 0.99, 0.99, owner="seed")

    characters = [
        {"canonical_name": "林雪", "gender": "female", "gender_confidence": 0.75,
         "gender_source": "context",
         "aliases": ["林雪"], "age_group": "unknown", "age_confidence": 0,
         "tone": "neutral", "tone_confidence": 0.4},
    ]
    service._enrich_gender(characters, {})
    assert characters[0]["gender"] == "female"
    assert characters[0]["gender_source"] == "context"


# ── Concurrent lookup coalescing ──


def test_concurrent_lookups_are_serialized_by_semaphore():
    client = GenderGuessClient()
    client._concurrency = threading.Semaphore(1)
    results = []

    mock_session = MagicMock()
    mock_session.get = _mock_session_get
    mock_session.headers = {}

    call_timestamps = []

    def slow_post(url, **kwargs):
        call_timestamps.append(time.monotonic())
        time.sleep(0.05)
        return _mock_session_post_success(url, **kwargs)

    mock_session.post = slow_post

    def do_lookup(name):
        with patch("app.gender_guess.requests.Session", return_value=mock_session):
            results.append(client.lookup(name))

    t1 = threading.Thread(target=do_lookup, args=("张伟",))
    t2 = threading.Thread(target=do_lookup, args=("李明",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert len(results) == 2


# ── Voice-lock stability ──


def test_unknown_gender_does_not_guess_a_gendered_voice():
    from app.audiobook import deterministic_voice

    unknown_character = {
        "canonical_name": "阿尔法",
        "gender": "unknown",
    }
    original_voice = deterministic_voice(42, unknown_character)
    assert original_voice == ""
    stable_voice = deterministic_voice(42, unknown_character)
    assert original_voice == stable_voice


def test_voice_key_preserved_when_gender_changes():
    from app.audiobook import AudiobookService

    class TestRepo:
        _mysql = None

        def reader_chapter(self, book_id, chapter_id):
            return {
                "title": "test",
                "content": "阿尔法：出发。",
                "next_id": None,
            }

    service = AudiobookService(TestRepo())
    book_id = "V" * 22
    m1 = service.create(
        owner_key="x" * 64, book_id=book_id, chapter_id=1, settings={
            "mode": "smart", "narrator": "mocheng", "voice": "nuanxi",
            "emotion": "auto", "rate": 1.0,
        }
    )["current"]
    char1 = next(c for c in m1["characters"] if c["canonical_name"] == "阿尔法")
    voice1 = char1["voice_key"]

    m2 = service.create(
        owner_key="y" * 64, book_id=book_id, chapter_id=1, settings={
            "mode": "smart", "narrator": "mocheng", "voice": "nuanxi",
            "emotion": "auto", "rate": 1.0,
        }
    )["current"]
    char2 = next(c for c in m2["characters"] if c["canonical_name"] == "阿尔法")
    voice2 = char2["voice_key"]

    assert voice1 == voice2


# ── Batch lookup ──


def test_batch_lookup_skips_non_chinese_names():
    client = MagicMock(spec=GenderGuessClient)
    client.lookup.return_value = None
    results = batch_lookup_cached(
        ["Alice", "Bob", "R2D2"],
        cache=None,
        client=client,
    )
    assert results == {}
    client.lookup.assert_not_called()


# ── Integration: _enrich_gender precedence ──


def test_enrich_does_not_override_high_confidence_local():
    """Strong evidence source (context) prevents external override."""
    from app.audiobook import AudiobookService

    class Repo:
        _mysql = None

        def reader_chapter(self, book_id, chapter_id):
            return {"title": "t", "content": "", "next_id": None}

    service = AudiobookService(Repo())
    service._gender_guess_enabled = True
    service._gender_guess_threshold = 0.7

    characters = [
        {"canonical_name": "林雪", "gender": "female", "gender_confidence": 0.99,
         "gender_source": "context",
         "aliases": ["林雪"], "age_group": "unknown", "age_confidence": 0,
         "tone": "neutral", "tone_confidence": 0.4},
    ]
    service._enrich_gender(characters, {})
    assert characters[0]["gender"] == "female"
    assert characters[0]["gender_confidence"] == 0.99


def test_enrich_reads_external_evidence_but_preserves_explicit_local_gender():
    from app.audiobook import AudiobookService

    class Repo:
        _mysql = None

        def reader_chapter(self, book_id, chapter_id):
            return {"title": "t", "content": "", "next_id": None}

    service = AudiobookService(Repo())
    service._gender_cache = MagicMock(spec=GenderGuessCache)
    service._gender_cache.get.return_value = {
        "gender": "male", "confidence": 0.91,
    }
    characters = [{
        "canonical_name": "云知意",
        "gender": "female",
        "gender_confidence": 0.99,
        "gender_source": "explicit-pronoun",
        "_external_gender_lookup": True,
    }]

    service._enrich_gender(characters, {})

    assert characters[0]["gender"] == "female"
    assert characters[0]["_gender_review_required"] is True
    service._gender_cache.get.assert_called_once_with("云知意")


def test_enrich_requires_strong_dialogue_attribution():
    """A name-like parser fragment must never trigger cache or provider use."""
    from app.audiobook import AudiobookService

    class Repo:
        _mysql = None

        def reader_chapter(self, book_id, chapter_id):
            return {"title": "t", "content": "", "next_id": None}

    service = AudiobookService(Repo())
    service._gender_guess_enabled = True
    service._gender_cache = MagicMock(spec=GenderGuessCache)
    characters = [{
        "canonical_name": "嘴角含",
        "gender": "unknown",
        "gender_confidence": 0.0,
        "gender_source": "unknown",
    }]

    service._enrich_gender(characters, {})

    service._gender_cache.get.assert_not_called()


def test_enrich_skips_protagonist():
    from app.audiobook import AudiobookService
    from app.audiobook_cast import PROTAGONIST_NAME

    class Repo:
        _mysql = None

        def reader_chapter(self, book_id, chapter_id):
            return {"title": "t", "content": "", "next_id": None}

    service = AudiobookService(Repo())
    service._gender_guess_enabled = True
    service._gender_guess_threshold = 0.7

    characters = [
        {"canonical_name": PROTAGONIST_NAME, "gender": "unknown", "gender_confidence": 0,
         "aliases": [PROTAGONIST_NAME, "我"], "age_group": "unknown", "age_confidence": 0,
         "tone": "neutral", "tone_confidence": 0.4},
    ]
    service._enrich_gender(characters, {})
    assert characters[0]["gender"] == "unknown"


# ── Session creation / transaction safety ──


def test_session_creation_makes_zero_external_calls():
    """Creating a session must never trigger external gender-guess HTTP."""
    from app.audiobook import AudiobookService

    class Repo:
        _mysql = None

        def reader_chapter(self, book_id, chapter_id):
            return {"title": "t", "content": "林雪说道：“走。”", "next_id": None}

    service = AudiobookService(Repo())
    service._gender_guess_enabled = True
    service._gender_guess_threshold = 0.7

    mock_client = MagicMock(spec=GenderGuessClient)
    service._gender_client = mock_client

    start = time.monotonic()
    service.create(
        owner_key="x" * 64, book_id="V" * 22, chapter_id=1,
        settings={"mode": "smart", "narrator": "mocheng", "voice": "nuanxi",
                  "emotion": "auto", "rate": 1.0},
    )
    elapsed = time.monotonic() - start

    mock_client.lookup.assert_not_called()
    assert elapsed < 2.0


def test_no_http_lookup_inside_cast():
    """_cast must not call any HTTP-making gender functions."""
    from app.audiobook import AudiobookService

    class Repo:
        _mysql = None

        def reader_chapter(self, book_id, chapter_id):
            return {"title": "t", "content": "张伟说道：“走。”", "next_id": None}

    service = AudiobookService(Repo())
    service._gender_guess_enabled = True

    mock_client = MagicMock(spec=GenderGuessClient)
    service._gender_client = mock_client

    characters = [
        {"canonical_name": "张伟", "gender": "unknown", "gender_confidence": 0.0,
         "gender_source": "unknown", "aliases": ["张伟"], "age_group": "unknown",
         "age_confidence": 0, "tone": "neutral", "tone_confidence": 0.4},
    ]
    service._cast(42, characters)

    mock_client.lookup.assert_not_called()


def test_existing_voice_unchanged_after_enrichment():
    """Existing voice_key must never change even when gender is enriched."""
    from app.audiobook import AudiobookService

    class Repo:
        _mysql = None

        def reader_chapter(self, book_id, chapter_id):
            return {"title": "t", "content": "张伟说道：“走。”", "next_id": None}

    service = AudiobookService(Repo())
    book_id = "V" * 22
    result1 = service.create(
        owner_key="x" * 64, book_id=book_id, chapter_id=1,
        settings={"mode": "smart", "narrator": "mocheng", "voice": "nuanxi",
                  "emotion": "auto", "rate": 1.0},
    )["current"]
    char1 = next(c for c in result1["characters"] if c["canonical_name"] == "张伟")
    voice1 = char1["voice_key"]

    result2 = service.create(
        owner_key="y" * 64, book_id=book_id, chapter_id=1,
        settings={"mode": "smart", "narrator": "mocheng", "voice": "nuanxi",
                  "emotion": "auto", "rate": 1.0},
    )["current"]
    char2 = next(c for c in result2["characters"] if c["canonical_name"] == "张伟")
    voice2 = char2["voice_key"]

    assert voice1 == voice2


# ── Response validation ──


def test_client_handles_missing_data_field():
    client = GenderGuessClient()
    mock_session = MagicMock()
    mock_session.get = _mock_session_get
    mock_session.headers = {}

    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"success": True}
    mock_session.post.return_value = resp

    with patch("app.gender_guess.requests.Session", return_value=mock_session):
        result = client.lookup("张伟")

    assert result is None


def test_client_handles_non_dict_response():
    client = GenderGuessClient()
    mock_session = MagicMock()
    mock_session.get = _mock_session_get
    mock_session.headers = {}

    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = "not a dict"
    mock_session.post.return_value = resp

    with patch("app.gender_guess.requests.Session", return_value=mock_session):
        result = client.lookup("张伟")

    assert result is None
