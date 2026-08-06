"""Resolve the canonical OOHStory backend root from source or data mirrors."""

from __future__ import annotations

import os
from pathlib import Path


def install_legacy_environment_aliases() -> None:
    """Expose OOHStory-owned variables to copied transitional code.

    The aliases are process-local. They make the OOHStory names authoritative
    even in scripts that read their configuration before importing the engine
    package.
    """

    prefix = "OOHSTORY_LIBRARY_"
    for name, value in tuple(os.environ.items()):
        if not name.startswith(prefix):
            continue
        suffix = name.removeprefix(prefix)
        if not suffix:
            continue
        os.environ[f"WEBNOVEL_{suffix}"] = value
        os.environ[f"WEBNOVEL_LIBRARY_{suffix}"] = value


install_legacy_environment_aliases()


def discover_app_root() -> Path:
    candidates: list[Path] = []
    configured = (
        os.environ.get("OOHSTORY_BACKEND_ROOT", "").strip()
    )
    if configured:
        candidates.append(Path(configured).expanduser())

    for start in (Path.cwd(), Path(__file__).absolute()):
        candidates.extend((start, *start.parents))

    canonical = Path("/opt/oohstory-admin")
    candidates.append(canonical)

    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.absolute()
        if candidate in seen:
            continue
        seen.add(candidate)
        if (
            (candidate / "src" / "oohstory_library" / "services").is_dir()
            and (candidate / "deploy" / "mysql").is_dir()
            and (candidate / "pyproject.toml").is_file()
        ):
            return candidate
    raise RuntimeError(
        "无法定位 oohstory-backend 项目根目录；"
        "请设置 OOHSTORY_BACKEND_ROOT"
    )


APP_ROOT = discover_app_root()
