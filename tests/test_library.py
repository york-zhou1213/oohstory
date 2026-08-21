from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.library import (
    OOHSTORY_DEFAULT_COVER_URL,
    InputError,
    LibraryRepository,
    UnsafePathError,
    _clean_summary,
)
from app.settings import DEFAULT_LIBRARY_ROOT, Settings


def test_default_library_paths_do_not_depend_on_webnovel_project() -> None:
    project_root = Path(__file__).resolve().parents[1]
    assert DEFAULT_LIBRARY_ROOT == project_root / "data" / "library"
    for relative in (
        "deploy/sync-public-deconstructions.sh",
        "deploy/grant-library-read-access.sh",
        "deploy/oohstory-deconstruction-sync.service",
    ):
        text = (project_root / relative).read_text(encoding="utf-8")
        assert "webnovel-writer" not in text


@pytest.fixture()
def repository(tmp_path: Path) -> LibraryRepository:
    library = tmp_path / "library"
    books = library / "书籍" / "科幻灵异"
    covers = library / "封面"
    index_root = library / "全局索引" / "阅读目录"
    decon = library / "全局拆书库" / "样本书"
    for path in (books, covers, index_root, decon):
        path.mkdir(parents=True)
    default_cover = (
        library / ".oohstory-default-assets" / "oohstory-default-cover-"
        "d421cee15a266d258979455101443085bbc686504ee802c55686cbfc92d0b09e.jpg"
    )
    default_cover.parent.mkdir(parents=True)
    default_cover.write_bytes(b"\xff\xd8\xff" + b"shared-default")
    source = books / "样本书__作者__1.txt"
    source.write_text(
        "样本书\n作者：测试\n\n第一章 出发\n第一段正文。\n\n第二章 星海\n第二段正文。",
        encoding="utf-8",
    )
    catalog = sqlite3.connect(library / "catalog.sqlite3")
    catalog.executescript(
        """
        CREATE TABLE books (
          id INTEGER PRIMARY KEY, source_id TEXT, title TEXT, author TEXT,
          category TEXT, detail_url TEXT, status TEXT, output_path TEXT, bytes INTEGER,
          discovered_at TEXT, updated_at TEXT
        );
        """
    )
    catalog.execute(
        "INSERT INTO books VALUES (1,'1','样本书','作者','科幻灵异',"
        "'https://www.txt80.cc/kehuan/txt1.html','done',?,?,NULL,NULL)",
        (str(source), source.stat().st_size),
    )
    catalog.commit()
    catalog.close()
    index = sqlite3.connect(library / "全局索引" / "electronic_library_index.sqlite3")
    index.executescript(
        """
        CREATE TABLE library_index (
          catalog_id INTEGER, summary TEXT, approx_word_count INTEGER,
          approx_chapter_count INTEGER, genre_tags TEXT, tone_tags TEXT
        );
        INSERT INTO library_index VALUES
          (1,'这是简介',100,2,'["科幻"]','["热血"]');
        """
    )
    index.commit()
    index.close()
    cover_db = sqlite3.connect(covers / "index.sqlite3")
    cover_db.execute(
        "CREATE TABLE covers (catalog_id INTEGER, filename TEXT, status TEXT)"
    )
    cover_db.commit()
    cover_db.close()
    (decon / "概要.md").write_text("# 样本书\n结构完整。", encoding="utf-8")
    (decon / "_progress.md").write_text(
        "| Stage 2 逐章摘要 | running | 2/10 |",
        encoding="utf-8",
    )
    settings = Settings(
        library_root=library.resolve(),
        state_root=(tmp_path / "state").resolve(),
        allowed_hosts=("testserver",),
    )
    return LibraryRepository(settings)


def test_lists_and_enriches_books(repository: LibraryRepository):
    result = repository.list_books(query="样本", category="科幻灵异")
    assert result["total"] == 1
    assert result["items"][0]["summary"] == "这是简介"
    assert result["items"][0]["genre_tags"] == ["科幻"]
    assert result["items"][0]["serialization_status"] == "finished"
    assert result["items"][0]["cover_url"] == OOHSTORY_DEFAULT_COVER_URL
    assert result["items"][0]["cover_is_default"] is True
    assert not repository.settings.index_path.with_name(
        repository.settings.index_path.name + "-shm"
    ).exists()
    assert not repository.settings.index_path.with_name(
        repository.settings.index_path.name + "-wal"
    ).exists()


