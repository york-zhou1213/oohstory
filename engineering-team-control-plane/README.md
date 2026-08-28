# Engineering learning control plane

This Python-standard-library package makes the engineering team's memory, retrieval, learning-receipt, event-ID, and lifecycle requirements executable. It is source-only: it does not activate production, fabricate another role's memory, delete receipts, or migrate the live store by itself.

## Commands

Run from this directory with `python3 scripts/learning_control_plane.py`:

- `audit-task --team-root ROOT --task TASK-ID --require john:implementation` requires the supplied stages to exactly match the task record's `learning-requirements`, rejects every open task receipt, and validates each authoritative stage's closed schema-v2 evidence.
- `audit-system --team-root ROOT [--stale-hours 24]` reports malformed, orphan, and stale open receipts; duplicate event IDs; broken references; and lifecycle, guard, promotion, and knowledge-flow debt.
- `allocate-id --team-root ROOT --kind ERR --owner john [--date YYYYMMDD]` recursively scans all five role stores plus the shared store and persists a reservation while holding an advisory full-store lock. A returned ID cannot be returned by a concurrent caller even before its Markdown header is written.
- `migrate-ids --team-root ROOT` performs a dry run. `--write --backup-dir NEW_EMPTY_DIR` applies the exact plan after a bounded verified backup. `--rollback MANIFEST` restores only files whose hashes still equal the migration's before/after states.
- `verify-deployment --manifest MANIFEST --source-root SOURCE --runtime-root RUNTIME` requires both source and runtime SHA-256 values to equal the committed manifest and rejects unmanifested importable/executable files anywhere in the runtime tree.
- `activate-release --contract CONTRACT --manifest MANIFEST --source-root SOURCE --release-root RELEASE --source-revision SHA --receipt RECEIPT` verifies a versioned release and atomically changes the active selector while recording the exact predecessor. Selector reads, validation, publication, and post-verification share one POSIX transition lock with rollback.
- `install-adapter --contract CONTRACT --adapter-source ACTIVE_ADAPTER --receipt RECEIPT` writes an immutable recovery receipt first, byte-preserves the legacy canonical consumer, then atomically installs the reviewed compatibility adapter. Repeating the command with that receipt resumes any interrupted durable boundary.
- `verify-live-consumer --contract CONTRACT --manifest MANIFEST --source-root SOURCE` proves the canonical consumer preserves legacy lifecycle commands and resolves audit commands to the exact active reviewed release.
- `rollback-release` and `rollback-adapter` require exact-field receipts, validate every referenced release and metadata hash, bind rollback to the current selector, reject path/symlink drift, and post-verify the restored state. Adapter rollback accepts only journal-consistent partial states, resumes cleanup after any durable boundary, and leaves a clean state for reinstall.

## Tests

The suite uses only disposable temporary roots:

```sh
PYTHONPATH=src python3 -m unittest discover -v
python3 -m compileall -q src scripts tests
```

See [docs/EVIDENCE_SCHEMA.md](docs/EVIDENCE_SCHEMA.md), [docs/MIGRATION.md](docs/MIGRATION.md), and [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for operator contracts.

## Known limitations

- Advisory locking uses POSIX `fcntl`; Windows is not supported.
- The migration recognizes event references matching `ERR|FR|LRN|FEAT-YYYYMMDD-SUFFIX`, where the alphanumeric suffix is at least three characters. Text that cites an event through another encoding is not inferred.
- An ambiguous plain reference to a duplicated historical ID requires an explicit occurrence-to-definition resolution. The tool intentionally refuses to guess.
- Task state is read from `tasks/active` and `tasks/archive`; custom task registries require an adapter before activation.
- Runtime adapter v1 intentionally preserves five legacy commands and routes only `audit-task` and `audit-system`; adding or moving a command requires a new reviewed contract version.
