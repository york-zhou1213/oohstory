#!/usr/bin/env bash
set -euo pipefail

readonly EX_USAGE=64
readonly EX_DATAERR=65
readonly EX_NOINPUT=66
readonly EX_CONFIG=78

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
manifest_source="$script_dir/active_task_worktrees.sh"

tasks_dir=${CBM_ACTIVE_TASKS_DIR:-/root/.openclaw/workspaces/engineering-team/tasks/active}
allowed_root=${CBM_ALLOWED_ROOT:-/root/.codespace/workspace}
managed_root=${CBM_MANAGED_ROOT:-/root/.codespace/workspace/.cbm-task-worktrees}
cache_dir=${CBM_CACHE_DIR:-/var/lib/codebase-memory-mcp/cache}
mem_budget_mb=${CBM_MEM_BUDGET_MB:-512}
workers=${CBM_WORKERS:-1}
state_dir=${CBM_STATE_DIR:-/var/lib/codebase-memory-workspace-sync}
sync_lock=${CBM_SYNC_LOCK:-/run/codebase-memory-workspace-sync.lock}
call_lock=${CBM_CALL_LOCK:-/run/codebase-memory-mcp.call.lock}
mcporter_config=${CBM_MCPORTER_CONFIG:-/root/.mcporter/mcporter.json}
client_configs=${CBM_CLIENT_CONFIGS:-/root/.openclaw/agents/john/agent/codex-home/config.toml:/root/.openclaw/agents/jucy/agent/codex-home/config.toml:/root/.openclaw/agents/mus/agent/codex-home/config.toml}
mcp_server=${CBM_MCP_SERVER:-codebase-memory-mcp}
mcp_wrapper=${CBM_MCP_WRAPPER:-/root/.codespace/workspace/codebase-memory/bin/mcp_call_locked.sh}
mcp_binary=${CBM_MCP_BINARY:-/usr/local/bin/codebase-memory-mcp}

expected_allowed_root=${CBM_EXPECTED_ALLOWED_ROOT:-/root/.codespace/workspace}
expected_cache_dir=${CBM_EXPECTED_CACHE_DIR:-/var/lib/codebase-memory-mcp/cache}
expected_mem_budget_mb=${CBM_EXPECTED_MEM_BUDGET_MB:-512}
expected_workers=${CBM_EXPECTED_WORKERS:-1}

mode=sync
rollback_manifest=

usage() {
  printf 'Usage: %s [--discover | --rollback MANIFEST]\n' "$0"
}

while (($#)); do
  case "$1" in
    --discover)
      [[ $mode == sync ]] || { usage >&2; exit "$EX_USAGE"; }
      mode=discover
      ;;
    --rollback)
      [[ $mode == sync ]] || { usage >&2; exit "$EX_USAGE"; }
      shift
      rollback_manifest=${1:-}
      [[ -n $rollback_manifest ]] || { usage >&2; exit "$EX_USAGE"; }
      mode=rollback
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit "$EX_USAGE"
      ;;
  esac
  shift
done

for command_name in cmp flock git jq mcporter realpath sha256sum stat; do
  command -v "$command_name" >/dev/null || {
    printf 'CODEBASE_MCP_BLOCKED: missing required command: %s\n' "$command_name" >&2
    exit "$EX_CONFIG"
  }
done
[[ -x $manifest_source ]] || {
  printf 'CODEBASE_MCP_BLOCKED: manifest source is not executable: %s\n' "$manifest_source" >&2
  exit "$EX_NOINPUT"
}

fail() {
  printf 'CODEBASE_MCP_BLOCKED: %s\n' "$*" >&2
  exit "$EX_DATAERR"
}

