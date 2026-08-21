from tests.frontend_contract_source import frontend_contract_source
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_page_jump_uses_current_and_total_pages_as_placeholder() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "placeholder: `${currentPage}/${data.page_count}`" in script
    assert "placeholder: `共 ${formatNumber(data.page_count)} 页`" not in script
    assert "value: String(currentPage)" not in script
    assert "if (!jumpInput.value.trim())" in script


def test_public_brand_uses_ooh_story_spelling() -> None:
    public_files = [
        PROJECT_ROOT / "static" / "app.js",
        PROJECT_ROOT / "static" / "index.html",
        PROJECT_ROOT / "static" / "manifest.webmanifest",
    ]
    content = "\n".join(path.read_text(encoding="utf-8") for path in public_files)

    assert "OOH STORY" in content
    assert "OOH Story" in content
    assert "OHH STORY" not in content
    assert "OHH Story" not in content


def test_reader_assets_use_current_cache_busters() -> None:
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = frontend_contract_source(PROJECT_ROOT)

    assert 'href="/styles.css?v=20260821-account-nav-touch1"' in html
    assert 'src="/audiobook-lifecycle.js?v=20260811-audiobook-v15-5-preload-contract"' in html
    assert 'src="/audiobook-fallback.js?v=20260811-audiobook-v15-5-preload-contract"' in html
    assert 'src="/app.js?v=20260821-account-nav-touch1"' in html
    assert 'href="/api/v1/home/hero-cover?variant=mobile"' not in html
    assert "data-hero-primer" not in html
    assert 'src="/account-ui.js?v=20260821-account-nav-touch1"' in html
    assert (
        'src="/audiobook-cache.js?v=20260813-audiobook-v15-20-five-window"'
        in html
    )
    assert "ux_mode: 'redirect'" in script
    assert "login_uri: location.origin" in script
    assert "'oohstory-web-link-v1' : 'oohstory-web-redirect-v1'" in script
    assert "href: '/downloads/android/latest.apk'" in script
    assert "下载 APK v1.18.21 安装包" in script
    assert "20260801-metrics1" not in html
    assert "20260801-seo1" not in html
    assert "20260801-progress1" not in html
    assert "20260730-reader2" not in html
    assert "20260730-brand1" not in html
    assert "['dashboard', '#/admin', '概览']" in script
    assert "accountApi('/api/v1/admin/summary')" in script


def test_transient_session_enrichment_failure_does_not_fake_logout() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "let data\n  try {\n    data = await accountApi('/api/v1/auth/session')" in script
    assert "A temporary network/upstream failure is not proof" in script
    assert "Profile enrichment may be independently throttled" in script


def test_disclaimer_publishes_complete_copyright_and_dmca_policy() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "staticPage('版权声明与 DMCA 政策', 'Copyright & DMCA Policy'" in script
    assert "平台性质与用户生成内容（UGC）" in script
    assert "通知—删除" in script
    assert "24 至 48 小时" in script
    assert "反通知（Counter-Notice）" in script
    assert "重复侵权与虚假投诉" in script
    assert "mailto:help@example.com?subject=Copyright%20Notice%20%2F%20DMCA" in script
    assert "English Summary" in script
    assert "最近更新：2026 年 7 月 12 日" in script


def test_book_seo_keywords_only_update_head_metadata() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "function bookSeoTitle(book)" in script
    assert "`${title}全文在线阅读_免费TXT下载_${author}｜${SITE_NAME}`" in script
    assert "function bookSeoKeywords(book)" in script
    assert "setMetaContent('meta[name=\"keywords\"]'" in script
    assert "title: bookSeoTitle(book)" in script
    assert "keywords: bookSeoKeywords(book)" in script
    assert "node('h1', { text: book.title })" in script


def test_reader_starts_tts_at_visible_paragraph_and_loads_interline_comments() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert "initialChapterComments" in script
    assert "/chapters/${requestedChapterId}/comments" in script
    assert "const readerParagraphFromPoint = (clientX, clientY, fallbackParagraph = null) =>" in script
    assert "document.elementsFromPoint?.(x, y)" in script
    assert "document.caretPositionFromPoint?.(x, y)?.offsetNode" in script
    assert "document.caretRangeFromPoint?.(x, y)?.startContainer" in script
    assert "const resolvedParagraph = readerParagraphFromPoint(clientX, clientY, paragraph) || paragraph" in script
    assert "const fallback = insideReader(fallbackParagraph)" in script
    assert "readerContent.addEventListener('pointerdown', event => {" in script
    assert (
        "if (!settingsVisible) currentParagraphHint = ttsFirstVisibleParagraph()"
        in script
    )
    assert "const startIdx = explicitStart\n        ? Math.max(0, startParagraph)\n        : 0" in script
    assert "ttsBackendManifest(startIdx, false)" in script
    assert "start_paragraph_index: Math.max(0, Number(startParagraph) || 0)" in script
    assert "const startInfo = payload.start && typeof payload.start === 'object' ? payload.start : null" in script
    assert "Number.isFinite(startItemIndex) ? Number(item.index) >= startItemIndex" in script
    assert "/api/v1/tts/speak" not in script
    assert "class: 'reader-paragraph'" in script
    assert "class: 'interline-action tts'" in script
    assert "class: 'interline-action comment'" in script
    assert "text: '字里行间'" in script
    assert "class: 'interline-bubble'" in script
    assert "class: 'interline-avatar-fallback'" in script
    assert "src: author.avatar_url" in script
    assert "class: 'interline-author-rank'" in script
    assert "reading.name || '只如初见'" in script
    assert "comment.viewer_like_count" in script
    assert "comment.like_count ?? comment.thanks_count" in script
    assert "/paragraph-comments/${comment.id}/likes" in script
    assert "已点满 3/3" in script
    assert "node('span', { text: likeLabel })" in script
    assert "text: '♡'" in script
    assert ".interline-like" in styles
    assert "window.alert(error.message || '评论无法发布')" in script
    for selector in (
        ".reader-paragraph",
        ".interline-bubble",
        ".interline-action",
        ".interline-overlay",
        ".interline-dialog",
        ".interline-comment",
        ".interline-like",
        ".interline-composer",
    ):
        assert selector in styles


