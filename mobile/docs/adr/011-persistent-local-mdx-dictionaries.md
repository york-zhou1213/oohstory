# ADR 011: Persistent local MDX dictionaries and sandboxed reader lookup

- Status: Accepted
- Date: 2026-09-17

## Context

The existing MDX adapter parses small MDX v2 fixtures and the local-content
demo can query one in-memory dictionary. That dictionary disappears when the
screen closes, is absent from the formal offline reader, rejects LZO blocks,
and has no MDD resource or entry-link contract. Product intake requires a
durable, offline-only integration with enable/disable, ordering, removal,
resources, links, and local styling.

MDX, MDD, definitions, CSS, links, images, and audio are untrusted local input.
They must not gain network, script, filesystem-path, or navigation authority.

## Decision

1. Store imported dictionaries under the application documents directory with
   application-generated IDs. Persist only bounded metadata in preferences;
   never retain picker paths. An optional same-name MDD companion is copied
   beside its MDX file.
2. Represent each dictionary with stable ID, display name, MDX/MDD sizes and
   hashes, entry count, enabled flag, explicit order, and import timestamp.
   Import validates before committing metadata. Remove deletes only the
   application-owned copies. Reordering and enablement are deterministic.
3. Keep the parser platform-neutral. Support MDX v2 stored, zlib, and LZO1X
   blocks with checksums and existing input/entry/expansion bounds. Parse MDD
   with the same container rules but preserve record bytes instead of decoding
   them as text.
4. Resolve exact lookups across enabled dictionaries in user order. Follow
   `@@@LINK=` redirects only within the same dictionary, with a depth limit and
   cycle detection. Query text remains in process and is never sent to an API.
5. Expose lookup from the formal text reader through selected text and a manual
   query dialog. The result sheet identifies the source dictionary and supports
   entry links without leaving the reader.
6. Sanitize definition markup before rendering: drop scripts, executable and
   embedded-document tags, event attributes, remote URLs, forms, and unsafe
   CSS. Allow a bounded presentation-only inline style subset. Resolve only
   exact MDD resource paths to in-memory image/audio bytes with MIME allowlists
   and size limits; never materialize definition-controlled paths.
7. Keep the existing transient Web picker path available. Durable native
   dictionary storage is the formal path; Web does not claim persistence where
   browser file grants and the current `dart:io` bookshelf store cannot provide
   it honestly.

## Data contract

```text
LocalDictionaryInfo
  id, name, mdx_size, mdx_sha256
  optional mdd_size, mdd_sha256
  entry_count, enabled, order, added_at

LocalDictionaryLookup
  query
  -> ordered LocalDictionaryResult[]
       dictionary_id, dictionary_name, term, sanitized_definition
       local resource references only
```

## Dependency graph

```text
Dictionary settings/import UI
  -> LocalDictionaryStore
       -> application-owned MDX/MDD files
       -> preference metadata
       -> MdxDictionaryAdapter + MddResourceAdapter
  -> LocalReaderScreen selection/manual lookup
       -> ordered enabled dictionaries
       -> redirect resolver
       -> sandboxed definition/resource view
```

## Alternatives considered

- Keeping the profile demo as the only entry was rejected because it loses the
  dictionary and bypasses the formal bookshelf reader.
- Retaining external picker paths was rejected because grants and paths are not
  durable across platforms or restarts.
- Rendering raw MDX HTML in a WebView was rejected because it gives untrusted
  dictionaries an unnecessarily large script, navigation, and network surface.
- Using a native-only LZO plugin was rejected because it would regress the
  current platform-neutral parser and Web lookup path.

## Consequences

- Native users can manage multiple durable local dictionaries and query them
  inside the real reading flow without network access.
- Malicious definitions and resources stay inside a bounded presentation
  sandbox.
- Large-file streaming/indexing beyond the bounded parser remains a future
  optimization; supported imports fail closed when their configured limits are
  exceeded instead of risking unbounded memory.

## Rollback

Hide the dictionary management and reader lookup controls, while leaving copied
dictionary files and metadata intact for a later compatible build. Reverting
the parser does not delete user dictionaries. Removal remains an explicit user
action.
