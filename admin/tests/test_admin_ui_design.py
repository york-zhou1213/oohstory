from __future__ import annotations

from pathlib import Path

from conftest import login


ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "src" / "oohstory_admin" / "static" / "admin.css"
JS = ROOT / "src" / "oohstory_admin" / "static" / "admin.js"
PARITY = ROOT / "docs" / "library-ui-parity.md"


def test_shared_console_navigation_and_real_routes(client):
    assert login(client).status_code == 303
    routes = {
        "/admin/": "系统概览",
        "/admin/library": "电子书库",
        "/admin/library/sync": "同步调度",
        "/admin/books/catalog": "书目总量",
        "/admin/books/search": "全局书源搜索",
        "/admin/operations": "运营管理",
        "/admin/pipeline": "管道与服务",
        "/admin/audit": "审计日志",
    }
    for route, title in routes.items():
        response = client.get(route)
        assert response.status_code == 200
        assert title in response.text
        assert 'data-nav-toggle' in response.text
        assert 'data-theme-toggle' in response.text
        assert 'data-sidebar' in response.text
        assert "仅 127.0.0.1 可访问" in response.text


def test_sky_design_system_and_responsive_contracts():
    styles = CSS.read_text(encoding="utf-8")
    scripts = JS.read_text(encoding="utf-8")

    assert "--bg: #dff3ff" in styles
    assert "Sky Console 2.0" in styles
    assert 'html[data-theme="dark"]' in styles
    assert "@media (max-width: 760px)" in styles
    assert "body.nav-open .sidebar" in styles
    assert "font-size: 16px" in styles
    assert "prefers-reduced-motion" in styles
    assert ".content { width: 100%" in styles
    assert ".operations-workspace" in styles
    assert "grid-template-columns: repeat(12,minmax(0,1fr))" in styles
    assert ".operations-table td::before" in styles
    assert "repeat(auto-fill,minmax(205px,1fr))" in styles
    assert "oohstory-admin-theme" in scripts
    assert "data-nav-toggle" in scripts
    assert 'event.key === "Escape"' in scripts
    assert 'navScrim?.setAttribute("aria-hidden", String(!open))' in scripts


def test_library_parity_controls_are_visible(client):
    assert login(client).status_code == 303
    books = client.get("/admin/books/catalog?view=deconstruction")
    assert books.status_code == 200
    for label in (
        "书目总量",
        "本地书库",
        "番茄书库",
        "正文可用",
        "基调索引",
        "剧情索引",
        "全局拆书库",
    ):
        assert label in books.text
    assert "全局书源搜索与归档" not in books.text
    assert "按站点全力同步正文" not in books.text
    assert "独立目录页只保留当前书目" in books.text

    sync = client.get("/admin/library/sync")
    assert sync.status_code == 200
    assert "书库定时同步" in sync.text
    assert "按站点全力同步正文" in sync.text
    assert 'id="site-full-sync"' in sync.text
    assert "与 Webnovel Writer 电子书库保持同等五站能力" in sync.text
    assert "这里只控制 OOHStory 自有" in sync.text
    assert "站点全力同步" in sync.text
    assert "封面重绘加速器" in sync.text
    assert "电子书库服务控制" in sync.text

    sync_template = (
        ROOT / "src" / "oohstory_admin" / "templates" / "library_sync.html"
    ).read_text(encoding="utf-8")
    assert 'name="books_per_cycle"' in sync_template
    assert 'name="target_per_hour"' in sync_template
    assert 'min="50"' in sync_template

    styles = CSS.read_text(encoding="utf-8")
    assert ".cover-redraw-control" in styles
    assert ".cover-redraw-metrics" in styles

    books_template = (
        ROOT / "src" / "oohstory_admin" / "templates" / "books.html"
    ).read_text(encoding="utf-8")
    assert "选择本页全部" in books_template
    assert "跳至" in books_template
    assert 'action="{{ base_path }}/books/catalog"' in books_template

    pipeline = client.get("/admin/pipeline")
    assert 'data-service-search' in pipeline.text
    assert 'data-service-status' in pipeline.text

    audit = client.get("/admin/audit")
    assert 'data-audit-search' in audit.text


def test_parity_matrix_documents_every_reference_surface():
    matrix = PARITY.read_text(encoding="utf-8")
    for expected in (
        "LibraryView.vue",
        "LibraryCatalogView.vue",
        "LibraryAssetView.vue",
        "LibraryBookDetailModal.vue",
        "全局书源搜索",
        "断点续跑",
        "剧情证据与改编",
        "明确排除",
        "对标项目基调匹配",
    ):
        assert expected in matrix
