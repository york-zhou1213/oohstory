from __future__ import annotations

from tests.frontend_contract_source import frontend_contract_source
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_web_tts_rebuilds_active_plan_and_versions_prefetch() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "const ttsSettingsSignature = () => JSON.stringify" in script
    assert "const ttsBackendManifest = async (startParagraph, allowServerResume = true) =>" in script
    lifecycle = (PROJECT_ROOT / "static" / "audiobook-lifecycle.js").read_text(encoding="utf-8")
    assert "const responseError = async response =>" in lifecycle
    assert "const failureNotice = error =>" in lifecycle
    assert "if (!response.ok) throw await ttsBackendResponseError(response)" in script
    assert "ttsBackendFailureNotice(error)" in script
    assert "Number(error?.status) === 401 && /登录状态已失效|请先登录/" in lifecycle
    assert "Number(error?.status) === 403" in lifecycle
    assert "generation !== ttsPlanGeneration" in script
    assert script.count("ttsScheduleRebuild()") >= 4
    assert "state.ttsController.stop({ preservePending: true })" in script


def test_web_tts_voice_selector_shows_only_voice_names() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "key: voice.key, label: voice.label," in script
    assert "label: `${voice.label} · ${voice.desc}`" not in script


def test_web_audiobook_uses_authoritative_manifest_and_volatile_session_cache() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    cache = (PROJECT_ROOT / "static" / "audiobook-cache.js").read_text(encoding="utf-8")
    assert "fetch('/api/v1/audiobook/sessions'" in script
    assert "method: 'POST'" in script
    assert "?text=" not in script[script.index("const ttsBackendManifest"):script.index("const ttsCacheWindow")]
    manifest_source = script[
        script.index("  const ttsBackendManifest =") : script.index(
            "  const ttsCacheWindow ="
        )
    ]
    assert "OOHStoryAudiobookCache.prepare" not in manifest_source
    assert "full_chapter=1" in script
    assert "class VolatileAudiobookCache" in cache
    assert "sessionSegments = new Map" in cache
    assert "clearPersistentStorage" in cache
    assert "caches.open" not in cache
    assert "indexedDB.open" not in cache
    assert "const TTS_STREAM_BATCH_SEGMENTS = 5" in script
    assert "stream:${batchStart}" in script
    assert "row.complete = manifest.segments.every" not in cache
    assert "lastAccessedAt" not in cache
    assert "navigator.storage?.estimate?.()" not in cache
    assert "connection?.saveData" in cache
    assert "const SEGMENT_STORE = 'segments'" not in cache
    assert "navigator.locks?.request" not in cache


def test_web_audiobook_cancels_on_end_logout_and_keeps_backend_next_chain() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    assert "window.OOHStoryAudiobookCache?.cancel?.()" in script
    assert "state.ttsController?.stop?.()" in script
    assert "method: 'DELETE'" in script
    assert "`/api/v1/audiobook/sessions/${audiobookServerSessionId}/next?from_chapter_id=${encodeURIComponent(fromChapterId)}`" in script
    rebuild = script[script.index("  const ttsRebuildActivePlan ="):script.index("  const ttsScheduleRebuild =")]
    assert "ttsBackendManifest(startIdx, false)" in rebuild
    assert "ttsBuildChapterPlan(ttsParagraphs(), startIdx)" not in rebuild


def test_web_audiobook_delegates_prefetch_to_stream_cache() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    window = script[script.index("  const ttsCacheWindow ="):script.index("  const ttsStopPlayback =")]
    assert "ttsChapterStreamUrl" in window
    assert "preload=1" in window
    assert "fetch(url" in window
    assert "await response.arrayBuffer()" in window
    assert "OOHStoryAudiobookCache.prepare" not in window
    assert "ttsContinuousStreamMode) return null" not in window


