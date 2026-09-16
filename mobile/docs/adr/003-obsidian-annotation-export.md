# ADR 003: Obsidian annotation export boundary

- Status: Accepted
- Date: 2026-09-15

## Context

OOHStory needs a first annotation-export integration that is useful without a
hosted OAuth application. Obsidian vaults are ordinary directories, but direct
filesystem export creates two risks: unsafe target paths and overwriting notes
that the reader edited after an earlier export.

The product contract also calls for common `DocumentIdentity`, `Annotation`,
and `ExportReceipt` models so later Notion, Readwise, and Joplin adapters do not
invent incompatible payloads.

## Decision

Introduce a batch-oriented `AnnotationSink` boundary. Each call receives a
document identity and its annotations and returns an `ExportReceipt` containing
the provider, canonical target, content hash, timestamp, and disposition.

The Obsidian adapter:

1. Writes one UTF-8 Markdown file per book beneath a user-selected vault and a
   validated relative subdirectory.
2. Uses a filesystem-safe title plus a stable SHA-256 suffix derived from the
   document ID, so titles can change without creating ambiguous collisions.
3. Emits deterministic YAML front matter and readable Markdown. Generated
   content does not contain an export-time value, so unchanged exports are
   byte-for-byte idempotent.
4. Stores the last successful content hash in local preferences, outside the
   vault. If an existing target no longer matches that receipt, the adapter
   reports a conflict instead of overwriting it.
5. Allows overwrite only after an explicit caller decision. Before a forced
   overwrite, it copies the externally modified note into an
   `.oohstory-backups` directory inside the export folder.
6. Rejects absolute/traversal subdirectories and symbolic-link escapes, and
   replaces files through a temporary swap with rollback on failure.

The UI exposes the action only when `OOHSTORY_OBSIDIAN_EXPORT_ENABLED` is true
and the runtime is not Web. Notion, Readwise, and Joplin remain disabled until
their own authenticated adapters and release evidence exist.

## Consequences

- Users retain control over their vault and receive an overwrite prompt when
  OOHStory detects external edits.
- Removing an annotation updates the generated document on the next export;
  deleting every annotation for a book does not delete an existing vault note.
- The first slice exports text annotations only. The target layout reserves
  relative paths for future attachments, but no attachment bytes are currently
  present in OOHStory's annotation model.
- Web cannot offer direct vault-directory writes and therefore keeps this
  capability hidden.
