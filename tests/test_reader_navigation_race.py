from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_reader_routes_reject_stale_chapter_loads_and_duplicate_navigation() -> None:
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "routeGeneration: 0" in script
    assert "const navigationGeneration = ++state.routeGeneration" in script
    assert "function routeIsCurrent(generation, expectedPath = '')" in script
    assert "async function loadReader(bookId, chapterId, navigationGeneration = state.routeGeneration)" in script
    assert "if (!routeIsCurrent(navigationGeneration, expectedReaderPath)) return" in script
    assert "if (!id || chapterNavigationPending) return false" in script
    assert "chapterNavigationPending = true" in script
    assert "if (!navigated) chapterNavigationPending = false" in script
    assert script.count("routeIsCurrent(navigationGeneration, expectedReaderPath)") >= 3
    assert "if (!routeIsCurrent(navigationGeneration, path)) return" in script


def test_new_chapter_wins_progress_sync_and_ios_horizontal_gestures() -> None:
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    account = (PROJECT_ROOT / "static" / "account-ui.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    after_layout = script.index("const afterLayout = () =>")
    assert script.index("flushReadingProgress()", after_layout) > script.index(
        "updateProgress()", after_layout
    )
    assert "cloudSyncGeneration: 0" in script
    assert "const syncGeneration = ++state.cloudSyncGeneration" in account
    assert "if (syncGeneration === state.cloudSyncGeneration) state.cloudState = cloudState" in account
    assert "event.preventDefault()" in script
    assert "}, { passive: false })" in script
    assert "overscroll-behavior-x: none;" in styles
