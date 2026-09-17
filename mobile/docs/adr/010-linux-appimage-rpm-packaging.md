# ADR 010: Reproducible unsigned Linux AppImage and RPM packages

- Status: Accepted
- Date: 2026-09-17

## Context

The Linux release currently produces a Flutter bundle, tarball, and Debian
package. The product acceptance matrix also calls for AppImage and RPM outputs.
These formats must be generated from the same verified Release bundle, include
desktop metadata and icons, and remain clearly unsigned.

## Decision

- The Flutter `build/linux/x64/release/bundle` directory remains the single
  package payload source.
- A dedicated RPM script stages `/opt/oohstory`, `/usr/bin/oohstory`, the
  desktop file, and the hicolor icon, then builds with `rpmbuild`. Flutter's
  `+build` suffix maps to the RPM `Release` field.
- A dedicated AppImage script stages the same bundle in an AppDir and delegates
  dependency collection and Type 2 image construction to a pinned
  `linuxdeploy` binary.
- CI downloads the exact linuxdeploy release asset plus appimagetool and the
  Type 2 runtime. All three must match pinned SHA-256 values before execution,
  and the tools run without a FUSE requirement.
- Every new artifact receives SHA-256 coverage and is included in the Linux
  SBOM/artifact job. Package scripts fail closed on malformed versions, missing
  bundles, missing tools, or unexpected architecture.
- These outputs are build-verified but unsigned. They do not change update
  channels or production distribution ownership.

## Dependency graph

`flutter build linux --release` → shared Release bundle → tar/deb/rpm/AppImage
→ metadata checks → SHA-256/SBOM → uploaded CI artifact.

## Alternatives considered

- Unverified dynamic “latest” tooling was rejected. Where upstream only exposes
  a continuous asset URL, CI accepts it only while its pinned checksum matches.
- A hand-built AppImage without dependency collection was rejected because it
  would falsely imply portability while relying on host GTK libraries.
- Flatpak/Snap were deferred because they require separate sandbox manifests,
  store identities, and permission acceptance beyond this packaging slice.

## Consequences

The Linux matrix covers common Debian, RPM, and portable-image distribution
paths. Clean-machine GUI acceptance is still required before claiming broad
distribution compatibility. The repository-root client workflow is the
authoritative remote builder; the mobile-local workflow retains the same
packaging contract for standalone client checkouts.

## Rollback

Remove the two package invocations from the Linux CI job. Existing tar/deb
generation is independent and remains unchanged.
