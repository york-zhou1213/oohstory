from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import login


class FakeCatalog:
    def browse(self, *, view, query, category, page, page_size, **filters):
        result = {
            "view": view,
            "view_label": "基调索引" if view == "tone" else "书目总量",
            "items": [
                {
                    "catalog_id": 42,
                    "public_id": "abcdefghijklmnopqrstuv",
                    "title": "星海测试",
                    "author": "作者",
                    "category": "科幻",
                    "library_id": "local",
                    "readable": True,
                    "book_status_label": "已完结",
                    "source_bytes": 36000,
                    "updated_at": "2026-08-03",
                    "updated_label": "2026-08-03",
                    "tone_indexed": True,
                    "plot_indexed": False,
                    "word_count": 12000,
                    "chapter_count": 12,
                    "chapter_count_known": True,
                    "chapter_count_label": "12 章",
                    "cover_available": True,
                    "cover_url": "/api/admin/books/catalog/42/cover?v=1",
                    "primary_tone_tags": ["群星", "成长"],
                    "secondary_tone_tags": [],
                    "genre_tags": ["科幻"],
                    "tone_source": "rule_evidence",
                    "tone_confidence": 0.88,
                    "summary": "真实索引简介",
                }
            ],
            "total": 1,
            "page": page,
            "page_size": page_size,
            "page_count": 1,
            "query": query,
            "category": category,
            "categories": [{"category": "科幻", "count": 1}],
            "tags": [{"name": "群星", "count": 1}],
            "evidence_total": 0,
            "filters": filters,
        }
        if view == "deconstruction":
            result.update(
                state_counts={
                    "all": 1,
                    "unstarted": 1,
                    "running": 0,
                    "scan": 0,
                    "full": 0,
                    "error": 0,
                },
                batches=[],
            )
            result["items"][0].update(
                deconstruction=None,
                deconstruction_state="unstarted",
            )
        return result

    def book(self, catalog_id):
        return {
            "catalog_id": catalog_id,
            "public_id": "abcdefghijklmnopqrstuv",
            "title": "星海测试",
            "author": "作者",
            "category": "科幻",
            "library_id": "local",
            "body_available": True,
            "chapter_count": 12,
            "chapter_count_known": True,
            "chapter_count_label": "12 章",
            "section_count": 12,
            "word_count": 12000,
            "source_bytes": 36000,
            "cover_available": True,
            "cover_url": "/api/admin/books/catalog/42/cover?v=1",
            "book_status_label": "已完结",
            "indexed_at": "2026-08-03",
            "tone_confidence": 0.9,
            "segment_count": 8,
            "summary": "简介",
            "primary_tone_tags": ["群星"],
            "secondary_tone_tags": [],
            "source_id": "local-42",
            "download_status": "done",
            "updated_at": "2026-08-03",
        }

    def plot_evidence(self, catalog_id, *, page, page_size):
        return {
            "book": {"catalog_id": catalog_id, "title": "星海测试", "author": "作者", "category": "科幻"},
            "items": [{"id": 1, "location": "第1章", "motif_tags": ["启航"], "content": "星舰离港。"}],
            "total": 1,
            "page": page,
            "page_size": page_size,
            "page_count": 1,
        }


def test_catalog_views_detail_and_incremental_index_control(settings, components):
    from oohstory_admin.app import create_app
    from test_library_actions import FakeLibraryActions

    reader, library, systemd, audit = components
    actions = FakeLibraryActions()
    app = create_app(
        settings,
        reader=reader,
        library=library,
        systemd=systemd,
        audit=audit,
        catalog=FakeCatalog(),
        library_actions=actions,
    )
    with TestClient(app) as client:
        assert login(client).status_code == 303
        page = client.get("/admin/books/catalog?view=tone")
        assert page.status_code == 200
        assert "书目总量" in page.text
        assert "番茄书库" in page.text
        assert "全局拆书库" in page.text
        assert "增量更新" in page.text
        assert "完整重建" in page.text
        assert "星海测试" in page.text
        assert "《星海测试》封面" in page.text
        assert "/api/admin/books/catalog/42/cover?v=1" in page.text
        assert "12 章" in page.text
        assert "35.2 KiB" in page.text
        assert "2026-08-03" in page.text
        assert "已完结" in page.text
        assert "章节目录" in page.text
        assert '<option value="28" selected>' in page.text
        assert "独立目录页只保留当前书目" in page.text
        assert "书库定时同步" not in page.text

        detail = client.get("/admin/books/catalog/42")
        assert detail.status_code == 200
        assert "《星海测试》封面" in detail.text
        assert "/api/admin/books/catalog/42/cover?v=1" in detail.text
        assert "12 章" in detail.text
        assert ">未识别<" not in detail.text
        assert "真实章节与阅读指标" in detail.text

        evidence = client.get("/admin/books/catalog/42/plot")
        assert evidence.status_code == 200
        assert "星舰离港" in evidence.text
        assert "启航" in evidence.text

        csrf = client.get("/api/admin/session").json()["csrf_token"]
        action = client.post(
            "/admin/books/index",
            data={"csrf_token": csrf, "index_kind": "tone", "force": "0"},
            follow_redirects=False,
        )
        assert action.status_code == 303
        assert actions.calls[-1] == (
            "index_refresh",
            {"index_kind": "tone", "force": False},
        )


def test_catalog_rejects_unknown_view(client):
    assert login(client).status_code == 303
    assert client.get("/admin/books?view=../../etc").status_code == 400
    assert client.get("/api/admin/books?view=../../etc").status_code == 400


def test_catalog_cover_route_requires_login_and_serves_verified_file(
    settings, components, tmp_path
):
    from oohstory_admin.app import create_app
    from test_library_actions import FakeLibraryActions

    cover = tmp_path / "cover.png"
    cover.write_bytes(b"\x89PNG\r\n\x1a\nadmin-cover")

    class CoverCatalog(FakeCatalog):
        def cover_file(self, catalog_id):
            if catalog_id != 42:
                raise KeyError(catalog_id)
            return {"path": cover, "media_type": "image/png", "size": cover.stat().st_size}

    reader, library, systemd, audit = components
    app = create_app(
        settings,
        reader=reader,
        library=library,
        systemd=systemd,
        audit=audit,
        catalog=CoverCatalog(),
        library_actions=FakeLibraryActions(),
    )
    with TestClient(app) as client:
        assert client.get("/api/admin/books/catalog/42/cover").status_code == 401
        assert login(client).status_code == 303
        response = client.get("/api/admin/books/catalog/42/cover")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store, no-transform"
        assert response.content == cover.read_bytes()
