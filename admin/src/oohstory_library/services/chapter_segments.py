"""Shared chapter-title and segmented-cache helpers for crawler providers."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable


_CHAPTER_LABEL = re.compile(
    r"^(第\s*[0-9零〇一二三四五六七八九十百千万两]+\s*[章节回话])"
    r"\s*[-—_:：·、]?\s*(.*)$"
)
_SPECIAL_HEADING = re.compile(
    r"^(?:序章|楔子|引子|前言|终章|尾声|后记|番外(?:篇|卷|章)?)"
)
_UNSAFE_FILENAME = str.maketrans(
    {
        "/": "／",
        "\\": "＼",
        ":": "：",
        "*": "＊",
        "?": "？",
        '"': "＂",
        "<": "＜",
        ">": "＞",
        "|": "｜",
    }
)


def clean_chapter_title(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip(" \t\r\n-—_:：·、")


def _split_heading(value: Any) -> tuple[str, str]:
    title = clean_chapter_title(value)
    match = _CHAPTER_LABEL.match(title)
    if not match:
        return "", title
    label = re.sub(r"\s+", "", match.group(1))
    subtitle = clean_chapter_title(match.group(2))
    return label, subtitle


def looks_like_chapter_heading(value: Any) -> bool:
    title = clean_chapter_title(value)
    return bool(_CHAPTER_LABEL.match(title) or _SPECIAL_HEADING.match(title))


def chapter_title_has_name(value: Any) -> bool:
    label, subtitle = _split_heading(value)
    return bool(subtitle if label else clean_chapter_title(value))


def prefer_chapter_title(catalog_title: Any, candidates: Iterable[Any]) -> str:
    """Prefer the richest chapter-page heading without accepting the book title."""

    catalog = clean_chapter_title(catalog_title)
    catalog_label, _ = _split_heading(catalog)
    accepted = [catalog] if catalog else []
    for value in candidates:
        candidate = clean_chapter_title(value)
        if not candidate or not looks_like_chapter_heading(candidate):
            continue
        candidate_label, _ = _split_heading(candidate)
        if catalog_label and candidate_label and candidate_label != catalog_label:
            continue
        accepted.append(candidate)
    if not accepted:
        return catalog

    def score(value: str) -> tuple[int, int]:
        _, subtitle = _split_heading(value)
        return (1 if subtitle else 0, len(subtitle or value))

    return max(accepted, key=score)


def strip_duplicate_heading(content: str, heading: str) -> str:
    """Remove one leading heading after its richer value has been captured."""

    lines = content.splitlines()
    first = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first is None:
        return ""
    candidate = clean_chapter_title(lines[first])
    heading_clean = clean_chapter_title(heading)
    candidate_label, _ = _split_heading(candidate)
    heading_label, _ = _split_heading(heading_clean)
    if candidate == heading_clean or (
        candidate_label and heading_label and candidate_label == heading_label
    ):
        lines.pop(first)
    return "\n".join(lines).strip()


def chapter_segment_filename(title: Any, ordinal: int) -> str:
    """Return a safe ``第xx章-章节名.txt`` cache filename."""

    clean = clean_chapter_title(title)
    label, subtitle = _split_heading(clean)
    if not label:
        label = f"第{max(int(ordinal), 1)}章"
        subtitle = clean
    subtitle = subtitle.translate(_UNSAFE_FILENAME)
    subtitle = re.sub(r"[\x00-\x1f\x7f]", "", subtitle).strip(" .")
    if len(subtitle) > 140:
        subtitle = subtitle[:140].rstrip(" .")
    return f"{label}-{subtitle}.txt" if subtitle else f"{label}.txt"


def chapter_title_from_segment(path: Path, fallback: Any = "") -> str:
    stem = path.stem
    match = _CHAPTER_LABEL.match(stem)
    if not match:
        return clean_chapter_title(fallback)
    label = re.sub(r"\s+", "", match.group(1))
    subtitle = clean_chapter_title(match.group(2))
    return f"{label} {subtitle}".strip()


def load_chapter_manifest(cache_dir: Path) -> dict[str, str]:
    path = cache_dir / ".chapters.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in payload.items():
        filename = str(value or "")
        if (
            isinstance(key, str)
            and key
            and filename == Path(filename).name
            and filename.endswith(".txt")
        ):
            result[key] = filename
    return result


def write_chapter_manifest(cache_dir: Path, entries: dict[str, str]) -> None:
    path = cache_dir / ".chapters.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(entries, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)
