# ADR 007: Local annotation attachment storage and backup

- Status: Accepted
- Date: 2026-09-16

## Context

ADR 006 defined a provenance-bound Joplin resource contract, but stored
annotations still emitted text only. Putting binary data in SharedPreferences
would make preference reads and writes unbounded, while retaining the original
picker path would break as soon as the source file moved or its permission was
revoked.

The local store also participates in OOHStory offline backup and restore. An
attachment feature that skipped that path would appear durable on one device
but disappear after migration.

## Decision

1. Copy each selected non-empty file into the application documents directory
   under `annotation_attachments/<generated-id>.bin`. SharedPreferences stores
   only the attachment identity and metadata.
2. Derive the stable local ID from the owning annotation ID and the content
   SHA-256. Reattaching identical bytes to the same annotation updates one
   record instead of creating a duplicate.
3. Keep the stored filename as display and Joplin resource metadata, but never
   use it in a local path. Accept only application-generated attachment IDs
   when reading or deleting files.
4. Limit one attachment to 16 MiB and one book to 64 attachments / 64 MiB, the
   same preflight envelope enforced by the Joplin exporter. Verify byte length
   and SHA-256 on every export or backup read.
5. Reuse one attachment picker and management dialog in both the native batch
   annotation screen and the local reader's annotation sheet. Users can add,
   list, and remove local attachments without leaving the reading context.
   Deleting an annotation or its local book also removes its application-owned
   attachment files.
6. Keep the existing text-only export source synchronous for Obsidian and
   Notion. A separate asynchronous source loads verified attachment bytes only
   for Joplin, so an attachment integrity failure cannot block unrelated text
   exports.
7. Advance the offline backup schema to version 3. Store metadata in the
   manifest and verified bytes under `attachments/`. Restore versions 1 and 2
   as attachment-free backups; validate all version 3 identities, ownership,
   sizes, and hashes before replacing the attachment directory.
8. Local removal does not request deletion of an already exported Joplin
   resource, preserving ADR 006's remote-data boundary.
9. Keep the existing CSV, Markdown, HTML, TXT, and JSON annotation files in the
   manual export ZIP. Add `attachments.json`, verified binary entries under
   `attachments/`, and relative links from Markdown, HTML, and TXT. Use only
   generated IDs plus a bounded safe extension for archive paths; the original
   filename remains metadata. Cap all exported attachment bytes at 256 MiB
   before building the in-memory archive.

## Dependency graph

```text
OfflineNotesScreen + file picker
LocalReaderScreen annotation sheet
  -> LocalStorageService
       -> SharedPreferences attachment metadata
       -> application-owned binary files
       -> offline backup schema 3
  -> StoredAnnotationExportSource.documentsWithAttachmentsFrom
       -> verified immutable bytes
       -> JoplinAnnotationExportService
```

## Alternatives considered

- Base64 in SharedPreferences was rejected because binary size would inflate
  preference storage and make every metadata read unbounded.
- Referencing the picker source path was rejected because external paths and
  grants are not durable.
- Loading attachments for every export adapter was rejected because Obsidian
  and Notion currently export text only.
- Automatic remote resource deletion was rejected because OOHStory cannot
  prove the resource has no other Joplin references.

## Consequences

- The complete local-to-Joplin attachment path is now product-reachable on
  native platforms and survives OOHStory backup/restore.
- Manual annotation ZIP exports preserve attachment names and ownership in a
  machine-readable index while keeping archive paths traversal-safe.
- Duplicate content within one annotation has one stable local identity.
- Tampered, missing, empty, or oversized attachment files fail closed before a
  Joplin write.
- Web does not show the local attachment picker. Joplin export remains a gated
  desktop capability pending a real Data API acceptance run.

## Rollback

Disable the Joplin capability to hide remote export. Reverting the UI does not
delete local attachment files. Before reverting storage schema support, export
or preserve schema 3 backups; older code does not understand their attachment
entries.
