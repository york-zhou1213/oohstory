#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
manifest_source="$repo_root/codebase-memory/bin/active_task_worktrees.sh"
sync_source="$repo_root/codebase-memory/bin/workspace_sync.sh"
materialize_source="$repo_root/codebase-memory/bin/materialize_runtime_files.sh"
wrapper_source="$repo_root/codebase-memory/bin/mcp_call_locked.sh"
runtime_staging_root="$repo_root/.cbm-runtime-staging"

tests_run=0
fx=
runtime_stage=

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_eq() {
  local expected=$1 actual=$2 label=$3
  [[ $actual == "$expected" ]] || fail "$label: expected <$expected>, got <$actual>"
}

assert_contains() {
  local file=$1 needle=$2 label=$3
  grep -F -- "$needle" "$file" >/dev/null || fail "$label: missing <$needle>"
}

cleanup_fixture() {
  if [[ -n ${runtime_stage:-} && -d $runtime_stage ]]; then
    rm -rf -- "$runtime_stage"
    runtime_stage=
  fi
  [[ -n ${fx:-} && -d $fx ]] || return 0
  if [[ -d $fx/workspace/repo/.git ]]; then
    git -C "$fx/workspace/repo" worktree list --porcelain 2>/dev/null |
      awk '/^worktree / {print substr($0,10)}' |
      while IFS= read -r worktree; do
        [[ $worktree == "$fx/"* ]] || continue
        [[ $worktree == "$fx/workspace/repo" ]] && continue
        git -C "$fx/workspace/repo" worktree remove --force -- "$worktree" >/dev/null 2>&1 || true
      done
  fi
  rm -rf -- "$fx"
  fx=
}
trap cleanup_fixture EXIT

write_client_config() {
  local path=$1 allowed=$2 cache=$3 memory=$4 workers=$5 wrapper=$6 lock=$7 binary=$8
  install -d -m 0700 -- "$(dirname -- "$path")"
  {
    printf '[mcp_servers.codebase-memory-mcp]\n'
    printf 'command = "%s"\n\n' "$wrapper"
    printf '[mcp_servers.codebase-memory-mcp.env]\n'
    printf 'CBM_ALLOWED_ROOT = "%s"\n' "$allowed"
    printf 'CBM_CACHE_DIR = "%s"\n' "$cache"
    printf 'CBM_MEM_BUDGET_MB = "%s"\n' "$memory"
    printf 'CBM_WORKERS = "%s"\n' "$workers"
    printf 'CBM_CALL_LOCK = "%s"\n' "$lock"
    printf 'CBM_MCP_BINARY = "%s"\n' "$binary"
  } >"$path"
}

write_task() {
  local state=$1 declared_worktree=$2 declared_head=$3 locked_path=${4:-locked/new.sh}
  local project="task-20260827-101-john-${declared_head:0:8}"
  {
    printf '%s\n' '---'
    printf 'task-id: TASK-20260827-101\n'
    printf 'objective: Fixture task\n'
    printf 'repository: %s\n' "$fx/workspace/repo"
    printf 'canonical-source: %s\n' "$fx/workspace/repo"
    printf 'base-revision: %s\n' "$base"
    printf 'owner: John\n'
    printf 'file-scope-lock:\n'
    printf '  - %s\n' "$locked_path"
    printf 'risk-class: HIGH\n'
    printf 'codebase-mcp:\n'
    printf '  project-id: %s\n' "$project"
    printf '  worktree: %s\n' "$declared_worktree"
    printf '  base: %s\n' "$base"
    printf '  head: %s\n' "$declared_head"
    printf 'state: %s\n' "$state"
    printf '%s\n' '---'
  } >"$fx/tasks/active/TASK-20260827-101.md"
}