def test_filters_books_by_word_count_and_serialization(repository: LibraryRepository):
    assert repository.list_books(words="under_100k")["total"] == 1
    assert repository.list_books(words="over_100k")["total"] == 0
    assert repository.list_books(serialization="finished")["total"] == 1
    assert repository.list_books(serialization="ongoing")["total"] == 0


def test_reader_builds_safe_chapter_index(repository: LibraryRepository):
    catalog = repository.reader_catalog(1)
    assert catalog["chapter_count"] == 2
    assert repository.reader_chapter_count(1) == 2
    chapter = repository.reader_chapter(1, 1)
    assert chapter["title"] == "出发"
    assert "第一段正文" in chapter["content"]
    assert chapter["next_id"] == 2


def test_reader_does_not_reorder_zero_or_duplicate_chapter_numbers() -> None:
    chapters = [
        {"id": index, "label": label, "title": f"标题{index}"}
        for index, label in enumerate(
            [
                "序", "第001章", "第002章", "第00章", "第004章", "第005章",
                "第01章", "第007章", "第008章", "第009章", "第010章",
                "第010章",
            ],
            1,
        )
    ]

    repaired = LibraryRepository._sort_chapters_by_number(chapters)

    assert [item["id"] for item in repaired] == [item["id"] for item in chapters]


def test_reader_repairs_dense_unique_numeric_order_in_numbered_slots() -> None:
    labels = [
        "序", "第1章", "第2章", "第4章", "第3章", "第5章",
        "第6章", "第7章", "第8章", "第9章", "第10章", "尾声",
    ]
    chapters = [
        {"id": index, "label": label, "title": label}
        for index, label in enumerate(labels, 1)
    ]

    repaired = LibraryRepository._sort_chapters_by_number(chapters)

    assert [item["label"] for item in repaired] == [
        "序", "第1章", "第2章", "第3章", "第4章", "第5章",
        "第6章", "第7章", "第8章", "第9章", "第10章", "尾声",
    ]


def test_reader_repairs_large_order_with_one_duplicate_missing_pair() -> None:
    numbers = (
        list(range(2285, 2273, -1))
        + list(range(1, 637))
        + [638, 638]
        + list(range(639, 2274))
    )
    chapters = [{"id": 1, "label": "序", "title": "作品信息"}]
    chapters.extend(
        {
            "id": index + 2,
            "label": f"第{number}章",
            "title": f"源位置{index + 1}",
        }
        for index, number in enumerate(numbers)
    )

    repaired = LibraryRepository._sort_chapters_by_number(chapters)
    story_labels = [item["label"] for item in repaired[1:]]

    assert story_labels[:3] == ["第1章", "第2章", "第3章"]
    assert story_labels[635:640] == [
        "第636章", "第638章", "第638章", "第639章", "第640章",
    ]
    assert story_labels[-12:] == [
        f"第{number}章" for number in range(2274, 2286)
    ]


def test_reader_keeps_navigation_in_repaired_catalog_order(
    repository: LibraryRepository,
) -> None:
    with sqlite3.connect(repository.settings.catalog_path) as connection:
        source = Path(
            connection.execute(
                "SELECT output_path FROM books WHERE id=1"
            ).fetchone()[0]
        )
    numbers = [12, *range(1, 12)]
    source.write_text(
        "\n\n".join(
            f"第{number}章 标题{number}\n正文{number}" for number in numbers
        ),
        encoding="utf-8",
    )

    catalog = repository.reader_catalog(1)

    assert [item["label"] for item in catalog["chapters"]] == [
        f"第{number}章" for number in range(1, 13)
    ]
    assert repository.reader_chapter(1, 12)["next_id"] == 1
    assert repository.reader_chapter(1, 1)["previous_id"] == 12