def test_tts_progress_uses_reader_paragraphs_and_exact_stream_time_mapping() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "const paragraphIndex = Math.max(0, Number(session.paragraphIndex) || 0)" in script
    assert "const paragraphCount = Math.max(1, Number(session.paragraphCount) || 1)" in script
    assert "((paragraphIndex + 1) / paragraphCount) * 100" in script
    assert "Math.min(paragraphCount, paragraphIndex + 1)" in script
    assert "state.ttsSession.paragraphCount = ttsPlanParagraphCount(plan)" in script
    assert "const ttsResolvedStreamPlanIndex" in script
    assert "const progressPlanIndex = ttsHlsMode" in script
    assert (
        ": (!ttsSegmentFallbackMode && ttsChapterPlan.length "
        "? ttsResolvedStreamPlanIndex() : Math.max(0, ttsPlanIndex))"
        in script
    )
    assert "ttsCurrentItemOffsetSeconds(progressPlanIndex)" in script
    assert "activeItem?.index ?? current.absoluteItemIndex ?? current.itemIndex" in script
    assert "if (!item.durationExact) break" not in script


def test_volume_desktop_matches_book_detail_content_width_and_library_return_context() -> (
    None
):
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert ".vol-detail-layout > .detail-main" in styles
    assert "flex: 0 1 928px" in styles
    assert "width: min(100%, 928px)" in styles
    assert "flex: 0 1 720px" not in styles
    assert "width: min(100%, 720px)" not in styles
    volume_layout = styles[
        styles.index(".vol-detail-layout {") : styles.index(
            ".vol-detail-layout > .detail-main"
        )
    ]
    volume_cover = styles[
        styles.index(".vol-detail-cover {") : styles.index(".vol-detail-cover img")
    ]
    mobile_volume = styles[styles.index("/* Mobile volume/illust responsive */") :]
    assert "margin: 14px 0 70px" in volume_layout
    assert "position: sticky" in volume_cover
    assert "top: 92px" in volume_cover
    assert "align-self: start" in volume_cover
    assert "position: static" in mobile_volume
    assert "top: auto" in mobile_volume
    assert "function safeLibraryReturnPath(value)" in script
    assert "function withLibraryReturn(path, returnTo)" in script
    assert "bookCard(book, { returnTo: libraryReturnPath })" in script
    assert "href: libraryReturnPath, text: '← 返回书库'" in script


def test_reading_identity_uses_hour_minute_duration_without_decimal_hours() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "function formatReadingDuration(seconds" in script
    assert "Math.ceil(raw / 60)" in script
    assert "Math.floor(raw / 60)" in script
    assert "formatReadingDuration(reading?.active_seconds)" in script
    assert (
        "formatReadingDuration(reading?.seconds_to_next, { remaining: true })" in script
    )


def test_account_navigation_centers_on_desktop_and_success_actions_fade_out() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert ".account-nav { display:flex; justify-content:center;" in styles
    assert ".account-nav { justify-content:flex-start;" in styles
    assert "function showAccountSuccessToast(message)" in script
    assert "showAccountSuccessToast('个人资料保存成功')" in script
    assert "showAccountSuccessToast(result.message)" in script
    assert "animation: account-success-toast-life 3s ease both" in styles
    assert "@keyframes account-success-toast-life" in styles


def test_header_login_button_uses_text_without_avatar_icon() -> None:
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert '<span class="account-label">登录</span>' in html
    assert 'class="reading-rank-icon rank-silver rank-level-01" hidden' in html
    assert 'src="/reading-level-icons/v13/level-01.webp"' in html
    assert 'class="account-avatar"' not in html
    assert "accountButton.querySelector('.account-avatar')" not in script
    assert "function readingRankIcon(reading" in script
    assert "class: 'account-name-row'" in script
    assert ".account-label {" in styles
    assert "display: inline; max-width: 78px;" in styles
    assert "function readingRankAsset(level)" in script
    assert "/reading-level-icons/v13/level-" in script
    assert ".rank-art {" in styles


def test_reading_rank_v13_assets_cover_all_eighteen_levels() -> None:
    asset_dir = PROJECT_ROOT / "static" / "reading-level-icons" / "v13"
    assets = sorted(asset_dir.glob("level-*.webp"))
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert [path.name for path in assets] == [
        f"level-{level:02d}.webp" for level in range(1, 19)
    ]
    assert all(path.stat().st_size > 1_000 for path in assets)
    for level in range(1, 19):
        padded = f"{level:02d}"
        assert (
            f".rank-level-{padded} {{ --rank-mask: url('/reading-level-icons/v13/level-{padded}.webp'); }}"
            in styles
        )
    assert ".reading-rank-icon::before" in styles
    assert ".reading-rank-icon::after" in styles
    assert "@keyframes reading-rank-glint" in styles
    assert "@keyframes reading-rank-aura" in styles
    assert "-webkit-mask: var(--rank-mask) center / contain no-repeat;" in styles
    assert ".reading-rank-icon::after { animation: none !important; }" in styles
    assert (
        "readingRankIcon({ level: index + 1, roman, name }"
        in frontend_contract_source(PROJECT_ROOT)
    )


