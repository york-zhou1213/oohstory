"""Fail-closed validation for user-contributed deconstruction sources."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


class UploadSecurityError(ValueError):
    pass


class UploadSecurityScanner:
    MAX_ARCHIVE_FILES = 20_000
    MAX_ARCHIVE_UNCOMPRESSED = 512 * 1024 * 1024
    MAX_ARCHIVE_RATIO = 250
    MAX_ARCHIVE_ENTRY = 64 * 1024 * 1024
    FORBIDDEN_SUFFIXES = {
        ".apk", ".app", ".bat", ".bin", ".cmd", ".com", ".dll", ".dmg",
        ".exe", ".hta", ".iso", ".jar", ".js", ".jse", ".lnk", ".msi",
        ".msp", ".pif", ".ps1", ".scr", ".sh", ".sys", ".vbe", ".vbs",
        ".wsf",
    }

    def __init__(self) -> None:
        self.clamdscan = shutil.which("clamdscan")
        self.clamscan = shutil.which("clamscan")

    @staticmethod
    def _clam_result(result: subprocess.CompletedProcess[str], mode: str) -> dict[str, str] | None:
        if result.returncode == 0:
            return {"clamav": "clean", "clamav_mode": mode}
        if result.returncode == 1:
            raise UploadSecurityError("ClamAV 检出病毒、木马或蠕虫，文件已销毁")
        return None

    def _clam_scan(self, path: Path) -> dict[str, str]:
        if self.clamdscan:
            result = subprocess.run(
                [self.clamdscan, "--fdpass", "--no-summary", "--infected", str(path)],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
                env={**os.environ, "LC_ALL": "C"},
            )
            clean = self._clam_result(result, "daemon")
            if clean:
                return clean
        if not self.clamscan:
            raise UploadSecurityError("服务器病毒扫描器不可用，安全策略已拒绝上传")
        result = subprocess.run(
            [
                self.clamscan,
                "--no-summary",
                "--infected",
                "--max-filesize=64M",
                "--max-scansize=128M",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
        clean = self._clam_result(result, "standalone")
        if not clean:
            raise UploadSecurityError("ClamAV 未能完成扫描，文件已按失败关闭策略拒绝")
        return clean

    @classmethod
    def _inspect_archive(cls, data: bytes, *, label: str) -> dict[str, int]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, OSError) as exc:
            raise UploadSecurityError(f"{label} 内容不是有效 ZIP 归档") from exc
        with archive:
            infos = archive.infolist()
            if not infos or len(infos) > cls.MAX_ARCHIVE_FILES:
                raise UploadSecurityError(f"{label} 文件数量异常")
            total = 0
            for info in infos:
                normalized = PurePosixPath(info.filename.replace("\\", "/"))
                if normalized.is_absolute() or ".." in normalized.parts or "\x00" in info.filename:
                    raise UploadSecurityError(f"{label} 包含路径穿越条目")
                if ((info.external_attr >> 16) & 0o170000) == stat.S_IFLNK:
                    raise UploadSecurityError(f"{label} 包含符号链接")
                if normalized.suffix.casefold() in cls.FORBIDDEN_SUFFIXES:
                    raise UploadSecurityError(f"{label} 包含禁止的可执行或脚本载荷")
                if int(info.file_size) > cls.MAX_ARCHIVE_ENTRY:
                    raise UploadSecurityError(f"{label} 包含超过单文件上限的条目")
                total += max(int(info.file_size), 0)
                compressed = max(int(info.compress_size), 1)
                if info.file_size > 10 * 1024 * 1024 and info.file_size / compressed > cls.MAX_ARCHIVE_RATIO:
                    raise UploadSecurityError(f"{label} 包含疑似解压炸弹")
            if total > cls.MAX_ARCHIVE_UNCOMPRESSED:
                raise UploadSecurityError(f"{label} 解压后体积超过安全上限")
        return {"archive_files": len(infos), "archive_uncompressed_bytes": total}

    @staticmethod
    def _inspect_text(data: bytes) -> dict[str, Any]:
        if data.startswith((b"MZ", b"\x7fELF", b"\xca\xfe\xba\xbe")):
            raise UploadSecurityError("文件是伪装成文本的可执行程序")
        sample = data[:1024 * 1024]
        if sample.count(b"\x00") > max(8, len(sample) // 100):
            raise UploadSecurityError("文本包含异常二进制载荷")
        decoded = None
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                decoded = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if decoded is None or len(decoded.strip()) < 100:
            raise UploadSecurityError("文本编码无法识别或正文过短")
        return {"text_chars": len(decoded)}

    def scan(self, path: Path, *, suffix: str, max_bytes: int) -> dict[str, Any]:
        path = Path(path).resolve(strict=True)
        size = path.stat().st_size
        if size < 100 or size > max_bytes:
            raise UploadSecurityError("文件为空、正文过短或超过上传大小限制")
        if path.is_symlink() or not path.is_file():
            raise UploadSecurityError("隔离区文件类型无效")
        data = path.read_bytes()
        if suffix == ".epub":
            structure = self._inspect_archive(data, label="EPUB")
        elif suffix == ".zip":
            structure = self._inspect_archive(data, label="ZIP")
        else:
            structure = self._inspect_text(data)
        clam = self._clam_scan(path)
        return {
            "status": "clean",
            "engine": "clamav+structural-v1",
            "bytes": size,
            "sha256": hashlib.sha256(data).hexdigest(),
            **clam,
            **structure,
        }

    def scan_binary(self, path: Path, *, max_bytes: int) -> dict[str, Any]:
        path = Path(path).resolve(strict=True)
        if path.is_symlink() or not path.is_file():
            raise UploadSecurityError("隔离区文件类型无效")
        size = path.stat().st_size
        if size <= 0 or size > max_bytes:
            raise UploadSecurityError("文件为空或超过上传大小限制")
        data = path.read_bytes()
        clam = self._clam_scan(path)
        return {
            "status": "clean", "engine": "clamav+binary-v1", "bytes": size,
            "sha256": hashlib.sha256(data).hexdigest(), **clam,
        }

    @classmethod
    def safe_extract_zip(cls, path: Path, destination: Path) -> list[str]:
        """Extract an already scanned ZIP without trusting member paths."""
        path = Path(path).resolve(strict=True)
        destination = Path(destination).resolve()
        destination.mkdir(parents=True, exist_ok=False, mode=0o700)
        extracted: list[str] = []
        total = 0
        try:
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                if not infos or len(infos) > cls.MAX_ARCHIVE_FILES:
                    raise UploadSecurityError("ZIP 文件数量异常")
                for info in infos:
                    name = info.filename.replace("\\", "/")
                    normalized = PurePosixPath(name)
                    if normalized.is_absolute() or ".." in normalized.parts or "\x00" in name:
                        raise UploadSecurityError("ZIP 包含路径穿越条目")
                    if ((info.external_attr >> 16) & 0o170000) == stat.S_IFLNK:
                        raise UploadSecurityError("ZIP 包含符号链接")
                    if info.is_dir():
                        continue
                    if normalized.suffix.casefold() in cls.FORBIDDEN_SUFFIXES:
                        raise UploadSecurityError("ZIP 包含禁止的可执行或脚本载荷")
                    total += max(int(info.file_size), 0)
                    if int(info.file_size) > cls.MAX_ARCHIVE_ENTRY or total > cls.MAX_ARCHIVE_UNCOMPRESSED:
                        raise UploadSecurityError("ZIP 解压后体积超过安全上限")
                    target = (destination / Path(*normalized.parts)).resolve()
                    if target != destination and destination not in target.parents:
                        raise UploadSecurityError("ZIP 目标路径越界")
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with archive.open(info) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    target.chmod(0o600)
                    extracted.append(normalized.as_posix())
        except BaseException:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        return extracted
