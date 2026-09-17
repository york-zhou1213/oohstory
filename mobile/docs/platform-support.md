# OOHStory platform support

OOHStory uses one Flutter product core across phone, tablet, desktop, and Web.
Platform claims are tracked at three separate levels so source scaffolding is
never presented as a shipped binary.

| Platform | Source target | Build verification | Signed distribution |
| --- | --- | --- | --- |
| Android | Yes | Local unsigned split APK + AAB; CI pipeline | Requires a release keystore |
| iOS / iPadOS | Yes | macOS CI, no codesign | Requires Apple signing |
| Web | Yes | Local and CI | Existing Safari Web remains production owner |
| Linux | Yes | Local Release bundle + `.deb` + `.rpm` + AppImage | Unsigned packages produced |
| Windows | Yes | Windows CI | Portable ZIP + unsigned installer produced |
| macOS | Yes | macOS CI | Unsigned ZIP/DMG pipeline; signing still required |

### Linux package boundary

- Current desktop artifacts target `x86_64`/`amd64` only and are built on the
  Ubuntu 24.04 GitHub runner baseline.
- The `.deb` is locally built and inspected on Ubuntu 24.04. The `.rpm` is
  metadata- and dependency-verified, with a clean Fedora 40+ install/upgrade/
  uninstall run still required before Fedora is listed as accepted.
- The AppImage contains the dependencies selected by `linuxdeploy`; it passed
  extraction and isolated Xvfb launch smoke tests on the build host. A clean
  supported-distribution launch and native file-dialog test is still required.
- No ARM Linux package, distribution signature, repository, or automatic
  update channel is claimed.

## Adaptive information architecture

- Phone: four primary destinations in a bottom navigation container.
- Tablet: compact navigation rail with the reading workspace beside it.
- Desktop: collapsible library sidebar plus a separate workspace command bar.
- Web: the same responsive breakpoints; existing `oohstory.com` routes and SEO
  remain owned by the Reader Web application until an explicit migration.

## Clean-room Koodo Reader reference

Koodo Reader is an AGPL-3.0 project. OOHStory may compare public behavior,
platform coverage, and layout concepts, but must not copy its source, CSS,
assets, or private data formats. OOHStory keeps its own Material 3 / Apple HIG
implementation and product identity.

## Capability gaps still tracked

- MOBI / AZW / AZW3 and CBR / CBT / CB7 now have a product-visible local
  reading entry, but formal compatibility remains limited to the fixture-backed
  compression/encoding matrix documented in the UI and tests.
- Local MDX v2 is integrated in the formal native reader with durable private
  copies, selected/manual lookup, stored/zlib/LZO blocks, optional MDD
  image/audio resources, entry links, sandboxed local styles, dictionary
  enable/order/remove controls, and checksum validation. Encrypted MDX remains
  explicitly unsupported. Web keeps the existing transient picker path instead
  of claiming durable native-style file persistence. Portable OCR remains a
  test/demo engine and is disabled in the production capability profile until a
  native platform engine has release evidence.
- WebDAV/S3 now have a gated “存储与同步” client path with verified-before-save
  configuration, secure credential separation, directory browsing, bounded local
  opening, create-only upload, ETag delete, WebDAV 207 mapping, S3 addressing
  modes and multipart upload. Native builds now persist pending upload/delete
  mutations in an AES-256-GCM encrypted, provider/account-partitioned queue whose
  master key stays in system secure storage. The UI exposes pending counts and
  explicit retry; Web deliberately falls back without persistent queuing because
  it has no equivalent trusted local key/file boundary. Both providers remain disabled by default until
  real sandbox/platform evidence exists. Dropbox/Google Drive are still adapter
  contracts only and have no OAuth product flow.
- Dedicated `/api/v1/sync/progress` has a gated client transport and persistent
  account-partitioned outbox. Production remains on the existing account-state
  path until the dedicated same-origin route is deployed and verified.
- Obsidian has a gated native one-way annotation export with deterministic
  Markdown, local hash receipts, external-edit detection, and backup-before-
  overwrite. Linux build and filesystem behavior are verified; Web is excluded
  and the other native platforms remain off pending their own permission tests.
- Notion has a separate gated native one-way export path for an explicitly
  selected page or data source. It stores the bearer token only in system secure
  storage, never searches the workspace, reuses the created page ID, and blocks
  replacement after remote edits until the user confirms. It remains disabled
  pending a real Notion connection/target acceptance run.
- Joplin has a separate gated Linux/Windows/macOS path for one explicitly
  selected desktop notebook. Setup validates the Data API and presents a
  bounded, hierarchy-aware notebook selector instead of requiring a copied ID.
  It can locate the official 41184..41194 port range using tokenless `/ping`,
  fixes the Data API host to `127.0.0.1`, stores
  the query token only in system secure storage, reuses deterministic note/tag
  IDs, and blocks replacement after remote edits or deletion. Linux build,
  fixtures, and an isolated Joplin CLI 3.7.1 live Data API run are verified; it
  remains disabled pending packaged Desktop UI/system-secure-storage acceptance.
  Web and mobile are excluded from Joplin export.
  Binary resources are adapter- and fixture-verified through a provenance-bound
  multipart path; native users can add/remove local attachments, and attachment
  persistence plus offline backup/restore are tested. Real Joplin note/tag/
  resource protocol acceptance passes.
- Readwise now has a separately gated native one-way export. It verifies and
  stores the access token through system secure storage, assigns stable opaque
  highlight URLs, recovers de-duplicated creation after ambiguous responses,
  and protects remote edits/deletions with explicit conflict confirmation. Its
  fixture-backed contract and UI gating are verified; it remains disabled by
  default pending a real Readwise account acceptance run. Disconnect never
  deletes remote highlights.
- Windows and Linux packaging exists. Linux now produces a tarball, `.deb`,
  `.rpm`, and linuxdeploy-collected AppImage from the same Release bundle, with
  AppStream metadata and SHA-256 checks. Code signing, notarization,
  clean-machine cross-distribution acceptance, auto-update, and store
  distribution remain external release work.
- Signed iOS / macOS artifacts and Apple release validation.
