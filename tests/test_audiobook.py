from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

from app.audiobook import (
    AudiobookService,
    CAST_VOICES,
    ENGINE_VERSION,
    FEMALE_VOICES,
    MALE_VOICES,
    SharedAudioWork,
    mp3_duration_ms,
    analyze_chapter,
    apply_authoritative_ai_gender,
    deterministic_voice,
    has_spoken_content,
    normalize_chapter_text,
    reconcile_character_gender,
    reusable_historical_voice,
    rendition_voice,
    split_tts_text,
    trusted_persisted_cast,
)
from app import main
from app.settings import Settings
from fastapi.testclient import TestClient
import pytest


BOOK_ID = "A" * 22


def test_audiobook_storage_root_supports_dedicated_mount_and_legacy_fallback(
    tmp_path: Path,
) -> None:
    configured = Settings(
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        allowed_hosts=("testserver",),
        audiobook_audio_root=tmp_path / "mounted-audio",
    )
    legacy = replace(configured, audiobook_audio_root=None)

    assert configured.audiobook_storage_root == tmp_path / "mounted-audio"
    assert legacy.audiobook_storage_root == tmp_path / "state"


class FakeRepository:
    _mysql = None

    def reader_chapter(self, book_id: str, chapter_id: int):
        assert book_id == BOOK_ID
        chapters = {
            1: {
                "title": "开始",
                "content": "[本地插图：x.jpg]\n林雪轻声说道：“别怕，我在。”\n张强：快走！",
                "next_id": 2,
            },
            2: {"title": "继续", "content": "阿尔法说道：“安全了。”", "next_id": 3},
            3: {
                "title": "匿名对白",
                "content": "风从长廊尽头吹来。\n「快走！」\n【不要回头。】\n[我会跟上。]",
                "next_id": None,
            },
        }
        return chapters[chapter_id]


def settings():
    return {
        "mode": "smart",
        "narrator": "mocheng",
        "voice": "nuanxi",
        "emotion": "auto",
        "rate": 1.2,
    }


def advanced_dialogue_fingerprints():
    return [
        {
            "canonical_name": "赫亦铭",
            "aliases": ["赫亦铭", "亦铭"],
            "gender": "male",
            "gender_confidence": 0.99,
            "voice_key": "kuangyun",
        },
        {
            "canonical_name": "邱恋",
            "aliases": ["邱恋", "我"],
            "gender": "female",
            "gender_confidence": 0.99,
            "voice_key": "nuanxi",
        },
        {
            "canonical_name": "大卫再次",
            "aliases": ["大卫再次", "再次"],
            "gender": "male",
            "gender_confidence": 0.99,
            "voice_key": "tongzhen",
        },
        {
            "canonical_name": "护士小姐",
            "aliases": ["护士小姐", "小姐"],
            "gender": "female",
            "gender_confidence": 0.99,
            "voice_key": "lingxian",
        },
        {
            "canonical_name": "赫亦铭冷",
            "aliases": ["赫亦铭冷", "铭冷"],
            "gender": "male",
            "gender_confidence": 0.99,
            "voice_key": "mocheng",
        },
    ]


def test_normalization_and_bounded_split_remove_media_markers():
    assert normalize_chapter_text(" A\r\n[本地插图：x.jpg]\n\nB ") == "A\nB"
    chunks = split_tts_text("甲。" + "乙" * 500, limit=100)
    assert chunks
    assert all(0 < len(chunk) <= 100 for chunk in chunks)


def test_analyzer_attributes_dialogue_and_rejects_pronoun_as_character():
    characters, segments = analyze_chapter(
        "林雪轻声说道：“别怕。”\n张强：快走！\n她说道：“知道了。”"
    )
    names = {item["canonical_name"] for item in characters}
    assert {"林雪", "张强"} <= names
    assert "她" not in names
    assert any(item["kind"] == "dialogue" and item["speaker"] == "林雪" for item in segments)
    assert next(item for item in characters if item["canonical_name"] == "林雪")["gender"] == "female"


def test_analyzer_preserves_financial_colon_rows_as_complete_narration() -> None:
    content = (
        "私募基金利润：4280万马克\n"
        "私募基金投资回报率：82％\n"
        "投资委托方集体预分：4280万×60％（分成比例）＝2568万马克\n"
        "投资委托方投资回报率：50.35％\n"
        "投资委托方各资本分红：\n"
        "柏林尼特斯勒国际投资公司：428万马克\n"
        "波恩圣米哥金融投资公司：435万马克\n"
        "柏林M．T．D文化投资公司：362万马克\n"
        "柏林STYL风险投资公司：352万马克"
    )

    characters, segments = analyze_chapter(content)

    assert characters == []
    assert [(item["paragraph_index"], item["kind"], item["text"]) for item in segments] == [
        (0, "narration", "私募基金利润:4280万马克"),
        (1, "narration", "私募基金投资回报率:82%"),
        (2, "narration", "投资委托方集体预分:4280万×60%(分成比例)=2568万马克"),
        (3, "narration", "投资委托方投资回报率:50.35%"),
        (4, "narration", "投资委托方各资本分红:"),
        (5, "narration", "柏林尼特斯勒国际投资公司:428万马克"),
        (6, "narration", "波恩圣米哥金融投资公司:435万马克"),
        (7, "narration", "柏林M.T.D文化投资公司:362万马克"),
        (8, "narration", "柏林STYL风险投资公司:352万马克"),
    ]


@pytest.mark.parametrize(
    "content, expected",
    [
        ("血量：120/300", "血量:120/300"),
        ("任务进度：82％", "任务进度:82%"),
        ("年龄：18岁", "年龄:18岁"),
        ("状态：正常", "状态:正常"),
        ("北京时间：12:30", "北京时间:12:30"),
        ("银河银行：2500万元", "银河银行:2500万元"),
        ("异常统计字段：17项", "异常统计字段:17项"),
    ],
)
def test_analyzer_preserves_cross_genre_structured_colon_fields(
    content: str,
    expected: str,
) -> None:
    characters, segments = analyze_chapter(content)

    assert characters == []
    assert [(item["kind"], item["speaker"], item["text"]) for item in segments] == [
        ("narration", "", expected),
    ]


@pytest.mark.parametrize(
    "content, speaker, expected",
    [
        ("张三：4280万不能少。", "张三", "4280万不能少。"),
        ("林雪：82％？你疯了。", "林雪", "82%?你疯了。"),
    ],
)
def test_analyzer_keeps_named_numeric_colon_dialogue(
    content: str,
    speaker: str,
    expected: str,
) -> None:
    _characters, segments = analyze_chapter(content)

    assert [(item["kind"], item["speaker"], item["text"]) for item in segments] == [
        ("dialogue", speaker, expected),
    ]


def test_analyzer_separates_local_role_identity_from_voice_gender() -> None:
    content = (
        "小门童刚一开门，就迎来两位娇客。\n"
        "“这位小哥，这里是林府吗？”她问道。\n"
        "她的鼻梁上有颗米痣，一说话露出两颗小虎牙，十分娇俏。\n"
        "小门童面色微红：“这里是林府，道姑有何事？”\n"
        "“我们找三老爷。”小道童说着回头看了一眼。\n"
        "小门童不由得也随着她的视线往后看。她身后的女子梳着双髻。\n"
        "“好，你等着，我这就是给你们通报！”他说着整个人缩回门里。"
    )

    _characters, segments = analyze_chapter(content)
    dialogue = [item for item in segments if item["kind"] == "dialogue"]

    assert [item["text"] for item in dialogue] == [
        "这位小哥,这里是林府吗?",
        "这里是林府,道姑有何事?",
        "我们找三老爷。",
        "好,你等着,我这就是给你们通报!",
    ]
    assert [item["speaker"] for item in dialogue] == [
        "", "小门童", "小道童", "小门童",
    ]
    assert [item["gender"] for item in dialogue] == [
        "female", "male", "female", "male",
    ]
    assert dialogue[1]["speaker_source"] == "state-before"
    assert dialogue[2]["gender_source"] == "adjacent-pronoun-coreference"


def test_analyzer_current_identity_overrides_previous_pronoun_context() -> None:
    content = (
        "她顾不得什么，抓到旁边一个路人问道：“现在是，什么年？”\n"
        "路人是个成年男子，被她紧紧攥着领子，惊慌失措又带些恼怒：“这是谁家疯子？”\n"
        "他拨开人群走到少女面前，微微含笑道：“今日是庚子年。”"
    )

    _characters, segments = analyze_chapter(content)
    dialogue = [item for item in segments if item["kind"] == "dialogue"]

    assert [(item["text"], item["gender"]) for item in dialogue] == [
        ("现在是,什么年?", "female"),
        ("这是谁家疯子?", "male"),
        ("今日是庚子年。", "male"),
    ]
    assert dialogue[1]["gender_source"] == "explicit-identity"
    assert dialogue[2]["gender_source"] == "explicit-pronoun"


def test_mp3_frame_payload_removes_every_leading_id3v2_tag() -> None:
    first = b"ID3\x04\x00\x00\x00\x00\x00\x04meta"
    second = b"ID3\x04\x00\x00\x00\x00\x00\x03tag"
    frames = b"\xff\xfb\x90\x64audio-frames"

    assert main._mp3_frame_payload(first + second + frames) == frames
    assert main._mp3_frame_payload(frames) == frames


def test_analyzer_supports_all_light_novel_dialogue_brackets():
    _characters, segments = analyze_chapter(
        "‘第零句。’\n“第一句。”\n「第二句。」\n『第三句。』\n【第四句。】\n[第五句。]\n\"第六句。\""
    )
    assert [item["text"] for item in segments] == [
        "第零句。", "第一句。", "第二句。", "第三句。", "第四句。", "第五句。", "第六句。"
    ]
    assert all(item["kind"] == "dialogue" for item in segments)


def test_analyzer_keeps_cross_genre_reference_quotes_as_narration():
    cases = [
        (
            "romance-reference",
            "枕畔那个阔口小药瓶已被清洗干净，里头装满了落桂。"
            "昏暗烛火中，有馥郁甜香隐约飘荡，像极了“她”的气味。",
            "“她”",
        ),
        (
            "science-fiction-acronym",
            "星舰操作台把协议标记为“EVA-02”，状态灯随即转绿。",
            "“EVA-02”",
        ),
        ("urban-number", "门禁屏幕上显示“404”，保安却没有解释。", "“404”"),
        (
            "technology-term",
            "研究者把这套架构称为‘零信任’，并列出了三个条件。",
            "‘零信任’",
        ),
        (
            "academic-title",
            "论文题为「群体记忆的边界」，摘要占了两页。",
            "「群体记忆的边界」",
        ),
        (
            "historical-citation",
            "史官写下『天启三年，北境大旱。』作为卷首引文。",
            "『天启三年，北境大旱。』",
        ),
        (
            "author-reporting",
            "作者写道：“她”是主角。",
            "“她”",
        ),
        (
            "remembered-line",
            "他想起那句“我会回来。”，脚步忽然停了。",
            "“我会回来。”",
        ),
        (
            "reflected-line",
            "他反复咀嚼着“我会回来。”这句话，久久没有开口。",
            "“我会回来。”",
        ),
        (
            "screen-message",
            "屏幕弹出“连接失败。”，工程师记下了错误时间。",
            "“连接失败。”",
        ),
        (
            "textbook-example",
            "“你好。”是教材里的第一句，旁边还配了一幅图。",
            "“你好。”",
        ),
        (
            "object-manipulation-title",
            "林雪抬手翻开“序章”。",
            "“序章”",
        ),
        (
            "object-manipulation-words",
            "林雪抬手指着“天空”两个字。",
            "“天空”",
        ),
        ("scare-quote", "人人口中的【英雄】独自离开了庆功宴。", "【英雄】"),
        (
            "code-label",
            "编译器返回错误代码[ERR_17]，程序随即退出。",
            "[ERR_17]",
        ),
        (
            "ascii-identifier",
            '配置文件里的变量名是"payload_id"，不是函数调用。',
            '"payload_id"',
        ),
        ("word-reference", "他特意在‘自由’两字上加了重音。", "‘自由’"),
        (
            "fixed-impression",
            "百姓心中“官府全是坏人”的固有印象并未消失。",
            "“官府全是坏人”",
        ),
        ("event-name", "等到“春日宴”时，她就会启程。", "“春日宴”"),
        (
            "written-text",
            "他提笔写下“庭前垂柳珍重待春风”。",
            "“庭前垂柳珍重待春风”",
        ),
    ]

    for label, content, quoted_surface in cases:
        characters, segments = analyze_chapter(content)
        rendered = "".join(item["text"] for item in segments)
        assert normalize_chapter_text(quoted_surface) in rendered, label
        assert segments and all(item["kind"] == "narration" for item in segments), label
        assert any(
            item.get("quote_intent") == "narration"
            and item.get("quote_intent_confidence", 0) >= 0.7
            for item in segments
        ), label
        assert characters == [], label


def test_analyzer_preserves_cross_genre_strong_dialogue_evidence():
    cases = [
        ("short-pronoun", "林雪说道：“她。”", "她。"),
        ("police-action", "女警抬手『放下武器！』", "放下武器！"),
        ("immediate-action", "林雪抬手“走！”", "走！"),
        ("standalone-unpunctuated", "「收到」", "收到"),
        ("game-channel", "【队伍】青衣：集合。", "集合。"),
        (
            "cross-paragraph",
            "“潮水要来了。”\n老船长抬头望向漆黑的海面。",
            "潮水要来了。",
        ),
        (
            "dialogue-action-dialogue",
            "“别过来！”他眼里闪过狠色，“否则我开枪！”",
            "否则我开枪！",
        ),
    ]

    for label, content, expected_text in cases:
        _characters, segments = analyze_chapter(content)
        dialogue = [item for item in segments if item["kind"] == "dialogue"]
        expected_text = normalize_chapter_text(expected_text)
        assert any(item["text"] == expected_text for item in dialogue), label


def test_analyzer_classifies_each_quote_before_mixed_line_attribution():
    characters, segments = analyze_chapter(
        "终端将目标标为“R-7”，林雪低声说道：“立即撤离。”"
    )

    narration = [item for item in segments if item["kind"] == "narration"]
    dialogue = [item for item in segments if item["kind"] == "dialogue"]
    assert any("“R-7”" in item["text"] for item in narration)
    assert any(item.get("quote_intent_source") == "embedded-token" for item in narration)
    assert [(item["speaker"], item["text"]) for item in dialogue] == [
        ("林雪", "立即撤离。")
    ]
    assert dialogue[0]["quote_intent"] == "dialogue"
    assert dialogue[0]["quote_intent_source"] == "explicit-speech"
    assert [item["canonical_name"] for item in characters] == ["林雪"]


def test_analyzer_preserves_trailing_actor_and_same_line_dialogue_chains():
    cases = [
        (
            "lip-action",
            "“当然？”苏晚扬唇，神色淡淡，“事情没有那么复杂。”",
            "苏晚",
            ["当然?", "事情没有那么复杂。"],
        ),
        (
            "speech-completion",
            "“都坐吧。”顾川随口说完，转身关上房门。",
            "顾川",
            ["都坐吧。"],
        ),
        (
            "adverbial-answer",
            "“没有。”周衡答得十分干脆，“只要证据。”",
            "周衡",
            ["没有。", "只要证据。"],
        ),
        (
            "object-action",
            "“也好。”沈宁将笔搁在桌上，这才抬头。",
            "沈宁",
            ["也好。"],
        ),
        (
            "head-action",
            "“明白。”许舟颔首，端起茶盏，“成交。”",
            "许舟",
            ["明白。", "成交。"],
        ),
    ]

    for label, content, expected_speaker, expected_texts in cases:
        _characters, segments = analyze_chapter(content)
        dialogue = [item for item in segments if item["kind"] == "dialogue"]
        assert [item["text"] for item in dialogue] == [
            normalize_chapter_text(text) for text in expected_texts
        ], label
        assert {item["speaker"] for item in dialogue} == {expected_speaker}, label


def test_rejected_quote_cannot_seed_speaker_memory_for_later_dialogue():
    known = [
        {
            "canonical_name": "林雪",
            "aliases": ["林雪"],
            "gender": "female",
            "gender_confidence": 0.99,
        },
        {
            "canonical_name": "李梅",
            "aliases": ["李梅"],
            "gender": "female",
            "gender_confidence": 0.99,
        },
    ]
    characters, segments = analyze_chapter(
        "档案把代号记作“林雪”，她抬手“撤离。”",
        known,
    )

    dialogue = next(item for item in segments if item["kind"] == "dialogue")
    assert dialogue["speaker"] == ""
    assert dialogue["speaker_source"] == "anonymous"
    assert any(
        item["kind"] == "narration" and "“林雪”" in item["text"]
        for item in segments
    )
    assert characters == []


