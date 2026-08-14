"""Site-owner-authorized txt80.cc online search and TXT download adapter."""

from __future__ import annotations

from .error_boundaries import RECOVERABLE_OPERATION_ERRORS

import os
import re
import threading
import time
import unicodedata
from typing import Any, Dict, List
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from oohstory_library.services.library_catalog import normalize_catalog_title


_TXT80_LOCK = threading.Lock()


class AuthorizedTxt80Provider:
    PROVIDER_ID = "authorized_txt80"

    def __init__(self) -> None:
        self.base_url = os.getenv(
            "WEBNOVEL_TXT80_BASE_URL", "https://www.txt80.cc"
        ).rstrip("/")
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "www.txt80.cc",
            "txt80.cc",
        }:
            raise ValueError("txt80 授权来源必须使用 https://www.txt80.cc")
        self.enabled = os.getenv("WEBNOVEL_TXT80_ONLINE_ENABLED", "1").lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        self.delay = max(
            0.5, float(os.getenv("WEBNOVEL_TXT80_ONLINE_DELAY", "1.0"))
        )
        self.max_bytes = max(
            1, int(os.getenv("WEBNOVEL_TXT80_ONLINE_MAX_MB", "120"))
        ) * 1024 * 1024
        configured_hosts = os.getenv(
            "WEBNOVEL_TXT80_DOWNLOAD_HOSTS",
            "www.txt80.cc,txt80.cc,d.txt80.la,d.txt80.com",
        )
        self.allowed_download_hosts = {
            value.strip().lower()
            for value in configured_hosts.split(",")
            if value.strip()
        }
        self._last_request = 0.0
        self._session = self._new_session()

    @staticmethod
    def _new_session() -> requests.Session:
        session = requests.Session()
        session.trust_env = False
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (compatible; WebnovelWriterTxt80/1.0; "
                    "site-owner-authorized)"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
            }
        )
        retry = Retry(
            total=4,
            connect=4,
            read=4,
            status=3,
            backoff_factor=1,
            status_forcelist=(408, 425, 429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
        session.mount("https://", adapter)
        return session

    def availability(self) -> Dict[str, Any]:
        return {
            "id": self.PROVIDER_ID,
            "name": "txt80.cc（站点所有者授权）",
            "enabled": self.enabled,
            "base_url": self.base_url,
            "authorization": "site_owner_authorized",
            "max_download_mb": self.max_bytes // (1024 * 1024),
        }

    def _throttle(self) -> None:
        wait_for = self.delay - (time.monotonic() - self._last_request)
        if wait_for > 0:
            time.sleep(wait_for)
        self._last_request = time.monotonic()

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        if not self.enabled:
            raise RuntimeError("txt80.cc 在线来源已被配置禁用")
        self._throttle()
        response = self._session.request(
            method,
            url,
            timeout=(10, 60),
            **kwargs,
        )
        response.raise_for_status()
        return response

    @staticmethod
    def _clean_text(value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    @classmethod
    def _normalize_title(cls, value: str) -> str:
        value = unicodedata.normalize("NFKC", cls._clean_text(value))
        value = value.removeprefix("《").replace("》", "", 1)
        value = re.sub(
            r"(?:全文|全集|全本)?TXT(?:电子书|小说)?下载.*$",
            "",
            value,
            flags=re.I,
        )
        return normalize_catalog_title(value.strip("《》 []"))

    @staticmethod
    def _source_id(value: str) -> str:
        match = re.search(r"txt(\d+)\.html(?:$|\?)", value or "")
        return match.group(1) if match else ""

    @staticmethod
    def _parse_size(value: str) -> int:
        match = re.search(r"([\d.]+)\s*(B|KB|MB|GB)", value or "", re.I)
        if not match:
            return 0
        return int(
            float(match.group(1))
            * {
                "B": 1,
                "KB": 1024,
                "MB": 1024**2,
                "GB": 1024**3,
            }[match.group(2).upper()]
        )

    @staticmethod
    def validate_source_ref(remote_id: Any, source_ref: str) -> str:
        remote_id = str(remote_id or "").strip()
        if not remote_id.isdigit():
            raise ValueError("txt80.cc 作品标识无效")
        parsed = urlparse(source_ref)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or ".." in parsed.path
            or "\\" in parsed.path
        ):
            raise ValueError("txt80.cc 详情引用必须是安全的站内相对路径")
        if AuthorizedTxt80Provider._source_id(parsed.path) != remote_id:
            raise ValueError("txt80.cc 详情引用与作品标识不匹配")
        return parsed.path

    def search(self, query: str, *, limit: int = 0) -> List[Dict[str, Any]]:
        query = query.strip().strip("《》")
        if len(query) < 2:
            raise ValueError("作品名或作者名至少需要 2 个字符")
        with _TXT80_LOCK:
            response = self._request(
                "POST",
                f"{self.base_url}/e/search/index.php",
                data={
                    "show": "title,softsay,softwriter",
                    "keyboard": query,
                    "tbname": "download",
                    "tempid": "1",
                },
            )
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
        lowered = query.casefold()
        results: List[Dict[str, Any]] = []
        for node in soup.select("div.slist"):
            anchor = node.select_one("div.info h4 a[href*='txt']")
            if not anchor:
                continue
            source_ref = str(anchor.get("href") or "").strip()
            remote_id = self._source_id(source_ref)
            if not remote_id:
                continue
            title = self._normalize_title(anchor.get_text(" ", strip=True))
            meta_links = node.select("p.xm a")
            category = (
                self._clean_text(meta_links[0].get_text())
                if meta_links
                else ""
            )
            author = (
                self._clean_text(meta_links[1].get_text())
                if len(meta_links) > 1
                else "作者未知"
            )
            info = self._clean_text(
                node.select_one("p.l").get_text(" ", strip=True)
                if node.select_one("p.l")
                else ""
            )
            size_match = re.search(r"小说大小[：:]\s*([^|]+)", info)
            filesize = (
                self._clean_text(size_match.group(1)) if size_match else ""
            )
            size_bytes = self._parse_size(filesize)
            if size_bytes and size_bytes > self.max_bytes:
                continue
            downloads_text = self._clean_text(
                node.select_one("h4 span").get_text(" ", strip=True)
                if node.select_one("h4 span")
                else ""
            )
            downloads_match = re.search(r"(\d+)", downloads_text)
            results.append(
                {
                    "provider": self.PROVIDER_ID,
                    "provider_label": "txt80.cc 在线",
                    "remote_id": remote_id,
                    "source_ref": source_ref,
                    "title": title,
                    "author": author,
                    "category": category,
                    "extension": "txt",
                    "filesize": filesize,
                    "size_bytes": size_bytes,
                    "download_count": int(
                        downloads_match.group(1)
                        if downloads_match
                        else 0
                    ),
                    "book_status": "已完结",
                    "license": "站点所有者授权测试来源",
                    "authorization": "site_owner_authorized",
                    "downloadable": True,
                    "source": "remote_authorized",
                    "_rank": (
                        300 if title.casefold() == lowered else 0,
                        200 if lowered in title.casefold() else 0,
                        100 if lowered in author.casefold() else 0,
                        int(downloads_match.group(1)) if downloads_match else 0,
                    ),
                }
            )
        results.sort(key=lambda item: item["_rank"], reverse=True)
        for item in results:
            item.pop("_rank", None)
        return results if limit <= 0 else results[:limit]

    def latest_updates(self, page: int = 1) -> List[Dict[str, Any]]:
        """Return txt80's latest whole-book releases with a resource revision."""

        page = max(int(page), 1)
        path = "/new/" if page == 1 else f"/new/index_{page}.html"
        response = self._request("GET", f"{self.base_url}{path}")
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
        results: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for node in soup.select("div.slist"):
            anchor = node.select_one("div.info h4 a[href*='txt']")
            if not anchor:
                continue
            source_ref = str(anchor.get("href") or "").strip()
            remote_id = self._source_id(source_ref)
            if not remote_id or remote_id in seen:
                continue
            title = self._normalize_title(anchor.get_text(" ", strip=True))
            meta_links = node.select("p.xm a")
            category = (
                self._clean_text(meta_links[0].get_text())
                if meta_links
                else "未分类"
            )
            author = (
                self._clean_text(meta_links[1].get_text())
                if len(meta_links) > 1
                else "作者未知"
            )
            info_node = node.select_one("p.l")
            info = self._clean_text(
                info_node.get_text(" ", strip=True) if info_node else ""
            )
            size_match = re.search(r"小说大小[：:]\s*([^|]+)", info)
            date_match = re.search(r"发布时间[：:]\s*([0-9-]+)", info)
            filesize = self._clean_text(
                size_match.group(1) if size_match else ""
            )
            published_at = date_match.group(1) if date_match else ""
            seen.add(remote_id)
            results.append(
                {
                    "provider": self.PROVIDER_ID,
                    "remote_id": remote_id,
                    "source_ref": urlparse(source_ref).path,
                    "title": title,
                    "author": author,
                    "category": category or "未分类",
                    "book_status": "已完结",
                    "expected_size": filesize,
                    "remote_updated_at": published_at,
                    "remote_revision": f"{remote_id}|{published_at}|{filesize}",
                }
            )
        return results

    def detail(self, remote_id: Any, source_ref: str) -> Dict[str, Any]:
        source_ref = self.validate_source_ref(remote_id, source_ref)
        detail_url = urljoin(self.base_url + "/", source_ref.lstrip("/"))
        with _TXT80_LOCK:
            response = self._request("GET", detail_url)
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
        title_node = soup.select_one("div.detail dd.bt h2")
        title = self._normalize_title(
            title_node.get_text(" ", strip=True)
            if title_node
            else f"txt80 {remote_id}"
        )
        author = "作者未知"
        category = "未分类"
        for node in soup.select("div.detail dd.db"):
            text = self._clean_text(node.get_text(" ", strip=True))
            if text.startswith("小说作者"):
                author = self._clean_text(text.split("：", 1)[-1])
            elif text.startswith("小说分类"):
                category = self._clean_text(text.split("：", 1)[-1])
        down_anchor = soup.select_one(
            "div.downlinks a[href*='/down/'], a[href*='/down/'][href$='.html']"
        )
        if not down_anchor:
            raise RuntimeError("txt80.cc 作品没有下载页")
        download_page_url = urljoin(
            self.base_url, str(down_anchor.get("href") or "").strip()
        )
        with _TXT80_LOCK:
            download_response = self._request("GET", download_page_url)
        download_response.encoding = download_response.apparent_encoding or "utf-8"
        download_soup = BeautifulSoup(download_response.text, "html.parser")
        file_urls = []
        for anchor in download_soup.select("div.downlist a[href]"):
            href = str(anchor.get("href") or "").strip()
            if href.lower().endswith(".txt"):
                file_urls.append(urljoin(self.base_url, href))
        if not file_urls:
            raise RuntimeError("txt80.cc 作品没有可用 TXT 文件")
        cover_node = soup.select_one("div.detail img.pics3[src], img.pics3[src]")
        cover_url = (
            urljoin(detail_url, str(cover_node.get("src") or "").strip())
            if cover_node
            else ""
        )
        return {
            "provider": self.PROVIDER_ID,
            "remote_id": str(remote_id),
            "source_book_id": str(remote_id),
            "title": title,
            "author": author,
            "categories": [category],
            "category": category,
            "extension": "txt",
            "detail_url": detail_url,
            "download_page_url": download_page_url,
            "file_urls": list(dict.fromkeys(file_urls)),
            "cover_url": cover_url,
            "book_status": "已完结",
        }

    def download(self, detail: Dict[str, Any]) -> bytes:
        errors: List[str] = []
        for url in detail.get("file_urls") or []:
            response = None
            try:
                current_url = str(url)
                for _ in range(6):
                    parsed = urlparse(current_url)
                    if (
                        parsed.scheme != "https"
                        or (parsed.hostname or "").lower()
                        not in self.allowed_download_hosts
                    ):
                        raise ValueError(
                            "下载地址不在 txt80.cc 授权 HTTPS 主机白名单"
                        )
                    with _TXT80_LOCK:
                        response = self._request(
                            "GET",
                            current_url,
                            stream=True,
                            allow_redirects=False,
                        )
                    if response.status_code not in {
                        301,
                        302,
                        303,
                        307,
                        308,
                    }:
                        break
                    location = str(
                        response.headers.get("Location") or ""
                    ).strip()
                    response.close()
                    response = None
                    if not location:
                        raise ValueError("txt80.cc 下载跳转缺少目标地址")
                    current_url = urljoin(current_url, location)
                else:
                    raise ValueError("txt80.cc 下载跳转次数过多")
                if response is None:
                    raise RuntimeError("txt80.cc 下载没有返回内容")
                content_type = str(
                    response.headers.get("Content-Type") or ""
                ).lower()
                if "text/html" in content_type:
                    response.close()
                    raise ValueError("txt80.cc 下载返回了 HTML 错误页")
                data = bytearray()
                for chunk in response.iter_content(256 * 1024):
                    if not chunk:
                        continue
                    data.extend(chunk)
                    if len(data) > self.max_bytes:
                        raise ValueError("txt80.cc 作品超过安全大小上限")
                response.close()
                if len(data) < 128:
                    raise ValueError("txt80.cc 作品内容为空或过短")
                return bytes(data)
            except RECOVERABLE_OPERATION_ERRORS as exc:
                errors.append(f"{type(exc).__name__}: {str(exc)[:160]}")
            finally:
                if response is not None:
                    response.close()
        raise RuntimeError("；".join(errors) or "txt80.cc 下载失败")
