#!/usr/bin/env python3
"""Read-only latency benchmark for the configured MySQL 8 catalog."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable


from project_paths import APP_ROOT  # noqa: E402
sys.path.insert(0, str(APP_ROOT / "src"))

from oohstory_library.services.library_catalog_mysql import MySQLCatalogStore  # noqa: E402
from oohstory_library.services.library_database import (  # noqa: E402
    LibraryInfrastructureSettings,
)


def require_mysql(settings: LibraryInfrastructureSettings) -> None:
    if settings.catalog_backend != "mysql":
        raise RuntimeError(
            "benchmark_catalog_scale.py only supports MySQL; set "
            "WEBNOVEL_CATALOG_BACKEND=mysql"
        )


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, math.ceil(len(ordered) * quantile) - 1)
    return ordered[index]


def measure(
    callback: Callable[[], dict[str, Any]],
    *,
    iterations: int,
) -> dict[str, Any]:
    samples: list[float] = []
    last: dict[str, Any] = {}
    for _ in range(max(iterations, 1)):
        started = time.perf_counter()
        last = callback()
        samples.append(time.perf_counter() - started)
    return {
        "iterations": len(samples),
        "min_seconds": round(min(samples), 6),
        "median_seconds": round(statistics.median(samples), 6),
        "p95_seconds": round(percentile(samples, 0.95), 6),
        "max_seconds": round(max(samples), 6),
        "total": int(last.get("total") or 0),
        "rows": len(last.get("rows") or []),
    }


def benchmark(
    store: MySQLCatalogStore,
    *,
    library: str,
    availability: str,
    page_size: int,
    pages: list[int],
    iterations: int,
    query: str = "",
) -> dict[str, Any]:
    """Benchmark catalog service reads without attaching a Redis client."""
    cases: dict[str, dict[str, Any]] = {}
    for page in pages:
        label = f"page_{page}"
        cases[label] = measure(
            lambda page=page: store.browse_catalog(
                library=library,
                query="",
                category="",
                availability=availability,
                page=page,
                page_size=page_size,
            ),
            iterations=iterations,
        )
    if query:
        cases["search"] = measure(
            lambda: store.browse_catalog(
                library=library,
                query=query,
                category="",
                availability=availability,
                page=1,
                page_size=page_size,
            ),
            iterations=iterations,
        )
    return cases


def parse_pages(value: str) -> list[int]:
    pages: list[int] = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        page = int(token)
        if page < 1:
            raise argparse.ArgumentTypeError("pages must be positive")
        if page not in pages:
            pages.append(page)
    if not pages:
        raise argparse.ArgumentTypeError("at least one page is required")
    return pages


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "对当前 MySQL 8 书目执行只读查询基准；"
            "不造数、不迁移、不写 Redis 缓存。"
        )
    )
    parser.add_argument(
        "--library",
        choices=("all", "local", "fanqie"),
        default="all",
    )
    parser.add_argument(
        "--availability",
        choices=("all", "readable", "recovery"),
        default="all",
    )
    parser.add_argument("--pages", type=parse_pages, default=[1, 500, 5000])
    parser.add_argument("--page-size", type=int, default=24)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument(
        "--query",
        default="",
        help="可选的真实搜索词；默认跳过全文检索，绝不写入测试数据",
    )
    args = parser.parse_args()
    if not 1 <= args.page_size <= 60:
        parser.error("--page-size must be between 1 and 60")
    if not 1 <= args.iterations <= 100:
        parser.error("--iterations must be between 1 and 100")

    settings = LibraryInfrastructureSettings.from_env()
    require_mysql(settings)
    # A None Redis client is intentional: MySQLCatalogStore still uses the
    # production SQL service but cannot read or write a cached benchmark result.
    store = MySQLCatalogStore(settings, cache_client=None)
    started = time.perf_counter()
    cases = benchmark(
        store,
        library=args.library,
        availability=args.availability,
        page_size=args.page_size,
        pages=args.pages,
        iterations=args.iterations,
        query=args.query.strip(),
    )
    payload = {
        "mode": "read-only",
        "backend": "mysql",
        "database": settings.mysql_database,
        "library": args.library,
        "availability": args.availability,
        "page_size": args.page_size,
        "cases": cases,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
