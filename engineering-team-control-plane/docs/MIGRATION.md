# Event-ID migration and rollback

Migration is dry-run by default and consumes all Markdown, JSON, and JSONL files in the five role learning stores and shared team-learning store. The plan records every input SHA-256, not just changed files.

1. Run a dry run on a bounded copy of the store.
2. Repair broken references before continuing.
3. For each ambiguous reference shown as `PATH:LINE:COLUMN:EVENT-ID`, create a resolution file:

```json
{
  "schema_version": 1,
  "references": {
    "team-learnings/TEAM_LEARNINGS.md:20:8:ERR-20260825-001": "john/learnings/ERRORS.md:7"
  }
}
```

Targets are exact definition locators. Unused, stale, or wrong-ID resolutions fail closed.

A duplicate-ID reference is inferred only when its byte position lies inside the Markdown section opened by that exact level-2 definition header and before the next ATX or Setext level-1/2 heading, which proves a structural self-reference. Fenced examples are ignored. Merely sharing a file with one duplicated definition is not evidence; references outside that section require an exact resolution entry.

4. Repeat the dry run with `--resolution-file FILE`.
5. Apply with `--write --backup-dir /bounded/new-empty-directory --max-backup-bytes N`.

The backup must be outside the team root and have no symlink ancestor. Before the first source write, the tool writes every changed original, `manifest.json`, and `manifest.sha256`. It verifies source preimages and backup hashes. A second dry run must report no changes.

Rollback with the exact manifest and `--team-root`. The manifest, backup preimages, target root, and each current source hash are verified. A partially applied migration is recoverable when each source is independently in its recorded before or after state. Unknown drift refuses rollback. Rollback never deletes receipts or learning data.

## Receipt schema disposition

Receipt schema changes use a separate, explicit migration. First inventory every
schema-v1 receipt and create one disposition entry for each exact root-relative
path. Omitting a schema-v1 receipt or naming a schema-v2/nonexistent receipt fails
closed.

```json
{
  "schema_version": 1,
  "tasks": {
    "TASK-CURRENT": ["john:implementation", "bob:review"]
  },
  "receipts": {
    "team-learnings/receipts/TASK-OLD/john-implementation.json": {
      "action": "retain",
      "reason": "historical receipt is not authoritative for a current closure"
    },
    "team-learnings/receipts/TASK-CURRENT/ken-release.json": {
      "action": "upgrade",
      "closure_evidence": {"schema_version": 1}
    }
  }
}
```

The optional `tasks` object is the only migration path for adding a missing
authoritative `learning-requirements` set. Each selected task must have exactly one
real active/archive record, the list must be non-empty, unique, and valid, and an
existing declaration must match byte-for-byte in order. A conflicting declaration
fails closed. Task-record changes share the same preimage backup, manifest, and
rollback transaction as receipt upgrades.

An `upgrade` entry must contain the complete closure-evidence object documented in
`EVIDENCE_SCHEMA.md`; abbreviated evidence in the example is intentionally invalid.
Run a dry run, then apply only with a new bounded backup directory:

```sh
python3 scripts/learning_control_plane.py migrate-receipts \
  --team-root TEAM_ROOT --disposition-file dispositions.json
python3 scripts/learning_control_plane.py migrate-receipts \
  --team-root TEAM_ROOT --disposition-file dispositions.json --write \
  --backup-dir BOUNDED_NEW_EMPTY_DIRECTORY --max-backup-bytes N
```

The manifest binds the disposition-file hash, the hash of every schema-v1 input,
all retain/upgrade decisions, every changed preimage, and every upgraded hash. A
retain-only run still writes the manifest and sidecar without mutating receipts.
Rollback is exact and idempotent:

```sh
python3 scripts/learning_control_plane.py migrate-receipts \
  --team-root TEAM_ROOT --rollback BOUNDED_BACKUP_DIR/manifest.json
```
