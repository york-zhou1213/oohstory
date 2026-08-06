"""Shared catalog identity and completeness helpers.

The crawler, maintenance job, and API must agree on what counts as the same
work.  Source ids identify remote records; normalized title + author identify
the logical work across duplicate source records and providers.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Mapping


CHAPTER_HEADING = re.compile(
    r"(?m)^\s*(?:正文\s*)?第\s*"
    r"([0-9零一二三四五六七八九十百千万两〇○]+)\s*"
    r"[章节卷回部篇集]"
)
TRAILING_LABELS = re.compile(
    r"(?:txt|全文|全集|全本|完本|精校版|校对版|电子书|小说)$",
    re.IGNORECASE,
)
MALFORMED_TRAILING_TYPE_LABEL = re.compile(r"\s*\[[^\]\r\n]*$")
IDENTITY_PUNCTUATION = re.compile(
    r"[《》〈〉「」『』【】\[\]()（）·•:：,，.。!！?？\s_\-—]+"
)


def normalize_identity_part(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = IDENTITY_PUNCTUATION.sub("", text)
    return TRAILING_LABELS.sub("", text).strip()


def normalize_catalog_title(value: Any) -> str:
    """Drop only a trailing, unclosed ``[type`` label from a book title."""

    raw_text = re.sub(
        r"\s+",
        " ",
        str(value or ""),
    ).strip()
    cleaned = MALFORMED_TRAILING_TYPE_LABEL.sub("", raw_text).rstrip()
    # Display titles retain the source punctuation.  Cross-source dedupe and
    # identity comparison already use ``normalize_identity_part`` (NFKC +
    # punctuation folding); applying NFKC here would visibly rewrite Chinese
    # ``：，？`` into ASCII ``:,?`` even though this helper promises to remove
    # only a malformed trailing type label.
    return cleaned or raw_text


def book_identity(title: Any, author: Any) -> tuple[str, str]:
    return normalize_identity_part(title), normalize_identity_part(author)


def source_library_id(source_id: Any) -> str:
    return (
        "fanqie"
        if str(source_id or "").strip().lower().startswith("fanqie-")
        else "local"
    )


def inspect_book_text(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    headings = CHAPTER_HEADING.findall(text)
    replacement_count = text.count("\ufffd")
    mojibake_count = sum(text.count(token) for token in ("锟斤拷", "Ã", "Â"))
    cjk_count = sum("\u3400" <= char <= "\u9fff" for char in text)
    visible_chars = sum(not char.isspace() for char in text)
    quality_penalty = replacement_count * 100 + mojibake_count * 20
    return {
        "chapter_count": len(headings),
        "text_chars": len(text),
        "visible_chars": visible_chars,
        "cjk_chars": cjk_count,
        "quality_penalty": quality_penalty,
    }


def preference_score(
    row: Mapping[str, Any],
    metrics: Mapping[str, Any],
    path: Path,
) -> tuple[int, int, int, int, int]:
    """Prefer the most complete, clean, and most recently stored edition."""

    return (
        int(metrics.get("chapter_count") or 0),
        -int(metrics.get("quality_penalty") or 0),
        int(metrics.get("visible_chars") or 0),
        int(path.stat().st_mtime_ns),
        int(row.get("id") or 0),
    )
