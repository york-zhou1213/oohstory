from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from . import __version__


class UpstreamUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class UpstreamResult:
    available: bool
    data: dict[str, Any] | None = None
    error: str | None = None


class JsonHttpClient:
    def __init__(self, base_url: str, timeout: float = 4.0, max_bytes: int = 2 * 1024 * 1024):
        if timeout <= 0 or timeout > 30:
            raise ValueError("upstream timeout must be between 0 and 30 seconds")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_bytes = max_bytes

    def get(self, path: str, query: dict[str, object] | None = None, max_bytes: int | None = None) -> dict[str, Any]:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("upstream path must be absolute")
        encoded_query = urllib.parse.urlencode(query or {}, doseq=False)
        url = f"{self.base_url}{path}"
        if encoded_query:
            url = f"{url}?{encoded_query}"
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"Accept": "application/json", "User-Agent": f"OOHStory-Admin/{__version__}"},
        )
        limit = self.max_bytes if max_bytes is None else max_bytes
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise UpstreamUnavailable(f"上游返回 HTTP {response.status}")
                content_type = response.headers.get_content_type()
                if content_type != "application/json":
                    raise UpstreamUnavailable("上游返回了非 JSON 内容")
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise UpstreamUnavailable("上游响应超过安全大小限制")
            data = json.loads(body)
            if not isinstance(data, dict):
                raise UpstreamUnavailable("上游 JSON 结构无效")
            return data
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise KeyError("上游资源不存在") from exc
            raise UpstreamUnavailable(f"上游返回 HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise UpstreamUnavailable("上游连接超时或不可用") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise UpstreamUnavailable("上游返回了无效 JSON") from exc

    def optional(self, path: str, query: dict[str, object] | None = None, max_bytes: int | None = None) -> UpstreamResult:
        try:
            return UpstreamResult(True, self.get(path, query, max_bytes=max_bytes))
        except (UpstreamUnavailable, KeyError, ValueError) as exc:
            return UpstreamResult(False, error=str(exc).strip("'"))


class ReaderClient:
    def __init__(self, client: JsonHttpClient):
        self.client = client

    def health(self) -> UpstreamResult:
        return self.client.optional("/healthz", max_bytes=64 * 1024)

    def home(self) -> UpstreamResult:
        return self.client.optional("/api/v1/home", max_bytes=2 * 1024 * 1024)

    def books(
        self,
        query: str = "",
        category: str = "",
        page: int = 1,
        page_size: int = 24,
        sort: str = "recent",
    ) -> dict[str, Any]:
        if sort not in {"recent", "title", "words", "chapters"}:
            sort = "recent"
        return self.client.get(
            "/api/v1/books",
            {
                "q": query[:100],
                "category": category[:100],
                "page": min(max(page, 1), 1_000_000),
                "page_size": min(max(page_size, 1), 60),
                "sort": sort,
            },
            max_bytes=4 * 1024 * 1024,
        )

    def book(self, public_id: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(public_id, safe="")
        return self.client.get(f"/api/v1/books/{encoded}", max_bytes=2 * 1024 * 1024)

    def chapters(self, public_id: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(public_id, safe="")
        return self.client.get(f"/api/v1/books/{encoded}/chapters", max_bytes=8 * 1024 * 1024)

    def metrics(self, public_id: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(public_id, safe="")
        return self.client.get(f"/api/v1/books/{encoded}/metrics", max_bytes=256 * 1024)
