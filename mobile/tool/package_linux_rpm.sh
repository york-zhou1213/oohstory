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
if ! command -v rpmbuild >/dev/null 2>&1 || ! command -v rpm >/dev/null 2>&1; then
  echo "rpmbuild and rpm are required" >&2
  exit 67
fi
if ! command -v convert >/dev/null 2>&1; then
  echo "ImageMagick convert is required" >&2
  exit 69
fi
if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "unsupported RPM architecture: $(uname -m)" >&2
  exit 68
fi

package_version="${version%%+*}"
package_release="${version##*+}"
bundle_directory="$(realpath "$bundle_directory")"
desktop_file="$(realpath packaging/linux/com.oohstory.oohstory.desktop)"
metainfo_file="$(realpath packaging/linux/com.oohstory.oohstory.appdata.xml)"
icon_file="$(realpath assets/oohstory-brand-icon.png)"
rpm_root="$(mktemp -d)"
trap 'rm -rf -- "$rpm_root"' EXIT
mkdir -p "$rpm_root"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
package_icon="$rpm_root/oohstory-512.png"
convert "$icon_file" -resize 512x512 "$package_icon"

spec_file="$rpm_root/SPECS/oohstory.spec"
cat >"$spec_file" <<EOF
Name: oohstory
Version: $package_version
Release: $package_release
Summary: OOHStory Chinese novel reader and analysis client
License: Proprietary
URL: https://oohstory.com
BuildArch: x86_64
Requires: gtk3, libsecret

%description
A cross-platform reader for the OOHStory library and local DRM-free books.

%install
mkdir -p %{buildroot}/opt/oohstory
mkdir -p %{buildroot}/usr/bin
mkdir -p %{buildroot}/usr/share/applications
mkdir -p %{buildroot}/usr/share/icons/hicolor/512x512/apps
mkdir -p %{buildroot}/usr/share/metainfo
cp -a "$bundle_directory/." %{buildroot}/opt/oohstory/
ln -s ../../opt/oohstory/oohstory %{buildroot}/usr/bin/oohstory
install -m 0644 "$desktop_file" %{buildroot}/usr/share/applications/com.oohstory.oohstory.desktop
install -m 0644 "$metainfo_file" %{buildroot}/usr/share/metainfo/com.oohstory.oohstory.appdata.xml
install -m 0644 "$package_icon" %{buildroot}/usr/share/icons/hicolor/512x512/apps/com.oohstory.oohstory.png

%files
%defattr(-,root,root,-)
/opt/oohstory
/usr/bin/oohstory
/usr/share/applications/com.oohstory.oohstory.desktop
/usr/share/icons/hicolor/512x512/apps/com.oohstory.oohstory.png
/usr/share/metainfo/com.oohstory.oohstory.appdata.xml

%changelog
* Thu Sep 17 2026 OOHStory <support@oohstory.com> - $package_version-$package_release
- Automated unsigned package from the verified Flutter Release bundle.
EOF

rpmbuild --define "_topdir $rpm_root" -bb "$spec_file"
source_rpm="$(find "$rpm_root/RPMS" -type f -name '*.rpm' -print -quit)"
if [[ -z "$source_rpm" || ! -s "$source_rpm" ]]; then
  echo "rpmbuild did not produce an RPM" >&2
  exit 70
fi
rpm -qp --queryformat '%{NAME} %{VERSION}-%{RELEASE} %{ARCH}\n' "$source_rpm" \
  | grep -Fx "oohstory $package_version-$package_release x86_64"

mkdir -p "$output_directory"
artifact="$output_directory/OOHStory-v${version}-Linux-x86_64.rpm"
cp "$source_rpm" "$artifact"
(
  cd "$output_directory"
  sha256sum "$(basename "$artifact")" >"$(basename "$artifact").sha256"
)
