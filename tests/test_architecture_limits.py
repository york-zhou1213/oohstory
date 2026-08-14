from pathlib import Path

from app.error_boundaries import RECOVERABLE_INTEGRATION_ERRORS


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_frontend_monolith_cannot_regrow_legacy_audiobook_logic() -> None:
    app_script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    cache_script = PROJECT_ROOT / "static" / "audiobook-cache.js"
    lifecycle_script = PROJECT_ROOT / "static" / "audiobook-lifecycle.js"
    fallback_script = PROJECT_ROOT / "static" / "audiobook-fallback.js"
    account_script = PROJECT_ROOT / "static" / "account-ui.js"

    assert len(app_script.splitlines()) <= 5800
    assert cache_script.is_file()
    assert lifecycle_script.is_file()
    assert fallback_script.is_file()
    assert len(fallback_script.read_text(encoding="utf-8").splitlines()) <= 220
    assert account_script.is_file()
    assert len(account_script.read_text(encoding="utf-8").splitlines()) <= 2000
    assert "/api/v1/tts/speak" not in app_script
    assert "buildDialoguePlan" not in app_script
    assert "const ttsPlaybackBlocked" not in app_script
    assert "let ttsPlaybackConnecting" not in app_script
    assert "const ttsMandarinPool" not in app_script


def test_reader_recovery_boundary_does_not_swallow_programming_errors() -> None:
    for error_type in (AttributeError, AssertionError, MemoryError):
        assert not issubclass(error_type, RECOVERABLE_INTEGRATION_ERRORS)
    for error_type in (OSError, RuntimeError, ValueError):
        assert issubclass(error_type, RECOVERABLE_INTEGRATION_ERRORS)
