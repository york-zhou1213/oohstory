from app.comment_moderation import (
    chapter_paragraphs,
    moderate_comment,
    moderate_display_name,
    moderation_stats,
)


def test_moderation_catches_obfuscation_without_blocking_normal_discussion() -> None:
    for content, category in (
        ("你真是傻\u200b逼", "辱骂"),
        ("无 码 成 人 影 片", "涉黄"),
        ("刷 单 返 利，稳赚不赔", "涉诈"),
        ("博 彩 投 注", "博彩"),
        ("h t t p s : / / example 点 com", "链接"),
        ("w w w 点 example 点 c n", "链接"),
        ("请看 e x a m p l e c o m", "链接"),
        ("备用网址 e.x.a.m.p.l.e 点 c o m", "链接"),
        ("备用网址 e x a m p l e d 0 t c 0 m", "链接"),
        ("玩 球 + 我 V x x x x 赚 钱", "引流"),
        ("联系薇 信 abc12345", "引流"),
        ("电话 １３８ ００１３ ８０００", "引流"),
        ("出售冰 毒，支持同城配送", "涉毒"),
    ):
        result = moderate_comment(content)
        assert result.allowed is False
        assert result.category == category
    assert moderate_comment("这一段把人物的犹豫写得很真实，感谢分享。 ").allowed is True
    assert moderate_comment("这章写主角靠兼职赚钱，代价和动机都很可信。").allowed is True
    assert moderate_comment("这一段提到微信读书和纸质书的差异。 ").allowed is True


def test_identity_moderation_blocks_promotional_handles_and_lexicon_is_large() -> None:
    for name in (
        "玩球+我vXxXx赚钱",
        "兼职日结联系VX88888",
        "福 利 姬 私 聊",
        "w w w 点 bonus 点 c o m",
        "博彩开户注册",
    ):
        result = moderate_display_name(name)
        assert result.allowed is False
        assert "昵称" in result.detail
    assert moderate_display_name("长风万里_07").allowed is True
    stats = moderation_stats()
    assert stats["term_count"] >= 1000
    assert {"辱骂", "涉黄", "涉诈", "博彩", "涉毒"}.issubset(stats["categories"])


def test_chapter_paragraph_keys_skip_blank_lines_and_illustrations() -> None:
    paragraphs = chapter_paragraphs("第一段\n\n[illustration:a.jpg]\n第二段\r\n")
    assert [item["index"] for item in paragraphs] == [0, 1]
    assert [item["text"] for item in paragraphs] == ["第一段", "第二段"]
    assert all(str(item["key"]).startswith(f"p{index}-") for index, item in enumerate(paragraphs))
