#!/bin/bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

if [[ ! -f .env.compose ]]; then
  python3 scripts/prepare_compose_env.py
fi

set -a
# This file is generated locally with mode 0600 and only contains Compose values.
# shellcheck disable=SC1091
source .env.compose
set +a

docker compose --env-file .env.compose up --detach --build --wait
verify_password_file="$(mktemp -t oohstory-root-password.XXXXXX)"
trap 'rm -f -- "$verify_password_file"' EXIT
chmod 0600 "$verify_password_file"
printf '%s\n' "$OOHSTORY_MYSQL_ROOT_PASSWORD" > "$verify_password_file"
python3 scripts/verify_mysql_schema.py \
  --port "${OOHSTORY_MYSQL_PUBLISH_PORT:-13306}" \
  --user root \
  --password-file "$verify_password_file"

curl --fail --silent --show-error \
  "http://127.0.0.1:${OOHSTORY_READER_PUBLISH_PORT:-8091}/healthz" >/dev/null
curl --fail --silent --show-error \
  "http://127.0.0.1:${OOHSTORY_ADMIN_PUBLISH_PORT:-8092}/healthz" >/dev/null
echo "OOH Story is ready: reader http://127.0.0.1:${OOHSTORY_READER_PUBLISH_PORT:-8091}, admin http://127.0.0.1:${OOHSTORY_ADMIN_PUBLISH_PORT:-8092}/admin/"
