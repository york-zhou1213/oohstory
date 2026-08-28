#!/usr/bin/env python3
"""Create and verify the unsigned OOHStory Windows portable preview."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable


ARCHIVE_NAME = "OohStory-Windows-x64-preview.zip"
MANIFEST_NAME = "OohStory-Windows-x64-preview.manifest.json"
PACKAGE_ROOT = "OohStory-Windows-x64-preview"
BUILD_EXECUTABLE = "oohstory.exe"
PACKAGE_EXECUTABLE = "OohStory.exe"
REQUIRED_BUNDLE_PATHS = (
    "oohstory.exe",
    "flutter_windows.dll",
    "data/icudtl.dat",
)
SHA_RE = re.compile(r"[0-9a-f]{40}")
RUN_ID_RE = re.compile(r"[1-9][0-9]*")
BUFFER_SIZE = 1024 * 1024


class PreviewError(ValueError):
    """Raised when preview input or output violates the portable contract."""


def _hash_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(BUFFER_SIZE), b""):
        digest.update(block)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return _hash_stream(stream)


def _safe_archive_path(raw: str) -> PurePosixPath:
    if "\x00" in raw or "\\" in raw:
        raise PreviewError(f"unsafe archive entry: {raw}")
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} or ":" in part for part in path.parts)
    ):
        raise PreviewError(f"unsafe archive entry: {raw}")
    return path


def _bundle_files(bundle: Path) -> list[tuple[Path, PurePosixPath]]:
    if not bundle.is_dir() or bundle.is_symlink() or (hasattr(bundle, "is_junction") and bundle.is_junction()):
        raise PreviewError(f"bundle must be a real directory: {bundle}")

    files: list[tuple[Path, PurePosixPath]] = []
    seen: set[str] = set()
    for source in sorted(bundle.rglob("*"), key=lambda item: item.relative_to(bundle).as_posix().casefold()):
        if source.is_symlink() or (hasattr(source, "is_junction") and source.is_junction()):
            raise PreviewError(f"bundle contains a symbolic link: {source.relative_to(bundle)}")
        if source.is_dir():
            continue
        if not source.is_file():
            raise PreviewError(f"bundle contains a non-regular file: {source.relative_to(bundle)}")
        relative = PurePosixPath(source.relative_to(bundle).as_posix())
        _safe_archive_path(relative.as_posix())
        identity = relative.as_posix().casefold()
        if identity in seen:
            raise PreviewError(f"bundle has a case-insensitive duplicate path: {relative}")
        seen.add(identity)
        files.append((source, relative))

    available = {relative.as_posix().casefold() for _, relative in files}
    missing = [path for path in REQUIRED_BUNDLE_PATHS if path.casefold() not in available]
    if missing:
        raise PreviewError(f"bundle is missing required paths: {', '.join(missing)}")
    if not any(path.startswith("data/flutter_assets/") for path in available):
        raise PreviewError("bundle has no Flutter assets")
    return files


def _zip_info(path: PurePosixPath) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path.as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.flag_bits |= 0x800
    return info


def _manifest_file_record(path: str, sha256: str, size_bytes: int) -> dict[str, object]:
    return {"path": path, "sha256": sha256, "size_bytes": size_bytes}


def create_preview(
    bundle: Path,
    output_dir: Path,
    source_commit: str,
    workflow_run_id: str,
    windows_tree: str,
) -> tuple[Path, Path]:
    if not SHA_RE.fullmatch(source_commit):
        raise PreviewError("source commit must be a lowercase 40-character Git SHA")
    if not SHA_RE.fullmatch(windows_tree):
        raise PreviewError("Windows tree must be a lowercase 40-character Git SHA")
    if not RUN_ID_RE.fullmatch(workflow_run_id):
        raise PreviewError("workflow run ID must be a positive decimal integer")

    files = _bundle_files(bundle)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / ARCHIVE_NAME
    manifest_path = output_dir / MANIFEST_NAME
    records: list[dict[str, object]] = []
    executable_record: dict[str, object] | None = None

    with zipfile.ZipFile(archive_path, "w", allowZip64=True) as archive:
        for source, relative in files:
            packaged_relative = PurePosixPath(PACKAGE_EXECUTABLE) if relative.as_posix().casefold() == BUILD_EXECUTABLE else relative
            packaged_path = PurePosixPath(PACKAGE_ROOT) / packaged_relative
            digest = sha256_file(source)
            size = source.stat().st_size
            record = _manifest_file_record(packaged_path.as_posix(), digest, size)
            records.append(record)
            if packaged_relative.as_posix() == PACKAGE_EXECUTABLE:
                executable_record = record
            with archive.open(_zip_info(packaged_path), "w", force_zip64=size >= 2**31) as target:
                with source.open("rb") as source_stream:
                    shutil.copyfileobj(source_stream, target, length=BUFFER_SIZE)

    if executable_record is None:
        raise PreviewError("packaged executable was not created")

    manifest = {
        "archive": {
            "name": ARCHIVE_NAME,
            "sha256": sha256_file(archive_path),
            "size_bytes": archive_path.stat().st_size,
        },
        "executable": executable_record,
        "files": records,
        "package_root": PACKAGE_ROOT,
        "schema_version": 1,
        "source_commit": source_commit,
        "windows_tree": windows_tree,
        "workflow_run_id": workflow_run_id,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    verify_preview(archive_path, manifest_path, source_commit, workflow_run_id, windows_tree)
    return archive_path, manifest_path


def _read_manifest(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PreviewError(f"cannot read manifest: {error}") from error
    if not isinstance(raw, dict):
        raise PreviewError("manifest root must be an object")
    return raw


def _expect_manifest_file(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PreviewError(f"manifest {label} must be an object")
    path = value.get("path")
    digest = value.get("sha256")
    size = value.get("size_bytes")
    if not isinstance(path, str) or not re.fullmatch(r"[0-9a-f]{64}", str(digest)) or not isinstance(size, int) or size < 0:
        raise PreviewError(f"manifest {label} has invalid fields")
    safe_path = _safe_archive_path(path)
    if safe_path.parts[0] != PACKAGE_ROOT:
        raise PreviewError(f"manifest {label} is outside the package root")
    return value


def verify_preview(
    archive_path: Path,
    manifest_path: Path,
    source_commit: str,
    workflow_run_id: str,
    windows_tree: str,
) -> None:
    manifest = _read_manifest(manifest_path)
    if manifest.get("schema_version") != 1:
        raise PreviewError("unsupported manifest schema")
    expected_scalars = {
        "package_root": PACKAGE_ROOT,
        "source_commit": source_commit,
        "workflow_run_id": workflow_run_id,
        "windows_tree": windows_tree,
    }
    for field, expected in expected_scalars.items():
        if manifest.get(field) != expected:
            raise PreviewError(f"manifest {field} mismatch")

    archive_record = manifest.get("archive")
    if not isinstance(archive_record, dict):
        raise PreviewError("manifest archive must be an object")
    if archive_record.get("name") != ARCHIVE_NAME:
        raise PreviewError("manifest archive name mismatch")
    if archive_record.get("sha256") != sha256_file(archive_path):
        raise PreviewError("archive SHA-256 mismatch")
    if archive_record.get("size_bytes") != archive_path.stat().st_size:
        raise PreviewError("archive size mismatch")

    raw_files = manifest.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise PreviewError("manifest files must be a non-empty array")
    file_records = [_expect_manifest_file(value, f"files[{index}]") for index, value in enumerate(raw_files)]
    record_by_path = {str(record["path"]).casefold(): record for record in file_records}
    if len(record_by_path) != len(file_records):
        raise PreviewError("manifest contains duplicate file paths")

    executable = _expect_manifest_file(manifest.get("executable"), "executable")
    expected_executable_path = f"{PACKAGE_ROOT}/{PACKAGE_EXECUTABLE}"
    if executable.get("path") != expected_executable_path:
        raise PreviewError("manifest executable path mismatch")

    required_packaged = {
        expected_executable_path.casefold(),
        f"{PACKAGE_ROOT}/flutter_windows.dll".casefold(),
        f"{PACKAGE_ROOT}/data/icudtl.dat".casefold(),
    }
    seen: set[str] = set()
    has_flutter_assets = False
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            for info in archive.infolist():
                path = _safe_archive_path(info.filename)
                if info.is_dir():
                    continue
                identity = path.as_posix().casefold()
                if identity in seen:
                    raise PreviewError(f"archive contains duplicate path: {path}")
                seen.add(identity)
                if identity.startswith(f"{PACKAGE_ROOT}/data/flutter_assets/".casefold()):
                    has_flutter_assets = True
                record = record_by_path.get(identity)
                if record is None:
                    raise PreviewError(f"archive entry is absent from manifest: {path}")
                if info.file_size != record["size_bytes"]:
                    raise PreviewError(f"archive entry size mismatch: {path}")
                with archive.open(info, "r") as stream:
                    if _hash_stream(stream) != record["sha256"]:
                        raise PreviewError(f"archive entry SHA-256 mismatch: {path}")
    except (OSError, zipfile.BadZipFile) as error:
        raise PreviewError(f"cannot read archive: {error}") from error

    if seen != set(record_by_path):
        raise PreviewError("manifest and archive file sets differ")
    if not required_packaged.issubset(seen) or not has_flutter_assets:
        raise PreviewError("archive is missing required Windows runtime content")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("create", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--source-commit", required=True)
        subparser.add_argument("--workflow-run-id", required=True)
        subparser.add_argument("--windows-tree", required=True)
        if command == "create":
            subparser.add_argument("--bundle", type=Path, required=True)
            subparser.add_argument("--output-dir", type=Path, required=True)
        else:
            subparser.add_argument("--archive", type=Path, required=True)
            subparser.add_argument("--manifest", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "create":
            archive, manifest = create_preview(
                arguments.bundle,
                arguments.output_dir,
                arguments.source_commit,
                arguments.workflow_run_id,
                arguments.windows_tree,
            )
            print(json.dumps({"archive": str(archive), "manifest": str(manifest)}, sort_keys=True))
        else:
            verify_preview(
                arguments.archive,
                arguments.manifest,
                arguments.source_commit,
                arguments.workflow_run_id,
                arguments.windows_tree,
            )
            print(json.dumps({"verified": str(arguments.archive)}, sort_keys=True))
    except PreviewError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
