from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


LANG_SUFFIX = {"zh": ".md", "en": ".en.md"}


def normalize_lang(lang: Optional[str]) -> str:
    return "en" if str(lang or "zh").lower() == "en" else "zh"


def language_suffix(lang: Optional[str]) -> str:
    return LANG_SUFFIX[normalize_lang(lang)]


def counterpart_lang(lang: Optional[str]) -> str:
    return "en" if normalize_lang(lang) == "zh" else "zh"


def is_lang_file(path: Path, lang: Optional[str]) -> bool:
    name = path.name
    if normalize_lang(lang) == "zh":
        return name.endswith('.md') and not name.endswith('.en.md')
    return name.endswith('.en.md')


def strip_lang_suffix(name: str) -> str:
    return re.sub(r"\.en\.md$", "", name)


def ensure_lang_path(path: Path, lang: Optional[str]) -> Path:
    lang = normalize_lang(lang)
    if lang == 'zh':
        if path.name.endswith('.en.md'):
            return path.with_name(path.name[:-6] + '.md')
        return path if path.name.endswith('.md') else path.with_name(path.name + '.md')
    if path.name.endswith('.en.md'):
        return path
    if path.name.endswith('.md'):
        return path.with_name(path.name[:-3] + '.en.md')
    return path.with_name(path.name + '.en.md')


def chapter_file_name(chapter_id: int, title: str, lang: Optional[str]) -> str:
    safe_title = re.sub(r"[\\/:*?\"<>|]+", " ", (title or "").strip())
    safe_title = re.sub(r"\s+", " ", safe_title).strip(" .") or f"第{chapter_id}章"
    suffix = '.en.md' if normalize_lang(lang) == 'en' else '.md'
    return f"第{chapter_id}章-{safe_title}{suffix}"


def outline_file_name(volume: int, title: str, lang: Optional[str]) -> str:
    safe_title = re.sub(r"[\\/:*?\"<>|]+", " ", (title or "").strip())
    safe_title = re.sub(r"\s+", " ", safe_title).strip(" .") or f"第{volume}卷-详细大纲"
    suffix = '.en.md' if normalize_lang(lang) == 'en' else '.md'
    return f"{safe_title}{suffix}"
