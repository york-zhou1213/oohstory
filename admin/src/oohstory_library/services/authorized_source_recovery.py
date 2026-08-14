"""Strict cross-source recovery for owner-authorized ebook sources."""

from __future__ import annotations

from .error_boundaries import RECOVERABLE_OPERATION_ERRORS

from typing import Any
from urllib.parse import urljoin

from oohstory_library.services.library_catalog import normalize_catalog_title
from oohstory_library.services.library_identity_claims import normalize_book_identity


UNKNOWN_AUTHORS = {"", "作者未知", "未知", "佚名"}

PERMANENT_ERROR_MARKERS = (
    "404 client error",
    "410 client error",
    "not found",
    "link not found",
    "作品没有下载页",
    "没有可用 txt 文件",
    "没有 zip 下载入口",
    "下载返回了 html 错误页",
    "下载结果不是 zip",
    "zip 内没有 txt 正文",
    "作品没有可抓取的章节",
    "详情缺少章节目录",
    "正文为空或过短",
    "下载没有返回内容",
    "unsupported txt encoding",
    "正文身份与详情不匹配",
    "资源不存在",
    "资源已失效",
)

TRANSIENT_ERROR_MARKERS = (
    "cloudflare",
    "turnstile",
    "timeout",
    "timed out",
    "connection reset",
    "connection aborted",
    "temporary failure",
    "temporarily unavailable",
    "too many requests",
    "429 client error",
    "1205",
    "1213",
    "2006",
    "2013",
    "lock wait timeout",
    "deadlock",
)


def exact_identity(title: Any, author: Any) -> tuple[str, str]:
    return (
        normalize_book_identity(normalize_catalog_title(title)),
        normalize_book_identity(author),
    )


def downloaded_text_matches_identity(text: Any, title: Any, author: Any) -> bool:
    title_key, author_key = exact_identity(title, author)
    if not title_key or not author_key or str(author or "").strip() in UNKNOWN_AUTHORS:
        return False
    head = normalize_book_identity(str(text or "")[:20_000])
    return title_key in head and author_key in head


def is_permanent_source_failure(
    error: BaseException | str,
    *,
    attempts: int = 0,
    max_attempts: int = 1,
) -> bool:
    text = str(error or "").casefold()
    if any(marker in text for marker in TRANSIENT_ERROR_MARKERS):
        return False
    if any(marker in text for marker in PERMANENT_ERROR_MARKERS):
        return True
    return int(attempts) + 1 >= max(int(max_attempts), 1)


def source_name_for_id(source_id: Any) -> str:
    value = str(source_id or "").strip()
    if value.startswith("xbiquge-"):
        return "xbiquge"
    if value.startswith("ixdzs-"):
        return "ixdzs"
    if value.startswith("shubaow-"):
        return "shubaow"
    if value.startswith("linovelib-"):
        return "linovelib"
    if value.isdigit():
        return "txt80"
    raise ValueError("待恢复书目不属于授权正文来源")


def provider_specs(service: Any) -> dict[str, Any]:
    return {
        "txt80": service.txt80_provider,
        "xbiquge": service.xbiquge_provider,
        "ixdzs": service.ixdzs_provider,
        "shubaow": service.shubaow_provider,
        "linovelib": service.linovelib_provider,
    }


def fallback_order(source_name: str) -> tuple[str, ...]:
    """Return production recovery sites; Shubaow remains test-only."""

    orders = {
        "txt80": ("ixdzs", "xbiquge"),
        "xbiquge": ("ixdzs",),
        "ixdzs": ("xbiquge",),
        "shubaow": ("ixdzs", "xbiquge"),
        "linovelib": ("ixdzs", "xbiquge"),
    }
    return orders.get(source_name, ("ixdzs", "xbiquge"))


def source_fields(service: Any, source_name: str, item: dict[str, Any]) -> dict[str, Any]:
    remote_id = str(item.get("remote_id") or "").strip()
    source_ref = str(item.get("source_ref") or "").strip()
    if not remote_id or not source_ref:
        raise ValueError("备用来源缺少作品标识或详情引用")
    if source_name == "txt80":
        source_id = remote_id
    else:
        source_id = f"{source_name}-{remote_id}"
    provider = provider_specs(service)[source_name]
    detail_url = urljoin(provider.base_url + "/", source_ref.lstrip("/"))
    return {
        "source_name": source_name,
        "source_id": source_id,
        "provider": str(item.get("provider") or provider.PROVIDER_ID),
        "remote_id": remote_id,
        "source_ref": source_ref,
        "detail_url": detail_url,
        "title": normalize_catalog_title(item.get("title")),
        "author": str(item.get("author") or "").strip(),
        "category": str(item.get("category") or "").strip(),
        "book_status": str(item.get("book_status") or "").strip(),
    }


def find_exact_fallback_candidates(
    service: Any,
    *,
    title: str,
    author: str,
    current_source_name: str,
    excluded_source_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    target_title, target_author = exact_identity(title, author)
    if not target_title or str(author or "").strip() in UNKNOWN_AUTHORS or not target_author:
        return [], ["作者未知，已拒绝跨源自动匹配"]
    excluded = {str(value) for value in (excluded_source_ids or set()) if value}
    providers = provider_specs(service)
    candidates: list[dict[str, Any]] = []
    errors: list[str] = []
    for source_name in fallback_order(current_source_name):
        provider = providers[source_name]
        try:
            results = provider.search(title, limit=12)
        except RECOVERABLE_OPERATION_ERRORS as exc:
            errors.append(f"{source_name}: {type(exc).__name__}: {str(exc)[:220]}")
            continue
        for item in results:
            candidate_title, candidate_author = exact_identity(
                item.get("title"), item.get("author")
            )
            if (candidate_title, candidate_author) != (target_title, target_author):
                continue
            try:
                candidate = source_fields(service, source_name, item)
            except RECOVERABLE_OPERATION_ERRORS as exc:
                errors.append(f"{source_name}: {type(exc).__name__}: {str(exc)[:220]}")
                continue
            if candidate["source_id"] in excluded:
                continue
            candidates.append(candidate)
            break
    return candidates, errors