def _add_light_novel(
    repository: LibraryRepository,
    catalog_id: int,
    dirname: str,
    volumes: list[tuple[str, str, int]],
) -> None:
    novel_root = repository.settings.books_root / "轻小说" / dirname
    novel_root.mkdir(parents=True)
    source = novel_root / "总文件.txt"
    source.write_text("轻小说正文入口\n", encoding="utf-8")
    for volume_name, chapter_folder, illustration_count in volumes:
        volume_root = novel_root / volume_name
        chapters = volume_root / chapter_folder
        illustrations = volume_root / "插画"
        chapters.mkdir(parents=True)
        illustrations.mkdir()
        (chapters / "0000001-100001-序章-0123456789.txt").write_text(
            f"{volume_name}正文", encoding="utf-8"
        )
        for index in range(illustration_count):
            (illustrations / f"illustration-{index + 1}.jpg").write_bytes(
                b"\xff\xd8\xff" + bytes([index + 1])
            )
    with sqlite3.connect(repository.settings.catalog_path) as connection:
        connection.execute(
            "INSERT INTO books VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                catalog_id,
                f"linovelib-{catalog_id}",
                dirname.split("__", 1)[0],
                "作者",
                "轻小说",
                f"https://www.linovelib.com/novel/{catalog_id}.html",
                "done",
                str(source),
                source.stat().st_size,
                None,
                None,
            ),
        )
        connection.commit()


def test_light_novel_irregular_single_volume_supports_deep_directories(
    repository: LibraryRepository,
) -> None:
    _add_light_novel(
        repository,
        2,
        "反派贵族的最强中立国家__作者__linovelib-5087",
        [("001-正文", "章节目录", 2)],
    )

    catalog = repository.reader_catalog(2)

    assert catalog["chapter_count"] == 1
    assert catalog["volumes"] == [
        {
            "id": 1,
            "title": "正文",
            "chapter_ids": [1],
            "illustration_count": 2,
            "cover_path": "",
            "illustration_paths": [
                "001-正文/插画/illustration-1.jpg",
                "001-正文/插画/illustration-2.jpg",
            ],
        }
    ]
    assert repository.reader_chapter(2, 1)["content"] == "001-正文正文"
    assert repository.illustration_path(2, "001-正文/插画/illustration-1.jpg").is_file()


def test_light_novel_multi_volume_keeps_each_deep_catalog_separate(
    repository: LibraryRepository,
) -> None:
    _add_light_novel(
        repository,
        3,
        "欢迎来到实力至上主义的教室__作者__linovelib-8",
        [("001-第一卷", "章节", 1), ("002-第二卷", "章节目录", 2)],
    )

    catalog = repository.reader_catalog(3)

    assert catalog["chapter_count"] == 2
    assert [volume["title"] for volume in catalog["volumes"]] == [
        "第一卷",
        "第二卷",
    ]
    assert [volume["chapter_ids"] for volume in catalog["volumes"]] == [
        [1],
        [2],
    ]
    assert [volume["illustration_count"] for volume in catalog["volumes"]] == [
        1,
        2,
    ]


def test_reader_chapter_count_does_not_serialize_chapter_rows(
    repository: LibraryRepository, monkeypatch
):
    monkeypatch.setattr(
        repository,
        "_reader_index",
        lambda *_args: {"chapter_count": 71, "chapters": object()},
    )

    assert repository.reader_chapter_count(1) == 71


def test_reader_removes_source_page_markers(repository: LibraryRepository):
    with sqlite3.connect(repository.settings.catalog_path) as conn:
        path = Path(
            conn.execute("SELECT output_path FROM books WHERE id=1").fetchone()[0]
        )
    path.write_text(
        "第一章 出发\n第(1/3)页\n第一段正文。\n第(1/3)页\n",
        encoding="utf-8",
    )
    chapter = repository.reader_chapter(1, 1)
    assert "第一段正文" in chapter["content"]
    assert "第(1/3)页" not in chapter["content"]


def test_unknown_sort_is_rejected(repository: LibraryRepository):
    with pytest.raises(InputError):
        repository.list_books(sort="random()")


