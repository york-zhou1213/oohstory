from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_cover_loader_waits_for_dom_and_hides_pending_images() -> None:
    script = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "queueMicrotask(() => {" in script
    assert "if (!img.isConnected) return" in script
    assert "img.style.visibility = 'hidden'" in script
    assert "img.style.removeProperty('visibility')" in script
    assert "const objectUrl = URL.createObjectURL(blob)" in script
    assert "URL.revokeObjectURL(objectUrl)" in script
    assert "url: freshCoverUrl(url)" in script
    assert "const inFlight = new Map()" in script
    assert "const blobCache = new Map()" in script
    assert "const existing = inFlight.get(url)" in script
    assert "fetch(url, { cache: 'default' })" in script
    assert "cache: 'no-cache'" not in script
    assert "COVER_CACHE_EPOCH" not in script
    assert 'src="/app.js?v=20260806-v40-oss1"' in html
