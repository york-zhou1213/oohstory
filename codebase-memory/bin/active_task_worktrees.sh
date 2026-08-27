#!/usr/bin/env bash
set -euo pipefail

readonly EX_USAGE=64
readonly EX_DATAERR=65
readonly EX_NOINPUT=66
readonly EX_CONFIG=78

tasks_dir=${CBM_ACTIVE_TASKS_DIR:-/root/.openclaw/workspaces/engineering-team/tasks/active}
managed_root=${CBM_MANAGED_ROOT:-/root/.codespace/workspace/.cbm-task-worktrees}
allowed_root=${CBM_ALLOWED_ROOT:-/root/.codespace/workspace}

usage() {
  printf 'Usage: %s [--tasks-dir PATH] [--managed-root PATH] [--allowed-root PATH]\n' "$0"
}

while (($#)); do
  case "$1" in
    --tasks-dir)
      shift
      tasks_dir=${1:-}
      ;;
    --managed-root)
      shift
      managed_root=${1:-}
      ;;
    --allowed-root)
      shift
      allowed_root=${1:-}
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

for command_name in awk git jq realpath sha256sum; do
  command -v "$command_name" >/dev/null || {
    printf 'CODEBASE_MCP_BLOCKED: missing required command: %s\n' "$command_name" >&2
    exit "$EX_CONFIG"
  }
done

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

[[ -d $tasks_dir ]] || {
  printf 'active task directory is missing: %s\n' "$tasks_dir" >&2
  exit "$EX_NOINPUT"
}
reject_symlink_components "$tasks_dir"
tasks_dir=$(realpath -e -- "$tasks_dir")

