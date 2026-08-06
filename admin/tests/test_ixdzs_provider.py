from types import SimpleNamespace

import pytest

from oohstory_library.services.ixdzs_provider import AuthorizedIxdzsProvider


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("带着农场混异界章节目录", "带着农场混异界"),
        ("带着农场混异界txt下载八零zip", "带着农场混异界"),
        ("带着农场混异界界", "带着农场混异界"),
        ("魔门败类最新章节", "魔门败类"),
        ("魔门败类笔趣阁无弹窗", "魔门败类"),
        ("魔门败类txt全集下载", "魔门败类"),
    ],
)
def test_seo_landing_title_is_detected_and_normalized(raw, canonical):
    assert AuthorizedIxdzsProvider.is_search_landing_title(raw)
    assert AuthorizedIxdzsProvider.normalize_source_title(raw) == canonical


def test_search_omits_seo_landing_pages_before_catalog_import():
    provider = AuthorizedIxdzsProvider()
    provider._request = lambda *_args, **_kwargs: SimpleNamespace(
        url="https://ixdzs8.com/bsearch?q=book",
        text="""
        <li class="burl"><h3 class="bname"><a href="/read/473295/">魔门败类最新章节</a></h3><span class="bauthor">惊涛骇浪</span></li>
        <li class="burl"><h3 class="bname"><a href="/read/124604/">魔门败类</a></h3><span class="bauthor">惊涛骇浪</span></li>
        """,
    )

    results = provider.search("魔门败类")

    assert [item["remote_id"] for item in results] == ["124604"]


def test_detail_rejects_seo_landing_page():
    provider = AuthorizedIxdzsProvider()
    provider._request = lambda *_args, **_kwargs: SimpleNamespace(
        url="https://ixdzs8.com/read/473295/",
        text='<meta property="og:novel:book_name" content="魔门败类最新章节">',
    )

    with pytest.raises(ValueError, match="搜索落地页"):
        provider.detail("473295")
