#!/usr/bin/env bash
set -euo pipefail

readonly EX_CONFIG=78

call_lock=${CBM_CALL_LOCK:-/run/codebase-memory-mcp.call.lock}
mcp_binary=${CBM_MCP_BINARY:-/usr/local/bin/codebase-memory-mcp}

[[ $call_lock == /* && $mcp_binary == /* && -x $mcp_binary ]] || {
  printf 'CODEBASE_MCP_BLOCKED: invalid locked MCP entry configuration\n' >&2
  exit "$EX_CONFIG"
}

exec flock -x -- "$call_lock" "$mcp_binary" "$@"
