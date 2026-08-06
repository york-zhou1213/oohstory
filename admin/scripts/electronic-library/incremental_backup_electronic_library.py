#!/usr/bin/env python3
"""Safely back up the canonical electronic-library tree with rsync.

The canonical repository is always ``oohstory-backend``.  A path containing
``webnovel-writer-production-mysql`` is rejected even when supplied through an
environment variable or symlink.  The default mode is a non-mutating rsync
dry-run; a real backup requires the explicit ``--apply`` flag.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fcntl
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TextIO


CANONICAL_SOURCE = Path(
    "/opt/oohstory-admin/electronic-library"
)
DEFAULT_DEST = Path("/srv/oohstory/library")
DEFAULT_LOG_DIR = DEFAULT_DEST / "_backup_logs"
DEFAULT_LOCK_FILE = Path("/tmp/electronic_library_backup.lock")
FORBIDDEN_DIRECTORY_NAME = "webnovel-writer-production-mysql"

SOURCE_ENV = "WEBNOVEL_ELECTRONIC_LIBRARY_SOURCE"
DEST_ENV = "WEBNOVEL_ELECTRONIC_LIBRARY_BACKUP_DEST"
LOG_DIR_ENV = "WEBNOVEL_ELECTRONIC_LIBRARY_BACKUP_LOG_DIR"
LOCK_FILE_ENV = "WEBNOVEL_ELECTRONIC_LIBRARY_BACKUP_LOCK_FILE"


def now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def environment_path(name: str, fallback: Path) -> str:
    return os.getenv(name, "").strip() or str(fallback)


def resolve_path(path_text: str) -> Path:
    return Path(path_text).expanduser().resolve()


def reject_production_mysql_path(path: Path, *, label: str) -> None:
    if FORBIDDEN_DIRECTORY_NAME in path.parts:
        raise ValueError(
            f"{label}禁止指向灰度目录 {FORBIDDEN_DIRECTORY_NAME}: {path}"
        )


def check_path_safety(
    source: Path,
    dest: Path,
    log_dir: Path,
    *,
    lock_file: Path,
    exclude_file: Path | None = None,
) -> None:
    checked = [
        ("源目录", source),
        ("目标目录", dest),
        ("日志目录", log_dir),
        ("锁文件", lock_file),
    ]
    if exclude_file is not None:
        checked.append(("排除规则文件", exclude_file))
    for label, path in checked:
        reject_production_mysql_path(path, label=label)

    if not source.is_dir():
        raise ValueError(f"源目录不存在或不是目录: {source}")
    if source == dest:
        raise ValueError("源目录和目标目录不能相同")
    try:
        dest.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("目标目录不能位于源目录内部")
    try:
        log_dir.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("日志目录不能位于源目录内部")
    if exclude_file is not None and not exclude_file.is_file():
        raise ValueError(f"排除规则文件不存在: {exclude_file}")


def check_rsync_exists() -> str:
    executable = shutil.which("rsync")
    if executable is None:
        raise RuntimeError("系统没有找到 rsync")
    return executable


def acquire_lock(lock_path: Path) -> TextIO:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_fh.close()
        raise RuntimeError("已有一个电子书库备份任务正在运行")
    lock_fh.write(str(os.getpid()))
    lock_fh.flush()
    return lock_fh


def build_rsync_command(
    args: argparse.Namespace,
    source: Path,
    dest: Path,
    *,
    log_file: Path | None,
    executable: str = "rsync",
) -> list[str]:
    command = [
        executable,
        "-aHAX",
        "--numeric-ids",
        "--human-readable",
        "--stats",
        "--info=progress2",
        "--partial",
        "--partial-dir=.rsync-partial",
        "--modify-window=1",
    ]
    if not args.apply:
        command.append("--dry-run")
    if log_file is not None:
        command.append(f"--log-file={log_file}")
    if args.delete:
        command.append("--delete-delay")
    if args.checksum:
        command.append("--checksum")
    if args.inplace:
        command.append("--inplace")
    if args.whole_file:
        command.append("--whole-file")
    if args.bwlimit:
        command.append(f"--bwlimit={args.bwlimit}")
    for pattern in args.exclude:
        command.extend(["--exclude", pattern])
    if args.exclude_file:
        command.extend(["--exclude-from", str(args.exclude_file)])
    command.extend([f"{source}/", f"{dest}/"])
    return command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "增量备份 canonical oohstory-backend/electronic-library；"
            "默认只 dry-run，正式同步必须显式 --apply。"
        )
    )
    parser.add_argument(
        "--source",
        default=environment_path(SOURCE_ENV, CANONICAL_SOURCE),
        help=f"源目录；环境变量 {SOURCE_ENV}",
    )
    parser.add_argument(
        "--dest",
        default=environment_path(DEST_ENV, DEFAULT_DEST),
        help=f"目标目录；环境变量 {DEST_ENV}",
    )
    parser.add_argument(
        "--log-dir",
        default=environment_path(LOG_DIR_ENV, DEFAULT_LOG_DIR),
        help=f"日志目录；环境变量 {LOG_DIR_ENV}",
    )
    parser.add_argument(
        "--lock-file",
        default=environment_path(LOCK_FILE_ENV, DEFAULT_LOCK_FILE),
        help=f"锁文件；环境变量 {LOCK_FILE_ENV}",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="正式执行备份；未指定时永远是 dry-run",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="显式 dry-run（默认行为，保留便于自动化阅读）",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="只检查环境、路径和 rsync，不启动 rsync",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="目标端延迟删除源端已删除文件；仅 --apply 时会真正删除",
    )
    parser.add_argument("--checksum", action="store_true")
    parser.add_argument("--inplace", action="store_true")
    parser.add_argument("--whole-file", action="store_true")
    parser.add_argument("--bwlimit", type=int, default=0)
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--exclude-file", default="")
    return parser


def run_backup(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.bwlimit < 0:
        parser.error("--bwlimit cannot be negative")

    source = resolve_path(args.source)
    dest = resolve_path(args.dest)
    log_dir = resolve_path(args.log_dir)
    lock_file = resolve_path(args.lock_file)
    exclude_file = (
        resolve_path(args.exclude_file) if args.exclude_file else None
    )
    args.exclude_file = exclude_file
    try:
        executable = check_rsync_exists()
        check_path_safety(
            source,
            dest,
            log_dir,
            lock_file=lock_file,
            exclude_file=exclude_file,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    configuration = {
        "mode": (
            "self-check"
            if args.self_check
            else ("apply" if args.apply else "dry-run")
        ),
        "source": str(source),
        "dest": str(dest),
        "log_dir": str(log_dir),
        "rsync": executable,
        "delete": bool(args.delete),
    }
    if args.self_check:
        print(json.dumps(configuration, ensure_ascii=False, indent=2))
        return 0

    log_file: Path | None = None
    lock_fh: TextIO | None = None
    if args.apply:
        dest.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            lock_fh = acquire_lock(lock_file)
        except RuntimeError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 3
        log_file = log_dir / f"electronic_library_backup_{now_stamp()}.log"

    command = build_rsync_command(
        args,
        source,
        dest,
        log_file=log_file,
        executable=executable,
    )
    print(json.dumps(configuration, ensure_ascii=False, indent=2))
    print("执行命令：")
    print(" ".join(subprocess.list2cmdline([part]) for part in command))
    try:
        return subprocess.run(command, check=False).returncode
    finally:
        if lock_fh is not None:
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
                lock_fh.close()
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(run_backup())
