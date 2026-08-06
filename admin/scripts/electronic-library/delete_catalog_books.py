#!/usr/bin/env python3
"""Delete an explicit bounded catalog selection through OOHStory's service.

Input and output are one JSON object on stdin/stdout so the privileged OOHStory
wrapper never accepts shell arguments or filesystem paths.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from oohstory_library.services.electronic_library import (  # noqa: E402
    get_electronic_library_service,
)


ALLOWED_KEYS = {"catalog_ids", "confirmation"}


def execute(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("请求必须是 JSON 对象")
    unexpected = sorted(set(payload) - ALLOWED_KEYS)
    if unexpected:
        raise ValueError("请求包含未允许字段：" + "、".join(unexpected))
    if set(payload) != ALLOWED_KEYS:
        raise ValueError("请求必须包含 catalog_ids 和 confirmation")
    if not isinstance(payload["catalog_ids"], list):
        raise ValueError("catalog_ids 必须是显式书目 ID 数组")
    if not isinstance(payload["confirmation"], str):
        raise ValueError("confirmation 必须是字符串")
    result = get_electronic_library_service().delete_catalog_books(
        catalog_ids=payload["catalog_ids"],
        confirmation=payload["confirmation"],
    )
    return {"ok": True, **result}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = execute(payload)
    except Exception as exc:
        result = {"ok": False, "error": str(exc)[:2000]}
        json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        sys.stdout.write("\n")
        return 1
    json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"), default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
