"""Bounded, recoverable filesystem staging for OOHStory catalog deletion."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


MAX_CATALOG_DELETE_BOOKS = 100


def normalize_catalog_delete_request(
    catalog_ids: Iterable[Any],
    confirmation: str,
) -> tuple[list[int], str]:
    normalized: set[int] = set()
    for value in catalog_ids:
        if isinstance(value, bool):
            raise ValueError("书目 ID 必须是正整数")
        try:
            catalog_id = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("书目 ID 必须是正整数") from exc
        if catalog_id <= 0:
            raise ValueError("书目 ID 必须是正整数")
        normalized.add(catalog_id)
    ids = sorted(normalized)
    if not ids:
        raise ValueError("请至少明确选择一本小说")
    if len(ids) > MAX_CATALOG_DELETE_BOOKS:
        raise ValueError(
            f"单次最多删除 {MAX_CATALOG_DELETE_BOOKS} 本小说，禁止筛选全量删除"
        )
    expected = f"确认删除{len(ids)}本书"
    if str(confirmation or "").strip() != expected:
        raise ValueError(f"请输入确认短语：{expected}")
    return ids, expected


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


@dataclass
class CatalogDeletionArchive:
    """Move exact catalog assets into a batch archive before database delete."""

    archive_root: Path
    allowed_roots: tuple[Path, ...]
    batch_id: str = field(
        default_factory=lambda: (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid.uuid4().hex[:10]
        )
    )

    def __post_init__(self) -> None:
        self.archive_root = self.archive_root.expanduser().resolve()
        self.allowed_roots = tuple(
            root.expanduser().resolve() for root in self.allowed_roots
        )
        self.batch_root = self.archive_root / self.batch_id
        self.assets_root = self.batch_root / "assets"
        self.entries: list[dict[str, Any]] = []

    def _root_and_relative(self, candidate: Path) -> tuple[int, Path, Path]:
        absolute = candidate.expanduser().absolute()
        for index, root in enumerate(self.allowed_roots):
            if not _within(absolute, root):
                continue
            resolved = absolute.resolve(strict=False)
            if resolved == root:
                raise ValueError("拒绝归档电子书库根目录")
            return index, root, resolved.relative_to(root)
        raise ValueError("待删除资产越过电子书库白名单根目录")

    def stage(self, paths: Iterable[Path]) -> list[dict[str, Any]]:
        resolved: list[tuple[int, Path, Path, Path]] = []
        seen: set[str] = set()
        for raw in paths:
            candidate = Path(raw).expanduser().absolute()
            if not candidate.exists() and not candidate.is_symlink():
                continue
            root_index, root, relative = self._root_and_relative(candidate)
            key = str(candidate.resolve(strict=False))
            if key in seen:
                continue
            seen.add(key)
            resolved.append((root_index, root, relative, candidate))

        selected_paths = [item[3].resolve(strict=False) for item in resolved]
        filtered: list[tuple[int, Path, Path, Path]] = []
        for item in resolved:
            candidate = item[3].resolve(strict=False)
            if any(
                parent != candidate and _within(candidate, parent)
                for parent in selected_paths
            ):
                continue
            filtered.append(item)

        self.assets_root.mkdir(parents=True, exist_ok=True)
        for root_index, root, relative, source in filtered:
            destination = self.assets_root / f"root-{root_index}" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                raise RuntimeError("归档目标发生冲突")
            shutil.move(str(source), str(destination))
            self.entries.append(
                {
                    "source": str(source),
                    "archive": str(destination),
                    "root": str(root),
                    "relative": str(relative),
                    "kind": "directory" if destination.is_dir() else "file",
                }
            )
        return list(self.entries)

    def write_manifest(self, payload: dict[str, Any]) -> Path:
        self.batch_root.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 1,
            "batch_id": self.batch_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "entries": self.entries,
            **payload,
        }
        target = self.batch_root / "manifest.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(temporary, target)
        return target

    def restore(self) -> None:
        for entry in reversed(self.entries):
            source = Path(entry["source"])
            archived = Path(entry["archive"])
            if not archived.exists() and not archived.is_symlink():
                continue
            if source.exists() or source.is_symlink():
                if source.is_dir() and not source.is_symlink():
                    shutil.rmtree(source)
                else:
                    source.unlink()
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(archived), str(source))
