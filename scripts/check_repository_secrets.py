#!/usr/bin/env python3
"""Fail CI when a repository contains common secret or private artifact patterns."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
IGNORED_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules"}
FORBIDDEN_SUFFIXES = {
    ".aab", ".apk", ".db", ".jks", ".key", ".p12", ".pem", ".pfx",
    ".sqlite", ".sqlite3", ".tar", ".tgz", ".zip",
}
FORBIDDEN_NAMES = {".env", "id_rsa", "id_ed25519"}
TEXT_RULES = {
    "private-key marker": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "GitHub token": re.compile(rb"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"),
    "Slack token": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "Google API key": re.compile(rb"\bAIza[A-Za-z0-9_-]{30,}\b"),
    "embedded bearer token": re.compile(rb"(?i)authorization\s*[:=]\s*['\"]?bearer\s+[A-Za-z0-9._~+/-]{20,}"),
    "Cloudflare beacon token": re.compile(rb"data-cf-beacon\s*=", re.IGNORECASE),
    "host-specific root path": re.compile(rb"/(?:root|home)/[^/\s]+/"),
    "production library mount": re.compile(rb"/mnt/" + rb"electronic-library"),
}


def is_ignored(path: Path) -> bool:
    return any(part in IGNORED_PARTS for part in path.relative_to(ROOT).parts)


def main() -> int:
    findings: list[tuple[str, int, str]] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or is_ignored(path):
            continue
        relative = path.relative_to(ROOT)
        lower_name = path.name.casefold()
        if lower_name in FORBIDDEN_NAMES or path.suffix.casefold() in FORBIDDEN_SUFFIXES:
            findings.append((relative.as_posix(), 0, "private/binary artifact"))
            continue
        if re.fullmatch(r"[0-9a-f]{32,64}\.txt", lower_name):
            findings.append((relative.as_posix(), 0, "site-verification key file"))
            continue
        try:
            payload = path.read_bytes()
        except OSError:
            findings.append((relative.as_posix(), 0, "unreadable file"))
            continue
        if b"\x00" in payload:
            continue
        for label, rule in TEXT_RULES.items():
            match = rule.search(payload)
            if match:
                line = payload.count(b"\n", 0, match.start()) + 1
                findings.append((relative.as_posix(), line, label))

    if findings:
        for filename, line, label in findings:
            location = f"{filename}:{line}" if line else filename
            print(f"FAIL {location}: {label}")
        return 1
    print("Repository secret/artifact scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
