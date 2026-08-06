"""Transactional global identity claims for the electronic-book catalog."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any


def normalize_book_identity(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def book_identity_key(title: Any, author: Any) -> str:
    title_key = normalize_book_identity(title)
    author_key = normalize_book_identity(author)
    return f"{title_key}\x1f{author_key}" if title_key else ""


def book_identity_hash(identity_key: str) -> bytes:
    return hashlib.sha256(identity_key.encode("utf-8")).digest()


def claim_global_book_identity(
    cursor: Any,
    *,
    identity_key: str,
    catalog_id: int | None = None,
) -> tuple[bool, int | None]:
    """Claim one canonical title+author identity in the current transaction.

    The primary key in ``global_book_identity_claims`` serializes concurrent
    importers.  ``INSERT IGNORE`` waits for an uncommitted competing insert,
    so a losing transaction observes the committed canonical catalog id
    instead of performing a racy application-level check followed by insert.
    """

    if not identity_key or not identity_key.split("\x1f", 1)[0]:
        return False, None
    identity_hash = book_identity_hash(identity_key)
    cursor.execute(
        """
        INSERT IGNORE INTO global_book_identity_claims (
            identity_hash, identity_key, catalog_id
        ) VALUES (%s, %s, %s)
        """,
        (identity_hash, identity_key, int(catalog_id) if catalog_id else None),
    )
    if int(cursor.rowcount or 0) == 1:
        return True, int(catalog_id) if catalog_id else None
    cursor.execute(
        """
        SELECT catalog_id
        FROM global_book_identity_claims
        WHERE identity_hash=%s
        FOR UPDATE
        """,
        (identity_hash,),
    )
    row = cursor.fetchone()
    canonical_id = int(row["catalog_id"]) if row and row.get("catalog_id") else None
    return False, canonical_id


def bind_global_book_identity(
    cursor: Any,
    *,
    identity_key: str,
    catalog_id: int,
) -> None:
    cursor.execute(
        """
        UPDATE global_book_identity_claims
        SET catalog_id=%s, identity_key=%s
        WHERE identity_hash=%s AND catalog_id IS NULL
        """,
        (
            int(catalog_id),
            identity_key,
            book_identity_hash(identity_key),
        ),
    )
