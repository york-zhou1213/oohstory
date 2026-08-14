from __future__ import annotations

import json
import inspect
from hashlib import sha256
from pathlib import Path
from unittest.mock import MagicMock

from app.audiobook import (
    AudiobookService,
    ENGINE_VERSION,
    FEMALE_VOICES,
    MALE_VOICES,
    analyze_chapter,
    deterministic_voice,
)
from app.audiobook_cast import (
    CastPrewarmManager,
    PROTAGONIST_NAME,
    action_fragment_profile_names,
)


BOOK_ID = "B" * 22
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_first_person_protagonist_is_gendered_before_and_after_dialogue() -> None:
    female, female_segments = analyze_chapter(
        "我低声说道：‘本姑娘绝不会认输。’"
    )
    male, male_segments = analyze_chapter(
        "“我是个男人，跟我走。”我沉声说道。"
    )

    assert female[0]["canonical_name"] == PROTAGONIST_NAME
    assert female[0]["gender"] == "female"
    assert set(female[0]["aliases"]) == {PROTAGONIST_NAME, "我"}
    assert male[0]["canonical_name"] == PROTAGONIST_NAME
    assert male[0]["gender"] == "male"
    assert next(
        item for item in female_segments if item["kind"] == "dialogue"
    )["speaker"] == PROTAGONIST_NAME
    assert next(
        item for item in male_segments if item["kind"] == "dialogue"
    )["speaker"] == PROTAGONIST_NAME
    assert deterministic_voice(7, female[0]) in FEMALE_VOICES
    assert deterministic_voice(7, male[0]) in MALE_VOICES


def test_profile_rows_keep_character_key_role_and_chapter_counts() -> None:
    key = sha256(PROTAGONIST_NAME.encode()).digest()
    cast = {
        PROTAGONIST_NAME: {
            "canonical_name": PROTAGONIST_NAME,
            "aliases": [PROTAGONIST_NAME, "我"],
            "character_key": key,
        }
    }
    rows = CastPrewarmManager._profile_rows(
        cast,
        [
            {"kind": "dialogue", "speaker": PROTAGONIST_NAME},
            {"kind": "dialogue", "speaker": PROTAGONIST_NAME},
            {"kind": "narration", "speaker": ""},
        ],
        19,
    )

    assert rows == [
        {
            "character_key": key,
            "canonical_name": PROTAGONIST_NAME,
            "role_type": "protagonist",
            "mention_count": 2,
            "dialogue_count": 2,
            "chapter_count": 1,
            "chapter_id": 19,
            "voice_locked": 0,
        }
    ]


def test_cast_scan_revision_changes_with_analyzer_engine(monkeypatch) -> None:
    class ScanRepository:
        _mysql = None

        @staticmethod
        def audiobook_cast_scan_plan(_book_id: str):
            return {
                "catalog_id": 7,
                "book_public_id": BOOK_ID,
                "content_revision": "ab" * 32,
                "chapter_ids": [1, 2],
            }

    manager = CastPrewarmManager(
        ScanRepository(),
        analyzer=analyze_chapter,
        existing_cast=lambda _catalog_id: [],
        resolve_cast=lambda _catalog_id, _characters: {},
    )
    monkeypatch.setenv("OOHSTORY_CAST_ENGINE_VERSION", "oohstory-cast-v11.4")
    first = manager._scan_plan(BOOK_ID)
    monkeypatch.setenv("OOHSTORY_CAST_ENGINE_VERSION", "oohstory-cast-v11.5")
    second = manager._scan_plan(BOOK_ID)

    assert first.content_revision == sha256(
        f"{'ab' * 32}\0oohstory-cast-v11.4".encode()
    ).hexdigest()
    assert first.content_revision != second.content_revision


def test_manifest_character_serializes_binary_mysql_key_as_hex() -> None:
    key = bytes.fromhex("ab" * 32)

    rendered = AudiobookService._manifest_character(
        {"canonical_name": "主人公", "character_key": memoryview(key)}
    )

    assert rendered["character_key"] == "ab" * 32
    assert json.dumps(rendered, ensure_ascii=False)


