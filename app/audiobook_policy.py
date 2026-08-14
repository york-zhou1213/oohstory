from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


CAST_ENGINE_VERSION = "oohstory-cast-v15-colonfield1"
POLICY_VERSION = "audiobook-contract-v5-edge-multilingual-emotion"
LOCAL_TTS_PROVIDER = "sherpa-aishell3"

TTS_VOICES: dict[str, dict[str, Any]] = {
    "nuanxi": {
        "id": "zh-CN-XiaoxiaoNeural", "label": "暖溪", "gender": "female",
        "desc": "温婉知性",
    },
    "lingxian": {
        "id": "zh-CN-XiaoyiNeural", "label": "灵弦", "gender": "female",
        "desc": "灵动俏皮",
    },
    "shuanger": {
        "id": "zh-CN-liaoning-XiaobeiNeural", "label": "霜儿", "gender": "female",
        "desc": "爽朗飒然",
    },
    "yanzhi": {
        "id": "zh-CN-shaanxi-XiaoniNeural", "label": "燕知", "gender": "female",
        "desc": "清亮质朴",
    },
    "ava": {
        "id": "en-US-AvaMultilingualNeural", "label": "艾娃",
        "gender": "female", "language": "zh-CN",
        "desc": "微软多语种 · 明亮亲和",
    },
    "emma": {
        "id": "en-US-EmmaMultilingualNeural", "label": "艾玛",
        "gender": "female", "language": "zh-CN",
        "desc": "微软多语种 · 清晰轻快",
    },
    "qinghe": {
        "provider": LOCAL_TTS_PROVIDER, "speaker_id": 33, "label": "清禾",
        "gender": "female", "desc": "明澈柔韧 · 本地普通话",
        "hidden": True,
    },
    "jinglan": {
        "provider": LOCAL_TTS_PROVIDER, "speaker_id": 0, "label": "静澜",
        "gender": "female", "desc": "沉静柔和 · 本地普通话",
        "hidden": True,
    },
    "wanqing": {
        "id": "zh-HK-HiuGaaiNeural", "label": "晚晴", "gender": "female",
        "desc": "柔婉细腻",
    },
    "muyao": {
        "id": "zh-HK-HiuMaanNeural", "label": "沐瑶", "gender": "female",
        "desc": "端庄优雅",
    },
    "qianyu": {
        "id": "zh-TW-HsiaoChenNeural", "label": "浅语", "gender": "female",
        "desc": "温润恬静",
    },
    "ruoxi": {
        "id": "zh-TW-HsiaoYuNeural", "label": "若汐", "gender": "female",
        "desc": "甜美亲和",
    },
    "kuangyun": {
        "id": "zh-CN-YunjianNeural", "label": "旷云", "gender": "male",
        "desc": "热血豪迈",
    },
    "qingyan": {
        "id": "zh-CN-YunxiNeural", "label": "清砚", "gender": "male",
        "desc": "少年朗逸",
    },
    "tongzhen": {
        "id": "zh-CN-YunxiaNeural", "label": "童真", "gender": "male",
        "desc": "稚气天真",
    },
    "mocheng": {
        "id": "zh-CN-YunyangNeural", "label": "墨澄", "gender": "male",
        "desc": "沉稳儒雅",
    },
    "andrew": {
        "id": "en-US-AndrewMultilingualNeural", "label": "安德鲁",
        "gender": "male", "language": "zh-CN",
        "desc": "微软多语种 · 温和笃定",
    },
    "brian": {
        "id": "en-US-BrianMultilingualNeural", "label": "布莱恩",
        "gender": "male", "language": "zh-CN",
        "desc": "微软多语种 · 沉稳自然",
    },
    "yunzhou": {
        "provider": LOCAL_TTS_PROVIDER, "speaker_id": 10, "label": "云舟",
        "gender": "male", "desc": "清朗坚定 · 本地普通话",
        "hidden": True,
    },
    "junchuan": {
        "provider": LOCAL_TTS_PROVIDER, "speaker_id": 75, "label": "峻川",
        "gender": "male", "desc": "浑厚沉着 · 本地普通话",
        "hidden": True,
    },
    "yueming": {
        "id": "zh-HK-WanLungNeural", "label": "岳鸣", "gender": "male",
        "desc": "浑厚磁性",
    },
    "hanfeng": {
        "id": "zh-TW-YunJheNeural", "label": "寒枫", "gender": "male",
        "desc": "清冷内敛",
    },
}


