from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app import main


def test_every_tts_voice_exposes_at_least_ten_emotion_modes() -> None:
    payload = main.tts_voices()
    emotion_keys = {item["key"] for item in payload["emotions"]}

    assert len(emotion_keys) >= 10
    assert len(main.TTS_EMOTIONS) >= 10
    assert {"neutral", "gentle", "joyful", "excited", "angry", "sad"} <= emotion_keys
    assert {
        "surprised", "comforting", "confident", "shy", "disgusted", "whispering"
    } <= emotion_keys
    voice_keys = {item["key"] for item in payload["voices"]}
    assert {"qinghe", "jinglan", "yunzhou", "junchuan"}.isdisjoint(voice_keys)
    multilingual = {
        item["key"]: item for item in payload["voices"]
        if item["key"] in {"ava", "emma", "andrew", "brian"}
    }
    assert set(multilingual) == {"ava", "emma", "andrew", "brian"}
    assert {item["language"] for item in multilingual.values()} == {"zh-CN"}
    assert {
        item["emotion_tuning"] for item in multilingual.values()
    } == {"edge-multilingual-chinese"}
    assert all(
        item.get("provider") != main.LOCAL_TTS_PROVIDER
        for item in payload["voices"]
    )
    for voice in payload["voices"]:
        assert voice["emotion_count"] == len(emotion_keys)
        assert set(voice["emotions"]) == emotion_keys


def test_tts_emotion_profile_adjusts_rate_pitch_and_volume(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeCommunicate:
        def __init__(self, text: str, voice: str, **kwargs: str) -> None:
            captured.update(text=text, voice=voice, **kwargs)

        async def stream(self):
            yield {"type": "audio", "data": b"ID3-emotional-audio"}

    monkeypatch.setattr(main.edge_tts, "Communicate", FakeCommunicate)
    audio, emotion = asyncio.run(
        main._synthesize_tts_bytes(
            "你给我站住！",
            "nuanxi",
            "+5%",
            "+2Hz",
            "+1%",
            "angry",
        )
    )

    assert audio == b"ID3-emotional-audio"
    assert emotion == "angry"
    assert captured == {
        "text": "你给我站住！",
        "voice": main.TTS_VOICES["nuanxi"]["id"],
        "rate": "+17%",
        "pitch": "-1Hz",
        "volume": "+11%",
    }


def test_multilingual_edge_voices_use_chinese_emotion_tuning(monkeypatch) -> None:
    calls: list[dict[str, str]] = []

    class FakeCommunicate:
        def __init__(self, text: str, voice: str, **kwargs: str) -> None:
            calls.append({"text": text, "voice": voice, **kwargs})

        async def stream(self):
            yield {"type": "audio", "data": b"ID3-multilingual-emotion"}

    monkeypatch.setattr(main.edge_tts, "Communicate", FakeCommunicate)

    samples = [
        ("ava", "angry", "+5%", "+2Hz", "+1%", "+12%", "-3Hz", "+7%"),
        ("emma", "comforting", "+0%", "+0Hz", "+0%", "-12%", "-3Hz", "-7%"),
        ("andrew", "confident", "+0%", "+0Hz", "+0%", "-3%", "-6Hz", "+6%"),
        ("brian", "whispering", "+0%", "+0Hz", "+0%", "-19%", "-10Hz", "-15%"),
    ]
    for voice, emotion, rate, pitch, volume, *_expected in samples:
        audio, actual_emotion = asyncio.run(
            main._synthesize_tts_bytes(
                "这里是林府，道姑有何事？",
                voice,
                rate,
                pitch,
                volume,
                emotion,
            )
        )
        assert audio == b"ID3-multilingual-emotion"
        assert actual_emotion == emotion

    for call, sample in zip(calls, samples, strict=True):
        voice, _emotion, _rate, _pitch, _volume, expected_rate, expected_pitch, expected_volume = sample
        assert call == {
            "text": "这里是林府，道姑有何事？",
            "voice": main.TTS_VOICES[voice]["id"],
            "rate": expected_rate,
            "pitch": expected_pitch,
            "volume": expected_volume,
        }


def test_tts_rejects_unknown_emotion_mode() -> None:
    response = TestClient(main.app).get("/api/v1/tts/speak?text=不得进入日志")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_hidden_legacy_local_mandarin_voice_uses_offline_provider(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_local(text: str, **kwargs: object) -> bytes:
        captured.update(text=text, **kwargs)
        return b"ID3-local-mandarin"

    monkeypatch.setattr(main, "_synthesize_local_tts_bytes", fake_local)
    assert main.TTS_VOICES["qinghe"]["hidden"] is True
    audio, emotion = asyncio.run(
        main._synthesize_tts_bytes(
            "别怕，我会陪你回家。",
            "qinghe",
            "+0%",
            "+0Hz",
            "+0%",
            "comforting",
        )
    )

    assert audio == b"ID3-local-mandarin"
    assert emotion == "comforting"
    assert captured == {
        "text": "别怕，我会陪你回家。",
        "speaker_id": 33,
        "speed": 0.88,
        "pitch_hz": 1,
        "volume_percent": -5,
    }