def test_prewarm_keeps_only_strong_or_existing_characters() -> None:
    characters = [
        {"canonical_name": "陈岂", "_external_gender_lookup": True},
        {"canonical_name": "章节数", "_external_gender_lookup": True},
        {"canonical_name": "嘴角含", "_external_gender_lookup": False},
        {
            "canonical_name": "小梅",
            "_external_gender_lookup": False,
            "_identity_confidence": 0.99,
            "_scanner_identity_verified": True,
        },
        {"canonical_name": "陈旧角", "_external_gender_lookup": False},
        {"canonical_name": PROTAGONIST_NAME, "_external_gender_lookup": False},
    ]
    existing = [{
        "canonical_name": "陈旧角", "aliases": ["旧角"],
        "_current_scan_revision": True,
    }]

    kept = CastPrewarmManager._eligible_characters(characters, existing)

    assert [item["canonical_name"] for item in kept] == [
        "陈岂", "小梅", "陈旧角", PROTAGONIST_NAME,
    ]


def test_prewarm_does_not_inherit_unversioned_legacy_pseudo_characters() -> None:
    characters = [
        {"canonical_name": "关键字", "_external_gender_lookup": False},
        {"canonical_name": "桑迪嘲", "_external_gender_lookup": False},
        {"canonical_name": "靳汝反", "_external_gender_lookup": False},
    ]
    existing = [
        {"canonical_name": item["canonical_name"], "aliases": []}
        for item in characters
    ]

    assert CastPrewarmManager._eligible_characters(characters, existing) == []


def test_prewarm_identity_gate_rejects_narrative_words_and_generic_roles() -> None:
    for value in (
        "解释", "回头", "眼睛", "轻轻", "男孩", "老板",
        "席间", "干脆", "连连", "车子调", "马上联", "马经",
        "淡淡", "慢慢", "呵呵", "一只手", "上来就", "忍不住",
    ):
        assert CastPrewarmManager._plausible_persisted_name(value) is False
    for value in ("肖亚文", "洛郁", "小梅", "阿城", "孟姐", "宝儿"):
        assert CastPrewarmManager._plausible_persisted_name(value) is True


def test_scan_completion_identifies_only_known_actor_action_tails() -> None:
    names = [
        "程逸", "程逸摆", "张岚", "张岚一", "小梅", "言知时",
        "肖亚文一", "欧阳雪", "欧阳雪吃", "周伟", "周伟怒",
    ]
    assert action_fragment_profile_names(names) == {
        "程逸摆", "张岚一", "欧阳雪吃", "周伟怒",
    }


def test_scan_completion_deletes_contaminated_profiles_and_voices() -> None:
    source = inspect.getsource(CastPrewarmManager._complete)
    assert "DELETE FROM audiobook_character_voices" in source
    assert "DELETE FROM audiobook_character_profiles" in source
    assert "DELETE v FROM audiobook_character_voices v" in source
    assert "p.scan_revision=UNHEX(%s)" in source


def test_ai_review_selection_includes_unlocked_high_confidence_conflicts() -> None:
    from app.audiobook_cast_review_worker import _candidate_rows

    source = _candidate_rows.__code__.co_consts
    assert any(
        isinstance(value, str) and "v.voice_locked=0" in value
        for value in source
    )
    assert any(
        isinstance(value, str)
        and "p.role_type IN ('protagonist','supporting')" in value
        for value in source
    )


def test_ai_review_claim_is_bound_to_current_completed_scan_revision() -> None:
    from app.audiobook_cast_review_worker import CLAIM_SQL

    assert "s.content_revision=j.content_revision" in CLAIM_SQL
    assert "s.status='complete'" in CLAIM_SQL


def test_prewarm_external_lookup_checks_explicit_gender_and_queues_conflict() -> None:
    manager = object.__new__(CastPrewarmManager)
    manager._gender_cache = MagicMock()
    manager._gender_cache.get.return_value = {
        "gender": "male", "confidence": 0.91,
    }
    manager._gender_client = MagicMock()
    manager._gender_guess_threshold = 0.7
    characters = [{
        "canonical_name": "云知意",
        "gender": "female",
        "gender_confidence": 0.99,
        "gender_source": "explicit-pronoun",
        "_external_gender_lookup": True,
    }]

    manager._enrich_unknown_genders(characters)

    assert characters[0]["gender"] == "female"
    assert characters[0]["_gender_review_required"] is True
    manager._gender_cache.get.assert_called_once_with("云知意")
    manager._gender_client.lookup.assert_not_called()