new_fixture() {
  cleanup_fixture
  fx=$(mktemp -d -t cbm-sync-test.XXXXXX)
  install -d -m 0700 -- "$fx/tasks/active" "$fx/tasks/archive" "$fx/workspace/.cbm-task-worktrees" "$fx/state" "$fx/bin" "$fx/config"

  git init -q "$fx/workspace/repo"
  git -C "$fx/workspace/repo" config user.name Fixture
  git -C "$fx/workspace/repo" config user.email fixture@localhost
  printf 'base\n' >"$fx/workspace/repo/app.txt"
  git -C "$fx/workspace/repo" add app.txt
  git -C "$fx/workspace/repo" commit -q -m base
  base=$(git -C "$fx/workspace/repo" rev-parse HEAD)
  install -d -m 0755 -- "$fx/workspace/repo/locked"
  printf '#!/usr/bin/env bash\nprintf "fixture\\n"\n' >"$fx/workspace/repo/locked/new.sh"
  chmod 0755 "$fx/workspace/repo/locked/new.sh"
  git -C "$fx/workspace/repo" add locked/new.sh
  git -C "$fx/workspace/repo" commit -q -m head
  head=$(git -C "$fx/workspace/repo" rev-parse HEAD)
  git -C "$fx/workspace/repo" worktree add -q --detach "$fx/outside-worktree" "$head"

  cache="$fx/cache"
  call_lock="$fx/call.lock"
  mcp_binary="$fx/bin/codebase-memory-mcp"
  printf '#!/usr/bin/env bash\nexit 0\n' >"$mcp_binary"
  chmod 0755 "$mcp_binary"
  install -d -m 0700 -- "$cache"
  for role in john jucy mus; do
    write_client_config "$fx/config/$role.toml" "$fx/workspace" "$cache" 512 1 \
      "$wrapper_source" "$call_lock" "$mcp_binary"
  done
  client_configs="$fx/config/john.toml:$fx/config/jucy.toml:$fx/config/mus.toml"

  jq -cn --arg allowed "$fx/workspace" --arg cache "$cache" --arg wrapper "$wrapper_source" \
    --arg call_lock "$call_lock" --arg binary "$mcp_binary" '
    {mcpServers:{"codebase-memory-mcp":{command:$wrapper,
      env:{CBM_ALLOWED_ROOT:$allowed,CBM_CACHE_DIR:$cache,CBM_MEM_BUDGET_MB:"512",CBM_WORKERS:"1",
        CBM_CALL_LOCK:$call_lock,CBM_MCP_BINARY:$binary}}},imports:[]}
  ' >"$fx/config/mcporter.json"

  apply_mock_mcporter
  write_task CODE_REVIEW "$fx/outside-worktree" "$head"
  export_fixture_profile
}

apply_mock_mcporter() {
  local mock="$fx/bin/mcporter"
  {
    printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail'
cat <<'MOCK'
if [[ ${1:-} == --config ]]; then
  shift 2
fi
[[ ${1:-} == call ]] || exit 64
tool=${2#*.}
shift 2
args='{}'
while (($#)); do
  case "$1" in
    --args) shift; args=${1:-} ;;
    --output) shift ;;
  esac
  shift
done
printf '%s\n' "$tool" >>"$MOCK_CALL_LOG"
case "$tool" in
  list_projects)
    if [[ -f $MOCK_INDEX_STATE ]]; then
      jq -cn --slurpfile state "$MOCK_INDEX_STATE" --argjson nodes "${MOCK_NODES:-9}" '
        {projects:[{name:$state[0].project,root_path:$state[0].root,branch:"DETACHED",nodes:$nodes,edges:12}]}'
    else
      printf '%s\n' '{"projects":[]}'
    fi
    ;;
  index_repository)
    project=$(jq -r '.name' <<<"$args")
    root=$(jq -r '.repo_path' <<<"$args")
    head=$(git -C "$root" rev-parse HEAD)
    jq -cn --arg project "$project" --arg root "$root" --arg head "$head" \
      '{project:$project,root:$root,head:$head}' >"$MOCK_INDEX_STATE"
    case ${MOCK_DIRTY_AFTER_INDEX:-none} in
      tracked) printf 'drift\n' >>"$root/app.txt" ;;
      untracked) printf 'drift\n' >"$root/untracked-drift.txt" ;;
    esac
    printf '%s\n' '{"status":"ready"}'
    ;;
  index_status)
    jq -cn --slurpfile state "$MOCK_INDEX_STATE" --argjson nodes "${MOCK_NODES:-9}" '
      {project:$state[0].project,nodes:$nodes,edges:12,status:"ready",root_path:$state[0].root,
       git:{worktree_root:$state[0].root,head_sha:$state[0].head},skipped:{count:0,files:[]}}'
    ;;
  check_index_coverage)
    if [[ ${MOCK_COVERAGE_MISSING:-0} == 1 ]]; then
      jq -cn --arg project "$(jq -r '.project' <<<"$args")" --arg path "$(jq -r '.paths[0]' <<<"$args")" '
        {project:$project,paths:[{requested_path:$path,path:$path,status:"missing",freshness:"metadata_missing"}]}'
    else
      jq -cn --arg project "$(jq -r '.project' <<<"$args")" --argjson paths "$(jq '.paths' <<<"$args")" '
        {project:$project,paths:[$paths[] | {requested_path:.,path:.,status:"no_recorded_issue",freshness:"metadata_match"}]}'
    fi
    ;;
  detect_changes)
    root=$(jq -r '.root' "$MOCK_INDEX_STATE")
    since=$(jq -r '.since' <<<"$args")
    head=$(git -C "$root" rev-parse HEAD)
    changed=$(git -C "$root" diff --name-only "$since...$head" | LC_ALL=C sort -u | jq -Rsc 'split("\n") | map(select(length > 0))')
    if [[ ${MOCK_DIRTY_DURING_IMPACT:-none} == untracked ]]; then
      printf 'drift\n' >"$root/impact-drift.txt"
    fi
    jq -cn --argjson changed "$changed" --argjson truncated "${MOCK_TRUNCATED:-false}" \
      '{changed_files:$changed,impacted_total:1,impacted_shown:1,
        impacted:[{qn:"fixture.consumer",label:"Module",file:"consumer.sh",hop:1}],truncated:$truncated}'
    ;;
  delete_project)
    rm -f -- "$MOCK_INDEX_STATE"
    printf '%s\n' '{"deleted":true}'
    ;;
  *) exit 64 ;;
