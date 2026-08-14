"""Site-owner-authorized xbiquge.info search and full-book crawler."""

from __future__ import annotations

from .error_boundaries import RECOVERABLE_OPERATION_ERRORS

import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from oohstory_library.services.chapter_segments import (
    chapter_segment_filename,
    chapter_title_from_segment,
    chapter_title_has_name,
    load_chapter_manifest,
    prefer_chapter_title,
    strip_duplicate_heading,
    write_chapter_manifest,
)


class AuthorizedXbiqugeProvider:
    PROVIDER_ID = "authorized_xbiquge"
    _BOOK_PATH = re.compile(r"^/(\d+)/(\d+)/$")
    _CHAPTER_PATH = re.compile(r"^/(\d+)/(\d+)/(\d+)\.html$")
    # xbiquge's /listN/ navigation is stable and is the authoritative
    # classification for catalog discovery.  Keep the mapping mechanical so
    # catalog scans never need an AI classification request.
    CATALOG_CATEGORIES = {
        1: ("玄幻", "玄幻奇幻"),
        2: ("修真", "武侠仙侠"),
        3: ("都市", "都市小说"),
        4: ("历史", "军事历史"),
        5: ("网游", "网游竞技"),
        6: ("科幻", "科幻灵异"),
        7: ("言情", "女生言情"),
        8: ("其它", "其他小说"),
    }

    def __init__(self) -> None:
        self.base_url = os.getenv(
            "WEBNOVEL_XBIQUGE_BASE_URL", "https://www.xbiquge.info"
        ).rstrip("/")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "www.xbiquge.info",
            "xbiquge.info",
        }:
            raise ValueError(
                "新笔趣阁授权来源必须使用 https://www.xbiquge.info"
            )
        self.enabled = os.getenv(
            "WEBNOVEL_XBIQUGE_ENABLED", "1"
        ).lower() not in {"0", "false", "no", "off"}
        self.delay = max(
            0.05, float(os.getenv("WEBNOVEL_XBIQUGE_DELAY", "0.05"))
        )
        self.workers = min(
            max(int(os.getenv("WEBNOVEL_XBIQUGE_WORKERS", "6")), 1), 12
        )
        self.max_bytes = max(
            1, int(os.getenv("WEBNOVEL_XBIQUGE_MAX_MB", "120"))
        ) * 1024 * 1024
        self._last_request = 0.0
        self._throttle_lock = threading.Lock()
        self._thread_local = threading.local()
        configured_cache = os.getenv(
            "WEBNOVEL_XBIQUGE_CHAPTER_CACHE",
            "",
        ).strip()
        self.chapter_cache_root = (
            Path(configured_cache).expanduser().resolve()
            if configured_cache
            else (
                Path(__file__).resolve().parents[3]
                / "electronic-library"
                / ".download-cache"
                / "xbiquge"
            )
        )

    @staticmethod
    def _new_session() -> requests.Session:
        session = requests.Session()
        session.trust_env = False
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (compatible; WebnovelWriterXbiquge/1.0; "
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
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
        session.mount("https://", adapter)
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
            "name": "新笔趣阁（站点所有者授权）",
            "enabled": self.enabled,
            "base_url": self.base_url,
            "full_catalog_url": f"{self.base_url}/full/",
            "authorization": "site_owner_authorized",
            "download_mode": "crawl_all_chapters_and_merge_txt",
            "max_download_mb": self.max_bytes // (1024 * 1024),
            "workers": self.workers,
        }

    def _request(self, path: str, *, params: Dict[str, Any] | None = None) -> requests.Response:
        if not self.enabled:
            raise RuntimeError("新笔趣阁在线来源已被配置禁用")
        parsed = urlparse(path)
        if parsed.scheme or parsed.netloc:
            url = path
        else:
            url = urljoin(self.base_url + "/", path.lstrip("/"))
        target = urlparse(url)
        if target.scheme != "https" or target.hostname not in {
            "www.xbiquge.info",
            "xbiquge.info",
        }:
            raise ValueError("新笔趣阁请求地址不在授权 HTTPS 主机内")
        with self._throttle_lock:
            wait_for = self.delay - (time.monotonic() - self._last_request)
            if wait_for > 0:
                time.sleep(wait_for)
            self._last_request = time.monotonic()
        response = self._session().get(
            url,
            params=params,
            timeout=(10, 60),
        )
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        return response

    @staticmethod
    def _clean_text(value: str) -> str:
        text = unicodedata.normalize("NFKC", value or "")
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def validate_source_ref(cls, remote_id: Any, source_ref: str) -> str:
        remote_id = str(remote_id or "").strip()
        if not remote_id.isdigit():
            raise ValueError("新笔趣阁作品标识无效")
        parsed = urlparse(source_ref)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or ".." in parsed.path
            or "\\" in parsed.path
        ):
            raise ValueError("新笔趣阁详情引用必须是安全的站内相对路径")
        match = cls._BOOK_PATH.fullmatch(parsed.path)
        if not match or match.group(2) != remote_id:
            raise ValueError("新笔趣阁详情引用与作品标识不匹配")
        return parsed.path

    @classmethod
    def _book_ref(cls, value: str) -> tuple[str, str] | None:
        path = urlparse(value or "").path
        match = cls._BOOK_PATH.fullmatch(path)
        if not match:
            return None
        return match.group(2), path

    def search(self, query: str, *, limit: int = 0) -> List[Dict[str, Any]]:
        query = query.strip().strip("《》")
        if len(query) < 2:
            raise ValueError("作品名或作者名至少需要 2 个字符")
        response = self._request("/search.php", params={"q": query})
        soup = BeautifulSoup(response.text, "html.parser")
        lowered = query.casefold()
        results: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for node in soup.select("div.box.hot dl"):
            anchor = node.select_one("dd h3 a[href]")
            if not anchor:
                continue
            reference = self._book_ref(str(anchor.get("href") or ""))
            if not reference:
                continue
            remote_id, source_ref = reference
            if remote_id in seen:
                continue
            seen.add(remote_id)
            heading = self._clean_text(anchor.get_text(" ", strip=True))
            category_match = re.match(r"^\[([^\]]+)\]\s*(.+)$", heading)
            category = category_match.group(1) if category_match else ""
            title = category_match.group(2) if category_match else heading
            author_node = node.select_one("dd.book_other span")
            author = (
                self._clean_text(author_node.get_text(" ", strip=True))
                if author_node
                else "作者未知"
            )
            book_status = ""
            for meta in node.select("dd.book_other"):
                text = self._clean_text(meta.get_text(" ", strip=True))
                if text.startswith("状态"):
                    book_status = re.split(r"[：:]", text, maxsplit=1)[-1].strip()
                    break
            results.append(
                {
                    "provider": self.PROVIDER_ID,
                    "provider_label": "新笔趣阁全章节",
                    "remote_id": remote_id,
                    "source_ref": source_ref,
                    "title": title,
                    "author": author,
                    "category": category,
                    "extension": "txt",
                    "book_status": book_status,
                    "license": "站点所有者授权来源",
                    "authorization": "site_owner_authorized",
                    "downloadable": True,
                    "download_mode": "抓取全章节并合并为单本 TXT",
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
        category = min(max(int(category), 1), 8)
        page = max(int(page), 1)
        source_category, library_category = self.CATALOG_CATEGORIES[category]
        path = f"/list{category}/" if page == 1 else f"/list{category}/{page}.html"
        soup = BeautifulSoup(self._request(path).text, "html.parser")
        results: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for node in soup.select("div.box.hot dl"):
            anchor = node.select_one("dd h3 a[href]")
            if not anchor:
                continue
            reference = self._book_ref(str(anchor.get("href") or ""))
            if not reference or reference[0] in seen:
                continue
            remote_id, source_ref = reference
            heading = self._clean_text(anchor.get_text(" ", strip=True))
            category_match = re.match(r"^\[([^\]]+)\]\s*(.+)$", heading)
            title = category_match.group(2) if category_match else heading
            author_node = node.select_one("dd.book_other span")
            author = self._clean_text(
                author_node.get_text(" ", strip=True) if author_node else ""
            ) or "作者未知"
            status = ""
            for meta in node.select("dd.book_other"):
                text = self._clean_text(meta.get_text(" ", strip=True))
                if text.startswith("状态"):
                    status = re.split(r"[：:]", text, maxsplit=1)[-1].strip()
                    break
            seen.add(remote_id)
            results.append(
                {
                    "provider": self.PROVIDER_ID,
                    "remote_id": remote_id,
                    "source_ref": source_ref,
                    "title": title,
                    "author": author,
                    "category": library_category,
                    "source_category": source_category,
                    "book_status": status,
                }
            )
        return results

    def latest_updates(self, page: int = 1) -> List[Dict[str, Any]]:
        """Return the site's recent-update feed with a stable chapter revision."""

        page = max(int(page), 1)
        path = "/lastupdate/" if page == 1 else f"/lastupdate/{page}.html"
        soup = BeautifulSoup(self._request(path).text, "html.parser")
        results: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for node in soup.select("div.box.hot dl"):
            anchor = node.select_one("dd h3 a[href]")
            if not anchor:
                continue
            reference = self._book_ref(str(anchor.get("href") or ""))
            if not reference or reference[0] in seen:
                continue
            remote_id, source_ref = reference
            heading = self._clean_text(anchor.get_text(" ", strip=True))
            category_match = re.match(r"^\[([^\]]+)\]\s*(.+)$", heading)
            title = category_match.group(2) if category_match else heading
            source_category = category_match.group(1) if category_match else ""
            library_category = next(
                (
                    mapped
                    for source, mapped in self.CATALOG_CATEGORIES.values()
                    if source == source_category
                ),
                "未分类",
            )
            author_node = node.select_one("dd.book_other span")
            author = self._clean_text(
                author_node.get_text(" ", strip=True) if author_node else ""
            ) or "作者未知"
            status = ""
            updated_at = ""
            latest_title = ""
            latest_path = ""
            for meta in node.select("dd.book_other"):
                text = self._clean_text(meta.get_text(" ", strip=True))
                if text.startswith("状态"):
                    status = re.split(r"[：:]", text, maxsplit=1)[-1].strip()
                elif text.startswith("更新时间"):
                    updated_at = re.split(r"[：:]", text, maxsplit=1)[-1].strip()
                elif text.startswith("最新章节"):
                    latest = meta.select_one("a[href]")
                    if latest:
                        latest_title = self._clean_text(
                            latest.get_text(" ", strip=True)
                        )
                        latest_path = urlparse(
                            str(latest.get("href") or "")
                        ).path
            if not title:
                continue
            seen.add(remote_id)
            results.append(
                {
                    "provider": self.PROVIDER_ID,
                    "remote_id": remote_id,
                    "source_ref": source_ref,
                    "title": title,
                    "author": author,
                    "category": library_category,
                    "source_category": source_category,
                    "book_status": status,
                    "remote_latest_chapter": latest_title,
                    "remote_latest_ref": latest_path,
                    "remote_updated_at": updated_at,
                    "remote_revision": latest_path or f"{updated_at}|{latest_title}",
                }
            )
        return results

    def _chapter_links(
        self, soup: BeautifulSoup, section_id: str, remote_id: str
    ) -> List[Dict[str, str]]:
        links: List[Dict[str, str]] = []
        for anchor in soup.select("div.book_list.book_list2 a[href]"):
            path = urlparse(str(anchor.get("href") or "")).path
            match = self._CHAPTER_PATH.fullmatch(path)
            if (
                not match
                or match.group(1) != section_id
                or match.group(2) != remote_id
            ):
                continue
            links.append(
                {
                    "title": self._clean_text(anchor.get_text(" ", strip=True)),
                    "path": path,
                }
            )
        return links

    def detail(
        self,
        remote_id: Any,
        source_ref: str,
        *,
        include_chapters: bool = True,
    ) -> Dict[str, Any]:
        source_ref = self.validate_source_ref(remote_id, source_ref)
        section_id = self._BOOK_PATH.fullmatch(source_ref).group(1)
        response = self._request(source_ref)
        soup = BeautifulSoup(response.text, "html.parser")

        def meta(property_name: str) -> str:
            node = soup.select_one(f'meta[property="{property_name}"]')
            return self._clean_text(str(node.get("content") or "")) if node else ""

        title = meta("og:novel:book_name")
        if not title:
            title_node = soup.select_one("div.book_info h1")
            title = self._clean_text(
                title_node.get_text(" ", strip=True) if title_node else ""
            )
        author = meta("og:novel:author") or "作者未知"
        category = meta("og:novel:category") or "未分类"
        book_status = meta("og:novel:status")
        summary = meta("og:description")
        cover_url = meta("og:image")
        if not cover_url:
            cover_node = soup.select_one("div.book_info img[src]")
            cover_url = (
                urljoin(response.url, str(cover_node.get("src") or "").strip())
                if cover_node
                else ""
            )
        elif cover_url:
            cover_url = urljoin(response.url, cover_url)
        chapters: List[Dict[str, str]] = []
        if include_chapters:
            total_pages = 1
            pages_node = soup.select_one("div.pages")
            page_summary = self._clean_text(
                pages_node.get_text(" ", strip=True) if pages_node else ""
            )
            page_fraction = re.search(r"\b\d+\s*/\s*(\d+)\b", page_summary)
            if page_fraction:
                total_pages = max(total_pages, int(page_fraction.group(1)))
            for anchor in soup.select("div.pages a[href*='index_']"):
                match = re.search(
                    r"index_(\d+)\.html",
                    str(anchor.get("href") or ""),
                )
                if match:
                    total_pages = max(total_pages, int(match.group(1)))
            seen: set[str] = set()
            for page_number in range(1, total_pages + 1):
                page_soup = (
                    soup
                    if page_number == 1
                    else BeautifulSoup(
                        self._request(
                            f"/{section_id}/{remote_id}/index_{page_number}.html"
                        ).text,
                        "html.parser",
                    )
                )
                for chapter in self._chapter_links(
                    page_soup, section_id, str(remote_id)
                ):
                    if chapter["path"] in seen:
                        continue
                    seen.add(chapter["path"])
                    chapters.append(chapter)
        if include_chapters and not chapters:
            raise RuntimeError("新笔趣阁作品没有可抓取的章节")
        return {
            "provider": self.PROVIDER_ID,
            "remote_id": str(remote_id),
            "source_book_id": str(remote_id),
            "title": title or f"新笔趣阁 {remote_id}",
            "author": author,
            "categories": [category],
            "category": category,
            "book_status": book_status,
            "summary": summary,
            "cover_url": cover_url,
            "extension": "txt",
            "detail_url": urljoin(self.base_url + "/", source_ref.lstrip("/")),
            "source_ref": source_ref,
            "chapter_count": len(chapters),
            "chapters": chapters,
        }

    def _download_chapter(
        self, index: int, chapter: Dict[str, str]
    ) -> tuple[int, str, str]:
        last_error = ""
        # A busy source occasionally returns a HTTP-200 shell without the
        # article. urllib3 cannot retry that semantic failure, so retry the
        # parse as well. This is especially important for 1,000+ chapter books.
        for attempt in range(1, 5):
            try:
                response = self._request(chapter["path"])
                soup = BeautifulSoup(response.text, "html.parser")
                article = soup.select_one(
                    "div.box.single article, article#content, div#content"
                )
                if not article:
                    raise RuntimeError("响应页缺少正文节点")
                content = article.get_text("\n", strip=True)
                content = unicodedata.normalize("NFKC", content).replace("\xa0", " ")
                content = re.sub(r"[ \t]+\n", "\n", content)
                content = re.sub(r"\n{3,}", "\n\n", content).strip()
                page_titles = [
                    node.get_text(" ", strip=True)
                    for node in soup.select(
                        "div.bookname h1, div.box_con h1, article h1, "
                        "div#content h1, h1"
                    )
                ]
                first_line = next(
                    (line.strip() for line in content.splitlines() if line.strip()),
                    "",
                )
                title = prefer_chapter_title(
                    chapter["title"],
                    [*page_titles, first_line],
                )
                content = strip_duplicate_heading(content, title)
                if len(content) < 10:
                    raise RuntimeError("正文为空或过短")
                return index, title, content
            except RECOVERABLE_OPERATION_ERRORS as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < 4:
                    time.sleep(min(0.5 * attempt, 1.5))
        raise RuntimeError(
            f"第 {index + 1} 个章节连续 4 次抓取失败：{last_error}"
        )

    def download(self, detail: Dict[str, Any]) -> bytes:
        chapters = list(detail.get("chapters") or [])
        if not chapters:
            raise ValueError("新笔趣阁详情缺少章节目录")
        downloaded: List[Path | None] = [None] * len(chapters)
        errors: List[str] = []
        remote_id = str(detail.get("remote_id") or "").strip()
        if not remote_id.isdigit():
            raise ValueError("新笔趣阁详情缺少有效作品 ID")
        cache_dir = self.chapter_cache_root / remote_id
        cache_dir.mkdir(parents=True, exist_ok=True)
        manifest = load_chapter_manifest(cache_dir)
        pending: List[tuple[int, Dict[str, str]]] = []
        for index, chapter in enumerate(chapters):
            chapter_ref = str(chapter.get("path") or "")
            expected = cache_dir / chapter_segment_filename(
                chapter.get("title"), index + 1
            )
            candidates = []
            if chapter_ref in manifest:
                candidates.append(cache_dir / manifest[chapter_ref])
            candidates.extend([expected, cache_dir / f"{index + 1:07d}.txt"])
            cache_path = next((path for path in candidates if path.is_file()), None)
            cached = ""
            if cache_path:
                try:
                    cached = cache_path.read_text(encoding="utf-8").strip()
                except (OSError, UnicodeError):
                    cached = ""
            cached_title = (
                chapter_title_from_segment(cache_path, chapter.get("title"))
                if cache_path
                else ""
            )
            verified = bool(chapter_ref and chapter_ref in manifest)
            if (
                cache_path
                and len(cached) >= 10
                and (verified or chapter_title_has_name(cached_title))
            ):
                canonical = cache_dir / chapter_segment_filename(
                    cached_title, index + 1
                )
                if cache_path != canonical:
                    os.replace(cache_path, canonical)
                downloaded[index] = canonical
                if chapter_ref:
                    manifest[chapter_ref] = canonical.name
            else:
                pending.append((index, chapter))
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {
                executor.submit(self._download_chapter, index, chapter): index
                for index, chapter in pending
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    _, title, content = future.result()
                    cache_path = cache_dir / chapter_segment_filename(
                        title, index + 1
                    )
                    temporary = cache_path.with_suffix(".txt.tmp")
                    temporary.write_text(content, encoding="utf-8")
                    os.replace(temporary, cache_path)
                    downloaded[index] = cache_path
                    chapter_ref = str(chapters[index].get("path") or "")
                    if chapter_ref:
                        manifest[chapter_ref] = cache_path.name
                except RECOVERABLE_OPERATION_ERRORS as exc:
                    errors.append(
                        f"章节 {index + 1}: {type(exc).__name__}: {str(exc)[:160]}"
                    )
        write_chapter_manifest(cache_dir, manifest)
        if errors or any(item is None for item in downloaded):
            preview = "；".join(errors[:8])
            raise RuntimeError(
                f"新笔趣阁全章节抓取不完整，已取消入库：{preview}"
            )
        header = [
            str(detail.get("title") or "未命名作品"),
            f"作者：{detail.get('author') or '作者未知'}",
            f"分类：{detail.get('category') or '未分类'}",
            f"状态：{detail.get('book_status') or '未知'}",
            f"章节数：{len(downloaded)}",
        ]
        summary = str(detail.get("summary") or "").strip()
        if summary:
            header.extend(["简介：", summary])
        parts = ["\n".join(header)]
        for index, cache_path in enumerate(downloaded):
            assert cache_path is not None
            title = chapter_title_from_segment(
                cache_path, chapters[index].get("title")
            )
            content = cache_path.read_text(encoding="utf-8").strip()
            parts.append(f"{title}\n{content}")
        data = ("\n\n".join(parts).strip() + "\n").encode("utf-8")
        if len(data) > self.max_bytes:
            raise ValueError("新笔趣阁合并后的作品超过安全大小上限")
        return data
