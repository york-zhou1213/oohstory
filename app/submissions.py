"""Validation contracts for reader-contributed archives and manuscripts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


SHORT_REQUIRED = (
    "_meta.json",
    "拆文报告.md",
    "情节节点.md",
    "写作手法.md",
    "原文/原文.txt",
)
LONG_REQUIRED = (
    "_progress.md",
    "概要.md",
    "原文/原文.txt",
)
LONG_REPORT_ALTERNATIVES = ("快速预览.md", "拆文报告.md")
LONG_OPTIONAL_GROUPS: dict[str, tuple[str, ...]] = {
    "章节摘要": ("章节",),
    "角色资料": ("角色",),
    "剧情分析": ("剧情",),
    "设定资料": ("设定",),
}


def _strip_single_root(names: list[str]) -> list[str]:
    parts = [Path(name).parts for name in names if name and not name.endswith("/")]
    if not parts:
        return []
    first = {part[0] for part in parts if part}
    if len(first) == 1 and all(len(part) > 1 for part in parts):
        return [Path(*part[1:]).as_posix() for part in parts]
    return [Path(*part).as_posix() for part in parts]


def inspect_deconstruction_structure(names: list[str]) -> dict[str, Any]:
    """Classify an oh-story-claudecode ZIP and explain every missing artifact."""
    raw_parts = [Path(name).parts for name in names if name and not name.endswith("/")]
    root_names = {parts[0] for parts in raw_parts if parts}
    normalized_root = next(iter(root_names)) if len(root_names) == 1 and all(len(parts) > 1 for parts in raw_parts) else ""
    normalized = _strip_single_root(names)
    existing = set(normalized)
    short_missing = [item for item in SHORT_REQUIRED if item not in existing]
    long_missing = [item for item in LONG_REQUIRED if item not in existing]
    if not any(item in existing for item in LONG_REPORT_ALTERNATIVES):
        long_missing.append("快速预览.md 或 拆文报告.md")
    optional_missing: list[str] = []
    for label, prefixes in LONG_OPTIONAL_GROUPS.items():
        if not any(
            any(name.startswith(f"{prefix}/") and name.casefold().endswith(".md") for prefix in prefixes)
            for name in existing
        ):
            optional_missing.append(label)
    if not short_missing:
        profile = "short"
        missing = []
        optional_missing = []
    elif len(long_missing) < len(short_missing):
        profile = "long"
        missing = long_missing
    else:
        profile = "short"
        missing = short_missing
        optional_missing = []
    return {
        "profile": profile,
        "valid": not missing,
        "missing_files": missing,
        "optional_missing": optional_missing,
        "file_count": len(existing),
        "files": sorted(existing)[:500],
        "contract": "oh-story-claudecode-v1",
        "normalized_root": normalized_root,
    }


class NovelMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    author: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=40)
    serialization_status: str
    summary: str = Field(min_length=20, max_length=4000)
    source: str = Field(min_length=3, max_length=500)
    authorization: str = Field(min_length=10, max_length=2000)

    @field_validator("title", "author", "category", "source", "authorization", "summary")
    @classmethod
    def clean_text(cls, value: str) -> str:
        cleaned = " ".join(str(value or "").replace("\x00", " ").split())
        if not cleaned:
            raise ValueError("必填字段不能为空")
        return cleaned

    @field_validator("serialization_status")
    @classmethod
    def valid_serialization(cls, value: str) -> str:
        normalized = str(value or "").strip().casefold()
        if normalized not in {"ongoing", "finished"}:
            raise ValueError("连载状态无效")
        return normalized


def parse_strict_review(value: str) -> dict[str, Any]:
    """Accept only the bounded JSON object emitted by the configured AI reviewer."""
    try:
        payload = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("审核未返回有效 JSON") from exc
    if not isinstance(payload, dict) or set(payload) - {
        "decision", "reason", "missing_files", "issues"
    }:
        raise ValueError("审核 JSON 字段不符合契约")
    decision = str(payload.get("decision") or "").casefold()
    if decision not in {"approve", "reject"}:
        raise ValueError("审核决策无效")
    reason = str(payload.get("reason") or "").strip()
    if not reason or len(reason) > 2000:
        raise ValueError("审核理由无效")
    result = {"decision": decision, "reason": reason}
    for key in ("missing_files", "issues"):
        values = payload.get(key) or []
        if not isinstance(values, list) or len(values) > 100:
            raise ValueError(f"审核 {key} 无效")
        result[key] = [str(item).strip()[:300] for item in values if str(item).strip()]
    return result


def safe_slug(value: str) -> str:
    cleaned = re.sub(r"[^\w\u3400-\u9fff-]+", "-", value, flags=re.UNICODE).strip("-_")
    return cleaned[:80] or "submission"
