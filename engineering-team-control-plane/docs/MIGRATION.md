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

4. Repeat the dry run with `--resolution-file FILE`.
5. Apply with `--write --backup-dir /bounded/new-empty-directory --max-backup-bytes N`.

The backup must be outside the team root and have no symlink ancestor. Before the first source write, the tool writes every changed original, `manifest.json`, and `manifest.sha256`. It verifies source preimages and backup hashes. A second dry run must report no changes.

Rollback with the exact manifest and `--team-root`. The manifest, backup preimages, target root, and each current source hash are verified. A partially applied migration is recoverable when each source is independently in its recorded before or after state. Unknown drift refuses rollback. Rollback never deletes receipts or learning data.