def test_web_audiobook_uses_one_full_chapter_media_source() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    play = script[
        script.index("  const ttsPlayItem ="):
        script.index("\n\n  const ttsPrefetchNextChapter", script.index("  const ttsPlayItem ="))
    ]
    assert "stream_id=${encodeURIComponent(streamId)}&continuous=1&full_chapter=1`" in play
    assert "ttsContinuousStreamMode = true" in script
    assert "? ttsChapterPlan.length" in script
    assert "if (!ttsChapterPlan[idx]?.durationExact)" in script
    assert "audio.src = item.url" not in play
    assert "audio.ontimeupdate = ttsSyncStreamPosition" in play
    assert "const nextBatchIdx" not in play
    assert "ttsChapterEnd()" in play
    assert "ttsFinishChapterStream(streamId, generation, { hardEnd: true })" in play
    assert "ttsConfirmStreamComplete(streamId, generation)" in script
    assert "ttsActiveStreamId !== streamId" in play
    assert "ttsStreamEnding = false" in script
    assert "ttsStreamEnding = true" in script
    assert "if (ttsContinuousStreamMode) {" not in play
    assert "ttsSetActiveItem(finalPlanIndex)" in script
    assert "chapter stream ended before its final receipt" in script
    assert "本章音频结束确认失败，已停在章末，点击重试" in script
    assert "chapter stream failed; switching to finite segment playback" in play
    assert "ttsFallbackPlayback.play(fallbackIdx, fallbackOffset)" in play
    assert "ttsPrefetchNextChapter().catch(() => {})" in play
    assert "limit=${TTS_STREAM_BATCH_SEGMENTS}" in script
    assert "limit=24" not in script
    assert "retryCount < 6" not in play


def test_ios_web_audiobook_uses_native_hls_queue_without_js_chapter_swaps() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    start = script[
        script.index("  const ttsSupportsNativeIosHls =") :
        script.index("\n\n  const ttsBackendManifest", script.index("  const ttsSupportsNativeIosHls ="))
    ]

    assert "application/vnd.apple.mpegurl" in start
    assert "navigator.maxTouchPoints" in start
    assert "`/api/v1/audiobook/sessions/${audiobookServerSessionId}/hls/queues`" in start
    assert "audio.src = String(payload.playlist_endpoint || '')" in start
    assert "Math.abs(currentTime - ttsHlsStartOffsetSeconds) >= 0.05" in start
    assert "audio.currentTime = ttsHlsStartOffsetSeconds" in start
    assert "audio.ontimeupdate = ttsHlsSyncPosition" in start
    assert "setInterval(() => ttsHlsRefreshQueue(), 15000)" in start
    assert "ttsHlsSeekToQueueIndex" in start
    assert "ttsStartNativeIosHls(" in script
    assert "if (!nativeHlsStarted) ttsPlayItem(0)" in script
    assert "manifest_hash: !ttsSegmentFallbackMode && ttsActiveStreamId" in script


def test_web_recovers_expired_cloudflare_challenge_without_killing_the_pwa() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    account = (PROJECT_ROOT / "static" / "account-ui.js").read_text(encoding="utf-8")
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "cf-mitigated" in script
    assert "response.headers.has('cf-ray')" in script
    assert "window.OOHStoryEdgeFetch = edgeFetch" in script
    assert "location.replace" in script
    assert "if (error?.edgeRecovery) return" in script
    assert "probeEdgeSessionAfterResume" in script
    assert "window.OOHStoryEdgeFetch || window.fetch.bind(window)" in account
    assert "app.js?v=20260821-account-nav-touch1" in html
    assert "account-ui.js?v=20260821-account-nav-touch1" in html


