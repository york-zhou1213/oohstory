from __future__ import annotations

import json
from pathlib import Path
import subprocess
from decimal import Decimal

import pytest

from app.audiobook import (
    AudiobookSession,
    AudiobookService,
    FEMALE_VOICES,
    MALE_VOICES,
    analyze_chapter,
)
from app.audiobook_policy import (
    AudiobookContractError,
    validate_manifest_contract,
    validate_manifest_payload,
    validate_voice_selection,
)
from app.audiobook_snapshot import (
    _assign_missing_confirmed_voice_rows,
    _clear_ai_rejected_voice_rows,
    _stable_snapshot_voices,
    _sync_snapshot_voice_rows,
    snapshot_rows,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOOK_ID = "A" * 22
SMART_SETTINGS = {
    "mode": "smart", "narrator": "mocheng", "voice": "nuanxi",
    "emotion": "auto", "rate": 1.0,
}


class ChapterRepository:
    _mysql = None

    def __init__(self, content: str):
        self.content = content

    def reader_chapter(self, _book_id: str, _chapter_id: int):
        return {"title": "契约", "content": self.content, "next_id": None}


def _published(name: str, voice: str, *, role: str = "supporting") -> dict:
    return {
        "canonical_name": name, "aliases": [name], "gender": "female",
        "gender_confidence": 0.99, "gender_source": "published_snapshot",
        "voice_key": voice, "voice_locked": 1, "role_type": role,
        "chapter_count": 10, "dialogue_count": 10,
    }


def test_manifest_contract_rejects_narrator_collision_and_cross_gender_voice() -> None:
    common = {
        "mode": "smart", "requested_narrator": "mocheng",
        "effective_narrator": "mocheng", "selected_voice": "nuanxi",
    }
    with pytest.raises(AudiobookContractError, match="narrator voice"):
        validate_manifest_contract(**common, segments=[{
            "kind": "dialogue", "speaker": "小梅", "voice": "mocheng",
            "gender": "female", "gender_confidence": 0.99,
        }])
    with pytest.raises(AudiobookContractError, match="female dialogue"):
        validate_manifest_contract(**common, segments=[{
            "kind": "dialogue", "speaker": "小梅", "voice": "qingyan",
            "gender": "female", "gender_confidence": 0.99,
        }])


def test_stored_manifest_contract_blocks_preload_cross_gender_voice() -> None:
    manifest = {
        "manifest_hash": "a" * 64,
        "mode": "smart",
        "selected_voice": "nuanxi",
        "requested_narrator": "mocheng",
        "effective_narrator": "mocheng",
        "segments": [{
            "index": 0,
            "kind": "dialogue",
            "speaker": "小梅",
            "gender": "female",
            "gender_confidence": 0.99,
            "voice": "qingyan",
            "sha256": "b" * 64,
            "text": "错声线。",
        }],
    }

    with pytest.raises(AudiobookContractError, match="female dialogue"):
        validate_manifest_payload(manifest, settings=SMART_SETTINGS)


def test_segment_preload_revalidates_attached_manifest_before_audio() -> None:
    manifest = {
        "manifest_hash": "c" * 64,
        "mode": "smart",
        "selected_voice": "nuanxi",
        "requested_narrator": "mocheng",
        "effective_narrator": "mocheng",
        "segments": [{
            "index": 0,
            "kind": "dialogue",
            "speaker": "张强",
            "gender": "male",
            "gender_confidence": 0.99,
            "voice": "nuanxi",
            "sha256": "d" * 64,
            "text": "错声线。",
        }],
    }
    service = AudiobookService(ChapterRepository(""))
    session = AudiobookSession(
        "s",
        "owner",
        {manifest["manifest_hash"]: manifest},
        current_chapter_id=1,
        settings=SMART_SETTINGS,
    )
    service._sessions[session.session_id] = session

    with pytest.raises(AudiobookContractError, match="male dialogue"):
        service.segment("s", manifest["manifest_hash"], 0, "owner")


def test_voice_registry_is_the_only_language_policy() -> None:
    validate_voice_selection(mode="smart", narrator="lingxian", voice="nuanxi")
    validate_voice_selection(mode="cantonese", narrator="lingxian", voice="wanqing")
    with pytest.raises(AudiobookContractError):
        validate_voice_selection(mode="smart", narrator="lingxian", voice="wanqing")


def test_playback_manifest_is_read_only_and_uses_only_published_cast() -> None:
    repository = ChapterRepository('小梅说道：“走吧。”')
    service = AudiobookService(repository)
    repository._mysql = object()  # production path: unverified actors are not ephemeral
    service._catalog_id = lambda _book_id: 7
    service._cast_revision = lambda _catalog_id: 3
    service._stored_manifest = lambda *_args: None
    service._persist_manifest = lambda _catalog_id, manifest: manifest
    service._existing_cast = lambda _catalog_id: [_published("小梅", "nuanxi")]
    service._cast = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("playback must never call the mutating cast writer")
    )

    manifest = service._manifest(BOOK_ID, 1, SMART_SETTINGS)
    dialogue = next(item for item in manifest["segments"] if item["kind"] == "dialogue")
    assert dialogue["speaker"] == "小梅"
    assert dialogue["voice"] in FEMALE_VOICES