esac
MOCK
  } >"$mock"
  chmod 0755 "$mock"
}

export_fixture_profile() {
  export PATH="$fx/bin:$ORIGINAL_PATH"
  export CBM_ACTIVE_TASKS_DIR="$fx/tasks/active"
  export CBM_ALLOWED_ROOT="$fx/workspace"
  export CBM_MANAGED_ROOT="$fx/workspace/.cbm-task-worktrees"
  export CBM_CACHE_DIR="$cache"
  export CBM_MEM_BUDGET_MB=512
  export CBM_WORKERS=1
  export CBM_STATE_DIR="$fx/state"
  export CBM_SYNC_LOCK="$fx/sync.lock"
  export CBM_CALL_LOCK="$fx/call.lock"
  export CBM_MCPORTER_CONFIG="$fx/config/mcporter.json"
  export CBM_CLIENT_CONFIGS="$client_configs"
  export CBM_EXPECTED_ALLOWED_ROOT="$fx/workspace"
  export CBM_EXPECTED_CACHE_DIR="$cache"
  export CBM_EXPECTED_MEM_BUDGET_MB=512
  export CBM_EXPECTED_WORKERS=1
  export CBM_MCP_WRAPPER="$wrapper_source"
  export CBM_MCP_BINARY="$mcp_binary"
  export MOCK_CALL_LOG="$fx/calls.log"
  export MOCK_INDEX_STATE="$fx/index-state.json"
  export MOCK_NODES=9
  export MOCK_COVERAGE_MISSING=0
  export MOCK_TRUNCATED=false
  export MOCK_DIRTY_AFTER_INDEX=none
  export MOCK_DIRTY_DURING_IMPACT=none
}

expect_failure() {
  local label=$1
  shift
  if "$@" >"$fx/stdout" 2>"$fx/stderr"; then
    fail "$label unexpectedly succeeded"
  fi
}

run_test() {
  local name=$1
  shift
  "$@"
  ((tests_run += 1))
  printf 'ok %s - %s\n' "$tests_run" "$name"
}

test_active_archive_and_disposable_exclusion() {
  new_fixture
  cp "$fx/tasks/active/TASK-20260827-101.md" "$fx/tasks/archive/TASK-20260827-102.md"
  install -d -m 0755 -- "$fx/workspace/mus-TASK-review-disposable" "$fx/workspace/review-checkout"
  "$manifest_source" --tasks-dir "$fx/tasks/active" --managed-root "$fx/workspace/.cbm-task-worktrees" >"$fx/manifest.json"
  assert_eq 1 "$(jq '.tasks | length' "$fx/manifest.json")" 'active record count'
  assert_eq TASK-20260827-101 "$(jq -r '.tasks[0].task_id' "$fx/manifest.json")" 'active task identity'
  assert_eq "$fx/workspace/.cbm-task-worktrees/TASK-20260827-101-john" \
    "$(jq -r '.tasks[0].managed_worktree' "$fx/manifest.json")" 'managed worktree identity'
  ! grep -F 'disposable' "$fx/manifest.json" >/dev/null || fail 'disposable directory leaked into manifest'
}

test_path_traversal_rejected() {
  new_fixture
  write_task CODE_REVIEW "$fx/outside-worktree" "$head" '../escape.sh'
  expect_failure traversal "$manifest_source" --tasks-dir "$fx/tasks/active" --managed-root "$fx/workspace/.cbm-task-worktrees"
  assert_contains "$fx/stderr" 'traversal locked path' 'traversal rejection'
}

