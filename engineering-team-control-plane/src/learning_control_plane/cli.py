"""Command-line interface for the engineering learning control plane."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from typing import Any
from .allocator import allocate_id
from .audit import audit_system, audit_task
from .common import DEFAULT_ROOT, ControlPlaneError
from .deployment import verify_deployment
from .migration import (apply_migration, apply_receipt_disposition,
    load_receipt_dispositions, load_resolutions, plan_migration,
    plan_receipt_disposition, rollback_migration, rollback_receipt_disposition,
    upgrade_current_receipt)
from .release_adapter import (activate_release, install_adapter, rollback_adapter,
                              rollback_release, verify_live_consumer)

def _emit(value: Any) -> None: print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
def _root(args: argparse.Namespace) -> Path: return Path(args.team_root)
def command_audit_task(args):
    result = audit_task(_root(args), args.task, args.require); _emit(result); return 0 if result["ok"] else 2
def command_audit_system(args):
    result = audit_system(_root(args), stale_hours=args.stale_hours); _emit(result); return 0 if result["ok"] else 2
def command_allocate(args): _emit(allocate_id(_root(args), kind=args.kind, owner=args.owner, day=args.date)); return 0
def command_migrate(args):
    if args.rollback: _emit(rollback_migration(Path(args.rollback), expected_root=_root(args))); return 0
    resolutions, resolution_sha = load_resolutions(Path(args.resolution_file) if args.resolution_file else None)
    plan = plan_migration(_root(args), resolutions=resolutions)
    if not args.write:
        _emit({k: v for k, v in plan.items() if not k.startswith("_")} | {"resolution_sha256": resolution_sha, "dry_run": True}); return 0
    if not args.backup_dir: raise ControlPlaneError("--write requires --backup-dir")
    _emit(apply_migration(_root(args), plan, backup_dir=Path(args.backup_dir), max_backup_bytes=args.max_backup_bytes,
        resolution_sha256=resolution_sha)); return 0
def command_migrate_receipts(args):
    if args.rollback:
        _emit(rollback_receipt_disposition(Path(args.rollback), expected_root=_root(args))); return 0
    if not args.disposition_file:
        raise ControlPlaneError("--disposition-file is required unless --rollback is used")
    dispositions, tasks, disposition_sha = load_receipt_dispositions(Path(args.disposition_file))
    plan = plan_receipt_disposition(
        _root(args), dispositions=dispositions, task_requirements=tasks)
    if not args.write:
        _emit({key: value for key, value in plan.items() if not key.startswith("_")} |
              {"disposition_sha256": disposition_sha, "dry_run": True}); return 0
    if not args.backup_dir: raise ControlPlaneError("--write requires --backup-dir")
    _emit(apply_receipt_disposition(
        _root(args), plan, backup_dir=Path(args.backup_dir),
        max_backup_bytes=args.max_backup_bytes,
        disposition_sha256=disposition_sha)); return 0
def command_upgrade_receipt(args):
    result = upgrade_current_receipt(
        _root(args), task=args.task, agent=args.agent, stage=args.stage)
    _emit(result["receipt"]); return 0
def command_verify_deployment(args):
    result = verify_deployment(Path(args.manifest), Path(args.source_root), Path(args.runtime_root)); _emit(result); return 0 if result["ok"] else 2
def command_activate_release(args):
    _emit(activate_release(Path(args.contract), Path(args.manifest), Path(args.source_root),
        Path(args.release_root), args.source_revision, Path(args.receipt))); return 0
def command_install_adapter(args):
    _emit(install_adapter(Path(args.contract), Path(args.adapter_source), Path(args.receipt))); return 0
def command_verify_live_consumer(args):
    result = verify_live_consumer(Path(args.contract), Path(args.manifest), Path(args.source_root)); _emit(result); return 0 if result["ok"] else 2
def command_rollback_release(args):
    _emit(rollback_release(Path(args.contract), Path(args.receipt))); return 0
def command_rollback_adapter(args):
    _emit(rollback_adapter(Path(args.contract), Path(args.receipt))); return 0

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__); commands = parser.add_subparsers(dest="command", required=True)
    item = commands.add_parser("audit-task"); item.add_argument("--team-root", default=DEFAULT_ROOT); item.add_argument("--task", required=True); item.add_argument("--require", action="append", required=True); item.set_defaults(function=command_audit_task)
    item = commands.add_parser("audit-system"); item.add_argument("--team-root", default=DEFAULT_ROOT); item.add_argument("--stale-hours", type=float, default=24.0); item.set_defaults(function=command_audit_system)
    item = commands.add_parser("allocate-id"); item.add_argument("--team-root", default=DEFAULT_ROOT); item.add_argument("--kind", choices=("ERR", "FR", "LRN", "FEAT"), required=True); item.add_argument("--owner", choices=("ken", "john", "jucy", "bob", "mus"), required=True); item.add_argument("--date"); item.set_defaults(function=command_allocate)
    item = commands.add_parser("migrate-ids"); item.add_argument("--team-root", default=DEFAULT_ROOT); item.add_argument("--resolution-file"); item.add_argument("--write", action="store_true"); item.add_argument("--backup-dir"); item.add_argument("--max-backup-bytes", type=int, default=10 * 1024 * 1024); item.add_argument("--rollback", metavar="MANIFEST"); item.set_defaults(function=command_migrate)
    item = commands.add_parser("migrate-receipts"); item.add_argument("--team-root", default=DEFAULT_ROOT); item.add_argument("--disposition-file"); item.add_argument("--write", action="store_true"); item.add_argument("--backup-dir"); item.add_argument("--max-backup-bytes", type=int, default=10 * 1024 * 1024); item.add_argument("--rollback", metavar="MANIFEST"); item.set_defaults(function=command_migrate_receipts)
    item = commands.add_parser("upgrade-receipt"); item.add_argument("--team-root", default=DEFAULT_ROOT); item.add_argument("--task", required=True); item.add_argument("--agent", choices=("ken", "john", "jucy", "bob", "mus"), required=True); item.add_argument("--stage", required=True); item.set_defaults(function=command_upgrade_receipt)
    item = commands.add_parser("verify-deployment"); item.add_argument("--manifest", required=True); item.add_argument("--source-root", required=True); item.add_argument("--runtime-root", required=True); item.set_defaults(function=command_verify_deployment)
    item = commands.add_parser("activate-release"); item.add_argument("--contract", required=True); item.add_argument("--manifest", required=True); item.add_argument("--source-root", required=True); item.add_argument("--release-root", required=True); item.add_argument("--source-revision", required=True); item.add_argument("--receipt", required=True); item.set_defaults(function=command_activate_release)
    item = commands.add_parser("install-adapter"); item.add_argument("--contract", required=True); item.add_argument("--adapter-source", required=True); item.add_argument("--receipt", required=True); item.set_defaults(function=command_install_adapter)
    item = commands.add_parser("verify-live-consumer"); item.add_argument("--contract", required=True); item.add_argument("--manifest", required=True); item.add_argument("--source-root", required=True); item.set_defaults(function=command_verify_live_consumer)
    item = commands.add_parser("rollback-release"); item.add_argument("--contract", required=True); item.add_argument("--receipt", required=True); item.set_defaults(function=command_rollback_release)
    item = commands.add_parser("rollback-adapter"); item.add_argument("--contract", required=True); item.add_argument("--receipt", required=True); item.set_defaults(function=command_rollback_adapter)
    return parser
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try: return int(args.function(args))
    except (ControlPlaneError, OSError) as exc: print(f"ERROR: {exc}", file=sys.stderr); return 2
if __name__ == "__main__": raise SystemExit(main())