def test_analyzer_distinguishes_game_channel_labels_from_bracket_dialogue():
    characters, segments = analyze_chapter(
        "【地图】青衣：老婆。\n"
        "【系统】玩家青衣进入战斗状态。\n"
        "【这是真正的括号对话。】"
    )
    dialogue = [item for item in segments if item["kind"] == "dialogue"]
    assert [(item["speaker"], item["text"]) for item in dialogue] == [
        ("青衣", "老婆。"),
        ("", "这是真正的括号对话。"),
    ]
    assert [item["canonical_name"] for item in characters] == ["青衣"]
    assert any(
        item["kind"] == "narration" and "玩家青衣" in item["text"]
        for item in segments
    )


def test_analyzer_attributes_actions_before_and_after_dialogue():
    characters, segments = analyze_chapter(
        "王姐拍了拍桌子『都安静。』\n"
        "“你们几个先出去吧!”张顶顺挥了挥手。"
    )
    dialogue = [item for item in segments if item["kind"] == "dialogue"]
    assert [(item["speaker"], item["speaker_source"]) for item in dialogue] == [
        ("王姐", "action-before"),
        ("张顶顺", "action-after"),
    ]
    assert all(item["speaker_confidence"] >= 0.9 for item in dialogue)
    cast = {item["canonical_name"]: item for item in characters}
    assert cast["王姐"]["gender"] == "female"
    assert cast["张顶顺"]["gender"] == "male"
    assert deterministic_voice(1, cast["王姐"]) in FEMALE_VOICES
    assert deterministic_voice(1, cast["张顶顺"]) in MALE_VOICES


def test_analyzer_resolves_explanation_chain_without_casting_state_as_actor():
    known = [
        {
            "canonical_name": "云知意",
            "aliases": ["云知意", "大小姐"],
            "gender": "female",
            "gender_confidence": 0.99,
            "voice_key": "qingyan",
        },
    ]
    characters, segments = analyze_chapter(
        "“大小姐今日不必再去见那位……”小梅一时想不起那个赌档东主该作何称呼，"
        "尴尬笑笑，“就是要卖赌档的那位。”\n"
        "“哦，不必了，后头的事自有官差办，不需我出面。"
        "等着听听宿家兄妹从城中带消息回来就行。”\n"
        "见小梅眼神茫然，云知意解释道：“昨日那郝当家接了我的定金，"
        "回城后自会去见各位小东主。”\n"
        "小梅愈发大惑不解：“黑市上的赌档出什么事了？”\n"
        "“多一事不如少一事呗。”云知意笑道。",
        known,
    )

    dialogue = [item for item in segments if item["kind"] == "dialogue"]
    assert [item["speaker"] for item in dialogue] == [
        "小梅", "小梅", "云知意", "云知意", "小梅", "云知意",
    ]
    assert [item["speaker_source"] for item in dialogue] == [
        "action-after",
        "action-between",
        "adjacent-attribution",
        "leading-speech-subject",
        "known-quote-before",
        "speech-verb-after",
    ]
    assert all(item["speaker_confidence"] >= 0.84 for item in dialogue)
    assert {item["canonical_name"] for item in characters} == {"小梅", "云知意"}
    assert "尴尬" not in {item["canonical_name"] for item in characters}


def test_analyzer_keeps_one_actor_across_dialogue_action_dialogue_chain():
    known = advanced_dialogue_fingerprints()
    characters, segments = analyze_chapter(
        "“怎么?你想反悔?”他眼里闪出一抹凶狠来,完全不顾我心里的抵制,"
        "“你不就是个万人上的小姐吗?知不知道有多少女人想要爬上我的床?"
        "邱恋,你不配合,可别怪我不客气!”",
        known,
    )

    dialogue = [item for item in segments if item["kind"] == "dialogue"]
    assert [item["speaker"] for item in dialogue] == ["赫亦铭", "赫亦铭"]
    assert dialogue[0]["speaker_source"] == "action-after"
    assert dialogue[1]["speaker_source"] == "same-paragraph-pronoun"
    assert [item["emotion_hint"] for item in dialogue] == ["angry", "angry"]
    cast = {item["canonical_name"]: item for item in characters}
    assert cast["赫亦铭"]["gender"] == "male"
    assert deterministic_voice(1, cast["赫亦铭"]) in MALE_VOICES


def test_analyzer_keeps_standalone_dialogue_anonymous_with_pronoun_only_followup():
    known = advanced_dialogue_fingerprints()
    characters, segments = analyze_chapter(
        "“这事儿还用我教你吗?那个老女人不是高手吗?想必你的功夫也不差吧!"
        "我给你一分钟的时间,要是让本少爷不高兴,这事儿你就甭想了。”\n"
        "他再次点燃一支烟,饶有兴致的吞云吐雾,他想要占有我,而且还是我主动去迎合他。",
        known,
    )

    dialogue = next(item for item in segments if item["kind"] == "dialogue")
    assert dialogue["speaker"] == ""
    assert dialogue["speaker_source"] == "anonymous"
    assert dialogue["speaker_confidence"] == 0.0
    assert dialogue["gender"] == "male"
    assert dialogue["gender_confidence"] >= 0.99
    assert dialogue["emotion_hint"] == "angry"
    assert characters == []


def test_analyzer_prefers_named_mysql_actor_over_next_paragraph_narrator():
    known = advanced_dialogue_fingerprints()
    characters, segments = analyze_chapter(
        "“你来了大姨妈?”\n"
        "我酸软无力的趴在床上想要休息一会儿的时候,赫亦铭再次出现在我的面前,"
        "他蹙着眉头,很是恼火的样子。\n"
        "我没做声,一句话都不想说。",
        known,
    )

    dialogue = next(item for item in segments if item["kind"] == "dialogue")
    assert dialogue["speaker"] == "赫亦铭"
    assert dialogue["speaker_source"] == "next-paragraph-context"
    assert dialogue["speaker_confidence"] >= 0.85
    assert dialogue["emotion_hint"] == "angry"
    cast = {item["canonical_name"]: item for item in characters}
    assert cast["赫亦铭"]["gender"] == "male"
    assert deterministic_voice(1, cast["赫亦铭"]) in MALE_VOICES


def test_analyzer_keeps_next_paragraph_attribution_anonymous_when_two_actors_compete():
    known = [
        {
            "canonical_name": "林雪",
            "aliases": ["林雪"],
            "gender": "female",
            "gender_confidence": 0.99,
        },
        {
            "canonical_name": "李梅",
            "aliases": ["李梅"],
            "gender": "female",
            "gender_confidence": 0.99,
        },
    ]
    _characters, segments = analyze_chapter(
        "“都别动。”\n林雪抬起头,李梅也转身看向门口。",
        known,
    )

    dialogue = next(item for item in segments if item["kind"] == "dialogue")
    assert dialogue["speaker"] == ""
    assert dialogue["speaker_source"] == "anonymous"


def test_analyzer_does_not_turn_next_paragraph_addressee_into_speaker():
    known = [{
        "canonical_name": "林雪",
        "aliases": ["林雪"],
        "gender": "female",
        "gender_confidence": 0.99,
    }]
    _characters, segments = analyze_chapter(
        "“林雪,你过来一下。”\n林雪抬起头,朝门口走了过去。",
        known,
    )

    dialogue = next(item for item in segments if item["kind"] == "dialogue")
    assert dialogue["speaker"] == ""
    assert dialogue["speaker_source"] == "anonymous"


def test_analyzer_keeps_gendered_family_titles_across_quote_styles():
    characters, segments = analyze_chapter(
        "梅姐抬手“跟我来。”\n"
        "『都让开。』张哥挥了挥手。\n"
        "李奶奶点头【孩子，别怕。】\n"
        "[马上出发。]王伯父站起身。"
    )

    dialogue = [item for item in segments if item["kind"] == "dialogue"]
    assert [item["speaker"] for item in dialogue] == [
        "梅姐", "张哥", "李奶奶", "王伯父"
    ]
    cast = {item["canonical_name"]: item for item in characters}
    assert deterministic_voice(1, cast["梅姐"]) in FEMALE_VOICES
    assert deterministic_voice(1, cast["李奶奶"]) in FEMALE_VOICES
    assert deterministic_voice(1, cast["张哥"]) in MALE_VOICES
    assert deterministic_voice(1, cast["王伯父"]) in MALE_VOICES


def test_analyzer_supports_single_character_kinship_actors_and_emotional_actions():
    characters, segments = analyze_chapter(
        "舅拍案『谁敢动她！』\n"
        "姨轻轻摸了摸我的头【别怕，有我在。】\n"
        "爷抬手[都退下。]"
    )

    dialogue = [item for item in segments if item["kind"] == "dialogue"]
    assert [item["speaker"] for item in dialogue] == ["舅", "姨", "爷"]
    assert [item["gender"] for item in dialogue] == ["male", "female", "male"]
    assert [item["emotion_hint"] for item in dialogue] == [
        "angry", "comforting", "neutral"
    ]
    cast = {item["canonical_name"]: item for item in characters}
    assert deterministic_voice(1, cast["舅"]) in MALE_VOICES
    assert deterministic_voice(1, cast["姨"]) in FEMALE_VOICES
    assert deterministic_voice(1, cast["爷"]) in MALE_VOICES


def test_trailing_state_word_is_not_absorbed_into_known_character_name():
    known = [{
        "canonical_name": "小梅",
        "aliases": ["小梅"],
        "gender": "female",
        "gender_confidence": 0.99,
    }]
    characters, segments = analyze_chapter(
        "“大小姐今日不必再去见那位……”小梅尴尬笑笑，“就是那位。”",
        known,
    )

    dialogue = [item for item in segments if item["kind"] == "dialogue"]
    assert [item["speaker"] for item in dialogue] == ["小梅", "小梅"]
    assert "小梅尴尬" not in {item["canonical_name"] for item in characters}


def test_analyzer_resolves_pronoun_action_from_mysql_character_fingerprint():
    known = [{
        "canonical_name": "林雪",
        "aliases": ["林雪", "雪姐"],
        "gender": "female",
        "gender_confidence": 0.99,
        "voice_key": "nuanxi",
    }]
    characters, segments = analyze_chapter(
        "她拽住了我的胳膊“你别走，我害怕。”",
        known,
    )
    dialogue = next(item for item in segments if item["kind"] == "dialogue")
    assert dialogue["speaker"] == "林雪"
    assert dialogue["speaker_source"] == "action-before"
    assert dialogue["speaker_confidence"] >= 0.75
    assert dialogue["emotion_hint"] == "fearful"
    assert characters[0]["canonical_name"] == "林雪"
    assert characters[0]["gender"] == "female"


def test_analyzer_refuses_unanchored_pronoun_when_fingerprint_gender_is_ambiguous():
    known = [
        {"canonical_name": "林雪", "aliases": ["林雪"], "gender": "female", "gender_confidence": 0.99},
        {"canonical_name": "李梅", "aliases": ["李梅"], "gender": "female", "gender_confidence": 0.99},
    ]
    characters, segments = analyze_chapter("她抬手“跟我走。”", known)
    dialogue = next(item for item in segments if item["kind"] == "dialogue")
    assert dialogue["speaker"] == ""
    assert dialogue["speaker_source"] == "anonymous"
    assert characters == []


def test_analyzer_ignores_legacy_polluted_character_fingerprints():
    known = [
        {"canonical_name": "见状苏艺琳", "aliases": ["艺琳"], "gender": "female", "gender_confidence": 0.99},
        {"canonical_name": "被我揭穿", "aliases": ["揭穿"], "gender": "female", "gender_confidence": 0.99},
    ]
    characters, segments = analyze_chapter("她抬手“跟我走。”", known)
    dialogue = next(item for item in segments if item["kind"] == "dialogue")
    assert dialogue["speaker"] == ""
    assert characters == []


def test_gender_evidence_is_bound_to_the_character_not_any_paragraph_pronoun():
    characters, segments = analyze_chapter(
        "云知意说道：“霍奉卿没搭理他，他已经离开了。”"
    )

    character = next(item for item in characters if item["canonical_name"] == "云知意")
    dialogue = next(item for item in segments if item["kind"] == "dialogue")
    assert character["gender"] == "unknown"
    assert character["gender_source"] == "unknown"
    assert dialogue["gender"] == "unknown"


def test_subject_of_speech_outranks_the_person_after_duixiang_preposition():
    known = [
        {
            "canonical_name": "霍奉卿",
            "aliases": ["霍奉卿"],
            "gender": "male",
            "gender_confidence": 0.99,
        },
        {
            "canonical_name": "云知意",
            "aliases": ["云知意"],
            "gender": "female",
            "gender_confidence": 0.99,
        },
    ]
    cases = [
        "霍奉卿对云知意道：“他说过此事。”",
        "霍奉卿没搭理他，端起茶盏，口中向云知意说道：“走吧。”",
        "霍奉卿转过身，朝她问道：“你看见了吗？”",
    ]

    for content in cases:
        _characters, segments = analyze_chapter(content, known)
        dialogue = next(item for item in segments if item["kind"] == "dialogue")
        assert dialogue["speaker"] == "霍奉卿", content
        assert dialogue["gender"] == "male", content
        assert dialogue["speaker_source"] == "speech-subject-before", content


def test_subject_object_speech_uses_the_nearest_explicit_clause_subject():
    known = [
        {
            "canonical_name": "林雪",
            "aliases": ["林雪"],
            "gender": "female",
            "gender_confidence": 0.99,
        },
        {
            "canonical_name": "云知意",
            "aliases": ["云知意"],
            "gender": "female",
            "gender_confidence": 0.99,
        },
    ]
    _characters, segments = analyze_chapter(
        "林雪看见陌生男子，陌生男子对云知意道：“走。”",
        known,
    )
    dialogue = next(item for item in segments if item["kind"] == "dialogue")
    assert dialogue["speaker"] != "林雪"

    known.append({
        "canonical_name": "小梅",
        "aliases": ["小梅"],
        "gender": "female",
        "gender_confidence": 0.99,
    })
    _characters, segments = analyze_chapter(
        "云知意压着烦躁想了想，扬声对小梅道：“让他俩进来。”",
        known,
    )
    dialogue = next(item for item in segments if item["kind"] == "dialogue")
    assert dialogue["speaker"] == "云知意"
    assert dialogue["gender"] == "female"


def test_response_source_frame_uses_responder_not_addressee():
    known = [
        {
            "canonical_name": "云知意",
            "aliases": ["云知意"],
            "gender": "female",
            "gender_confidence": 0.99,
        },
        {
            "canonical_name": "霍奉卿",
            "aliases": ["霍奉卿"],
            "gender": "male",
            "gender_confidence": 0.99,
        },
    ]
    _characters, segments = analyze_chapter(
        "回答她的，是霍奉卿板着脸一记凶冷白眼，以及几不可闻的一声：“嗯。”",
        known,
    )
    dialogue = next(item for item in segments if item["kind"] == "dialogue")
    assert dialogue["speaker"] == "霍奉卿"
    assert dialogue["speaker_source"] == "response-source-before"
    assert dialogue["gender"] == "male"

    _characters, segments = analyze_chapter(
        "回应云知意的，是门外几个人含混的一声：“知道了。”",
        known,
    )
    dialogue = next(item for item in segments if item["kind"] == "dialogue")
    assert dialogue["speaker"] == ""


def test_prepositional_companion_is_not_bound_to_the_following_pronoun():
    known = [{
        "canonical_name": "霍奉卿",
        "aliases": ["霍奉卿"],
        "gender": "male",
        "gender_confidence": 0.99,
    }]
    characters, segments = analyze_chapter(
        "与霍奉卿一照面，她就惊讶得脱口而出：“你昨晚偷牛去了？”",
        known,
    )

    dialogue = next(item for item in segments if item["kind"] == "dialogue")
    assert dialogue["speaker"] == ""
    assert dialogue["gender"] == "female"
    assert characters == []


def test_anonymous_pronoun_frames_keep_gender_without_guessing_identity():
    contaminated = [
        {
            "canonical_name": "云知意",
            "aliases": ["云知意", "知意"],
            "gender": "male",
            "gender_confidence": 0.99,
        },
        {
            "canonical_name": "小梅",
            "aliases": ["小梅"],
            "gender": "female",
            "gender_confidence": 0.99,
        },
    ]
    cases = [
        "她一面摘着花瓣，一面以落寞的笑音聊起天来："
        "“当年扬言要将你欺得驯顺如狗。”",
        "“你知道吗？人若输太多次，就会急眼。”\n她的语气像威胁。",
        "“别再说了。”她的声音冷了下来。",
    ]

    for content in cases:
        _characters, segments = analyze_chapter(content, contaminated)
        dialogue = next(item for item in segments if item["kind"] == "dialogue")
        assert dialogue["speaker"] == "", content
        assert dialogue["speaker_source"] == "anonymous", content
        assert dialogue["gender"] == "female", content
        assert dialogue["gender_confidence"] >= 0.99, content
        assert dialogue["gender_source"] == "explicit-pronoun", content


