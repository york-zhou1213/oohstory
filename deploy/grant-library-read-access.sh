#!/usr/bin/env bash
set -euo pipefail

library_root=${OOHSTORY_LIBRARY_ROOT:-/srv/oohstory/library}
reader_user=${OOHSTORY_READER_USER:-oohstory}

id "$reader_user" >/dev/null
setfacl -m "u:${reader_user}:r" "$library_root/catalog.sqlite3"

for public_root in \
  "$library_root/书籍" \
  "$library_root/封面" \
  "$library_root/全局索引"
do
  setfacl -R -m "u:${reader_user}:rX" "$public_root"
  find "$public_root" -type d -exec \
    setfacl -m "d:u:${reader_user}:rX" {} +
done