def test_summary_removes_control_characters():
    assert _clean_summary("正常\u0000摘要\n下一行") == "正常摘要\n下一行"


def test_summary_never_exposes_local_media_paths():
    assert (
        _clean_summary(
            "[本地分卷封面：book/001/封面/cover.jpg]\n"
            "[本地插图：book/001/插图/image.jpg]\n"
            "正常简介"
        )
        == "正常简介"
    )


def test_path_escape_is_rejected(repository: LibraryRepository, tmp_path: Path):
    outside = tmp_path / "outside.txt"
    outside.write_text("第一章 越界\n内容", encoding="utf-8")
    with sqlite3.connect(repository.settings.catalog_path) as conn:
        conn.execute("UPDATE books SET output_path=? WHERE id=1", (str(outside),))
        conn.commit()
    with pytest.raises(UnsafePathError):
        repository.get_book(1)


def test_download_source_is_scoped_and_has_safe_filename(
    repository: LibraryRepository,
):
    source, filename = repository.download_source(1)
    assert source.is_file()
    assert source.is_relative_to(repository.settings.books_root)
    assert filename == "样本书 - 作者.txt"
    assert "/" not in filename
    assert "\\" not in filename


def test_download_filename_removes_header_and_path_characters():
    filename = LibraryRepository._download_filename(
        '危险/书名\\:*?"<>|\r\n',
        "作者/甲",
    )
    assert filename.endswith(".txt")
    assert not any(character in filename for character in '/\\:*?"<>|\r\n')


def test_public_metrics_are_anonymous_persistent_and_event_scoped(
    repository: LibraryRepository,
):
    visitor = "d9428888-122b-4ed3-8f18-1d6f0f585c8d"
    first = repository.record_public_metric(1, visitor, "read")
    repeated = repository.record_public_metric(1, visitor, "read")
    download = repository.record_public_metric(1, visitor, "download")

    assert first["counted"] is True
    assert repeated["counted"] is False
    assert download["counted"] is True
    assert repository.public_metrics(1) == {
        "public_id": repository.list_books()["items"][0]["public_id"],
        "read_count": 1,
        "download_count": 1,
        "recommend_count": 0,
        "favorite_count": 0,
    }
    with sqlite3.connect(repository._sqlite_metrics_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(book_public_metric_visitors)"
            )
        }
        stored = connection.execute(
            "SELECT visitor_hash FROM book_public_metric_visitors"
        ).fetchone()[0]
    assert columns == {
        "catalog_id",
        "visitor_hash",
        "first_read_at",
        "first_download_at",
        "first_recommend_at",
        "first_favorite_at",
        "created_at",
        "updated_at",
    }
    assert isinstance(stored, bytes)
    assert len(stored) == 32
    assert visitor.encode() not in repository._sqlite_metrics_path.read_bytes()
    assert repository.settings.catalog_path.read_bytes().find(visitor.encode()) == -1


def test_account_favorite_count_is_exact_and_anonymous_favorite_is_rejected(
    repository: LibraryRepository,
):
    public_id = repository.list_books()["items"][0]["public_id"]
    assert repository.set_favorite_count(public_id, 3)["favorite_count"] == 3
    assert repository.set_favorite_count(public_id, 1)["favorite_count"] == 1
    assert repository.public_metrics(public_id)["favorite_count"] == 1
    with pytest.raises(InputError, match="未知统计事件"):
        repository.record_public_metric(
            public_id,
            "2f1c8f98-0db7-4e1c-9a42-5926ffad08a1",
            "favorite",
        )


def test_duplicate_public_metric_is_concurrency_safe(
    repository: LibraryRepository,
):
    visitor = "2f1c8f98-0db7-4e1c-9a42-5926ffad08a1"
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda _index: repository.record_public_metric(1, visitor, "read"),
                range(16),
            )
        )
    assert sum(result["counted"] for result in results) == 1
    assert repository.public_metrics(1)["read_count"] == 1