def test_contaminated_cast_is_corrected_by_bound_narrative_evidence():
    known = [
        {
            "canonical_name": "云知意",
            "aliases": ["云知意", "知意"],
            "gender": "male",
            "gender_confidence": 0.99,
            "voice_key": "lingxian",
        },
        {
            "canonical_name": "霍奉卿",
            "aliases": ["霍奉卿"],
            "gender": "male",
            "gender_confidence": 0.99,
            "voice_key": "qingyan",
        },
        {
            "canonical_name": "小梅",
            "aliases": ["小梅"],
            "gender": "female",
            "gender_confidence": 0.99,
            "voice_key": "shuanger",
        },
    ]
    content = (
        "对云知意来说不需要过脑子，她只有心烦时才会沉默。\n"
        "她以落寞的笑音聊起天来：“当年扬言要将你欺得驯顺如狗。”\n"
        "“你知道吗？人若输太多次，就会急眼。”\n她的语气像威胁。\n"
        "霍奉卿没搭理他，口中对云知意道：“他说过此事。”\n"
        "云知意说道：“走吧。”"
    )

    characters, segments = analyze_chapter(content, known)
    cast = {item["canonical_name"]: item for item in characters}
    assert cast["云知意"]["gender"] == "female"
    assert cast["云知意"]["gender_source"] == "explicit-pronoun"
    assert cast["霍奉卿"]["gender"] == "male"
    anonymous = [
        item for item in segments
        if item["kind"] == "dialogue" and not item["speaker"]
    ]
    assert len(anonymous) == 2
    assert all(item["gender"] == "female" for item in anonymous)
    huo = next(item for item in segments if item["speaker"] == "霍奉卿")
    assert huo["gender"] == "male"


def test_action_fragments_are_not_created_as_characters():
    cases = [
        "就静静拍桌大叫道：“够了。”",
        "拍桌大叫道：“够了。”",
    ]
    for content in cases:
        characters, segments = analyze_chapter(content)
        assert characters == [], content
        assert next(item for item in segments if item["kind"] == "dialogue")[
            "speaker"
        ] == "", content

    characters, segments = analyze_chapter(
        "言知时斜眼示意霍奉卿，表示不敢说。"
    )
    assert characters == []
    assert all(item["kind"] == "narration" for item in segments)

    positive_cases = [
        "言知时斜着眼说道：“走。”",
        "言知时一时忘形，拍桌大笑：“你也有今天。”",
    ]
    for content in positive_cases:
        characters, segments = analyze_chapter(content)
        assert [item["canonical_name"] for item in characters] == ["言知时"], content
        dialogue = next(item for item in segments if item["kind"] == "dialogue")
        assert dialogue["speaker"] == "言知时", content

    characters, segments = analyze_chapter("徐勉好笑地望着她：\u201c躲什么？\u201d")
    assert [item["canonical_name"] for item in characters] == ["徐勉"]
    assert next(item for item in segments if item["kind"] == "dialogue")[
        "speaker"
    ] == "徐勉"


def test_known_actor_action_suffixes_do_not_create_new_people():
    known = [
        {
            "canonical_name": "田岳", "aliases": ["田岳"],
            "gender": "male", "gender_confidence": 0.99,
        },
        {
            "canonical_name": "陈琇", "aliases": ["陈琇"],
            "gender": "female", "gender_confidence": 0.99,
        },
        {
            "canonical_name": "言珝", "aliases": ["言珝"],
            "gender": "female", "gender_confidence": 0.99,
        },
    ]
    content = (
        "田岳苦笑着摇头：\u201c别去。\u201d\n"
        "陈琇举起三根手指：\u201c我发誓。\u201d\n"
        "言珝眯起笑眼：\u201c坐吧。\u201d"
    )

    characters, segments = analyze_chapter(content, known)

    assert [
        item["speaker"] for item in segments if item["kind"] == "dialogue"
    ] == ["田岳", "陈琇", "言珝"]
    assert {item["canonical_name"] for item in characters} == {
        "田岳", "陈琇", "言珝",
    }


def test_chapter_thirteen_dialogue_scene_keeps_real_actors_and_turns():
    """Regression for the production chapter reported by the listener."""
    known = [
        {
            "canonical_name": "云知意",
            "aliases": ["云知意", "知意"],
            "gender": "female",
            "gender_confidence": 1.0,
            "voice_key": "lingxian",
            "role_type": "protagonist",
            "dialogue_count": 608,
            "chapter_count": 81,
        },
        {
            "canonical_name": "唤来小梅",
            "aliases": ["唤来小梅", "小梅"],
            "gender": "female",
            "gender_confidence": 0.99,
            "voice_key": "shuanger",
            "role_type": "supporting",
            "dialogue_count": 80,
            "chapter_count": 30,
        },
        {
            "canonical_name": "霍奉卿",
            "aliases": ["霍奉卿", "霍大哥", "霍大人"],
            "gender": "male",
            "gender_confidence": 1.0,
            "voice_key": "qingyan",
        },
    ]
    content = (
        "云知意边走边道：\u201c其实，不管这次我参不参与查案，都一定会有人找茬。\u201d\n"
        "小梅虽是婢女，到底也会动动脑。\n"
        "她道：\u201c若所有官员都只顾控制民意，那不就没人真心做事了？\u201d\n"
        "云知意举目望天：\u201c因为原州从来不缺只会闷头做事的傻子们。\u201d\n"
        "云知意笔下稍顿，蹙眉嘀咕：\u201c盛敬侑还没死心？\u201d\n"
        "\u201c都坐吧。言知时，你近来突然转性。\u201d云知意随口说完。\n"
        "言知时噎了噎，干笑：\u201c快十六了，是得醒事点。\u201d\n"
        "听她这么一说，言知时噗嗤笑开：\u201c还是长姐文雅。\u201d\n"
        "霍奉卿冷冷扫来一眼，让他倏地住嘴，讪讪缩了缩脖子：\u201c当我没说。\u201d\n"
        "言知时每次进去就迈不动腿，只能看看。\n"
        "云知意抛出的诱饵过于诱人，于是他立刻变脸：\u201c当时我就问他……\u201d\n"
        "霍奉卿扬声打断：\u201c与其利诱他，不如利诱我。\u201d\n"
        "\u201c这种生意你也抢？\u201d言知时瞪他。\n"
        "\u201c没有，\u201d霍奉卿答得干脆，\u201c只需帮我抄一首诗。\u201d\n"
        "\u201c哦，那利诱你更划算，\u201d云知意颔首，端起茶盏道，\u201c成交。\u201d\n"
        "言知时一时忘形，拍桌大笑：\u201c好好好，这笔生意让给你。\u201d"
    )

    characters, segments = analyze_chapter(content, known)
    dialogue = [item for item in segments if item["kind"] == "dialogue"]

    assert [item["speaker"] for item in dialogue] == [
        "云知意", "小梅", "云知意", "云知意", "云知意",
        "言知时", "言知时", "言知时", "言知时", "霍奉卿",
        "言知时", "霍奉卿", "霍奉卿", "云知意", "云知意", "言知时",
    ]
    assert {item["canonical_name"] for item in characters} == {
        "云知意", "小梅", "言知时", "霍奉卿",
    }
    assert not {
        "唤来小梅", "言知时噎", "言知时噗", "蹙眉", "云知",
    } & {item["canonical_name"] for item in characters}
    yan = next(item for item in characters if item["canonical_name"] == "言知时")
    assert yan["gender"] == "male"
    assert yan["_external_gender_lookup"] is True


def test_first_person_alias_resolves_to_the_persisted_protagonist():
    known = [{
        "canonical_name": "云知意",
        "aliases": ["云知意", "知意"],
        "gender": "female",
        "gender_confidence": 1.0,
        "voice_key": "lingxian",
        "role_type": "protagonist",
        "dialogue_count": 608,
        "chapter_count": 81,
    }]

    _characters, segments = analyze_chapter("我道：\u201c不能退。\u201d", known)

    dialogue = next(item for item in segments if item["kind"] == "dialogue")
    assert dialogue["speaker"] == "云知意"
    assert dialogue["gender"] == "female"


def test_analyzer_does_not_assign_the_person_being_addressed_as_speaker():
    characters, segments = analyze_chapter(
        "“绛紫，你每天这么卡点上班，老板知道吗？”"
        "苏绛紫刚踏进办公室，就听到她们的调侃。"
    )
    dialogue = next(item for item in segments if item["kind"] == "dialogue")
    assert dialogue["speaker"] == ""
    assert characters == []


def test_standalone_vocative_uses_previous_pronoun_speech_lead() -> None:
    known = [{
        "canonical_name": "霍奉卿",
        "aliases": ["霍奉卿", "霍大人"],
        "gender": "male",
        "gender_confidence": 0.99,
        "voice_key": "qingyan",
    }]
    content = (
        "每次，她都展臂环着他的脖颈，用迷离的眼神笑觑他，开口就唤——\n"
        "“霍大人。”\n"
        "梦里的霍奉卿照例不应声，就静静看着她。"
    )

    _characters, segments = analyze_chapter(content, known)
    dialogue = next(item for item in segments if item["kind"] == "dialogue")
    assert dialogue["text"] == "霍大人。"
    assert dialogue["speaker"] == ""
    assert dialogue["speaker_source"] == "anonymous"
    assert dialogue["gender"] == "female"
    assert dialogue["gender_confidence"] >= 0.99
    assert dialogue["gender_source"] == "explicit-pronoun"


def test_smart_mode_uses_female_voice_for_previous_pronoun_vocative(
    monkeypatch,
) -> None:
    class VocativeRepository:
        _mysql = None

        def reader_chapter(self, book_id: str, chapter_id: int):
            return {
                "title": "梦中称呼",
                "content": (
                    "每次，她都展臂环着他的脖颈，用迷离的眼神笑觑他，开口就唤——\n"
                    "“霍大人。”\n"
                    "梦里的霍奉卿照例不应声，就静静看着她。"
                ),
                "next_id": None,
            }

    service = AudiobookService(VocativeRepository())
    monkeypatch.setattr(service, "_existing_cast", lambda _catalog_id: [{
        "canonical_name": "霍奉卿",
        "aliases": ["霍奉卿", "霍大人"],
        "gender": "male",
        "gender_confidence": 0.99,
        "voice_key": "qingyan",
    }])
    manifest = service.create(
        owner_key="v" * 64,
        book_id=BOOK_ID,
        chapter_id=1,
        settings=settings(),
    )["current"]
    dialogue = next(
        item for item in manifest["segments"] if item["text"] == "霍大人。"
    )
    assert dialogue["speaker"] == ""
    assert dialogue["gender"] == "female"
    assert dialogue["voice"] in FEMALE_VOICES
    assert dialogue["voice"] not in MALE_VOICES


def test_smart_manifest_uses_segment_emotion_and_gendered_cast_voice():
    class EmotionalRepository:
        _mysql = None

        def reader_chapter(self, book_id: str, chapter_id: int):
            assert book_id == BOOK_ID
            assert chapter_id == 1
            return {
                "title": "惊惧",
                "content": "林雪拽住了我的胳膊“别杀我，我害怕！”",
                "next_id": None,
            }

    manifest = AudiobookService(EmotionalRepository()).create(
        owner_key="8" * 64,
        book_id=BOOK_ID,
        chapter_id=1,
        settings=settings(),
    )["current"]
    dialogue = next(item for item in manifest["segments"] if item["kind"] == "dialogue")
    assert dialogue["speaker"] == "林雪"
    assert dialogue["voice"] in FEMALE_VOICES
    assert dialogue["emotion"] == "fearful"
    assert dialogue["emotion_source"] == "segment-context"


def test_manifest_character_serializes_mysql_decimal_values():
    rendered = AudiobookService._manifest_character({
        "canonical_name": "林雪",
        "character_key": b"\x01" * 32,
        "gender_confidence": Decimal("0.9900"),
        "_internal": True,
    })

    assert rendered["character_key"] == "01" * 32
    assert rendered["gender_confidence"] == 0.99
    assert "_internal" not in rendered
    json.dumps(rendered, ensure_ascii=False)


def test_analyzer_merges_unambiguous_short_aliases_before_casting():
    characters, segments = analyze_chapter(
        "亚丝娜说道：“出发。”\n丝娜轻声说道：“跟上我。”"
    )
    assert [item["canonical_name"] for item in characters] == ["亚丝娜"]
    assert set(characters[0]["aliases"]) == {"亚丝娜", "丝娜"}
    assert {item["speaker"] for item in segments if item["kind"] == "dialogue"} == {"亚丝娜"}


def test_cross_book_action_fragments_never_become_canonical_people():
    cases = {
        "桑迪嘲道：“就凭你？”": "桑迪",
        "靳汝反问道：“为什么？”": "靳汝",
        "靳汝想了想，问道：“可以吗？”": None,
        "肖亚文一边走一边说道：“跟上。”": "肖亚文",
        "芮小丹也说道：“我同意。”": "芮小丹",
        "古蒂小声说：“别惊动他。”": "古蒂",
        "云知意笑道：“这就来。”": "云知意",
    }
    forbidden = {"桑迪嘲", "靳汝反", "靳汝想", "肖亚文一", "芮小丹也"}

    for content, expected in cases.items():
        characters, _segments = analyze_chapter(content)
        names = {item["canonical_name"] for item in characters}
        if expected:
            assert expected in names, content
        assert not (names & forbidden), content

    for fake in ("关键字", "开怀大"):
        characters, _segments = analyze_chapter(f"{fake}说道：“测试。”")
        assert fake not in {item["canonical_name"] for item in characters}


def test_scanner_accepts_explicit_foreign_and_rare_surname_identities() -> None:
    characters, segments = analyze_chapter(
        "阿南德·拜依说：“进来。”\n战骋问：“都准备好了吗？”"
    )
    by_name = {item["canonical_name"]: item for item in characters}
    assert by_name["阿南德·拜依"]["_scanner_identity_verified"] is True
    assert by_name["战骋"]["_scanner_identity_verified"] is True
    assert {item["speaker"] for item in segments if item["kind"] == "dialogue"} == {
        "阿南德·拜依", "战骋",
    }


def test_smart_cast_pool_uses_microsoft_edge_mandarin_and_multilingual_voices():
    assert FEMALE_VOICES == (
        "nuanxi", "lingxian", "shuanger", "yanzhi", "ava", "emma",
    )
    assert MALE_VOICES == (
        "kuangyun", "qingyan", "tongzhen", "mocheng", "andrew", "brian",
    )
    assert {"qinghe", "jinglan", "yunzhou", "junchuan"}.isdisjoint(CAST_VOICES)


def test_remote_savior_chapter_one_keeps_alternating_women_distinct():
    known = [{
        "canonical_name": "芮小丹",
        "aliases": ["芮小丹", "小丹"],
        "gender": "female",
        "gender_confidence": 0.99,
        "voice_key": "shuanger",
        "role_type": "supporting",
        "voice_locked": 1,
    }]
    content = "\n".join((
        "肖亚文说：「你先坐，我去倒杯水。」",
        "芮小丹说：「不用忙，我只待一会儿。」",
        "肖亚文一上车就笑着说：「今天怎么想起我了？」",
        "芮小丹惊诧地看看她，不解地质问：「这话是什么意思？」",
        "肖亚文摆摆手说：「没什么意思，随口问问。」",
        "芮小丹问：「你最近还好吗？」",
        "肖亚文回答：「还是老样子。」",
        "芮小丹说：「那就好。」",
        "肖亚文喊：「等一下。」",
        "芮小丹说：「我在门口等你。」",
    ))

    characters, segments = analyze_chapter(content, known)
    dialogue = [item for item in segments if item["kind"] == "dialogue"]

    assert [item["speaker"] for item in dialogue] == [
        "肖亚文", "芮小丹", "肖亚文", "芮小丹", "肖亚文",
        "芮小丹", "肖亚文", "芮小丹", "肖亚文", "芮小丹",
    ]
    names = {item["canonical_name"] for item in characters}
    assert {"肖亚文", "芮小丹"} <= names
    assert not ({"肖亚文一", "肖亚文摆"} & names)


