# Task-scoped Codebase Memory sync

This source binds Codebase Memory projects to canonical engineering task records instead of filesystem globs. It never treats disposable review/test checkout names as authority.

## Contract

- `active_task_worktrees.sh` reads only `tasks/active/TASK-*.md`, validates the frontmatter identity, lifecycle state, Git repository, Base/Head ancestry, declared MCP binding, and locked paths, then emits one deterministic JSON manifest.
- Managed worktrees are named `<TASK-ID>-<owner>` below `/root/.codespace/workspace/.cbm-task-worktrees/`. An outside-root declared worktree is evidence for the immutable Head; the sync creates a detached managed mirror rather than broadening the allowed root.
- `workspace_sync.sh` exits successfully without any MCP call while any active record is `IMPLEMENTING`, `FIX_REQUIRED`, or `TESTING`. Its scheduler lock prevents overlapping reconciliation runs.
- `mcp_call_locked.sh` is the mandatory command for mcporter and every John/Jucy/Mus task client. It holds the shared call lock for the complete `codebase-memory-mcp` process lifetime, so interactive and maintenance requests cannot overlap.
- `materialize_runtime_files.sh OUTPUT_DIR` is the sole canonical source for `workspace-sync.env` and the service/timer units. `OUTPUT_DIR` must already exist below `/root/.codespace/workspace/.cbm-runtime-staging`; every directory is opened with no-follow semantics and all writes remain relative to the retained output-directory FD.
- The maintenance mcporter profile and the John/Jucy/Mus task-client profiles must all use the locked entry and match its call lock and server binary plus the reviewed cache, allowed root, 512 MiB memory budget, and one-worker profile. Any mismatch fails before indexing.
- A successful receipt binds task ID, deterministic project ID, a clean managed worktree before/after indexing and before receipt, exact Base/Head, non-zero graph, every tracked file resolved by the locked scope, the exact Base-to-Head changed-file set, and a non-truncated inbound impact result. It embeds canonically hashed status, coverage, and impact payloads, including shown/total/truncated fields.

## Source-only verification

Run the fixture matrix without touching live MCP state:

```bash
tests/codebase-memory/test_workspace_sync.sh
```

Read-only manifest discovery is also safe:

```bash
codebase-memory/bin/workspace_sync.sh --discover | jq .
```

Render deployment inputs for review in a disposable directory:

```bash
install -d -m 0700 /root/.codespace/workspace/.cbm-runtime-staging
output_dir=$(mktemp -d /root/.codespace/workspace/.cbm-runtime-staging/review.XXXXXX)
codebase-memory/bin/materialize_runtime_files.sh "$output_dir"
```

Do not run the live sync, edit MCP/client configuration, or install/enable the timer during implementation, review, or test. Ken owns activation after the exact source commit passes Bob and Mus.

## Activation and rollback

1. Materialize the reviewed environment and unit bytes into a new non-live staging directory; verify their hashes and modes before installing them into their separately backed-up live destinations.
2. Confirm no task is in `IMPLEMENTING`, `FIX_REQUIRED`, or `TESTING` and no task caller owns the shared call lock.
3. Reconcile mcporter plus all three task-client profiles to the exact values in the rendered `workspace-sync.env`.
4. Run the oneshot once. Preserve its `run-*.json`, receipt paths, and hashes as activation evidence.
5. Enable the timer only after exact-Head coverage passes.

Rollback uses only a sync-created manifest:

```bash
codebase-memory/bin/workspace_sync.sh --rollback /var/lib/codebase-memory-workspace-sync/run-<id>.json
```

Rollback deletes only projects and clean managed worktrees recorded as created by that manifest. It rejects manifests outside the state directory, symlinks, dirty worktrees, wrong repository identity, and Head drift. Restore any prior timer/config backup only after the call lock is free.