def _is_mandarin_cast_voice(item: Mapping[str, Any]) -> bool:
    return bool(
        not item.get("hidden")
        and (
            str(item.get("id") or "").startswith("zh-CN-")
            or str(item.get("language") or "") == "zh-CN"
        )
    )


FEMALE_VOICES = tuple(
    key for key, item in TTS_VOICES.items()
    if item["gender"] == "female"
    and _is_mandarin_cast_voice(item)
)
MALE_VOICES = tuple(
    key for key, item in TTS_VOICES.items()
    if item["gender"] == "male"
    and _is_mandarin_cast_voice(item)
)
CAST_VOICES = FEMALE_VOICES + MALE_VOICES
MODE_LANGUAGE = {
    "normal": "zh-CN", "smart": "zh-CN", "cantonese": "zh-HK",
    "hokkien": "zh-TW",
}


class AudiobookContractError(RuntimeError):
    """Raised before an invalid audiobook decision can be cached or played."""


@dataclass(frozen=True)
class ManifestContractReport:
    named_speakers: int
    dialogue_voices: int
    narration_segments: int
    dialogue_segments: int


def voice_language(key: str) -> str:
    item = TTS_VOICES.get(key)
    if item is None:
        return ""
    if item.get("language"):
        return str(item.get("language") or "")
    if item.get("provider") == LOCAL_TTS_PROVIDER:
        return "zh-CN"
    return str(item.get("id") or "")[:5]


def validate_voice_selection(*, mode: str, narrator: str, voice: str) -> None:
    expected_language = MODE_LANGUAGE.get(mode)
    if expected_language is None:
        raise AudiobookContractError("unsupported audiobook mode")
    if narrator not in TTS_VOICES or voice not in TTS_VOICES:
        raise AudiobookContractError("unsupported audiobook voice")
    if TTS_VOICES[narrator].get("hidden") or TTS_VOICES[voice].get("hidden"):
        raise AudiobookContractError("audiobook voice is not selectable")
    if voice_language(narrator) != "zh-CN":
        raise AudiobookContractError("narrator must use a Mandarin voice")
    if voice_language(voice) != expected_language:
        raise AudiobookContractError("selected voice does not match audiobook mode")


def validate_manifest_contract(
    *,
    mode: str,
    requested_narrator: str,
    effective_narrator: str,
    selected_voice: str,
    segments: Sequence[Mapping[str, Any]],
) -> ManifestContractReport:
    """Enforce playback invariants at the final manifest boundary.

    Parsing, full-book scanning, AI review and cached cast data may all be
    imperfect.  None of them may bypass these conditions when producing an
    immutable playback manifest.
    """
    validate_voice_selection(
        mode=mode, narrator=requested_narrator, voice=selected_voice,
    )
    if effective_narrator != requested_narrator:
        raise AudiobookContractError("effective narrator differs from request")

    named_voices: dict[str, str] = {}
    named_genders: dict[str, str] = {}
    narration_segments = 0
    dialogue_segments = 0
    dialogue_voices: set[str] = set()
    for segment in segments:
        kind = str(segment.get("kind") or "narration")
        segment_voice = str(segment.get("voice") or "")
        speaker = str(segment.get("speaker") or "").strip()
        if segment_voice not in TTS_VOICES:
            raise AudiobookContractError("manifest contains an unsupported voice")
        if mode != "smart":
            if segment_voice != selected_voice:
                raise AudiobookContractError("single-voice manifest changed voice")
            continue
        if kind != "dialogue":
            narration_segments += 1
            if segment_voice != effective_narrator:
                raise AudiobookContractError("narration changed the requested voice")
            continue
        dialogue_segments += 1
        dialogue_voices.add(segment_voice)
        if segment_voice == effective_narrator:
            raise AudiobookContractError("dialogue reused the narrator voice")
        if bool(segment.get("identity_candidate_rejected")) and speaker:
            raise AudiobookContractError("rejected identity remained bound")
        gender = str(segment.get("gender") or "unknown")
        if gender == "female" and segment_voice not in FEMALE_VOICES:
            raise AudiobookContractError("female dialogue used a male voice")
        if gender == "male" and segment_voice not in MALE_VOICES:
            raise AudiobookContractError("male dialogue used a female voice")
        if not speaker:
            continue
        previous = named_voices.setdefault(speaker, segment_voice)
        named_genders.setdefault(speaker, gender)
        if previous != segment_voice:
            raise AudiobookContractError("named speaker changed voice inside a manifest")

    for gender, pool in (("female", FEMALE_VOICES), ("male", MALE_VOICES)):
        gender_speakers = {
            speaker: named_voices[speaker]
            for speaker, value in named_genders.items()
            if value == gender
        }
        available_dialogue_voices = len(pool) - int(effective_narrator in pool)
        if len(gender_speakers) > available_dialogue_voices:
            continue
        reverse: dict[str, str] = {}
        for speaker, voice in gender_speakers.items():
            previous_speaker = reverse.setdefault(voice, speaker)
            if previous_speaker != speaker:
                raise AudiobookContractError("named speakers share a voice despite capacity")
    return ManifestContractReport(
        named_speakers=len(named_voices), dialogue_voices=len(dialogue_voices),
        narration_segments=narration_segments, dialogue_segments=dialogue_segments,
    )