def test_cross_book_audit_cancels_with_matching_device_identity():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts" / "audit_audiobook_cast_v12.py"
    ).read_text(encoding="utf-8")
    assert '"X-Audiobook-Client": client_id' in source
    assert "cancelled.raise_for_status()" in source
    assert '"dialogue_narrator_collisions"' in source


def test_scanner_identity_gate_keeps_xiaomei_without_action_fragments():
    content = (
        "“大小姐今日不必再去见那位……”小梅尴尬笑笑，"
        "“就是要卖赌档的那位。”"
    )
    characters, _segments = analyze_chapter(content)
    indexed = {item["canonical_name"]: item for item in characters}
    assert indexed["小梅"]["_identity_confidence"] >= 0.92
    assert indexed["小梅"]["_scanner_identity_verified"] is True
    assert not ({"小梅尴尬", "小梅笑"} & set(indexed))


def test_verified_actor_keeps_same_gender_locked_voice_across_engine_revision():
    historical = {
        "canonical_name": "云知意",
        "gender": "female",
        "voice_key": "lingxian",
        "voice_locked": 1,
    }
    assert reusable_historical_voice(
        historical,
        canonical_name="云知意",
        gender="female",
        verified=True,
    ) == "lingxian"
    assert reusable_historical_voice(
        historical,
        canonical_name="云知意",
        gender="male",
        verified=True,
    ) == ""
    assert reusable_historical_voice(
        historical,
        canonical_name="云知意",
        gender="female",
        verified=False,
    ) == ""


def test_full_book_ai_gender_is_authoritative_over_noisy_chapter_evidence():
    persisted = {
        "canonical_name": "云知意",
        "gender": "male",
        "gender_confidence": 1.0,
        "voice_key": "yunzhou",
        "voice_locked": 0,
        "ai_review_gender": "female",
        "ai_review_confidence": 0.99,
    }
    assert apply_authoritative_ai_gender(304471, persisted) is True
    assert persisted["gender"] == "female"
    assert persisted["gender_confidence"] == 0.99
    assert persisted["voice_key"] in FEMALE_VOICES
    assert persisted["voice_locked"] == 1
    assert persisted["_ai_gender_verified"] is True


def test_playback_identity_gate_rejects_cameos_and_known_action_tails():
    rows = [
        {
            "canonical_name": "江叙",
            "role_type": "protagonist",
            "gender": "male",
            "voice_key": "mocheng",
            "voice_locked": 1,
        },
        {
            "canonical_name": "江叙没",
            "role_type": "supporting",
            "gender": "male",
            "voice_key": "qingyan",
            "voice_locked": 1,
        },
        {
            "canonical_name": "勾唇",
            "role_type": "supporting",
            "gender": "female",
            "voice_key": "lingxian",
            "voice_locked": 1,
        },
        {
            "canonical_name": "临时客人",
            "role_type": "cameo",
            "gender": "male",
            "voice_key": "kuangyun",
            "voice_locked": 1,
        },
        {
            "canonical_name": "解释",
            "role_type": "supporting",
            "gender": "male",
            "voice_key": "yunzhou",
            "voice_locked": 1,
        },
        {
            "canonical_name": "男孩",
            "role_type": "supporting",
            "gender": "male",
            "voice_key": "kuangyun",
            "voice_locked": 1,
        },
    ]
    assert [item["canonical_name"] for item in trusted_persisted_cast(rows)] == [
        "江叙"
    ]


def test_enhanced_emotions_require_action_or_compound_context_evidence():
    from app.audiobook import _infer_emotion

    cases = [
        ("你怎么会在这里？", "她震惊地瞪大了眼。", "surprised"),
        ("别怕，有我在。", "她轻拍我的肩膀，柔声安慰。", "comforting"),
        ("这一局我不会输。", "他胸有成竹，语气笃定。", "confident"),
        ("我、我才没有等你。", "她耳根发红，神情羞怯。", "shy"),
        ("离我远点。", "她满脸厌恶，皱眉避开。", "disgusted"),
        ("墙后有人。", "他贴近我耳边，压低了声音。", "whispering"),
    ]
    for speech, context, expected in cases:
        emotion, confidence, source = _infer_emotion(speech, context)
        assert emotion == expected
        assert confidence >= 0.8
        assert source == "segment-context"

    # High-frequency connective words alone are not an emotion signal.
    assert _infer_emotion("他竟然按时到了。", "大家继续吃饭。")[0] == "neutral"
    assert _infer_emotion("没事就早点回去。", "她看了一眼时钟。")[0] == "neutral"


def test_analyzer_does_not_cast_ordinary_prose_before_quotes():
    characters, _segments = analyze_chapter(
        "一般来说：“这种写法不代表一般来是角色。”\n"
        "作品的名字说道：“这里也不是人物。”\n"
        "林雪轻声说道：“只有明确的人物归属才绑定声音。”"
    )
    assert [item["canonical_name"] for item in characters] == ["林雪"]


def test_analyzer_attributes_separate_dialogue_from_following_or_previous_context():
    characters, segments = analyze_chapter(
        "“弥勒学姐，我们走吧。”\n"
        "凯一边喘着粗气，一边对我说道。\n"
        "跑在我身旁的凯开口了。\n"
        "“大家都已经先去避难了。”\n"
        "“妈妈和爸爸呢？”\n"
        "孩子一边跑，一边向我问道。"
    )
    assert {item["canonical_name"] for item in characters} == {"凯", "孩子"}
    dialogue = [item for item in segments if item["kind"] == "dialogue"]
    assert [item["speaker"] for item in dialogue] == ["凯", "凯", "孩子"]


def test_session_returns_current_first_and_lazily_prefetches_next():
    service = AudiobookService(FakeRepository())
    created = service.create(
        owner_key="1" * 64, book_id=BOOK_ID, chapter_id=1, settings=settings()
    )
    assert created["engine_version"] == ENGINE_VERSION
    assert created["next"] is None
    assert created["current"]["chapter_id"] == 1
    assert created["current"]["stream_endpoint"].endswith("/stream.mp3")
    assert all(item["audio_endpoint"].startswith("/api/v1/audiobook/sessions/") for item in created["current"]["segments"])
    assert all("?text=" not in item["audio_endpoint"] for item in created["current"]["segments"])

    following = service.prefetch_next(created["session_id"], "1" * 64)
    assert following and following["chapter_id"] == 2


def test_session_resume_normalizes_oversized_audio_offset(monkeypatch):
    service = AudiobookService(FakeRepository())
    manifest = {
        "chapter_id": 1,
        "content_hash": "1" * 64,
        "settings_hash": "2" * 64,
        "engine_version": ENGINE_VERSION,
        "manifest_hash": "3" * 64,
        "title": "开始",
        "next_chapter_id": None,
        "characters": [],
        "complete": True,
        "cache_key": "audiobook:" + "3" * 64,
        "mode": "normal",
        "selected_voice": "nuanxi",
        "requested_narrator": "mocheng",
        "effective_narrator": "mocheng",
        "segments": [
            {"index": 0, "paragraph_index": 0, "text": "第一段", "voice": "nuanxi", "sha256": "a" * 64},
            {"index": 1, "paragraph_index": 1, "text": "第二段", "voice": "nuanxi", "sha256": "b" * 64},
            {"index": 2, "paragraph_index": 2, "text": "第三段", "voice": "nuanxi", "sha256": "c" * 64},
        ],
    }
    monkeypatch.setattr(service, "_manifest", lambda *_args, **_kwargs: manifest)
    monkeypatch.setattr(
        service,
        "progress",
        lambda *_args, **_kwargs: {
            "book_id": BOOK_ID,
            "chapter_id": 1,
            "paragraph_index": 0,
            "item_index": 0,
            "audio_offset_ms": 12_500,
            "manifest_hash": manifest["manifest_hash"],
            "settings_hash": manifest["settings_hash"],
            "cast_revision": 0,
        },
    )
    monkeypatch.setattr(
        service,
        "_known_segment_durations_ms",
        lambda _segments: {0: 5_000, 1: 4_000, 2: 6_000},
    )

    created = service.create(
        owner_key="1" * 64,
        book_id=BOOK_ID,
        chapter_id=1,
        settings={**settings(), "mode": "normal"},
    )

    assert created["resume"]["item_index"] == 2
    assert created["resume"]["paragraph_index"] == 2
    assert created["resume"]["audio_offset_ms"] == 3_500


def test_session_explicit_start_returns_server_resolved_segment(monkeypatch):
    service = AudiobookService(FakeRepository())
    manifest = {
        "chapter_id": 1,
        "content_hash": "1" * 64,
        "settings_hash": "2" * 64,
        "engine_version": ENGINE_VERSION,
        "manifest_hash": "3" * 64,
        "title": "开始",
        "next_chapter_id": None,
        "characters": [],
        "complete": True,
        "cache_key": "audiobook:" + "3" * 64,
        "mode": "normal",
        "selected_voice": "nuanxi",
        "requested_narrator": "mocheng",
        "effective_narrator": "mocheng",
        "segments": [
            {"index": 0, "paragraph_index": 0, "text": "第一段", "voice": "nuanxi", "sha256": "a" * 64},
            {"index": 1, "paragraph_index": 3, "text": "第四段上", "voice": "nuanxi", "sha256": "b" * 64},
            {"index": 2, "paragraph_index": 3, "text": "第四段下", "voice": "nuanxi", "sha256": "c" * 64},
        ],
    }
    monkeypatch.setattr(service, "_manifest", lambda *_args, **_kwargs: manifest)
    monkeypatch.setattr(
        service,
        "progress",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("explicit start must not load resume progress")),
    )

    created = service.create(
        owner_key="1" * 64,
        book_id=BOOK_ID,
        chapter_id=1,
        settings={**settings(), "mode": "normal"},
        resume_existing=False,
        start_paragraph_index=2,
    )

    assert created["resume"] is None
    assert created["start"] == {
        "requested_paragraph_index": 2,
        "paragraph_index": 3,
        "item_index": 1,
        "audio_offset_ms": 0,
    }


def test_save_progress_normalizes_oversized_audio_offset_before_persist(
    monkeypatch,
):
    manifest = {
        "manifest_hash": "3" * 64,
        "segments": [
            {"index": 0, "paragraph_index": 0, "text": "第一段", "sha256": "a" * 64},
            {"index": 1, "paragraph_index": 1, "text": "第二段", "sha256": "b" * 64},
            {"index": 2, "paragraph_index": 2, "text": "第三段", "sha256": "c" * 64},
        ],
    }
    calls: list[tuple[str, tuple]] = []
    rows = [
        {"catalog_id": 7, "book_public_id": BOOK_ID},
        {
            "manifest_hash": manifest["manifest_hash"],
            "settings_hash": "4" * 64,
            "cast_revision": 0,
            "manifest_json": json.dumps(manifest, ensure_ascii=False),
        },
    ]

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params=None):
            calls.append((" ".join(query.split()), tuple(params or ())))

        def fetchone(self):
            return rows.pop(0)

    class Context:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self.value

        def __exit__(self, *_args):
            return False

    cursor = Cursor()
    connection = type("Connection", (), {"cursor": lambda self: Context(cursor)})()
    pool = type("Pool", (), {"transaction": lambda self: Context(connection)})()
    repository = type("Repo", (), {"_mysql": type("MySQL", (), {"pool": pool})()})()
    service = object.__new__(AudiobookService)
    service.repository = repository
    monkeypatch.setattr(
        service,
        "_known_segment_durations_ms",
        lambda _segments: {0: 5_000, 1: 4_000, 2: 6_000},
    )

    service.save_progress(
        "a" * 32,
        "b" * 64,
        device_key="c" * 64,
        chapter_id=1,
        paragraph_index=0,
        item_index=0,
        audio_offset_ms=12_500,
    )

    insert_params = calls[-1][1]
    assert insert_params[5:8] == (2, 2, 3_500)


def test_segment_is_owner_scoped_and_cancelled_session_cannot_read():
    service = AudiobookService(FakeRepository())
    created = service.create(
        owner_key="2" * 64, book_id=BOOK_ID, chapter_id=1, settings=settings()
    )
    manifest = created["current"]
    full_manifest = service.manifest(
        created["session_id"], manifest["manifest_hash"], "2" * 64
    )
    assert full_manifest["chapter_id"] == 1
    first = service.segment(
        created["session_id"], manifest["manifest_hash"], 0, "2" * 64
    )
    assert first["rate"] == 1.2
    try:
        service.segment(created["session_id"], manifest["manifest_hash"], 0, "3" * 64)
    except KeyError:
        pass
    else:
        raise AssertionError("another owner read the segment")
    assert service.cancel(created["session_id"], "2" * 64)
    try:
        service.segment(created["session_id"], manifest["manifest_hash"], 0, "2" * 64)
    except KeyError:
        pass
    else:
        raise AssertionError("cancelled session remained readable")


def test_unknown_character_safely_avoids_the_narrator_channel():
    service = AudiobookService(FakeRepository())
    manifest = service.create(
        owner_key="4" * 64, book_id=BOOK_ID, chapter_id=2, settings=settings()
    )["current"]
    character = next(item for item in manifest["characters"] if item["canonical_name"] == "阿尔法")
    assert character["gender"] == "unknown"
    assert character["unknown_fallback"] is True
    assert character["voice_key"] in CAST_VOICES
    assert character["voice_key"] != "mocheng"
    repeated = AudiobookService(FakeRepository()).create(
        owner_key="7" * 64, book_id=BOOK_ID, chapter_id=2, settings=settings()
    )["current"]
    repeated_character = next(
        item for item in repeated["characters"] if item["canonical_name"] == "阿尔法"
    )
    assert repeated_character["voice_key"] == character["voice_key"]


def test_smart_mode_never_uses_narrator_for_unknown_anonymous_dialogue():
    service = AudiobookService(FakeRepository())
    manifest = service.create(
        owner_key="5" * 64, book_id=BOOK_ID, chapter_id=3, settings=settings()
    )["current"]
    narration = [item for item in manifest["segments"] if item["kind"] == "narration"]
    dialogue = [item for item in manifest["segments"] if item["kind"] == "dialogue"]
    assert narration and {item["voice"] for item in narration} == {"mocheng"}
    assert dialogue
    assert all(item["voice"] != "mocheng" for item in dialogue)
    assert all(item["gender"] == "unknown" for item in dialogue)


def test_smart_mode_uses_gender_pool_for_anonymous_explicit_pronouns():
    class PronounRepository:
        _mysql = None

        def reader_chapter(self, book_id: str, chapter_id: int):
            assert book_id == BOOK_ID
            assert chapter_id == 1
            return {
                "title": "匿名男女",
                "content": (
                    "她低声聊起天来：“先走吧。”\n"
                    "他忽然开口说道：“我留下。”"
                ),
                "next_id": None,
            }

    manifest = AudiobookService(PronounRepository()).create(
        owner_key="g" * 64,
        book_id=BOOK_ID,
        chapter_id=1,
        settings=settings(),
    )["current"]
    dialogue = [item for item in manifest["segments"] if item["kind"] == "dialogue"]
    assert [(item["speaker"], item["gender"]) for item in dialogue] == [
        ("", "female"),
        ("", "male"),
    ]
    assert dialogue[0]["voice"] in FEMALE_VOICES
    assert dialogue[1]["voice"] in MALE_VOICES


def test_persisted_gender_reconciliation_and_voice_pool_consistency():
    contaminated = {
        "canonical_name": "云知意",
        "gender": "male",
        "gender_confidence": 0.99,
        "voice_key": "lingxian",
    }
    explicit_female = {
        "canonical_name": "云知意",
        "gender": "female",
        "gender_confidence": 0.99,
        "gender_source": "explicit-pronoun",
    }
    gender, confidence, changed = reconcile_character_gender(
        contaminated, explicit_female
    )
    assert (gender, confidence, changed) == ("female", 0.99, True)
    corrected = {**contaminated, **explicit_female, "gender": gender}
    assert rendition_voice(304471, corrected, "lingxian") in FEMALE_VOICES
    assert rendition_voice(304471, corrected, "lingxian") != "qingyan"

    weak_conflict = {
        **explicit_female,
        "gender_confidence": 0.72,
        "gender_source": "name_heuristic",
    }
    assert reconcile_character_gender(contaminated, weak_conflict) == (
        "male", 0.99, False
    )
    unknown = {**contaminated, "gender": "unknown", "gender_confidence": 0.0}
    upgraded = reconcile_character_gender(unknown, explicit_female)
    assert upgraded == ("female", 0.99, True)

    stable_female = {
        "canonical_name": "苏晚",
        "gender": "female",
        "gender_confidence": 0.99,
        "voice_key": "nuanxi",
    }
    contradictory_male = {
        "canonical_name": "苏晚",
        "gender": "male",
        "gender_confidence": 0.99,
        "gender_source": "explicit-pronoun",
    }
    assert reconcile_character_gender(stable_female, contradictory_male) == (
        "female", 0.99, False
    )
    assert reconcile_character_gender(stable_female, contradictory_male) == (
        "female", 0.99, False
    )


