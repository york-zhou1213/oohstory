#!/usr/bin/env python3
"""Download a Fanqie scan result with the official desktop app and import it."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


from project_paths import APP_ROOT  # noqa: E402


sys.path.insert(0, str(APP_ROOT / "src"))

from oohstory_library.services.ai_service import get_ai_service  # noqa: E402
from oohstory_library.services.electronic_library import ElectronicLibraryService  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Use the pinned official Fanqie desktop downloader in an isolated "
            "display, then normalize and classify its export into the library."
        )
    )
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--book-id", metavar="BOOK_ID")
    parser.add_argument("--title", default="")
    parser.add_argument("--author", default="")
    parser.add_argument(
        "--format", choices=("txt", "epub"), default="txt"
    )
    parser.add_argument("--start-chapter", type=int)
    parser.add_argument("--end-chapter", type=int)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="leave the completed official export in the bridge directory",
    )
    parser.add_argument(
        "--import-existing",
        type=Path,
        metavar="PATH",
        help="import an already completed official bridge export",
    )
    args = parser.parse_args()

    service = ElectronicLibraryService()
    if args.status:
        print(
            json.dumps(
                service.fanqie_downloader.availability(),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if not args.book_id:
        parser.error("--book-id is required unless --status is used")

    if args.import_existing:
        downloaded = {
            "book_id": args.book_id,
            "title": args.title,
            "author": args.author,
            "path": str(args.import_existing),
            "extension": args.import_existing.suffix.lstrip(".").lower(),
            "status": "downloaded",
        }
    else:
        downloaded = service.fanqie_downloader.download(
            args.book_id,
            file_format=args.format,
            start_chapter=args.start_chapter,
            end_chapter=args.end_chapter,
            timeout=args.timeout,
        )
    if args.download_only:
        print(json.dumps(downloaded, ensure_ascii=False, indent=2))
        return
    if args.start_chapter is not None or args.end_chapter is not None:
        raise ValueError(
            "章节区间下载只能用于桥接验证，不能作为完整作品写入全局书库"
        )

    imported = asyncio.run(
        service.import_fanqie_export(
            book_id=args.book_id,
            source_path=Path(downloaded["path"]),
            title=args.title or str(downloaded.get("title") or ""),
            author=args.author or str(downloaded.get("author") or ""),
            ai_service=get_ai_service(),
        )
    )
    print(
        json.dumps(
            {"download": downloaded, "import": imported},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
