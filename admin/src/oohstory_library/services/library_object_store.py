"""Filesystem/NAS storage for immutable cover and archive objects."""

from __future__ import annotations

from .error_boundaries import RECOVERABLE_OPERATION_ERRORS

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True)
class StoredObject:
    object_key: str
    path: Path
    bytes: int
    sha256: str


class NasObjectStore:
    """Store immutable objects below one NAS root.

    A caller writes to a temporary file on the same filesystem and atomically
    replaces the destination. Existing objects with the same digest are reused.
    """

    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()

    @staticmethod
    def object_key(
        *,
        asset_type: str,
        sha256: str,
        extension: str,
    ) -> str:
        digest = sha256.lower().strip()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("sha256 must be a 64-character hexadecimal digest")
        safe_type = asset_type.strip().lower()
        if safe_type == "body":
            raise ValueError("正文对象直接存放在电子书库根目录的书籍/目录下")
        if safe_type not in {"cover", "archive"}:
            raise ValueError("unsupported object asset type")
        suffix = extension.lower().strip().lstrip(".")
        if not suffix or not suffix.isalnum() or len(suffix) > 10:
            raise ValueError("invalid object extension")
        return f"{safe_type}/{digest[:2]}/{digest[2:4]}/{digest}.{suffix}"

    def resolve(self, object_key: str) -> Path:
        relative = Path(object_key)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("object key escapes the configured root")
        resolved = (self.root / relative).resolve()
        resolved.relative_to(self.root)
        return resolved

    def put_file(
        self,
        source: Path,
        *,
        asset_type: str,
        extension: str | None = None,
    ) -> StoredObject:
        source = source.expanduser().resolve()
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as reader:
            for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        sha256 = digest.hexdigest()
        object_key = self.object_key(
            asset_type=asset_type,
            sha256=sha256,
            extension=extension or source.suffix or "bin",
        )
        destination = self.resolve(object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.is_file() or destination.stat().st_size != size:
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
            )
            try:
                with os.fdopen(file_descriptor, "wb") as writer:
                    with source.open("rb") as reader:
                        shutil.copyfileobj(reader, writer, 1024 * 1024)
                    writer.flush()
                    os.fsync(writer.fileno())
                os.replace(temporary_name, destination)
            except RECOVERABLE_OPERATION_ERRORS:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
                raise
        return StoredObject(
            object_key=object_key,
            path=destination,
            bytes=size,
            sha256=sha256,
        )

    def open(self, object_key: str) -> BinaryIO:
        return self.resolve(object_key).open("rb")

    def health(self) -> dict[str, object]:
        self.root.mkdir(parents=True, exist_ok=True)
        stat = shutil.disk_usage(self.root)
        return {
            "ok": os.access(self.root, os.R_OK | os.W_OK | os.X_OK),
            "root": str(self.root),
            "free_bytes": stat.free,
            "total_bytes": stat.total,
        }
