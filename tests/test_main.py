from __future__ import annotations

from base64 import b64encode
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from xml.etree import ElementTree

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app import main
from app import rate_limiter as rate_limiter_module
from app.library import InputError

PUBLIC_BOOK_ID = "AAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture(autouse=True)
def isolate_request_limiter(monkeypatch, tmp_path: Path) -> None:
    """Keep per-test request accounting independent and deterministic."""
    monkeypatch.setattr(
        rate_limiter_module,
        "_DOWNLOAD_LOG_PATH",
        tmp_path / "download_daily.json",
    )
    monkeypatch.setattr(
        rate_limiter_module,
        "_DOWNLOAD_LOG_LOCK_PATH",
        tmp_path / "download_daily.lock",
    )
    monkeypatch.setattr(
        main,
        "_rate_limiter",
        main.RateLimiter(
            global_rate=1_000,
            global_burst=10_000,
            sitemap_rate=1_000,
            sitemap_burst=10_000,
            chapter_rate=1_000,
            chapter_burst=10_000,
            ban_threshold=100_000,
            reader_guard_enabled=False,
        ),
    )


def test_reader_admin_routes_require_authentication() -> None:
    client = TestClient(main.app)

    assert client.get("/api/v1/admin/users").status_code == 401
    assert client.get("/api/v1/admin/categories").status_code == 401