def test_public_metric_rejects_invalid_event_and_unreadable_book(
    repository: LibraryRepository,
):
    with pytest.raises(InputError, match="未知统计事件"):
        repository.record_public_metric(
            1, "2f1c8f98-0db7-4e1c-9a42-5926ffad08a1", "open"
        )
    with pytest.raises(InputError, match="无效匿名访客"):
        repository.record_public_metric(1, "not-a-uuid", "read")
    with pytest.raises(Exception, match="作品不存在"):
        repository.record_public_metric(
            999, "2f1c8f98-0db7-4e1c-9a42-5926ffad08a1", "read"
        )


def test_catalog_source_root_maps_only_to_public_mount(
    repository: LibraryRepository, tmp_path: Path
):
    source_root = tmp_path / "original"
    mounted_root = repository.settings.library_root
    mapped_settings = Settings(
        library_root=mounted_root,
        state_root=(tmp_path / "mapped-state").resolve(),
        allowed_hosts=("testserver",),
        catalog_source_root=source_root.resolve(),
    )
    current = sqlite3.connect(repository.settings.catalog_path)
    output_path = current.execute(
        "SELECT output_path FROM books WHERE id=1"
    ).fetchone()[0]
    current.execute(
        "UPDATE books SET output_path=? WHERE id=1",
        (str(source_root / "书籍" / "科幻灵异" / Path(output_path).name),),
    )
    current.commit()
    current.close()
    mapped = LibraryRepository(mapped_settings)
    assert mapped.get_book(1)["title"] == "样本书"


def test_deconstruction_is_allowlisted(repository: LibraryRepository):
    items = repository.list_deconstructions()
    assert items[0]["title"] == "样本书"
    assert items[0]["cover_url"] == OOHSTORY_DEFAULT_COVER_URL
    assert items[0]["progress_percent"] == 20
    detail = repository.get_deconstruction("样本书")
    assert detail["documents"][0]["filename"] == "概要.md"
    assert "结构完整" in detail["documents"][0]["content"]
    book = repository.get_book(1)
    assert book["deconstruction_slug"] == "样本书"


def test_deconstruction_uses_real_golden_three_and_natural_chapter_order(
    repository: LibraryRepository,
):
    root = repository.settings.deconstruction_root / "样本书"
    chapters = root / "章节"
    chapters.mkdir()
    for number in (1, 2, 3):
        (chapters / f"第{number}章_深度拆解.md").write_text(
            f"第{number}章真正深度拆解", encoding="utf-8"
        )
    for number in (10, 2, 1):
        (chapters / f"第{number}章_摘要.md").write_text(
            f"第{number}章摘要", encoding="utf-8"
        )
    (root / "快速预览.md").write_text("错误的快速预览", encoding="utf-8")
    (root / "_submission.json").write_text(
        '{"contributor_username":"贡献用户"}', encoding="utf-8"
    )

    detail = repository.get_deconstruction("样本书")
    golden = detail["documents"][0]
    assert golden["filename"] == "黄金三章"
    assert golden["subdirectory"] == "章节"
    assert [item["filename"] for item in golden["items"]] == [
        "第1章_深度拆解.md",
        "第2章_深度拆解.md",
        "第3章_深度拆解.md",
    ]
    first_golden = repository.get_deconstruction_file(
        "样本书", "章节/第1章_深度拆解.md"
    )
    assert first_golden["content"] == "第1章真正深度拆解"
    assert "错误的快速预览" not in str(golden)
    assert detail["contributor_username"] == "贡献用户"
    chapter_tab = next(
        item for item in detail["subdirectories"] if item["name"] == "章节"
    )
    summary_labels = [item["label"] for item in chapter_tab["items"]]
    assert summary_labels == ["第1章_摘要", "第2章_摘要", "第10章_摘要"]


def test_deconstruction_symlink_cannot_escape_public_root(
    repository: LibraryRepository, tmp_path: Path
):
    deconstruction = repository.settings.deconstruction_root / "样本书"
    outside = tmp_path / "outside.md"
    outside.write_text("PRIVATE SERVER CONTENT", encoding="utf-8")
    (deconstruction / "文风.md").symlink_to(outside)

    items = repository.list_deconstructions()
    assert all(document["filename"] != "文风.md" for document in items[0]["documents"])
    detail = repository.get_deconstruction("样本书")
    assert "PRIVATE SERVER CONTENT" not in str(detail)


