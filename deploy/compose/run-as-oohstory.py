#!/usr/bin/env python3
"""Prepare small state volumes and drop container privileges."""

from __future__ import annotations

import os
import sys
from pathlib import Path


UID = 10001
GID = 10001
ALLOWED_PREFIX = "/var/lib/oohstory-"


def main() -> int:
    command = sys.argv[1:]
    if not command:
        raise SystemExit("container command is required")
    for raw in os.environ.get("OOHSTORY_CONTAINER_STATE_DIRS", "").split(":"):
        if not raw:
            continue
        if not raw.startswith(ALLOWED_PREFIX):
            raise SystemExit(f"refusing unexpected state path: {raw}")
        path = Path(raw)
        path.mkdir(parents=True, exist_ok=True)
        os.chown(path, UID, GID)
    os.setgroups([])
    os.setgid(GID)
    os.setuid(UID)
    os.execvp(command[0], command)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
