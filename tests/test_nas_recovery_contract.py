from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECOVERY_SCRIPT = ROOT / "deploy" / "oohstory-nas-recovery"
RECOVERY_SERVICE = ROOT / "deploy" / "oohstory-nas-recovery.service"
RECOVERY_TIMER = ROOT / "deploy" / "oohstory-nas-recovery.timer"
FSTAB_TEMPLATE = ROOT / "deploy" / "oohstory-nas-fstab.conf"
ENROLL_CLIENT = ROOT / "deploy" / "oohstory-nas-enroll-client"
ENROLL_WRAPPER = ROOT / "deploy" / "oohstory-nas-enroll-ssh-wrapper"
NAS_ENSURE_CLIENT = ROOT / "deploy" / "oohstory-nas-ensure-client"
NAS_SUDOERS = ROOT / "deploy" / "oohstory-nas-enroll.sudoers"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _fake_commands(tmp_path: Path) -> dict[str, str]:
    systemctl = tmp_path / "systemctl"
    mountpoint = tmp_path / "mountpoint"
    findmnt = tmp_path / "findmnt"
    enroll = tmp_path / "enroll"
    state = tmp_path / "state"
    state.mkdir()

    _write_executable(
        systemctl,
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

state = Path(os.environ["FAKE_STATE"])
args = sys.argv[1:]
with (state / "calls.log").open("a", encoding="utf-8") as handle:
    handle.write(" ".join(args) + "\\n")

if args[:2] == ["start", r"mnt-nas\\x2delectronic\\x2dlibrary\\x2ddata.mount"]:
    if os.environ.get("FAKE_MOUNT_RESULT", "success") != "success":
        raise SystemExit(1)
    (state / "mounted").touch()
elif args[:2] == ["is-enabled", "--quiet"]:
    enabled = set(os.environ.get("FAKE_ENABLED", "oohstory-reader.service,oohstory-admin.service").split(","))
    raise SystemExit(0 if args[2] in enabled else 1)
elif args[:2] == ["is-active", "--quiet"]:
    active = set((state / "active").read_text(encoding="utf-8").splitlines()) if (state / "active").exists() else set()
    raise SystemExit(0 if args[2] in active else 3)
elif args[0] == "start" and args[1].startswith("oohstory-"):
    active_path = state / "active"
    active = set(active_path.read_text(encoding="utf-8").splitlines()) if active_path.exists() else set()
    active.add(args[1])
    active_path.write_text("\\n".join(sorted(active)) + "\\n", encoding="utf-8")
""",
    )
    _write_executable(
        enroll,
        """#!/usr/bin/env python3
import os
from pathlib import Path

state = Path(os.environ["FAKE_STATE"])
with (state / "calls.log").open("a", encoding="utf-8") as handle:
    handle.write("enroll\\n")
raise SystemExit(0 if os.environ.get("FAKE_ENROLL_RESULT", "success") == "success" else 1)
""",
    )
    _write_executable(
        mountpoint,
        """#!/usr/bin/env python3
import os
from pathlib import Path
raise SystemExit(0 if (Path(os.environ["FAKE_STATE"]) / "mounted").exists() else 1)
""",
    )
    _write_executable(
        findmnt,
        """#!/usr/bin/env python3
import os
import sys
if sys.argv[-1] == "SOURCE":
    print(os.environ.get("FAKE_SOURCE", "192.0.2.20:/export/oohstory-library"))
elif sys.argv[-1] == "FSTYPE":
    print(os.environ.get("FAKE_FSTYPE", "nfs4"))
else:
    raise SystemExit(2)
""",
    )
    return {
        "OOHSTORY_NAS_RECOVERY_SYSTEMCTL": str(systemctl),
        "OOHSTORY_NAS_RECOVERY_MOUNTPOINT": str(mountpoint),
        "OOHSTORY_NAS_RECOVERY_FINDMNT": str(findmnt),
        "OOHSTORY_NAS_RECOVERY_ENROLL_CLIENT": str(enroll),
        "OOHSTORY_NAS_RECOVERY_LOCK_FILE": str(tmp_path / "recovery.lock"),
        "FAKE_STATE": str(state),
    }


def _run(tmp_path: Path, **overrides: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    env = os.environ.copy()
    env.update(_fake_commands(tmp_path))
    env.update(overrides)
    result = subprocess.run(
        ["/usr/bin/bash", str(RECOVERY_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    calls = (tmp_path / "state" / "calls.log").read_text(encoding="utf-8").splitlines()
    return result, calls


def test_recovery_starts_enabled_services_after_verified_mount(tmp_path: Path) -> None:
    result, calls = _run(tmp_path)

    assert result.returncode == 0
    assert not any("automount" in call for call in calls)
    assert calls.index("enroll") < calls.index(r"start mnt-nas\x2delectronic\x2dlibrary\x2ddata.mount")
    assert "start oohstory-reader.service" in calls
    assert "start oohstory-admin.service" in calls


def test_mount_failure_never_starts_oohstory(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, FAKE_MOUNT_RESULT="failed")

    assert result.returncode == 0
    assert "later timer run will retry" in result.stdout
    assert not any(call == "start oohstory-reader.service" for call in calls)
    assert not any(call == "start oohstory-admin.service" for call in calls)


def test_enrollment_failure_still_tries_existing_nfs_rules(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, FAKE_ENROLL_RESULT="failed")

    assert result.returncode == 0
    assert "attempting the existing NFS rules" in result.stdout
    assert r"start mnt-nas\x2delectronic\x2dlibrary\x2ddata.mount" in calls
    assert "start oohstory-reader.service" in calls
    assert "start oohstory-admin.service" in calls


def test_wrong_mount_identity_never_starts_oohstory(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, FAKE_SOURCE="server:/unexpected")

    assert result.returncode == 0
    assert "unexpected mount identity" in result.stdout
    assert not any(call == "start oohstory-reader.service" for call in calls)
    assert not any(call == "start oohstory-admin.service" for call in calls)


def test_repeated_recovery_is_idempotent(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.update(_fake_commands(tmp_path))
    for _ in range(2):
        result = subprocess.run(
            ["/usr/bin/bash", str(RECOVERY_SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0

    calls = (tmp_path / "state" / "calls.log").read_text(encoding="utf-8").splitlines()
    assert calls.count("start oohstory-reader.service") == 1
    assert calls.count("start oohstory-admin.service") == 1
    assert calls.count("enroll") == 1


def test_disabled_service_is_not_resurrected(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, FAKE_ENABLED="oohstory-reader.service")

    assert result.returncode == 0
    assert "start oohstory-reader.service" in calls
    assert "start oohstory-admin.service" not in calls
    assert "respecting operator intent" in result.stdout


def test_systemd_contract_has_bounded_retry_and_hardening() -> None:
    service = RECOVERY_SERVICE.read_text(encoding="utf-8")
    timer = RECOVERY_TIMER.read_text(encoding="utf-8")

    assert "ExecStart=/usr/local/libexec/oohstory-nas-recovery" in service
    assert "TimeoutStartSec=50" in service
    assert "NoNewPrivileges=true" in service
    assert "RestrictAddressFamilies=AF_UNIX" in service
    assert "RestrictAddressFamilies=AF_UNIX AF_INET" in service
    assert "IPAddressDeny=any" in service
    assert "IPAddressAllow=192.0.2.20/32" in service
    assert "OnBootSec=20s" in timer
    assert "OnUnitInactiveSec=30s" in timer
    assert "Persistent=true" in timer
    assert "WantedBy=timers.target" in timer


def test_fstab_contract_is_eager_and_nonblocking() -> None:
    fstab = FSTAB_TEMPLATE.read_text(encoding="utf-8")
    data_lines = [line for line in fstab.splitlines() if line and not line.startswith("#")]

    assert len(data_lines) == 1
    fields = data_lines[0].split()
    assert fields[:3] == [
        "192.0.2.20:/export/oohstory-library",
        "/srv/oohstory/library",
        "nfs4",
    ]
    options = set(fields[3].split(","))
    assert {"_netdev", "nofail", "hard", "x-systemd.mount-timeout=30s"} <= options
    assert "x-systemd.automount" not in options


def test_nas_enrollment_contract_is_credential_free_and_restricted() -> None:
    client = ENROLL_CLIENT.read_text(encoding="utf-8")
    wrapper = ENROLL_WRAPPER.read_text(encoding="utf-8")
    helper = NAS_ENSURE_CLIENT.read_text(encoding="utf-8")
    sudoers = NAS_SUDOERS.read_text(encoding="utf-8")

    assert "192.0.2.20" in client
    assert 'NAS_PORT="22"' in client
    assert "BatchMode=yes" in client
    assert "StrictHostKeyChecking=yes" in client
    assert "IdentitiesOnly=yes" in client
    assert "password" not in client.lower()
    assert 'SSH_ORIGINAL_COMMAND:-}" != "$EXPECTED_COMMAND"' in wrapper
    assert "SSH_CONNECTION" in wrapper
    assert "/usr/local/sbin/oohstory-nas-ensure-client" in wrapper
    assert 'SHARE_NAME="electronic-library-data"' in helper
    assert "SYNO.Core.FileServ.NFS.SharePrivilege" in helper
    assert "method=load" in helper
    assert "method=save" in helper
    assert "192\\.0\\.2|198\\.51\\.100|203\\.0\\.113" in helper
    assert "/etc/exports" not in helper
    assert sudoers.strip() == (
        "oohstory-nas ALL=(root) NOPASSWD: /usr/local/sbin/oohstory-nas-ensure-client *"
    )


def test_nas_helper_adds_exact_peer_once_and_preserves_existing_rules(tmp_path: Path) -> None:
    state_path = tmp_path / "rules.json"
    state_path.write_text(
        json.dumps(
            [
                {
                    "client": "192.0.2.1",
                    "privilege": "rw",
                    "root_squash": "admin",
                    "async": True,
                    "insecure": True,
                    "crossmnt": True,
                    "security_flavor": {"sys": True},
                }
            ]
        ),
        encoding="utf-8",
    )
    webapi = tmp_path / "synowebapi"
    _write_executable(
        webapi,
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state = Path(os.environ["FAKE_NFS_STATE"])
params = dict(arg.split("=", 1) for arg in sys.argv[1:] if "=" in arg)
if params.get("method") == "load":
    print(json.dumps({"success": True, "data": {"rule": json.loads(state.read_text())}}))
elif params.get("method") == "save":
    state.write_text(json.dumps(json.loads(params["rule"])), encoding="utf-8")
    print(json.dumps({"success": True}))
else:
    raise SystemExit(2)
""",
    )
    env = os.environ.copy()
    env.update(
        {
            "FAKE_NFS_STATE": str(state_path),
            "OOHSTORY_NAS_ENROLL_WEBAPI": str(webapi),
            "OOHSTORY_NAS_ENROLL_JQ": "/usr/bin/jq",
            "OOHSTORY_NAS_ENROLL_FLOCK": "/usr/bin/flock",
            "OOHSTORY_NAS_ENROLL_LOCK_FILE": str(tmp_path / "enroll.lock"),
        }
    )

    for _ in range(2):
        result = subprocess.run(
            ["/usr/bin/bash", str(NAS_ENSURE_CLIENT), "192.0.2.42"],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr

    rules = json.loads(state_path.read_text(encoding="utf-8"))
    assert [rule["client"] for rule in rules] == ["192.0.2.1", "192.0.2.42"]


def test_nas_helper_rejects_unapproved_peer_before_loading_rules(tmp_path: Path) -> None:
    result = subprocess.run(
        ["/usr/bin/bash", str(NAS_ENSURE_CLIENT), "8.8.8.8"],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "OOHSTORY_NAS_ENROLL_WEBAPI": "/bin/false",
            "OOHSTORY_NAS_ENROLL_JQ": "/usr/bin/jq",
            "OOHSTORY_NAS_ENROLL_FLOCK": "/usr/bin/flock",
            "OOHSTORY_NAS_ENROLL_LOCK_FILE": str(tmp_path / "enroll.lock"),
        },
    )

    assert result.returncode == 65
    assert "outside the approved server networks" in result.stderr