def test_reader_rank_names_match_the_current_identity_system() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    for name in ("长风万里", "扶摇九霄", "凌云绝顶", "摘星揽月", "天人合一"):
        assert name in script
    for retired in ("静候轮回", "伊人知否", "情深不寿", "惠极必伤", "天赋神权"):
        assert retired not in script


def test_light_novel_volumes_use_the_same_cards_without_cover_art() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert (
        "const hasVolumes = Boolean(catalog.volumes && catalog.volumes.length > 0)"
        in script
    )
    assert "if (hasVolumes)" in script
    assert "hasVolCovers" not in script
    assert "href: contextualHref(`/books/${bookId}/volumes/${vol.id}`)" in script
    assert "Number(vol.id) === 1 && book.cover_url && !book.cover_is_default" in script
    assert "const renderVolumePlaceholder = () =>" in script
    assert "coverImg.addEventListener('error', renderVolumePlaceholder" in script
    assert ".vol-detail-cover.is-placeholder" in styles
    assert ".vol-cover-placeholder::before" in styles


def test_volume_page_reuses_reference_component_without_synthetic_empty_section() -> (
    None
):
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert "const volumeChapterDisplayTitle = title =>" in script
    assert "text: volumeChapterDisplayTitle(ch.title)" in script
    assert "const genericVolumeTitle = /^第" in script
    assert "node('h1', { text: volumeDisplayTitle })" in script
    assert "let illustSection = null" in script
    assert "if (illustPaths.length > 0)" in script
    assert "text: '本卷暂无可展示插画'" not in script
    assert "text: '章节目录仍可正常阅读'" not in script
    assert ".illust-empty {" not in styles


def test_mobile_volume_chapter_directory_expands_without_nested_scrolling() -> None:
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    mobile_volume = styles[styles.index("/* Mobile volume/illust responsive */") :]

    assert ".vol-detail-layout .chapter-list {" in mobile_volume
    assert "grid-template-columns: 1fr;" in mobile_volume
    assert "max-height: none;" in mobile_volume
    assert "overflow: visible;" in mobile_volume
    assert "grid-template-columns: 30px minmax(0, 1fr);" not in mobile_volume
    assert ".vol-detail-layout .chapter-index {" not in mobile_volume
    assert ".vol-detail-layout .chapter-link strong {" in mobile_volume
    assert "white-space: normal;" in mobile_volume
    assert "overflow-wrap: anywhere;" in mobile_volume


def test_mobile_search_inputs_prevent_ios_focus_zoom_without_overflow() -> None:
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert (
        ".hero-bottom .search-bar input { max-width: 100%; padding: 6px 8px; "
        "font-size: 16px; }"
    ) in styles
    assert (
        ".library-toolbar .field input { min-width: 0; max-width: 100%; "
        "font-size: 16px; }"
    ) in styles
    assert ".library-toolbar { grid-template-columns: 1fr; }" in styles


def test_account_ui_uses_system_categories_compact_history_and_rank_icon() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert "api('/api/v1/categories')" in script
    assert "node('select', { name: 'category', required: '' }" in script
    assert "请选择系统分类" in script
    assert "node('input', { name: 'category'" not in script
    assert "class: 'account-book-title-row'" in script
    assert "class: 'account-reading-time'" in script
    assert "class: 'account-latest-chapter'" in script
    assert script.index("class: 'account-latest-chapter'") < script.index(
        "class: 'account-book-context'"
    )
    assert "class: 'account-logout-zone'" in script
    assert "reading-level-seal' }, [readingRankIcon(reading)]" in script
    assert "flex-wrap:nowrap" in styles
    assert "text-overflow:ellipsis; white-space:nowrap" in styles
    for selector in (
        ".account-book-cover.record-cover",
        ".account-book-title-row",
        ".account-history-status",
        ".account-reading-time",
        ".account-logout-zone",
        ".notification-page-heading",
        ".submission-history-panel",
        ".notification-row.unread",
        ".notification-stream",
        ".notification-page-button",
    ):
        assert selector in styles


def test_all_mobile_search_controls_keep_ios_safe_font_size() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert script.count("type: 'search'") >= 2
    assert 'input[type="search"]' in styles
    assert "font-size:16px!important" in styles


def test_home_hero_prefers_exact_then_approximate_chapter_count() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "const exactChapterCount = Number(book.chapter_count)" in script
    assert "const approximateChapterCount = Number(book.approx_chapter_count)" in script
    assert "exactChapterCount > 0" in script
    assert "approximateChapterCount > 0 ? approximateChapterCount : '?'" in script
    assert "text: `${chapterCount}章`" in script


def test_home_hero_long_summary_cannot_push_cover_carousel_down() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert "const HERO_SUMMARY_MAX_CHARS = 180" in script
    assert "function compactHeroSummary(value)" in script
    assert "`${text.slice(0, HERO_SUMMARY_MAX_CHARS - 2).trimEnd()}……`" in script
    assert "text: compactHeroSummary(book.summary)" in script
    assert "flex: 1 1 0;" in styles
    assert "min-height: 68px; flex: 0 0 auto; box-sizing: border-box;" in styles
    assert "max-height: calc(1.75em * 3);" in styles
    assert ".hero .hero-carousel-tabs { min-height: 33px;" in styles
    assert ".hero .hero-carousel-tab { flex: 0 0 20px; height: 27px;" in styles
    assert (
        ".hero-book-info { min-height: auto; flex: 0 0 auto; gap: 3px; overflow: visible; }"
        in styles
    )