def test_web_audiobook_does_not_resume_ended_chapter_stream_during_transition() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    resume = script[
        script.index("  const ttsResumePlayback ="):
        script.index("\n\n  const restartTTSFromChapterStart", script.index("  const ttsResumePlayback ="))
    ]
    visibility = script[
        script.index("  visibilityListener ="):
        script.index("  window.addEventListener('pagehide'", script.index("  visibilityListener ="))
    ]
    chapter_end = script[
        script.index("  const ttsChapterEndOnce = async transitionGeneration =>"):
        script.index(
            "\n\n  const ttsUpdateMediaSession",
            script.index("  const ttsChapterEndOnce = async transitionGeneration =>"),
        )
    ]

    assert "if (!state.reader.ttsActive || ttsStreamEnding) return" in resume
    assert "!ttsStreamEnding && !ttsLifecycle.isPausedByUser()" in visibility
    assert "ttsStreamEnding = false; ttsMarkPlaybackBlocked" in chapter_end


def test_web_media_session_uses_current_book_cover_artwork() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    media = script[
        script.index("  const ttsUpdateMediaSession ="):
        script.index("\n\n  const ttsEstimatedDuration", script.index("  const ttsUpdateMediaSession ="))
    ]

    assert "state.ttsSession?.mediaCoverUrl" in media
    assert "artwork: [{ src: cover }]" in media
    assert "`/api/v1/books/${requestedBookId}/cover?variant=media-art`" in media
    assert "mediaCoverUrl: `/api/v1/books/${requestedBookId}/cover?variant=media-art`" in script


def test_web_audiobook_has_a_finite_segment_fallback_for_stalled_chapter_streams() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    fallback = (PROJECT_ROOT / "static" / "audiobook-fallback.js").read_text(encoding="utf-8")
    timeline = script[
        script.index("  const ttsRefreshTimeline ="):
        script.index("\n  const ttsNewStreamId", script.index("  const ttsRefreshTimeline ="))
    ]
    play = script[
        script.index("  const ttsPlayItem ="):
        script.index("\n\n  const ttsPrefetchNextChapter", script.index("  const ttsPlayItem ="))
    ]

    assert "if (timelineStart > lastAbsolute) return true" in timeline
    assert "method: 'POST'" in fallback
    assert "response.blob()" in fallback
    assert "play(index + 1)" in fallback
    assert "const BATCH_SIZE = 5" in fallback
    assert "prepareBatchRemainder" in fallback
    assert "Math.max(1000, Number(options.timeoutMs) || 8000)" in fallback
    assert "正在生成当前片段" in script
    assert "chapter stream connection stalled; switching to finite segment playback" in play
    assert "Math.max(idx, ttsPlanIndex, ttsTrustedPlanIndex)" in play
    assert "ttsFallbackPlayback.play(fallbackIdx, fallbackIdx === ttsTrustedPlanIndex ? ttsTrustedItemOffsetSeconds : ttsCurrentItemOffsetSeconds(fallbackIdx))" in play
    assert "media stream replayed from the beginning; reopening at trusted position" in script
    assert "ttsRecoverFromStreamReplay()" in script
    assert "audio.removeAttribute('src')" in play
    assert "OOHStoryAudiobookConnectTimeoutMs || 8000" in script
    assert "progress: force => ttsQueueServerProgress(force)" in script
    assert "progress: ttsQueueServerProgress" not in script


