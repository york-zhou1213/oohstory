#!/usr/bin/env bash
set -euo pipefail

source_root=${OOHSTORY_DECONSTRUCTION_SOURCE:-/srv/oohstory/library/全局拆书库}
target_root=${OOHSTORY_DECONSTRUCTION_PUBLIC_ROOT:-/srv/oohstory-deconstructions}

if [[ -L "$target_root" ]]; then
  printf 'refusing public mirror symlink: %s -> %s\n' \
    "$target_root" "$(readlink "$target_root")" >&2
  exit 1
fi
install -d -m 0755 "$target_root"
source_real=$(realpath -e "$source_root")
target_real=$(realpath -e "$target_root")
if [[ "$source_real" == "$target_real" ]]; then
  printf 'refusing identical source and public mirror: %s\n' \
    "$source_real" >&2
  exit 1
fi
existing_link=$(find "$target_root" -type l -print -quit)
if [[ -n "$existing_link" ]]; then
  printf 'refusing public mirror containing symlink: %s\n' \
    "$existing_link" >&2
  exit 1
fi
rsync -rt --prune-empty-dirs \
  --include='*/' \
  --include='_progress.md' \
  --include='快速预览.md' \
  --include='拆文报告.md' \
  --include='概要.md' \
  --include='文风.md' \
  --exclude='*' \
  "$source_root/" "$target_root/"
find "$target_root" -type d -exec chmod 0755 {} +
find "$target_root" -type f -exec chmod 0644 {} +
