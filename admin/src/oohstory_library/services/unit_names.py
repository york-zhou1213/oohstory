"""Validate systemd unit identifiers owned by OOHStory."""

from __future__ import annotations

def library_unit_name(legacy_name: str) -> str:
    """Return an OOHStory unit name and reject cross-project identifiers.

    Service identity is an ownership boundary.  Silently translating a
    Webnovel-Writer unit into an OOHStory unit can make one project's admin UI
    control the other project's runtime after a copy or merge.
    """

    name = str(legacy_name or "").strip()
    if not name.startswith("oohstory-"):
        raise ValueError("OOHStory may only manage oohstory-* systemd units")
    return name
