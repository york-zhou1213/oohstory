from fastapi.testclient import TestClient

from app import main


def test_android_update_endpoint_returns_public_release_notes() -> None:
    response = TestClient(main.app).get(
        "/api/v1/app/android/latest?version_code=54&version_name=1.18.10"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["platform"] == "android"
    assert payload["available"] is True
    assert payload["latest"]["version_name"] == "1.18.20"
    assert payload["latest"]["version_code"] == 64
    assert payload["latest"]["download_url"] == (
        f"{main.SITE_ORIGIN}/downloads/android/latest.apk"
    )
    assert payload["latest"]["release_notes_public"]
    assert "回滚" not in "\n".join(payload["latest"]["release_notes_public"])
    assert "SHA256" not in "\n".join(payload["latest"]["release_notes_public"]).upper()


def test_android_update_endpoint_suppresses_current_version_prompt() -> None:
    response = TestClient(main.app).get(
        "/api/v1/app/android/latest?version_code=64&version_name=1.18.20"
    )
    assert response.status_code == 200
    assert response.json()["available"] is False