def test_running_scan_counts_can_promote_unclassified_fingerprint_to_trusted():
    rows = [
        {
            "canonical_name": "林雪",
            "aliases": ["林雪"],
            "role_type": "unclassified",
            "chapter_count": 3,
            "dialogue_count": 7,
            "gender": "female",
            "gender_confidence": 0.99,
            "voice_key": "nuanxi",
            "voice_locked": 1,
        }
    ]

    trusted = trusted_persisted_cast(rows)

    assert len(trusted) == 1
    assert trusted[0]["canonical_name"] == "林雪"
    assert trusted[0]["role_type"] == "supporting"

    reviewed_cameo = [
        {
            "canonical_name": "淡淡",
            "aliases": ["淡淡"],
            "role_type": "unclassified",
            "chapter_count": 253,
            "dialogue_count": 422,
            "gender": "female",
            "gender_confidence": 1.0,
            "voice_key": "qinghe",
            "voice_locked": 1,
            "ai_review_role_type": "cameo",
            "ai_review_confidence": 0.99,
        }
    ]
    assert trusted_persisted_cast(reviewed_cameo) == []


def test_playback_cast_current_chapter_gender_overrides_stale_snapshot_voice():
    service = AudiobookService(FakeRepository())
    current = {
        "canonical_name": "张强",
        "aliases": ["张强"],
        "gender": "male",
        "gender_confidence": 0.97,
        "gender_source": "role_suffix",
        "age_group": "unknown",
        "age_confidence": 0.0,
        "tone": "neutral",
        "tone_confidence": 0.4,
    }
    stale_snapshot = {
        "canonical_name": "张强",
        "aliases": ["张强"],
        "gender": "female",
        "gender_confidence": 0.99,
        "gender_source": "published_snapshot",
        "voice_key": "nuanxi",
        "voice_locked": 1,
        "role_type": "supporting",
        "chapter_count": 9,
        "dialogue_count": 30,
    }

    cast = service._playback_cast(42, [current], [stale_snapshot])

    assert cast["张强"]["gender"] == "male"
    assert cast["张强"]["gender_source"] == "role_suffix"
    assert cast["张强"]["voice_key"] in MALE_VOICES
    assert cast["张强"]["voice_locked"] == 0


def test_database_cast_correction_repairs_legacy_gender_voice_mismatch():
    existing = {
        "character_key": b"c" * 32,
        "canonical_name": "云知意",
        "aliases": json.dumps(["云知意", "知意"], ensure_ascii=False),
        "gender": "male",
        "age_group": "unknown",
        "tone": "neutral",
        "gender_confidence": 0.99,
        "age_confidence": 0.0,
        "tone_confidence": 0.4,
        "voice_key": "lingxian",
        "voice_locked": 1,
        "role_type": "supporting",
    }

    class CastCursor:
        def __init__(self):
            self.rows = [existing]
            self.updated = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params=None):
            if query.lstrip().startswith("UPDATE audiobook_character_voices"):
                self.updated = params

        def fetchone(self):
            return {"revision": 0}

        def fetchall(self):
            return list(self.rows)

    class Context:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self.value

        def __exit__(self, *_args):
            return False

    cursor = CastCursor()
    connection = type("CastConnection", (), {"cursor": lambda self: Context(cursor)})()
    pool = type("CastPool", (), {"transaction": lambda self: Context(connection)})()
    repository = type("CastRepo", (), {"_mysql": type("MySQL", (), {"pool": pool})()})()
    service = object.__new__(AudiobookService)
    service.repository = repository
    candidate = {
        "canonical_name": "云知意",
        "aliases": ["云知意", "知意"],
        "gender": "female",
        "gender_confidence": 0.99,
        "gender_source": "explicit-pronoun",
        "age_group": "unknown",
        "age_confidence": 0.0,
        "tone": "neutral",
        "tone_confidence": 0.4,
        "_external_gender_lookup": True,
    }

    cast = service._cast(304471, [candidate])

    assert cursor.updated is not None
    assert cursor.updated[2:4] == ("female", 0.99)
    assert cursor.updated[4] in FEMALE_VOICES
    assert cursor.updated[4] == "lingxian"
    assert cast["云知意"]["gender"] == "female"
    assert cast["云知意"]["voice_key"] in FEMALE_VOICES


def test_new_verified_character_avoids_requested_narrator_voice():
    class EmptyCastCursor:
        rowcount = 1

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _query, _params=None):
            return None

        def fetchone(self):
            return {"revision": 0}

        def fetchall(self):
            return []

    class Context:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self.value

        def __exit__(self, *_args):
            return False

    cursor = EmptyCastCursor()
    connection = type(
        "CastConnection", (), {"cursor": lambda self: Context(cursor)}
    )()
    pool = type(
        "CastPool", (), {"transaction": lambda self: Context(connection)}
    )()
    repository = type(
        "CastRepo", (), {"_mysql": type("MySQL", (), {"pool": pool})()}
    )()
    service = object.__new__(AudiobookService)
    service.repository = repository
    candidate = {
        "canonical_name": "云知意",
        "aliases": ["云知意"],
        "gender": "female",
        "gender_confidence": 0.99,
        "gender_source": "explicit-pronoun",
        "age_group": "unknown",
        "age_confidence": 0.0,
        "tone": "neutral",
        "tone_confidence": 0.4,
        "_external_gender_lookup": True,
    }

    cast = service._cast(
        304471, [candidate], "lingxian", allow_new_identities=True
    )

    assert cast["云知意"]["voice_key"] in FEMALE_VOICES
    assert cast["云知意"]["voice_key"] != "lingxian"
    assert cast["云知意"]["voice_locked"] == 1


def test_manifest_segment_gender_is_synchronized_with_effective_cast(monkeypatch):
    class OneChapterRepository:
        _mysql = None

        def reader_chapter(self, book_id: str, chapter_id: int):
            return {
                "title": "纠错",
                "content": "云知意说道：“走吧。”",
                "next_id": None,
            }

    service = AudiobookService(OneChapterRepository())
    contaminated = [{
        "canonical_name": "云知意",
        "aliases": ["云知意"],
        "gender": "male",
        "gender_confidence": 0.99,
        "voice_key": "lingxian",
    }]
    monkeypatch.setattr(service, "_existing_cast", lambda _catalog_id: contaminated)
    monkeypatch.setattr(service, "_catalog_id", lambda _book_id: 304471)

    def corrected_cast(_catalog_id, characters, _published_cast):
        character = characters[0]
        return {
            "云知意": {
                **character,
                "gender": "female",
                "gender_confidence": 0.99,
                "gender_source": "explicit-pronoun",
                "voice_key": "lingxian",
            }
        }

    monkeypatch.setattr(service, "_playback_cast", corrected_cast)
    manifest = service.create(
        owner_key="s" * 64,
        book_id=BOOK_ID,
        chapter_id=1,
        settings=settings(),
    )["current"]
    dialogue = next(item for item in manifest["segments"] if item["kind"] == "dialogue")
    assert dialogue["speaker"] == "云知意"
    assert dialogue["gender"] == "female"
    assert dialogue["gender_source"] == "explicit-pronoun"
    assert dialogue["voice"] in FEMALE_VOICES


def test_normal_mode_uses_the_selected_single_voice_for_every_segment():
    service = AudiobookService(FakeRepository())
    normal = {**settings(), "mode": "normal", "voice": "nuanxi"}
    manifest = service.create(
        owner_key="6" * 64, book_id=BOOK_ID, chapter_id=1, settings=normal
    )["current"]
    assert {item["voice"] for item in manifest["segments"]} == {"nuanxi"}


def test_character_voice_stays_fixed_when_selected_narrator_collides():
    character = {"canonical_name": "凯", "gender": "male", "voice_key": "mocheng"}
    first = rendition_voice(306188, character, "mocheng")
    assert first == "mocheng"
    assert first == rendition_voice(306188, character, "mocheng")


def test_smart_scene_keeps_fixed_character_voices_and_requested_narrator(
    monkeypatch,
):
    class SceneRepository:
        _mysql = None

        @staticmethod
        def reader_chapter(_book_id: str, _chapter_id: int):
            return {
                "title": "对角戏",
                "content": (
                    "窗外风声渐紧。\n"
                    "云知意道：\u201c我来。\u201d\n"
                    "小梅道：\u201c我陪您。\u201d\n"
                    "言知时道：\u201c算我一个。\u201d\n"
                    "霍奉卿道：\u201c走吧。\u201d"
                ),
                "next_id": None,
            }

    fixed = {
        "云知意": {
            "canonical_name": "云知意", "aliases": ["云知意", "我"],
            "gender": "female", "gender_confidence": 1.0,
            "gender_source": "persisted", "voice_key": "lingxian",
        },
        "小梅": {
            "canonical_name": "小梅", "aliases": ["小梅"],
            "gender": "female", "gender_confidence": 0.99,
            "gender_source": "persisted", "voice_key": "shuanger",
        },
        "言知时": {
            "canonical_name": "言知时", "aliases": ["言知时"],
            "gender": "male", "gender_confidence": 0.8434,
            "gender_source": "external", "voice_key": "tongzhen",
        },
        "霍奉卿": {
            "canonical_name": "霍奉卿", "aliases": ["霍奉卿"],
            "gender": "male", "gender_confidence": 1.0,
            "gender_source": "persisted", "voice_key": "qingyan",
        },
    }
    service = AudiobookService(SceneRepository())
    monkeypatch.setattr(service, "_existing_cast", lambda _catalog_id: list(fixed.values()))
    monkeypatch.setattr(
        service,
        "_cast",
        lambda _catalog_id, _characters, _reserved_voice="": fixed,
    )

    manifest = service.create(
        owner_key="d" * 64,
        book_id=BOOK_ID,
        chapter_id=1,
        settings={**settings(), "narrator": "lingxian"},
    )["current"]

    voices = {
        item["speaker"]: item["voice"]
        for item in manifest["segments"]
        if item["kind"] == "dialogue"
    }
    assert voices["云知意"] in FEMALE_VOICES
    assert voices["云知意"] != "lingxian"
    assert voices["小梅"] == "shuanger"
    assert voices["言知时"] == "tongzhen"
    assert voices["霍奉卿"] == "qingyan"
    assert len(set(voices.values())) == 4
    assert manifest["requested_narrator"] == "lingxian"
    assert manifest["effective_narrator"] == "lingxian"
    narration = next(
        item for item in manifest["segments"] if item["kind"] == "narration"
    )
    assert narration["voice"] == "lingxian"


def test_shared_audio_work_deduplicates_across_worker_instances(tmp_path: Path):
    first = SharedAudioWork(cache_root=tmp_path)
    second = SharedAudioWork(cache_root=tmp_path)
    calls = 0

    async def builder() -> bytes:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return b"shared-audio"

    async def exercise() -> tuple[bytes, bytes, bytes]:
        key = "a" * 64
        left, right = await asyncio.gather(
            first.get(key, builder), second.get(key, builder)
        )
        cached = await SharedAudioWork(cache_root=tmp_path).get(key, builder)
        return left, right, cached

    assert asyncio.run(exercise()) == (
        b"shared-audio",
        b"shared-audio",
        b"shared-audio",
    )
    assert calls == 1


def test_shared_audio_work_releases_job_when_nas_lock_fails(
    tmp_path: Path, monkeypatch
):
    work = SharedAudioWork(cache_root=tmp_path)
    events: list[tuple[str, bool]] = []
    monkeypatch.setattr(work, "_job_start", lambda _key: None)
    monkeypatch.setattr(
        work,
        "_job_release",
        lambda _key, *, failed=False: events.append(("release", failed)),
    )
    monkeypatch.setattr(
        work,
        "_acquire_file_lock",
        lambda _path: (_ for _ in ()).throw(OSError("NAS unavailable")),
    )

    result = asyncio.run(work.get("f" * 64, lambda: asyncio.sleep(0, result=b"memory")))

    assert result == b"memory"
    assert events == [("release", True)]
    assert not list(tmp_path.rglob("*.mp3"))


def test_shared_audio_work_cancels_builder_after_last_waiter_leaves(tmp_path: Path):
    work = SharedAudioWork(cache_root=tmp_path)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def builder() -> bytes:
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()
        return b"never"

    async def exercise():
        task = asyncio.create_task(work.get("e" * 64, builder))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cancelled.wait(), timeout=1)

    asyncio.run(exercise())


def test_distributed_tts_lease_is_renewed_until_slot_exits(monkeypatch):
    limiter = main.TtsConcurrencyLimiter()
    limiter.renew_interval_seconds = 0.01
    renewed: list[str] = []
    released: list[str] = []
    monkeypatch.setattr(limiter, "_distributed_acquire", lambda *_args: "lease")
    monkeypatch.setattr(
        limiter,
        "_distributed_renew",
        lambda token: renewed.append(token) or True,
    )
    monkeypatch.setattr(
        limiter, "_distributed_release", lambda token: released.append(token)
    )

    async def exercise():
        async with limiter.slot("a" * 64, "127.0.0.1"):
            await asyncio.sleep(0.035)

    asyncio.run(exercise())
    assert len(renewed) >= 2
    assert released == ["lease"]


def test_distributed_tts_lease_releases_when_stream_task_is_cancelled(monkeypatch):
    limiter = main.TtsConcurrencyLimiter()
    released: list[str] = []
    monkeypatch.setattr(limiter, "_distributed_acquire", lambda *_args: "lease")
    monkeypatch.setattr(limiter, "_distributed_release", released.append)

    async def exercise():
        entered = asyncio.Event()

        async def stream():
            async with limiter.slot("a" * 64, "127.0.0.1"):
                entered.set()
                await asyncio.sleep(60)

        task = asyncio.create_task(stream())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert released == ["lease"]


def test_distributed_tts_lease_loss_cancels_active_generation(monkeypatch):
    limiter = main.TtsConcurrencyLimiter()
    limiter.renew_interval_seconds = 0.01
    released: list[str] = []
    stopped = asyncio.Event()
    monkeypatch.setattr(limiter, "_distributed_acquire", lambda *_args: "lease")
    monkeypatch.setattr(limiter, "_distributed_renew", lambda _token: False)
    monkeypatch.setattr(limiter, "_distributed_release", released.append)

    async def exercise():
        with pytest.raises(RuntimeError, match="lease was lost"):
            async with limiter.slot("a" * 64, "127.0.0.1"):
                try:
                    await asyncio.sleep(60)
                finally:
                    stopped.set()
        assert stopped.is_set()

    asyncio.run(exercise())
    assert released == ["lease"]


def test_chapter_stream_waits_for_capacity_instead_of_raising_after_headers(
    monkeypatch, tmp_path
):
    session_id = "a" * 32
    manifest_hash = "b" * 64

    class FakeService:
        def manifest(self, *_args):
            return {
                "manifest_hash": manifest_hash,
                "segments": [{"index": 0, "text": "继续播放"}],
            }

    class FlakyLimiter:
        attempts = 0

        @asynccontextmanager
        async def slot(self, *_args):
            self.attempts += 1
            if self.attempts < 3:
                raise main.HTTPException(429, "听书生成并发过高，请稍后再试")
            yield

    limiter = FlakyLimiter()
    monkeypatch.setattr(main, "settings", replace(main.settings, state_root=tmp_path))
    monkeypatch.setattr(main, "audiobook_service", lambda: FakeService())
    monkeypatch.setattr(main, "tts_concurrency_limiter", lambda: limiter)
    monkeypatch.setattr(main, "_audiobook_quota", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        main, "_audiobook_segment_bytes", lambda _segment: asyncio.sleep(0, result=b"mp3")
    )

    response = TestClient(main.app).get(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/stream.mp3",
        headers={"X-Audiobook-Client": "client_1234567890123456"},
    )

    assert response.status_code == 200
    assert response.content == b"mp3"
    assert limiter.attempts == 3