def _settings_value(settings: Mapping[str, Any] | None, key: str) -> str:
    if not isinstance(settings, Mapping):
        return ""
    return str(settings.get(key) or "")


def _infer_manifest_mode(
    segments: Sequence[Mapping[str, Any]], selected_voice: str
) -> str:
    voices = {
        str(segment.get("voice") or "")
        for segment in segments
        if str(segment.get("voice") or "")
    }
    if len(voices) > 1:
        return "smart"
    selected_language = voice_language(selected_voice)
    for mode, language in MODE_LANGUAGE.items():
        if mode != "smart" and language == selected_language:
            return mode
    return "normal"


def _infer_selected_voice(
    segments: Sequence[Mapping[str, Any]], effective_narrator: str
) -> str:
    for segment in segments:
        voice = str(segment.get("voice") or "")
        if voice:
            return voice
    return effective_narrator


def validate_manifest_payload(
    manifest: Mapping[str, Any],
    *,
    settings: Mapping[str, Any] | None = None,
) -> ManifestContractReport:
    """Validate a stored/attached manifest before any audio is generated.

    Fresh manifests carry their mode and selected voice explicitly. Older
    session-attached manifests can still be checked against the immutable
    session settings so stale bad manifests fail closed instead of being
    preloaded into the client cache.
    """
    raw_segments = manifest.get("segments")
    if not isinstance(raw_segments, Sequence) or isinstance(raw_segments, (str, bytes)):
        raise AudiobookContractError("manifest segments are invalid")
    segments = []
    for segment in raw_segments:
        if not isinstance(segment, Mapping):
            raise AudiobookContractError("manifest segment is invalid")
        segments.append(segment)

    requested_narrator = (
        str(manifest.get("requested_narrator") or "")
        or _settings_value(settings, "narrator")
    )
    effective_narrator = (
        str(manifest.get("effective_narrator") or "")
        or requested_narrator
    )
    selected_voice = (
        str(manifest.get("selected_voice") or "")
        or _settings_value(settings, "voice")
        or _infer_selected_voice(segments, effective_narrator)
    )
    mode = (
        str(manifest.get("mode") or "")
        or _settings_value(settings, "mode")
        or _infer_manifest_mode(segments, selected_voice)
    )
    if not requested_narrator:
        requested_narrator = effective_narrator or selected_voice
    if not effective_narrator:
        effective_narrator = requested_narrator
    return validate_manifest_contract(
        mode=mode,
        requested_narrator=requested_narrator,
        effective_narrator=effective_narrator,
        selected_voice=selected_voice,
        segments=segments,
    )