def test_mobile_home_hero_uses_an_adaptive_edge_to_edge_cover() -> None:
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert "grid-template-columns: clamp(104px, 32vw, 136px) minmax(0, 1fr);" in styles
    assert "aspect-ratio: 2 / 3;" in styles
    assert (
        """.hero .hero-cover .hero-carousel-slide img {
    display: block;
    object-fit: cover;
    object-position: center;
  }"""
        in styles
    )
    assert (
        ".hero .hero-cover .hero-carousel-slide img { object-fit: contain; }"
        not in styles
    )
    assert ".hero-right { padding: 10px 12px 8px 8px;" in styles
    assert "box-shadow: inset 0 0 0 1px var(--line), var(--shadow-sm);" in styles


def test_home_continue_reading_uses_account_history_presentation() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "function readingHistoryPresentation(item, { local = false } = {})" in script
    assert "const cloudHistory = Array.isArray(state.cloudState?.history)" in script
    assert "hasOverallProgress ? '全书进度' : '本章进度'" in script
    assert (
        "const historyPresentation = kind === 'history' ? readingHistoryPresentation(item) : null"
        in script
    )
    assert "class: 'continue-reading-progress'" in script
    assert "accountBootstrapPromise.then(refreshHomeContinueReading)" in script


def test_home_loads_primary_first_and_defers_secondary_near_view() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "api('/api/v1/home/primary', { cache: 'no-store' })" in script
    assert "data-hero-primer" not in script
    assert "api('/api/v1/home/secondary', { cache: 'no-store' })" in script
    assert "const deferredObserver = new IntersectionObserver" in script
    assert "{ rootMargin: '200px 0px' }" in script
    assert "const accountBootstrapPromise = bootstrapAccount()" in script
    assert (
        "route()\n  accountBootstrapPromise.then(refreshHomeContinueReading).catch"
        in script
    )
    assert "bootstrapAccount().finally(route)" in script


def test_contact_page_mobile_layout_overrides_desktop_grid() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    contact_base = styles.index("/* Contact info grid */")
    mobile_override = styles.index("@media (max-width: 720px)", contact_base)
    mobile_styles = styles[mobile_override:]

    assert mobile_override > contact_base
    assert "grid-template-columns: minmax(0, 1fr);" in mobile_styles
    assert "width: calc(100% - 24px);" in mobile_styles
    assert "overflow-wrap: anywhere;" in styles
    assert "function loadContact()" in script
    assert "class: 'contact-grid'" in script


def test_initial_html_has_complete_honest_search_metadata() -> None:
    import json
    import re

    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")

    for marker in (
        '<html lang="zh-CN">',
        '<meta name="description"',
        '<meta name="keywords"',
        '<meta name="robots"',
        '<meta name="author" content="OOH Story">',
        '<meta name="referrer" content="strict-origin-when-cross-origin">',
        '<meta property="og:title"',
        '<meta property="og:description"',
        '<meta property="og:url"',
        '<meta property="og:image"',
        '<meta name="twitter:card" content="summary">',
        '<link rel="canonical" href="https://reader.example.com/">',
    ):
        assert marker in html
    keywords_match = re.search(
        r'<meta name="keywords" content="([^"]+)">',
        html,
    )
    assert keywords_match is not None
    keywords = [item.strip() for item in keywords_match.group(1).split(",")]
    assert keywords == [
        "OOH Story",
        "免费小说阅读",
        "免费小说下载",
        "中文小说",
        "全本小说",
        "TXT电子书",
        "深度拆书",
    ]
    assert len(keywords) == len(set(keywords))
    assert "SearchAction" not in html
    assert "twitter:site" not in html
    payload = re.search(
        r'<script id="structured-data" type="application/ld\+json">(.+?)</script>',
        html,
    )
    assert payload is not None
    structured = json.loads(payload.group(1))
    assert structured["@context"] == "https://schema.org"
    entities = {item["@type"]: item for item in structured["@graph"]}
    assert entities["Organization"]["url"] == "https://reader.example.com/"
    assert (
        entities["Organization"]["logo"]["url"] == "https://reader.example.com/icon-512.png"
    )
    assert entities["WebSite"]["url"] == "https://reader.example.com/"
    assert entities["WebSite"]["publisher"] == {
        "@id": "https://reader.example.com/#organization"
    }


def test_noscript_copy_is_semantic_and_not_keyword_stuffing() -> None:
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "<noscript>" in html
    assert "OOH Story 中文小说阅读与拆书档案" in html
    assert 'href="/library"' in html
    assert 'href="/deconstructions"' in html


def test_public_navigation_uses_clean_urls_and_brand_images_have_alt_text() -> None:
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = frontend_contract_source(PROJECT_ROOT)

    assert 'href="#/' not in html
    assert html.count('src="/icon-192.png?v=20260730-icon1" alt="OOH Story 标志"') == 2
    for public_fragment in (
        "#/book/",
        "#/read/",
        "#/library",
        "#/rankings",
        "#/deconstruction",
    ):
        assert public_fragment not in script


def test_spa_updates_route_seo_from_real_payloads_without_html_injection() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "function setSeo({" in script
    assert "structuredData.textContent = JSON.stringify" in script
    assert "'@type': 'Organization'" in script
    assert "publisher: { '@id': `${SITE_ORIGIN}/#organization` }" in script
    assert "canonicalLink.setAttribute('href', canonical)" in script
    assert "url.origin !== SITE_ORIGIN" in script
    assert "book.cover_url || SITE_DEFAULT_IMAGE" in script
    assert "chapter.book.title" in script
    assert "data.documents.map(document => cleanSeoText(document.label" in script
    assert "innerHTML" not in script
    for canonical_path in (
        "canonicalPath: '/'",
        "canonicalPath: '/library'",
        "publicUrl(`/books/${encodeURIComponent(book.public_id || bookId)}`)",
        "publicUrl(`/books/${encodeURIComponent(requestedBookId)}/chapters/${requestedChapterId}`)",
        "canonicalPath: '/deconstructions'",
        "`/deconstructions/${encodeURIComponent(data.slug || slug)}`",
    ):
        assert canonical_path in script