def test_segment_playback_limits_requests_without_charging_cached_text(
    monkeypatch,
):
    session_id = "a" * 32
    manifest_hash = "b" * 64
    charged: list[str] = []

    class FakeService:
        def segment(self, *_args):
            return {
                "index": 0,
                "text": "这是已有缓存的听书片段",
                "voice": "mocheng",
                "emotion": "neutral",
                "rate": 1.0,
                "sha256": "c" * 64,
            }

    class FreeLimiter:
        @asynccontextmanager
        async def slot(self, *_args):
            yield

    def fake_quota(_owner, _ip, scope, **_kwargs):
        charged.append(scope)
        if scope == "characters":
            raise AssertionError("playback must not charge source text")

    monkeypatch.setattr(main, "audiobook_service", lambda: FakeService())
    monkeypatch.setattr(main, "tts_concurrency_limiter", lambda: FreeLimiter())
    monkeypatch.setattr(main, "_audiobook_quota", fake_quota)
    monkeypatch.setattr(
        main,
        "_audiobook_segment_bytes",
        lambda _segment: asyncio.sleep(0, result=b"mp3"),
    )

    response = TestClient(main.app).post(
        f"/api/v1/audiobook/sessions/{session_id}/segments/{manifest_hash}/0",
        headers={"X-Audiobook-Client": "client_1234567890123456"},
    )

    assert response.status_code == 200
    assert response.content == b"mp3"
    assert charged == ["segment-requests"]


def test_inflight_segment_is_cancelled_when_session_disappears(monkeypatch):
    cancelled = asyncio.Event()

    async def slow_segment(_segment):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    class Request:
        async def is_disconnected(self):
            return False

    class Service:
        def manifest(self, *_args):
            raise KeyError("cancelled")

    monkeypatch.setattr(main, "_audiobook_segment_bytes", slow_segment)
    monkeypatch.setattr(main, "audiobook_service", lambda: Service())

    result = asyncio.run(
        main._audiobook_segment_while_active(
            {"sha256": "1" * 64}, Request(), "s" * 32, "m" * 64, "o" * 64
        )
    )
    assert result is None
    assert cancelled.is_set()


def test_session_http_contract_uses_ids_and_owner_scoped_cancel(
    monkeypatch, tmp_path
):
    calls = []
    session_id = "a" * 32

    class FakeService:
        def create(self, **kwargs):
            calls.append(("create", kwargs))
            return {
                "session_id": session_id,
                "current": {"segments": []},
                "next": None,
            }

        def cancel(self, session_id, owner_key):
            calls.append(("cancel", session_id, owner_key))
            return True

    monkeypatch.setattr(
        main,
        "settings",
        replace(main.settings, audiobook_audio_root=tmp_path),
    )
    monkeypatch.setattr(main, "audiobook_service", lambda: FakeService())
    monkeypatch.setattr(main, "_audiobook_quota", lambda *args, **kwargs: None)
    client = TestClient(main.app)
    payload = {"book_id": BOOK_ID, "chapter_id": 1, "client_id": "client_1234567890123456",
               "mode": "smart", "narrator": "mocheng", "voice": "nuanxi",
               "emotion": "auto", "rate": 1.0}
    created = client.post("/api/v1/audiobook/sessions", json=payload)
    assert created.status_code == 201
    assert "oohstory_audiobook_client=" in created.headers["set-cookie"]
    assert "HttpOnly" in created.headers["set-cookie"]
    assert calls[0][1]["book_id"] == BOOK_ID
    assert calls[0][1]["resume_existing"] is True
    assert "content" not in calls[0][1]

    client.cookies.set(main.settings.session_cookie, "expired-account-session")
    recreated = client.post("/api/v1/audiobook/sessions", json=payload)
    assert recreated.status_code == 201
    assert "oohstory_audiobook_client=" in recreated.headers["set-cookie"]

    receipt_dir = tmp_path / "audiobook-stream-receipts" / session_id
    receipt_dir.mkdir(parents=True)
    (receipt_dir / f"{'b' * 64}.{'c' * 32}.complete").write_text(
        "complete\n", encoding="ascii"
    )
    cancelled = client.delete(
        f"/api/v1/audiobook/sessions/{session_id}",
        headers={"X-Audiobook-Client": payload["client_id"]},
    )
    assert cancelled.status_code == 204
    assert "oohstory_audiobook_client=\"\"" in cancelled.headers["set-cookie"]
    assert "Max-Age=0" in cancelled.headers["set-cookie"]
    assert not receipt_dir.exists()
    assert len(calls[-1][2]) == 64