def test_chapter_body_is_never_shared_or_edge_cached(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class ChapterRepository:
        @staticmethod
        def reader_chapter(book_id: str, chapter_id: int) -> dict[str, object]:
            return {
                "id": chapter_id,
                "book": {"public_id": book_id},
                "paragraphs": ["正文"],
            }

    monkeypatch.setattr(main, "repository", lambda: ChapterRepository())
    monkeypatch.setattr(
        main,
        "_rate_limiter",
        main.RateLimiter(
            reader_guard_enabled=True,
            reader_guard_path=tmp_path / "reader-abuse.sqlite3",
            reader_probe_secret_path=tmp_path / "reader-probe.key",
        ),
    )
    client = TestClient(main.app)

    response = client.get(
        f"/api/v1/books/{PUBLIC_BOOK_ID}/chapters/1",
        headers={"Host": "testserver"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["cdn-cache-control"] == "no-store"
    assert response.headers["cloudflare-cdn-cache-control"] == "no-store"
    assert response.json()["_reader_probe"].startswith(
        f"/api/v1/reader-probe/{PUBLIC_BOOK_ID}/1/"
    )


def test_reader_probe_needs_two_hits_then_blocks_chapters(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class ChapterRepository:
        @staticmethod
        def reader_chapter(book_id: str, chapter_id: int) -> dict[str, object]:
            return {
                "id": chapter_id,
                "book": {"public_id": book_id},
                "paragraphs": ["正文"],
            }

    monkeypatch.setattr(main, "repository", lambda: ChapterRepository())
    monkeypatch.setattr(
        main,
        "_rate_limiter",
        main.RateLimiter(
            reader_guard_enabled=True,
            reader_guard_path=tmp_path / "reader-abuse.sqlite3",
            reader_probe_secret_path=tmp_path / "reader-probe.key",
        ),
    )
    client = TestClient(main.app)
    headers = {
        "Host": "testserver",
        "CF-Connecting-IP": "203.0.113.77",
        "User-Agent": "Mozilla/5.0 OOHStory reader probe regression",
    }

    chapter = client.get(
        f"/api/v1/books/{PUBLIC_BOOK_ID}/chapters/1",
        headers=headers,
    )
    probe = chapter.json()["_reader_probe"]

    assert client.get(probe, headers=headers).status_code == 404
    assert client.get(probe, headers=headers).status_code == 404
    blocked = client.get(
        f"/api/v1/books/{PUBLIC_BOOK_ID}/chapters/2",
        headers=headers,
    )
    assert blocked.status_code == 429


def test_maintenance_mode_serves_static_page_and_keeps_health(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        main,
        "settings",
        replace(main.settings, state_root=tmp_path),
    )
    monkeypatch.setattr(main, "repository", lambda: object())
    (tmp_path / "maintenance.enabled").write_text("enabled\n", encoding="utf-8")
    client = TestClient(main.app)

    page = client.get("/reader/anything", headers={"Host": "testserver"})
    api = client.get("/api/v1/home", headers={"Host": "testserver"})
    health = client.get("/healthz", headers={"Host": "testserver"})
    icon = client.get("/icon-192.png", headers={"Host": "testserver"})

    assert page.status_code == 503
    assert "稍作停驻，故事马上继续" in page.text
    assert "<script" not in page.text
    assert page.headers["retry-after"] == "300"
    assert page.headers["cache-control"] == "no-store, no-transform"
    assert api.status_code == 503
    assert api.json() == {
        "detail": "OOHStory 正在维护，请稍后重试",
        "maintenance": True,
    }
    assert health.status_code == 200
    assert icon.status_code == 200


def test_maintenance_page_is_self_contained_and_responsive() -> None:
    source = main.MAINTENANCE_PAGE.read_text(encoding="utf-8")

    assert "https://" not in source
    assert "http://" not in source
    assert "min-height: 100dvh" in source
    assert "@media (max-width: 760px)" in source
    assert "prefers-reduced-motion" in source
    assert "prefers-color-scheme: dark" in source
    assert "—" not in source
    assert "–" not in source


class HomeRepository:
    def __init__(self) -> None:
        self.list_books_args: dict[str, object] = {}
        self.chapter_count_requests: list[str] = []

    def stats(self) -> dict[str, int]:
        return {}

    def categories(self) -> list[object]:
        return []

    def list_books(self, **kwargs: object) -> dict[str, list[dict[str, object]]]:
        self.list_books_args = kwargs
        count = int(kwargs["page_size"])
        return {
            "items": [
                {
                    "public_id": f"book-{item}",
                    "approx_chapter_count": 900 + item,
                }
                for item in range(count)
            ]
        }

    def reader_chapter_count(self, public_id: str) -> int:
        self.chapter_count_requests.append(public_id)
        if public_id == "book-1":
            raise InputError("index unavailable")
        return 68 + int(public_id.removeprefix("book-"))

    def random_recommendations(
        self, count: int, *, words: str = ""
    ) -> list[dict[str, object]]:
        return [{"public_id": f"recommendation-{words or 'all'}"}] * count

    def category_books(self, _count: int) -> dict[str, object]:
        return {}

    def rankings(self, _count: int) -> dict[str, object]:
        return {}

    def list_deconstructions(self) -> list[object]:
        return []


def test_home_returns_two_complete_desktop_rows(monkeypatch) -> None:
    repository = HomeRepository()
    monkeypatch.setattr(main, "repository", lambda: repository)

    result = main.home()

    assert len(result["featured"]) == 14
    assert repository.list_books_args == {
        "page": 1,
        "page_size": 14,
        "sort": "recent",
    }


def test_home_enriches_each_featured_book_with_exact_chapter_count(
    monkeypatch,
) -> None:
    repository = HomeRepository()
    monkeypatch.setattr(main, "repository", lambda: repository)

    result = main.home()

    assert repository.chapter_count_requests == [f"book-{item}" for item in range(14)]
    assert result["featured"][0]["chapter_count"] == 68
    assert result["featured"][0]["approx_chapter_count"] == 68
    assert "chapter_count" not in result["featured"][1]
    assert result["featured"][1]["approx_chapter_count"] == 901
    assert result["featured"][13]["chapter_count"] == 81
    assert "chapter_count" not in result["recommendations"][0]


def test_home_snapshots_split_primary_and_secondary(monkeypatch) -> None:
    repository = HomeRepository()
    monkeypatch.setattr(main, "repository", lambda: repository)

    primary = main.home_primary()
    secondary = main.home_secondary()

    assert set(primary) == {"stats", "categories", "featured", "recommendations"}
    assert set(secondary) == {
        "long_novels",
        "short_novels",
        "category_books",
        "rankings",
        "deconstructions",
    }
    assert main.HOME_PRIMARY_CACHE_SECONDS == 60
    assert main.HOME_SECONDARY_CACHE_SECONDS == 300


def test_security_headers_and_error_responses_are_not_cacheable() -> None:
    client = TestClient(main.app)
    response = client.get(
        "/api/v1/books/not-a-number",
        headers={"Host": "testserver"},
    )

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["strict-transport-security"].startswith("max-age=31536000")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cross-origin-opener-policy"] == (
        "same-origin-allow-popups"
    )
    content_security_policy = response.headers["content-security-policy"]
    assert "default-src 'self'" in content_security_policy
    directives = {
        parts[0]: parts[1:]
        for directive in content_security_policy.split(";")
        if (parts := directive.strip().split())
    }
    assert directives["script-src"] == [
        "'self'",
        "https://static.cloudflareinsights.com",
        "https://challenges.cloudflare.com",
    ]
    assert directives["connect-src"] == [
        "'self'",
        "https://cloudflareinsights.com",
        "https://*.cloudflareinsights.com",
    ]
    assert directives["img-src"] == ["'self'", "data:", "blob:"]
    assert directives["media-src"] == ["'self'", "blob:", "data:"]
    assert directives["script-src-attr"] == ["'none'"]
    assert "'unsafe-eval'" not in directives["script-src"]
    assert "'unsafe-inline'" not in directives["script-src"]
    assert "*" not in directives["script-src"]
    assert "*" not in directives["connect-src"]


def test_google_login_security_policy_allows_popup_callback_and_iframe(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        main,
        "settings",
        replace(
            main.settings,
            google_web_client_id="web.apps.googleusercontent.com",
        ),
    )

    response = TestClient(main.app).get(
        "/api/v1/books/not-a-number",
        headers={"Host": "testserver"},
    )

    directives = {
        parts[0]: parts[1:]
        for directive in response.headers["content-security-policy"].split(";")
        if (parts := directive.strip().split())
    }
    assert response.headers["cross-origin-opener-policy"] == (
        "same-origin-allow-popups"
    )
    assert "https://accounts.google.com/gsi/client" in directives["script-src"]
    assert "https://accounts.google.com" in directives["connect-src"]
    assert directives["frame-src"] == ["https://accounts.google.com"]


def test_google_redirect_accepts_null_origin_form_but_not_other_cross_origin_requests() -> None:
    with TestClient(main.app, base_url="https://testserver") as client:
        client.cookies.set("g_csrf_token", "cookie-value")
        google_form = client.post(
            "/",
            headers={"Host": "testserver", "Origin": "null"},
            data={
                "credential": "x" * 200,
                "g_csrf_token": "different-value",
                "state": "oohstory-web-redirect-v1",
            },
            follow_redirects=False,
        )
        attacker_form = client.post(
            "/",
            headers={"Host": "testserver", "Origin": "https://attacker.invalid"},
            data={"credential": "x" * 200},
            follow_redirects=False,
        )
        null_origin_json = client.post(
            "/api/v1/auth/google",
            headers={"Host": "testserver", "Origin": "null"},
            json={"id_token": "x" * 200, "client": "web"},
        )

    assert google_form.status_code == 303
    assert google_form.headers["location"].startswith("/?google_error=")
    assert attacker_form.status_code == 403
    assert attacker_form.json()["detail"] == "跨站请求已拒绝"
    assert null_origin_json.status_code == 403


def test_cover_response_must_revalidate_after_pointer_changes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    cover_path = tmp_path / "cover.jpg"
    cover_path.write_bytes(b"\xff\xd8\xff" + b"cover-bytes")

    class CoverRepository:
        def cover_path_and_version(self, _book_id: str) -> tuple[Path, str]:
            return cover_path, "0123456789abcdef"

    monkeypatch.setattr(main, "repository", lambda: CoverRepository())
    response = TestClient(main.app).get(
        f"/api/v1/books/{PUBLIC_BOOK_ID}/cover",
        headers={"Host": "testserver"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == ("public, max-age=0, must-revalidate")


def test_media_cover_art_preserves_portrait_cover_ratio(
    monkeypatch,
    tmp_path: Path,
) -> None:
    cover_path = tmp_path / "portrait.jpg"
    Image.new("RGB", (300, 600), (180, 20, 30)).save(cover_path, "JPEG")

    class CoverRepository:
        def cover_path_and_version(self, _book_id: str) -> tuple[Path, str]:
            return cover_path, "0123456789abcdef"

    monkeypatch.setattr(main, "repository", lambda: CoverRepository())
    response = TestClient(main.app).get(
        f"/api/v1/books/{PUBLIC_BOOK_ID}/cover?variant=media-art",
        headers={"Host": "testserver"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    with Image.open(BytesIO(response.content)) as image:
        assert image.size == (512, 512)
        assert image.getpixel((256, 256))[0] > 150
        assert image.getpixel((8, 256))[0] > 235


def test_search_favicons_use_short_revalidating_cache_and_brand_payload() -> None:
    client = TestClient(main.app)
    expected = {
        "/favicon.ico": "favicon.ico",
        "/oohstory-favicon-48.png": "favicon-48.png",
        "/oohstory-favicon-96.png": "favicon-96.png",
    }

    for route, filename in expected.items():
        response = client.get(route)
        assert response.status_code == 200
        assert response.content == (main.STATIC_ROOT / filename).read_bytes()
        assert response.headers["cache-control"] == main.SEARCH_FAVICON_CACHE_CONTROL
        assert (
            response.headers["cdn-cache-control"]
            == main.SEARCH_FAVICON_CDN_CACHE_CONTROL
        )
        assert (
            response.headers["cloudflare-cdn-cache-control"]
            == main.SEARCH_FAVICON_CDN_CACHE_CONTROL
        )

        head = client.head(route)
        assert head.status_code == 200
        assert head.content == b""
        assert head.headers["cache-control"] == main.SEARCH_FAVICON_CACHE_CONTROL
        assert (
            head.headers["cdn-cache-control"] == main.SEARCH_FAVICON_CDN_CACHE_CONTROL
        )
        assert (
            head.headers["cloudflare-cdn-cache-control"]
            == main.SEARCH_FAVICON_CDN_CACHE_CONTROL
        )


def test_versioned_cover_response_is_immutable(monkeypatch, tmp_path: Path) -> None:
    cover_path = tmp_path / "cover.jpg"
    cover_path.write_bytes(b"\xff\xd8\xff" + b"versioned-cover")

    class CoverRepository:
        def cover_path_and_version(self, _book_id: str) -> tuple[Path, str]:
            return cover_path, "0123456789abcdef"

    monkeypatch.setattr(main, "repository", lambda: CoverRepository())
    response = TestClient(main.app).get(
        f"/api/v1/books/{PUBLIC_BOOK_ID}/cover?v=0123456789abcdef",
        headers={"Host": "testserver"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == (
        "public, max-age=31536000, immutable, no-transform"
    )


def test_home_hero_cover_uses_stable_preloadable_url(
    monkeypatch, tmp_path: Path
) -> None:
    cover_path = tmp_path / "hero.jpg"
    Image.new("RGB", (300, 450), color=(30, 80, 120)).save(cover_path, format="JPEG")

    class HeroCoverRepository:
        def cover_path_and_version(self, book_id: str) -> tuple[Path, str]:
            assert book_id == PUBLIC_BOOK_ID
            return cover_path, "0123456789abcdef"

    monkeypatch.setattr(main, "repository", lambda: HeroCoverRepository())
    monkeypatch.setattr(
        main,
        "home_primary",
        lambda: {
            "featured": [
                {
                    "public_id": PUBLIC_BOOK_ID,
                    "cover_url": (
                        f"/api/v1/books/{PUBLIC_BOOK_ID}/cover?v=0123456789abcdef"
                    ),
                }
            ]
        },
    )
    response = TestClient(main.app).get(
        "/api/v1/home/hero-cover",
        headers={"Host": "testserver"},
    )

    assert response.status_code == 200
    assert response.content == cover_path.read_bytes()
    assert response.headers["cache-control"] == (
        "public, max-age=60, stale-while-revalidate=300, no-transform"
    )

    mobile = TestClient(main.app).get(
        "/api/v1/home/hero-cover?variant=mobile",
        headers={"Host": "testserver"},
    )
    assert mobile.status_code == 200
    with Image.open(BytesIO(mobile.content)) as image:
        assert image.size == (160, 240)
        assert image.format == "JPEG"
    assert len(mobile.content) < len(response.content)


def test_shared_default_cover_is_immutable_and_single_url(
    monkeypatch, tmp_path
) -> None:
    cover_path = tmp_path / "default.jpg"
    cover_path.write_bytes(b"\xff\xd8\xff" + b"default-cover")

    class DefaultCoverRepository:
        def shared_default_cover_path(self) -> Path:
            return cover_path

    monkeypatch.setattr(main, "repository", lambda: DefaultCoverRepository())
    response = TestClient(main.app).get(
        "/api/v1/assets/default-cover?v=d421cee15a266d25",
        headers={"Host": "testserver"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == (
        "public, max-age=31536000, immutable, no-transform"
    )


def test_html_csp_uses_per_response_nonce_and_exact_json_ld_hash() -> None:
    client = TestClient(main.app)

    first = client.get("/", headers={"Host": "testserver"})
    second = client.get("/", headers={"Host": "testserver"})
    first_sources = {
        parts[0]: parts[1:]
        for directive in first.headers["content-security-policy"].split(";")
        if (parts := directive.strip().split())
    }["script-src"]
    second_sources = {
        parts[0]: parts[1:]
        for directive in second.headers["content-security-policy"].split(";")
        if (parts := directive.strip().split())
    }["script-src"]
    first_nonces = [source for source in first_sources if source.startswith("'nonce-")]
    second_nonces = [
        source for source in second_sources if source.startswith("'nonce-")
    ]

    assert len(first_nonces) == 1
    assert len(second_nonces) == 1
    assert first_nonces != second_nonces
    index_html = (Path(main.STATIC_ROOT) / "index.html").read_text(encoding="utf-8")
    json_ld = re.search(
        r'<script id="structured-data" type="application/ld\+json">(.*?)</script>',
        index_html,
        re.DOTALL,
    )
    assert json_ld is not None
    expected_hash = b64encode(sha256(json_ld.group(1).encode("utf-8")).digest()).decode(
        "ascii"
    )
    assert f"'sha256-{expected_hash}'" in first_sources
    assert "'unsafe-inline'" not in first_sources
    assert {
        directive.strip().casefold()
        for directive in first.headers["cache-control"].split(",")
    } == {"no-transform"}
def test_untrusted_host_is_rejected() -> None:
    client = TestClient(main.app)
    response = client.get("/", headers={"Host": "attacker.invalid"})

    assert response.status_code == 400


def test_clean_canonical_routes_return_the_same_spa_index() -> None:
    client = TestClient(main.app)
    expected = (Path(main.STATIC_ROOT) / "index.html").read_bytes()

    for path in (
        "/library",
        "/rankings",
        "/deconstructions",
        "/deconstructions/%E6%A0%B7%E6%9C%AC%E4%B9%A6",
        "/about",
        "/disclaimer",
        "/guide",
        "/contact",
        "/client",
    ):
        response = client.get(path, headers={"Host": "testserver"})
        assert response.status_code == 200
        assert response.content == expected
        assert response.headers["content-type"].startswith("text/html")
        assert {
            directive.strip().casefold()
            for directive in response.headers["cache-control"].split(",")
        } == {"no-cache", "no-transform"}


class SeoRepository:
    def get_book(self, book_id: str) -> dict[str, object]:
        assert book_id == PUBLIC_BOOK_ID
        return {
            "public_id": PUBLIC_BOOK_ID,
            "title": "Re:从零开始的异世界生活[Web版]",
            "author": "长月达平",
            "category": "轻小说",
            "summary": "菜月昴来到异世界，并获得了死亡回归的能力。",
            "genre_tags": ["异世界", "冒险"],
            "cover_url": f"/api/v1/books/{PUBLIC_BOOK_ID}/cover?v=cover1",
        }

    def reader_catalog(self, book_id: str) -> dict[str, object]:
        assert book_id == PUBLIC_BOOK_ID
        return {
            "book": {
                "public_id": PUBLIC_BOOK_ID,
                "title": "Re:从零开始的异世界生活[Web版]",
                "author": "长月达平",
                "category": "轻小说",
            },
            "chapter_count": 2,
            "chapters": [
                {"id": 1, "label": "第一章", "title": "始まりの余熱"},
                {"id": 45, "label": "第四十五章", "title": "王都的清晨"},
            ],
            "volumes": [
                {"id": 2, "title": "第二卷", "chapter_ids": [45]},
            ],
        }


def _structured_data(response_text: str) -> tuple[str, dict[str, object]]:
    match = re.search(
        r'<script id="structured-data" type="application/ld\+json">(.*?)</script>',
        response_text,
        re.DOTALL,
    )
    assert match is not None
    return match.group(1), json.loads(match.group(1))


def test_book_page_returns_server_rendered_book_seo_without_changing_body(
    monkeypatch,
) -> None:
    monkeypatch.setattr(main, "repository", lambda: SeoRepository())
    client = TestClient(main.app)
    response = client.get(
        f"/books/{PUBLIC_BOOK_ID}",
        headers={"Host": "testserver"},
    )

    expected_title = (
        "Re:从零开始的异世界生活[Web版]全文在线阅读_免费TXT下载_长月达平｜OOH Story"
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert f"<title>{expected_title}</title>" in response.text
    assert (
        f'<link rel="canonical" href="{main.SITE_ORIGIN}/books/{PUBLIC_BOOK_ID}">'
        in response.text
    )
    assert (
        '<meta name="keywords" content="Re:从零开始的异世界生活[Web版], '
        "Re:从零开始的异世界生活[Web版]在线阅读" in response.text
    )
    assert f'<meta property="og:title" content="{expected_title}">' in response.text
    assert f'<meta name="twitter:title" content="{expected_title}">' in response.text
    assert "菜月昴来到异世界" in response.text.split("</head>", 1)[0]
    assert "菜月昴来到异世界" not in response.text.split("</head>", 1)[1]
    assert "X-OOHStory-SEO-CSP-Hash" not in response.headers
    json_ld_text, json_ld = _structured_data(response.text)
    entities = json_ld["@graph"]
    assert any(
        entity.get("@type") == "Organization"
        and entity.get("logo", {}).get("url") == f"{main.SITE_ORIGIN}/icon-512.png"
        for entity in entities
    )
    assert any(
        entity.get("@type") == "WebSite"
        and entity.get("publisher") == {"@id": f"{main.SITE_ORIGIN}/#organization"}
        for entity in entities
    )
    assert any(
        entity.get("@type") == "Book"
        and entity.get("name") == "Re:从零开始的异世界生活[Web版]"
        for entity in entities
    )
    expected_hash = b64encode(sha256(json_ld_text.encode("utf-8")).digest()).decode(
        "ascii"
    )
    assert f"'sha256-{expected_hash}'" in response.headers["content-security-policy"]
    assert {
        directive.strip().casefold()
        for directive in response.headers["cache-control"].split(",")
    } == {"public", "max-age=300", "no-transform"}


def test_chapter_and_volume_pages_return_page_specific_raw_html(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(main, "repository", lambda: SeoRepository())
    monkeypatch.setattr(
        main,
        "_rate_limiter",
        main.RateLimiter(
            reader_guard_enabled=True,
            reader_guard_path=tmp_path / "reader-abuse.sqlite3",
            reader_probe_secret_path=tmp_path / "reader-probe.key",
        ),
    )
    client = TestClient(main.app)

    chapter = client.get(
        f"/books/{PUBLIC_BOOK_ID}/chapters/45",
        headers={"Host": "testserver"},
    )
    volume = client.get(
        f"/books/{PUBLIC_BOOK_ID}/volumes/2",
        headers={"Host": "testserver"},
    )

    assert chapter.status_code == 200
    assert "第四十五章 · 王都的清晨" in chapter.text
    assert f"{main.SITE_ORIGIN}/books/{PUBLIC_BOOK_ID}/chapters/45" in chapter.text
    assert any(
        entity.get("@type") == "Chapter"
        for entity in _structured_data(chapter.text)[1]["@graph"]
    )
    assert chapter.headers["cache-control"] == "private, no-store, no-transform"
    assert chapter.headers["cdn-cache-control"] == "no-store"
    assert chapter.headers["cloudflare-cdn-cache-control"] == "no-store"
    assert volume.status_code == 200
    assert "Re:从零开始的异世界生活[Web版] 第二卷" in volume.text
    assert f"{main.SITE_ORIGIN}/books/{PUBLIC_BOOK_ID}/volumes/2" in volume.text
    assert any(
        entity.get("@type") == "CollectionPage"
        for entity in _structured_data(volume.text)[1]["@graph"]
    )


def test_missing_book_and_reader_children_return_404(monkeypatch) -> None:
    class MissingSeoRepository(SeoRepository):
        def get_book(self, book_id: str) -> dict[str, object]:
            raise main.NotFoundError("作品不存在或正文尚未就绪")

    monkeypatch.setattr(main, "repository", lambda: MissingSeoRepository())
    client = TestClient(main.app)

    for path in (
        f"/books/{PUBLIC_BOOK_ID}",
        f"/books/{PUBLIC_BOOK_ID}/chapters/45",
        f"/books/{PUBLIC_BOOK_ID}/volumes/2",
    ):
        response = client.get(path, headers={"Host": "testserver"})
        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store"


def test_numeric_public_book_routes_are_not_exposed() -> None:
    client = TestClient(main.app)

    for path in (
        "/api/v1/books/304735",
        "/api/v1/books/304735/chapters",
        "/books/304735",
        "/books/304735/chapters/1",
    ):
        response = client.get(path, headers={"Host": "testserver"})
        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store"


class SitemapRepository:
    def sitemap_book_ids(self) -> list[str]:
        return [PUBLIC_BOOK_ID]

    def get_book(self, book_id: str) -> dict[str, str]:
        assert book_id == PUBLIC_BOOK_ID
        return {"public_id": book_id, "title": "样本书"}

    def reader_catalog(self, book_id: str) -> dict[str, object]:
        assert book_id == PUBLIC_BOOK_ID
        return {
            "chapters": [
                {"id": 1, "label": "第一章", "title": "开端"},
                {"id": 1602, "label": "第一千六百章", "title": "重逢"},
            ]
        }


def test_robots_and_sitemap_use_real_clean_200_urls(monkeypatch) -> None:
    monkeypatch.setattr(main, "repository", lambda: SitemapRepository())
    client = TestClient(main.app)
    robots = client.get("/robots.txt", headers={"Host": "testserver"})
    crawler_headers = {
        "Host": "testserver",
        "CF-Connecting-IP": "66.249.64.1",
    }
    sitemap = client.get("/sitemap.xml", headers=crawler_headers)

    assert robots.status_code == 200
    assert "User-agent: *\n" in robots.text
    assert "Allow: /\n" in robots.text
    assert "Disallow: /api/\n" in robots.text
    assert f"Sitemap: {main.SITE_ORIGIN}/sitemap.xml\n" in robots.text
    assert sitemap.status_code == 200
    assert sitemap.headers["content-type"].startswith("application/xml")
    root = ElementTree.fromstring(sitemap.content)
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    sitemap_urls = [item.text for item in root.findall("s:sitemap/s:loc", namespace)]
    assert sitemap_urls == [
        f"{main.SITE_ORIGIN}/sitemaps/static.xml",
        f"{main.SITE_ORIGIN}/sitemaps/books/{PUBLIC_BOOK_ID}.xml",
    ]
    assert "#" not in sitemap.text
    for url in sitemap_urls:
        path = url.removeprefix(main.SITE_ORIGIN) or "/"
        response = client.get(path, headers=crawler_headers)
        assert response.status_code == 200

    static = client.get("/sitemaps/static.xml", headers=crawler_headers)
    static_root = ElementTree.fromstring(static.content)
    static_urls = [item.text for item in static_root.findall("s:url/s:loc", namespace)]
    assert static_urls == [
        f"{main.SITE_ORIGIN}/",
        f"{main.SITE_ORIGIN}/library",
        f"{main.SITE_ORIGIN}/deconstructions",
    ]

    book = client.get(
        f"/sitemaps/books/{PUBLIC_BOOK_ID}.xml",
        headers=crawler_headers,
    )
    book_root = ElementTree.fromstring(book.content)
    book_urls = [item.text for item in book_root.findall("s:url/s:loc", namespace)]
    assert book_urls == [
        f"{main.SITE_ORIGIN}/books/{PUBLIC_BOOK_ID}",
        f"{main.SITE_ORIGIN}/books/{PUBLIC_BOOK_ID}/chapters/1",
        f"{main.SITE_ORIGIN}/books/{PUBLIC_BOOK_ID}/chapters/1602",
    ]


def test_sitemap_escapes_each_location_before_xml_rendering() -> None:
    source = Path(main.__file__).read_text(encoding="utf-8")

    assert "escape(url, quote=True)" in source


def test_www_host_is_served_without_redirecting_to_the_apex_domain() -> None:
    nginx = (Path(main.__file__).parents[1] / "deploy/nginx-oohstory.conf").read_text(
        encoding="utf-8"
    )

    assert "return 301 https://$host$request_uri;" in nginx
    assert "server_name m.reader.example.com;" in nginx
    assert "server_name reader.example.com www.reader.example.com;" in nginx
    assert "server_name www.reader.example.com m.reader.example.com;" not in nginx


class MetricsRepository:
    def __init__(self, source: Path) -> None:
        self.source = source
        self.read_count = 0
        self.download_count = 0
        self.seen: set[tuple[str, str]] = set()

    def public_metrics(self, book_id: str) -> dict[str, int | str]:
        assert book_id == PUBLIC_BOOK_ID
        return {
            "public_id": PUBLIC_BOOK_ID,
            "read_count": self.read_count,
            "download_count": self.download_count,
        }

    def record_public_metric(
        self, book_id: str, visitor_id: str, event: str
    ) -> dict[str, object]:
        if event not in {"read", "download"}:
            raise InputError("未知统计事件")
        key = (visitor_id, event)
        counted = key not in self.seen
        self.seen.add(key)
        if counted and event == "read":
            self.read_count += 1
        if counted and event == "download":
            self.download_count += 1
        return {
            **self.public_metrics(book_id),
            "counted": counted,
        }

    def download_source(self, book_id: str) -> tuple[Path, str]:
        assert book_id == PUBLIC_BOOK_ID
        return self.source, "sample.txt"


def test_metrics_api_strict_uuid_deduplicates_and_never_caches_post(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "sample.txt"
    source.write_text("sample", encoding="utf-8")
    repository = MetricsRepository(source)
    monkeypatch.setattr(main, "repository", lambda: repository)
    client = TestClient(main.app)
    visitor = "d9428888-122b-4ed3-8f18-1d6f0f585c8d"

    first = client.post(
        f"/api/v1/books/{PUBLIC_BOOK_ID}/metrics/read",
        headers={"Host": "testserver"},
        json={"visitor_id": visitor},
    )
    repeated = client.post(
        f"/api/v1/books/{PUBLIC_BOOK_ID}/metrics/read",
        headers={"Host": "testserver"},
        json={"visitor_id": visitor},
    )

    assert first.status_code == 200
    assert first.json()["counted"] is True
    assert first.headers["cache-control"] == "no-store"
    assert repeated.json()["counted"] is False
    metrics = client.get(
        f"/api/v1/books/{PUBLIC_BOOK_ID}/metrics", headers={"Host": "testserver"}
    )
    assert metrics.json()["read_count"] == 1
    assert metrics.headers["cache-control"] == "no-cache"


def test_metrics_api_rejects_bad_uuid_event_form_and_cross_origin(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "sample.txt"
    source.write_text("sample", encoding="utf-8")
    repository = MetricsRepository(source)
    monkeypatch.setattr(main, "repository", lambda: repository)
    client = TestClient(main.app)
    valid = "d9428888-122b-4ed3-8f18-1d6f0f585c8d"

    bad_uuid = client.post(
        f"/api/v1/books/{PUBLIC_BOOK_ID}/metrics/read",
        headers={"Host": "testserver"},
        json={"visitor_id": "not-a-uuid"},
    )
    uppercase = client.post(
        f"/api/v1/books/{PUBLIC_BOOK_ID}/metrics/read",
        headers={"Host": "testserver"},
        json={"visitor_id": valid.upper()},
    )
    extra = client.post(
        f"/api/v1/books/{PUBLIC_BOOK_ID}/metrics/read",
        headers={"Host": "testserver"},
        json={"visitor_id": valid, "ip": "127.0.0.1"},
    )
    invalid_event = client.post(
        f"/api/v1/books/{PUBLIC_BOOK_ID}/metrics/open",
        headers={"Host": "testserver"},
        json={"visitor_id": valid},
    )
    form = client.post(
        f"/api/v1/books/{PUBLIC_BOOK_ID}/metrics/read",
        headers={"Host": "testserver"},
        data={"visitor_id": valid},
    )
    cross_origin = client.post(
        f"/api/v1/books/{PUBLIC_BOOK_ID}/metrics/read",
        headers={"Host": "testserver", "Origin": "https://attacker.invalid"},
        json={"visitor_id": valid},
    )
    legacy_recommend = client.post(
        f"/api/v1/books/{PUBLIC_BOOK_ID}/metrics/recommend",
        headers={"Host": "testserver"},
        json={"visitor_id": valid},
    )

    assert bad_uuid.status_code == 422
    assert uppercase.status_code == 422
    assert extra.status_code == 422
    assert invalid_event.status_code == 400
    assert form.status_code in {415, 422}
    assert cross_origin.status_code == 403
    assert legacy_recommend.status_code == 401
    assert legacy_recommend.json()["detail"] == (
        "请登录后捐赠 1 小时阅读经验时长，为这本好书助力推荐"
    )
    assert repository.read_count == 0


def test_download_get_and_head_never_increment_metrics(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "sample.txt"
    source.write_text("sample", encoding="utf-8")
    repository = MetricsRepository(source)
    monkeypatch.setattr(main, "repository", lambda: repository)
    client = TestClient(main.app)

    get_response = client.get(
        f"/api/v1/books/{PUBLIC_BOOK_ID}/download", headers={"Host": "testserver"}
    )
    head_response = client.head(
        f"/api/v1/books/{PUBLIC_BOOK_ID}/download", headers={"Host": "testserver"}
    )

    assert get_response.status_code == 200
    assert head_response.status_code == 200
    assert repository.download_count == 0
