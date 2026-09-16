# ADR 004: Notion annotation export boundary

- Status: Accepted
- Date: 2026-09-15

## Context

OOHStory already has a common `DocumentIdentity`, `Annotation`,
`AnnotationSink`, and `ExportReceipt` contract plus a gated Obsidian exporter.
The next ecosystem slice is a one-way Notion export that updates the same page
on repeat runs without searching or reading the user's whole workspace.

The Notion API is external, rate limited, and credentialed. Its current
2026-03-11 contract distinguishes page parents from data-source parents and
provides deterministic create/read/replace-markdown endpoints. A client-only
internal-connection token is acceptable only on native platforms and only in
system secure storage; it is not suitable for a Web build.

## Requirements

- Export one deterministic Markdown page per OOHStory document beneath an
  explicitly selected Notion page or data source.
- Persist the resulting Notion page ID locally so repeat export updates that
  exact page and never performs workspace search.
- Detect edits made in Notion after the previous export and require explicit
  overwrite confirmation.
- Respect `Retry-After`, bound request/response sizes, ignore additive response
  fields, and never include access tokens or provider bodies in errors/logs.
- Keep the feature off by default and expose it only on native runtimes.
- Let the user disconnect by deleting the secure token, non-secret target
  configuration, and local page mapping without deleting remote Notion pages.

## Decision

1. Add `NotionAnnotationExporter` behind
   `OOHSTORY_NOTION_EXPORT_ENABLED=false` by default.
2. Use only these pinned Notion 2026-03-11 operations:
   `POST /v1/pages`, `GET /v1/pages/{id}/markdown`, and
   `PATCH /v1/pages/{id}/markdown` with `replace_content`.
3. A page parent uses the standard `title` property. A data-source parent uses
   a user-supplied title-property name. OOHStory does not query the data source
   schema or workspace search endpoint.
4. Store the access token in `FlutterSecureStorage` through the existing
   `SecureCredentialStore` boundary. Store target IDs, parent kind, title
   property, page mappings, and content hashes in versioned
   `SharedPreferences` records. No secret is written to preferences.
5. After each acknowledged write, read the remote Markdown and persist its
   hash. Before replacement, compare the current remote hash with that value.
   A mismatch raises a visible conflict; explicit overwrite uses Notion's
   `replace_content` command while leaving `allow_deleting_content=false` so a
   child page/database cannot be silently removed.
6. Do not automatically retry page creation because a timeout can have an
   unknown outcome and a retry could create a duplicate. Safe GET/PATCH calls
   may retry transient responses and must honor `Retry-After`.
7. Disconnect is local-only. It clears connection state and the page mapping
   for that target, but never archives, erases, or deletes Notion content.

## Data contract

```text
NotionExportConfiguration
  parent_kind: page | dataSource
  parent_id: normalized UUID
  title_property: required only for dataSource

NotionExportState
  parent_key + document_id -> page_id
  last_local_content_hash
  last_remote_content_hash
```

`ExportReceipt.target` is the stable `notion:page:<page_id>` identifier and
`ExportReceipt.contentHash` is the deterministic local Markdown hash.

## Dependency graph

```text
ProductCapabilityProfile
        -> OfflineNotesScreen / Notion menu
              -> NotionConnectionRepository
                    -> SharedPreferences (non-secret target)
                    -> SecureCredentialStore (access token)
              -> StoredAnnotationExportSource
              -> NotionAnnotationExporter
                    -> NotionApiClient
                          -> CloudHttpTransport + RetryPolicy
                    -> NotionExportStateStore
```

The critical path is configuration separation -> deterministic exporter ->
conflict-safe remote replacement -> gated UI. OAuth application creation,
production enablement, and real workspace writes remain external acceptance
steps and are not authorized by this ADR.

## Alternatives considered

- Search Notion by title or marker on every run: rejected because it expands
  read scope, creates ambiguous matches, and violates the least-access goal.
- Append blocks on every export: rejected because retries duplicate content and
  do not provide a stable one-page-per-book result.
- Replace content without checking the remote version: rejected because it can
  destroy user edits made in Notion.
- Store the token with the target configuration: rejected because ordinary
  preferences are not secret storage.
- Enable the internal-token flow on Web: rejected because a long-lived bearer
  token would be exposed to the browser runtime.

## Consequences

- Native users can connect an explicitly shared page/data source and export
  without granting whole-workspace read access.
- A timed-out initial create can require manual reconciliation rather than an
  automatic retry; this is preferable to silently producing duplicates.
- Real Notion acceptance still requires an owner-provided test connection and
  target. Until then, the capability remains disabled in production builds.
- The first page title remains the stable record title; later book-title changes
  are reflected inside the regenerated page content.

## Rollback

Disable `OOHSTORY_NOTION_EXPORT_ENABLED` to remove the product entry. Reverting
the adapter does not delete remote pages. Disconnect removes only local token,
configuration, and mappings. Existing ZIP and Obsidian exports remain
independent and usable.
