from pathlib import Path

from oohstory_library.services.electronic_library import ElectronicLibraryService


def test_explicit_summary_stops_before_local_media_markers() -> None:
    sample = """作品名
作者：作者
简介：
这是一段真实的作品简介。
【第一卷】
分卷封面
[本地分卷封面：book/001/封面/cover.jpg]
[本地插图：book/001/插图/image.jpg]
第一章 开始
正文内容。
"""

    assert ElectronicLibraryService._summary_from_sample(sample) == "这是一段真实的作品简介。"


def test_missing_summary_uses_first_chapter_first_300_characters() -> None:
    first_chapter = "第一章正文" * 80
    sample = f"""作品名
作者：作者
【第一卷】
分卷封面
[本地分卷封面：book/001/封面/cover.jpg]
插画
[本地插图：book/001/插图/image.jpg]
第一章 开始
{first_chapter}
第二章 后续
不能进入简介
"""

    summary = ElectronicLibraryService._summary_from_sample(sample)

    assert summary == first_chapter[:300]
    assert len(summary) == 300
    assert "本地插图" not in summary
    assert "第二章" not in summary


def test_large_file_sampling_always_includes_the_beginning(tmp_path: Path) -> None:
    path = tmp_path / "large.txt"
    path.write_text(
        "作品名\n简介：\n开头真实简介\n第一章 开始\n正文\n"
        + ("后续内容\n" * 30_000),
        encoding="utf-8",
    )

    sample = ElectronicLibraryService._read_sample(path, chunk_size=1024)

    assert sample.startswith("<<<TONE_SAMPLE:00>>>\n作品名\n简介：\n开头真实简介")
    assert ElectronicLibraryService._summary_from_sample(sample) == "开头真实简介"