def test_unpublished_identity_stays_anonymous_without_becoming_narration() -> None:
    repository = ChapterRepository('医生说道：“先别动。”')
    service = AudiobookService(repository)
    repository._mysql = object()
    service._catalog_id = lambda _book_id: 8
    service._cast_revision = lambda _catalog_id: 0
    service._stored_manifest = lambda *_args: None
    service._persist_manifest = lambda _catalog_id, manifest: manifest
    service._existing_cast = lambda _catalog_id: []

    manifest = service._manifest(BOOK_ID, 1, SMART_SETTINGS)
    dialogue = next(item for item in manifest["segments"] if item["kind"] == "dialogue")
    assert dialogue["speaker"] == ""
    assert dialogue["identity_candidate_rejected"] is True
    assert dialogue["voice"] != "mocheng"


def test_rejected_local_roles_keep_same_gender_voice_pools() -> None:
    repository = ChapterRepository(
        "小门童面色微红：“这里是林府，道姑有何事？”\n"
        "“我们找三老爷。”小道童说着回头看了一眼。\n"
        "小门童不由得也随着她的视线往后看。"
    )
    service = AudiobookService(repository)
    repository._mysql = object()
    service._catalog_id = lambda _book_id: 81
    service._cast_revision = lambda _catalog_id: 0
    service._stored_manifest = lambda *_args: None
    service._persist_manifest = lambda _catalog_id, manifest: manifest
    service._existing_cast = lambda _catalog_id: []

    manifest = service._manifest(BOOK_ID, 1, SMART_SETTINGS)
    dialogue = [
        item for item in manifest["segments"] if item["kind"] == "dialogue"
    ]

    assert [item["speaker"] for item in dialogue] == ["", ""]
    assert all(item["identity_candidate_rejected"] is True for item in dialogue)
    assert dialogue[0]["gender"] == "male"
    assert dialogue[0]["voice"] in MALE_VOICES
    assert dialogue[1]["gender"] == "female"
    assert dialogue[1]["voice"] in FEMALE_VOICES
    assert all(item["voice"] != "mocheng" for item in dialogue)


def test_unknown_colon_role_is_preserved_as_narration() -> None:
    _characters, segments = analyze_chapter("责任编辑：先别动。")
    assert len(segments) == 1
    assert segments[0]["kind"] == "narration"
    assert segments[0]["speaker"] == ""
    assert segments[0]["text"] == "责任编辑:先别动。"