[[ $managed_root == /* ]] || fail "managed root is not absolute: $managed_root"
reject_symlink_components "$managed_root"
managed_root=$(realpath -m -- "$managed_root")
[[ $allowed_root == /* ]] || fail "allowed root is not absolute: $allowed_root"
reject_symlink_components "$allowed_root"
[[ -d $allowed_root ]] || fail "allowed root is missing: $allowed_root"
allowed_root=$(realpath -e -- "$allowed_root")
case "${managed_root%/}/" in
  "${allowed_root%/}/"*) ;;
  *) fail "managed root is outside allowed root" ;;
esac

frontmatter_scalar() {
  local file=$1 key=$2
  awk -v wanted="$key" '
    NR == 1 && $0 == "---" { frontmatter=1; next }
    frontmatter && $0 == "---" { exit }
    frontmatter && index($0, wanted ":") == 1 {
      value=substr($0, length(wanted) + 2)
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      if ((substr(value,1,1) == "\"" && substr(value,length(value),1) == "\"") ||
          (substr(value,1,1) == "\047" && substr(value,length(value),1) == "\047")) {
        value=substr(value,2,length(value)-2)
      }
      print value
      exit
    }
  ' "$file"
}

frontmatter_nested_scalar() {
  local file=$1 section=$2 key=$3
  awk -v wanted_section="$section" -v wanted_key="$key" '
    NR == 1 && $0 == "---" { frontmatter=1; next }
    frontmatter && $0 == "---" { exit }
    !frontmatter { next }
    $0 == wanted_section ":" { in_section=1; next }
    in_section && $0 !~ /^  / { exit }
    in_section && index($0, "  " wanted_key ":") == 1 {
      value=substr($0, length(wanted_key) + 4)
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      if ((substr(value,1,1) == "\"" && substr(value,length(value),1) == "\"") ||
          (substr(value,1,1) == "\047" && substr(value,length(value),1) == "\047")) {
        value=substr(value,2,length(value)-2)
      }
      print value
      exit
    }
  ' "$file"
}

frontmatter_list_json() {
  local file=$1 key=$2
  awk -v wanted="$key" '
    NR == 1 && $0 == "---" { frontmatter=1; next }
    frontmatter && $0 == "---" { exit }
    !frontmatter { next }
    $0 == wanted ":" { in_list=1; next }
    in_list && $0 ~ /^  - / {
      value=substr($0,5)
      sub(/[[:space:]]+$/, "", value)
      if ((substr(value,1,1) == "\"" && substr(value,length(value),1) == "\"") ||
          (substr(value,1,1) == "\047" && substr(value,length(value),1) == "\047")) {
        value=substr(value,2,length(value)-2)
      }
      print value
      next
    }
    in_list && $0 !~ /^  / { exit }
  ' "$file" | jq -Rsc 'split("\n") | map(select(length > 0))'
}

is_managed_state() {
  case "$1" in
    ASSIGNED|IMPLEMENTING|CODE_REVIEW|FIX_REQUIRED|TESTING|READY_TO_RELEASE|BLOCKED|DEPLOYING|VERIFIED)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

is_busy_state() {
  case "$1" in
    IMPLEMENTING|FIX_REQUIRED|TESTING) return 0 ;;
    *) return 1 ;;
  esac
}

validate_locked_paths() {
  local task_id=$1 paths_json=$2 path
  while IFS= read -r path; do
    [[ -n $path ]] || fail "$task_id has an empty locked path"
    [[ $path != /* ]] || fail "$task_id has an absolute locked path: $path"
    case "/$path/" in
      */../*|*/./*) fail "$task_id has a traversal locked path: $path" ;;
    esac
    [[ $path != *$'\n'* && $path != *$'\r'* && $path != *$'\t'* ]] ||
      fail "$task_id has a control character in a locked path"
  done < <(jq -r '.[]' <<<"$paths_json")
}

common_git_dir() {
  local repo=$1 common
  common=$(git -C "$repo" rev-parse --git-common-dir)
  if [[ $common != /* ]]; then
    common="$repo/$common"
  fi
  realpath -e -- "$common"
}

records_file=$(mktemp)
trap 'rm -f "$records_file"' EXIT

shopt -s nullglob
task_files=("$tasks_dir"/TASK-*.md)
for task_file in "${task_files[@]}"; do
  reject_symlink_components "$task_file"
  task_hash_before=$(sha256sum "$task_file" | awk '{print $1}')

  task_id=$(frontmatter_scalar "$task_file" task-id)
  state=$(frontmatter_scalar "$task_file" state)
  [[ $task_id =~ ^TASK-[0-9]{8}-[0-9]{3,}$ ]] || fail "invalid task-id in $task_file"
  [[ ${task_file##*/} == "$task_id.md" ]] || fail "task filename does not match task-id: $task_file"
  is_managed_state "$state" || continue

  owner=$(frontmatter_scalar "$task_file" owner)
  repository=$(frontmatter_scalar "$task_file" repository)
  base_revision=$(frontmatter_scalar "$task_file" base-revision)
  declared_project=$(frontmatter_nested_scalar "$task_file" codebase-mcp project-id)
  declared_worktree=$(frontmatter_nested_scalar "$task_file" codebase-mcp worktree)
  declared_base=$(frontmatter_nested_scalar "$task_file" codebase-mcp base)
  head=$(frontmatter_nested_scalar "$task_file" codebase-mcp head)
  locked_paths=$(frontmatter_list_json "$task_file" file-scope-lock)

  [[ $owner =~ ^[A-Za-z][A-Za-z0-9_-]*$ ]] || fail "$task_id has an invalid owner"
  validate_locked_paths "$task_id" "$locked_paths"
  ((${#locked_paths} > 2)) || fail "$task_id has no file-scope lock"

  busy=false
  is_busy_state "$state" && busy=true
  owner_slug=$(tr '[:upper:]_' '[:lower:]-' <<<"$owner" | tr -d '\n')
  managed_worktree="$managed_root/$task_id-$owner_slug"

  binding_ready=true
  if [[ ! $repository == /* || ! $base_revision =~ ^[0-9a-f]{40}$ ||
        ! $declared_base =~ ^[0-9a-f]{40}$ || ! $head =~ ^[0-9a-f]{40}$ ||
        -z $declared_project || $declared_project == CODEBASE_MCP_BLOCKED-* ||
        -z $declared_worktree || $declared_worktree == CODEBASE_MCP_BLOCKED-* ]]; then
    binding_ready=false
  fi

  if [[ $busy == true && $binding_ready == true && -d $declared_worktree ]] &&
     [[ $(git -C "$declared_worktree" rev-parse HEAD 2>/dev/null || true) != "$head" ]]; then
    binding_ready=false
  fi

  if [[ $busy == false && $binding_ready == true ]]; then
    reject_symlink_components "$repository"
    [[ -d $repository ]] || fail "$task_id repository is missing: $repository"
    repository=$(realpath -e -- "$repository")
    case "${repository%/}/" in
      "${allowed_root%/}/"*) ;;
      *) fail "$task_id repository is outside allowed root" ;;
    esac
    git -C "$repository" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
      fail "$task_id repository is not a Git worktree: $repository"
    [[ $declared_base == "$base_revision" ]] || fail "$task_id MCP Base differs from base-revision"
    git -C "$repository" cat-file -e "$base_revision^{commit}" 2>/dev/null ||
      fail "$task_id Base commit is unavailable in repository"
    git -C "$repository" cat-file -e "$head^{commit}" 2>/dev/null ||
      fail "$task_id Head commit is unavailable in repository"
    git -C "$repository" merge-base --is-ancestor "$base_revision" "$head" ||
      fail "$task_id Base is not an ancestor of Head"

    [[ $declared_worktree == /* ]] || fail "$task_id declared worktree is not absolute"
    reject_symlink_components "$declared_worktree"
    if [[ -e $declared_worktree ]]; then
      [[ -d $declared_worktree ]] || fail "$task_id declared worktree is not a directory"
      declared_worktree=$(realpath -e -- "$declared_worktree")
      [[ $(git -C "$declared_worktree" rev-parse HEAD 2>/dev/null) == "$head" ]] ||
        fail "$task_id declared worktree has exact-Head drift"
      [[ $(common_git_dir "$declared_worktree") == "$(common_git_dir "$repository")" ]] ||
        fail "$task_id declared worktree belongs to another repository"
    fi
  fi

  task_hash_after=$(sha256sum "$task_file" | awk '{print $1}')
  [[ $task_hash_after == "$task_hash_before" ]] || fail "$task_id record changed while it was read"

  jq -cn \
    --arg task_id "$task_id" \
    --arg owner "$owner" \
    --arg state "$state" \
    --arg repository "$repository" \
    --arg base "$base_revision" \
    --arg head "$head" \
    --arg project "$declared_project" \
    --arg declared_worktree "$declared_worktree" \
    --arg managed_worktree "$managed_worktree" \
    --argjson locked_paths "$locked_paths" \
    --argjson busy "$busy" \
    --argjson binding_ready "$binding_ready" \
    '{task_id:$task_id,owner:$owner,state:$state,repository:$repository,
      base:$base,head:$head,project:$project,declared_worktree:$declared_worktree,
      managed_worktree:$managed_worktree,locked_paths:$locked_paths,busy:$busy,
      binding_ready:$binding_ready}' >>"$records_file"
done

jq -sc --arg tasks_dir "$tasks_dir" --arg managed_root "$managed_root" --arg allowed_root "$allowed_root" \
  '{schema:1,tasks_dir:$tasks_dir,managed_root:$managed_root,allowed_root:$allowed_root,tasks:sort_by(.task_id)}' \
  "$records_file"