test_symlink_worktree_rejected() {
  new_fixture
  ln -s "$fx/outside-worktree" "$fx/worktree-link"
  write_task CODE_REVIEW "$fx/worktree-link" "$head"
  expect_failure symlink "$manifest_source" --tasks-dir "$fx/tasks/active" --managed-root "$fx/workspace/.cbm-task-worktrees"
  assert_contains "$fx/stderr" 'symlink path is forbidden' 'symlink rejection'
}

test_active_caller_skips_all_mcp_work() {
  new_fixture
  write_task IMPLEMENTING "$fx/outside-worktree" "$head"
  "$sync_source" >"$fx/stdout"
  assert_contains "$fx/stdout" 'active task caller(s)=TASK-20260827-101' 'active caller skip'
  [[ ! -s $MOCK_CALL_LOG ]] || fail 'active caller skip invoked MCP'
}

test_active_caller_rejects_locked_entry_drift() {
  new_fixture
  write_task IMPLEMENTING "$fx/outside-worktree" "$head"
  sed -i 's#^command = .*#command = "/usr/local/bin/codebase-memory-mcp"#' "$fx/config/jucy.toml"
  expect_failure active-bypass "$sync_source"
  assert_contains "$fx/stderr" 'client bypasses locked MCP entry' 'active client bypass rejection'
  [[ ! -s $MOCK_CALL_LOG ]] || fail 'active client bypass invoked MCP'
}

test_active_caller_with_in_stage_head_drift_skips() {
  new_fixture
  write_task IMPLEMENTING "$fx/outside-worktree" "$base"
  "$sync_source" >"$fx/stdout"
  assert_contains "$fx/stdout" 'active task caller(s)=TASK-20260827-101' 'drifted active caller skip'
  [[ ! -s $MOCK_CALL_LOG ]] || fail 'drifted active caller skip invoked MCP'
}

test_profile_mismatch_fails_before_index() {
  new_fixture
  jq '.mcpServers["codebase-memory-mcp"].env.CBM_MEM_BUDGET_MB="2048"' "$fx/config/mcporter.json" >"$fx/config/mcporter.next"
  mv "$fx/config/mcporter.next" "$fx/config/mcporter.json"
  expect_failure profile "$sync_source"
  assert_contains "$fx/stderr" 'mcporter cache/root/worker profile mismatch' 'profile mismatch'
  [[ ! -s $MOCK_CALL_LOG ]] || fail 'profile mismatch invoked MCP'
}

test_direct_client_bypass_rejected() {
  new_fixture
  sed -i 's#^command = .*#command = "/usr/local/bin/codebase-memory-mcp"#' "$fx/config/john.toml"
  expect_failure bypass "$sync_source"
  assert_contains "$fx/stderr" 'client bypasses locked MCP entry' 'direct client bypass rejection'
  [[ ! -s $MOCK_CALL_LOG ]] || fail 'client bypass invoked MCP'

  new_fixture
  jq '.mcpServers["codebase-memory-mcp"].command="/usr/local/bin/codebase-memory-mcp"' \
    "$fx/config/mcporter.json" >"$fx/config/mcporter.next"
  mv "$fx/config/mcporter.next" "$fx/config/mcporter.json"
  expect_failure mcporter-bypass "$sync_source"
  assert_contains "$fx/stderr" 'mcporter cache/root/worker profile mismatch' \
    'mcporter locked-entry bypass rejection'
  [[ ! -s $MOCK_CALL_LOG ]] || fail 'mcporter bypass invoked MCP'
}

test_locked_entry_serializes_real_processes() {
  new_fixture
  cat >"$fx/bin/concurrent-mcp" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if ! mkdir "$MCP_ACTIVE_DIR" 2>/dev/null; then
  touch "$MCP_OVERLAP_FILE"
  exit 1
fi
printf 'start %s\n' "$$" >>"$MCP_RUN_LOG"
sleep 0.2
printf 'end %s\n' "$$" >>"$MCP_RUN_LOG"
rmdir "$MCP_ACTIVE_DIR"
EOF
  chmod 0755 "$fx/bin/concurrent-mcp"
  export CBM_CALL_LOCK="$fx/real-concurrency.lock"
  export CBM_MCP_BINARY="$fx/bin/concurrent-mcp"
  export MCP_ACTIVE_DIR="$fx/mcp-active"
  export MCP_OVERLAP_FILE="$fx/mcp-overlap"
  export MCP_RUN_LOG="$fx/mcp-runs.log"

  "$wrapper_source" &
  first_pid=$!
  "$wrapper_source" &
  second_pid=$!
  wait "$first_pid"
  wait "$second_pid"

  [[ ! -e $MCP_OVERLAP_FILE ]] || fail 'locked entry allowed overlapping MCP processes'
  assert_eq 2 "$(grep -c '^start ' "$MCP_RUN_LOG")" 'serialized process starts'
  assert_eq 2 "$(grep -c '^end ' "$MCP_RUN_LOG")" 'serialized process completions'
}

