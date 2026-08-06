"""Authorized z-library.im search and download adapter.

The provider is intentionally opt-in to a single configured host and uses a
real browser session for the site's JavaScript challenge. Downloads are
bounded, rate-limited, and restricted to observed Z-Library CDN hosts.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote, unquote, urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


_BROWSER_LOCK = threading.Lock()


class AuthorizedZLibraryProvider:
    PROVIDER_ID = "authorized_zlibrary"

    def __init__(self) -> None:
        self.base_url = os.getenv(
            "WEBNOVEL_ZLIBRARY_BASE_URL", "https://singlelogin.re"
        ).rstrip("/")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "singlelogin.re",
            "z-library.im",
        }:
            raise ValueError("Z-Library 来源必须使用受控 HTTPS 登录主机")
        self.enabled = os.getenv("WEBNOVEL_ZLIBRARY_ENABLED", "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        self.browser_bin = shutil.which("agent-browser")
        self.browser_session = os.getenv(
            "WEBNOVEL_ZLIBRARY_BROWSER_SESSION", "webnovel_zlibrary"
        )
        self.delay = max(
            0.5, float(os.getenv("WEBNOVEL_ZLIBRARY_DELAY", "1.2"))
        )
        self.max_bytes = max(
            1,
            int(os.getenv("WEBNOVEL_ZLIBRARY_MAX_MB", "120")),
        ) * 1024 * 1024
        suffixes = os.getenv(
            "WEBNOVEL_ZLIBRARY_DOWNLOAD_HOST_SUFFIXES", ".ncdn.ec"
        )
        self.download_host_suffixes = tuple(
            value.strip().lower()
            for value in suffixes.split(",")
            if value.strip()
        )
        self._last_request = 0.0
        self.email = os.getenv("WEBNOVEL_ZLIBRARY_EMAIL", "").strip()
        self.password = os.getenv("WEBNOVEL_ZLIBRARY_PASSWORD", "")
        self._authenticated = False

    def availability(self) -> Dict[str, Any]:
        return {
            "id": self.PROVIDER_ID,
            "name": "Z-Library（站点所有者授权）",
            "enabled": bool(self.enabled and self.browser_bin),
            "base_url": self.base_url,
            "browser_required": True,
            "account_configured": bool(self.email and self.password),
            "authorization": "site_owner_authorized",
            "max_download_mb": self.max_bytes // (1024 * 1024),
        }

    def _throttle(self) -> None:
        wait_for = self.delay - (time.monotonic() - self._last_request)
        if wait_for > 0:
            time.sleep(wait_for)
        self._last_request = time.monotonic()

    def _run_browser(
        self, *arguments: str, timeout: int = 35
    ) -> subprocess.CompletedProcess[str]:
        if not self.enabled:
            raise RuntimeError("Z-Library 授权来源已被配置禁用")
        if not self.browser_bin:
            raise RuntimeError("缺少 agent-browser，无法完成站点浏览器校验")
        command = [
            self.browser_bin,
            "--session",
            self.browser_session,
            *arguments,
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "浏览器命令失败").strip()
            message = re.sub(r"\x1b\[[0-9;]*m", "", message)
            raise RuntimeError(message[:500])
        return result

    def _browser_json(self, *arguments: str, timeout: int = 35) -> Any:
        result = self._run_browser(*arguments, "--json", timeout=timeout)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("站点浏览器返回了无法解析的数据") from exc
        if not payload.get("success"):
            raise RuntimeError(str(payload.get("error") or "站点浏览器操作失败")[:500])
        return payload.get("data")

    def _open(self, url: str) -> None:
        self._throttle()
        navigation_error = ""
        for attempt in range(3):
            try:
                self._run_browser("open", url, timeout=45)
                navigation_error = ""
                break
            except RuntimeError as exc:
                navigation_error = str(exc)
                if "ERR_ABORTED" not in navigation_error or attempt == 2:
                    raise
                self._run_browser("wait", "1200", timeout=10)
                current = self._browser_json("get", "url", timeout=15) or {}
                if str(current.get("url") or "").startswith(url):
                    navigation_error = ""
                    break
        if navigation_error:
            raise RuntimeError(navigation_error)
        deadline = time.monotonic() + 24
        while True:
            data = self._browser_json("get", "title", timeout=15) or {}
            title = str(data.get("title") or "")
            if title and "Checking your browser" not in title:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("Z-Library 浏览器校验超时")
            self._run_browser("wait", "1500", timeout=10)

    def _eval(self, script: str) -> Any:
        data = self._browser_json("eval", script, timeout=30) or {}
        return data.get("result")

    def _ensure_authenticated(self) -> None:
        if self._authenticated:
            return
        if not self.email or not self.password:
            raise RuntimeError("Z-Library 模拟登录凭据尚未安全配置")
        self._open(self.base_url)
        account = self._eval(
            """(() => ({
              loggedIn: Boolean(
                document.querySelector(
                  'a[href*="logout"], form[action*="logout"], .user-info, .profile'
                )
              ),
              href: location.href
            }))()"""
        )
        if isinstance(account, dict) and account.get("loggedIn"):
            self._authenticated = True
            return
        self._open(urljoin(self.base_url + "/", "login.php"))
        form = self._eval(
            """(() => ({
              email: Boolean(document.querySelector(
                'input[type="email"], input[name="email"], input[name="username"]'
              )),
              password: Boolean(document.querySelector('input[type="password"]'))
            }))()"""
        )
        if not isinstance(form, dict) or not form.get("email") or not form.get("password"):
            raise RuntimeError("Z-Library 登录页被反自动化校验拦截，未提交账号")
        # Values are taken only from the root-readable service environment and
        # are never persisted in the repository or emitted to application logs.
        credentials = json.dumps(
            {"email": self.email, "password": self.password},
            ensure_ascii=False,
        )
        submitted = self._eval(
            f"""(() => {{
              const c = {credentials};
              const e = document.querySelector(
                'input[type="email"], input[name="email"], input[name="username"]'
              );
              const p = document.querySelector('input[type="password"]');
              if (!e || !p) return false;
              e.value = c.email; p.value = c.password;
              for (const node of [e,p]) {{
                node.dispatchEvent(new Event('input', {{bubbles:true}}));
                node.dispatchEvent(new Event('change', {{bubbles:true}}));
              }}
              const form = p.closest('form');
              if (form?.requestSubmit) form.requestSubmit();
              else form?.submit();
              return true;
            }})()"""
        )
        if not submitted:
            raise RuntimeError("Z-Library 登录表单提交失败")
        self._run_browser("wait", "2500", timeout=10)
        account = self._eval(
            """(() => ({
              loggedIn: Boolean(
                document.querySelector(
                  'a[href*="logout"], form[action*="logout"], .user-info, .profile'
                )
              ),
              hasPassword: Boolean(document.querySelector('input[type="password"]')),
              text: document.body?.innerText?.slice(0, 500) || ''
            }))()"""
        )
        if not isinstance(account, dict) or not account.get("loggedIn"):
            text = str(account.get("text") or "") if isinstance(account, dict) else ""
            if re.search(r"invalid|incorrect|wrong|错误|失败", text, re.I):
                raise RuntimeError("Z-Library 账号或密码校验失败")
            raise RuntimeError("Z-Library 登录未建立有效会话")
        self._authenticated = True

    @staticmethod
    def _parse_size(value: str) -> int:
        match = re.search(r"([\d.]+)\s*(B|KB|MB|GB)", value or "", re.I)
        if not match:
            return 0
        amount = float(match.group(1))
        multiplier = {
            "B": 1,
            "KB": 1024,
            "MB": 1024**2,
            "GB": 1024**3,
        }[match.group(2).upper()]
        return int(amount * multiplier)

    @staticmethod
    def _slug_from_href(value: str) -> str:
        match = re.search(r"/book/([A-Za-z0-9_-]{4,40})(?:/|$)", value or "")
        return match.group(1) if match else ""

    @staticmethod
    def validate_remote_id(value: Any) -> str:
        remote_id = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{4,40}", remote_id):
            raise ValueError("Z-Library 作品标识无效")
        return remote_id

    def search(self, query: str, *, limit: int = 0) -> List[Dict[str, Any]]:
        query = query.strip()
        if len(query) < 2:
            raise ValueError("作品名或作者名至少需要 2 个字符")
        with _BROWSER_LOCK:
            self._ensure_authenticated()
            self._open(f"{self.base_url}/s/{quote(query, safe='')}")
            rows = self._eval(
                """(() => Array.from(document.querySelectorAll('z-bookcard')).map(card => ({
                  book_id: card.getAttribute('id') || '',
                  href: card.getAttribute('href') || '',
                  download_path: card.getAttribute('download') || '',
                  deleted: card.getAttribute('deleted') || '0',
                  publisher: card.getAttribute('publisher') || '',
                  language: card.getAttribute('language') || '',
                  year: card.getAttribute('year') || '',
                  extension: (card.getAttribute('extension') || '').toLowerCase(),
                  filesize: card.getAttribute('filesize') || '',
                  rating: card.getAttribute('rating') || '',
                  quality: card.getAttribute('quality') || '',
                  title: card.querySelector('[slot="title"]')?.textContent?.trim() || '',
                  author: card.querySelector('[slot="author"]')?.textContent?.trim() || ''
                })))()"""
            )
        allowed_extensions = {"txt", "epub"}
        results: List[Dict[str, Any]] = []
        seen: set[str] = set()
        lowered = query.casefold()
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or str(row.get("deleted")) == "1":
                continue
            remote_id = self._slug_from_href(str(row.get("href") or ""))
            extension = str(row.get("extension") or "").lower()
            size_bytes = self._parse_size(str(row.get("filesize") or ""))
            if (
                not remote_id
                or remote_id in seen
                or extension not in allowed_extensions
                or (size_bytes and size_bytes > self.max_bytes)
            ):
                continue
            title = str(row.get("title") or "未命名作品").strip()
            author = str(row.get("author") or "作者未知").strip() or "作者未知"
            exact = lowered in {title.casefold(), author.casefold()}
            results.append(
                {
                    "provider": self.PROVIDER_ID,
                    "provider_label": "Z-Library 在线",
                    "remote_id": remote_id,
                    "source_ref": str(row.get("href") or ""),
                    "source_book_id": str(row.get("book_id") or ""),
                    "title": title,
                    "author": author,
                    "publisher": str(row.get("publisher") or ""),
                    "language": str(row.get("language") or ""),
                    "year": str(row.get("year") or ""),
                    "extension": extension,
                    "filesize": str(row.get("filesize") or ""),
                    "size_bytes": size_bytes,
                    "rating": str(row.get("rating") or ""),
                    "quality": str(row.get("quality") or ""),
                    "license": "站点所有者授权测试来源",
                    "authorization": "site_owner_authorized",
                    "downloadable": True,
                    "source": "remote_authorized",
                    "_rank": (
                        200 if exact else 0,
                        20 if extension == "txt" else 10,
                        float(row.get("quality") or 0),
                    ),
                }
            )
            seen.add(remote_id)
        results.sort(key=lambda item: item["_rank"], reverse=True)
        for item in results:
            item.pop("_rank", None)
        return results if limit <= 0 else results[:limit]

    @staticmethod
    def _validated_detail_path(remote_id: str, source_ref: str) -> str:
        if not source_ref:
            return f"/book/{remote_id}"
        parsed = urlparse(source_ref)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("Z-Library 详情引用必须是站内相对路径")
        prefix = f"/book/{remote_id}/"
        decoded_path = unquote(parsed.path)
        if (
            not decoded_path.startswith(prefix)
            or ".." in decoded_path
            or "\\" in decoded_path
        ):
            raise ValueError("Z-Library 详情引用与作品标识不匹配")
        return parsed.path

    def detail(
        self, remote_id: Any, source_ref: str = ""
    ) -> Dict[str, Any]:
        remote_id = self.validate_remote_id(remote_id)
        detail_path = self._validated_detail_path(remote_id, source_ref)
        with _BROWSER_LOCK:
            self._ensure_authenticated()
            self._open(urljoin(self.base_url + "/", detail_path.lstrip("/")))
            row = self._eval(
                """(() => {
                  const card = document.querySelector('.details-book');
                  const download = document.querySelector('a.addDownloadedBook');
                  const format = document.querySelector('.book-property__extension');
                  const cover = document.querySelector(
                    'img[itemprop="image"], .details-book img, .book-cover img, img.cover'
                  );
                  const categories = Array.from(document.querySelectorAll(
                    'a[href*="/category/"], a[href*="/category-books/"]'
                  )).map(a => a.textContent.trim()).filter(Boolean);
                  return {
                    book_id: card?.getAttribute('data-book_id') || '',
                    title: document.querySelector('.book-title')?.textContent?.trim() || '',
                    author: document.querySelector('.authors')?.textContent?.trim() || '',
                    download_path: download?.getAttribute('href') || '',
                    extension: format?.textContent?.trim()?.toLowerCase() || '',
                    filesize: download?.textContent?.replace(format?.textContent || '', '')?.trim()?.replace(/^,\\s*/, '') || '',
                    cover_url: cover?.currentSrc || cover?.src || '',
                    categories
                  };
                })()"""
            )
        if not isinstance(row, dict):
            raise RuntimeError("Z-Library 作品详情无法解析")
        download_path = str(row.get("download_path") or "")
        extension = str(row.get("extension") or "").lower()
        if not re.fullmatch(r"/dl/[A-Za-z0-9_-]{4,80}", download_path):
            raise RuntimeError("Z-Library 作品没有可用的授权下载入口")
        if extension not in {"txt", "epub"}:
            raise ValueError("当前仅支持导入 TXT 或 EPUB")
        size_bytes = self._parse_size(str(row.get("filesize") or ""))
        if size_bytes and size_bytes > self.max_bytes:
            raise ValueError("远程作品超过配置的安全大小上限")
        return {
            "provider": self.PROVIDER_ID,
            "remote_id": remote_id,
            "source_book_id": str(row.get("book_id") or ""),
            "title": str(row.get("title") or f"Z-Library {remote_id}"),
            "author": str(row.get("author") or "作者未知"),
            "categories": [
                str(value) for value in (row.get("categories") or []) if value
            ],
            "download_path": download_path,
            "extension": extension,
            "filesize": str(row.get("filesize") or ""),
            "size_bytes": size_bytes,
            "cover_url": urljoin(
                self.base_url + "/", str(row.get("cover_url") or "")
            ) if row.get("cover_url") else "",
            "detail_url": urljoin(
                self.base_url + "/", detail_path.lstrip("/")
            ),
        }

    def _browser_credentials(self) -> tuple[str, List[Dict[str, Any]]]:
        ua = str(self._eval("navigator.userAgent") or "")
        data = self._browser_json("cookies", timeout=20) or {}
        cookies = data.get("cookies") or []
        return ua, [item for item in cookies if isinstance(item, dict)]

    def _download_host_allowed(self, hostname: str) -> bool:
        host = (hostname or "").lower()
        return any(
            host == suffix.lstrip(".") or host.endswith(suffix)
            for suffix in self.download_host_suffixes
        )

    def download(self, detail: Dict[str, Any]) -> bytes:
        download_path = str(detail.get("download_path") or "")
        if not re.fullmatch(r"/dl/[A-Za-z0-9_-]{4,80}", download_path):
            raise ValueError("Z-Library 下载路径无效")
        with _BROWSER_LOCK:
            ua, cookies = self._browser_credentials()
        session = requests.Session()
        session.trust_env = False
        session.headers.update(
            {
                "User-Agent": ua,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "*/*",
            }
        )
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status=2,
            backoff_factor=1,
            status_forcelist=(408, 425, 429, 500, 502, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        for cookie in cookies:
            session.cookies.set(
                str(cookie.get("name") or ""),
                str(cookie.get("value") or ""),
                domain=str(
                    cookie.get("domain")
                    or urlparse(self.base_url).hostname
                    or "singlelogin.re"
                ),
                path=str(cookie.get("path") or "/"),
            )

        current_url = urljoin(self.base_url + "/", download_path.lstrip("/"))
        response: requests.Response | None = None
        for hop in range(5):
            self._throttle()
            response = session.get(
                current_url,
                stream=True,
                allow_redirects=False,
                timeout=(12, 90),
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            location = response.headers.get("Location") or ""
            response.close()
            next_url = urljoin(current_url, location)
            parsed = urlparse(next_url)
            if parsed.scheme != "https":
                raise ValueError("Z-Library 下载重定向不是 HTTPS")
            if hop == 0:
                if not self._download_host_allowed(parsed.hostname or ""):
                    raise ValueError("Z-Library 下载重定向到了未授权主机")
            elif not self._download_host_allowed(parsed.hostname or ""):
                raise ValueError("Z-Library CDN 重定向到了未授权主机")
            current_url = next_url
        if response is None:
            raise RuntimeError("Z-Library 下载没有返回响应")
        try:
            response.raise_for_status()
            content_type = str(response.headers.get("Content-Type") or "").lower()
            length = int(response.headers.get("Content-Length") or 0)
            if length and length > self.max_bytes:
                raise ValueError("远程作品超过配置的安全大小上限")
            data = bytearray()
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                data.extend(chunk)
                if len(data) > self.max_bytes:
                    raise ValueError("远程作品超过配置的安全大小上限")
            if len(data) < 128:
                raise ValueError("远程作品内容为空或过短")
            expected = int(detail.get("size_bytes") or 0)
            if expected and len(data) < expected * 0.2:
                raise ValueError(
                    "远程下载内容明显小于书目大小，可能命中限额或错误页"
                )
            extension = str(detail.get("extension") or "").lower()
            leading = bytes(data[:512]).lstrip().lower()
            if (
                "text/html" in content_type
                or leading.startswith((b"<!doctype html", b"<html"))
            ):
                raise ValueError("远程下载返回了 HTML 错误页")
            if extension == "epub" and not bytes(data).startswith(b"PK"):
                raise ValueError("远程下载结果不是有效 EPUB")
            return bytes(data)
        finally:
            response.close()
            session.close()
