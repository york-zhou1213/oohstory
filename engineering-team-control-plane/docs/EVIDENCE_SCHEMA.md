# Closure evidence schema

`audit-task` requires receipt `schema_version: 2`. Schema-v1 receipts remain readable by `audit-system`, but cannot silently satisfy the new memory/RAG gate.

The canonical compatibility consumer preserves the legacy `preflight` and `close`
arguments. `preflight` still runs the byte-exact legacy writer. After a successful
legacy `close`, the reviewed adapter immediately upgrades that exact closed receipt
to schema v2 through the active release. The upgrade binds the closure-day owner
memory plus every consulted lesson to current byte counts, full-file line ranges,
and SHA-256 values. If the upgrade fails, the closed schema-v1 receipt remains
recoverable: repeat the same `close` command after repairing the missing evidence;
the legacy close is idempotent and the schema-v2 upgrade is independently
idempotent.

Because the preserved legacy writer performs deterministic local lesson selection
rather than provider-backed semantic search, this compatibility upgrade records
`exact_file_fallback`, `semantic_recall.status=unavailable`, and every selected file
in both `fallback_files` and hash/line-bound `exact_retrieval`. It never labels the
legacy selection as a successful semantic recall.

The authoritative task record must declare the complete participating stage set in front matter:

```yaml
learning-requirements:
  - john:implementation
  - bob:review
```

Every `--require` value must exactly match this set. A missing requirement, an extra caller-supplied requirement, a missing/invalid authoritative receipt, or any additional open receipt under the task fails closed. Closed historical/rework receipts may remain for append-only audit history.

```json
{
  "schema_version": 2,
  "task_id": "TASK-EXAMPLE",
  "agent": "john",
  "stage": "implementation",
  "status": "closed",
  "closed_at": "2026-08-25T18:30:00+08:00",
  "closure_evidence": {
    "schema_version": 1,
    "owner_day_memory": {
      "date": "2026-08-25",
      "path": "john/memory/2026-08-25.md",
      "bytes": 1234,
      "sha256": "<lowercase SHA-256>"
    },
    "retrieval_evidence": {
      "mode": "semantic_recall",
      "semantic_recall": {
        "status": "ok",
        "query": "TASK-EXAMPLE backend high implementation",
        "hits": ["john/memory/2026-08-25.md:10-14"]
      },
      "exact_retrieval": [{
        "path": "john/memory/2026-08-25.md",
        "line_start": 10,
        "line_end": 14,
        "sha256": "<lowercase SHA-256>"
      }]
    }
  }
}
```

The memory date is compared with the calendar date in `closed_at`'s declared timezone. Paths are root-relative, traversal and symlinks are forbidden, the file must be non-empty UTF-8, and byte count and SHA-256 bind the exact content.

When semantic search is unavailable, set `mode` to `exact_file_fallback`, semantic status to `unavailable`, record a non-empty reason and query, and add `fallback_files`. Every fallback path must also appear as a hash- and line-bound `exact_retrieval` item. Exact retrieval must include the owner-day memory under both modes.

Historical schema-v1 receipts are never silently upgraded. Before activation,
`migrate-receipts` requires an explicit disposition for every schema-v1 receipt:
either `retain` with a non-empty reason or `upgrade` with complete closure evidence
that already passes this validator. The write produces a hash-bound backup manifest
even when every receipt is retained; upgraded receipts can be restored byte-for-byte
with the same manifest.