test_exact_head_drift_rejected() {
  new_fixture
  write_task CODE_REVIEW "$fx/outside-worktree" "$base"
  expect_failure head-drift "$sync_source"
  assert_contains "$fx/stderr" 'declared worktree has exact-Head drift' 'exact Head drift'
  [[ ! -s $MOCK_CALL_LOG ]] || fail 'Head drift invoked MCP'
}

test_untracked_state_before_index_rejected() {
  new_fixture
  managed="$fx/workspace/.cbm-task-worktrees/TASK-20260827-101-john"
  git -C "$fx/workspace/repo" worktree add -q --detach "$managed" "$head"
  printf 'drift\n' >"$managed/pre-index-drift.txt"
  expect_failure pre-index-drift "$sync_source"
  assert_contains "$fx/stderr" 'managed worktree is dirty before maintenance MCP calls' \
    'untracked pre-index state rejection'
  [[ ! -s $MOCK_CALL_LOG ]] || fail 'dirty pre-index state invoked MCP'
}

test_tracked_drift_after_index_rejected() {
  new_fixture
  export MOCK_DIRTY_AFTER_INDEX=tracked
  expect_failure tracked-drift "$sync_source"
  assert_contains "$fx/stderr" 'managed worktree is dirty after indexing' 'tracked post-index drift rejection'
  [[ ! -e $fx/state/receipt-TASK-20260827-101-$head.json ]] ||
    fail 'tracked drift produced a receipt'
}

test_untracked_drift_before_receipt_rejected() {
  new_fixture
  export MOCK_DIRTY_DURING_IMPACT=untracked
  expect_failure untracked-drift "$sync_source"
  assert_contains "$fx/stderr" 'managed worktree is dirty before receipt' 'untracked receipt drift rejection'
  [[ ! -e $fx/state/receipt-TASK-20260827-101-$head.json ]] ||
    fail 'untracked drift produced a receipt'
}

test_zero_node_scope_fails_closed() {
  new_fixture
  export MOCK_NODES=0
  expect_failure zero-node "$sync_source"
  assert_contains "$fx/stderr" 'zero nodes' 'zero-node rejection'
  assert_eq index_repository "$(sed -n '2p' "$MOCK_CALL_LOG")" 'index call ordering'
}

test_locked_path_omission_fails_closed() {
  new_fixture
  export MOCK_COVERAGE_MISSING=1
  expect_failure coverage "$sync_source"
  assert_contains "$fx/stderr" 'locked-path coverage is incomplete' 'locked-path omission'
  assert_contains "$MOCK_CALL_LOG" check_index_coverage 'coverage call'
}

test_exact_mirror_receipt_and_rollback() {
  new_fixture
  "$sync_source" >"$fx/stdout"
  managed="$fx/workspace/.cbm-task-worktrees/TASK-20260827-101-john"
  [[ -d $managed ]] || fail 'managed mirror was not created'
  assert_eq "$head" "$(git -C "$managed" rev-parse HEAD)" 'managed mirror Head'
  run_manifest=$(sed -n 's/^.* manifest=\([^ ]*\) sha256=.*$/\1/p' "$fx/stdout")
  [[ -f $run_manifest ]] || fail 'run manifest missing'
  receipt=$(jq -r '.receipts[0].path' "$run_manifest")
  [[ -f $receipt ]] || fail 'coverage receipt missing'
  assert_eq "$managed" "$(jq -r '.worktree' "$receipt")" 'receipt worktree'
  assert_eq "$head" "$(jq -r '.head' "$receipt")" 'receipt Head'
  assert_eq true "$(jq -r '.consumers.impact_complete' "$receipt")" 'consumer completeness'
  assert_eq false "$(jq -r '.consumers.truncated' "$receipt")" 'consumer truncation flag'
  assert_eq 1 "$(jq -r '.consumers.impacted_shown' "$receipt")" 'consumer shown count'
  assert_eq 1 "$(jq -r '.consumers.impacted_total' "$receipt")" 'consumer total count'
  assert_eq 9 "$(jq -r '.nodes' "$receipt")" 'receipt nodes'
  assert_eq 0 "$(jq -r '.evidence.status.payload.skipped.count' "$receipt")" 'status skipped count'
  assert_eq metadata_match \
    "$(jq -r '.evidence.coverage.payload.paths[0].freshness' "$receipt")" \
    'coverage metadata freshness'
  assert_eq false "$(jq -r '.evidence.impact.payload.truncated' "$receipt")" 'raw impact truncation'
  assert_eq fixture.consumer "$(jq -r '.evidence.impact.payload.impacted[0].qn' "$receipt")" \
    'specific consumer evidence'
  for evidence_name in status coverage impact; do
    expected_sha=$(jq -r --arg name "$evidence_name" '.evidence[$name].sha256' "$receipt")
    actual_sha=$(jq -cS --arg name "$evidence_name" '.evidence[$name].payload' "$receipt" | sha256sum | awk '{print $1}')
    assert_eq "$expected_sha" "$actual_sha" "$evidence_name evidence hash binding"
  done

  "$sync_source" --rollback "$run_manifest" >"$fx/rollback.out"
  [[ ! -e $managed ]] || fail 'rollback left managed mirror'
  [[ ! -e $MOCK_INDEX_STATE ]] || fail 'rollback left graph project'
  assert_contains "$MOCK_CALL_LOG" delete_project 'rollback project deletion'
}