def test_cover_rejects_disguised_non_image(
    repository: LibraryRepository,
):
    cover = repository.settings.cover_root / "malicious.jpg"
    cover.write_text("<?php echo 'webshell'; ?>", encoding="utf-8")
    with sqlite3.connect(repository.settings.cover_index_path) as conn:
        conn.execute("INSERT INTO covers VALUES (1, 'malicious.jpg', 'done')")
        conn.commit()

    with pytest.raises(UnsafePathError, match="内容无效"):
        repository.cover_path(1)


def test_cover_rejects_symlink(repository: LibraryRepository, tmp_path: Path):
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"\xff\xd8\xff" + b"not-public")
    cover = repository.settings.cover_root / "linked.jpg"
    cover.symlink_to(outside)
    with sqlite3.connect(repository.settings.cover_index_path) as conn:
        conn.execute("INSERT INTO covers VALUES (1, 'linked.jpg', 'done')")
        conn.commit()

    with pytest.raises(UnsafePathError, match="路径无效"):
        repository.cover_path(1)


def test_progress_prefers_stage_two_scope(tmp_path: Path):
    progress = tmp_path / "_progress.md"
    progress.write_text(
        """
- 章节数：715
| Stage | 名称 | 状态 |
| 2 | 逐章摘要 | ✅ 完成（300/300）|
| 偏移 | 500/500 | 其他边界 |
""",
        encoding="utf-8",
    )
    parsed = LibraryRepository._read_progress(progress)
    assert parsed["progress"] == "300/300"
    assert parsed["progress_percent"] == 100


@pytest.mark.parametrize(
    ("stage_two", "trailing_noise", "expected"),
    [
        (
            "| Stage 2 逐章摘要 | completed | 1098/1098 |",
            "| source_number_recovery | 第265/360/393/394/870/1019/1097章 |",
            "1098/1098",
        ),
        (
            "| Stage 2 逐章摘要 | completed | 1506/1506 |",
            "| 汇总报告与概要 | Stage 5 | 通过 | 内部链接15/15 |",
            "1506/1506",
        ),
    ],
)
def test_progress_never_uses_non_stage_two_fractions(
    tmp_path: Path,
    stage_two: str,
    trailing_noise: str,
    expected: str,
):
    progress = tmp_path / "_progress.md"
    progress.write_text(
        f"## 管道进度\n{stage_two}\n## 审计\n{trailing_noise}\n",
        encoding="utf-8",
    )

    parsed = LibraryRepository._read_progress(progress)

    assert parsed["progress"] == expected
    assert parsed["progress_percent"] == 100


def test_progress_without_explicit_stage_two_ratio_is_unknown(tmp_path: Path):
    progress = tmp_path / "_progress.md"
    progress.write_text(
        "异常章号：第870/1019章\n内部链接15/15\n",
        encoding="utf-8",
    )

    parsed = LibraryRepository._read_progress(progress)

    assert parsed["progress"] == ""
    assert parsed["progress_percent"] == 0


def test_oversized_unindexed_source_is_rejected(repository: LibraryRepository):
    restrictive = Settings(
        library_root=repository.settings.library_root,
        state_root=repository.settings.state_root.parent / "small-state",
        allowed_hosts=("testserver",),
        max_index_source_bytes=8,
    )
    with pytest.raises(InputError, match="尚未预生成"):
        LibraryRepository(restrictive).reader_catalog(1)


def test_mysql_public_catalog_enforces_manual_publication_state_everywhere() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "app" / "mysql_catalog.py"
    ).read_text(encoding="utf-8")
    assert source.count("is_active=1") == 20
    assert source.count("is_published=1") == source.count("is_active=1")
    assert "FROM public_catalog_facets" in source
    assert (
        "FROM catalog_facets\n                    WHERE body_available=1" not in source
    )