reject_symlink_components() {
  local candidate=$1 current=/ component
  [[ $candidate == /* ]] || fail "path is not absolute: $candidate"
  IFS='/' read -r -a components <<<"${candidate#/}"
  for component in "${components[@]}"; do
    [[ -n $component ]] || continue
    current=${current%/}/$component
    [[ ! -L $current ]] || fail "symlink path is forbidden: $current"
  done
}

path_is_within() {
  local child=${1%/}/ parent=${2%/}/
  [[ $child == "$parent"* ]]
}

toml_env_value() {
  local file=$1 key=$2
  awk -v wanted="$key" '
    $0 == "[mcp_servers.codebase-memory-mcp.env]" { in_section=1; next }
    in_section && /^\[/ { exit }
    in_section && $0 ~ "^[[:space:]]*" wanted "[[:space:]]*=" {
      value=$0
      sub(/^[^=]*=[[:space:]]*/, "", value)
      sub(/[[:space:]]*#.*/, "", value)
      sub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if ((substr(value,1,1) == "\"" && substr(value,length(value),1) == "\"") ||
          (substr(value,1,1) == "\047" && substr(value,length(value),1) == "\047")) {
        value=substr(value,2,length(value)-2)
      }
      print value
      count++
    }
    END { if (count != 1) exit 1 }
  ' "$file"
}

toml_server_value() {
  local file=$1 key=$2
  awk -v wanted="$key" '
    $0 == "[mcp_servers.codebase-memory-mcp]" { in_section=1; next }
    in_section && /^\[/ { exit }
    in_section && $0 ~ "^[[:space:]]*" wanted "[[:space:]]*=" {
      value=$0
      sub(/^[^=]*=[[:space:]]*/, "", value)
      sub(/[[:space:]]*#.*/, "", value)
      sub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if ((substr(value,1,1) == "\"" && substr(value,length(value),1) == "\"") ||
          (substr(value,1,1) == "\047" && substr(value,length(value),1) == "\047")) {
        value=substr(value,2,length(value)-2)
      }
      print value
      count++
    }
    END { if (count != 1) exit 1 }
  ' "$file"
}

validate_profile() {
  local config value
  [[ $allowed_root == "$expected_allowed_root" ]] || fail "allowed-root profile mismatch"
  [[ $cache_dir == "$expected_cache_dir" ]] || fail "cache profile mismatch"
  [[ $mem_budget_mb == "$expected_mem_budget_mb" ]] || fail "memory-budget profile mismatch"
  [[ $workers == "$expected_workers" ]] || fail "worker-count profile mismatch"
  [[ $workers =~ ^[1-9][0-9]*$ && $workers -le 1 ]] || fail "worker profile is not single-flight"
  [[ $mem_budget_mb =~ ^[1-9][0-9]*$ && $mem_budget_mb -le 512 ]] || fail "memory profile is not bounded"
  [[ $mcp_wrapper == /* && -x $mcp_wrapper ]] || fail "locked MCP entry is missing or not executable"
  [[ $mcp_binary == /* && -x $mcp_binary ]] || fail "MCP binary is missing or not executable"

  reject_symlink_components "$mcporter_config"
  [[ -f $mcporter_config ]] || fail "mcporter config is missing"
  jq -e \
    --arg server "$mcp_server" \
    --arg allowed "$allowed_root" \
    --arg cache "$cache_dir" \
    --arg memory "$mem_budget_mb" \
    --arg workers "$workers" \
    --arg wrapper "$mcp_wrapper" \
    --arg call_lock "$call_lock" \
    --arg binary "$mcp_binary" \
    '.mcpServers[$server].command == $wrapper and
     .mcpServers[$server].env.CBM_ALLOWED_ROOT == $allowed and
     .mcpServers[$server].env.CBM_CACHE_DIR == $cache and
     .mcpServers[$server].env.CBM_MEM_BUDGET_MB == $memory and
     .mcpServers[$server].env.CBM_WORKERS == $workers and
     .mcpServers[$server].env.CBM_CALL_LOCK == $call_lock and
     .mcpServers[$server].env.CBM_MCP_BINARY == $binary' \
    "$mcporter_config" >/dev/null || fail "mcporter cache/root/worker profile mismatch"

  IFS=':' read -r -a configs <<<"$client_configs"
  ((${#configs[@]} == 3)) || fail "exactly John/Jucy/Mus client configs are required"
  for config in "${configs[@]}"; do
    reject_symlink_components "$config"
    [[ -f $config ]] || fail "task client config is missing: $config"
    value=$(toml_server_value "$config" command) || fail "client MCP command is ambiguous: $config"
    [[ $value == "$mcp_wrapper" ]] || fail "client bypasses locked MCP entry: $config"
    value=$(toml_env_value "$config" CBM_ALLOWED_ROOT) || fail "client allowed-root is ambiguous: $config"
    [[ $value == "$allowed_root" ]] || fail "client allowed-root profile mismatch: $config"
    value=$(toml_env_value "$config" CBM_CACHE_DIR) || fail "client cache is ambiguous: $config"
    [[ $value == "$cache_dir" ]] || fail "client cache profile mismatch: $config"
    value=$(toml_env_value "$config" CBM_MEM_BUDGET_MB) || fail "client memory budget is ambiguous: $config"
    [[ $value == "$mem_budget_mb" ]] || fail "client memory profile mismatch: $config"
    value=$(toml_env_value "$config" CBM_WORKERS) || fail "client worker count is ambiguous: $config"
    [[ $value == "$workers" ]] || fail "client worker profile mismatch: $config"
    value=$(toml_env_value "$config" CBM_CALL_LOCK) || fail "client call lock is ambiguous: $config"
    [[ $value == "$call_lock" ]] || fail "client call-lock profile mismatch: $config"
    value=$(toml_env_value "$config" CBM_MCP_BINARY) || fail "client MCP binary is ambiguous: $config"
    [[ $value == "$mcp_binary" ]] || fail "client MCP binary profile mismatch: $config"
  done
}

prepare_directory() {
  local path=$1 mode_bits=$2
  [[ $path == /* ]] || fail "directory is not absolute: $path"
  reject_symlink_components "$path"
  if [[ ! -e $path ]]; then
    install -d -m "$mode_bits" -- "$path"
  fi
  [[ -d $path && ! -L $path ]] || fail "unsafe directory: $path"
}

if [[ $mode == discover ]]; then
  exec "$manifest_source" --tasks-dir "$tasks_dir" --managed-root "$managed_root" --allowed-root "$allowed_root"
fi

prepare_directory "$state_dir" 0700
prepare_directory "$managed_root" 0755
reject_symlink_components "$allowed_root"
allowed_root=$(realpath -e -- "$allowed_root")
managed_root=$(realpath -e -- "$managed_root")
path_is_within "$managed_root" "$allowed_root" || fail "managed root is outside allowed root"

reject_symlink_components "$sync_lock"
reject_symlink_components "$call_lock"
exec {sync_fd}>"$sync_lock"
flock -n "$sync_fd" || {
  printf 'workspace sync skipped: another sync owns the lock\n'
  exit 0
}

mcporter_call() {
  local tool=$1 args=$2 output=$3
  mcporter --config "$mcporter_config" call "$mcp_server.$tool" --args "$args" --output json >"$output"
}

common_git_dir() {
  local repo=$1 common
  common=$(git -C "$repo" rev-parse --git-common-dir)
  if [[ $common != /* ]]; then
    common="$repo/$common"
  fi
  realpath -e -- "$common"
}

verify_worktree() {
  local task_id=$1 repo=$2 path=$3 head=$4
  reject_symlink_components "$path"
  [[ -d $path ]] || fail "$task_id managed worktree is missing"
  path_is_within "$(realpath -e -- "$path")" "$managed_root" || fail "$task_id managed worktree escaped root"
  [[ $(git -C "$path" rev-parse HEAD 2>/dev/null) == "$head" ]] || fail "$task_id managed worktree has exact-Head drift"
  [[ $(common_git_dir "$path") == "$(common_git_dir "$repo")" ]] || fail "$task_id managed worktree belongs to another repository"
}

verify_clean_worktree() {
  local task_id=$1 path=$2 head=$3 stage=$4 status
  [[ $(git -C "$path" rev-parse HEAD 2>/dev/null) == "$head" ]] ||
    fail "$task_id managed worktree has exact-Head drift $stage"
  status=$(git -C "$path" status --porcelain=v1 --untracked-files=all --ignore-submodules=none)
  [[ -z $status ]] || fail "$task_id managed worktree is dirty $stage"
}

rollback_run_manifest() {
  local manifest=$1 project path repo head args result rollback_projects
  reject_symlink_components "$manifest"
  [[ -f $manifest && ! -L $manifest ]] || fail "rollback manifest is not a regular file"
  path_is_within "$(realpath -e -- "$manifest")" "$state_dir" || fail "rollback manifest is outside state directory"
  jq -e '.schema == 1 and (.created_projects | type == "array") and (.created_worktrees | type == "array")' "$manifest" >/dev/null ||
    fail "rollback manifest schema is invalid"

  while IFS=$'\t' read -r path repo head; do
    [[ -n $path && -n $repo && $head =~ ^[0-9a-f]{40}$ ]] || fail "invalid worktree rollback record"
    [[ -e $path ]] || continue
    verify_worktree rollback "$repo" "$path" "$head"
    [[ -z $(git -C "$path" status --porcelain) ]] || fail "rollback refuses a dirty managed worktree: $path"
  done < <(jq -r '.created_worktrees[] | [.path,.repository,.head] | @tsv' "$manifest" | LC_ALL=C sort -r)

  rollback_projects=$(mktemp "$state_dir/rollback-projects.XXXXXX.json")
  mcporter_call list_projects '{}' "$rollback_projects"
  while IFS= read -r project; do
    [[ $project =~ ^[A-Za-z0-9._-]+$ ]] || fail "unsafe project name in rollback manifest"
    jq -e --arg project "$project" '.projects[]? | select(.name == $project)' "$rollback_projects" >/dev/null || continue
    result=$(mktemp "$state_dir/delete-project.XXXXXX.json")
    args=$(jq -cn --arg project "$project" '{project:$project}')
    mcporter_call delete_project "$args" "$result"
    rm -f "$result"
  done < <(jq -r '.created_projects[]' "$manifest" | LC_ALL=C sort -r)
  rm -f "$rollback_projects"

  while IFS=$'\t' read -r path repo head; do
    [[ -e $path ]] || continue
    git -C "$repo" worktree remove -- "$path"
  done < <(jq -r '.created_worktrees[] | [.path,.repository,.head] | @tsv' "$manifest" | LC_ALL=C sort -r)

  printf 'rollback complete: manifest=%s\n' "$manifest"
}

if [[ $mode == rollback ]]; then
  validate_profile
  rollback_run_manifest "$rollback_manifest"
  exit 0
fi

manifest_file=$(mktemp "$state_dir/task-manifest.XXXXXX.json")
manifest_verify=$(mktemp "$state_dir/task-manifest-verify.XXXXXX.json")
projects_before=$(mktemp "$state_dir/projects-before.XXXXXX.json")
projects_after=$(mktemp "$state_dir/projects-after.XXXXXX.json")
run_manifest_tmp=$(mktemp "$state_dir/run-manifest.XXXXXX.json")
cleanup_files=()
run_started=0
on_exit() {
  local status=$? failed_manifest
  if ((status != 0 && run_started == 1)) && [[ -s $run_manifest_tmp ]]; then
    failed_manifest="$state_dir/failed-run-$(date -u +%Y%m%dT%H%M%SZ)-$$.json"
    jq --arg failed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --argjson exit_status "$status" \
      '. + {failed_at:$failed_at,exit_status:$exit_status}' "$run_manifest_tmp" >"$failed_manifest"
    chmod 0600 "$failed_manifest"
    printf 'rollback manifest: %s\n' "$failed_manifest" >&2
  fi
  rm -f "$manifest_file" "$manifest_verify" "$projects_before" "$projects_after" "$run_manifest_tmp" "${cleanup_files[@]:-}"
  exit "$status"
}
trap on_exit EXIT

"$manifest_source" --tasks-dir "$tasks_dir" --managed-root "$managed_root" --allowed-root "$allowed_root" >"$manifest_file"
validate_profile
busy_tasks=$(jq -r '[.tasks[] | select(.busy) | .task_id] | join(",")' "$manifest_file")
if [[ -n $busy_tasks ]]; then
  printf 'workspace sync skipped: active task caller(s)=%s\n' "$busy_tasks"
  exit 0
fi

unresolved_tasks=$(jq -r '[.tasks[] | select(.binding_ready | not) | .task_id] | join(",")' "$manifest_file")
[[ -z $unresolved_tasks ]] || fail "active task bindings are unresolved: $unresolved_tasks"

verify_manifest_unchanged() {
  "$manifest_source" --tasks-dir "$tasks_dir" --managed-root "$managed_root" --allowed-root "$allowed_root" >"$manifest_verify"
  cmp -s "$manifest_file" "$manifest_verify" || fail "active task manifest changed during maintenance"
}

verify_manifest_unchanged

task_count=$(jq '.tasks | length' "$manifest_file")
for ((preflight_index=0; preflight_index<task_count; preflight_index++)); do
  preflight_task=$(jq -c ".tasks[$preflight_index]" "$manifest_file")
  preflight_task_id=$(jq -r '.task_id' <<<"$preflight_task")
  preflight_repo=$(jq -r '.repository' <<<"$preflight_task")
  preflight_head=$(jq -r '.head' <<<"$preflight_task")
  preflight_worktree=$(jq -r '.managed_worktree' <<<"$preflight_task")
  if [[ -e $preflight_worktree ]]; then
    verify_worktree "$preflight_task_id" "$preflight_repo" "$preflight_worktree" "$preflight_head"
    verify_clean_worktree "$preflight_task_id" "$preflight_worktree" "$preflight_head" \
      "before maintenance MCP calls"
  fi
done

mcporter_call list_projects '{}' "$projects_before"
jq -cn --arg started_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{schema:1,started_at:$started_at,created_projects:[],created_worktrees:[],receipts:[]}' >"$run_manifest_tmp"
run_started=1

for ((task_index=0; task_index<task_count; task_index++)); do
  verify_manifest_unchanged
  task=$(jq -c ".tasks[$task_index]" "$manifest_file")
  task_id=$(jq -r '.task_id' <<<"$task")
  repo=$(jq -r '.repository' <<<"$task")
  base=$(jq -r '.base' <<<"$task")
  head=$(jq -r '.head' <<<"$task")
  declared_project=$(jq -r '.project' <<<"$task")
  managed_worktree=$(jq -r '.managed_worktree' <<<"$task")
  task_slug=$(tr '[:upper:]' '[:lower:]' <<<"$task_id" | tr -d '\n')
  owner_slug=$(jq -r '.owner | ascii_downcase' <<<"$task")
  project_slug=$(tr '[:upper:]' '[:lower:]' <<<"$declared_project" | tr -d '\n')
  [[ $declared_project =~ ^[A-Za-z0-9._-]+$ && $project_slug == *"$task_slug"* &&
     $project_slug == *"$owner_slug"* && $project_slug == *"${head:0:8}"* ]] ||
    fail "$task_id project-id does not bind task, owner, and exact Head"
  project=$declared_project

  existing_project=$(jq -c --arg project "$project" '.projects[]? | select(.name == $project)' "$projects_before")
  if [[ -n $existing_project ]]; then
    [[ $(jq -r '.root_path' <<<"$existing_project") == "$managed_worktree" ]] ||
      fail "$task_id project-id already belongs to another worktree"
  fi

  if [[ -e $managed_worktree ]]; then
    verify_worktree "$task_id" "$repo" "$managed_worktree" "$head"
  else
    reject_symlink_components "$managed_worktree"
    path_is_within "$managed_worktree" "$managed_root" || fail "$task_id managed path escaped root"
    jq --arg path "$managed_worktree" --arg repository "$repo" --arg head "$head" \
      '.created_worktrees += [{path:$path,repository:$repository,head:$head}]' \
      "$run_manifest_tmp" >"$run_manifest_tmp.next"
    mv -f "$run_manifest_tmp.next" "$run_manifest_tmp"
    git -C "$repo" worktree add --detach -- "$managed_worktree" "$head" >/dev/null
    verify_worktree "$task_id" "$repo" "$managed_worktree" "$head"
  fi
  verify_clean_worktree "$task_id" "$managed_worktree" "$head" "before indexing"

  coverage_paths=$(mktemp "$state_dir/coverage-paths.XXXXXX.json")
  cleanup_files+=("$coverage_paths")
  paths_text=$(mktemp "$state_dir/coverage-paths.XXXXXX.txt")
  cleanup_files+=("$paths_text")
  while IFS= read -r locked_path; do
    if [[ $locked_path == */ || -d $managed_worktree/$locked_path ]]; then
      locked_files=$(git -C "$managed_worktree" ls-tree -r --name-only "$head" -- "$locked_path")
      [[ -n $locked_files ]] || fail "$task_id locked directory is empty at Head: $locked_path"
      printf '%s\n' "$locked_files" >>"$paths_text"
    else
      git -C "$managed_worktree" cat-file -e "$head:$locked_path" 2>/dev/null ||
        fail "$task_id locked path is absent at Head: $locked_path"
      printf '%s\n' "$locked_path" >>"$paths_text"
    fi
  done < <(jq -r '.locked_paths[]' <<<"$task")
  LC_ALL=C sort -u "$paths_text" | jq -Rsc 'split("\n") | map(select(length > 0))' >"$coverage_paths"
  [[ $(jq length "$coverage_paths") -gt 0 ]] || fail "$task_id locked scope resolved to zero files"

  index_result=$(mktemp "$state_dir/index-result.XXXXXX.json")
  cleanup_files+=("$index_result")
  index_args=$(jq -cn --arg repo "$managed_worktree" --arg name "$project" \
    '{repo_path:$repo,name:$name,mode:"full",persistence:false}')
  if [[ -z $existing_project ]]; then
    jq --arg project "$project" '.created_projects += [$project]' \
      "$run_manifest_tmp" >"$run_manifest_tmp.next"
    mv -f "$run_manifest_tmp.next" "$run_manifest_tmp"
  fi
  mcporter_call index_repository "$index_args" "$index_result"
  verify_clean_worktree "$task_id" "$managed_worktree" "$head" "after indexing"
  mcporter_call list_projects '{}' "$projects_after"

  jq -e --arg project "$project" --arg root "$managed_worktree" \
    '.projects[] | select(.name == $project and .root_path == $root and (.nodes // 0) > 0)' \
    "$projects_after" >/dev/null || fail "$task_id index has wrong project/root or zero nodes"

  status_file=$(mktemp "$state_dir/index-status.XXXXXX.json")
  cleanup_files+=("$status_file")
  status_args=$(jq -cn --arg project "$project" '{project:$project,verbose:true}')
  mcporter_call index_status "$status_args" "$status_file"
  jq -e --arg root "$managed_worktree" --arg head "$head" \
    '(.status == "ready") and (.root_path == $root) and ((.nodes // 0) > 0) and
     ((.skipped.count // .skipped_count // 0) == 0) and
     (.git.worktree_root == $root) and (.git.head_sha == $head)' \
    "$status_file" >/dev/null || fail "$task_id index status is stale, skipped, wrong-root, wrong-Head, or zero-node"

  coverage_file=$(mktemp "$state_dir/index-coverage.XXXXXX.json")
  cleanup_files+=("$coverage_file")
  coverage_args=$(jq -cn --arg project "$project" --slurpfile paths "$coverage_paths" \
    '{project:$project,paths:$paths[0]}')
  mcporter_call check_index_coverage "$coverage_args" "$coverage_file"
  jq -e --slurpfile expected "$coverage_paths" '
    ([.paths[] | select(.status == "no_recorded_issue" and .freshness == "metadata_match") | .path] | sort) ==
    ($expected[0] | sort)
  ' "$coverage_file" >/dev/null || fail "$task_id locked-path coverage is incomplete or stale"

  impact_file=$(mktemp "$state_dir/impact.XXXXXX.json")
  cleanup_files+=("$impact_file")
  impact_args=$(jq -cn --arg project "$project" --arg since "$base" \
    '{project:$project,since:$since,direction:"inbound",scope:"impact",depth:8,limit:100000,format:"json"}')
  mcporter_call detect_changes "$impact_args" "$impact_file"
  verify_clean_worktree "$task_id" "$managed_worktree" "$head" "before receipt"
  changed_paths=$(mktemp "$state_dir/changed-paths.XXXXXX.json")
  cleanup_files+=("$changed_paths")
  git -C "$managed_worktree" diff --name-only "$base...$head" | LC_ALL=C sort -u |
    jq -Rsc 'split("\n") | map(select(length > 0))' >"$changed_paths"
  jq -e --slurpfile expected "$changed_paths" '
    (.truncated == false) and ((.changed_files | sort) == ($expected[0] | sort)) and
    ((.impacted_total | type) == "number") and ((.impacted_shown | type) == "number") and
    (.impacted_total >= 0) and (.impacted_shown == .impacted_total) and
    ((.impacted | length) == .impacted_shown)
  ' "$impact_file" >/dev/null || fail "$task_id consumer impact is truncated or does not bind the exact diff"

  receipt_file="$state_dir/receipt-$task_id-$head.json"
  receipt_tmp=$(mktemp "$state_dir/receipt.XXXXXX.json")
  cleanup_files+=("$receipt_tmp")
  status_canonical=$(mktemp "$state_dir/index-status.XXXXXX.canonical.json")
  coverage_canonical=$(mktemp "$state_dir/index-coverage.XXXXXX.canonical.json")
  impact_canonical=$(mktemp "$state_dir/impact.XXXXXX.canonical.json")
  cleanup_files+=("$status_canonical" "$coverage_canonical" "$impact_canonical")
  jq -cS . "$status_file" >"$status_canonical"
  jq -cS . "$coverage_file" >"$coverage_canonical"
  jq -cS . "$impact_file" >"$impact_canonical"
  status_sha=$(sha256sum "$status_canonical" | awk '{print $1}')
  coverage_sha=$(sha256sum "$coverage_canonical" | awk '{print $1}')
  impact_sha=$(sha256sum "$impact_canonical" | awk '{print $1}')
  nodes=$(jq -r --arg project "$project" '.projects[] | select(.name == $project) | .nodes' "$projects_after")
  edges=$(jq -r --arg project "$project" '.projects[] | select(.name == $project) | .edges' "$projects_after")
  impacted_total=$(jq -r '.impacted_total // 0' "$impact_file")
  impacted_shown=$(jq -r '.impacted_shown // (.impacted | length)' "$impact_file")
  impact_truncated=$(jq -r '.truncated' "$impact_file")
  jq -cn \
    --arg task_id "$task_id" --arg project "$project" --arg worktree "$managed_worktree" \
    --arg base "$base" --arg head "$head" --arg recorded_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg status_sha "$status_sha" --arg coverage_sha "$coverage_sha" --arg impact_sha "$impact_sha" \
    --argjson nodes "$nodes" --argjson edges "$edges" --argjson impacted_total "$impacted_total" \
    --argjson impacted_shown "$impacted_shown" --argjson impact_truncated "$impact_truncated" \
    --slurpfile locked_paths "$coverage_paths" --slurpfile changed_files "$changed_paths" \
    --slurpfile status "$status_canonical" --slurpfile coverage "$coverage_canonical" \
    --slurpfile impact "$impact_canonical" \
    '{schema:2,task_id:$task_id,project:$project,worktree:$worktree,base:$base,head:$head,
      nodes:$nodes,edges:$edges,locked_paths:$locked_paths[0],changed_files:$changed_files[0],
      consumers:{impact_complete:($impact_truncated == false and $impacted_shown == $impacted_total),impacted_total:$impacted_total,
        impacted_shown:$impacted_shown,truncated:$impact_truncated},
      evidence:{
        status:{sha256:$status_sha,payload:$status[0]},
        coverage:{sha256:$coverage_sha,payload:$coverage[0]},
        impact:{sha256:$impact_sha,payload:$impact[0]}
      },recorded_at:$recorded_at}' >"$receipt_tmp"
  chmod 0600 "$receipt_tmp"
  mv -f "$receipt_tmp" "$receipt_file"
  receipt_sha=$(sha256sum "$receipt_file" | awk '{print $1}')
  jq --arg receipt "$receipt_file" --arg sha256 "$receipt_sha" \
    '.receipts += [{path:$receipt,sha256:$sha256}]' "$run_manifest_tmp" >"$run_manifest_tmp.next"
  mv -f "$run_manifest_tmp.next" "$run_manifest_tmp"
done

verify_manifest_unchanged

run_id=$(date -u +%Y%m%dT%H%M%SZ)-$$
run_manifest="$state_dir/run-$run_id.json"
jq --arg completed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '. + {completed_at:$completed_at}' \
  "$run_manifest_tmp" >"$run_manifest"
chmod 0600 "$run_manifest"
printf 'workspace sync complete: tasks=%s manifest=%s sha256=%s\n' \
  "$task_count" "$run_manifest" "$(sha256sum "$run_manifest" | awk '{print $1}')"
