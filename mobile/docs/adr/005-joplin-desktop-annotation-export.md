# ADR 005: Joplin desktop annotation export boundary

- Status: Accepted
- Date: 2026-09-15

## Context

OOHStory already renders deterministic annotation Markdown and has native-only,
capability-gated Obsidian and Notion exporters. The next ecosystem slice is a
Joplin desktop export through the Joplin Data API exposed by the Web Clipper
service.

The Data API normally listens on localhost port 41184 and requires its token as
a query parameter. That makes arbitrary endpoints, browser builds, URI logging,
and ordinary preference storage unacceptable. Joplin supports caller-supplied
32-character hexadecimal IDs, which allows retries to recover an acknowledged
create without generating duplicate notes or tags.

## Decision

1. Add `JoplinAnnotationExporter` behind
   `OOHSTORY_JOPLIN_EXPORT_ENABLED=false` by default and expose it only on
   Linux, Windows, and macOS.
2. Construct the endpoint from an explicitly configured port and the fixed
   host `127.0.0.1`. Do not accept a URL or hostname. Before saving a connection,
   require both `GET /ping` and `GET /folders/{id}` to succeed. ADR 008 replaces
   manual notebook-ID entry with bounded `GET /folders` discovery and explicit
   selection.
3. Store the Data API token only through `SecureCredentialStore`. Store the
   port, notebook ID, export mappings, and content fingerprints in versioned
   `SharedPreferences` records.
4. Derive each note ID from the target notebook and OOHStory document ID. Create
   notes with that ID, an OOHStory ownership marker in `application_data`, the
   deterministic Markdown body, and the explicitly selected `parent_id`.
5. Derive stable IDs for the `oohstory` and `reading-notes` tags. Read before
   create, validate any existing title, then attach only missing relationships.
6. Do not automatically retry any POST. Safe GET and PUT requests may use the
   shared bounded retry policy. A later run can recover a timed-out create by
   reading the deterministic ID.
7. Fingerprint the last confirmed remote note title, body, parent, and ownership
   marker. If the current fingerprint differs, stop until the user explicitly
   confirms replacement. A missing remote note is also a conflict and is not
   silently recreated.
8. Disconnect removes only local credentials, configuration, and mappings. It
   never deletes Joplin notes or tags.

## Scope boundary

This slice exports one Markdown note per book and maintains two stable tags.
Binary resource upload and reconciliation are defined separately by ADR 006.
Local attachment capture and persistence are defined by ADR 007, and bounded
notebook discovery by ADR 008. Remote resource deletion remains deliberately
excluded.

## Data contract

```text
JoplinExportConfiguration
  port: 1024..65535
  notebook_id: 32 lowercase hexadecimal characters

JoplinExportState
  target_key + document_id -> deterministic note_id
  last_local_content_hash
  last_remote_note_fingerprint
  optional attachment_id -> generated resource_id mappings (ADR 006)
```

`ExportReceipt.target` is `joplin:note:<note_id>` and its content hash is the
deterministic local Markdown hash, combined with local resource fingerprints
when attachments are present.

The target key and deterministic note ID deliberately exclude the local port:
changing Joplin's Web Clipper port must reconnect to the same notebook notes,
not create duplicates.

## Dependency graph

```text
ProductCapabilityProfile
        -> OfflineNotesScreen / Joplin menu
              -> JoplinConnectionRepository
                    -> SharedPreferences (port and notebook)
                    -> SecureCredentialStore (Data API token)
              -> StoredAnnotationExportSource
              -> JoplinAnnotationExporter
                    -> JoplinApiClient
                          -> fixed 127.0.0.1 endpoint
                          -> CloudHttpTransport + RetryPolicy
                    -> JoplinExportStateStore
```

## Consequences

- A desktop user can export to one explicit notebook without exposing the Data
  API token to Web or allowing requests to arbitrary hosts.
- Deterministic note/tag IDs make ambiguous create recovery safe. Generated
  resource IDs use the provenance recovery boundary in ADR 006.
- Closing Joplin or disabling Web Clipper makes export unavailable; it does not
  affect local annotations.
- Mobile, server-hosted Joplin, local attachment capture, and bidirectional
  deletion remain outside this release claim. Binary resource export uses the
  separate ADR 006 boundary.

## Rollback

Disable `OOHSTORY_JOPLIN_EXPORT_ENABLED` to remove the product entry. Reverting
the adapter or disconnecting only removes local state. Notes and tags already
created in Joplin remain available and must be deleted by the user in Joplin if
desired.