def test_voice_mapping_is_independent_of_first_speaking_order() -> None:
    cast = [_published("小梅", "nuanxi"), _published("云知意", "nuanxi", role="protagonist")]

    def mapping(content: str) -> dict[str, str]:
        service = AudiobookService(ChapterRepository(content))
        service._catalog_id = lambda _book_id: 9
        service._existing_cast = lambda _catalog_id: cast
        manifest = service._manifest(BOOK_ID, 1, SMART_SETTINGS)
        return {
            item["speaker"]: item["voice"] for item in manifest["segments"]
            if item["kind"] == "dialogue" and item["speaker"]
        }

    first = mapping('小梅说道：“甲。”\n云知意说道：“乙。”')
    second = mapping('云知意说道：“乙。”\n小梅说道：“甲。”')
    assert first == second
    assert len(set(first.values())) == 2


def test_snapshot_voice_assignment_is_stable_and_never_crosses_gender_pool() -> None:
    rows = [
        _published(name, "nuanxi", role="supporting")
        for name in ("小梅", "云知意", "芮小丹", "肖亚文", "欧阳雪", "林雨", "苏晴")
    ]
    reversed_rows = [dict(row) for row in reversed(rows)]
    _stable_snapshot_voices(10, rows)
    _stable_snapshot_voices(10, reversed_rows)
    first = {row["canonical_name"]: row["voice_key"] for row in rows}
    second = {row["canonical_name"]: row["voice_key"] for row in reversed_rows}
    assert first == second
    assert set(first.values()) <= set(FEMALE_VOICES)


def test_snapshot_rows_normalize_real_mysql_decimal_values() -> None:
    class Cursor:
        def execute(self, _sql, _params):
            return None

        def fetchall(self):
            return [{
                **_published("小梅", "nuanxi"),
                "character_key": b"x" * 32,
                "gender_confidence": Decimal("0.9900"),
                "age_confidence": Decimal("0.5000"),
                "tone_confidence": Decimal("0.8000"),
                "ai_review_confidence": Decimal("0.9100"),
                "ai_review_gender": "female",
                "ai_review_role_type": "supporting",
            }]

    rows = snapshot_rows(Cursor(), catalog_id=1, content_revision=b"r" * 32)
    assert rows[0]["gender_confidence"] == 0.91
    json.dumps(rows)


def test_snapshot_rows_uses_latest_ai_review_and_clears_unknown_verdict() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.sql = ""

        def execute(self, sql, _params):
            self.sql = sql

        def fetchall(self):
            return [{
                **_published("淡淡", "qinghe"),
                "character_key": b"d" * 32,
                "gender_confidence": Decimal("1.0000"),
                "voice_locked": 1,
                "ai_review_confidence": Decimal("0.9900"),
                "ai_review_gender": "unknown",
                "ai_review_role_type": "cameo",
            }]

    cursor = Cursor()
    rows = snapshot_rows(cursor, catalog_id=1, content_revision=b"r" * 32)

    assert "ORDER BY r2.created_at DESC LIMIT 1" in cursor.sql
    assert "AND r.scan_revision=p.scan_revision" not in cursor.sql
    assert rows[0]["gender"] == "unknown"
    assert rows[0]["gender_confidence"] == 0.99
    assert rows[0]["voice_key"] == ""
    assert rows[0]["voice_locked"] == 0
    assert rows[0]["role_type"] == "cameo"


def test_snapshot_voice_assignment_is_written_back_to_mysql_fingerprint() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        def execute(self, sql, params):
            self.calls.append((sql, params))

    row = {
        **_published("小梅", ""),
        "character_key": "aa" * 32,
        "gender": "female",
        "gender_confidence": 0.99,
        "voice_locked": 0,
    }
    _stable_snapshot_voices(11, [row])
    cursor = Cursor()

    _sync_snapshot_voice_rows(cursor, catalog_id=11, rows=[row])

    assert row["voice_key"] in FEMALE_VOICES
    assert row["voice_locked"] == 1
    assert cursor.calls
    assert "UPDATE audiobook_character_voices SET gender=%s" in cursor.calls[0][0]
    assert cursor.calls[0][1][2] == row["voice_key"]