def test_finite_segment_endpoint_returns_a_bounded_response() -> None:
    backend = (PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    endpoint = backend[
        backend.index("async def audiobook_segment_audio"):
        backend.index("\n\n@", backend.index("async def audiobook_segment_audio"))
    ]
    assert "return Response(" in endpoint
    assert "content=audio" in endpoint
    assert "StreamingResponse(iter([audio])" not in endpoint
    assert "await _audiobook_segment_while_active(" in endpoint
    assert 'raise HTTPException(409, "听书会话已结束")' in endpoint


def test_web_audiobook_counts_only_readable_chapters_and_commits_real_transitions() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "const ttsChapterMetrics = chapterId =>" in script
    assert "catalog.chapters.filter(item => !isFrontMatterChapter(item))" in script
    assert "count: exactCount > 0 ? exactCount" in script
    assert "chapterNumber: chapterMetrics.number" in script
    assert "chapterCount: chapterMetrics.count" in script
    assert "await ttsActivateChapter(enteringChapterId)" in script
    assert "/chapters/${encodeURIComponent(chapterId)}/activate`" in script
    assert "?from_chapter_id=${encodeURIComponent(fromChapterId)}`" in script


def test_web_tts_keeps_ios_audio_unlock_during_hot_switch() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    play_start = script.index("  const ttsPlayItem =")
    play_end = script.index("\n\n  const ttsPrefetchNextChapter", play_start)
    play_source = script[play_start:play_end]
    rebuild_start = script.index("  const ttsRebuildActivePlan =")
    rebuild_end = script.index("\n\n  const startTTS =", rebuild_start)
    rebuild_source = script[rebuild_start:rebuild_end]

    assert "stream_id=${encodeURIComponent(streamId)}&continuous=1&full_chapter=1`" in play_source
    assert "await ttsGetBlobUrl" not in play_source
    assert "ttsStopPlayback()" not in rebuild_source
    assert "audio.ontimeupdate = null" in rebuild_source
    assert "ttsRebuildTimer = window.setTimeout" in script


def test_web_tts_does_not_skip_safari_policy_rejections() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "['NotAllowedError', 'AbortError']" in script
    assert "ttsMarkPlaybackBlocked(error)" in script
    assert "点击继续智能听书" in script
    open_tts = script[
        script.index("  const restartTTSFromChapterStart = () =>"):
        script.index("\n\n  desktopProgressFill", script.index("  const restartTTSFromChapterStart = () =>"))
    ]
    assert "startTTS(0)" in open_tts
    assert "ttsResumePlayback()" not in open_tts
    assert "onclick: restartTTSFromChapterStart" in script


def test_web_tts_full_exit_is_immediate_and_reentry_safe() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    stop_playback = script[
        script.index("  const ttsStopPlayback ="):
        script.index("\n\n  const ttsModeLabel", script.index("  const ttsStopPlayback ="))
    ]
    stop_session = script[
        script.index("  const stopTTS ="):
        script.index("\n\n  const ttsFirstVisibleParagraph", script.index("  const stopTTS ="))
    ]
    runtime = (PROJECT_ROOT / "tests" / "tts_ios_webkit_runtime.js").read_text(encoding="utf-8")

    assert "let ttsAudioUnlockGeneration = 0" in script
    assert "const unlockGeneration = ++ttsAudioUnlockGeneration" in script
    assert "unlockGeneration !== ttsAudioUnlockGeneration || audio !== ttsAudioEl" in script
    assert "ttsAudioUnlockGeneration++" in stop_playback
    assert "ttsAudioEl = null" not in stop_playback
    assert "ttsAudioUnlocked = false" not in stop_playback
    assert "if (ttsAudioUnlockPromise && !ttsAudioUnlocked)" not in script
    assert "audio.onplaying = markActuallyPlaying" in script
    assert "if (ttsLifecycle.snapshot().state === 'connecting') ttsLifecycle.playing()" in script
    assert "Promise.resolve(playPromise).then(markActuallyPlaying)" in script
    delete_call = "fetch(`/api/v1/audiobook/sessions/${closingSessionId}`"
    assert delete_call in stop_session
    assert "if (closingSessionId) audiobookAbortController?.abort()" in stop_session
    assert "Promise.resolve(finalProgress).finally" not in stop_session
    assert "for (let round = 0; round < 3; round++)" in runtime
    assert "pause must stop the active stream" in runtime
    assert "paused === false" in runtime


def test_web_tts_preserves_the_requested_smart_narrator_contract() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    manifest = script[
        script.index("  const ttsBackendManifest ="):
        script.index("\n\n  const ttsCacheWindow", script.index("  const ttsBackendManifest ="))
    ]
    runtime = (PROJECT_ROOT / "tests" / "tts_ios_webkit_runtime.js").read_text(encoding="utf-8")

    assert "const requestedNarrator = String(state.reader.ttsNarrator || 'mocheng')" in manifest
    assert "fetch('/api/v1/tts/voices'" in script
    assert "const ttsMandarinPool" not in script
    assert "const streamId = ttsNewStreamId()" in script
    assert "chapter stream failed; switching to finite segment playback" in script
    assert "正在连接音频" in script
    assert "modeVoiceFilter = policy.mode_languages || {}" in script
    assert "narrator: requestedNarrator" in manifest
    assert "responseRequestedNarrator !== requestedNarrator || effectiveNarrator !== requestedNarrator" in manifest
    assert "error.code = 'narrator_voice_mismatch'" in manifest
    assert "state.ttsSession.requestedNarrator = requestedNarrator" in manifest
    assert "state.ttsSession.effectiveNarrator = effectiveNarrator" in manifest
    assert "payload.narrator === 'lingxian'" in runtime
    assert "payload.current?.effective_narrator === 'lingxian'" in runtime


def test_web_tts_manifest_failure_is_visible_and_retryable() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    lifecycle = (PROJECT_ROOT / "static" / "audiobook-lifecycle.js").read_text(encoding="utf-8")
    assert "toast.className = 'tts-error-toast'" in lifecycle
    assert "toast.setAttribute('role', 'alert')" in lifecycle
    assert "tts-error-toast-retry" in lifecycle
    assert "if (!ttsChapterPlan.length)" in script
    assert "ttsRebuildActivePlan()" in script
    assert ".tts-error-toast-retry" in css


def test_reader_settings_offer_a_true_audiobook_exit_and_hide_it_when_inactive() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert "let ttsExitControl = null" in script
    assert "class: 'reader-tts-exit'" in script
    assert "text: '退出听书'" in script
    assert "if (ttsExitControl) ttsExitControl.hidden = !state.reader.ttsActive" in script
    exit_start = script.index("ttsExitControl = node('section'")
    exit_source = script[exit_start:script.index("  const setSettingsVisible", exit_start)]
    assert "stopTTS()" in exit_source
    assert "setSettingsVisible(false)" in exit_source
    assert ".reader-tts-exit[hidden]" in css


def test_mobile_paged_modes_use_compact_bottom_spacing_without_changing_vertical_mode() -> None:
    css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    paged_start = css.index(".reader-stage:not(.reader-mode-vertical) .reader-content")
    paged = css[paged_start:css.index(".reader-stage.reader-mode-cover .reader-content", paged_start)]
    assert "max(12px, env(safe-area-inset-bottom))" in paged
    assert "margin-bottom: .56em" in paged
    assert "orphans: 1" in paged
    assert "widows: 1" in paged


def test_web_tts_does_not_skip_dialogue_on_audio_error() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    play_start = script.index("  const ttsPlayItem =")
    play_end = script.index("\n\n  const ttsPrefetchNextChapter", play_start)
    play_source = script[play_start:play_end]
    failure_start = play_source.index("    const advanceAfterFailure =")
    failure_end = play_source.index("\n    audio.onended =", failure_start)
    failure_source = play_source[failure_start:failure_end]

    assert "chapter stream failed; switching to finite segment playback" in failure_source
    assert "ttsFallbackPlayback.play(fallbackIdx, fallbackOffset)" in failure_source
    assert "ttsPlayItem(idx + 1)" not in failure_source
    assert "URL.createObjectURL" not in play_source


def test_web_tts_has_no_frontend_character_or_voice_guessing_fallback() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "ttsInferContextGender" not in script
    assert "ttsBuildSpeakerMap" not in script
    assert "ttsBuildChapterPlan" not in script


def test_web_tts_prefetches_only_one_five_segment_next_chapter_batch() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    cache_start = script.index("  const ttsCacheWindow =")
    cache_source = script[cache_start:script.index("\n  const ttsStopPlayback", cache_start)]

    assert "ttsChapterStreamUrl" in cache_source
    assert "preload=1" in cache_source
    assert "await response.arrayBuffer()" in cache_source
    assert "OOHStoryAudiobookCache.prepare" not in cache_source
    assert "window.OOHStoryAudiobookCache.prepare(" not in script
    assert "|| ttsContinuousStreamMode" not in cache_source
    assert "idx >= Math.max(0, ttsChapterPlan.length - 2)" in script
    assert "TTS_PREFETCH_AHEAD" not in script


def test_web_tts_next_chapter_manifest_is_not_blocked_by_prefetch_capacity() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    start = script.index("  const ttsPrefetchNextChapter = () =>")
    source = script[start:script.index("\n\n  const ttsQueueServerProgress", start)]

    manifest_fetch = source.index("next?from_chapter_id")
    plan_assign = source.index("ttsNextChapterPlan = nextPlan")
    capacity_check = source.index("OOHStoryAudiobookCache.shouldPrefetch")
    assert manifest_fetch < capacity_check
    assert plan_assign < capacity_check
    assert "ttsNextChapterStreamUrl" in source
    assert "?start=${encodeURIComponent(firstIndex)}&preload=1" in source
    assert "await preload.arrayBuffer()" in source


def test_web_tts_can_detach_and_return_without_stopping_audio() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert 'id="global-tts-return"' in html
    assert ".global-tts-return" in css
    assert "state.ttsController.detach()" in script
    assert "state.ttsController?.attach?.(index =>" in script
    assert "navigateInApp(session.returnPath" in script
    assert "ttsSessionIsPlaying(bookId)" in script


def test_web_tts_opens_a_dedicated_mobile_player_without_replacing_audio() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert 'id="tts-player"' in html
    assert 'id="tts-player-toggle"' in html
    assert 'id="tts-player-rate"' in html
    assert 'id="tts-player-mode"' in html
    assert ".tts-player" in css
    assert "bottom: max(82px, calc(env(safe-area-inset-bottom) + 78px));" in css
    assert "function openTtsPlayer()" in script
    assert "ttsPlayer.scrollTop = 0" in script
    assert "globalTtsReturn?.addEventListener('click', openTtsPlayer)" in script
    state_bar = script[
        script.index("(ttsStateBar = node('button'"):
        script.index("  ].filter(Boolean))", script.index("(ttsStateBar = node('button'"))
    ]
    assert "onclick: openTtsPlayer" in state_bar
    assert "onclick: restartTTSFromChapterStart" not in state_bar
    assert "pause: () =>" in script
    assert "previous: () =>" in script
    assert "next: () =>" in script
    assert "ttsPlayerStop?.addEventListener" in script
    assert script.count("let ttsAudioEl = null") == 1
    assert script.count("const ttsEnsureAudio = () =>") == 1
    assert script.index("const ttsEnsureAudio = () =>") < script.index("const startTTS =")
    assert "const ttsPrimeAudioFromGesture = () =>" in script
    assert "audio.src = TTS_AUDIO_UNLOCK_SRC" in script
    assert "ttsPrimeAudioFromGesture()" in script
    start_source = script[script.index("  const startTTS ="):script.index("\n\n  const ttsResumePlayback")]
    assert start_source.index("ttsPrimeAudioFromGesture()") < start_source.index("state.reader.ttsActive = true")
    open_tts = script[
        script.index("  const restartTTSFromChapterStart = () =>"):
        script.index("\n\n  desktopProgressFill", script.index("  const restartTTSFromChapterStart = () =>"))
    ]
    assert "startTTS(0)" in open_tts
    assert "openTtsPlayer()" in open_tts
    assert "console.error('[TTS] player initialization failed', error)" in script
    assert "'paragraphs:', ttsParagraphs().length" in script
    assert "'paragraphs:', paragraphs.length" not in script


def test_web_tts_supports_auto_and_manual_emotion_modes() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert script.count("desc: '") >= 13
    assert "/api/v1/tts/speak" not in script
    assert "emotion: state.reader.ttsEmotion" in script
    assert "setEmotion: value =>" in script
    assert 'id="tts-player-emotion"' in html
    assert 'id="tts-emotion-sheet"' in html
    assert ".tts-emotion-options" in css
    assert "情感阅读" in script


def test_web_mobile_tts_starts_and_tracks_from_top_visible_line() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "sort((a, b) => a.rect.top - b.rect.top)" in script
    assert "visible[0].paragraph.dataset.ttsIndex" in script
    assert "window.matchMedia('(max-width: 720px)').matches ? 'start' : 'center'" in script


def test_web_tts_retries_highlight_when_reader_paragraph_is_not_attached() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "let ttsPendingHighlightIndex = null" in script
    assert "const ttsScheduleHighlightRetry = index =>" in script
    assert "requestAnimationFrame(() =>" in script
    assert "const ttsActiveHighlightPresent = index =>" in script
    assert "if (!ttsActiveHighlightPresent(item.paraIdx)) ttsHighlight(item.paraIdx)" in script


def test_web_tts_discards_stale_prefetch_results_after_rebuild() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "audiobookAbortController?.abort()" in script
    assert "window.OOHStoryAudiobookCache?.cancel?.()" in script
    assert "ttsFallbackPlayback?.release()" in script


def test_backend_tts_splits_long_paragraphs_without_losing_paragraph_identity() -> None:
    backend = (PROJECT_ROOT / "app" / "audiobook.py").read_text(encoding="utf-8")

    assert "def split_tts_text(value: str, limit: int = 450)" in backend
    assert '"paragraph_index": paragraph_index' in backend
    assert "for chunk in split_tts_text" in backend


def test_web_tts_preserves_unlocked_audio_across_automatic_chapter_route() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
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
    assert "ttsAudioEl = null" not in stop_source
    assert "state.ttsController.stop({ preservePending: true })" in script


def test_web_tts_adopts_prefetched_chapter_without_stopping_the_player() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    chapter_end_start = script.index(
        "  const ttsChapterEndOnce = async transitionGeneration =>"
    )
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


def test_web_tts_deduplicates_prefetch_and_recovers_missing_media_ended() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    prefetch_start = script.index("  const ttsPrefetchNextChapter = () =>")
    prefetch_end = script.index("\n\n  const ttsQueueServerProgress", prefetch_start)
    prefetch = script[prefetch_start:prefetch_end]

    assert "ttsNextChapterPrefetchPromise" in prefetch
    assert "ttsNextChapterPrefetchSourceId === fromChapterId" in prefetch
    assert "ttsNextChapterPlan.length && String(ttsNextChapterId)" in prefetch
    assert "const ttsMaybeCompleteChapterAtMediaEof" in script
    assert "ttsExpectedStreamDuration()" in script
    assert "ttsTimelineLoadedThrough < finalAbsolute" in script
    assert "ttsFinishChapterStream(streamId, generation, { hardEnd: false })" in script
    assert "await Promise.resolve(ttsQueueServerProgress(true))" in script
    assert "if (ttsChapterTransitionPromise) return ttsChapterTransitionPromise" in script


def test_web_tts_timeline_only_polls_when_playback_needs_more_durations() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    start = script.index("  const ttsRefreshTimeline = async (force = false) =>")
    timeline = script[start:script.index("\n  const ttsNewStreamId", start)]

    assert "const desiredThrough = Math.min(lastAbsolute, currentAbsolute + TTS_STREAM_BATCH_SEGMENTS - 1)" in timeline
    assert "ttsTimelineLoadedThrough >= desiredThrough" in timeline
    assert "now - ttsLastTimelineRefresh < 1500" in timeline