test_failed_run_manifest_rolls_back_partial_state() {
  new_fixture
  export MOCK_COVERAGE_MISSING=1
  expect_failure partial-run "$sync_source"
  failed_manifest=$(sed -n 's/^rollback manifest: //p' "$fx/stderr")
  [[ -f $failed_manifest ]] || fail 'failed run did not preserve rollback manifest'
  assert_eq 1 "$(jq '.created_worktrees | length' "$failed_manifest")" 'failed-run worktree inventory'
  assert_eq 1 "$(jq '.created_projects | length' "$failed_manifest")" 'failed-run project inventory'
  export MOCK_COVERAGE_MISSING=0
  "$sync_source" --rollback "$failed_manifest" >"$fx/rollback.out"
  [[ ! -e $fx/workspace/.cbm-task-worktrees/TASK-20260827-101-john ]] || fail 'partial rollback left managed mirror'
  [[ ! -e $MOCK_INDEX_STATE ]] || fail 'partial rollback left graph project'
}

test_runtime_materialization_verifies() {
  local render_dir expected_dir unsafe_dir verify_root verify_output rendered_names
  local race_dir race_bound race_outside race_pid race_state
  new_fixture
  [[ -x $sync_source && -x $manifest_source && -x $materialize_source && -x $wrapper_source ]] ||
    fail 'production scripts are not executable'

  install -d -m 0700 -- "$runtime_staging_root"
  runtime_stage=$(mktemp -d "$runtime_staging_root/TASK-20260826-002-test.XXXXXX")
  render_dir="$runtime_stage/runtime-render"
  expected_dir="$runtime_stage/runtime-expected"
  unsafe_dir="$runtime_stage/runtime-unsafe"
  install -d -m 0700 -- "$render_dir" "$expected_dir" "$unsafe_dir"

  if "$materialize_source" "$runtime_stage/missing-output" >"$fx/stdout" 2>"$fx/stderr"; then
    fail 'missing output directory unexpectedly accepted'
  fi
  assert_contains "$fx/stderr" 'output directory is missing' 'missing output rejection'

  if "$materialize_source" /etc >"$fx/stdout" 2>"$fx/stderr"; then
    fail 'live output directory unexpectedly accepted'
  fi
  assert_contains "$fx/stderr" 'outside staging root' 'live output rejection'

  if "$materialize_source" /root/.config/systemd/user >"$fx/stdout" 2>"$fx/stderr"; then
    fail 'live user unit directory unexpectedly accepted'
  fi
  assert_contains "$fx/stderr" 'outside staging root' 'live user unit rejection'

  ln -s "$render_dir" "$runtime_stage/runtime-link"
  if "$materialize_source" "$runtime_stage/runtime-link" >"$fx/stdout" 2>"$fx/stderr"; then
    fail 'symlinked output directory unexpectedly accepted'
  fi
  assert_contains "$fx/stderr" 'symlink or non-directory path is forbidden' 'symlinked output rejection'

  ln -s "$fx/nowhere" "$unsafe_dir/workspace-sync.env"
  if "$materialize_source" "$unsafe_dir" >"$fx/stdout" 2>"$fx/stderr"; then
    fail 'symlinked output target unexpectedly accepted'
  fi
  assert_contains "$fx/stderr" 'symlink target is forbidden' 'symlinked target rejection'
  [[ ! -e $unsafe_dir/codebase-memory-workspace-sync.service ]] ||
    fail 'unsafe target rejection produced a partial service file'

  race_dir="$runtime_stage/race-output"
  race_bound="$runtime_stage/race-bound"
  race_outside="$fx/race-outside"
  install -d -m 0700 -- "$race_dir" "$race_outside"
  CBM_TEST_STOP_AFTER_OUTPUT_OPEN=1 "$materialize_source" "$race_dir" \
    >"$fx/race.stdout" 2>"$fx/race.stderr" &
  race_pid=$!
  race_state=
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    race_state=$(ps -o stat= -p "$race_pid" 2>/dev/null | tr -d '[:space:]')
    [[ $race_state == T* ]] && break
    sleep 0.05
  done
  [[ $race_state == T* ]] || fail 'materializer race probe did not stop after output FD open'
  mv -- "$race_dir" "$race_bound"
  ln -s "$race_outside" "$race_dir"
  kill -CONT "$race_pid"
  wait "$race_pid"
  assert_eq 3 "$(find "$race_bound" -mindepth 1 -maxdepth 1 -type f | wc -l)" \
    'FD-bound race output count'
  assert_eq 0 "$(find "$race_outside" -mindepth 1 -maxdepth 1 | wc -l)" \
    'symlink race outside write count'

  (umask 077; "$materialize_source" "$render_dir") >"$fx/materialize.out"
  rendered_names=$(find "$render_dir" -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort)
  assert_eq $'codebase-memory-workspace-sync.service\ncodebase-memory-workspace-sync.timer\nworkspace-sync.env' \
    "$rendered_names" 'rendered native filenames'

  cat >"$expected_dir/workspace-sync.env" <<'EOF'
# Install as /etc/codebase-memory/workspace-sync.env only after review and test.
CBM_ACTIVE_TASKS_DIR=/root/.openclaw/workspaces/engineering-team/tasks/active
CBM_ALLOWED_ROOT=/root/.codespace/workspace
CBM_MANAGED_ROOT=/root/.codespace/workspace/.cbm-task-worktrees
CBM_CACHE_DIR=/var/lib/codebase-memory-mcp/cache
CBM_MEM_BUDGET_MB=512
CBM_WORKERS=1
CBM_STATE_DIR=/var/lib/codebase-memory-workspace-sync
CBM_SYNC_LOCK=/run/codebase-memory-workspace-sync.lock
CBM_CALL_LOCK=/run/codebase-memory-mcp.call.lock
CBM_MCPORTER_CONFIG=/root/.mcporter/mcporter.json
CBM_CLIENT_CONFIGS=/root/.openclaw/agents/john/agent/codex-home/config.toml:/root/.openclaw/agents/jucy/agent/codex-home/config.toml:/root/.openclaw/agents/mus/agent/codex-home/config.toml
CBM_MCP_SERVER=codebase-memory-mcp
CBM_MCP_WRAPPER=/root/.codespace/workspace/codebase-memory/bin/mcp_call_locked.sh
CBM_MCP_BINARY=/usr/local/bin/codebase-memory-mcp
EOF

  cat >"$expected_dir/codebase-memory-workspace-sync.service" <<'EOF'
[Unit]
Description=Bind Codebase Memory indexes to exact active engineering task Heads
Documentation=file:///root/.codespace/workspace/codebase-memory/README.md
After=local-fs.target
ConditionPathExists=/root/.codespace/workspace/codebase-memory/bin/workspace_sync.sh

[Service]
Type=oneshot
User=root
Group=root
EnvironmentFile=/etc/codebase-memory/workspace-sync.env
ExecStart=/root/.codespace/workspace/codebase-memory/bin/workspace_sync.sh
UMask=0077
Nice=10
IOSchedulingClass=idle
NoNewPrivileges=yes
PrivateDevices=yes
PrivateTmp=yes
ProtectClock=yes
ProtectControlGroups=yes
ProtectKernelLogs=yes
ProtectKernelModules=yes
ProtectKernelTunables=yes
ProtectSystem=strict
ProtectHome=read-only
RestrictAddressFamilies=AF_UNIX
RestrictNamespaces=yes
LockPersonality=yes
MemoryDenyWriteExecute=no
ReadOnlyPaths=/root/.openclaw/workspaces/engineering-team/tasks
ReadWritePaths=/root/.codespace/workspace/.git
ReadWritePaths=/root/.codespace/workspace/.cbm-task-worktrees
ReadWritePaths=/var/lib/codebase-memory-mcp/cache
ReadWritePaths=/var/lib/codebase-memory-workspace-sync
ReadWritePaths=/run
TimeoutStartSec=45min

[Install]
WantedBy=multi-user.target
EOF

  cat >"$expected_dir/codebase-memory-workspace-sync.timer" <<'EOF'
[Unit]
Description=Periodic exact-Head Codebase Memory task reconciliation

[Timer]
OnBootSec=15min
OnUnitActiveSec=15min
RandomizedDelaySec=60s
AccuracySec=30s
Persistent=false
Unit=codebase-memory-workspace-sync.service

[Install]
WantedBy=timers.target
EOF

  cmp -s "$expected_dir/workspace-sync.env" "$render_dir/workspace-sync.env" ||
    fail 'rendered environment bytes differ from fixture'
  cmp -s "$expected_dir/codebase-memory-workspace-sync.service" \
    "$render_dir/codebase-memory-workspace-sync.service" ||
    fail 'rendered service bytes differ from fixture'
  cmp -s "$expected_dir/codebase-memory-workspace-sync.timer" \
    "$render_dir/codebase-memory-workspace-sync.timer" ||
    fail 'rendered timer bytes differ from fixture'
  assert_eq 600 "$(stat -c '%a' "$render_dir/workspace-sync.env")" 'rendered environment mode'
  assert_eq 644 "$(stat -c '%a' "$render_dir/codebase-memory-workspace-sync.service")" 'rendered service mode'
  assert_eq 644 "$(stat -c '%a' "$render_dir/codebase-memory-workspace-sync.timer")" 'rendered timer mode'

  if command -v systemd-analyze >/dev/null; then
    verify_root="$fx/systemd-root"
    verify_output="$fx/systemd-verify.out"
    install -d -m 0755 -- \
      "$verify_root/etc/codebase-memory" \
      "$verify_root/etc/systemd/system" \
      "$verify_root/root/.codespace/workspace/codebase-memory/bin"
    install -m 0755 "$sync_source" \
      "$verify_root/root/.codespace/workspace/codebase-memory/bin/workspace_sync.sh"
    install -m 0600 "$render_dir/workspace-sync.env" \
      "$verify_root/etc/codebase-memory/workspace-sync.env"
    install -m 0644 \
      "$render_dir/codebase-memory-workspace-sync.service" \
      "$render_dir/codebase-memory-workspace-sync.timer" \
      "$verify_root/etc/systemd/system/"
    if ! systemd-analyze --root="$verify_root" --recursive-errors=no verify \
      codebase-memory-workspace-sync.service \
      codebase-memory-workspace-sync.timer \
      >"$verify_output" 2>&1; then
      fail 'systemd-analyze rejected rendered task units'
    fi
    if grep -F 'codebase-memory-workspace-sync' "$verify_output" >/dev/null; then
      fail 'systemd-analyze reported a rendered task-unit warning'
    fi
  fi
}