def test_snapshot_sync_clears_ai_rejected_character_fingerprint() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        def execute(self, sql, params):
            self.calls.append((sql, params))

    row = {
        **_published("淡淡", "qinghe"),
        "character_key": "dd" * 32,
        "gender": "unknown",
        "gender_confidence": 0.99,
        "voice_locked": 0,
        "ai_review_gender": "unknown",
        "ai_review_confidence": 0.99,
    }
    cursor = Cursor()

    _sync_snapshot_voice_rows(cursor, catalog_id=11, rows=[row])

    assert cursor.calls
    assert "SET gender='unknown'" in cursor.calls[0][0]
    assert row["voice_key"] == ""
    assert row["voice_locked"] == 0


def test_snapshot_publish_clears_stale_ai_rejected_fingerprints() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []
            self._selected = False

        def execute(self, sql, params):
            self.calls.append((sql, params))
            if sql.startswith("SELECT HEX(v.character_key)"):
                self._selected = True

        def fetchall(self):
            if self._selected:
                self._selected = False
                return [{"character_key": "ee" * 32, "confidence": Decimal("0.9900")}]
            return []

    cursor = Cursor()

    _clear_ai_rejected_voice_rows(cursor, catalog_id=11)

    assert any("ORDER BY r2.created_at DESC LIMIT 1" in call[0] for call in cursor.calls)
    update = [call for call in cursor.calls if call[0].startswith("UPDATE audiobook_character_voices")]
    assert update
    assert update[0][1] == (0.99, 11, "ee" * 32)


def test_snapshot_publish_assigns_missing_confirmed_fingerprint_voice() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []
            self._selected = False

        def execute(self, sql, params):
            self.calls.append((sql, params))
            if sql.startswith("SELECT HEX(v.character_key)"):
                self._selected = True

        def fetchall(self):
            if self._selected:
                self._selected = False
                return [{
                    "character_key": "ff" * 32,
                    "canonical_name": "夏秀臣",
                    "gender": "female",
                }]
            return []

    cursor = Cursor()

    _assign_missing_confirmed_voice_rows(cursor, catalog_id=11)

    update = [call for call in cursor.calls if call[0].startswith("UPDATE audiobook_character_voices")]
    assert update
    assert update[0][1][0] in FEMALE_VOICES
    assert update[0][1][1:] == (11, "ff" * 32)


def test_frontend_lifecycle_rejects_invalid_transitions_and_preserves_user_pause() -> None:
    lifecycle_path = json.dumps(str(PROJECT_ROOT / "static" / "audiobook-lifecycle.js"))
    script = f"""
require({lifecycle_path});
const machine = globalThis.OOHStoryAudiobookLifecycle.create();
for (let round = 0; round < 4; round++) {{
  machine.start(); machine.connect(); machine.playing(); machine.pause();
  if (!machine.isPausedByUser()) throw new Error('pause lost');
  machine.resume(); machine.playing(); machine.stop(); machine.finish();
}}
let rejected = false;
try {{ machine.playing(); }} catch (_) {{ rejected = true; }}
if (!rejected || machine.snapshot().state !== 'idle') throw new Error('invalid transition accepted');
machine.start(); machine.pause();
if (machine.snapshot().state !== 'paused_by_user') throw new Error('starting pause lost');
machine.stop(); machine.finish();
"""
    completed = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_frontend_cancels_late_session_and_never_auto_resumes_user_pause() -> None:
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "if (payload.session_id) fetch(`/api/v1/audiobook/sessions/${payload.session_id}`" in script
    stop_start = script.index("  const stopTTS =")
    stop_end = script.index("\n\n  const ttsFirstVisibleParagraph", stop_start)
    stop_source = script[stop_start:stop_end]
    assert "if (closingSessionId) audiobookAbortController?.abort()" in stop_source
    assert "const closingSessionId = audiobookServerSessionId\n      audiobookAbortController?.abort()" not in stop_source
    assert script.count("!ttsLifecycle.isPausedByUser()") >= 3
    assert "const ttsPlaybackBlocked" not in script
    assert "let ttsPlaybackConnecting" not in script
