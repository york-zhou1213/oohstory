"""Linovelib light-novel catalog, chapter, and cover crawler."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from oohstory_library.services.browser_recovery import shared_browser_recovery
from oohstory_library.services.library_catalog import normalize_catalog_title


class LinovelibProvider:
    """Fetch translated light novels while failing closed on partial chapters."""

    PROVIDER_ID = "linovelib"
    LIBRARY_CATEGORY = "轻小说"
    _BOOK_PATH = re.compile(r"^/novel/(\d+)\.html$")
    _VOLUME_PATH = re.compile(r"^/novel/(\d+)/vol_(\d+)\.html$")
    _CHAPTER_PATH = re.compile(r"^/novel/(\d+)/(\d+)\.html$")
    _TRUSTED_HOSTS = {
        "linovelib.com",
        "www.linovelib.com",
        "bilinovel.com",
        "www.bilinovel.com",
    }
    CATALOG_CATEGORIES = {
        1: ("dengekibunko", "电击文库"),
        2: ("fujimibunko", "富士见文库"),
        3: ("kadokawabunko", "角川文库"),
        4: ("emuefubunkojei", "MF文库J"),
        5: ("famitsubunko", "Fami通文库"),
        6: ("gagraphicbunko", "GA文库"),
        7: ("hobbyjapanbunko", "HJ文库"),
        8: ("ichijinsha", "一迅社"),
        9: ("shueisha", "集英社"),
        10: ("shogakukan", "小学馆"),
        11: ("kodansha", "讲谈社"),
        12: ("teenagebunko", "少女文库"),
        13: ("other", "其他文库"),
        14: ("chineselightnovel", "华文轻小说"),
    }
    _PARTIAL_MARKERS = (
        "內容加載失敗",
        "内容加载失败",
        "請刷新或更換瀏覽器",
        "请刷新或更换浏览器",
    )
    _ILLUSTRATION_HOST = re.compile(r"^img\d+\.readpai\.com$", re.I)
    _IMAGE_CONTENT_TYPES = {
        "image/avif": ".avif",
        "image/gif": ".gif",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }

    def __init__(self) -> None:
        self.base_url = os.getenv(
            "OOHSTORY_LIBRARY_LINOVELIB_BASE_URL", "https://www.linovelib.com"
        ).rstrip("/")
        self.catalog_base_url = os.getenv(
            "OOHSTORY_LIBRARY_LINOVELIB_CATALOG_BASE_URL",
            "https://www.linovelib.com",
        ).rstrip("/")
        for label, value in (
            ("正文", self.base_url),
            ("目录", self.catalog_base_url),
        ):
            parsed = urlparse(value)
            if (
                parsed.scheme != "https"
                or (parsed.hostname or "").lower() not in self._TRUSTED_HOSTS
            ):
                raise ValueError(f"轻小说文库{label}来源必须使用受信任 HTTPS 域名")
        self.enabled = os.getenv(
            "OOHSTORY_LIBRARY_LINOVELIB_ENABLED", "1"
        ).lower() not in {"0", "false", "no", "off"}
        self.delay = max(
            0.35, float(os.getenv("OOHSTORY_LIBRARY_LINOVELIB_DELAY", "0.8"))
        )
        self.workers = min(
            max(int(os.getenv("OOHSTORY_LIBRARY_LINOVELIB_WORKERS", "3")), 1), 4
        )
        self.max_bytes = max(
            1, int(os.getenv("OOHSTORY_LIBRARY_LINOVELIB_MAX_MB", "120"))
        ) * 1024 * 1024
        self.max_cover_bytes = max(
            1, int(os.getenv("OOHSTORY_LIBRARY_LINOVELIB_MAX_COVER_MB", "15"))
        ) * 1024 * 1024
        self.max_illustration_bytes = max(
            1, int(os.getenv("OOHSTORY_LIBRARY_LINOVELIB_MAX_ILLUSTRATION_MB", "15"))
        ) * 1024 * 1024
        self.cdp_url = os.getenv(
            "OOHSTORY_LIBRARY_LINOVELIB_CDP_URL",
            os.getenv("OOHSTORY_LIBRARY_SHUBAOW_CDP_URL", ""),
        ).strip().rstrip("/")
        self._last_request = 0.0
        self._throttle_lock = threading.Lock()
        self._thread_local = threading.local()
        self._illustration_lock = threading.Lock()
        default_volume_root = (
            Path(__file__).resolve().parents[2]
            / "electronic-library"
            / "txt80"
            / "书籍"
            / self.LIBRARY_CATEGORY
        ).resolve()
        configured_cache = os.getenv(
            "OOHSTORY_LIBRARY_LINOVELIB_CHAPTER_CACHE", ""
        ).strip()
        self.chapter_cache_root = (
            Path(configured_cache).expanduser().resolve()
            if configured_cache
            else default_volume_root
        )
        configured_illustrations = os.getenv(
            "OOHSTORY_LIBRARY_LINOVELIB_ILLUSTRATION_ROOT", ""
        ).strip()
        self.illustration_root = (
            Path(configured_illustrations).expanduser().resolve()
            if configured_illustrations
            else default_volume_root
        )

    @staticmethod
    def _new_session() -> requests.Session:
        session = requests.Session()
        session.trust_env = False
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        retry = Retry(
            total=4,
            connect=4,
            read=4,
            status=3,
            backoff_factor=0.8,
            status_forcelist=(408, 425, 429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
            respect_retry_after_header=True,
        )
        session.mount(
            "https://",
            HTTPAdapter(
                max_retries=retry,
                pool_connections=4,
                pool_maxsize=4,
            ),
        )
        return session

    def _session(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = self._new_session()
            self._thread_local.session = session
        return session

    @staticmethod
    def _clean(value: Any) -> str:
        return re.sub(
            r"\s+",
            " ",
            unicodedata.normalize("NFKC", str(value or "")),
        ).strip()

    @classmethod
    def _trusted_url(cls, value: str) -> str:
        parsed = urlparse(str(value or ""))
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() not in cls._TRUSTED_HOSTS
        ):
            raise ValueError("轻小说文库请求超出受信任 HTTPS 域名")
        return value

    def _throttle(self) -> None:
        with self._throttle_lock:
            wait_for = self.delay - (time.monotonic() - self._last_request)
            if wait_for > 0:
                time.sleep(wait_for)
            self._last_request = time.monotonic()

    @classmethod
    def _challenge(cls, response: requests.Response) -> bool:
        sample = response.text[:7000].casefold()
        return (
            response.status_code in {403, 429, 503}
            or response.headers.get("cf-mitigated", "").casefold()
            == "challenge"
            or "just a moment" in sample
            or "challenge-platform" in sample
            or "cf-chl-" in sample
        )

    def _browser_request(self, url: str, *, binary: bool = False) -> requests.Response:
        if not self.cdp_url:
            raise RuntimeError("轻小说文库浏览器回退未配置 CDP 会话")
        tool = (
            Path(__file__).resolve().parents[2]
            / "scripts"
            / "electronic-library"
            / "linovelib_browser_fetch.mjs"
        )
        env = os.environ.copy()
        env["OOHSTORY_LIBRARY_LINOVELIB_CDP_URL"] = self.cdp_url
        if binary:
            env["OOHSTORY_LIBRARY_LINOVELIB_FETCH_BINARY"] = "1"
        def fetch() -> requests.Response:
            completed = subprocess.run(
                ["node", str(tool), url],
                cwd=Path(__file__).resolve().parents[2],
                env=env,
                capture_output=True,
                text=True,
                timeout=75,
                check=False,
            )
            if completed.returncode:
                detail = (
                    completed.stderr or "轻小说文库浏览器请求失败"
                ).strip()
                raise RuntimeError(detail[-500:])
            try:
                payload = json.loads(completed.stdout)
                response = requests.Response()
                response.status_code = int(payload.get("status") or 200)
                response.url = str(payload.get("url") or url)
                response.headers["Content-Type"] = str(
                    payload.get("contentType") or ""
                )
                response._content = (
                    base64.b64decode(
                        str(payload.get("bodyBase64") or ""), validate=True
                    )
                    if binary
                    else str(payload.get("body") or "").encode("utf-8")
                )
            except (TypeError, ValueError) as exc:
                raise RuntimeError("轻小说文库浏览器返回了无效响应") from exc
            response.encoding = "utf-8"
            return response

        return shared_browser_recovery.call(self.cdp_url, fetch)

    def _request(
        self,
        path: str,
        *,
        base_url: str | None = None,
        params: Dict[str, Any] | None = None,
    ) -> requests.Response:
        if not self.enabled:
            raise RuntimeError("轻小说文库来源已禁用")
        parsed = urlparse(str(path or ""))
        url = (
            str(path)
            if parsed.scheme or parsed.netloc
            else urljoin((base_url or self.base_url) + "/", str(path).lstrip("/"))
        )
        self._trusted_url(url)
        self._throttle()
        response = self._session().get(url, params=params, timeout=(10, 60))
        self._trusted_url(response.url)
        response.encoding = response.apparent_encoding or "utf-8"
        if self._challenge(response):
            response = self._browser_request(response.url or url)
        response.raise_for_status()
        return response

    def availability(self) -> Dict[str, Any]:
        return {
            "id": self.PROVIDER_ID,
            "name": "哔哩轻小说 / Linovelib",
            "enabled": self.enabled,
            "base_url": self.base_url,
            "catalog_base_url": self.catalog_base_url,
            "authorization": "user_configured_remote_source",
            "category": self.LIBRARY_CATEGORY,
            "download_mode": "crawl_all_chapters_cover_and_merge_txt",
            "max_download_mb": self.max_bytes // (1024 * 1024),
            "workers": self.workers,
            "browser_transport_configured": bool(self.cdp_url),
        }

    @classmethod
    def validate_source_ref(cls, remote_id: Any, source_ref: str) -> str:
        remote_id = str(remote_id or "").strip()
        parsed = urlparse(str(source_ref or f"/novel/{remote_id}.html"))
        if (
            not remote_id.isdigit()
            or parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or ".." in parsed.path
            or "\\" in parsed.path
        ):
            raise ValueError("轻小说文库作品引用无效")
        match = cls._BOOK_PATH.fullmatch(parsed.path)
        if not match or match.group(1) != remote_id:
            raise ValueError("轻小说文库详情引用与作品标识不匹配")
        return parsed.path

    @classmethod
    def _book_ref(cls, value: str) -> tuple[str, str] | None:
        path = urlparse(str(value or "")).path
        match = cls._BOOK_PATH.fullmatch(path)
        return (match.group(1), path) if match else None

    def _catalog_items(
        self, soup: BeautifulSoup, *, source_category: str
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for node in soup.select(".book-li, .mind-book, .bookbox"):
            anchor = node.select_one(
                ".bookname a[href], a.book-layout[href], a[href]"
            )
            reference = self._book_ref(str(anchor.get("href") or "")) if anchor else None
            if not reference or reference[0] in seen:
                continue
            remote_id, source_ref = reference
            title_node = node.select_one(".book-title, .bookname")
            author_node = node.select_one(".book-author, .author")
            metadata_nodes = node.select(".bookilnk span")
            status_nodes = [*node.select(".tag-small"), *metadata_nodes]
            title = normalize_catalog_title(
                self._clean(
                    title_node.get_text(" ", strip=True)
                    if title_node
                    else anchor.get_text(" ", strip=True)
                )
            )
            if not title:
                continue
            author = self._clean(
                author_node.get_text(" ", strip=True)
                if author_node
                else (
                    metadata_nodes[0].get_text(" ", strip=True)
                    if metadata_nodes
                    else ""
                )
            )
            author = re.sub(r"^作者\s*", "", author).strip() or "作者未知"
            status_values = [
                self._clean(item.get_text(" ", strip=True))
                for item in status_nodes
            ]
            book_status = next(
                (value for value in status_values if value in {"连载", "完结"}),
                "",
            )
            remote_updated_at = next(
                (
                    value
                    for value in status_values
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?", value)
                ),
                "",
            )
            seen.add(remote_id)
            items.append(
                {
                    "provider": self.PROVIDER_ID,
                    "provider_label": "哔哩轻小说全章节",
                    "remote_id": remote_id,
                    "source_ref": source_ref,
                    "title": title,
                    "author": author,
                    "category": self.LIBRARY_CATEGORY,
                    "source_category": source_category,
                    "book_status": book_status,
                    "extension": "txt",
                    "downloadable": True,
                    "download_mode": "按分卷抓取章节和插画，并合并总文件 TXT",
                    "source": "remote_configured",
                    "authorization": "user_configured_remote_source",
                    # 文库更新列表给出的日期先作为低成本候选版本；已入库
                    # 作品会再读取详情页的 latest_chapter 元数据，组成精确
                    # 修订号，避免同一天多次更新被漏掉。
                    "remote_revision": remote_updated_at,
                    "remote_updated_at": remote_updated_at,
                }
            )
        return items

    def latest_updates(self, page: int = 1) -> List[Dict[str, Any]]:
        """Return the Wenku update feed in newest-first order."""

        page = max(int(page), 1)
        response = self._request(
            f"/wenku/lastupdate_0_0_0_0_0_0_0_{page}_0.html",
            base_url=self.catalog_base_url,
        )
        return self._catalog_items(
            BeautifulSoup(response.text, "html.parser"),
            source_category=self.LIBRARY_CATEGORY,
        )

    def probe_update(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve a feed row to an exact latest-chapter revision."""

        detail = self.detail(
            item.get("remote_id"),
            str(item.get("source_ref") or ""),
            include_chapters=False,
        )
        latest_path = urlparse(
            str(detail.get("latest_chapter_url") or "")
        ).path
        latest_title = self._clean(detail.get("latest_chapter"))
        updated_at = self._clean(
            detail.get("remote_updated_at")
            or item.get("remote_updated_at")
        )
        revision = latest_path or f"{updated_at}|{latest_title}"
        return {
            **item,
            "book_status": str(
                detail.get("book_status") or item.get("book_status") or ""
            ),
            "remote_updated_at": updated_at,
            "remote_latest_chapter": latest_title,
            "remote_revision": revision,
        }

    def catalog_page(self, category: int, page: int) -> List[Dict[str, Any]]:
        category = min(max(int(category), 1), len(self.CATALOG_CATEGORIES))
        page = max(int(page), 1)
        slug, source_category = self.CATALOG_CATEGORIES[category]
        response = self._request(
            f"/wenku/{slug}/{page}.html",
            base_url=self.catalog_base_url,
        )
        return self._catalog_items(
            BeautifulSoup(response.text, "html.parser"),
            source_category=source_category,
        )

    def search(self, query: str, *, limit: int = 0) -> List[Dict[str, Any]]:
        """Search a bounded set of newest catalog pages when remote search is empty."""
        query = self._clean(query).strip("《》")
        if len(query) < 2:
            raise ValueError("作品名或作者名至少需要 2 个字符")
        lowered = query.casefold()
        matches: List[Dict[str, Any]] = []
        seen: set[str] = set()
        # The public search endpoint intentionally returns an empty body to
        # non-interactive clients. Keep remote search to one ranking page;
        # full discovery and durable searching remain cursor/catalog driven.
        response = self._request(
            "/topfull/allvisit/1.html", base_url=self.catalog_base_url
        )
        for item in self._catalog_items(
            BeautifulSoup(response.text, "html.parser"),
            source_category="轻小说",
        ):
            remote_id = str(item.get("remote_id") or "")
            if remote_id in seen:
                continue
            title = str(item.get("title") or "").casefold()
            author = str(item.get("author") or "").casefold()
            if lowered not in title and lowered not in author:
                continue
            seen.add(remote_id)
            matches.append(item)
            if limit > 0 and len(matches) >= limit:
                break
        return matches

    @classmethod
    def _trusted_illustration_url(cls, value: str) -> str:
        parsed = urlparse(str(value or ""))
        if (
            parsed.scheme != "https"
            or not cls._ILLUSTRATION_HOST.fullmatch(
                (parsed.hostname or "").lower()
            )
            or parsed.username
            or parsed.password
        ):
            raise ValueError("轻小说插图请求超出受信任图片域名")
        return value

    @classmethod
    def _section_container(cls, heading: Any, names: set[str]) -> Any | None:
        """Return the first approved container before the next section heading."""
        parent = getattr(heading, "parent", None)
        parent_classes = set(parent.get("class") or []) if parent else set()
        if parent_classes & names:
            return parent
        for node in heading.next_elements:
            if node is heading or not getattr(node, "name", None):
                continue
            if node.name in {"h2", "h3", "h4", "h5"}:
                break
            if set(node.get("class") or []) & names:
                return node
        return None

    @classmethod
    def _chapter_items(
        cls,
        soup: BeautifulSoup,
        remote_id: str,
        *,
        allow_unlabelled: bool = False,
    ) -> List[Dict[str, str]]:
        container_names = {
            "book-new-chapter",
            "chapter-list",
            "chapter-li",
            "chapter-ol",
        }
        directory: List[Any] = []
        latest: List[Any] = []
        for heading in soup.select("h2, h3, h4, h5"):
            label = cls._clean(heading.get_text(" ", strip=True))
            target = cls._section_container(heading, container_names)
            if target is None:
                continue
            if "目录" in label:
                directory.append(target)
            elif "最新章节" in label or label in {"章节", "最新章"}:
                latest.append(target)
        containers = directory or latest
        if not containers and allow_unlabelled:
            containers = list(
                soup.select(
                    ".book-new-chapter, .chapter-list, .chapter-li, .chapter-ol"
                )
            )
        items: List[Dict[str, str]] = []
        seen: set[str] = set()
        for container in containers:
            for anchor in container.select("a[href]"):
                path = urlparse(str(anchor.get("href") or "")).path
                match = cls._CHAPTER_PATH.fullmatch(path)
                if (
                    not match
                    or match.group(1) != remote_id
                    or path in seen
                ):
                    continue
                title = cls._clean(anchor.get_text(" ", strip=True))
                if not title:
                    continue
                seen.add(path)
                items.append({"title": title, "path": path})
        return items

    def _volume_refs(
        self, soup: BeautifulSoup, remote_id: str
    ) -> List[Dict[str, str]]:
        newest_first: List[Dict[str, str]] = []
        seen: set[str] = set()
        for anchor in soup.select(".book-vol-chapter a[href]"):
            path = urlparse(str(anchor.get("href") or "")).path
            match = self._VOLUME_PATH.fullmatch(path)
            if (
                not match
                or match.group(1) != remote_id
                or path in seen
            ):
                continue
            cover_url = ""
            styled = anchor.select_one('[style*="background-image"]')
            style = str((styled or anchor).get("style") or "")
            match_cover = re.search(
                r"background-image\s*:\s*url\(\s*(['\"]?)(.*?)\1\s*\)",
                style,
                flags=re.I,
            )
            if match_cover:
                candidate = urljoin(
                    self.base_url + "/", match_cover.group(2).strip()
                )
                try:
                    cover_url = self._trusted_volume_cover_url(candidate)
                except ValueError:
                    cover_url = ""
            seen.add(path)
            newest_first.append(
                {
                    "title": self._clean(
                        anchor.get("title")
                        or anchor.get_text(" ", strip=True)
                    ),
                    "path": path,
                    "cover_url": cover_url,
                }
            )
        # The work page is newest-to-oldest; a merged book must be readable.
        return list(reversed(newest_first))

    @classmethod
    def _trusted_volume_cover_url(cls, value: str) -> str:
        parsed = urlparse(str(value or ""))
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or (
                host not in cls._TRUSTED_HOSTS
                and not cls._ILLUSTRATION_HOST.fullmatch(host)
            )
            or parsed.username
            or parsed.password
        ):
            raise ValueError("轻小说分卷封面请求超出受信任图片域名")
        return value

    def _volume_illustrations(
        self, soup: BeautifulSoup, page_url: str
    ) -> List[str]:
        containers: List[Any] = []
        for heading in soup.select("h2, h3, h4, h5"):
            label = self._clean(heading.get_text(" ", strip=True))
            if "插画" not in label and "插圖" not in label:
                continue
            target = self._section_container(heading, {"volumeimages"})
            if target is not None:
                containers.append(target)
        if not containers:
            # Some legacy single-volume pages omit the heading but retain the
            # dedicated image container. It is still narrow enough to be safe.
            containers = list(soup.select(".volumeimages"))
        urls: List[str] = []
        seen: set[str] = set()
        for container in containers:
            for image in container.select(
                "img.imagecontent, img[data-src], img[data-original], img[src]"
            ):
                raw = next(
                    (
                        str(image.get(name) or "").strip()
                        for name in ("data-src", "data-original", "src")
                        if str(image.get(name) or "").strip()
                    ),
                    "",
                )
                url = urljoin(page_url, raw)
                if not url or url in seen:
                    continue
                try:
                    self._trusted_illustration_url(url)
                except ValueError:
                    continue
                seen.add(url)
                urls.append(url)
        return urls

    def detail(
        self,
        remote_id: Any,
        source_ref: str,
        *,
        include_chapters: bool = True,
    ) -> Dict[str, Any]:
        source_ref = self.validate_source_ref(remote_id, source_ref)
        response = self._request(source_ref)
        soup = BeautifulSoup(response.text, "html.parser")

        def meta(name: str) -> str:
            node = soup.select_one(f'meta[property="{name}"]')
            return self._clean(node.get("content") if node else "")

        title = normalize_catalog_title(
            meta("og:novel:book_name") or meta("og:title")
        )
        author = meta("og:novel:author") or "作者未知"
        source_category = meta("og:novel:category") or "轻小说"
        source_tags = meta("og:novel:tags")
        cover_url = urljoin(response.url, meta("og:image")) if meta("og:image") else ""
        chapters: List[Dict[str, Any]] = []
        volumes: List[Dict[str, Any]] = []
        illustrations: List[Dict[str, Any]] = []
        volume_covers: List[Dict[str, Any]] = []
        if include_chapters:
            remote_id_text = str(remote_id)
            volume_refs = self._volume_refs(soup, remote_id_text)
            seen_chapters: set[str] = set()
            if volume_refs:
                # Intentionally serial. The site rate-limits even four volume
                # page requests in parallel; _request also applies throttling.
                for volume_index, volume_ref in enumerate(volume_refs, 1):
                    volume_response = self._request(volume_ref["path"])
                    volume_soup = BeautifulSoup(
                        volume_response.text, "html.parser"
                    )
                    volume_title = volume_ref["title"] or f"第{volume_index}卷"
                    volume_url = volume_response.url
                    volume_chapters: List[Dict[str, Any]] = []
                    for item in self._chapter_items(
                        volume_soup, remote_id_text
                    ):
                        if item["path"] in seen_chapters:
                            continue
                        seen_chapters.add(item["path"])
                        chapter = {
                            **item,
                            "volume_index": volume_index,
                            "volume_title": volume_title,
                            "volume_path": volume_ref["path"],
                            "volume_url": volume_url,
                        }
                        chapters.append(chapter)
                        volume_chapters.append(chapter)
                    volume_images = self._volume_illustrations(
                        volume_soup, volume_url
                    )
                    volume_illustrations = [
                        {
                            "url": url,
                            "referer": volume_url,
                            "volume_index": volume_index,
                            "volume_title": volume_title,
                        }
                        for url in volume_images
                    ]
                    illustrations.extend(volume_illustrations)
                    volume_cover = None
                    if volume_ref.get("cover_url"):
                        volume_cover = {
                            "url": volume_ref["cover_url"],
                            "referer": response.url,
                            "volume_index": volume_index,
                            "volume_title": volume_title,
                        }
                        volume_covers.append(volume_cover)
                    volumes.append(
                        {
                            "index": volume_index,
                            "title": volume_title,
                            "path": volume_ref["path"],
                            "chapter_count": len(volume_chapters),
                            "illustration_count": len(volume_illustrations),
                            "illustrations": volume_illustrations,
                            "cover_url": str(
                                volume_ref.get("cover_url") or ""
                            ),
                            "cover": volume_cover,
                        }
                    )
            else:
                # Legacy/single-volume works expose numeric chapter links on
                # the work page itself. Never depend on the often-stalling
                # /catalog endpoint.
                flat_chapters = self._chapter_items(
                    soup, remote_id_text, allow_unlabelled=True
                )
                volume_title = "正文"
                for item in flat_chapters:
                    chapters.append(
                        {
                            **item,
                            "volume_index": 1,
                            "volume_title": volume_title,
                            "volume_path": source_ref,
                            "volume_url": response.url,
                        }
                    )
                volume_images = self._volume_illustrations(soup, response.url)
                volume_illustrations = [
                    {
                        "url": url,
                        "referer": response.url,
                        "volume_index": 1,
                        "volume_title": volume_title,
                    }
                    for url in volume_images
                ]
                illustrations.extend(volume_illustrations)
                if flat_chapters:
                    volumes.append(
                        {
                            "index": 1,
                            "title": volume_title,
                            "path": source_ref,
                            "chapter_count": len(flat_chapters),
                            "illustration_count": len(volume_illustrations),
                            "illustrations": volume_illustrations,
                        }
                    )
            if not chapters:
                raise RuntimeError("轻小说文库作品没有可抓取的章节")
        return {
            "provider": self.PROVIDER_ID,
            "remote_id": str(remote_id),
            "source_book_id": str(remote_id),
            "source_ref": source_ref,
            "title": title or f"轻小说文库 {remote_id}",
            "author": author,
            "categories": [self.LIBRARY_CATEGORY, source_category, source_tags],
            "category": self.LIBRARY_CATEGORY,
            "source_category": source_category,
            "source_tags": source_tags,
            "book_status": meta("og:novel:status"),
            "remote_updated_at": meta("og:novel:update_time"),
            "latest_chapter": meta("og:novel:latest_chapter_name"),
            "latest_chapter_url": meta("og:novel:latest_chapter_url"),
            "summary": meta("og:description"),
            "cover_url": cover_url,
            "extension": "txt",
            "detail_url": urljoin(self.base_url + "/", source_ref.lstrip("/")),
            "chapter_count": len(chapters),
            "chapters": chapters,
            "volume_count": len(volumes),
            "volumes": volumes,
            "illustration_count": len(illustrations),
            "illustrations": illustrations,
            "illustration_paths": [],
            "volume_cover_count": len(volume_covers),
            "volume_covers": volume_covers,
            "volume_cover_paths": [],
        }

    @classmethod
    def _chapter_payload(
        cls, html: str, page_url: str
    ) -> tuple[str, List[str]]:
        soup = BeautifulSoup(html, "html.parser")
        article = soup.select_one(
            "#TextContent, #acontent, .TextContent, .contente"
        )
        if not article:
            return "", []
        image_nodes = list(
            article.select("img[src], img[data-src], img[data-original]")
        )
        # Illustration-only chapters may keep extra lazy images outside the
        # visible article while still binding them to this chapter.
        image_nodes.extend(
            soup.select(
                "#hidden-images img[src], #hidden-images img[data-src], "
                "#hidden-images img[data-original], "
                ".hidden-images img[src], .hidden-images img[data-src], "
                ".hidden-images img[data-original]"
            )
        )
        image_urls: List[str] = []
        seen_images: set[str] = set()
        for image in image_nodes:
            raw = next(
                (
                    str(image.get(name) or "").strip()
                    for name in ("data-src", "data-original", "src")
                    if str(image.get(name) or "").strip()
                ),
                "",
            )
            url = urljoin(page_url, raw)
            if url and url not in seen_images:
                seen_images.add(url)
                image_urls.append(url)
        for node in article.select("script, style, iframe, .adsbygoogle"):
            node.decompose()
        content = unicodedata.normalize(
            "NFKC", article.get_text("\n", strip=True)
        ).replace("\xa0", " ")
        content = re.sub(r"[ \t]+\n", "\n", content)
        content = re.sub(r"\n{3,}", "\n\n", content).strip()
        return content, image_urls

    @classmethod
    def _chapter_content(cls, html: str) -> tuple[str, int]:
        content, image_urls = cls._chapter_payload(
            html, "https://www.linovelib.com/"
        )
        return content, len(image_urls)

    @staticmethod
    def _safe_component(value: Any, fallback: str) -> str:
        cleaned = re.sub(r"[^\w\u3400-\u9fff-]+", "-", str(value or ""))
        return cleaned.strip("-_")[:60] or fallback

    def _book_directory(self, root: Path, detail: Dict[str, Any]) -> Path:
        remote_id = str(detail.get("remote_id") or "").strip()
        if not remote_id.isdigit():
            raise ValueError("轻小说分卷目录缺少有效作品标识")
        title = self._safe_component(detail.get("title"), f"linovelib-{remote_id}")
        author = self._safe_component(detail.get("author"), "作者未知")
        directory = root / f"{title}__{author}__linovelib-{remote_id}"
        resolved = directory.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("轻小说分卷目录越界")
        return resolved

    def book_directory(self, detail: Dict[str, Any]) -> Path:
        """Return the persistent directory holding the merged file and volumes."""

        return self._book_directory(self.chapter_cache_root, detail)

    def _volume_directory(
        self,
        root: Path,
        *,
        remote_id: str,
        book_title: str,
        book_author: str,
        volume_index: int,
        volume_title: str,
    ) -> Path:
        book_directory = self._book_directory(
            root,
            {
                "remote_id": remote_id,
                "title": book_title,
                "author": book_author,
            },
        )
        volume_name = self._safe_component(
            volume_title, f"volume-{volume_index:03d}"
        )
        directory = book_directory / f"{volume_index:03d}-{volume_name}"
        resolved = directory.resolve()
        if not resolved.is_relative_to(book_directory):
            raise ValueError("轻小说分卷子目录越界")
        return resolved

    def _illustration_directory(
        self,
        remote_id: str,
        volume_index: int,
        volume_title: str,
        book_title: str = "",
        book_author: str = "",
    ) -> Path:
        directory = self._volume_directory(
            self.illustration_root,
            remote_id=remote_id,
            book_title=book_title,
            book_author=book_author,
            volume_index=volume_index,
            volume_title=volume_title,
        )
        return directory / "插画"

    def _volume_cover_directory(
        self,
        remote_id: str,
        volume_index: int,
        volume_title: str,
        book_title: str = "",
        book_author: str = "",
    ) -> Path:
        directory = self._volume_directory(
            self.illustration_root,
            remote_id=remote_id,
            book_title=book_title,
            book_author=book_author,
            volume_index=volume_index,
            volume_title=volume_title,
        )
        return directory / "封面"

    def _download_image_asset(
        self,
        url: str,
        *,
        referer: str,
        directory: Path,
        max_bytes: int,
        asset_label: str,
        validate_url: Any,
    ) -> str:
        url = validate_url(url)
        referer = self._trusted_url(referer)
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        with self._illustration_lock:
            directory.mkdir(parents=True, exist_ok=True)
            for extension in self._IMAGE_CONTENT_TYPES.values():
                existing = directory / f"{digest}{extension}"
                try:
                    size = existing.stat().st_size
                except OSError:
                    continue
                if 0 < size <= max_bytes:
                    return existing.relative_to(self.illustration_root).as_posix()

            self._throttle()
            response = self._session().get(
                url,
                headers={
                    "Referer": referer,
                    "Accept": "image/avif,image/webp,image/*",
                },
                timeout=(10, 60),
                stream=True,
            )
            validate_url(response.url or url)
            response.raise_for_status()
            content_type = str(response.headers.get("Content-Type") or "")
            mime = content_type.split(";", 1)[0].strip().lower()
            extension = self._IMAGE_CONTENT_TYPES.get(mime)
            if not extension:
                response.close()
                raise ValueError(f"{asset_label}响应不是受支持的图片格式")
            content_length = str(response.headers.get("Content-Length") or "")
            if content_length.isdigit() and int(content_length) > max_bytes:
                response.close()
                raise ValueError(f"{asset_label}超过安全大小上限")
            destination = directory / f"{digest}{extension}"
            temporary = directory / f".{digest}.{threading.get_ident()}.part"
            total = 0
            try:
                with temporary.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError(f"{asset_label}超过安全大小上限")
                        output.write(chunk)
                if total <= 0:
                    raise ValueError(f"{asset_label}为空")
                os.replace(temporary, destination)
            finally:
                response.close()
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
            return destination.relative_to(self.illustration_root).as_posix()

    def _download_illustration(
        self,
        url: str,
        *,
        referer: str,
        remote_id: str,
        volume_index: int,
        volume_title: str,
        book_title: str = "",
        book_author: str = "",
    ) -> str:
        directory = self._illustration_directory(
            remote_id,
            volume_index,
            volume_title,
            book_title,
            book_author,
        )
        return self._download_image_asset(
            url,
            referer=referer,
            directory=directory,
            max_bytes=self.max_illustration_bytes,
            asset_label="轻小说插图",
            validate_url=self._trusted_illustration_url,
        )

    def _download_volume_cover(
        self,
        url: str,
        *,
        referer: str,
        remote_id: str,
        volume_index: int,
        volume_title: str,
        book_title: str = "",
        book_author: str = "",
    ) -> str:
        directory = self._volume_cover_directory(
            remote_id,
            volume_index,
            volume_title,
            book_title,
            book_author,
        )
        return self._download_image_asset(
            url,
            referer=referer,
            directory=directory,
            max_bytes=self.max_cover_bytes,
            asset_label="轻小说分卷封面",
            validate_url=self._trusted_volume_cover_url,
        )

    @staticmethod
    def _illustration_markers(content: str) -> List[str]:
        return re.findall(r"\[本地插图：([^\]\r\n]+)\]", content)

    @staticmethod
    def _is_file_with_transient_retry(path: Path) -> bool:
        for attempt in range(5):
            try:
                return path.is_file()
            except BlockingIOError:
                if attempt >= 4:
                    return False
                time.sleep(0.05 * (attempt + 1))
            except OSError:
                return False
        return False

    def _cached_content_complete(self, content: str) -> bool:
        for relative in self._illustration_markers(content):
            candidate = (self.illustration_root / relative).resolve()
            if (
                not candidate.is_relative_to(self.illustration_root)
                or not self._is_file_with_transient_retry(candidate)
            ):
                return False
        return True

    def _chapter_cache_path(
        self, cache_dir: Path, index: int, chapter: Dict[str, Any]
    ) -> Path:
        path = str(chapter.get("path") or "")
        match = self._CHAPTER_PATH.fullmatch(urlparse(path).path)
        chapter_id = match.group(2) if match else "unknown"
        title = self._safe_component(chapter.get("title"), "untitled")
        signature = hashlib.sha256(
            f"{path}\0{chapter.get('title') or ''}\0{chapter.get('volume_title') or ''}".encode(
                "utf-8"
            )
        ).hexdigest()[:10]
        volume_directory = self._volume_directory(
            self.chapter_cache_root,
            remote_id=str(chapter.get("remote_id") or ""),
            book_title=str(chapter.get("book_title") or ""),
            book_author=str(chapter.get("book_author") or ""),
            volume_index=int(chapter.get("volume_index") or 1),
            volume_title=str(chapter.get("volume_title") or "正文"),
        )
        expected_book_directory = cache_dir.resolve()
        if not volume_directory.is_relative_to(expected_book_directory):
            raise ValueError("轻小说章节目录与作品总目录不一致")
        return volume_directory / "章节" / (
            f"{index + 1:07d}-{chapter_id}-{title}-{signature}.txt"
        )

    def _download_chapter(
        self, index: int, chapter: Dict[str, Any]
    ) -> tuple[int, str, str]:
        last_error = ""
        for attempt in range(1, 5):
            try:
                response = self._request(chapter["path"])
                content, image_urls = self._chapter_payload(
                    response.text, response.url
                )
                if any(marker in content for marker in self._PARTIAL_MARKERS):
                    browser = self._browser_request(
                        urljoin(self.base_url + "/", chapter["path"].lstrip("/"))
                    )
                    content, image_urls = self._chapter_payload(
                        browser.text, browser.url
                    )
                if any(marker in content for marker in self._PARTIAL_MARKERS):
                    raise RuntimeError("浏览器返回的章节正文仍不完整")
                image_paths: List[str] = []
                for image_url in image_urls:
                    image_paths.append(
                        self._download_illustration(
                            image_url,
                            referer=str(
                                chapter.get("volume_url") or response.url
                            ),
                            remote_id=str(chapter.get("remote_id") or ""),
                            volume_index=int(chapter.get("volume_index") or 1),
                            volume_title=str(
                                chapter.get("volume_title") or "正文"
                            ),
                            book_title=str(chapter.get("book_title") or ""),
                            book_author=str(chapter.get("book_author") or ""),
                        )
                    )
                image_paths = list(dict.fromkeys(image_paths))
                if len(content) < 10 and image_paths:
                    content = f"[插图页：共 {len(image_paths)} 张插图]"
                if len(content) < 10:
                    raise RuntimeError("正文为空或过短")
                if image_paths:
                    markers = "\n".join(
                        f"[本地插图：{path}]" for path in image_paths
                    )
                    content = f"{content}\n\n{markers}"
                return index, chapter["title"], content
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < 4:
                    time.sleep(min(0.5 * attempt, 1.5))
        raise RuntimeError(
            f"第 {index + 1} 个章节连续 4 次抓取失败：{last_error}"
        )

    def download(self, detail: Dict[str, Any]) -> bytes:
        chapters = list(detail.get("chapters") or [])
        remote_id = str(detail.get("remote_id") or "").strip()
        if not chapters or not remote_id.isdigit():
            raise ValueError("轻小说文库详情缺少有效章节目录")
        cache_dir = self.book_directory(detail)
        cache_dir.mkdir(parents=True, exist_ok=True)
        normalized_chapters: List[Dict[str, Any]] = []
        for chapter in chapters:
            normalized_chapters.append(
                {
                    **chapter,
                    "remote_id": remote_id,
                    "book_title": str(detail.get("title") or ""),
                    "book_author": str(detail.get("author") or ""),
                }
            )
        downloaded: List[tuple[str, str] | None] = [None] * len(chapters)
        pending: List[tuple[int, Dict[str, Any], Path]] = []
        for index, chapter in enumerate(normalized_chapters):
            cache_path = self._chapter_cache_path(cache_dir, index, chapter)
            try:
                cached = cache_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                cached = ""
            if len(cached.strip()) >= 10 and self._cached_content_complete(cached):
                downloaded[index] = (chapter["title"], cached.strip())
            else:
                pending.append((index, chapter, cache_path))
        errors: List[str] = []
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {
                executor.submit(self._download_chapter, index, chapter): (
                    index,
                    cache_path,
                )
                for index, chapter, cache_path in pending
            }
            for future in as_completed(futures):
                index, cache_path = futures[future]
                try:
                    _, title, content = future.result()
                    downloaded[index] = (title, content)
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = cache_path.with_suffix(".txt.tmp")
                    temporary.write_text(content, encoding="utf-8")
                    os.replace(temporary, cache_path)
                except Exception as exc:
                    errors.append(
                        f"章节 {index + 1}: {type(exc).__name__}: {str(exc)[:160]}"
                    )
        if errors or any(item is None for item in downloaded):
            raise RuntimeError(
                "轻小说文库全章节抓取不完整，已取消入库："
                + "；".join(errors[:8])
            )
        volume_cover_paths: Dict[int, str] = {}
        for item in list(detail.get("volume_covers") or []):
            volume_index = int(item.get("volume_index") or 1)
            volume_cover_paths[volume_index] = self._download_volume_cover(
                str(item.get("url") or ""),
                referer=str(
                    item.get("referer") or detail.get("detail_url") or ""
                ),
                remote_id=remote_id,
                volume_index=volume_index,
                volume_title=str(item.get("volume_title") or "正文"),
                book_title=str(detail.get("title") or ""),
                book_author=str(detail.get("author") or ""),
            )

        volume_image_paths: Dict[int, List[str]] = {}
        for item in list(detail.get("illustrations") or []):
            volume_index = int(item.get("volume_index") or 1)
            relative = self._download_illustration(
                str(item.get("url") or ""),
                referer=str(item.get("referer") or detail.get("detail_url") or ""),
                remote_id=remote_id,
                volume_index=volume_index,
                volume_title=str(item.get("volume_title") or "正文"),
                book_title=str(detail.get("title") or ""),
                book_author=str(detail.get("author") or ""),
            )
            volume_image_paths.setdefault(volume_index, []).append(relative)
        for volume_index, paths in volume_image_paths.items():
            volume_image_paths[volume_index] = list(dict.fromkeys(paths))

        all_image_paths: List[str] = []
        for paths in volume_image_paths.values():
            all_image_paths.extend(paths)
        for item in downloaded:
            assert item is not None
            all_image_paths.extend(self._illustration_markers(item[1]))
        all_image_paths = list(dict.fromkeys(all_image_paths))
        detail["illustration_paths"] = all_image_paths
        detail["illustration_count"] = len(all_image_paths)
        detail["volume_cover_paths"] = list(volume_cover_paths.values())
        detail["volume_cover_count"] = len(volume_cover_paths)
        detail["book_directory"] = str(cache_dir)

        header = [
            str(detail.get("title") or "未命名作品"),
            f"作者：{detail.get('author') or '作者未知'}",
            f"分类：{self.LIBRARY_CATEGORY}",
            f"来源文库：{detail.get('source_category') or '未标注'}",
            f"状态：{detail.get('book_status') or '未知'}",
            f"章节数：{len(downloaded)}",
            f"分卷数：{int(detail.get('volume_count') or 1)}",
            f"分卷封面数：{len(volume_cover_paths)}",
            f"插图数：{len(all_image_paths)}",
        ]
        summary = str(detail.get("summary") or "").strip()
        if summary:
            header.extend(["简介：", summary])
        parts = ["\n".join(header)]
        active_volume: int | None = None
        for chapter, downloaded_item in zip(normalized_chapters, downloaded):
            assert downloaded_item is not None
            title, content = downloaded_item
            volume_index = int(chapter.get("volume_index") or 1)
            if volume_index != active_volume:
                active_volume = volume_index
                parts.append(
                    f"【{chapter.get('volume_title') or f'第{volume_index}卷'}】"
                )
                volume_cover = volume_cover_paths.get(volume_index)
                if volume_cover:
                    parts.append(
                        f"分卷封面\n[本地分卷封面：{volume_cover}]"
                    )
                volume_paths = volume_image_paths.get(volume_index, [])
                if volume_paths:
                    parts.append(
                        "插画\n"
                        + "\n".join(
                            f"[本地插图：{path}]" for path in volume_paths
                        )
                    )
            parts.append(f"{title}\n{content}")
        data = ("\n\n".join(parts).strip() + "\n").encode("utf-8")
        if len(data) > self.max_bytes:
            raise ValueError("轻小说文库合并后的作品超过安全大小上限")
        return data

    def download_cover(self, cover_url: str) -> tuple[bytes, str]:
        self._trusted_url(str(cover_url or ""))
        self._throttle()
        response = self._session().get(cover_url, timeout=(10, 45))
        self._trusted_url(response.url)
        if self._challenge(response):
            response = self._browser_request(response.url or cover_url, binary=True)
        response.raise_for_status()
        data = response.content
        if not data or len(data) > self.max_cover_bytes:
            raise ValueError("轻小说文库封面为空或超过安全大小上限")
        return data, str(response.headers.get("Content-Type") or "")