def test_clean_paths_map_to_hash_routes_only_when_hash_is_absent() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "function pathFromLocation()" in script
    assert "if (location.hash)" in script
    assert "location.pathname.replace" in script
    assert "return `/book/${bookMatch[1]}`" in script
    assert "return `/read/${chapterMatch[1]}/${chapterMatch[2]}`" in script
    assert "return `/book/${volumeMatch[1]}/volume/${volumeMatch[2]}`" in script
    assert "const path = pathFromLocation()" in script


def test_missing_chapter_titles_use_sequential_chinese_fallbacks() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "function formatChineseChapterNumber" in script
    assert "function chapterPresentation(chapter, position = null)" in script
    assert "const fallback = `第${formatChineseChapterNumber(sequence)}章`" in script
    assert "catalog.chapters.forEach((chapter, index)" in script
    assert "catalog.chapters.forEach((item, index)" in script
    assert "原文未标注章名'" in script  # legacy payload is recognized, never rendered
    assert "return title && title !== label ? title : '原文未标注章名'" not in script


def test_generated_brand_icon_is_wired_for_browsers_and_pwa() -> None:
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    manifest = (PROJECT_ROOT / "static" / "manifest.webmanifest").read_text(
        encoding="utf-8"
    )
    static_dir = PROJECT_ROOT / "static"

    for filename in (
        "favicon.ico",
        "favicon-16.png",
        "favicon-32.png",
        "favicon-48.png",
        "favicon-96.png",
        "apple-touch-icon.png",
        "icon-192.png",
        "icon-512.png",
        "icon-maskable-512.png",
    ):
        assert (static_dir / filename).is_file()
    assert (
        '<link rel="icon" href="/oohstory-favicon-48.png" type="image/png" sizes="48x48">'
        in html
    )
    assert (
        '<link rel="icon" href="/oohstory-favicon-96.png" type="image/png" sizes="96x96">'
        in html
    )
    assert '<link rel="shortcut icon" href="/favicon.ico">' in html
    assert "/favicon.ico?v=" not in html
    for filename, expected_size in (("favicon-48.png", 48), ("favicon-96.png", 96)):
        payload = (static_dir / filename).read_bytes()
        assert payload[:8] == b"\x89PNG\r\n\x1a\n"
        assert int.from_bytes(payload[16:20], "big") == expected_size
        assert int.from_bytes(payload[20:24], "big") == expected_size
    ico = (static_dir / "favicon.ico").read_bytes()
    image_count = int.from_bytes(ico[4:6], "little")
    ico_widths = {ico[6 + (offset * 16)] or 256 for offset in range(image_count)}
    assert {16, 32, 48} <= ico_widths
    assert "20260730-icon1" in html
    assert 'src="/icon-192.png?v=20260730-icon1"' in html
    assert '"purpose": "maskable"' in manifest
    assert '"theme_color": "#031440"' in manifest


def test_mobile_reader_uses_delayed_single_tap_and_catalog_double_tap() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "function bindMobileReaderGestures" in script
    assert "now - lastTapAt <= 320" in script
    assert "window.clearTimeout(singleTapTimer)" in script
    assert "mobileNav.classList.toggle('visible', willShow)" in script
    assert "toggleCatalog()" in script
    assert (
        "Math.abs(readerScrollMetrics(stage).scrollTop - touchStartScrollTop) > 4"
        in script
    )
    assert "event.target.closest('a, button, input, select, textarea')" in script


def test_mobile_reader_navigation_is_overlay_only_and_catalog_is_drawer() -> None:
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    script = frontend_contract_source(PROJECT_ROOT)

    assert ".reader-sidebar.mobile-visible" in styles
    assert ".reader-catalog-backdrop.visible" in styles
    assert "position: fixed;" in styles
    assert "transform: translateY(calc(100% + 28px));" in styles
    assert "min-height: calc(100dvh - 100px)" not in styles
    assert "reader-locate-button" in script


def test_reader_caches_catalog_chapters_and_prefetches_adjacent() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "sessionStorage.getItem(readerCatalogCacheKey(bookId))" in script
    assert "Date.now() - savedAt < 5 * 60 * 1000" not in script
    assert "readerCatalogCacheKey(bookId)}:saved-at" not in script
    assert "state.readerInflight.has(key)" in script
    assert "READER_CHAPTER_CACHE_LIMIT = 8" in script
    assert "getReaderChapter(requestedBookId, id).catch(() => {})" in script
    assert "if (catalogRendered) return" in script
    assert "if (!window.matchMedia('(max-width: 720px)').matches)" in script
    assert "ensureCatalog()" in script


def test_reader_has_complete_mobile_settings_and_progress_contract() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    for label in (
        "页面亮度（不修改系统）",
        "阅读字号",
        "阅读行距",
        "阅读背景色",
        "平移翻页",
        "覆盖翻页",
        "仿真翻页",
        "上下翻页",
        "护眼模式",
        "自动阅读速度",
        "全书进度",
    ):
        assert label in script
    assert "repeat(6, minmax(0, 1fr))" in styles
    assert ".reader-settings-panel.visible" in styles
    assert ".reader-stage.reader-mode-simulation .reader-content" in styles


