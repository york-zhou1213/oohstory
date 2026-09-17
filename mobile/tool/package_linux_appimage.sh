#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "usage: $0 <version> <output-directory> <linuxdeploy-path> <appimagetool-path> <runtime-path>" >&2
  exit 64
fi

version="$1"
output_directory="$2"
linuxdeploy="$3"
appimagetool="$4"
runtime_file="$5"
bundle_directory="build/linux/x64/release/bundle"

if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+\+[0-9]+$ ]]; then
  echo "invalid OOHStory version: $version" >&2
  exit 65
fi
if [[ ! -x "$bundle_directory/oohstory" ]]; then
  echo "Linux release bundle is missing; run flutter build linux --release" >&2
  exit 66
fi
if [[ ! -x "$linuxdeploy" ]]; then
  echo "verified linuxdeploy executable is missing: $linuxdeploy" >&2
  exit 67
fi
if [[ ! -x "$appimagetool" || ! -f "$runtime_file" ]]; then
  echo "verified appimagetool or AppImage runtime is missing" >&2
  exit 70
fi
if ! command -v convert >/dev/null 2>&1; then
  echo "ImageMagick convert is required" >&2
  exit 69
fi
if ! command -v appstreamcli >/dev/null 2>&1; then
  echo "appstreamcli is required" >&2
  exit 71
fi
if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "unsupported AppImage architecture: $(uname -m)" >&2
  exit 68
fi

appdir="$(mktemp -d)"
trap 'rm -rf -- "$appdir"' EXIT
mkdir -p \
  "$appdir/usr/bin" \
  "$appdir/usr/share/applications" \
  "$appdir/usr/share/icons/hicolor/512x512/apps" \
  "$appdir/usr/share/metainfo"
cp -a "$bundle_directory/." "$appdir/usr/bin/"
install -m 0644 packaging/linux/com.oohstory.oohstory.desktop \
  "$appdir/usr/share/applications/com.oohstory.oohstory.desktop"
install -m 0644 packaging/linux/com.oohstory.oohstory.appdata.xml \
  "$appdir/usr/share/metainfo/com.oohstory.oohstory.appdata.xml"
convert assets/oohstory-brand-icon.png -resize 512x512 \
  "$appdir/usr/share/icons/hicolor/512x512/apps/com.oohstory.oohstory.png"
chmod 0644 \
  "$appdir/usr/share/icons/hicolor/512x512/apps/com.oohstory.oohstory.png"
appstreamcli validate --no-net \
  "$appdir/usr/share/metainfo/com.oohstory.oohstory.appdata.xml"

mkdir -p "$output_directory"
output_directory="$(realpath "$output_directory")"
artifact="$output_directory/OOHStory-v${version}-Linux-x86_64.AppImage"
export APPIMAGE_EXTRACT_AND_RUN=1
export NO_STRIP=1
"$linuxdeploy" \
  --appdir "$appdir" \
  --desktop-file "$appdir/usr/share/applications/com.oohstory.oohstory.desktop" \
  --icon-file "$appdir/usr/share/icons/hicolor/512x512/apps/com.oohstory.oohstory.png"
"$appimagetool" --no-appstream --runtime-file "$runtime_file" \
  "$appdir" "$artifact"

test -s "$artifact"
chmod 0755 "$artifact"
(
  cd "$output_directory"
  sha256sum "$(basename "$artifact")" >"$(basename "$artifact").sha256"
)