def test_prewarm_checks_every_eligible_chinese_name_without_three_name_cap() -> None:
    manager = object.__new__(CastPrewarmManager)
    manager._gender_cache = None
    manager._gender_client = MagicMock()
    manager._gender_client.lookup.side_effect = lambda name: {
        "name": name, "gender": "male", "percent": 0.85,
    }
    manager._gender_guess_threshold = 0.7
    names = ["张伟", "李明", "王强", "陈刚", "赵勇"]
    characters = [{
        "canonical_name": name,
        "gender": "unknown",
        "gender_confidence": 0.0,
        "gender_source": "unknown",
        "_external_gender_lookup": True,
    } for name in names]

    manager._enrich_unknown_genders(characters, max_new_lookups=3)

    assert manager._gender_client.lookup.call_count == len(names)
    assert {item["gender_source"] for item in characters} == {"external"}


class FakeRepository:
    _mysql = None

    def reader_chapter(self, book_id: str, chapter_id: int):
        assert book_id == BOOK_ID
        assert chapter_id == 1
        return {
            "title": "开场",
            "content": "林雪说道：‘走吧。’",
            "next_id": None,
        }


def _settings(mode: str) -> dict[str, object]:
    return {
        "mode": mode,
        "narrator": "mocheng",
        "voice": "nuanxi",
        "emotion": "auto",
        "rate": 1.0,
    }


def test_only_smart_sessions_enqueue_nonblocking_whole_book_prewarm() -> None:
    service = AudiobookService(FakeRepository())
    requested: list[str] = []
    service._cast_prewarm.request = requested.append

    service.create(
        owner_key="1" * 64,
        book_id=BOOK_ID,
        chapter_id=1,
        settings=_settings("normal"),
    )
    service.create(
        owner_key="2" * 64,
        book_id=BOOK_ID,
        chapter_id=1,
        settings=_settings("smart"),
    )

    assert requested == [BOOK_ID]
    assert ENGINE_VERSION == "oohstory-cast-v15-colonfield1"
    worker = (PROJECT_ROOT / "app" / "audiobook_cast.py").read_text(encoding="utf-8")
    assert "completed_at=UTC_TIMESTAMP(6),error_count=0,last_error=NULL" in worker


def _service_with_snapshot_cursor(cursor: MagicMock) -> AudiobookService:
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = connection
    service = object.__new__(AudiobookService)
    service.repository = MagicMock(_mysql=MagicMock(pool=pool))
    return service


def test_engine_upgrade_uses_last_published_cast_until_replacement_exists() -> None:
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        None,
        {
            "cast_json": [{
                "canonical_name": "林雪",
                "aliases": ["林雪"],
                "gender": "female",
                "gender_confidence": 0.99,
                "voice_key": "nuanxi",
                "voice_locked": 1,
                "role_type": "supporting",
                "chapter_count": 5,
                "dialogue_count": 12,
            }],
        },
    ]
    service = _service_with_snapshot_cursor(cursor)

    cast = service._existing_cast(17, trusted_only=False)

    assert [item["canonical_name"] for item in cast] == ["林雪"]
    assert cast[0]["_current_scan_revision"] is False
    assert cursor.execute.call_args_list[0].args[1] == (
        17, "oohstory-cast-v15-colonfield1",
    )
    assert cursor.execute.call_args_list[1].args[1] == (17,)


def test_engine_upgrade_keeps_previous_cast_revision_stable_during_scan() -> None:
    cursor = MagicMock()
    cursor.fetchone.side_effect = [None, {"revision": 284}]
    service = _service_with_snapshot_cursor(cursor)

    assert service._cast_revision(17) == 284
    assert cursor.execute.call_count == 2
    assert cursor.execute.call_args_list[1].args[1] == (17,)


