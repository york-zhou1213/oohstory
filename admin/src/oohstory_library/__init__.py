"""OOHStory-owned electronic-library engine.

``OOHSTORY_LIBRARY_*`` variables take precedence over the legacy
``WEBNOVEL_*`` names during the source-ownership transition.  The aliases are
process-local and do not read or copy any environment file or secret.
"""

from __future__ import annotations

import os


def library_env_name(legacy_name: str) -> str:
    """Return the preferred OOHStory name for one legacy variable."""

    legacy = str(legacy_name or "").strip()
    if legacy.startswith("WEBNOVEL_LIBRARY_"):
        suffix = legacy.removeprefix("WEBNOVEL_LIBRARY_")
    elif legacy.startswith("WEBNOVEL_"):
        suffix = legacy.removeprefix("WEBNOVEL_")
    else:
        return legacy
    return f"OOHSTORY_LIBRARY_{suffix}"


def library_env(legacy_name: str, default: str | None = None) -> str | None:
    """Read an OOHStory library variable before its legacy equivalent."""

    legacy = str(legacy_name or "").strip()
    preferred = library_env_name(legacy)
    if preferred in os.environ:
        return os.environ[preferred]
    return os.getenv(legacy, default)


def install_legacy_environment_aliases() -> None:
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
