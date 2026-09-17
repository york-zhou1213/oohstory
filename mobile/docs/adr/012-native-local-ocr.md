# ADR 012: Native, device-only OCR

Date: 2026-09-17

## Context

The previous portable OCR adapter proved cancellation and resource-boundary
contracts, but recognized only high-contrast English PNG fixtures and was
disabled in production. The product requirement is useful Simplified Chinese
and English OCR without uploading reading material.

## Decision

- Keep validation, encoded-size/dimension limits, cancellation, and ephemeral
  byte clearing in the shared Dart adapter.
- Use a private in-process Flutter method channel for native recognition:
  - Android: bundled Google ML Kit Latin and Chinese text-recognition models.
  - iOS/iPadOS and macOS: Apple Vision accurate text recognition with
    `zh-Hans` and `en-US` candidates.
  - Windows: `Windows.Media.Ocr`; Chinese/English require the corresponding OS
    language pack and report an actionable unavailable error when absent.
- Accept bounded PNG and JPEG images. Android/iOS expose both file/gallery
  selection and an explicit camera scan action.
- Keep Linux on the bounded English-only portable PNG engine and say so in the
  support matrix. Web and Fuchsia expose no OCR entry and never fall back to a
  remote service.
- Enable local OCR in the production capability profile on documented native
  platforms. `OOHSTORY_LOCAL_OCR_ENABLED=false` remains an emergency kill
  switch.

## Consequences

OCR images remain inside the application process and operating-system OCR
framework. Android artifacts grow because the Chinese and Latin models are
bundled, trading package size for offline availability. Windows behavior also
depends on installed language packs. UI cancellation settles immediately;
platform work already accepted by an OS framework may finish in the background,
but its late result is discarded and the shared ephemeral byte buffer is wiped.