def test_reader_boundary_gestures_require_start_and_end_boundary() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "const metrics = readerScrollMetrics(stage)" in script
    assert (
        "const bottomAtStart = touchStartScrollTop + metrics.clientHeight >= metrics.scrollHeight - 3"
        in script
    )
    assert (
        "const bottomAtEnd = metrics.scrollTop + metrics.clientHeight >= metrics.scrollHeight - 3"
        in script
    )
    assert "deltaY < 0 && bottomAtStart && bottomAtEnd && nextId" in script
    assert "deltaY > 0 && atTop && previousId" in script


def test_reading_progress_is_browser_local_versioned_and_lru_bounded() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "READING_PROGRESS_STORAGE_KEY = 'oohstory-reading-progress'" in script
    assert "READING_PROGRESS_SCHEMA = 'oohstory-reading-progress'" in script
    assert "READING_PROGRESS_VERSION = 1" in script
    assert "READING_PROGRESS_BOOK_LIMIT = 100" in script
    assert "books: {}" in script
    assert "chapterId: normalizedChapterId" in script
    assert "within: Math.min(1, Math.max(0" in script
    assert "updatedAt: Date.now()" in script
    assert ".sort(([, left], [, right]) => right.updatedAt - left.updatedAt)" in script
    assert ".slice(0, READING_PROGRESS_BOOK_LIMIT)" in script


def test_book_detail_resumes_only_a_chapter_still_in_the_catalog() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert (
        "const savedProgress = getReadingProgress(book.public_id || bookId)" in script
    )
    assert (
        "catalog.chapters.find(chapter => Number(chapter.id) === Number(savedProgress.chapterId))"
        in script
    )
    assert "const firstChapter = catalog.chapters.find(chapter =>" in script
    assert "Number(chapter.id) === Number(catalog.first_chapter_id)" in script
    assert "text: resumeChapter ? '📖 继续阅读' : '📖 开始阅读'" in script
    assert (
        "href: contextualHref(`/books/${bookId}/chapters/${readingChapter.id}`)"
        in script
    )


def test_book_cards_show_read_marker_and_detail_actions_are_equal_width() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert "class: 'book-read-badge', text: '阅读'" in script
    assert "const actionRow = node('div', { class: 'detail-actions' }" in script
    assert ".detail-actions > .primary-button" in styles
    assert ".detail-actions > .ghost-button" in styles
    assert "width: 100%;" in styles


def test_book_detail_does_not_expose_or_mutate_the_internal_source_url() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "const sourcePanel = null" in script
    assert "book.source_url" not in script
    assert "source-url" not in script


def test_reader_restores_normalized_position_and_saves_at_lifecycle_boundaries() -> (
    None
):
    script = frontend_contract_source(PROJECT_ROOT)

    assert "Number(savedProgress?.chapterId) === requestedChapterId" in script
    assert (
        "setReaderScrollTop(stage, restoreWithin * Math.max(0, metrics.scrollHeight - metrics.clientHeight), layoutMode)"
        in script
    )
    assert (
        "pageIndex = Math.round(restoreWithin * Math.max(0, pageCount - 1))" in script
    )
    assert "READING_PROGRESS_SAVE_DELAY = 750" in script
    assert "scheduleReadingProgressSave()" in script
    assert "flushReadingProgress()" in script
    assert "window.addEventListener('pagehide', pageHideListener)" in script
    assert "document.addEventListener('visibilitychange', visibilityListener)" in script
    assert script.index("state.readerNavigation?.cancelTap?.()") < script.index(
        "loading()", script.index("async function route()")
    )


def test_progress_storage_failure_is_silent_and_signed_in_progress_syncs_safely() -> (
    None
):
    script = frontend_contract_source(PROJECT_ROOT)

    assert "function localStorageGet(key)" in script
    assert "function localStorageSet(key, value)" in script
    assert "window.localStorage.setItem(key, value)" in script
    assert "} catch {\n    return false\n  }" in script
    assert (
        "Local progress remains authoritative until the next successful sync." in script
    )
    assert "scheduleReadingHistorySync(publicId, entry)" in script
    assert "'/api/v1/me/state'" in script


def test_public_metrics_use_device_uuid_and_recommendations_require_a_reading_gift() -> (
    None
):
    script = frontend_contract_source(PROJECT_ROOT)

    assert "PUBLIC_METRIC_VISITOR_KEY = 'oohstory-public-metric-visitor-id'" in script
    assert "crypto.randomUUID()" in script
    assert "crypto.getRandomValues(new Uint8Array(16))" in script
    assert "JSON.stringify({ visitor_id: visitorId })" in script
    assert "method: 'POST'" in script
    assert "keepalive" in script
    assert "trackBookMetric(requestedBookId, 'read').catch(() => {})" in script
    assert "onclick: event =>" in script
    assert "if (!event.isTrusted) return" in script
    assert "function queueBookMetricBeacon(bookId, event)" in script
    assert "navigator.sendBeacon(" in script
    assert "{ type: 'application/json' }" in script
    assert "queueBookMetricBeacon(bookId, 'download')" in script
    assert "window.setTimeout(refreshMetricCounts, 600)" in script
    assert "trackBookMetric(bookId, 'download', { keepalive: true })" in script
    assert ".finally(() => {\n            window.location.assign(destination)" in script
    assert (
        "new Promise(resolve => window.setTimeout(() => resolve(null), 350)"
        not in script
    )
    assert "['read', 'download'].includes(event)" in script
    assert "trackBookMetric(bookId, 'recommend')" not in script
    assert "accountApi(`/api/v1/books/${bookId}/recommend`" in script
    assert "title: '为这本好书助力？'" in script
    assert "message: '捐赠 1 小时阅读经验时长，将好书推荐给更多人。'" in script
    assert "primaryLabel: '助力推荐'" in script
    assert "secondaryLabel: '再想想'" in script
    assert "body: { event_id: randomUuidV4() }" in script
    assert "你已经推荐过这本书" not in script
    assert "同一账户对同一本书只扣除一次" not in script
    assert "人已阅读 / ${formatNumber(metrics.download_count)} 人已下载" in script
    assert "按本设备、每本书、每类动作去重" not in script
    assert (
        "reading-progress"
        not in script.split("JSON.stringify({ visitor_id: visitorId })")[0][-200:]
    )


