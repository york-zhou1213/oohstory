"""Site-owner-authorized shubaow.org search and full-book crawler."""

from __future__ import annotations

import os
import json
import base64
import re
import subprocess
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urljoin, urlparse
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from oohstory_library.services.browser_recovery import shared_browser_recovery
from oohstory_library.services.chapter_segments import (
    chapter_segment_filename,
    chapter_title_from_segment,
    chapter_title_has_name,
    load_chapter_manifest,
    prefer_chapter_title,
    strip_duplicate_heading,
    write_chapter_manifest,
)


APP_ROOT = Path(__file__).resolve().parents[3]


class AuthorizedShubaowProvider:
    PROVIDER_ID = "authorized_shubaow"
    _BOOK_PATH = re.compile(r"^/book(\d+)\.html$")
    _CHAPTER_PATH = re.compile(r"^/book(\d+)/(\d+)\.html$")
    CATALOG_CATEGORIES = {
        1: ("danmei", "耽美", "女生言情"),
        2: ("nansheng", "男频", "都市小说"),
        3: ("yanqing", "现代", "女生言情"),
        4: ("guzhuang", "古装", "女生言情"),
        5: ("chuanyue", "穿越重生", "女生言情"),
        6: ("qita", "其他", "其他小说"),
        7: ("taiwan", "台湾", "女生言情"),
        8: ("kehuan", "网游科幻", "科幻灵异"),
        9: ("tuili", "恐怖推理", "科幻灵异"),
        10: ("xianxia", "仙侠", "武侠仙侠"),
        11: ("xuanhuan", "玄幻", "玄幻奇幻"),
        12: ("bgtr", "BG同人", "女生言情"),
    }
    CATEGORY_TO_LIBRARY = {
        "耽美同人": "女生言情",
        "男频小说": "都市小说",
        "现代情感": "女生言情",
        "古装迷情": "女生言情",
        "穿越重生": "女生言情",
        "其他言情": "女生言情",
        "台湾言情": "女生言情",
        "网游科幻": "科幻灵异",
        "恐怖推理": "科幻灵异",
        "仙侠修真": "武侠仙侠",
        "玄幻奇幻": "玄幻奇幻",
        "BG同人": "女生言情",
    }

    def __init__(self) -> None:
        self.base_url = os.getenv(
            "OOHSTORY_LIBRARY_SHUBAOW_BASE_URL", "https://www.shubaow.org"
        ).rstrip("/")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "www.shubaow.org", "shubaow.org"
        }:
            raise ValueError("书宝网授权来源必须使用 https://www.shubaow.org")
        self.enabled = os.getenv("OOHSTORY_LIBRARY_SHUBAOW_ENABLED", "1").lower() not in {
            "0", "false", "no", "off"
        }
        self.delay = max(0.35, float(os.getenv("OOHSTORY_LIBRARY_SHUBAOW_DELAY", "0.75")))
        self.workers = min(max(int(os.getenv("OOHSTORY_LIBRARY_SHUBAOW_WORKERS", "2")), 1), 4)
        self.max_bytes = max(1, int(os.getenv("OOHSTORY_LIBRARY_SHUBAOW_MAX_MB", "120"))) * 1024 * 1024
        self.cookie_file = os.getenv("OOHSTORY_LIBRARY_SHUBAOW_COOKIE_FILE", "").strip()
        self.cdp_url = os.getenv("OOHSTORY_LIBRARY_SHUBAOW_CDP_URL", "").strip().rstrip("/")
        self.user_agent = os.getenv(
            "OOHSTORY_LIBRARY_SHUBAOW_USER_AGENT",
            (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
            ),
        ).strip()
        self._last_request = 0.0
        self._throttle_lock = threading.Lock()
        self._thread_local = threading.local()
        configured_cache = os.getenv("OOHSTORY_LIBRARY_SHUBAOW_CHAPTER_CACHE", "").strip()
        self.chapter_cache_root = (
            Path(configured_cache).expanduser().resolve()
            if configured_cache
            else APP_ROOT
            / "electronic-library" / "txt80" / ".download-cache" / "shubaow"
        )

    def _new_session(self) -> requests.Session:
        session = requests.Session()
        session.trust_env = False
        session.headers.update({
            "User-Agent": self.user_agent,
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        if self.cookie_file:
            cookie_path = Path(self.cookie_file).expanduser()
            if cookie_path.is_file():
                cookie_header = cookie_path.read_text(encoding="utf-8").strip()
                if cookie_header:
                    session.headers["Cookie"] = cookie_header
        retry = Retry(
            total=4, connect=4, read=4, status=3, backoff_factor=0.8,
            status_forcelist=(408, 425, 429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
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
        cookie_configured = False
        if self.cookie_file:
            try:
                cookie_configured = bool(
                    Path(self.cookie_file).expanduser().read_text(
                        encoding="utf-8"
                    ).strip()
                )
            except OSError:
                cookie_configured = False
        return {
            "id": self.PROVIDER_ID,
            "name": "书宝网（站点所有者授权）",
            "enabled": self.enabled,
            "base_url": self.base_url,
            "authorization": "site_owner_authorized",
            "download_mode": "crawl_all_chapters_and_merge_txt",
            "max_download_mb": self.max_bytes // (1024 * 1024),
            "workers": self.workers,
            "cloudflare_session_configured": cookie_configured,
            "browser_transport_configured": bool(self.cdp_url),
        }

    def _browser_request(
        self,
        url: str,
        *,
        method: str,
        data: Dict[str, Any] | None,
    ) -> requests.Response:
        tool = (
            APP_ROOT
            / "scripts"
            / "electronic-library"
            / "shubaow_browser_fetch.mjs"
        )
        env = os.environ.copy()
        env["OOHSTORY_LIBRARY_SHUBAOW_CDP_URL"] = self.cdp_url
        if data:
            env["OOHSTORY_LIBRARY_SHUBAOW_FETCH_BODY"] = urlencode(data)
        def fetch() -> requests.Response:
            completed = subprocess.run(
                ["node", str(tool), url, method],
                cwd=APP_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=75,
                check=False,
            )
            if completed.returncode:
                detail = (completed.stderr or "书宝网浏览器请求失败").strip()
                raise RuntimeError(detail[-500:])
            try:
                payload = json.loads(completed.stdout)
                response = requests.Response()
                response.status_code = int(payload.get("status") or 0)
                response.url = str(payload.get("url") or url)
                response.headers["Content-Type"] = str(
                    payload.get("contentType") or ""
                )
                response._content = str(payload.get("body") or "").encode(
                    "utf-8"
                )
            except (TypeError, ValueError) as exc:
                raise RuntimeError("书宝网浏览器返回了无效响应") from exc
            response.encoding = "utf-8"
            return response

        return shared_browser_recovery.call(self.cdp_url, fetch)

    def download_cover(self, cover_url: str) -> tuple[bytes, str]:
        parsed = urlparse(str(cover_url or ""))
        if parsed.scheme != "https" or parsed.hostname not in {
            "pic.shubaow.org", "www.shubaow.org", "shubaow.org"
        }:
            raise ValueError("书宝网封面不在可信 HTTPS 主机内")
        if not self.cdp_url:
            raise RuntimeError("书宝网封面下载要求已验证的 Chrome CDP 会话")
        tool = (
            APP_ROOT
            / "scripts"
            / "electronic-library"
            / "shubaow_browser_fetch.mjs"
        )
        env = os.environ.copy()
        env["OOHSTORY_LIBRARY_SHUBAOW_CDP_URL"] = self.cdp_url
        env["OOHSTORY_LIBRARY_SHUBAOW_FETCH_BINARY"] = "1"
        last_error = ""
        for attempt in range(1, 4):
            try:
                def fetch() -> tuple[bytes, str]:
                    completed = subprocess.run(
                        ["node", str(tool), cover_url, "GET"],
                        cwd=APP_ROOT,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=45,
                        check=False,
                    )
                    if completed.returncode:
                        detail = (
                            completed.stderr or "书宝网封面浏览器请求失败"
                        ).strip()
                        raise RuntimeError(detail[-500:])
                    payload = json.loads(completed.stdout)
                    if int(payload.get("status") or 0) >= 400:
                        raise RuntimeError(
                            f"书宝网封面响应 HTTP {payload.get('status')}"
                        )
                    return (
                        base64.b64decode(
                            str(payload.get("bodyBase64") or ""),
                            validate=True,
                        ),
                        str(payload.get("contentType") or ""),
                    )

                return shared_browser_recovery.call(self.cdp_url, fetch)
            except (RuntimeError, ValueError, json.JSONDecodeError,
                    subprocess.TimeoutExpired) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < 3:
                    time.sleep(float(attempt))
        raise RuntimeError(
            f"书宝网封面连续 3 次抓取失败：{last_error[-500:]}"
        )

    @staticmethod
    def _challenge(response: requests.Response) -> bool:
        sample = response.text[:5000].casefold()
        return (
            response.headers.get("cf-mitigated", "").casefold() == "challenge"
            or "just a moment" in sample
            or "cf-chl-" in sample
            or "challenge-platform" in sample
        )

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        data: Dict[str, Any] | None = None,
    ) -> requests.Response:
        if not self.enabled:
            raise RuntimeError("书宝网在线来源已被配置禁用")
        parsed = urlparse(path)
        url = path if parsed.scheme or parsed.netloc else urljoin(self.base_url + "/", path.lstrip("/"))
        target = urlparse(url)
        if target.scheme != "https" or target.hostname not in {"www.shubaow.org", "shubaow.org"}:
            raise ValueError("书宝网请求地址不在授权 HTTPS 主机内")
        with self._throttle_lock:
            wait_for = self.delay - (time.monotonic() - self._last_request)
            if wait_for > 0:
                time.sleep(wait_for)
            self._last_request = time.monotonic()
        response = (
            self._browser_request(url, method=method, data=data)
            if self.cdp_url
            else self._session().request(method, url, data=data, timeout=(10, 60))
        )
        response.encoding = response.apparent_encoding or "utf-8"
        if self._challenge(response):
            raise RuntimeError(
                "书宝网 Cloudflare 验证未通过；请先建立受信任浏览器会话并配置安全 Cookie 文件"
            )
        response.raise_for_status()
        return response

    @staticmethod
    def _clean_text(value: str) -> str:
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "")).strip()

    @classmethod
    def validate_source_ref(cls, remote_id: Any, source_ref: str) -> str:
        remote_id = str(remote_id or "").strip()
        if not remote_id.isdigit():
            raise ValueError("书宝网作品标识无效")
        parsed = urlparse(source_ref)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or ".." in parsed.path or "\\" in parsed.path:
            raise ValueError("书宝网详情引用必须是安全的站内相对路径")
        match = cls._BOOK_PATH.fullmatch(parsed.path)
        if not match or match.group(1) != remote_id:
            raise ValueError("书宝网详情引用与作品标识不匹配")
        return parsed.path

    @classmethod
    def _book_ref(cls, value: str) -> tuple[str, str] | None:
        path = urlparse(value or "").path
        match = cls._BOOK_PATH.fullmatch(path)
        return (match.group(1), path) if match else None

    def _list_items(
        self, soup: BeautifulSoup, *, provider_label: str = "书宝网全章节"
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for node in soup.select("li"):
            anchor = node.select_one("span.s2 a[href], a[href*='/book']")
            if not anchor:
                continue
            reference = self._book_ref(str(anchor.get("href") or ""))
            if not reference or reference[0] in seen:
                continue
            remote_id, source_ref = reference
            title = self._clean_text(anchor.get_text(" ", strip=True))
            if not title:
                continue
            category_node = node.select_one("span.s1")
            author_node = node.select_one("span.s4, span.s5")
            category = self._clean_text(category_node.get_text(" ", strip=True) if category_node else "").strip("[]")
            author = self._clean_text(author_node.get_text(" ", strip=True) if author_node else "") or "作者未知"
            latest = node.select_one("span.s3 a[href]")
            updated = node.select_one("span.s5") if node.select_one("span.s4") else None
            latest_title = self._clean_text(latest.get_text(" ", strip=True) if latest else "")
            latest_path = urlparse(str(latest.get("href") or "")).path if latest else ""
            updated_at = self._clean_text(updated.get_text(" ", strip=True) if updated else "")
            seen.add(remote_id)
            results.append({
                "provider": self.PROVIDER_ID,
                "provider_label": provider_label,
                "remote_id": remote_id,
                "source_ref": source_ref,
                "title": title,
                "author": author,
                "category": self.CATEGORY_TO_LIBRARY.get(
                    category, category or "未分类"
                ),
                "source_category": category,
                "extension": "txt",
                "book_status": "",
                "license": "站点所有者授权来源",
                "authorization": "site_owner_authorized",
                "downloadable": True,
                "download_mode": "抓取全章节并合并为单本 TXT",
                "source": "remote_authorized",
                "remote_latest_chapter": latest_title,
                "remote_latest_ref": latest_path,
                "remote_updated_at": updated_at,
                "remote_revision": latest_path or f"{updated_at}|{latest_title}",
            })
        return results

    def search(self, query: str, *, limit: int = 0) -> List[Dict[str, Any]]:
        query = query.strip().strip("《》")
        if len(query) < 2:
            raise ValueError("作品名或作者名至少需要 2 个字符")
        response = self._request(
            "/modules/article/search.php", method="POST", data={"searchkey": query}
        )
        direct = self._book_ref(urlparse(response.url).path)
        if direct:
            detail = self.detail(direct[0], direct[1], include_chapters=False)
            results = [{
                **detail,
                "provider_label": "书宝网全章节",
                "license": "站点所有者授权来源",
                "authorization": "site_owner_authorized",
                "downloadable": True,
                "download_mode": "抓取全章节并合并为单本 TXT",
                "source": "remote_authorized",
            }]
        else:
            results = self._list_items(BeautifulSoup(response.text, "html.parser"))
        lowered = query.casefold()
        results.sort(
            key=lambda item: (
                str(item.get("title") or "").casefold() == lowered,
                lowered in str(item.get("title") or "").casefold(),
                lowered in str(item.get("author") or "").casefold(),
            ),
            reverse=True,
        )
        return results if limit <= 0 else results[:limit]

    def catalog_page(self, category: int, page: int) -> List[Dict[str, Any]]:
        category = min(max(int(category), 1), 12)
        page = max(int(page), 1)
        slug, source_category, library_category = self.CATALOG_CATEGORIES[category]
        path = f"/{slug}/" if page == 1 else f"/list/{category}_{page}.html"
        results = self._list_items(BeautifulSoup(self._request(path).text, "html.parser"))
        for item in results:
            item["source_category"] = item.get("source_category") or source_category
            item["category"] = library_category
        return results

    def latest_updates(self, page: int = 1) -> List[Dict[str, Any]]:
        page = max(int(page), 1)
        if page > 1:
            return []
        soup = BeautifulSoup(self._request("/").text, "html.parser")
        heading = next((node for node in soup.select("h2") if "最近更新" in node.get_text()), None)
        listing = heading.find_next("ul") if heading else None
        results = self._list_items(listing or soup)
        for item in results:
            item["book_status"] = "连载中"
        return results

    def detail(self, remote_id: Any, source_ref: str, *, include_chapters: bool = True) -> Dict[str, Any]:
        source_ref = self.validate_source_ref(remote_id, source_ref)
        response = self._request(source_ref)
        soup = BeautifulSoup(response.text, "html.parser")

        def meta(name: str) -> str:
            node = soup.select_one(f'meta[property="{name}"]')
            return self._clean_text(str(node.get("content") or "")) if node else ""

        title = meta("og:novel:book_name") or meta("og:title")
        author = meta("og:novel:author") or "作者未知"
        source_category = meta("og:novel:category") or "未分类"
        category = self.CATEGORY_TO_LIBRARY.get(source_category, source_category)
        status = meta("og:novel:status")
        summary = BeautifulSoup(meta("og:description"), "html.parser").get_text(" ", strip=True)
        cover_url = urljoin(response.url, meta("og:image")) if meta("og:image") else ""
        chapters: List[Dict[str, str]] = []
        seen: set[str] = set()
        if include_chapters:
            chapter_root = soup.select_one("ul#section-list")
            anchors = (
                chapter_root.select("a[href]")
                if chapter_root
                else soup.select("ul.section-list a[href], div.section-box a[href]")
            )
            for anchor in anchors:
                path = urlparse(str(anchor.get("href") or "")).path
                match = self._CHAPTER_PATH.fullmatch(path)
                if not match or match.group(1) != str(remote_id) or path in seen:
                    continue
                seen.add(path)
                chapter_title = self._clean_text(anchor.get_text(" ", strip=True))
                chapter_title = re.sub(
                    r"^(第\s*[0-9零〇一二三四五六七八九十百千万两]+\s*章)\s*晋江文学城\s*[：:]?",
                    r"\1 ",
                    chapter_title,
                ).strip()
                chapters.append({"title": chapter_title, "path": path})
        if include_chapters and not chapters:
            raise RuntimeError("书宝网作品没有可抓取的章节")
        return {
            "provider": self.PROVIDER_ID,
            "remote_id": str(remote_id),
            "source_book_id": str(remote_id),
            "source_ref": source_ref,
            "title": title or f"书宝网 {remote_id}",
            "author": author,
            "categories": [category],
            "category": category,
            "source_category": source_category,
            "book_status": status,
            "summary": summary,
            "cover_url": cover_url,
            "extension": "txt",
            "detail_url": urljoin(self.base_url + "/", source_ref.lstrip("/")),
            "chapter_count": len(chapters),
            "chapters": chapters,
        }

    def _download_chapter(self, index: int, chapter: Dict[str, str]) -> tuple[int, str, str]:
        last_error = ""
        for attempt in range(1, 5):
            try:
                soup = BeautifulSoup(self._request(chapter["path"]).text, "html.parser")
                article = soup.select_one("div#content, article#content, div.content")
                if not article:
                    raise RuntimeError("响应页缺少正文节点")
                for node in article.select("script, style, .adsbygoogle, a[href*='newmessage']"):
                    node.decompose()
                content = unicodedata.normalize("NFKC", article.get_text("\n", strip=True)).replace("\xa0", " ")
                content = re.sub(r"[ \t]+\n", "\n", content)
                content = re.sub(r"\n{3,}", "\n\n", content).strip()
                lines = [
                    line for line in content.splitlines()
                    if line.strip() not in {"【本文首发于】", "本文首发于"}
                ]
                page_titles = [
                    node.get_text(" ", strip=True)
                    for node in soup.select(
                        "div.bookname h1, div.box_con h1, article h1, "
                        "div#content h1, h1"
                    )
                ]
                title = prefer_chapter_title(
                    chapter["title"],
                    [
                        *page_titles,
                        next((line.strip() for line in lines if line.strip()), ""),
                    ],
                )
                content = strip_duplicate_heading("\n".join(lines), title)
                content = re.sub(r"\(\s*[^\n]{0,80}shubaow\.org[^\n]*\)\s*$", "", content, flags=re.I).strip()
                if len(content) < 10:
                    raise RuntimeError("正文为空或过短")
                return index, title, content
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < 4:
                    time.sleep(min(0.5 * attempt, 1.5))
        raise RuntimeError(f"第 {index + 1} 个章节连续 4 次抓取失败：{last_error}")

    def download(self, detail: Dict[str, Any]) -> bytes:
        chapters = list(detail.get("chapters") or [])
        remote_id = str(detail.get("remote_id") or "").strip()
        if not chapters or not remote_id.isdigit():
            raise ValueError("书宝网详情缺少有效章节目录")
        cache_dir = self.chapter_cache_root / remote_id
        cache_dir.mkdir(parents=True, exist_ok=True)
        downloaded: List[Path | None] = [None] * len(chapters)
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
        errors: List[str] = []
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
                except Exception as exc:
                    errors.append(f"章节 {index + 1}: {type(exc).__name__}: {str(exc)[:160]}")
        write_chapter_manifest(cache_dir, manifest)
        if errors or any(item is None for item in downloaded):
            raise RuntimeError(f"书宝网全章节抓取不完整，已取消入库：{'；'.join(errors[:8])}")
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
            raise ValueError("书宝网合并后的作品超过安全大小上限")
        return data