def test_session_rejects_a_dialect_voice_in_normal_mode():
    response = TestClient(main.app).post(
        "/api/v1/audiobook/sessions",
        json={
            "book_id": BOOK_ID,
            "chapter_id": 1,
            "client_id": "client_1234567890123456",
            "mode": "normal",
            "narrator": "mocheng",
            "voice": "wanqing",
            "emotion": "auto",
            "rate": 1.0,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "所选音源与听书模式不匹配"


def test_session_owner_can_be_resolved_by_the_creating_device() -> None:
    service = AudiobookService(FakeRepository())
    owner = "a" * 64
    device = "b" * 64
    created = service.create(
        owner_key=owner,
        device_key=device,
        book_id=BOOK_ID,
        chapter_id=1,
        settings={**settings(), "mode": "normal"},
    )

    assert service.session_owner(
        created["session_id"], owner_key="c" * 64, device_key=device
    ) == owner
    assert service.session_owner(
        created["session_id"], owner_key="c" * 64, device_key="d" * 64
    ) is None


def test_chapter_stream_is_one_continuous_owner_scoped_batch(monkeypatch, tmp_path):
    session_id = "a" * 32
    manifest_hash = "b" * 64

    class FakeService:
        def manifest(self, seen_session, seen_manifest, owner_key):
            assert seen_session == session_id
            assert seen_manifest == manifest_hash
            assert len(owner_key) == 64
            return {
                "manifest_hash": manifest_hash,
                "segments": [
                    {"index": 0, "text": "第一句"},
                    {"index": 1, "text": "第二句"},
                    {"index": 2, "text": "第三句"},
                ],
            }

    async def fake_segment_bytes(segment):
        return f"mp3-{segment['index']}".encode()

    monkeypatch.setattr(main, "settings", replace(main.settings, state_root=tmp_path))
    monkeypatch.setattr(main, "audiobook_service", lambda: FakeService())
    monkeypatch.setattr(main, "_audiobook_segment_bytes", fake_segment_bytes)
    monkeypatch.setattr(main, "_audiobook_quota", lambda *args, **kwargs: None)
    client = TestClient(main.app)
    response = client.get(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/stream.mp3?start=1",
        headers={"X-Audiobook-Client": "client_1234567890123456"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["accept-ranges"] == "none"
    assert response.headers["x-audiobook-stream-mode"] == "live"
    assert response.content == b"mp3-1mp3-2"
    assert response.headers["x-audiobook-batch-start"] == "1"
    assert response.headers["x-audiobook-batch-end"] == "3"
    assert response.headers["x-audiobook-batch-size"] == "2"
    assert response.headers["x-audiobook-chapter-complete"] == "1"


def test_chapter_stream_starts_batch_synthesis_ahead_of_first_yield(
    monkeypatch, tmp_path
):
    session_id = "a" * 32
    manifest_hash = "b" * 64
    later_started = asyncio.Event()
    started: list[int] = []

    class FakeService:
        def manifest(self, *_args):
            return {
                "manifest_hash": manifest_hash,
                "segments": [
                    {"index": index, "text": f"第{index}句"}
                    for index in range(5)
                ],
            }

    class FreeLimiter:
        @asynccontextmanager
        async def slot(self, *_args):
            yield

    async def fake_segment_bytes(segment):
        index = int(segment["index"])
        started.append(index)
        if index > 0:
            later_started.set()
        if index == 0:
            await asyncio.wait_for(later_started.wait(), timeout=1)
        await asyncio.sleep(0)
        return f"mp3-{index}".encode()

    monkeypatch.setattr(main, "settings", replace(main.settings, state_root=tmp_path))
    monkeypatch.setattr(main, "audiobook_service", lambda: FakeService())
    monkeypatch.setattr(main, "tts_concurrency_limiter", lambda: FreeLimiter())
    monkeypatch.setattr(main, "_audiobook_segment_bytes", fake_segment_bytes)
    monkeypatch.setattr(main, "_audiobook_quota", lambda *args, **kwargs: None)

    response = TestClient(main.app).get(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/stream.mp3",
        headers={"X-Audiobook-Client": "client_1234567890123456"},
    )

    assert response.status_code == 200
    assert response.content == b"mp3-0mp3-1mp3-2mp3-3mp3-4"
    assert 0 in started and any(index > 0 for index in started)


def test_chapter_stream_hard_limits_generation_to_five_manifest_segments(
    monkeypatch, tmp_path
):
    session_id = "a" * 32
    manifest_hash = "b" * 64
    synthesized: list[tuple[int, str, str]] = []

    class FakeService:
        def manifest(self, *_args):
            return {
                "manifest_hash": manifest_hash,
                "segments": [
                    {
                        "index": index,
                        "text": f"第{index}句",
                        "speaker": "角色甲",
                        "gender": "female",
                        "voice": "nuanxi",
                    }
                    for index in range(12)
                ],
            }

    async def fake_segment_bytes(segment):
        synthesized.append(
            (segment["index"], segment["gender"], segment["voice"])
        )
        return f"mp3-{segment['index']}".encode()

    monkeypatch.setattr(main, "settings", replace(main.settings, state_root=tmp_path))
    monkeypatch.setattr(main, "audiobook_service", lambda: FakeService())
    monkeypatch.setattr(main, "_audiobook_segment_bytes", fake_segment_bytes)
    monkeypatch.setattr(main, "_audiobook_quota", lambda *args, **kwargs: None)
    client = TestClient(main.app)
    headers = {"X-Audiobook-Client": "client_1234567890123456"}

    first = client.get(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/stream.mp3?start=2",
        headers=headers,
    )
    assert first.status_code == 200
    assert first.content == b"mp3-2mp3-3mp3-4mp3-5mp3-6"
    assert sorted(synthesized) == [
        (index, "female", "nuanxi") for index in range(2, 7)
    ]
    assert first.headers["x-audiobook-batch-size"] == "5"
    assert first.headers["x-audiobook-batch-end"] == "7"
    assert first.headers["x-audiobook-chapter-complete"] == "0"

    final = client.get(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/stream.mp3?start=7",
        headers=headers,
    )
    assert final.status_code == 200
    assert final.content == b"mp3-7mp3-8mp3-9mp3-10mp3-11"
    assert sorted(synthesized) == [
        (index, "female", "nuanxi") for index in range(2, 12)
    ]
    assert final.headers["x-audiobook-chapter-complete"] == "1"


def test_chapter_continuous_stream_rolls_forward_in_five_segment_windows(
    monkeypatch, tmp_path
):
    session_id = "a" * 32
    manifest_hash = "b" * 64
    active = 0
    max_active = 0
    synthesized: list[int] = []

    class FakeService:
        def manifest(self, *_args):
            return {
                "manifest_hash": manifest_hash,
                "segments": [
                    {"index": index, "text": f"第{index}句"} for index in range(12)
                ],
            }

    class FreeLimiter:
        @asynccontextmanager
        async def slot(self, *_args):
            yield

    async def fake_segment_bytes(segment):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        synthesized.append(int(segment["index"]))
        active -= 1
        return f"mp3-{segment['index']}".encode()

    monkeypatch.setattr(main, "settings", replace(main.settings, state_root=tmp_path))
    monkeypatch.setattr(main, "audiobook_service", lambda: FakeService())
    monkeypatch.setattr(main, "tts_concurrency_limiter", lambda: FreeLimiter())
    monkeypatch.setattr(main, "_audiobook_segment_bytes", fake_segment_bytes)
    monkeypatch.setattr(main, "_audiobook_quota", lambda *args, **kwargs: None)

    response = TestClient(main.app).get(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/stream.mp3"
        "?start=0&continuous=1",
        headers={"X-Audiobook-Client": "client_1234567890123456"},
    )

    assert response.status_code == 200
    assert response.headers["x-audiobook-stream-mode"] == "continuous"
    assert response.headers["x-audiobook-continuous"] == "1"
    assert response.headers["x-audiobook-batch-size"] == "5"
    assert response.headers["x-audiobook-batch-end"] == "5"
    assert response.headers["x-audiobook-chapter-complete"] == "0"
    assert response.content == b"".join(f"mp3-{index}".encode() for index in range(5))
    assert sorted(synthesized) == list(range(5))
    assert max_active <= 5


def test_chapter_stream_trims_only_the_resume_segment(monkeypatch, tmp_path):
    session_id = "a" * 32
    manifest_hash = "b" * 64

    class FakeService:
        def manifest(self, *_args):
            return {
                "manifest_hash": manifest_hash,
                "segments": [
                    {"index": 0, "text": "第一句"},
                    {"index": 1, "text": "第二句"},
                ],
            }

    async def fake_segment_bytes(segment):
        return f"full-{segment['index']}".encode()

    monkeypatch.setattr(main, "settings", replace(main.settings, state_root=tmp_path))
    monkeypatch.setattr(main, "audiobook_service", lambda: FakeService())
    monkeypatch.setattr(main, "_audiobook_segment_bytes", fake_segment_bytes)
    monkeypatch.setattr(
        main,
        "_trim_mp3_audio",
        lambda audio, offset: b"trimmed" if offset == 1250 else audio,
    )
    monkeypatch.setattr(main, "_audiobook_quota", lambda *args, **kwargs: None)
    response = TestClient(main.app).get(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/stream.mp3"
        "?start=0&offset_ms=1250",
        headers={"X-Audiobook-Client": "client_1234567890123456"},
    )
    assert response.status_code == 200
    assert response.content == b"trimmedfull-1"
    assert response.headers["x-audiobook-resume-offset-ms"] == "1250"
    assert response.headers["x-audiobook-stream-mode"] == "live"


def test_chapter_stream_stops_after_session_is_cancelled(monkeypatch, tmp_path):
    session_id = "a" * 32
    manifest_hash = "b" * 64

    class FakeService:
        checks = 0

        def manifest(self, *_args):
            self.checks += 1
            if self.checks > 2:
                raise KeyError("cancelled")
            return {
                "manifest_hash": manifest_hash,
                "segments": [
                    {"index": index, "text": f"第{index}句"} for index in range(6)
                ],
            }

    async def fake_segment_bytes(segment):
        return str(segment["index"]).encode()

    service = FakeService()
    monkeypatch.setattr(main, "settings", replace(main.settings, state_root=tmp_path))
    monkeypatch.setattr(main, "audiobook_service", lambda: service)
    monkeypatch.setattr(main, "_audiobook_segment_bytes", fake_segment_bytes)
    monkeypatch.setattr(main, "_audiobook_quota", lambda *args, **kwargs: None)

    response = TestClient(main.app).get(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/stream.mp3",
        headers={
            "X-Audiobook-Client": "client_1234567890123456",
            "CF-Connecting-IP": "192.0.2.201",
        },
    )

    assert response.status_code == 200
    assert response.content == b"012"


def test_timeline_endpoint_returns_owner_scoped_exact_durations(monkeypatch):
    session_id = "a" * 32
    manifest_hash = "b" * 64

    class FakeService:
        def timeline(self, seen_session, seen_manifest, owner, *, start, limit):
            assert (seen_session, seen_manifest) == (session_id, manifest_hash)
            assert len(owner) == 64
            assert (start, limit) == (7, 12)
            return {
                "manifest_hash": manifest_hash,
                "complete": True,
                "segments": [{"index": 0, "duration_ms": 1234}],
            }

    monkeypatch.setattr(main, "audiobook_service", lambda: FakeService())
    response = TestClient(main.app).get(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/timeline?start=7&limit=12",
        headers={
            "X-Audiobook-Client": "client_1234567890123456",
            "CF-Connecting-IP": "192.0.2.202",
        },
    )
    assert response.status_code == 200
    assert response.json()["segments"][0]["duration_ms"] == 1234
    assert response.headers["cache-control"] == "private, no-store"


def test_timeline_endpoint_defaults_to_five_segment_window(monkeypatch):
    session_id = "a" * 32
    manifest_hash = "b" * 64

    class FakeService:
        def timeline(self, _session_id, _manifest_hash, _owner, *, start, limit):
            assert (start, limit) == (22, 5)
            return {
                "manifest_hash": manifest_hash,
                "complete": False,
                "segments": [],
            }

    monkeypatch.setattr(main, "audiobook_service", lambda: FakeService())
    response = TestClient(main.app).get(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/timeline?start=22",
        headers={
            "X-Audiobook-Client": "client_1234567890123456",
            "CF-Connecting-IP": "192.0.2.203",
        },
    )

    assert response.status_code == 200


def test_chapter_stream_receipt_confirms_only_live_server_eof(
    monkeypatch, tmp_path
):
    session_id = "a" * 32
    manifest_hash = "b" * 64
    stream_id = "c" * 32

    class FakeService:
        def manifest(self, seen_session, seen_manifest, owner_key):
            assert (seen_session, seen_manifest) == (session_id, manifest_hash)
            assert len(owner_key) == 64
            return {
                "manifest_hash": manifest_hash,
                "segments": [{"index": 0, "text": "完整一句"}],
            }

    async def fake_segment_bytes(_segment):
        return b"complete-mp3"

    monkeypatch.setattr(main, "settings", replace(main.settings, state_root=tmp_path))
    monkeypatch.setattr(main, "audiobook_service", lambda: FakeService())
    monkeypatch.setattr(main, "_audiobook_segment_bytes", fake_segment_bytes)
    monkeypatch.setattr(main, "_audiobook_quota", lambda *args, **kwargs: None)
    client = TestClient(main.app)
    headers = {
        "X-Audiobook-Client": "client_1234567890123456",
        "CF-Connecting-IP": "192.0.2.61",
    }

    streamed = client.get(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/stream.mp3"
        f"?start=0&stream_id={stream_id}",
        headers=headers,
    )
    assert streamed.status_code == 200
    assert streamed.content == b"complete-mp3"
    assert streamed.headers["cache-control"] == "private, no-store"
    assert streamed.headers["accept-ranges"] == "none"
    assert streamed.headers["x-audiobook-stream-mode"] == "live"

    completed = client.get(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/streams/{stream_id}",
        headers=headers,
    )
    assert completed.status_code == 200
    assert completed.json() == {"complete": True}

    consumed = client.get(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/streams/{stream_id}",
        headers=headers,
    )
    assert consumed.status_code == 200
    assert consumed.json() == {"complete": False}

def test_duplicate_chapter_stream_get_reuses_session_audio_cache(
    monkeypatch, tmp_path
):
    session_id = "a" * 32
    manifest_hash = "b" * 64
    stream_id = "c" * 32
    synthesized: list[int] = []
    charged: list[str] = []

    class FakeService:
        def manifest(self, seen_session, seen_manifest, owner_key):
            assert (seen_session, seen_manifest) == (session_id, manifest_hash)
            assert len(owner_key) == 64
            return {
                "manifest_hash": manifest_hash,
                "segments": [
                    {"index": 0, "text": "第一句"},
                    {"index": 1, "text": "第二句"},
                ],
            }

    async def fake_segment_bytes(segment):
        synthesized.append(segment["index"])
        return f"mp3-{segment['index']}".encode()

    def fake_quota(_owner, _ip, scope, **_kwargs):
        charged.append(scope)

    monkeypatch.setattr(main, "settings", replace(main.settings, state_root=tmp_path))
    monkeypatch.setattr(main, "audiobook_service", lambda: FakeService())
    monkeypatch.setattr(main, "_audiobook_segment_bytes", fake_segment_bytes)
    monkeypatch.setattr(main, "_audiobook_quota", fake_quota)
    client = TestClient(main.app)
    url = (
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/stream.mp3"
        f"?start=0&stream_id={stream_id}"
    )
    headers = {
        "X-Audiobook-Client": "client_1234567890123456",
        "CF-Connecting-IP": "192.0.2.62",
    }

    first = client.get(url, headers=headers)
    duplicate = client.get(
        url,
        headers={**headers, "CF-Connecting-IP": "192.0.2.63"},
    )
    reconnects = [
        client.get(
            url,
            headers={
                **headers,
                "CF-Connecting-IP": f"192.0.2.{70 + index}",
                "Range": "bytes=0-1",
            },
        )
        for index in range(35)
    ]

    assert first.status_code == 200
    assert first.content == b"mp3-0mp3-1"
    assert first.headers["x-audiobook-stream-mode"] == "live"
    assert first.headers["cache-control"] == "private, no-store"
    assert first.headers["accept-ranges"] == "none"
    assert duplicate.status_code == 200
    assert duplicate.content == first.content
    assert duplicate.headers["x-audiobook-stream-mode"] == "live"
    assert duplicate.headers["accept-ranges"] == "none"
    assert all(response.status_code == 200 for response in reconnects)
    assert sorted(synthesized) == [0, 1]
    assert charged.count("chapter-stream-requests") == 1
    assert "characters" not in charged
    assert not hasattr(main, "_audiobook_chapter_stream_cache_path")
    assert not hasattr(main, "_cached_audiobook_chapter_stream")

    fresh = client.get(
        url.replace(stream_id, "d" * 32),
        headers={**headers, "CF-Connecting-IP": "192.0.2.64"},
    )
    assert fresh.status_code == 200
    assert fresh.content == b"mp3-0mp3-1"
    assert fresh.headers["x-audiobook-stream-mode"] == "live"
    assert sorted(synthesized[-2:]) == [0, 1]
    assert "characters" not in charged


def test_duplicate_continuous_stream_get_is_idempotent(
    monkeypatch, tmp_path
):
    session_id = "a" * 32
    manifest_hash = "b" * 64
    stream_id = "c" * 32
    synthesized: list[int] = []
    charged: list[str] = []

    class FakeService:
        def manifest(self, seen_session, seen_manifest, owner_key):
            assert (seen_session, seen_manifest) == (session_id, manifest_hash)
            assert len(owner_key) == 64
            return {
                "manifest_hash": manifest_hash,
                "segments": [
                    {"index": 0, "text": "第一句"},
                    {"index": 1, "text": "第二句"},
                    {"index": 2, "text": "第三句"},
                ],
            }

    async def fake_segment_bytes(segment):
        synthesized.append(segment["index"])
        return f"mp3-{segment['index']}".encode()

    def fake_quota(_owner, _ip, scope, **_kwargs):
        charged.append(scope)

    monkeypatch.setattr(main, "settings", replace(main.settings, state_root=tmp_path))
    monkeypatch.setattr(main, "audiobook_service", lambda: FakeService())
    monkeypatch.setattr(main, "_audiobook_segment_bytes", fake_segment_bytes)
    monkeypatch.setattr(main, "_audiobook_quota", fake_quota)
    client = TestClient(main.app)
    url = (
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/stream.mp3"
        f"?start=0&stream_id={stream_id}&continuous=1"
    )
    headers = {
        "X-Audiobook-Client": "client_1234567890123456",
        "CF-Connecting-IP": "192.0.2.62",
    }

    first = client.get(url, headers=headers)
    duplicate = client.get(
        url,
        headers={**headers, "CF-Connecting-IP": "192.0.2.63"},
    )

    assert first.status_code == 200
    assert first.content == b"mp3-0mp3-1mp3-2"
    assert first.headers["x-audiobook-stream-mode"] == "continuous"
    assert duplicate.status_code == 200
    assert duplicate.content == first.content
    assert duplicate.headers["x-audiobook-start-segment"] == "0"
    assert duplicate.headers["x-audiobook-stream-mode"] == "continuous"
    assert sorted(synthesized) == [0, 1, 2]
    assert charged.count("chapter-stream-requests") == 1


def test_parallel_duplicate_stream_gets_share_session_audio(monkeypatch, tmp_path):
    session_id = "a" * 32
    manifest_hash = "b" * 64
    stream_id = "c" * 32
    synthesized: list[int] = []

    class FakeService:
        def manifest(self, *_args):
            return {
                "manifest_hash": manifest_hash,
                "segments": [
                    {"index": index, "text": f"第{index}句"} for index in range(3)
                ],
            }

    async def fake_segment_bytes(segment):
        await asyncio.sleep(0.05)
        synthesized.append(int(segment["index"]))
        return f"stable-{segment['index']}".encode()

    monkeypatch.setattr(main, "settings", replace(main.settings, state_root=tmp_path))
    monkeypatch.setattr(main, "audiobook_service", lambda: FakeService())
    monkeypatch.setattr(main, "_audiobook_segment_bytes", fake_segment_bytes)
    monkeypatch.setattr(main, "_audiobook_quota", lambda *args, **kwargs: None)
    url = (
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/stream.mp3"
        f"?start=0&stream_id={stream_id}&continuous=1"
    )
    headers = {"X-Audiobook-Client": "client_1234567890123456"}

    def fetch() -> bytes:
        response = TestClient(main.app).get(url, headers=headers)
        assert response.status_code == 200
        return response.content

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _index: fetch(), range(2)))

    assert first == second == b"stable-0stable-1stable-2"
    assert sorted(synthesized) == [0, 1, 2]


def test_full_chapter_stream_uses_one_url_and_five_segment_synthesis_windows(
    monkeypatch, tmp_path
):
    session_id = "a" * 32
    manifest_hash = "b" * 64
    synthesized: list[int] = []

    class FakeService:
        def manifest(self, *_args):
            return {
                "manifest_hash": manifest_hash,
                "segments": [
                    {"index": index, "text": f"第{index}句"} for index in range(12)
                ],
            }

    async def fake_segment_bytes(segment):
        synthesized.append(int(segment["index"]))
        return f"mp3-{segment['index']}".encode()

    monkeypatch.setattr(main, "settings", replace(main.settings, state_root=tmp_path))
    monkeypatch.setattr(main, "audiobook_service", lambda: FakeService())
    monkeypatch.setattr(main, "_audiobook_segment_bytes", fake_segment_bytes)
    monkeypatch.setattr(main, "_audiobook_quota", lambda *args, **kwargs: None)

    response = TestClient(main.app).get(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/stream.mp3"
        "?start=2&continuous=1&full_chapter=1",
        headers={"X-Audiobook-Client": "client_1234567890123456"},
    )

    assert response.status_code == 200
    assert response.headers["x-audiobook-full-chapter"] == "1"
    assert response.headers["x-audiobook-batch-size"] == "5"
    assert response.headers["x-audiobook-stream-end"] == "12"
    assert response.headers["x-audiobook-chapter-complete"] == "1"
    assert response.content == b"".join(
        f"mp3-{index}".encode() for index in range(2, 12)
    )
    assert sorted(synthesized) == list(range(2, 12))


def test_legacy_stream_ids_are_normalized_before_streaming(
    monkeypatch, tmp_path
):
    session_id = "a" * 32
    manifest_hash = "b" * 64
    legacy_stream_id = "explicit178655069881107746"
    synthesized: list[int] = []
    charged: list[str] = []

    class FakeService:
        def manifest(self, seen_session, seen_manifest, owner_key):
            assert (seen_session, seen_manifest) == (session_id, manifest_hash)
            assert len(owner_key) == 64
            return {
                "manifest_hash": manifest_hash,
                "segments": [
                    {"index": 22, "text": "第二十二段"},
                    {"index": 23, "text": "第二十三段"},
                    {"index": 24, "text": "第二十四段"},
                ],
            }

    async def fake_segment_bytes(segment):
        synthesized.append(segment["index"])
        return f"mp3-{segment['index']}".encode()

    def fake_quota(_owner, _ip, scope, **_kwargs):
        charged.append(scope)

    monkeypatch.setattr(main, "settings", replace(main.settings, state_root=tmp_path))
    monkeypatch.setattr(main, "audiobook_service", lambda: FakeService())
    monkeypatch.setattr(main, "_audiobook_segment_bytes", fake_segment_bytes)
    monkeypatch.setattr(main, "_audiobook_quota", fake_quota)
    client = TestClient(main.app)
    url = (
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/stream.mp3"
        f"?start=0&stream_id={legacy_stream_id}&continuous=1"
    )
    headers = {
        "X-Audiobook-Client": "client_1234567890123456",
        "CF-Connecting-IP": "192.0.2.65",
    }

    first = client.get(url, headers=headers)
    duplicate = client.get(
        url,
        headers={**headers, "CF-Connecting-IP": "192.0.2.66"},
    )
    completed = client.get(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}"
        f"/streams/{legacy_stream_id}",
        headers=headers,
    )

    assert first.status_code == 200
    assert first.content == b"mp3-22mp3-23mp3-24"
    assert first.headers["x-audiobook-start-segment"] == "0"
    assert duplicate.status_code == 200
    assert duplicate.content == first.content
    assert duplicate.headers["x-audiobook-start-segment"] == "0"
    assert completed.status_code == 200
    assert completed.json() == {"complete": True}
    assert sorted(synthesized) == [22, 23, 24]
    assert charged.count("chapter-stream-requests") == 1


def test_chapter_stream_ignores_legacy_segment_cache_and_synthesizes_live(
    monkeypatch, tmp_path
):
    session_id = "a" * 32
    manifest_hash = "b" * 64
    stream_id = "c" * 32
    spoken_hash = "d" * 64
    charged: list[str] = []
    legacy = tmp_path / "tts-audio-cache" / spoken_hash[:2] / f"{spoken_hash}.mp3"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"stale-spoken-mp3")

    class FakeService:
        def manifest(self, seen_session, seen_manifest, owner_key):
            assert (seen_session, seen_manifest) == (session_id, manifest_hash)
            assert len(owner_key) == 64
            return {
                "manifest_hash": manifest_hash,
                "segments": [
                    {"index": 0, "text": "——", "sha256": "e" * 64},
                    {"index": 1, "text": "已有音频", "sha256": spoken_hash},
                ],
            }

    async def fake_segment_bytes(segment):
        return f"fresh-{segment['index']}".encode()

    def fake_quota(_owner, _ip, scope, **_kwargs):
        charged.append(scope)

    monkeypatch.setattr(
        main,
        "settings",
        replace(main.settings, state_root=tmp_path, audiobook_audio_root=tmp_path),
    )
    monkeypatch.setattr(main, "audiobook_service", lambda: FakeService())
    monkeypatch.setattr(main, "_audiobook_segment_bytes", fake_segment_bytes)
    monkeypatch.setattr(main, "_audiobook_quota", fake_quota)
    client = TestClient(main.app)
    response = client.get(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/stream.mp3"
        f"?start=0&stream_id={stream_id}",
        headers={
            "X-Audiobook-Client": "client_1234567890123456",
            "CF-Connecting-IP": "192.0.2.68",
        },
    )

    assert response.status_code == 200
    assert response.content == b"fresh-0fresh-1"
    assert response.headers["x-audiobook-stream-mode"] == "live"
    assert response.headers["accept-ranges"] == "none"
    assert charged == ["chapter-stream-requests"]
    assert legacy.read_bytes() == b"stale-spoken-mp3"


def test_chapter_stream_never_bypasses_session_owner_validation(
    monkeypatch, tmp_path
):
    session_id = "a" * 32
    manifest_hash = "b" * 64
    monkeypatch.setattr(main, "settings", replace(main.settings, state_root=tmp_path))

    class MissingSessionService:
        def manifest(self, *_args):
            raise KeyError("wrong owner")

    monkeypatch.setattr(main, "audiobook_service", lambda: MissingSessionService())
    client = TestClient(main.app)
    response = client.get(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/stream.mp3",
        headers={
            "X-Audiobook-Client": "client_1234567890123456",
            "CF-Connecting-IP": "192.0.2.67",
        },
    )

    assert response.status_code == 404


def test_chapter_stream_live_path_receipts_eof_without_cache_directory(
    monkeypatch, tmp_path
):
    session_id = "a" * 32
    manifest_hash = "b" * 64
    stream_id = "c" * 32

    class FakeService:
        def manifest(self, *_args):
            return {
                "manifest_hash": manifest_hash,
                "segments": [{"index": 0, "text": "继续播放"}],
            }

    async def fake_segment_bytes(_segment):
        return b"live-mp3"

    monkeypatch.setattr(
        main,
        "settings",
        replace(main.settings, state_root=tmp_path, audiobook_audio_root=tmp_path),
    )
    monkeypatch.setattr(main, "audiobook_service", lambda: FakeService())
    monkeypatch.setattr(main, "_audiobook_segment_bytes", fake_segment_bytes)
    monkeypatch.setattr(main, "_audiobook_quota", lambda *_args, **_kwargs: None)
    client = TestClient(main.app)
    headers = {
        "X-Audiobook-Client": "client_1234567890123456",
        "CF-Connecting-IP": "192.0.2.69",
    }
    streamed = client.get(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/stream.mp3"
        f"?start=0&stream_id={stream_id}",
        headers=headers,
    )

    assert streamed.status_code == 200
    assert streamed.content == b"live-mp3"
    assert streamed.headers["x-audiobook-stream-mode"] == "live"
    assert not (tmp_path / "audiobook-chapter-stream-cache").exists()
    completed = client.get(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/{manifest_hash}/streams/{stream_id}",
        headers=headers,
    )
    assert completed.json() == {"complete": True}


def test_chapter_stream_persistent_mp3_cache_helpers_are_removed():
    source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(
        encoding="utf-8"
    )

    assert "audiobook-chapter-stream-cache" not in source
    assert "tts-audio-cache" not in source
    assert "X-Audiobook-Stream-Mode\": \"seekable\"" not in source
    assert "SharedAudioWork" not in source