def test_account_collections_share_cards_fill_covers_and_paginate_by_ten() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert "const pageSize = 10" in script
    assert "class: 'account-book-cover record-cover'" in script
    assert "class: 'account-book-copy record-copy'" in script
    assert "action('首页', 1" in script
    assert "action('<', currentPage - 1" in script
    assert "action('>', currentPage + 1" in script
    assert "placeholder: '自定义页数'" in script
    assert "action('尾页', pageCount" in script
    assert ".account-book-cover.record-cover img" in styles
    assert "object-fit:cover" in styles
    assert (
        "object-fit:contain"
        not in styles.split(".account-book-cover.record-cover img", 1)[1].split("}", 1)[
            0
        ]
    )


def test_favorite_detail_refreshes_authoritative_metric() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert (
        "const updatedMetrics = await api(`/api/v1/books/${bookId}/metrics`, { cache: 'no-store' })"
        in script
    )
    assert (
        "favoriteCountEl.textContent = formatNumber(updatedMetrics.favorite_count || 0)"
        in script
    )


def test_home_seo_uses_the_previous_clean_headline() -> None:
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = frontend_contract_source(PROJECT_ROOT)

    title = "OOH Story｜免费中文小说阅读与深度拆书"
    assert f"<title>{title}</title>" in html
    assert title in script
    assert "OOH Story 好故事｜免费小说阅读、免费小说下载与TXT电子书" not in html
    assert (
        "const SITE_DESCRIPTION = 'OOH Story 是免费、开源、自托管的中文小说阅读站"
        in script
    )


def test_public_book_routes_use_opaque_ids_without_catalog_id_leaks() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "[A-Za-z0-9_-]{22}" in script
    assert "book.public_id" in script
    assert "catalog_id" not in script
    assert "/^\\/book\\/\\d+$/" not in script


def test_reader_is_fullscreen_without_top_toolbar_and_auto_scroll_can_stop() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert "reader-desktop-toolbar" in script
    assert "reader-desktop-toolbar" in styles
    assert "rangeSetting('阅读行距'" in script
    assert "0.05)" in script
    assert "state.reader.mode = 'vertical'" in script
    assert (
        "setReaderScrollTop(stage, metrics.scrollTop + pixelsPerSecond * elapsed / 1000, 'vertical')"
        in script
    )
    assert "autoFrame = requestAnimationFrame(tick)" in script
    assert "onclick: stopAutoReading" in script
    assert "reader-nav.auto-active" in styles


def test_mobile_vertical_reader_uses_page_scroll_and_immersive_platform_contracts() -> (
    None
):
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    manifest = (PROJECT_ROOT / "static" / "manifest.webmanifest").read_text(
        encoding="utf-8"
    )

    assert 'name="apple-mobile-web-app-capable" content="yes"' in html
    assert (
        'name="apple-mobile-web-app-status-bar-style" content="black-translucent"'
        in html
    )
    assert "function readerUsesPageScroll(mode = state.reader.mode)" in script
    assert "document.scrollingElement || document.documentElement" in script
    assert "window.visualViewport?.height || window.innerHeight" in script
    assert "requestFullscreen({ navigationUI: 'hide' })" in script
    assert 'a[href^="/books/"][href*="/chapters/"]' in script
    assert ':root[data-reader-mode="vertical"] body.reader-mode' in styles
    assert "position: static;" in styles
    assert "overflow-y: auto;" in styles
    assert '"display_override": ["fullscreen", "standalone", "minimal-ui"]' in manifest


def test_ios_fullscreen_header_respects_notch_and_dynamic_island_safe_area() -> None:
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert "viewport-fit=cover" in html
    assert (
        'name="apple-mobile-web-app-status-bar-style" content="black-translucent"'
        in html
    )
    assert "min-height: calc(68px + env(safe-area-inset-top, 0px));" in styles
    assert "calc(10px + env(safe-area-inset-top, 0px))" in styles
    assert "min-height: calc(60px + env(safe-area-inset-top, 0px));" in styles
    assert "calc(8px + env(safe-area-inset-top, 0px))" in styles
    assert "max(12px, env(safe-area-inset-right, 0px))" in styles
    assert "max(12px, env(safe-area-inset-left, 0px))" in styles
    assert "padding-bottom: calc(72px + env(safe-area-inset-bottom, 0px));" in styles


def test_home_keeps_deconstruction_archive_off_homepage() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "orderHomeDeconstructions" in script
    assert "api('/api/v1/deconstructions')" in script  # archive route remains available
    assert "深度拆书" in script
    assert "const homeDeconstructionSection" not in script
    assert "homeDeconstructionSection," not in script
    assert "从读完，到真正读懂" not in script