ORIGINAL_PATH=$PATH
for required in awk cmp find flock git jq ps python3 realpath sha256sum stat; do
  command -v "$required" >/dev/null || fail "missing test dependency: $required"
done

run_test 'active records only; disposable checkouts excluded' test_active_archive_and_disposable_exclusion
run_test 'locked-path traversal rejected' test_path_traversal_rejected
run_test 'symlinked declared worktree rejected' test_symlink_worktree_rejected
run_test 'active interactive caller skips all MCP work' test_active_caller_skips_all_mcp_work
run_test 'active caller still rejects locked-entry drift' test_active_caller_rejects_locked_entry_drift
run_test 'in-stage Head drift still applies active-caller skip' test_active_caller_with_in_stage_head_drift_skips
run_test 'cache/root/worker profile mismatch fails before indexing' test_profile_mismatch_fails_before_index
run_test 'direct client launch bypassing shared lock is rejected' test_direct_client_bypass_rejected
run_test 'shared locked entry serializes real MCP process lifetimes' test_locked_entry_serializes_real_processes
run_test 'declared exact-Head drift fails before indexing' test_exact_head_drift_rejected
run_test 'untracked state before indexing blocks MCP work' test_untracked_state_before_index_rejected
run_test 'tracked drift after indexing blocks receipt' test_tracked_drift_after_index_rejected
run_test 'untracked drift before receipt blocks receipt' test_untracked_drift_before_receipt_rejected
run_test 'zero-node graph fails closed' test_zero_node_scope_fails_closed
run_test 'locked-path coverage omission fails closed' test_locked_path_omission_fails_closed
run_test 'exact mirror, durable receipt, and manifest rollback' test_exact_mirror_receipt_and_rollback
run_test 'failed run preserves and executes bounded rollback' test_failed_run_manifest_rolls_back_partial_state
run_test 'runtime files ignore restrictive umask and verify native units' test_runtime_materialization_verifies

printf 'PASS: %s tests\n' "$tests_run"
