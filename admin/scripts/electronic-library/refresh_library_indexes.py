#!/usr/bin/env python3
"""Legacy manual refresh for the shared tone index only.

Derived data is written to ``electronic-library/txt80/全局索引``. The source
catalog is opened read-only by ElectronicLibraryService. Plot indexing is
intentionally absent: it can only be requested from the admin plot-index API.
"""

import fcntl
from pathlib import Path
import sys


from project_paths import APP_ROOT  # noqa: E402
LOCK_PATH = (
    APP_ROOT
    / "electronic-library"
    / "txt80"
    / "全局索引"
    / ".library-index-refresh.lock"
)


sys.path.insert(0, str(APP_ROOT / "src"))

from oohstory_library.services.electronic_library import ElectronicLibraryService  # noqa: E402


def main() -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        service = ElectronicLibraryService()
        base = service.build_index(force=False)
        print(base["message"], flush=True)


if __name__ == "__main__":
    main()
