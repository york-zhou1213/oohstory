#!/usr/bin/env bash
set -euo pipefail

readonly EX_USAGE=64
readonly EX_CANTCREAT=73

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_root=$(cd -- "$script_dir/../.." && pwd -P)
readonly STAGING_ROOT="$repo_root/.cbm-runtime-staging"

usage() {
  printf 'Usage: %s OUTPUT_DIR\n' "$0"
}

if (($# != 1)); then
  usage >&2
  exit "$EX_USAGE"
fi

command -v python3 >/dev/null || {
  printf 'refusing to materialize runtime files: missing required command: python3\n' >&2
  exit "$EX_CANTCREAT"
}

exec python3 - "$STAGING_ROOT" "$1" <<'PY'
import errno
import os
import signal
import stat
import sys

EX_NOINPUT = 66
EX_CANTCREAT = 73

staging_root, output_path = sys.argv[1:]


def fail(message, status=EX_CANTCREAT):
    print(f"refusing to materialize runtime files: {message}", file=sys.stderr)
    raise SystemExit(status)


def path_parts(path):
    if not os.path.isabs(path):
        fail(f"path is not absolute: {path}")
    normalized = os.path.normpath(path)
    if normalized != path.rstrip("/") or normalized == "/":
        fail(f"path is not normalized: {path}")
    return [part for part in normalized.split("/") if part]


def open_directory_beneath(root_fd, parts, display_path):
    current_fd = os.dup(root_fd)
    try:
        for part in parts:
            try:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current_fd,
                )
            except FileNotFoundError:
                fail(f"output directory is missing: {display_path}", EX_NOINPUT)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    fail(f"symlink or non-directory path is forbidden: {display_path}")
                raise
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


root_parts = path_parts(staging_root)
output_parts = path_parts(output_path)
if output_parts[: len(root_parts)] != root_parts or len(output_parts) == len(root_parts):
    fail(f"output directory is outside staging root: {output_path}")

slash_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    staging_fd = open_directory_beneath(slash_fd, root_parts, staging_root)
finally:
    os.close(slash_fd)

try:
    output_fd = open_directory_beneath(
        staging_fd,
        output_parts[len(root_parts) :],
        output_path,
    )
finally:
    os.close(staging_fd)

if os.environ.get("CBM_TEST_STOP_AFTER_OUTPUT_OPEN") == "1":
    os.kill(os.getpid(), signal.SIGSTOP)

files = {
    "workspace-sync.env": (
        0o600,
        b"""# Install as /etc/codebase-memory/workspace-sync.env only after review and test.
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
""",
    ),
    "codebase-memory-workspace-sync.service": (
        0o644,
        b"""[Unit]
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
""",
    ),
    "codebase-memory-workspace-sync.timer": (
        0o644,
        b"""[Unit]
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
""",
    ),
}

temporary_names = []
try:
    for target_name in files:
        try:
            target_stat = os.stat(target_name, dir_fd=output_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(target_stat.st_mode):
            fail(f"symlink target is forbidden: {output_path}/{target_name}")
        if not stat.S_ISREG(target_stat.st_mode):
            fail(f"non-regular target is forbidden: {output_path}/{target_name}")

    for index, (target_name, (mode, content)) in enumerate(files.items()):
        temporary_name = f".{target_name}.{os.getpid()}.{index}.tmp"
        try:
            temp_fd = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                mode,
                dir_fd=output_fd,
            )
        except OSError as exc:
            fail(f"cannot create staged file {target_name}: {exc.strerror}")
        temporary_names.append(temporary_name)
        with os.fdopen(temp_fd, "wb", closefd=True) as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    for target_name, temporary_name in zip(files, list(temporary_names)):
        try:
            target_stat = os.stat(target_name, dir_fd=output_fd, follow_symlinks=False)
        except FileNotFoundError:
            target_stat = None
        if target_stat is not None and (
            stat.S_ISLNK(target_stat.st_mode) or not stat.S_ISREG(target_stat.st_mode)
        ):
            fail(f"unsafe target appeared before publish: {output_path}/{target_name}")
        os.replace(
            temporary_name,
            target_name,
            src_dir_fd=output_fd,
            dst_dir_fd=output_fd,
        )
        temporary_names.remove(temporary_name)
    os.fsync(output_fd)
finally:
    for temporary_name in temporary_names:
        try:
            os.unlink(temporary_name, dir_fd=output_fd)
        except FileNotFoundError:
            pass
    os.close(output_fd)

print(f"materialized runtime files: output={output_path}")
PY
