# ADR 009: Readwise annotation export with stable remote identity

- Status: Accepted
- Date: 2026-09-17

## Context

OOHStory already has a provider-neutral annotation model and gated exports for
Obsidian, Notion, and Joplin. Readwise remained only a contract name. A usable
integration must keep the access token out of ordinary preferences, avoid
duplicate highlights after ambiguous network failures, and not overwrite edits
made in Readwise without an explicit user decision.

Readwise's official API uses a per-user access token, de-duplicates highlight
creation by source metadata, and supports a stable `highlight_url`. It also
offers detail, patch, and paginated export endpoints.

## Decision

The native client exposes a separately gated, one-way Readwise export:

- The access token is stored only through `SecureCredentialStore` and is
  verified with `GET /api/v2/auth/` before it replaces a saved token.
- Each OOHStory annotation receives a deterministic, opaque HTTPS
  `highlight_url`; raw book and annotation IDs do not leave the app in URLs.
- Acknowledged Readwise highlight IDs and local/remote managed-field hashes are
  kept in local preferences. Tokens are never stored with that state.
- Initial creation uses `POST /api/v2/highlights/`. If a retry is de-duplicated
  and returns no modified ID, recovery searches the bounded export feed for the
  stable URL rather than creating a second identity.
- Repeat export reads the remote highlight. Text, note, location, or deletion
  changes made remotely become conflicts. OOHStory only patches or recreates a
  conflict after explicit confirmation.
- Disconnect removes the local token and receipts. It never deletes Readwise
  content. Attachments are not uploaded because the Readwise highlight API does
  not provide a matching binary-resource contract.
- Web remains excluded because this release has no equivalent trusted token
  boundary. The capability is off by default until a real-account acceptance
  run is recorded.

## API and state contract

- Authentication: `Authorization: Token <secret>` over HTTPS to
  `readwise.io` only.
- Endpoints: auth check, highlight create/detail/patch, and bounded paginated
  export recovery.
- Managed remote fields: `text`, `note`, `location`, `location_type`.
- Local state version: `oohstory_readwise_export_states_v1`.
- Errors use the existing `CoreErrorCode` mapping; provider response bodies and
  credentials are never surfaced in user messages.

## Alternatives considered

- Re-posting without stable identity was rejected because a timeout could
  create duplicates.
- Blindly patching on every export was rejected because it would erase remote
  edits.
- Two-way deletion was rejected as a default because deleting a local
  annotation must not unexpectedly destroy third-party data.
- Storing the token in `SharedPreferences` was rejected because those values
  are ordinary application configuration, not secret storage.

## Consequences

The integration is idempotent and conflict-safe for OOHStory-managed fields.
Recovery can scan at most 20 export pages; accounts exceeding that bounded
window receive a recoverable not-found error rather than an unbounded request.
Book title/author are supplied at creation, while subsequent patching is limited
to fields supported by the Readwise highlight detail contract.

## Rollback

Leave `OOHSTORY_READWISE_EXPORT_ENABLED` unset or false. Existing local state
can be cleared through Disconnect without changing any Readwise highlight.
