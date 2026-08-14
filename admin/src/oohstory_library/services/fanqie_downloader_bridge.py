"""Headless interoperability bridge for the official Fanqie desktop downloader.

The referenced public repository publishes a closed-core Tauri desktop binary,
not a CLI or reusable source library.  This bridge therefore treats the
official, checksum-verified Linux package as an external application and
automates its stable v2026.7.26-709 UI inside an isolated Xvfb display.
"""

from __future__ import annotations

from .error_boundaries import RECOVERABLE_OPERATION_ERRORS

import difflib
import fcntl
import json
import os
import re
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional


class FanqieDownloaderBridge:
    PROVIDER_ID = "fanqie_desktop_bridge"
    SUPPORTED_VERSION = "2026.7.26-709"
    PACKAGE_NAME = "fanqie-novel-downloader"
    APP_ID = "com.pofl.fanqienoveldownloader"

    def __init__(self, library_root: Path) -> None:
        self.library_root = Path(library_root).resolve()
        self.state_root = self.library_root / "fanqie-downloader"
        self.data_root = self.state_root / "data"
        self.config_root = self.state_root / "config"
        self.app_data_root = self.data_root / self.APP_ID
        self.export_root = (
            self.app_data_root / "downloads" / "FanqieNovels"
        )
        self.state_path = self.app_data_root / "rust_state.json"
        self.lock_path = self.state_root / "bridge.lock"
        self.binary = shutil.which("fanqie-desktop")
        self.xvfb = shutil.which("Xvfb")
        self.xdotool = shutil.which("xdotool")
        self.xclip = shutil.which("xclip")

    @staticmethod
    def validate_book_id(value: Any) -> str:
        book_id = str(value or "").strip()
        if not re.fullmatch(r"\d{10,24}", book_id):
            raise ValueError("番茄作品 bookId 必须是 10～24 位数字")
        return book_id

    @staticmethod
    def _package_version() -> str:
        try:
            result = subprocess.run(
                [
                    "dpkg-query",
                    "-W",
                    "-f=${Version}",
                    FanqieDownloaderBridge.PACKAGE_NAME,
                ],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""

    def availability(self) -> Dict[str, Any]:
        version = self._package_version()
        dependencies = {
            "fanqie-desktop": bool(self.binary),
            "Xvfb": bool(self.xvfb),
            "xdotool": bool(self.xdotool),
            "xclip": bool(self.xclip),
        }
        return {
            "id": self.PROVIDER_ID,
            "name": "Fanqie Novel Downloader 桌面桥",
            "enabled": (
                all(dependencies.values())
                and version == self.SUPPORTED_VERSION
            ),
            "installed_version": version,
            "supported_version": self.SUPPORTED_VERSION,
            "dependencies": dependencies,
            "automation": "isolated_xvfb_ui",
            "upstream": (
                "https://github.com/POf-L/"
                "Fanqie-novel-Downloader"
            ),
            "source_model": "public_release_private_core",
            "export_root": str(self.export_root),
        }

    def _require_available(self) -> None:
        status = self.availability()
        if not all(status["dependencies"].values()):
            missing = [
                name
                for name, present in status["dependencies"].items()
                if not present
            ]
            raise RuntimeError(
                "番茄下载桥缺少运行依赖：" + "、".join(missing)
            )
        if status["installed_version"] != self.SUPPORTED_VERSION:
            raise RuntimeError(
                "番茄下载器界面版本未验证："
                f"已安装 {status['installed_version'] or '无'}，"
                f"当前支持 {self.SUPPORTED_VERSION}"
            )

    @staticmethod
    def _start_xvfb() -> tuple[subprocess.Popen, str]:
        read_fd, write_fd = os.pipe()
        try:
            process = subprocess.Popen(
                [
                    "Xvfb",
                    "-displayfd",
                    str(write_fd),
                    "-screen",
                    "0",
                    "1280x1024x24",
                    "-nolisten",
                    "tcp",
                    "-ac",
                ],
                pass_fds=(write_fd,),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=False,
            )
        finally:
            os.close(write_fd)
        try:
            display_number = os.read(read_fd, 32).decode().strip()
        finally:
            os.close(read_fd)
        if not display_number or process.poll() is not None:
            stderr = (
                process.stderr.read().decode(errors="replace")
                if process.stderr
                else ""
            )
            raise RuntimeError(
                f"无法启动隔离显示：{stderr[:300]}"
            )
        return process, f":{display_number}"

    @staticmethod
    def _run_xdotool(
        display: str, *arguments: str, timeout: int = 12
    ) -> str:
        result = subprocess.run(
            ["xdotool", *arguments],
            env={**os.environ, "DISPLAY": display},
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                (result.stderr or result.stdout or "xdotool 执行失败")[
                    :400
                ]
            )
        return result.stdout.strip()

    def _wait_for_window(self, display: str, timeout: int = 35) -> str:
        deadline = time.monotonic() + timeout
        last_error = ""
        while time.monotonic() < deadline:
            try:
                output = self._run_xdotool(
                    display,
                    "search",
                    "--onlyvisible",
                    "--name",
                    "番茄小说下载器",
                )
                window_id = output.splitlines()[-1].strip()
                if window_id:
                    return window_id
            except RECOVERABLE_OPERATION_ERRORS as exc:
                last_error = str(exc)
            time.sleep(0.5)
        raise RuntimeError(
            "番茄下载器窗口启动超时"
            + (f"：{last_error[:200]}" if last_error else "")
        )

    @classmethod
    def _click(
        cls, display: str, window_id: str, x: int, y: int
    ) -> None:
        cls._run_xdotool(
            display,
            "mousemove",
            "--window",
            window_id,
            str(x),
            str(y),
            "click",
            "1",
        )

    def _replace_input(
        self,
        display: str,
        window_id: str,
        x: int,
        y: int,
        value: str,
    ) -> None:
        self._click(display, window_id, x, y)
        self._run_xdotool(display, "key", "ctrl+a")
        # ``xdotool type`` only handles keysyms reliably and silently drops
        # Chinese search terms.  The official client accepts a normal
        # clipboard paste, which also works for numeric book IDs.
        clipboard = subprocess.Popen(
            [str(self.xclip), "-selection", "clipboard", "-i"],
            env={**os.environ, "DISPLAY": display},
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert clipboard.stdin is not None
            clipboard.stdin.write(value)
            clipboard.stdin.close()
            time.sleep(0.15)
            self._run_xdotool(display, "key", "ctrl+v")
            return_code = clipboard.wait(timeout=8)
            if return_code != 0:
                stderr = (
                    clipboard.stderr.read()
                    if clipboard.stderr is not None
                    else ""
                )
                raise RuntimeError(
                    (stderr or "无法写入番茄搜索剪贴板")[:300]
                )
        finally:
            if clipboard.poll() is None:
                clipboard.terminate()
                try:
                    clipboard.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    clipboard.kill()

    def _read_completed_export(
        self, book_id: str, started_at: float
    ) -> Optional[Dict[str, Any]]:
        try:
            payload = json.loads(
                self.state_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return None
        for item in reversed(payload.get("history") or []):
            if str(item.get("book_id") or "") != book_id:
                continue
            path = Path(str(item.get("save_path") or "")).resolve()
            if (
                not path.is_file()
                or not path.is_relative_to(self.export_root.resolve())
                or path.suffix.lower() not in {".txt", ".epub"}
                or path.stat().st_size < 128
            ):
                continue
            if path.stat().st_mtime + 2 < started_at:
                continue
            return {
                "book_id": book_id,
                "title": str(item.get("book_name") or "").strip(),
                "author": str(item.get("author") or "").strip(),
                "extension": path.suffix.lower().lstrip("."),
                "path": str(path),
                "bytes": path.stat().st_size,
                "download_time": item.get("download_time"),
            }
        return None

    def _archive_previous_export(self, book_id: str) -> Optional[Path]:
        """Keep an earlier export while preventing the desktop overwrite modal."""
        try:
            payload = json.loads(
                self.state_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return None
        for item in reversed(payload.get("history") or []):
            if str(item.get("book_id") or "") != book_id:
                continue
            path = Path(str(item.get("save_path") or "")).resolve()
            if (
                not path.is_file()
                or not path.is_relative_to(self.export_root.resolve())
            ):
                continue
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            archived = path.with_name(
                f"{path.stem}.previous-{timestamp}{path.suffix}"
            )
            counter = 1
            while archived.exists():
                archived = path.with_name(
                    f"{path.stem}.previous-{timestamp}-{counter}"
                    f"{path.suffix}"
                )
                counter += 1
            path.replace(archived)
            return archived
        return None

    @staticmethod
    def _normalize_search_text(value: Any) -> str:
        text = unicodedata.normalize("NFKC", str(value or "")).casefold()
        return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)

    @classmethod
    def _search_rank(
        cls, query: str, title: str, author: str
    ) -> float:
        needle = cls._normalize_search_text(query)
        title_text = cls._normalize_search_text(title)
        author_text = cls._normalize_search_text(author)
        if not needle:
            return 0.0
        if needle == title_text:
            return 100.0
        if needle == author_text:
            return 96.0
        if needle in title_text:
            return 92.0 - min(len(title_text) - len(needle), 30) * 0.2
        if needle in author_text:
            return 88.0 - min(len(author_text) - len(needle), 30) * 0.2
        title_ratio = difflib.SequenceMatcher(
            None, needle, title_text
        ).ratio()
        author_ratio = difflib.SequenceMatcher(
            None, needle, author_text
        ).ratio()
        return round(max(title_ratio * 80.0, author_ratio * 76.0), 2)

    @classmethod
    def parse_search_clipboard(
        cls, text: str, query: str, *, limit: int = 0
    ) -> List[Dict[str, Any]]:
        """Parse stable visible text copied from the pinned desktop app."""
        query = str(query or "").strip()
        limit = max(int(limit), 0)
        lines = [
            re.sub(r"\s+", " ", line).strip()
            for line in str(text or "").splitlines()
            if line.strip()
        ]
        id_pattern = re.compile(
            r"^(?P<author>.+?)\s*[·•]\s*ID\s*"
            r"(?P<book_id>\d{10,24})$"
        )
        status_values = {"连载中", "已完结", "完结", "连载"}
        results: List[Dict[str, Any]] = []
        seen_ids = set()
        for index, line in enumerate(lines):
            match = id_pattern.match(line)
            if not match:
                continue
            book_id = match.group("book_id")
            if book_id in seen_ids:
                continue
            title = lines[index - 1] if index else ""
            if title.endswith(" 封面"):
                title = title[: -len(" 封面")].strip()
            if not title or title in {"搜索", "载入", "详情"}:
                continue
            author = match.group("author").strip()
            description = ""
            status = ""
            for candidate in lines[index + 1 : index + 4]:
                if candidate in status_values:
                    status = candidate
                    break
                if not description and not id_pattern.match(candidate):
                    description = candidate
            seen_ids.add(book_id)
            results.append(
                {
                    "provider": cls.PROVIDER_ID,
                    "provider_label": "番茄小说（官方下载器）",
                    "remote_id": book_id,
                    "source_ref": (
                        f"https://fanqienovel.com/page/{book_id}"
                    ),
                    "title": title,
                    "author": author or "作者未知",
                    "description": description[:600],
                    "book_status": status,
                    "extension": "txt",
                    "filesize": "",
                    "downloadable": True,
                    "license": "通过已安装的官方番茄下载器获取",
                    "source": "fanqie_official_downloader",
                    "_rank": cls._search_rank(query, title, author),
                }
            )
        results.sort(
            key=lambda item: (
                item["_rank"],
                cls._normalize_search_text(item["title"]),
            ),
            reverse=True,
        )
        for item in results:
            item.pop("_rank", None)
        return results if limit <= 0 else results[:limit]

    def _copy_visible_text(
        self, display: str, window_id: str
    ) -> str:
        self._click(display, window_id, 600, 400)
        self._run_xdotool(display, "key", "ctrl+a")
        self._run_xdotool(display, "key", "ctrl+c")
        time.sleep(0.25)
        result = subprocess.run(
            [str(self.xclip), "-selection", "clipboard", "-o"],
            env={**os.environ, "DISPLAY": display},
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr or ""
            if (
                "target STRING not available" in message
                or "selection owner" in message.lower()
            ):
                return ""
            raise RuntimeError(
                (message or "无法读取番茄搜索结果剪贴板")[:300]
            )
        return result.stdout

    def search(
        self, query: str, *, limit: int = 0, timeout: int = 90
    ) -> List[Dict[str, Any]]:
        """Search by title/author through the pinned official desktop app."""
        self._require_available()
        query = unicodedata.normalize("NFKC", str(query or "")).strip()
        if len(query) < 2:
            raise ValueError("番茄搜索关键词至少需要 2 个字符")
        if len(query) > 100:
            raise ValueError("番茄搜索关键词不能超过 100 个字符")
        limit = max(int(limit), 0)
        timeout = min(max(int(timeout), 20), 120)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.config_root.mkdir(parents=True, exist_ok=True)
        self.export_root.mkdir(parents=True, exist_ok=True)

        app_process = None
        xvfb_process = None
        with self.lock_path.open("a+") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                xvfb_process, display = self._start_xvfb()
                app_env = {
                    **os.environ,
                    "DISPLAY": display,
                    "XDG_DATA_HOME": str(self.data_root),
                    "XDG_CONFIG_HOME": str(self.config_root),
                    "LIBGL_ALWAYS_SOFTWARE": "1",
                    "WEBKIT_DISABLE_COMPOSITING_MODE": "1",
                }
                app_process = subprocess.Popen(
                    [str(self.binary)],
                    env=app_env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                window_id = self._wait_for_window(display)
                self._run_xdotool(
                    display, "windowsize", window_id, "1180", "760"
                )
                self._run_xdotool(
                    display, "windowmove", window_id, "50", "132"
                )
                time.sleep(1)
                self._replace_input(
                    display, window_id, 170, 292, query
                )
                self._click(display, window_id, 390, 292)

                deadline = time.monotonic() + timeout
                last_text = ""
                while time.monotonic() < deadline:
                    if app_process.poll() is not None:
                        raise RuntimeError(
                            "番茄下载器在搜索完成前退出"
                        )
                    time.sleep(2)
                    last_text = self._copy_visible_text(
                        display, window_id
                    )
                    results = self.parse_search_clipboard(
                        last_text, query, limit=limit
                    )
                    if results:
                        return results
                    if re.search(r"共找到\s*0\s*条结果", last_text):
                        return []
                raise TimeoutError(
                    "番茄作品搜索超时；请稍后重试或检查下载器网络"
                )
            finally:
                if app_process is not None and app_process.poll() is None:
                    app_process.terminate()
                    try:
                        app_process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        app_process.kill()
                if xvfb_process is not None and xvfb_process.poll() is None:
                    xvfb_process.terminate()
                    try:
                        xvfb_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        xvfb_process.kill()
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def download(
        self,
        book_id: Any,
        *,
        file_format: str = "txt",
        start_chapter: Optional[int] = None,
        end_chapter: Optional[int] = None,
        timeout: int = 3600,
    ) -> Dict[str, Any]:
        """Download one Fanqie book through the pinned official desktop app."""
        self._require_available()
        book_id = self.validate_book_id(book_id)
        file_format = file_format.lower().strip(".")
        if file_format not in {"txt", "epub"}:
            raise ValueError("番茄下载器只支持 TXT 或 EPUB")
        if start_chapter is not None and int(start_chapter) < 1:
            raise ValueError("起始章节必须大于 0")
        if end_chapter is not None and int(end_chapter) < 1:
            raise ValueError("结束章节必须大于 0")
        if (
            start_chapter is not None
            and end_chapter is not None
            and int(end_chapter) < int(start_chapter)
        ):
            raise ValueError("结束章节不能小于起始章节")
        timeout = min(max(int(timeout), 30), 7200)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.config_root.mkdir(parents=True, exist_ok=True)
        self.export_root.mkdir(parents=True, exist_ok=True)

        app_process = None
        xvfb_process = None
        with self.lock_path.open("a+") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                previous_export = self._archive_previous_export(book_id)
                xvfb_process, display = self._start_xvfb()
                app_env = {
                    **os.environ,
                    "DISPLAY": display,
                    "XDG_DATA_HOME": str(self.data_root),
                    "XDG_CONFIG_HOME": str(self.config_root),
                    "LIBGL_ALWAYS_SOFTWARE": "1",
                    "WEBKIT_DISABLE_COMPOSITING_MODE": "1",
                }
                app_process = subprocess.Popen(
                    [str(self.binary)],
                    env=app_env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                window_id = self._wait_for_window(display)
                self._run_xdotool(
                    display,
                    "windowsize",
                    window_id,
                    "1180",
                    "760",
                )
                self._run_xdotool(
                    display, "windowmove", window_id, "50", "132"
                )
                time.sleep(1)

                self._replace_input(
                    display, window_id, 170, 292, book_id
                )
                self._click(display, window_id, 390, 292)
                # Search results are populated asynchronously by the
                # desktop app.  The upstream request can occasionally take
                # about a minute, so a single fixed-delay click races the
                # result card and leaves the detail pane empty.  Re-select
                # the sole exact-bookId result during a bounded 90-second
                # window; selecting an already active card is harmless.
                result_points = ((250, 398), (285, 398), (250, 420))
                for attempt in range(18):
                    time.sleep(5)
                    self._click(
                        display,
                        window_id,
                        *result_points[attempt % len(result_points)],
                    )
                time.sleep(4)
                self._run_xdotool(
                    display,
                    "mousemove",
                    "--window",
                    window_id,
                    "1070",
                    "658",
                    "click",
                    "--repeat",
                    "7",
                    "--delay",
                    "100",
                    "5",
                )
                time.sleep(1)
                if file_format == "epub":
                    self._click(display, window_id, 808, 510)
                else:
                    self._click(display, window_id, 691, 510)
                if start_chapter is not None:
                    self._replace_input(
                        display,
                        window_id,
                        745,
                        580,
                        str(int(start_chapter)),
                    )
                if end_chapter is not None:
                    self._replace_input(
                        display,
                        window_id,
                        1010,
                        580,
                        str(int(end_chapter)),
                    )
                started_at = time.time()
                self._click(display, window_id, 690, 649)

                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if app_process.poll() is not None:
                        raise RuntimeError(
                            "番茄下载器在任务完成前退出"
                        )
                    completed = self._read_completed_export(
                        book_id, started_at
                    )
                    if completed:
                        completed.update(
                            {
                                "provider": self.PROVIDER_ID,
                                "status": "downloaded",
                                "chapter_start": start_chapter,
                                "chapter_end": end_chapter,
                                "previous_export": (
                                    str(previous_export)
                                    if previous_export
                                    else None
                                ),
                            }
                        )
                        return completed
                    time.sleep(2)
                raise TimeoutError(
                    f"番茄作品下载超过 {timeout} 秒仍未完成"
                )
            finally:
                if app_process is not None and app_process.poll() is None:
                    app_process.terminate()
                    try:
                        app_process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        app_process.kill()
                if xvfb_process is not None and xvfb_process.poll() is None:
                    xvfb_process.terminate()
                    try:
                        xvfb_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        xvfb_process.kill()
                fcntl.flock(lock_file, fcntl.LOCK_UN)
