from __future__ import annotations

from fastapi.testclient import TestClient

from app import main


def test_every_tts_voice_exposes_at_least_ten_emotion_modes() -> None:
    payload = main.tts_voices()
    emotion_keys = {item["key"] for item in payload["emotions"]}

    assert len(emotion_keys) >= 10
    assert len(main.TTS_EMOTIONS) >= 10
    assert {"neutral", "gentle", "joyful", "excited", "angry", "sad"} <= emotion_keys
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
    client = TestClient(main.app)
    response = client.get(
        "/api/v1/tts/speak",
        params={
            "text": "你给我站住！",
            "voice": "nuanxi",
            "rate": "+5%",
            "pitch": "+2Hz",
            "volume": "+1%",
            "emotion": "angry",
        },
    )

    assert response.status_code == 200
    assert response.content == b"ID3-emotional-audio"
    assert response.headers["x-tts-emotion"] == "angry"
    assert captured == {
        "text": "你给我站住！",
        "voice": main.TTS_VOICES["nuanxi"]["id"],
        "rate": "+17%",
        "pitch": "-1Hz",
        "volume": "+11%",
    }


def test_tts_rejects_unknown_emotion_mode() -> None:
    response = TestClient(main.app).get(
        "/api/v1/tts/speak",
        params={"text": "测试", "voice": "nuanxi", "emotion": "unknown"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "不支持的情感模式"
