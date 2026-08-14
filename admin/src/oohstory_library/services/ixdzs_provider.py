"""Site-owner-authorized ixdzs8.com catalog and ZIP/TXT adapter."""

from __future__ import annotations

import io
import os
import re
import threading
import time
import unicodedata
import zipfile
from pathlib import PurePosixPath
from typing import Any, Dict, List
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from oohstory_library.services.library_catalog import normalize_catalog_title


class AuthorizedIxdzsProvider:
    PROVIDER_ID = "authorized_ixdzs"
    _BOOK_PATH = re.compile(r"^/read/(\d+)/$")
    _CHAPTER_MARKER = re.compile(r"第([0-9]{1,6})章")
    _SEARCH_LANDING_SUFFIX = re.compile(
        r"(?:txt下载八零zip|txt全集下载|txt全文下载|笔趣阁无弹窗|"
        r"章节目录|有声小说|百度百科|整本免费|全文阅读|最新章节|"
        r"新笔趣阁|顶点小说|好看吗|全集下载|无弹窗|首发|完整|字数|下载)$",
        re.IGNORECASE,
    )
    # ixdzs exposes these source sections under /sort/<id>/.  Translate them
    # directly into the electronic library's established directory taxonomy.
    # Category 0 is a real "其他小说" section even though normal scans begin
    # with category 1.
    CATALOG_CATEGORIES = {
        0: ("其他小说", "其他小说"),
        1: ("玄幻奇幻", "玄幻奇幻"),
        2: ("修真仙侠", "武侠仙侠"),
        3: ("都市青春", "都市小说"),
        4: ("军事历史", "军事历史"),
        5: ("网游竞技", "网游竞技"),
        6: ("科幻灵异", "科幻灵异"),
        7: ("言情穿越", "女生言情"),
        8: ("耽美同人", "耽美同人"),
        9: ("台言古言", "女生言情"),
        10: ("传统武侠", "武侠仙侠"),
        11: ("古典文学", "文学名著"),
        12: ("侦探推理", "科幻灵异"),
    }

    def __init__(self) -> None:
        self.base_url = os.getenv(
            "WEBNOVEL_IXDZS_BASE_URL", "https://ixdzs8.com"
        ).rstrip("/")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or parsed.hostname != "ixdzs8.com":
            raise ValueError("爱下电子书授权来源必须使用 https://ixdzs8.com")
        self.enabled = os.getenv("WEBNOVEL_IXDZS_ENABLED", "1").lower() not in {
            "0", "false", "no", "off",
        }
        self.max_bytes = max(
            1, int(os.getenv("WEBNOVEL_IXDZS_MAX_MB", "120"))
        ) * 1024 * 1024
        self.max_download_seconds = max(
            30,
            int(os.getenv("WEBNOVEL_IXDZS_MAX_DOWNLOAD_SECONDS", "300")),
        )
        self.delay = max(0.1, float(os.getenv("WEBNOVEL_IXDZS_DELAY", "0.4")))
        self._last_request = 0.0
        self._throttle_lock = threading.Lock()
        self._thread_local = threading.local()

    @staticmethod
    def _new_session() -> requests.Session:
        session = requests.Session()
        session.trust_env = False
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (compatible; WebnovelWriterIxdzs/1.0; "
                    "site-owner-authorized)"
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
            allowed_methods=frozenset({"GET"}),
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

    def availability(self) -> Dict[str, Any]:
        return {
            "id": self.PROVIDER_ID,
            "name": "爱下电子书（站点所有者授权）",
            "enabled": self.enabled,
            "base_url": self.base_url,
            "authorization": "site_owner_authorized",
            "search_mode": "title_or_author",
            "download_mode": "download_zip_extract_utf8_txt",
            "max_download_mb": self.max_bytes // (1024 * 1024),
            "max_download_seconds": self.max_download_seconds,
        }

    def _request(
        self,
        path: str,
        *,
        params: Dict[str, Any] | None = None,
        stream: bool = False,
    ) -> requests.Response:
        if not self.enabled:
            raise RuntimeError("爱下电子书来源已禁用")
        url = urljoin(self.base_url + "/", str(path).lstrip("/"))
        parsed = urlparse(url)
        if parsed.scheme != "https" or not (
            parsed.hostname == "ixdzs8.com"
            or str(parsed.hostname or "").endswith(".ixdzs8.com")
            or str(parsed.hostname or "").endswith(".ixdzs.com")
        ):
            raise ValueError("爱下电子书请求超出授权 HTTPS 主机")
        with self._throttle_lock:
            wait = self.delay - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
        response = self._session().get(
            url,
            params=params,
            timeout=(12, 90),
            stream=stream,
        )
        response.raise_for_status()
        return response

    @classmethod
    def validate_source_ref(cls, remote_id: Any, source_ref: str) -> str:
        remote_id = str(remote_id or "").strip()
        path = urlparse(str(source_ref or f"/read/{remote_id}/")).path
        match = cls._BOOK_PATH.fullmatch(path)
        if not remote_id.isdigit() or not match or match.group(1) != remote_id:
            raise ValueError("爱下电子书作品引用无效")
        return path

    @staticmethod
    def _clean(value: str) -> str:
        return re.sub(
            r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))
        ).strip()

    @classmethod
    def normalize_source_title(cls, value: Any) -> str:
        title = normalize_catalog_title(cls._clean(str(value or "")))
        previous = ""
        while title and title != previous:
            previous = title
            title = cls._SEARCH_LANDING_SUFFIX.sub("", title).strip()
        if len(title) >= 8 and title[-1] == title[-2]:
            title = title[:-1]
        return normalize_catalog_title(title)

    @classmethod
    def is_search_landing_title(cls, value: Any) -> bool:
        title = normalize_catalog_title(cls._clean(str(value or "")))
        if not title:
            return False
        return bool(cls._SEARCH_LANDING_SUFFIX.search(title)) or (
            len(title) >= 8 and title[-1] == title[-2]
        )

    def search(self, query: str, *, limit: int = 0) -> List[Dict[str, Any]]:
        query = self._clean(query).strip("《》")
        if len(query) < 2:
            raise ValueError("作品名或作者名至少需要 2 个字符")
        response = self._request("/bsearch", params={"q": query})
        soup = BeautifulSoup(response.text, "html.parser")
        lowered = query.casefold()
        results: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for node in soup.select("li.burl"):
            anchor = node.select_one("h3.bname a[href]")
            if not anchor:
                continue
            path = urlparse(str(anchor.get("href") or "")).path
            match = self._BOOK_PATH.fullmatch(path)
            if not match:
                continue
            remote_id = match.group(1)
            if remote_id in seen:
                continue
            raw_title = self._clean(
                str(anchor.get("title") or anchor.get_text(" ", strip=True))
            )
            if self.is_search_landing_title(raw_title):
                continue
            title = self.normalize_source_title(raw_title)
            if not title:
                continue
            seen.add(remote_id)
            author_node = node.select_one(".bauthor")
            status_node = node.select_one(".lz")
            size_node = node.select_one(".size")
            summary_node = node.select_one(".l-p2")
            cover_node = node.select_one(".l-img img[src]")
            author = self._clean(
                author_node.get_text(" ", strip=True) if author_node else ""
            ) or "作者未知"
            results.append(
                {
                    "provider": self.PROVIDER_ID,
                    "provider_label": "爱下电子书",
                    "remote_id": remote_id,
                    "source_ref": path,
                    "title": title,
                    "author": author,
                    "category": "",
                    "summary": self._clean(
                        summary_node.get_text(" ", strip=True)
                        if summary_node
                        else ""
                    ),
                    "cover_url": urljoin(
                        response.url,
                        str(cover_node.get("src") or ""),
                    )
                    if cover_node
                    else "",
                    "extension": "zip",
                    "filesize": self._clean(
                        size_node.get_text(" ", strip=True)
                        if size_node
                        else ""
                    ),
                    "book_status": self._clean(
                        status_node.get_text(" ", strip=True)
                        if status_node
                        else ""
                    ),
                    "license": "站点所有者授权来源",
                    "authorization": "site_owner_authorized",
                    "downloadable": True,
                    "download_mode": "下载 ZIP、提取正文并转换为 UTF-8 TXT",
                    "source": "remote_authorized",
                    "_rank": (
                        300 if title.casefold() == lowered else 0,
                        200 if lowered in title.casefold() else 0,
                        100 if lowered in author.casefold() else 0,
                    ),
                }
            )
        results.sort(key=lambda item: item["_rank"], reverse=True)
        for item in results:
            item.pop("_rank", None)
        return results if limit <= 0 else results[:limit]

    def catalog_page(self, category: int, page: int) -> List[Dict[str, Any]]:
        category = max(int(category), 0)
        page = max(int(page), 1)
        category_names = self.CATALOG_CATEGORIES.get(category)
        # The site ignores ``?page=`` and always returns page 1.  Its real
        # catalog pagination is encoded in the path.
        path = (
            f"/sort/{category}/"
            if page == 1
            else f"/sort/{category}/index-0-0-0-{page}.html"
        )
        response = self._request(path)
        soup = BeautifulSoup(response.text, "html.parser")
        items: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for anchor in soup.select('a[href^="/read/"][href$="/"]'):
            match = self._BOOK_PATH.fullmatch(urlparse(anchor.get("href") or "").path)
            if not match or match.group(1) in seen:
                continue
            raw_title = self._clean(
                anchor.get("title") or anchor.get_text(" ", strip=True)
            )
            if self.is_search_landing_title(raw_title):
                continue
            title = self.normalize_source_title(raw_title)
            if not title:
                continue
            remote_id = match.group(1)
            container = anchor.find_parent(["li", "div", "dl"])
            author_node = container.select_one(".bauthor") if container else None
            seen.add(remote_id)
            items.append(
                {
                    "provider": self.PROVIDER_ID,
                    "remote_id": remote_id,
                    "source_ref": f"/read/{remote_id}/",
                    "title": title,
                    "author": self._clean(
                        author_node.get_text(" ", strip=True) if author_node else ""
                    ) or "作者未知",
                    "category": (
                        category_names[1] if category_names else "未分类"
                    ),
                    "source_category": (
                        category_names[0] if category_names else ""
                    ),
                }
            )
        return items

    def latest_updates(self, page: int = 1) -> List[Dict[str, Any]]:
        """Return recent books together with the source's latest-chapter token."""

        page = max(int(page), 1)
        response = self._request("/new/", params={"page": page})
        soup = BeautifulSoup(response.text, "html.parser")
        items: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for node in soup.select("li.burl"):
            anchor = node.select_one("h3.bname a[href]")
            if not anchor:
                continue
            match = self._BOOK_PATH.fullmatch(
                urlparse(str(anchor.get("href") or "")).path
            )
            if not match or match.group(1) in seen:
                continue
            remote_id = match.group(1)
            raw_title = self._clean(
                anchor.get("title") or anchor.get_text(" ", strip=True)
            )
            if self.is_search_landing_title(raw_title):
                continue
            title = self.normalize_source_title(raw_title)
            if not title:
                continue
            author_node = node.select_one(".bauthor")
            status_node = node.select_one(".lz")
            latest_node = node.select_one(".l-last a[href]")
            latest_title_node = node.select_one(".l-chapter")
            updated_node = node.select_one(".l-time")
            latest_ref = (
                urlparse(str(latest_node.get("href") or "")).path
                if latest_node
                else ""
            )
            latest_title = self._clean(
                latest_title_node.get_text(" ", strip=True)
                if latest_title_node
                else ""
            )
            updated_at = self._clean(
                updated_node.get_text(" ", strip=True) if updated_node else ""
            )
            seen.add(remote_id)
            items.append(
                {
                    "provider": self.PROVIDER_ID,
                    "remote_id": remote_id,
                    "source_ref": f"/read/{remote_id}/",
                    "title": title,
                    "author": self._clean(
                        author_node.get_text(" ", strip=True)
                        if author_node
                        else ""
                    ) or "作者未知",
                    "category": "未分类",
                    "book_status": self._clean(
                        status_node.get_text(" ", strip=True)
                        if status_node
                        else ""
                    ),
                    "remote_latest_chapter": latest_title,
                    "remote_latest_ref": latest_ref,
                    "remote_updated_at": updated_at,
                    "remote_revision": latest_ref or f"{updated_at}|{latest_title}",
                }
            )
        return items

    def detail(self, remote_id: Any, source_ref: str = "") -> Dict[str, Any]:
        source_ref = self.validate_source_ref(remote_id, source_ref)
        response = self._request(source_ref)
        soup = BeautifulSoup(response.text, "html.parser")

        def meta(name: str) -> str:
            node = soup.select_one(f'meta[property="{name}"]')
            return self._clean(node.get("content") if node else "")

        download = soup.select_one('a[href*="down"][href$=".zip"]')
        cover = soup.select_one(".n-img img[src]")
        category = self._clean(
            soup.select_one(".nsort").get_text(" ", strip=True)
            if soup.select_one(".nsort") else ""
        )
        raw_title = meta("og:novel:book_name") or f"爱下电子书 {remote_id}"
        if self.is_search_landing_title(raw_title):
            raise ValueError("爱下电子书详情是搜索落地页，不是独立小说")
        return {
            "provider": self.PROVIDER_ID,
            "remote_id": str(remote_id),
            "source_book_id": str(remote_id),
            "title": self.normalize_source_title(raw_title),
            "author": meta("og:novel:author") or "作者未知",
            "book_status": meta("og:novel:status"),
            "category": category or "未分类",
            "categories": [category] if category else [],
            "summary": self._clean(
                soup.select_one("#intro").get_text(" ", strip=True)
                if soup.select_one("#intro") else ""
            ),
            "cover_url": urljoin(response.url, cover.get("src"))
            if cover else "",
            "download_url": urljoin(response.url, download.get("href"))
            if download else "",
            "detail_url": response.url,
            "source_ref": source_ref,
            "extension": "zip",
        }

    def download(self, detail: Dict[str, Any]) -> bytes:
        url = str(detail.get("download_url") or "")
        if not url:
            raise ValueError("爱下电子书详情没有 ZIP 下载入口")
        response = self._request(url, stream=True)
        started = time.monotonic()
        try:
            data = bytearray()
            for chunk in response.iter_content(256 * 1024):
                if time.monotonic() - started > self.max_download_seconds:
                    raise TimeoutError(
                        "爱下电子书整包下载超过最大等待时间，已保留旧正文"
                    )
                data.extend(chunk)
                if len(data) > self.max_bytes:
                    raise ValueError("爱下电子书下载超过安全大小上限")
            if not bytes(data).startswith(b"PK\x03\x04"):
                raise ValueError("爱下电子书下载结果不是 ZIP")
            return bytes(data)
        finally:
            response.close()

    @staticmethod
    def extract_text(data: bytes) -> str:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            candidates = [
                info
                for info in archive.infolist()
                if not info.is_dir()
                and PurePosixPath(info.filename.replace("\\", "/")).suffix.casefold()
                in {".txt", ".text"}
            ]
            if not candidates:
                raise ValueError("爱下电子书 ZIP 内没有 TXT 正文")
            target = max(candidates, key=lambda item: item.file_size)
            raw = archive.read(target)
        for encoding in ("utf-8-sig", "gb18030", "big5"):
            try:
                return AuthorizedIxdzsProvider.normalize_download_text(
                    raw.decode(encoding)
                )
            except UnicodeDecodeError:
                continue
        return AuthorizedIxdzsProvider.normalize_download_text(
            raw.decode("utf-8", errors="replace")
        )

    @classmethod
    def normalize_download_text(cls, content: str) -> str:
        """Repair the source ZIP's chapter markers glued to prior prose."""
        text = str(content or "").replace("\r\n", "\n").replace("\r", "\n")
        separator = "------章节内容开始-------"
        header, found, body = text.partition(separator)
        if not found:
            header, body = "", text

        pieces: list[str] = []
        cursor = 0
        expected = 1
        for match in cls._CHAPTER_MARKER.finditer(body):
            number = int(match.group(1))
            if number != expected:
                continue
            pieces.append(body[cursor:match.start()].rstrip())
            pieces.append("\n\n")
            cursor = match.start()
            expected += 1
        pieces.append(body[cursor:])
        normalized_body = "".join(pieces).lstrip()
        if not found:
            return normalized_body.strip() + "\n"
        return (
            header.strip()
            + "\n"
            + separator
            + "\n"
            + normalized_body.strip()
            + "\n"
        )
