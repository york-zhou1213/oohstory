# ADR 006: Joplin resource provenance and idempotent reconciliation

- Status: Accepted
- Date: 2026-09-16

## Context

ADR 005 deliberately excluded binary Joplin resources because the annotation
export model did not identify an attachment, bind it to a book and annotation,
or record where it came from. Without that contract, a retry could duplicate a
resource, an unrelated resource could be overwritten, and a local removal could
silently delete user data from Joplin.

Joplin's Data API accepts resource bytes as multipart `data` plus JSON `props`.
A live Joplin 3.7.1 acceptance run showed two details that fixture-only testing
did not expose:

- multipart resource create/update accepts a caller-supplied ID but only applies
  the title from `props`; MIME is derived from the uploaded temporary filename,
  while `filename` and `user_data` are not applied;
- note create accepts the stable source but does not apply `application_data`.

Both object types therefore require a deterministic create followed by an
idempotent metadata update before they can be confirmed.

## Decision

1. Extend `AnnotationExportDocument` with optional
   `AnnotationExportAttachment` values. Each attachment carries a stable local
   ID, owning book ID, owning annotation ID, filename, media type, immutable
   bytes, and an explicit `(source, sourceId)` provenance pair.
2. Reject duplicate or unbound identities, control characters, path-like
   filenames, invalid media types, empty bodies, more than 64 attachments,
   resources over 16 MiB, or a per-book total over 64 MiB before any remote
   write.
3. Derive a deterministic resource ID from target, document and attachment
   identity. Send it with the multipart create, then apply title, MIME, filename
   and canonical OOHStory ownership `user_data` through an idempotent JSON PUT.
   The ownership data includes target, document, annotation, attachment and
   provenance identities.
4. Persist the confirmed resource ID plus local and confirmed remote
   fingerprints in `JoplinExportState`. Existing note-only state remains valid
   because the `resources` collection defaults to empty when absent.
5. When local state is unavailable, first retain compatibility with legacy
   generated IDs by scanning at most 2,000 resource metadata records for the
   exact `user_data` marker. Otherwise read the deterministic ID. An exact
   title/size/content match with an empty marker is treated only as an
   interrupted OOHStory create and has its metadata finalized; any other
   occupant is a non-overwriteable conflict.
6. Do not automatically retry `POST /resources`. A later run reads the
   deterministic ID and recovers a create that committed before the connection
   failed. Bounded `GET` and idempotent metadata/blob PUT requests may use the
   shared retry policy.
7. Read both metadata and bounded resource bytes before reconciliation. A
   changed or missing resource stops by default; explicit replacement is
   required. A changed ownership marker is never overwritten. Resolve and
   validate every attachment in one read-only preflight before the first
   resource write, and batch missing-provenance discovery into one bounded
   pagination pass.
8. Add the confirmed resource ID to the exported note with Joplin's
   `:/<resource-id>` Markdown form. The export receipt hash covers the note and
   every local resource fingerprint.
9. Removing an attachment removes its note reference and local mapping but does
   not delete the remote resource. Remote deletion remains a user-controlled
   Joplin action.

## Data contract

```text
AnnotationExportAttachment
  id + book_id + annotation_id
  filename + media_type + immutable bytes
  provenance.source + provenance.source_id

JoplinResourceExportState
  attachment_id -> deterministic or legacy resource_id
  last_local_fingerprint
  last_remote_fingerprint
```

The remote fingerprint covers resource ID, title, media type, filename, size,
ownership data, and the SHA-256 hash of downloaded bytes.

## Dependency graph

```text
AnnotationExportDocument
  -> AnnotationExportAttachment + provenance
  -> JoplinAnnotationExporter
       -> bounded validation
       -> deterministic multipart resource create/update
       -> idempotent JSON metadata finalization
       -> exact user_data legacy recovery scan
       -> JoplinExportStateStore resource mappings
       -> Markdown note resource links
```

## Alternatives considered

- Relying on multipart `props` for all resource metadata was rejected after the
  live service proved that only the ID/title survive that route.
- Letting Joplin choose every resource ID was replaced because a deterministic
  ID gives an unambiguous recovery point before ownership metadata is finalized.
- Retrying resource `POST` was rejected because a committed response loss would
  create a duplicate.
- Automatic remote cleanup was rejected because reference removal does not
  prove that the user no longer needs the resource elsewhere in Joplin.

## Consequences

- Resource creation and recovery are idempotent against the verified Joplin
  3.7.1 Data API behavior.
- Every overwrite decision is tied to both local provenance and confirmed
  remote bytes.
- Recovery scans are bounded and occur only when a resource mapping is absent.
- Local capture, application-owned storage, verified reads, and backup/restore
  are completed by ADR 007. The protocol path has a disposable live acceptance
  harness; signed desktop UI and secure-storage acceptance remain separate.

## Rollback

Disable the existing Joplin capability flag or revert the attachment/resource
adapter changes. Older note-only state continues to load. Rollback never deletes
resources already written to Joplin.
