#!/bin/bash
set -euo pipefail

secret() {
  local path="/run/secrets/$1"
  [[ -f "$path" ]] || { echo "missing secret: $path" >&2; exit 1; }
  tr -d '\r\n' < "$path"
}

valid_password() {
  [[ "$1" =~ ^[A-Za-z0-9_-]{32,128}$ ]]
}

root_password="$(secret mysql_root_password)"
writer_password="$(secret mysql_writer_password)"
admin_password="$(secret mysql_admin_reader_password)"
public_password="$(secret mysql_public_reader_password)"
for value in "$root_password" "$writer_password" "$admin_password" "$public_password"; do
  valid_password "$value" || { echo "invalid generated database password" >&2; exit 1; }
done

export MYSQL_PWD="$root_password"
(
  cd /workspace/admin
  mysql --protocol=socket -uroot < deploy/mysql/init.sql
)

mysql --protocol=socket -uroot <<SQL
CREATE USER IF NOT EXISTS 'oohstory_library_writer'@'%' IDENTIFIED BY '${writer_password}';
ALTER USER 'oohstory_library_writer'@'%' IDENTIFIED BY '${writer_password}';
CREATE USER IF NOT EXISTS 'oohstory_library_reader'@'%' IDENTIFIED BY '${admin_password}';
ALTER USER 'oohstory_library_reader'@'%' IDENTIFIED BY '${admin_password}';
CREATE USER IF NOT EXISTS 'oohstory_public_reader'@'%' IDENTIFIED BY '${public_password}';
ALTER USER 'oohstory_public_reader'@'%' IDENTIFIED BY '${public_password}';
GRANT 'oohstory_library_writer_role'@'%' TO 'oohstory_library_writer'@'%';
GRANT 'oohstory_library_reader_role'@'%' TO 'oohstory_library_reader'@'%';
GRANT 'oohstory_public_reader_role'@'%' TO 'oohstory_public_reader'@'%';
SET DEFAULT ROLE 'oohstory_library_writer_role'@'%' TO 'oohstory_library_writer'@'%';
SET DEFAULT ROLE 'oohstory_library_reader_role'@'%' TO 'oohstory_library_reader'@'%';
SET DEFAULT ROLE 'oohstory_public_reader_role'@'%' TO 'oohstory_public_reader'@'%';
SQL

unset MYSQL_PWD root_password writer_password admin_password public_password
