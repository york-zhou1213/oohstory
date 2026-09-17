#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <version> <output-directory>" >&2
  exit 64
fi

version="$1"
output_directory="$2"
bundle_directory="build/linux/x64/release/bundle"

if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+\+[0-9]+$ ]]; then
  echo "invalid OOHStory version: $version" >&2
  exit 65
fi
if [[ ! -x "$bundle_directory/oohstory" ]]; then
  echo "Linux release bundle is missing; run flutter build linux --release" >&2
  exit 66
fi
if ! command -v convert >/dev/null 2>&1; then
  echo "ImageMagick convert is required" >&2
  exit 67
fi

package_root="$(mktemp -d)"
trap 'rm -rf -- "$package_root"' EXIT
chmod 0755 "$package_root"
architecture="$(dpkg --print-architecture)"
mkdir -p \
  "$package_root/DEBIAN" \
  "$package_root/opt/oohstory" \
  "$package_root/usr/bin" \
  "$package_root/usr/share/applications" \
  "$package_root/usr/share/icons/hicolor/512x512/apps" \
  "$package_root/usr/share/metainfo"
cp -a "$bundle_directory/." "$package_root/opt/oohstory/"
ln -s ../../opt/oohstory/oohstory "$package_root/usr/bin/oohstory"
install -m 0644 packaging/linux/com.oohstory.oohstory.desktop \
  "$package_root/usr/share/applications/com.oohstory.oohstory.desktop"
install -m 0644 packaging/linux/com.oohstory.oohstory.appdata.xml \
  "$package_root/usr/share/metainfo/com.oohstory.oohstory.appdata.xml"
convert assets/oohstory-brand-icon.png -resize 512x512 \
  "$package_root/usr/share/icons/hicolor/512x512/apps/com.oohstory.oohstory.png"
chmod 0644 \
  "$package_root/usr/share/icons/hicolor/512x512/apps/com.oohstory.oohstory.png"

installed_size="$(du -sk "$package_root/opt/oohstory" | cut -f1)"
cat >"$package_root/DEBIAN/control" <<EOF
Package: oohstory
Version: $version
Section: office
Priority: optional
Architecture: $architecture
Installed-Size: $installed_size
Maintainer: OOHStory <support@oohstory.com>
Depends: libgtk-3-0, libsecret-1-0
Homepage: https://oohstory.com
Description: OOHStory Chinese novel reader and analysis client
 A cross-platform reader for the OOHStory library and local DRM-free books.
EOF

mkdir -p "$output_directory"
artifact="$output_directory/OOHStory-v${version}-Linux-${architecture}.deb"
dpkg-deb --root-owner-group --build "$package_root" "$artifact"
(
  cd "$output_directory"
  sha256sum "$(basename "$artifact")" >"$(basename "$artifact").sha256"
)