def test_mysql_migration_has_resumable_jobs_profiles_and_role_grants() -> None:
    migration = (
        PROJECT_ROOT / "deploy" / "mysql-audiobook-cast-prewarm.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS audiobook_cast_scan_jobs" in migration
    assert "processed_chapters" in migration
    assert "lease_token" in migration
    assert "CREATE TABLE IF NOT EXISTS audiobook_character_profiles" in migration
    assert "voice_locked" in migration
    assert migration.count("TO 'oohstory_audiobook_role'@'%'") == 5
    assert (
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "oohstory_library.audiobook_character_profiles"
    ) in migration
    assert "GRANT DELETE ON oohstory_library.audiobook_character_voices" in migration
    assert "CREATE TABLE IF NOT EXISTS audiobook_cast_ai_review_jobs" in migration
    assert "CREATE TABLE IF NOT EXISTS audiobook_cast_ai_reviews" in migration
    assert "review_audiobook_cast_with_openclaw.py" in (
        PROJECT_ROOT / "app" / "audiobook_cast_review_worker.py"
    ).read_text(encoding="utf-8")
    review_worker = (
        PROJECT_ROOT / "app" / "audiobook_cast_review_worker.py"
    ).read_text(encoding="utf-8")
    assert "status='deferred'" in review_worker
    assert "1440" in review_worker
    assert "cast_ai_review=deferred" in review_worker


def test_runtime_v8_migration_versions_cast_and_global_runtime_state() -> None:
    migration = (
        PROJECT_ROOT / "deploy" / "mysql-audiobook-runtime-v8.sql"
    ).read_text(encoding="utf-8")
    scanner_unit = (
        PROJECT_ROOT / "deploy" / "oohstory-audiobook-cast.service"
    ).read_text(encoding="utf-8")
    reader_unit = (
        PROJECT_ROOT / "deploy" / "oohstory-reader.service"
    ).read_text(encoding="utf-8")
    maintenance_unit = (
        PROJECT_ROOT / "deploy" / "oohstory-audiobook-maintenance.service"
    ).read_text(encoding="utf-8")
    main_source = (PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")

    assert "audiobook_cast_revisions" in migration
    assert "cast_revision" in migration
    assert "audiobook_tts_leases" in migration
    assert "audiobook_progress" in migration
    assert "site_limit: int = 8" in main_source
    assert '"WHERE expires_at>UTC_TIMESTAMP(6)"' in main_source
    assert "self.site_limit" in main_source
    audio_root = "/srv/oohstory/library/oohstory/audiobook-audio"
    assert f"Environment=OOHSTORY_AUDIOBOOK_AUDIO_ROOT={audio_root}" in reader_unit
    assert audio_root in next(
        line for line in reader_unit.splitlines() if line.startswith("ReadWritePaths=")
    )
    assert f"Environment=OOHSTORY_AUDIOBOOK_AUDIO_ROOT={audio_root}" in maintenance_unit
    assert audio_root in next(
        line
        for line in maintenance_unit.splitlines()
        if line.startswith("ReadWritePaths=")
    )
    assert "CPUQuota=50%" in scanner_unit
    assert "MemoryMax=512M" in scanner_unit
    assert "IOWeight=20" in scanner_unit


def test_audiobook_v9_closes_resume_cache_cleanup_and_adaptive_prefetch_contracts() -> None:
    app_script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    cache_script = (PROJECT_ROOT / "static" / "audiobook-cache.js").read_text(encoding="utf-8")
    maintenance = (PROJECT_ROOT / "app" / "audiobook_maintenance.py").read_text(encoding="utf-8")
    review_unit = (PROJECT_ROOT / "deploy" / "oohstory-audiobook-cast-review.service").read_text(encoding="utf-8")
    review_timer = (PROJECT_ROOT / "deploy" / "oohstory-audiobook-cast-review.timer").read_text(encoding="utf-8")

    assert "class VolatileAudiobookCache" in cache_script
    assert "sessionSegments = new Map" in cache_script
    assert "SESSION_SEGMENT_LIMIT = 5" in cache_script
    assert "clearPersistentStorage" in cache_script
    assert "LEGACY_CACHES" in cache_script
    assert "caches.open" not in cache_script
    assert "indexedDB.open" not in cache_script
    assert "rows.filter(row => row.complete)" not in cache_script
    assert "navigator.getBattery" in cache_script
    assert "/api/v1/audiobook/capacity" in cache_script
    assert "resume: Boolean(allowServerResume)" in app_script
    assert "payload.resume?.item_index" in app_script
    assert "payload.resume?.audio_offset_ms" in app_script
    assert "offset_ms=${encodeURIComponent" in app_script
    assert "/timeline" in app_script
    assert '(".complete", ".intent", ".cursor")' in maintenance
    assert 'counts["audio_jobs"] = _cleanup_audio_jobs()' in maintenance
    assert "CPUQuota=35%" in review_unit
    assert "MemoryMax=512M" in review_unit
    assert "OnUnitActiveSec=2min" in review_timer


def test_audiobook_v10_has_executable_claim_and_edge_path_guards() -> None:
    from app.audiobook_cast_review_worker import CLAIM_SQL, _reviewed_voice

    app_script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    cache_script = (PROJECT_ROOT / "static" / "audiobook-cache.js").read_text(encoding="utf-8")
    migration = (PROJECT_ROOT / "deploy" / "mysql-audiobook-runtime-v10.sql").read_text(encoding="utf-8")
    worker = (PROJECT_ROOT / "app" / "audiobook_cast_review_worker.py").read_text(encoding="utf-8")
    review_script = (PROJECT_ROOT / "scripts" / "review_audiobook_cast_with_openclaw.py").read_text(encoding="utf-8")
    main_source = (PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")

    assert "attempt_count<3)" not in CLAIM_SQL
    assert CLAIM_SQL.count("(") == CLAIM_SQL.count(")")
    assert '"attempt_count=0,last_error=NULL,"' in worker
    assert "voice_locked" in worker
    assert 'OOHSTORY_CAST_REVIEW_TRANSPORT", "local"' in review_script
    assert 'f"--{transport}"' in review_script
    assert '"GatewayTransportError"' in review_script
    assert "audiobook_device_progress" in migration
    assert "manifest_hash" in migration and "settings_hash" in migration
    assert "ttsStreamResumeBaseSeconds + relative" in app_script
    assert "const ttsResolvedStreamPlanIndex" in app_script
    assert "if (!item.durationExact) break" not in app_script
    assert "priorityStartIndex, priorityCount, maxSegments" in cache_script
    assert "selectedSegments(manifest" in cache_script
    assert "SESSION_SEGMENT_LIMIT" in cache_script
    assert "await request.is_disconnected()" in main_source
    assert "position % 3 == 0" in main_source
    assert _reviewed_voice(
        7,
        {"canonical_name": "林雪", "voice_key": "mocheng", "voice_locked": 1},
        "female",
    ) == "mocheng"
    assert _reviewed_voice(
        7,
        {"canonical_name": "林雪", "voice_key": "mocheng", "voice_locked": 0},
        "female",
    ) in FEMALE_VOICES


def test_audiobook_v11_scopes_sessions_to_devices_and_cache_objects() -> None:
    migration = (
        PROJECT_ROOT / "deploy" / "mysql-audiobook-runtime-v11.sql"
    ).read_text(encoding="utf-8")
    service = (PROJECT_ROOT / "app" / "audiobook.py").read_text(encoding="utf-8")
    main_source = (PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    cache = (PROJECT_ROOT / "static" / "audiobook-cache.js").read_text(encoding="utf-8")

    assert "device_hash" in migration
    assert "idx_audiobook_sessions_owner_device" in migration
    assert "def cancel_device" in service
    assert "INTERVAL 12 HOUR" in service
    assert "lease_lost.set()" in main_source
    assert "owner_task.cancel()" in main_source
    assert "const SEGMENT_STORE = 'segments'" not in cache
    assert "navigator.locks?.request" not in cache
    assert "evictUnlocked" not in cache
    assert "window.addEventListener('pagehide'" in cache
