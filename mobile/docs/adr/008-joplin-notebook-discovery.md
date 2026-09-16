# ADR 008: Bounded Joplin notebook discovery

- Status: Accepted
- Date: 2026-09-16

## Context

The original Joplin connection flow required users to copy a 32-character
notebook ID manually. That kept the target explicit, but it was error-prone and
did not prove that the Data API token could enumerate the intended notebooks.

Joplin's official Data API exposes `GET /ping`, paginated `GET /folders`, field
selection, and `GET /folders/:id`. It recommends finding the local service by
probing ports 41184 through 41194. Authentication remains a token query
parameter for protected endpoints. The official reference is:
<https://joplinapp.org/help/api/references/rest_api/>.

## Decision

1. Add a connection-only `JoplinApiClient` constructor that has a validated
   loopback port but no export notebook. Export operations still require a full
   `JoplinExportConfiguration`.
2. Send `GET /ping` without a token. Let the user run an explicit, sequential,
   750 ms-per-port scan of the official 41184..41194 range and stop at the first
   exact `JoplinClipperServer` response. Manual ports remain supported.
3. After a port is verified, read `GET /folders` with only
   `id,parent_id,title`, `limit=100`, and explicit page numbers. Read at most 20
   pages / 2,000 notebooks.
4. Validate every 32-character ID, reject duplicate IDs, missing parents and
   ancestry cycles, cap hierarchy depth at 100, and bound display titles to 255
   runes. Construct stable root-to-child paths for the selector.
5. Apply a 10-second bound to both response acquisition and response-stream
   progress. On timeout, cancel the underlying transport request and return a
   provider-safe error that cannot expose the token-bearing URI.
6. Replace the free-text notebook ID field with a two-step desktop dialog:
   verify the port and token, load notebook titles/hierarchy, then select one
   explicit target. The UI states that only notebook titles and hierarchy are
   read.
7. A blank token reuses the token already held by the system credential store.
   A newly entered token is validated in memory and is saved only after the
   selected notebook is verified again.
8. Do not call Joplin search, read note bodies during setup, create notebooks,
   or accept a remote hostname. The endpoint remains fixed to `127.0.0.1`.

## Data flow

```text
explicit scan or manual port
  -> tokenless GET /ping
  -> secure token
  -> GET /folders?fields=id,parent_id,title&limit=100&page=N
  -> validate bounded hierarchy
  -> user selects notebook
  -> GET /folders/:id?fields=id,title
  -> persist port + notebook ID; persist token only in secure storage
```

## Alternatives considered

- Keeping manual notebook IDs was rejected because copy errors are common and
  the setup screen could not show which notebook would receive data.
- Using `/search?type=folder` was rejected because an exhaustive, paginated
  folder listing is simpler and does not introduce search semantics.
- Automatically creating an OOHStory notebook was rejected because setup must
  not mutate Joplin before the user chooses a target.
- Accepting a configurable host was rejected because the token is carried in
  the URI and the integration is intentionally limited to the desktop loopback
  service.

## Consequences

- Users choose a readable notebook path instead of copying an opaque ID.
- Users can locate the standard local service without copying its port, and a
  wrong port cannot receive the Data API token during service detection.
- Setup proves Data API reachability, authorization, enumeration permission and
  final target existence before credentials/configuration are committed.
- Very large or malformed notebook trees fail closed and perform no writes.
- The protocol flow is covered by a disposable Joplin CLI 3.7.1 live test.
  Packaged Joplin Desktop UI and system-secure-storage acceptance are still
  required before enabling the production capability by default.

## Rollback

Disable the Joplin capability flag to hide the flow. Reverting the selector
does not delete the persisted connection, exported notes, tags or resources.
