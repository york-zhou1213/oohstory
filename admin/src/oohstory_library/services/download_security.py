"""Mandatory malware and container checks for every remote library download."""

from __future__ import annotations

import fcntl
import hashlib
import io
import os
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


class DownloadSecurityError(ValueError):
    """Raised when a downloaded payload must not enter the library."""


class DownloadSecurityScanner:
    MAX_ARCHIVE_FILES = 20_000
    MAX_ARCHIVE_UNCOMPRESSED = 512 * 1024 * 1024
    MAX_ARCHIVE_RATIO = 250
    FORBIDDEN_SUFFIXES = {
        ".apk", ".app", ".bat", ".bin", ".cmd", ".com", ".dll", ".dmg",
        ".exe", ".hta", ".iso", ".jar", ".js", ".jse", ".lnk", ".msi",
        ".msp", ".pif", ".ps1", ".scr", ".sh", ".sys", ".vbe", ".vbs",
        ".wsf",
    }

    def __init__(self, staging_root: Path) -> None:
        self.staging_root = Path(staging_root).expanduser().resolve()
        self.clamscan = shutil.which("clamscan")
        self.clamdscan = shutil.which("clamdscan")

    @staticmethod
    def _payload_kind(data: bytes, extension: str) -> str:
        suffix = str(extension or "").lower().lstrip(".")
        if data.startswith(b"PK\x03\x04") or suffix == "epub":
            return "epub"
        if suffix in {"jpg", "jpeg", "png", "gif", "webp"}:
            return "image"
        return "text"

    @classmethod
    def _inspect_epub(cls, data: bytes) -> dict[str, Any]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, OSError) as exc:
            raise DownloadSecurityError("下载文件不是有效 EPUB/ZIP") from exc
        with archive:
            infos = archive.infolist()
            if not infos or len(infos) > cls.MAX_ARCHIVE_FILES:
                raise DownloadSecurityError("EPUB 文件数量异常，拒绝入库")
            total_uncompressed = 0
            for info in infos:
                normalized = PurePosixPath(info.filename.replace("\\", "/"))
                if (
                    normalized.is_absolute()
                    or ".." in normalized.parts
                    or "\x00" in info.filename
                ):
                    raise DownloadSecurityError("EPUB 包含路径穿越条目")
                mode = (info.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise DownloadSecurityError("EPUB 包含符号链接")
                suffix = normalized.suffix.casefold()
                if suffix in cls.FORBIDDEN_SUFFIXES:
                    raise DownloadSecurityError(
                        f"EPUB 包含禁止的可执行/脚本载荷：{suffix}"
                    )
                total_uncompressed += max(int(info.file_size), 0)
                compressed = max(int(info.compress_size), 1)
                if info.file_size > 10 * 1024 * 1024 and (
                    info.file_size / compressed
                ) > cls.MAX_ARCHIVE_RATIO:
                    raise DownloadSecurityError("EPUB 包含疑似解压炸弹条目")
            if total_uncompressed > cls.MAX_ARCHIVE_UNCOMPRESSED:
                raise DownloadSecurityError("EPUB 解压后体积超过安全上限")
        return {
            "archive_files": len(infos),
            "archive_uncompressed_bytes": total_uncompressed,
        }

    @staticmethod
    def _inspect_plain(data: bytes, kind: str) -> None:
        if data.startswith((b"MZ", b"\x7fELF", b"\xca\xfe\xba\xbe")):
            raise DownloadSecurityError("下载内容伪装成书籍的可执行文件")
        if kind == "text" and data:
            sample = data[:1024 * 1024]
            if sample.count(b"\x00") > max(8, len(sample) // 100):
                raise DownloadSecurityError("文本含异常二进制载荷")

    @staticmethod
    def _clam_result(
        result: subprocess.CompletedProcess[str],
        *,
        mode: str,
    ) -> dict[str, str] | None:
        if result.returncode == 0:
            return {"clamav": "clean", "clamav_mode": mode}
        if result.returncode == 1:
            raise DownloadSecurityError("ClamAV 检出病毒或木马，文件已拒绝入库")
        return None

    def _clam_scan(self, path: Path) -> dict[str, str]:
        # ``clamscan`` reloads the complete signature database for every
        # payload (10-20 seconds and ~200 MiB each on this host).  Prefer the
        # resident daemon so concurrent downloads share one already-loaded
        # database.  A daemon transport failure falls back to the standalone
        # scanner and therefore never weakens the fail-closed policy.
        daemon_error = ""
        # Packages can be installed/recovered while a long-running backend is
        # alive.  Re-resolve a previously missing daemon client so the process
        # switches away from the expensive standalone scanner immediately.
        self.clamdscan = self.clamdscan or shutil.which("clamdscan")
        if self.clamdscan:
            daemon = subprocess.run(
                [
                    self.clamdscan,
                    "--fdpass",
                    "--no-summary",
                    "--infected",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
                env={**os.environ, "LC_ALL": "C"},
            )
            daemon_result = self._clam_result(daemon, mode="daemon")
            if daemon_result:
                return daemon_result
            daemon_error = (
                daemon.stderr or daemon.stdout or "ClamAV daemon unavailable"
            ).strip()
        if not self.clamscan:
            raise DownloadSecurityError(
                "服务器未安装可用的 ClamAV，安全策略禁止任何远程文件入库"
            )
        # The standalone scanner loads roughly 1 GiB of signatures on this
        # host.  Serialize the emergency fallback across downloader workers so
        # a daemon outage cannot recreate the previous memory/I/O stampede.
        fallback_lock = self.staging_root / ".clamav-standalone.lock"
        fallback_lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with fallback_lock.open("a+b") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            result = subprocess.run(
                [
                    self.clamscan,
                    "--no-summary",
                    "--infected",
                    "--max-filesize=150M",
                    "--max-scansize=200M",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
                env={**os.environ, "LC_ALL": "C"},
            )
        standalone_result = self._clam_result(result, mode="standalone")
        if not standalone_result:
            message = (result.stderr or result.stdout or "ClamAV 扫描失败").strip()
            if daemon_error:
                message = f"daemon={daemon_error[:100]}; standalone={message}"
            raise DownloadSecurityError(
                f"ClamAV 未能完成扫描，默认拒绝入库：{message[:220]}"
            )
        return standalone_result

    def scan_bytes(
        self,
        data: bytes,
        *,
        extension: str,
        source: str,
    ) -> dict[str, Any]:
        if not isinstance(data, bytes) or len(data) < 16:
            raise DownloadSecurityError("下载内容为空或过短")
        kind = self._payload_kind(data, extension)
        self._inspect_plain(data, kind)
        archive = self._inspect_epub(data) if kind == "epub" else {}
        self.staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        suffix = "." + str(extension or "bin").lower().lstrip(".")
        with tempfile.NamedTemporaryFile(
            prefix="remote-scan-",
            suffix=suffix,
            dir=self.staging_root,
            delete=False,
        ) as handle:
            handle.write(data)
            temporary = Path(handle.name)
        try:
            temporary.chmod(0o600)
            clam_result = self._clam_scan(temporary)
        finally:
            temporary.unlink(missing_ok=True)
        return {
            "status": "clean",
            "engine": "clamav+structural",
            "source": str(source or "")[:80],
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            **clam_result,
            **archive,
        }

    def scan_file(
        self,
        path: Path,
        *,
        extension: str = "",
        source: str = "",
        max_bytes: int = 150 * 1024 * 1024,
    ) -> dict[str, Any]:
        path = Path(path).expanduser().resolve()
        if not path.is_file():
            raise DownloadSecurityError("待扫描下载文件不存在")
        size = path.stat().st_size
        if size <= 0 or size > max_bytes:
            raise DownloadSecurityError("下载文件大小超出安全范围")
        return self.scan_bytes(
            path.read_bytes(),
            extension=extension or path.suffix,
            source=source,
        )
