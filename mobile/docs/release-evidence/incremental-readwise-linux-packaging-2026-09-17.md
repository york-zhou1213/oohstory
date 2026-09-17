# OOHStory Readwise and Linux packaging evidence

- Date: 2026-09-17
- Base snapshot: `build/current-v1.27.0-20260917` at `3bb144e`
- Status: local implementation and package verification complete; real
  Readwise account and clean-distribution acceptance remain gated

## Readwise export

- Native-only product entry behind `OOHSTORY_READWISE_EXPORT_ENABLED`; the
  production default remains off.
- Token validation uses the official auth endpoint and the token is stored only
  through the system secure-credential boundary.
- Stable opaque source/highlight URLs provide server-side de-duplication without
  disclosing local document or annotation IDs.
- Local receipts track acknowledged remote IDs plus local and remote hashes.
  Repeat export is unchanged when both sides match, patches local edits, blocks
  remote edits/deletions, and only overwrites after explicit confirmation.
- Ambiguous create recovery is bounded to 20 paginated export pages. Disconnect
  clears local token/state and never deletes Readwise content.
- Eight focused tests cover credential separation, auth, initial creation,
  unchanged retry, local update, remote conflict/overwrite, ambiguous-create
  recovery, and local disconnect cleanup. A ninth regression covers explicit
  recreation after Readwise reports a soft-deleted highlight. Capability and
  widget gating tests also pass.

## Linux packages

- One Flutter Linux Release bundle feeds Debian, RPM, and AppImage package
  scripts.
- linuxdeploy, appimagetool, and the Type 2 runtime are fetched from fixed URLs
  and must pass pinned SHA-256 checks before execution.
- AppStream metadata passes `appstreamcli validate --no-net`.
- The canonical 1024x1024 brand asset is deterministically resized to 512x512
  for the matching hicolor directory; the initial size mismatch was caught by
  the real AppImage build and corrected for all Linux packages.
- Local package metadata and structure checks passed:
  - Debian: package `oohstory`, version `1.27.0+75`, architecture `amd64`.
  - RPM: package `oohstory`, version/release `1.27.0-75`, architecture `x86_64`.
  - AppImage: pinned runtime Build-ID retained; extraction, AppRun, AppStream,
    icon dimensions, and ELF dependency resolution passed.
- The AppImage launched under an isolated XDG/DBus/Xvfb smoke environment and
  stayed healthy for the full 10-second observation window.
- Local unsigned artifacts:
  - `OOHStory-v1.27.0+75-Linux-amd64.deb`
  - `OOHStory-v1.27.0+75-Linux-x86_64.rpm`
  - `OOHStory-v1.27.0+75-Linux-x86_64.AppImage`

## Repository verification

- `flutter analyze`: no issues.
- Full Flutter suite: 287 passed, 1 conditional real-Joplin test skipped.
- Repository secret/artifact scan: passed.
- Both workflow YAML files parse, and the repository-root
  `current-client-builds` workflow owns the remotely executed Linux package job.
- Final local Release builds passed for Linux and Android. Android produced
  three split-ABI APKs plus one AAB, and every file was explicitly verified as
  unsigned before packaging.

## Remaining acceptance boundary

- Readwise needs a real user token and explicit target-account acceptance run;
  no credential is available in this workspace and none is requested in source
  or logs.
- RPM needs a clean Fedora 40+ VM install/upgrade/uninstall run; the current
  Ubuntu 24.04 build baseline does not justify a RHEL claim. AppImage needs a
  clean supported-distribution launch and file-dialog test.
- All Linux outputs are unsigned; no production update channel or store upload
  is claimed.