def test_tts_retries_recoverable_provider_failures(monkeypatch):
    attempts = 0

    class FakeCommunicate:
        def __init__(self, *_args, **_kwargs):
            pass

        async def stream(self):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError("temporary provider failure")
            yield {"type": "audio", "data": b"recovered-mp3"}

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(main.edge_tts, "Communicate", FakeCommunicate)
    monkeypatch.setattr(main.asyncio, "sleep", no_sleep)

    audio, emotion = asyncio.run(
        main._synthesize_tts_bytes(
            "恢复播放", "mocheng", "+0%", "+0Hz", "+0%", "neutral"
        )
    )
    assert audio == b"recovered-mp3"
    assert emotion == "neutral"
    assert attempts == 3


def test_punctuation_only_segments_use_a_valid_pause_without_edge_tts(monkeypatch):
    assert not has_spoken_content("——……")

    async def should_not_run(*_args, **_kwargs):
        raise AssertionError("punctuation-only segment reached Edge TTS")

    monkeypatch.setattr(main, "_synthesize_tts_bytes", should_not_run)
    audio = asyncio.run(
        main._audiobook_segment_bytes(
            {
                "text": "——",
                "voice": "lingxian",
                "rate": 1.0,
                "emotion": "neutral",
                "sha256": "f" * 64,
            }
        )
    )
    assert audio.startswith(b"ID3")
    assert len(audio) > 100


def test_prefetch_and_activation_http_contract(monkeypatch):
    calls = []
    session_id = "c" * 32

    class FakeService:
        def prefetch_next(self, seen_session, owner_key, from_chapter_id=None):
            calls.append(("prefetch", seen_session, len(owner_key), from_chapter_id))
            return {"chapter_id": 14}

        def activate_chapter(self, seen_session, chapter_id, owner_key):
            calls.append(("activate", seen_session, chapter_id, len(owner_key)))
            return True

    monkeypatch.setattr(main, "audiobook_service", lambda: FakeService())
    monkeypatch.setattr(main, "_audiobook_quota", lambda *args, **kwargs: None)
    client = TestClient(main.app)
    headers = {
        "X-Audiobook-Client": "client_1234567890123456",
        "CF-Connecting-IP": "192.0.2.62",
    }

    prefetched = client.post(
        f"/api/v1/audiobook/sessions/{session_id}/next?from_chapter_id=13",
        headers=headers,
    )
    assert prefetched.status_code == 200
    assert prefetched.json()["next"]["chapter_id"] == 14

    activated = client.post(
        f"/api/v1/audiobook/sessions/{session_id}/chapters/14/activate",
        headers=headers,
    )
    assert activated.status_code == 204
    assert calls[0][0::3] == ("prefetch", 13)
    assert calls[1][0:3] == ("activate", session_id, 14)


def test_nginx_allows_only_the_audiobook_session_methods() -> None:
    nginx = (Path(__file__).resolve().parents[1] / "deploy" / "nginx-oohstory.conf").read_text(encoding="utf-8")

    assert "POST:/api/v1/audiobook/sessions$" in nginx
    assert "DELETE:/api/v1/audiobook/sessions/[0-9a-f]{32}$" in nginx
    assert "POST:/api/v1/audiobook/sessions/[0-9a-f]{32}/next$" in nginx
    assert "POST:/api/v1/audiobook/sessions/[0-9a-f]{32}/chapters/[1-9][0-9]*/activate$" in nginx
    assert "PUT:/api/v1/audiobook/sessions/[0-9a-f]{32}/progress$" in nginx
    assert "POST:/api/v1/audiobook/sessions/[0-9a-f]{32}/chapters/[1-9][0-9]*/activate$" in nginx
    assert "streams/[0-9a-f]{32}" in nginx
    assert "POST:/api/v1/audiobook/sessions/[0-9a-f]{32}/segments/[0-9a-f]{64}/[0-9]+$" in nginx
    assert "[0-9a-f]{64}/(?:stream\\.mp3|streams/[0-9a-f]{32}|timeline)" in nginx
    assert "/api/v1/audiobook/capacity" in (
        Path(__file__).resolve().parents[1] / "static" / "audiobook-cache.js"
    ).read_text(encoding="utf-8")
    location = nginx.split(
        'location ~ "^/api/v1/audiobook/sessions', 1
    )[1].split("\n    }", 1)[0]
    assert "client_max_body_size 2k;" in location
    assert "proxy_read_timeout 3600s;" in location
    assert "proxy_request_buffering off;" in location
    assert "proxy_buffering off;" in location


def test_two_owners_same_book_different_chapters_are_fully_isolated():
    service = AudiobookService(FakeRepository())
    owner_a = "a" * 64
    owner_b = "b" * 64
    session_a = service.create(owner_key=owner_a, book_id=BOOK_ID, chapter_id=1, settings=settings())
    session_b = service.create(owner_key=owner_b, book_id=BOOK_ID, chapter_id=2, settings=settings())

    assert session_a["session_id"] != session_b["session_id"]
    assert session_a["current"]["chapter_id"] == 1
    assert session_b["current"]["chapter_id"] == 2

    next_a = service.prefetch_next(session_a["session_id"], owner_a)
    assert next_a and next_a["chapter_id"] == 2

    manifest_a = service.manifest(session_a["session_id"], session_a["current"]["manifest_hash"], owner_a)
    assert manifest_a["chapter_id"] == 1
    manifest_b = service.manifest(session_b["session_id"], session_b["current"]["manifest_hash"], owner_b)
    assert manifest_b["chapter_id"] == 2

    try:
        service.manifest(session_a["session_id"], session_a["current"]["manifest_hash"], owner_b)
    except KeyError:
        pass
    else:
        raise AssertionError("owner B accessed owner A's manifest")

    try:
        service.manifest(session_b["session_id"], session_b["current"]["manifest_hash"], owner_a)
    except KeyError:
        pass
    else:
        raise AssertionError("owner A accessed owner B's manifest")

    try:
        service.prefetch_next(session_a["session_id"], owner_b)
    except KeyError:
        pass
    else:
        raise AssertionError("owner B advanced owner A's cursor")

    next_b = service.prefetch_next(session_b["session_id"], owner_b)
    assert next_b and next_b["chapter_id"] == 3
    assert next_a["manifest_hash"] != next_b["manifest_hash"] or next_a["chapter_id"] == next_b["chapter_id"]


def test_cancel_one_session_does_not_affect_other_sessions():
    service = AudiobookService(FakeRepository())
    owner_a = "c" * 64
    owner_b = "d" * 64
    session_a = service.create(owner_key=owner_a, book_id=BOOK_ID, chapter_id=1, settings=settings())
    session_b = service.create(owner_key=owner_b, book_id=BOOK_ID, chapter_id=1, settings=settings())
    session_a2 = service.create(owner_key=owner_a, book_id=BOOK_ID, chapter_id=2, settings=settings())

    assert service.cancel(session_a["session_id"], owner_a)

    try:
        service.manifest(session_a["session_id"], session_a["current"]["manifest_hash"], owner_a)
    except KeyError:
        pass
    else:
        raise AssertionError("cancelled session remained readable")

    manifest_b = service.manifest(session_b["session_id"], session_b["current"]["manifest_hash"], owner_b)
    assert manifest_b["chapter_id"] == 1
    manifest_a2 = service.manifest(session_a2["session_id"], session_a2["current"]["manifest_hash"], owner_a)
    assert manifest_a2["chapter_id"] == 2

    assert not service.cancel(session_a["session_id"], owner_b)


def test_cancel_owner_only_affects_own_sessions():
    service = AudiobookService(FakeRepository())
    owner_a = "e" * 64
    owner_b = "f" * 64
    service.create(owner_key=owner_a, book_id=BOOK_ID, chapter_id=1, settings=settings())
    service.create(owner_key=owner_a, book_id=BOOK_ID, chapter_id=2, settings=settings())
    session_b = service.create(owner_key=owner_b, book_id=BOOK_ID, chapter_id=1, settings=settings())

    assert service.cancel_owner(owner_a) == 2

    manifest_b = service.manifest(session_b["session_id"], session_b["current"]["manifest_hash"], owner_b)
    assert manifest_b["chapter_id"] == 1


def test_prefetch_is_read_only_until_chapter_activation():
    service = AudiobookService(FakeRepository())
    owner = "9" * 64
    session = service.create(owner_key=owner, book_id=BOOK_ID, chapter_id=1, settings=settings())
    sid = session["session_id"]

    ch2 = service.prefetch_next(sid, owner)
    assert ch2 and ch2["chapter_id"] == 2

    repeated = service.prefetch_next(sid, owner)
    assert repeated and repeated["chapter_id"] == 2

    assert service.activate_chapter(sid, 2, owner)
    ch3 = service.prefetch_next(sid, owner)
    assert ch3 and ch3["chapter_id"] == 3

    assert service.activate_chapter(sid, 3, owner)
    assert service.prefetch_next(sid, owner) is None
    assert service.prefetch_next(sid, owner) is None


def test_concurrent_prefetch_threads_return_same_next_without_moving_cursor():
    import threading

    service = AudiobookService(FakeRepository())
    owner = "0" * 64
    session = service.create(owner_key=owner, book_id=BOOK_ID, chapter_id=1, settings=settings())
    sid = session["session_id"]

    results: list[dict | None] = [None, None]

    def prefetch(idx):
        results[idx] = service.prefetch_next(sid, owner)

    t1 = threading.Thread(target=prefetch, args=(0,))
    t2 = threading.Thread(target=prefetch, args=(1,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    chapters_returned = {r["chapter_id"] for r in results if r is not None}
    assert 2 in chapters_returned

    assert chapters_returned == {2}
    assert service.activate_chapter(sid, 2, owner)
    remaining = service.prefetch_next(sid, owner)
    assert remaining and remaining["chapter_id"] == 3


def test_shared_manifests_reused_across_sessions_with_same_settings():
    service = AudiobookService(FakeRepository())
    session_a = service.create(owner_key="a" * 64, book_id=BOOK_ID, chapter_id=1, settings=settings())
    session_b = service.create(owner_key="b" * 64, book_id=BOOK_ID, chapter_id=1, settings=settings())
    assert session_a["current"]["manifest_hash"] == session_b["current"]["manifest_hash"]

    different_settings = {**settings(), "rate": 0.8}
    session_c = service.create(owner_key="c" * 64, book_id=BOOK_ID, chapter_id=1, settings=different_settings)
    assert session_c["current"]["manifest_hash"] != session_a["current"]["manifest_hash"]


def test_character_voice_change_cannot_reuse_the_previous_segment_audio():
    def manifest_with_female_voice(voice_key: str) -> dict:
        service = AudiobookService(FakeRepository())

        def fixed_cast(_catalog_id, characters, _published_cast):
            return {
                character["canonical_name"]: {
                    **character,
                    "character_key": character["canonical_name"].encode(),
                    "voice_key": (
                        voice_key
                        if character.get("gender") == "female"
                        else "qingyan"
                    ),
                }
                for character in characters
            }

        service._playback_cast = fixed_cast
        return service._manifest(BOOK_ID, 1, settings())

    original = manifest_with_female_voice("nuanxi")
    repeated = manifest_with_female_voice("nuanxi")
    changed = manifest_with_female_voice("shuanger")
    original_line = next(
        item for item in original["segments"] if item.get("speaker") == "林雪"
    )
    repeated_line = next(
        item for item in repeated["segments"] if item.get("speaker") == "林雪"
    )
    changed_line = next(
        item for item in changed["segments"] if item.get("speaker") == "林雪"
    )

    assert original_line["voice"] == repeated_line["voice"] == "nuanxi"
    assert original_line["sha256"] == repeated_line["sha256"]
    assert changed_line["voice"] == "shuanger"
    assert changed_line["sha256"] != original_line["sha256"]
    assert changed["manifest_hash"] != original["manifest_hash"]


class _ManifestContext:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *_args):
        return False


class _ManifestCursor:
    def __init__(self, stored_manifest):
        self.stored_manifest = stored_manifest
        self.queries: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, _params=None):
        self.queries.append(" ".join(query.split()))

    def fetchone(self):
        return {"manifest_json": json.dumps(self.stored_manifest, ensure_ascii=False)}


class _ManifestConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return _ManifestContext(self._cursor)


class _ManifestPool:
    def __init__(self, cursor):
        self._connection = _ManifestConnection(cursor)

    def transaction(self):
        return _ManifestContext(self._connection)


def test_persist_manifest_never_mutates_a_referenced_manifest_hash():
    service = AudiobookService(FakeRepository())
    stored = {
        "chapter_id": 1,
        "content_hash": "1" * 64,
        "settings_hash": "2" * 64,
        "engine_version": ENGINE_VERSION,
        "manifest_hash": "a" * 64,
        "mode": "smart",
        "selected_voice": "nuanxi",
        "requested_narrator": "mocheng",
        "effective_narrator": "mocheng",
        "segments": [{
            "index": 0,
            "kind": "narration",
            "voice": "mocheng",
            "text": "旧清单",
            "sha256": "3" * 64,
        }],
    }
    candidate = {
        **stored,
        "manifest_hash": "b" * 64,
        "segments": [{
            "index": 0,
            "kind": "narration",
            "voice": "mocheng",
            "text": "重算清单",
            "sha256": "4" * 64,
        }],
    }
    cursor = _ManifestCursor(stored)
    service.repository._mysql = type(
        "ManifestMySQL", (), {"pool": _ManifestPool(cursor)}
    )()

    persisted = service._persist_manifest(304471, candidate)

    assert persisted["manifest_hash"] == stored["manifest_hash"]
    assert persisted["segments"] == stored["segments"]
    assert cursor.queries[0].startswith("INSERT IGNORE INTO audiobook_chapter_manifests")
    assert all("manifest_hash=VALUES" not in query for query in cursor.queries)
    assert any(query.endswith("FOR UPDATE") for query in cursor.queries)


def test_manifest_reuses_stored_semantic_key_before_reanalyzing(monkeypatch):
    service = AudiobookService(FakeRepository())
    stored = {
        "chapter_id": 1,
        "content_hash": "1" * 64,
        "settings_hash": "2" * 64,
        "engine_version": ENGINE_VERSION,
        "manifest_hash": "a" * 64,
        "segments": [{"index": 0, "text": "稳定清单", "sha256": "3" * 64}],
    }
    monkeypatch.setattr(service, "_catalog_id", lambda _book_id: 304471)
    monkeypatch.setattr(service, "_stored_manifest", lambda *_args: stored)

    def unexpected_cast_load(_catalog_id):
        raise AssertionError("stored manifest should bypass mutable cast analysis")

    monkeypatch.setattr(service, "_existing_cast", unexpected_cast_load)

    assert service._manifest(BOOK_ID, 1, settings()) is stored


def test_cancel_does_not_delete_manifest_links_when_owner_check_fails():
    class Cursor:
        rowcount = 0

        def __init__(self):
            self.queries = []

        def execute(self, query, _params=None):
            self.queries.append(" ".join(query.split()))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Context:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self.value

        def __exit__(self, *_args):
            return False

    cursor = Cursor()
    connection = type("Connection", (), {"cursor": lambda self: Context(cursor)})()
    pool = type("Pool", (), {"transaction": lambda self: Context(connection)})()
    repository = type("Repo", (), {"_mysql": type("MySQL", (), {"pool": pool})()})()
    service = object.__new__(AudiobookService)
    service.repository = repository

    assert service.cancel("a" * 32, "b" * 64) is False
    assert len(cursor.queries) == 1
    assert cursor.queries[0].startswith("UPDATE audiobook_sessions")


def test_cancelled_session_record_is_physically_purged(monkeypatch):
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params):
            calls.append((" ".join(query.split()), params))

    class Context:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self.value

        def __exit__(self, *_args):
            return False

    connection = type("Connection", (), {"cursor": lambda self: Cursor()})()
    pool = type("Pool", (), {"transaction": lambda self: Context(connection)})()
    repository = type(
        "Repo", (), {"_mysql": type("MySQL", (), {"pool": pool})()}
    )()
    service = type("Service", (), {"repository": repository})()
    monkeypatch.setattr(main, "audiobook_service", lambda: service)

    main._purge_cancelled_audiobook_session("a" * 32, "b" * 64)

    assert calls == [
        (
            "DELETE FROM audiobook_sessions WHERE session_id=%s "
            "AND owner_hash=UNHEX(%s) AND cancelled=1",
            ("a" * 32, "b" * 64),
        )
    ]


def test_mp3_duration_uses_frame_timing_instead_of_fixed_byte_rate():
    header = bytes.fromhex("fffb9000")
    frame_size = 417
    audio = b"".join(header + bytes(frame_size - 4) for _ in range(10))

    assert 255 <= mp3_duration_ms(audio) <= 265