def test_web_registration_keeps_invitation_code_optional() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "name: 'invite_code'" in script
    assert "邀请码（选填）" in script
    assert "邮箱密码账户也可单独创建" in script
    assert "required: '', placeholder: '请输入有效邀请码'" not in script
    assert "body.invite_code = values.get('invite_code') || ''" in script
    assert "if (!isLogin) {" in script
    assert "/api/v1/auth/google/link/start" in script
    assert "state.account.google_linked" in script
    assert "Google 首次登录即可直接进入，无需另外注册" in script
    assert "邮箱密码可稍后在个人中心按需启用" in script
    assert "启用邮箱密码登录" in script
    assert "/api/v1/me/password/setup" in script
    assert "mode: 'verify'" in script


def test_google_web_login_uses_current_browser_popup_with_explicit_fallback() -> None:
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = frontend_contract_source(PROJECT_ROOT)

    assert 'href="/styles.css?v=20260821-account-nav-touch1"' in html
    assert 'src="/account-ui.js?v=20260821-account-nav-touch1"' in html
    assert "ux_mode: 'popup'" in script
    assert "callback: handleCredential" in script
    assert "mode === 'link' ? '/api/v1/auth/google/link' : '/api/v1/auth/google'" in script
    assert "body: { id_token: credential, client: 'web' }" in script
    assert "弹窗无法使用？切换兼容登录" in script
    assert "forceRedirect: true" in script
    assert "ux_mode: 'redirect'" in script
    assert script.index("ux_mode: 'popup'") > script.index("if (forceRedirect)")


def test_web_deconstruction_upload_copy_uses_result_focused_message() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert (
        "提交标准 ZIP 档案，系统会完成结构识别、内容复核和正式入库。"
        in script
    )
    assert "请上传 ZIP。系统会在隔离沙箱完成 ClamAV 验毒" not in script


def test_login_payload_excludes_registration_only_fields() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    assert "const body = {" in script
    assert "email: values.get('email')" in script
    assert "password: values.get('password')" in script
    assert "if (!isLogin) {" in script
    assert "body.display_name = values.get('display_name')" in script
    assert "body.invite_code = values.get('invite_code')" in script


def test_web_identity_and_comment_forms_use_local_content_guard() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert "function localUserContentIssue" in script
    assert "normalize('NFKC')" in script
    assert "const identityIssue = localUserContentIssue" in script
    assert "const contentIssue = localUserContentIssue(content)" in script
    assert "拼接链接均无法发布" in script
    assert "function openUserContentNotice" in script
    assert "换个昵称吧" in script
    assert "这条评论需要修改" in script
    assert "这条评论暂时不能发布" in script
    assert "返回修改" in script
    assert ".content-notice-overlay" in styles
    assert ".content-notice-dialog" in styles


def test_home_deconstruction_cards_have_honest_status_and_accessible_progress() -> None:
    script = frontend_contract_source(PROJECT_ROOT)

    for label in ("拆解中", "已完成", "档案已建立", "打开档案 →"):
        assert label in script
    assert (
        "item.documents.map(document => node('span', { text: document.label }))"
        in script
    )
    assert "item.completed_chapters" in script
    assert "item.total_chapters" in script
    assert "role: 'progressbar'" in script
    assert "'aria-valuenow': String(percentage)" in script
    assert "document.filename" not in script
    assert "deconstructionBackdrop(item)" in script
    assert "item.cover_url" in script


def test_home_deconstruction_layout_has_featured_desktop_and_mobile_snap_rail() -> None:
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert (
        "grid-template-columns: minmax(300px, 1.12fr) repeat(2, minmax(0, 1fr));"
        in styles
    )
    assert ".home-deconstruction-card.featured" in styles
    assert "grid-row: 1 / span 2;" in styles
    assert "scroll-snap-type: x mandatory;" in styles
    assert "scroll-snap-align: start;" in styles
    assert "flex: 0 0 min(82vw, 310px);" in styles


def test_archive_cards_use_cover_backdrops_and_responsive_editorial_layout() -> None:
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert "deconstruction-cover-backdrop" in script
    assert "deconstruction-archive-stats" in script
    assert "class: `deconstruction-card${index === 0 ? ' featured' : ''}`" in script
    assert ".deconstruction-card.has-cover" in styles
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in styles
    assert ".deconstruction-card.featured" in styles


def test_deconstruction_golden_three_uses_file_browser_and_actions_have_contrast() -> (
    None
):
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert "Array.isArray(doc.items)" in script
    assert "name: doc.subdirectory" in script
    assert "items: doc.items" in script
    assert "loadSubdirFile(tab.name" in script
    assert ".report-head-actions .ghost-button" in styles
    assert "color: #245878;" in styles
    assert ':root[data-theme="dark"] .report-head-actions .ghost-button' in styles


def test_auth_cover_centers_brand_mark_and_uses_reduced_motion_orbits() -> None:
    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = frontend_contract_source(PROJECT_ROOT)
    styles = (PROJECT_ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    assert 'href="/styles.css?v=20260821-account-nav-touch1"' in html
    assert 'src="/account-ui.js?v=20260821-account-nav-touch1"' in html
    assert "class: 'auth-art-visual'" in script
    assert "class: 'auth-art-halo auth-art-halo-one'" in script
    assert "class: 'auth-art-halo auth-art-halo-two'" in script
    assert script.count("class: 'auth-art-orbit-dot'") == 2
    assert ".auth-art-visual {" in styles
    assert "width: 230px;" in styles
    assert "height: 230px;" in styles
    assert "place-items: center;" in styles
    assert "@keyframes auth-art-halo-turn" in styles
    reduced_motion = styles[styles.index("@media (prefers-reduced-motion: reduce)") :]
    assert ".auth-art-halo { animation: none !important; will-change: auto; }" in reduced_motion
    assert "animation: none !important;" in reduced_motion
    assert ".auth-art-visual { width:104px; height:104px;" in styles
