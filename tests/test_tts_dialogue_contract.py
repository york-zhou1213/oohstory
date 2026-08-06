from __future__ import annotations

import json
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _dialogue_probe() -> dict[str, bool]:
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    start = script.index("  const ttsAllQuoteRe =")
    end = script.index("\n\n  const ttsNonNameWords", start)
    source = script[start:end]
    cases = [
        "“你终于来了。”",
        '"你终于来了。"',
        "「你终于来了。」",
        "『你终于来了。』",
        "【你终于来了。】",
        "[你终于来了。]",
        "［你终于来了。］",
        "林夏：你终于来了。",
        "林夏: 你终于来了。",
        "雨还在下，街上没有人。",
    ]
    probe = (
        source
        + "\nconsole.log(JSON.stringify(Object.fromEntries("
        + json.dumps(cases, ensure_ascii=False)
        + ".map(line => [line, ttsIsDialogueLine(line)]))))"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", probe],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_web_smart_tts_recognizes_supported_dialogue_forms() -> None:
    results = _dialogue_probe()

    assert all(results[line] for line in list(results)[:-1])
    assert results["雨还在下，街上没有人。"] is False


def test_web_tts_rebuilds_active_plan_and_versions_prefetch() -> None:
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "const ttsSettingsSignature = () => JSON.stringify" in script
    assert "signature !== ttsSettingsSignature()" in script
    assert "ttsNextChapterSignature = signature" in script
    assert "generation !== ttsPlanGeneration" in script
    assert script.count("ttsScheduleRebuild()") >= 4
    assert "state.ttsController.stop({ preservePending: true })" in script


def test_web_tts_keeps_ios_audio_unlock_during_hot_switch() -> None:
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    play_start = script.index("  const ttsPlayItem =")
    play_end = script.index("\n\n  const ttsPrefetchNextChapter", play_start)
    play_source = script[play_start:play_end]
    rebuild_start = script.index("  const ttsRebuildActivePlan =")
    rebuild_end = script.index("\n\n  const startTTS =", rebuild_start)
    rebuild_source = script[rebuild_start:rebuild_end]

    assert "audio.src = item.url" in play_source
    assert "await ttsGetBlobUrl" not in play_source
    assert "ttsStopPlayback()" not in rebuild_source
    assert "if (audio.paused || audio.ended || !audio.src || ttsPlaybackBlocked)" in rebuild_source
    assert "if (ttsRebuildRequested)" in play_source


def test_web_tts_does_not_skip_safari_policy_rejections() -> None:
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "['NotAllowedError', 'AbortError']" in script
    assert "ttsMarkPlaybackBlocked(error)" in script
    assert "点击继续智能听书" in script
    assert "else if (ttsPlaybackBlocked) ttsResumePlayback()" in script
    assert "onclick: openTTS" in script


def test_web_tts_does_not_skip_dialogue_on_audio_error() -> None:
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    play_start = script.index("  const ttsPlayItem =")
    play_end = script.index("\n\n  const ttsPrefetchNextChapter", play_start)
    play_source = script[play_start:play_end]
    failure_start = play_source.index("    const advanceAfterFailure =")
    failure_end = play_source.index("\n    audio.onended =", failure_start)
    failure_source = play_source[failure_start:failure_end]

    assert "音频加载失败，点击重试" in failure_source
    assert "ttsPlayItem(idx + 1)" not in failure_source
    assert "URL.createObjectURL" not in script[script.index("  const ttsCachePrefetch ="):play_start]


def test_web_tts_rotates_unlabelled_dialogue_voices() -> None:
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    plan_start = script.index("  const ttsBuildChapterPlan =")
    plan_end = script.index("\n\n  const TTS_PREFETCH_AHEAD", plan_start)
    plan_source = script[plan_start:plan_end]

    assert "const gender = ttsInferContextGender(line)" in plan_source
    assert "gender === 'female' ? ttsFemalePool" in plan_source
    assert "gender === 'male' ? ttsMalePool" in plan_source
    assert "lastSpeakerVoice" not in plan_source


def test_web_tts_keeps_a_ten_item_sliding_prefetch_window() -> None:
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "const TTS_PREFETCH_AHEAD = 10" in script
    assert "ttsChapterPlan.slice(fromIdx, fromIdx + TTS_PREFETCH_AHEAD)" in script
    assert "TTS_PREFETCH_AHEAD - windowItems.length" in script
    assert "if (!keepUrls.has(url)) ttsCache.delete(url)" in script


def test_web_tts_can_detach_and_return_without_stopping_audio() -> None:
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert 'id="global-tts-return"' in html
    assert ".global-tts-return" in css
    assert "state.ttsController.detach()" in script
    assert "state.ttsController?.attach?.(index =>" in script
    assert "navigateInApp(session.returnPath" in script
    assert "ttsSessionIsPlaying(bookId)" in script


def test_web_tts_opens_a_dedicated_mobile_player_without_replacing_audio() -> None:
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert 'id="tts-player"' in html
    assert 'id="tts-player-toggle"' in html
    assert 'id="tts-player-rate"' in html
    assert 'id="tts-player-mode"' in html
    assert ".tts-player" in css
    assert "bottom: max(82px, calc(env(safe-area-inset-bottom) + 78px));" in css
    assert "function openTtsPlayer()" in script
    assert "globalTtsReturn?.addEventListener('click', openTtsPlayer)" in script
    assert "pause: () =>" in script
    assert "previous: () =>" in script
    assert "next: () =>" in script
    assert "ttsPlayerStop?.addEventListener" in script
    assert script.count("let ttsAudioEl = null") == 1


def test_web_tts_supports_auto_and_manual_emotion_modes() -> None:
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert script.count("desc: '") >= 13
    assert "const ttsEmotionForText = text => state.reader.ttsEmotion === 'auto'" in script
    assert "new URLSearchParams({ text: cleaned, voice, rate, emotion })" in script
    assert "emotion: state.reader.ttsEmotion" in script
    assert "setEmotion: value =>" in script
    assert 'id="tts-player-emotion"' in html
    assert 'id="tts-emotion-sheet"' in html
    assert ".tts-emotion-options" in css
    assert "情感阅读" in script


def test_web_mobile_tts_starts_and_tracks_from_top_visible_line() -> None:
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "sort((a, b) => a.rect.top - b.rect.top)" in script
    assert "visible[0].paragraph.dataset.ttsIndex" in script
    assert "window.matchMedia('(max-width: 720px)').matches ? 'start' : 'center'" in script


def test_web_tts_discards_stale_prefetch_results_after_rebuild() -> None:
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "let ttsCacheEpoch = 0" in script
    assert "epoch !== ttsCacheEpoch || ttsCachePromises.get(url) !== p" in script
    assert "ttsCacheEpoch++" in script


def test_web_tts_splits_long_paragraphs_without_losing_paragraph_identity() -> None:
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "const TTS_REQUEST_CHAR_LIMIT = 900" in script
    assert "const ttsSplitForRequest = text =>" in script
    assert "ttsAppendPlan(plan, line, state.reader.ttsVoice, i)" in script
    assert "ttsAppendPlan(plan, seg.text, seg.voice, i)" in script


def test_web_tts_preserves_unlocked_audio_across_automatic_chapter_route() -> None:
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    load_reader = script.index("async function loadReader")
    stop_start = script.index("  const stopTTS =")
    stop_end = script.index("\n\n  const ttsFirstVisibleParagraph", stop_start)
    stop_source = script[stop_start:stop_end]

    assert script.index("let ttsAudioEl = null") < load_reader
    assert script.count("let ttsAudioEl = null") == 1
    assert "if (preservePending)" in stop_source
    assert "ttsAudioEl.onended = null" in stop_source
    assert "ttsAudioEl.onerror = null" in stop_source
    assert "else {\n      ttsStopPlayback()" in stop_source
    assert "state.ttsController.stop({ preservePending: true })" in script


def test_web_tts_adopts_prefetched_chapter_without_stopping_the_player() -> None:
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    chapter_end_start = script.index("  const ttsChapterEnd = async () =>")
    chapter_end_end = script.index("\n\n  const ttsUpdateMediaSession", chapter_end_start)
    chapter_end = script[chapter_end_start:chapter_end_end]

    assert "ttsChapterPlan = ttsNextChapterPlan" in chapter_end
    assert "ttsPlayItem(0)" in chapter_end
    assert "navigateInApp(contextualHref" in chapter_end
    assert "state.ttsContinueOnLoad = false" in chapter_end
    assert "state.ttsController.stop" not in chapter_end
    assert "const TTS_CHECKPOINT_STORAGE_KEY" in script
    assert "saveTtsCheckpoint(state.ttsSession)" in script
    assert "class: 'interline-action tts'" in script
    assert "text: '从此处听书'" in script
