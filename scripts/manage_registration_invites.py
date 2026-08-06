#!/usr/bin/env python3
"""Offline operator CLI for hashed reader-registration invitations."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.accounts import AccountError, AccountStore


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument(
        "--database",
        type=Path,
        default=Path(
            os.getenv(
                "OOHSTORY_ACCOUNT_DATABASE",
                str(Path(os.getenv("OOHSTORY_STATE_ROOT", "var")) / "accounts.sqlite3"),
            )
        ),
    )
    actions = command.add_subparsers(dest="action", required=True)
    create = actions.add_parser("create", help="create an invitation; plaintext is printed once")
    create.add_argument("--label", default="")
    create.add_argument("--max-uses", type=int, default=1)
    create.add_argument("--expires-days", type=int, default=30)
    actions.add_parser("list", help="list invitation metadata without plaintext codes")
    revoke = actions.add_parser("revoke", help="revoke an invitation by id")
    revoke.add_argument("invite_id")
    return command


def main() -> int:
    args = parser().parse_args()
    store = AccountStore(args.database, session_ttl_seconds=3600)
    try:
        if args.action == "create":
            code, item = store.create_invite(
                label=args.label,
                max_uses=args.max_uses,
                expires_in_days=args.expires_days,
            )
            print(json.dumps(item | {"code": code}, ensure_ascii=False))
        elif args.action == "list":
            print(json.dumps({"items": store.list_invites()}, ensure_ascii=False))
        else:
            store.revoke_invite(args.invite_id)
            print(json.dumps({"revoked": args.invite_id}, ensure_ascii=False))
    except AccountError as exc:
        raise SystemExit(exc.detail) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
