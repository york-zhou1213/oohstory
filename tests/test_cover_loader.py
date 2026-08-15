from tests.frontend_contract_source import frontend_contract_source
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_cover_loader_waits_for_dom_and_hides_pending_images() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
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
    assert 'src="/app.js?v=20260815-submission-atelier2"' in html


def test_light_novel_volume_gallery_eagerly_loads_the_first_visible_row() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "const eagerVolumeCoverCount = window.matchMedia('(max-width: 720px)').matches ? 3 : 6" in script
    assert "if (volumeIndex < eagerVolumeCoverCount) coverLoader.loadNow(img, coverUrl)" in script
    assert "text: hasVolumes ? '分卷封面与目录' : '章节目录'" in script
    assert "hasVolumes ? chapterPanel : null" in script
