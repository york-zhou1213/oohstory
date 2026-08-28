from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import learning_control_plane.release_adapter as release_adapter_module
from learning_control_plane.common import ControlPlaneError
from learning_control_plane.release_adapter import (
    activate_release,
    install_adapter,
    load_runtime_contract,
    rollback_adapter,
    rollback_release,
    verify_live_consumer,
)
from .support import add_task, prepare_root


PROJECT = Path(__file__).parents[1]


class ReleaseAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.team = self.base / "team"
        self.runtime = self.team / "runtime" / "learning-control-plane"
        self.consumer = self.team / "scripts" / "learning_loop.py"
        self.consumer.parent.mkdir(parents=True)
        self.runtime.mkdir(parents=True)
        self.consumer.write_text(
            "#!/usr/bin/env python3\nimport sys\nprint('legacy:' + sys.argv[1])\n",
            encoding="utf-8",
        )
        self.consumer.chmod(0o755)
        self.contract = self.base / "runtime-contract.json"
        self.contract.write_text(json.dumps({
            "schema_version": 1,
            "adapter_version": 1,
            "canonical_team_root": str(self.team),
            "canonical_runtime_root": str(self.runtime),
            "canonical_consumer": str(self.consumer),
            "releases_dir": "releases",
            "active_link": "active",
            "legacy_target": "compat/v1/learning_loop.py",
            "release_entrypoint": "scripts/learning_control_plane.py",
            "adapter_source": "scripts/learning_loop_adapter.py",
            "legacy_commands": ["bootstrap", "preflight", "close", "metrics", "stale"],
            "release_commands": ["audit-task", "audit-system"],
        }, indent=2) + "\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def stage_release(self, release_id: str, marker: str):
        source = self.base / f"source-{release_id}"
        scripts = source / "scripts"
        scripts.mkdir(parents=True)
        entrypoint = scripts / "learning_control_plane.py"
        entrypoint.write_text(
            "#!/usr/bin/env python3\nimport sys\nprint(%r + ':' + sys.argv[1])\n" % marker,
            encoding="utf-8",
        )
        adapter = scripts / "learning_loop_adapter.py"
        shutil.copyfile(PROJECT / "scripts" / "learning_loop_adapter.py", adapter)
        manifest = self.base / f"manifest-{release_id}.json"
        files = []
        for name in ("scripts/learning_control_plane.py", "scripts/learning_loop_adapter.py"):
            digest = hashlib.sha256((source / name).read_bytes()).hexdigest()
            files.append({"source": name, "runtime": name, "sha256": digest})
        manifest.write_text(json.dumps({"schema_version": 1, "files": files}), encoding="utf-8")
        release = self.runtime / "releases" / release_id
        for item in files:
            target = release / item["runtime"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / item["source"], target)
        return source, manifest, release

    def prepare_release(self, release_id: str, revision: str, marker: str):
        source, manifest, release = self.stage_release(release_id, marker)
        receipt = self.base / f"activate-{release_id}.json"
        activate_release(self.contract, manifest, source, release, revision, receipt)
        return source, manifest, release, receipt

    def run_consumer(self, command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.consumer), command],
            check=False, capture_output=True, text=True, timeout=10,
        )

    def run_consumer_args(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.pop("PYTHONDONTWRITEBYTECODE", None)
        environment.pop("PYTHONPYCACHEPREFIX", None)
        return subprocess.run(
            [sys.executable, str(self.consumer), *arguments],
            check=False, capture_output=True, text=True, timeout=10,
            env=environment,
        )

    @staticmethod
    def write_json(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def assert_install_reenters_after(self, field: str, *, json_boundary: bool = False) -> None:
        source, manifest, release, _activation = self.prepare_release(
            "release-a", "a" * 40, "release-a")
        receipt = self.base / f"adapter-{field.replace(' ', '-')}.json"
        helper_name = "_atomic_json_at" if json_boundary else "_atomic_write_at"
        original = getattr(release_adapter_module, helper_name)
        failed = False

        def fail_after(*args, **kwargs):
            nonlocal failed
            result = original(*args, **kwargs)
            if not failed and kwargs.get("field") == field:
                failed = True
                raise OSError(f"injected failure after {field}")
            return result

        with mock.patch.object(release_adapter_module, helper_name, side_effect=fail_after):
            with self.assertRaisesRegex(OSError, "injected failure"):
                install_adapter(
                    self.contract, release / "scripts" / "learning_loop_adapter.py", receipt)
        self.assertTrue(failed)
        recovered = install_adapter(
            self.contract, release / "scripts" / "learning_loop_adapter.py", receipt)
        self.assertTrue(recovered["ok"])
        self.assertTrue(verify_live_consumer(self.contract, manifest, source)["ok"])

    def assert_rollback_reenters_after(self, field: str, *, unlink_boundary: bool) -> None:
        _source, _manifest, release, _activation = self.prepare_release(
            "release-a", "a" * 40, "release-a")
        receipt = self.base / f"adapter-{field.replace(' ', '-')}.json"
        adapter = release / "scripts" / "learning_loop_adapter.py"
        original_consumer = self.consumer.read_bytes()
        install_adapter(self.contract, adapter, receipt)
        helper_name = "_unlink_at" if unlink_boundary else "_atomic_write_at"
        original = getattr(release_adapter_module, helper_name)
        failed = False

        def fail_after(*args, **kwargs):
            nonlocal failed
            result = original(*args, **kwargs)
            observed_field = args[2] if unlink_boundary else kwargs.get("field")
            if not failed and observed_field == field:
                failed = True
                raise OSError(f"injected failure after {field}")
            return result

        with mock.patch.object(release_adapter_module, helper_name, side_effect=fail_after):
            with self.assertRaisesRegex(OSError, "injected failure"):
                rollback_adapter(self.contract, receipt)
        self.assertTrue(failed)
        self.assertTrue(rollback_adapter(self.contract, receipt)["ok"])
        self.assertEqual(self.consumer.read_bytes(), original_consumer)
        redeploy_receipt = self.base / f"redeploy-{field.replace(' ', '-')}.json"
        self.assertTrue(install_adapter(self.contract, adapter, redeploy_receipt)["ok"])

    def test_adapter_preserves_legacy_commands_and_routes_audits_to_active_release(self) -> None:
        source, manifest, release, activation = self.prepare_release("release-a", "a" * 40, "release-a")
        adapter_receipt = self.base / "adapter.json"
        original = self.consumer.read_bytes()
        install_adapter(self.contract, release / "scripts" / "learning_loop_adapter.py", adapter_receipt)

        for command in ("bootstrap", "preflight", "close", "metrics", "stale"):
            completed = self.run_consumer(command)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), f"legacy:{command}")
        for command in ("audit-task", "audit-system"):
            completed = self.run_consumer(command)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), f"release-a:{command}")

        verified = verify_live_consumer(self.contract, manifest, source)
        self.assertTrue(verified["ok"], verified)
        self.assertEqual(verified["source_revision"], "a" * 40)
        self.assertEqual(verified["inspection"]["active_target"], "releases/release-a")

        self.assertFalse(rollback_adapter(self.contract, adapter_receipt)["idempotent"])
        self.assertEqual(self.consumer.read_bytes(), original)
        self.assertFalse(rollback_release(self.contract, activation)["idempotent"])
        self.assertFalse((self.runtime / "active").exists())

    def test_real_release_upgrades_legacy_close_and_passes_authoritative_audit(self) -> None:
        prepare_root(self.base)
        add_task(self.team, "TASK-E2E", requirements=("john:implementation",))
        self.consumer.write_text(
            """#!/usr/bin/env python3
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("command", choices=("preflight", "close"))
parser.add_argument("--team-root", required=True)
parser.add_argument("--task", required=True)
parser.add_argument("--agent", required=True)
parser.add_argument("--stage", required=True)
parser.add_argument("--query")
parser.add_argument("--consult", action="append")
parser.add_argument("--outcome")
parser.add_argument("--summary")
args = parser.parse_args()
path = Path(args.team_root) / "team-learnings" / "receipts" / args.task / f"{args.agent}-{args.stage}.json"
path.parent.mkdir(parents=True, exist_ok=True)
if args.command == "preflight":
    receipt = {"schema_version": 1, "task_id": args.task, "agent": args.agent,
        "stage": args.stage, "status": "open",
        "opened_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "query": args.query, "consulted_lessons": args.consult or []}
else:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if receipt.get("status") != "closed":
        receipt.update({"status": "closed",
            "closed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "outcome": args.outcome, "summary": args.summary})
path.write_text(json.dumps(receipt, indent=2) + "\\n", encoding="utf-8")
print(json.dumps(receipt))
""",
            encoding="utf-8",
        )
        self.consumer.chmod(0o755)

        manifest = PROJECT / "release" / "deployment-manifest.json"
        release = self.runtime / "releases" / "release-real"
        for item in json.loads(manifest.read_text(encoding="utf-8"))["files"]:
            target = release / item["runtime"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PROJECT / item["source"], target)
        activation = self.base / "activate-real.json"
        activate_release(
            self.contract, manifest, PROJECT, release, "c" * 40, activation)
        install_adapter(
            self.contract, release / "scripts" / "learning_loop_adapter.py",
            self.base / "adapter-real.json")

        preflight = self.run_consumer_args(
            "preflight", "--team-root", str(self.team), "--task", "TASK-E2E",
            "--agent", "john", "--stage", "implementation", "--query",
            "TASK-E2E backend implementation", "--consult",
            "john/learnings/LEARNINGS.md")
        self.assertEqual(preflight.returncode, 0, preflight.stderr)
        close_arguments = (
            "close", "--team-root", str(self.team), "--task", "TASK-E2E",
            "--agent", "john", "--stage", "implementation", "--outcome",
            "no_new_learning", "--summary", "fixture closure")
        missing_memory = self.run_consumer_args(*close_arguments)
        self.assertEqual(missing_memory.returncode, 2)
        self.assertFalse(list(release.rglob("__pycache__")))
        legacy_receipt = (self.team / "team-learnings" / "receipts" /
                          "TASK-E2E" / "john-implementation.json")
        self.assertEqual(json.loads(legacy_receipt.read_text())["schema_version"], 1)
        day = datetime.now(timezone.utc).date().isoformat()
        (self.team / "john" / "memory" / f"{day}.md").write_text(
            "# Daily memory\n\nTASK-E2E exact retrieval evidence.\n", encoding="utf-8")
        close = self.run_consumer_args(*close_arguments)
        self.assertEqual(close.returncode, 0, close.stderr)
        receipt = json.loads(close.stdout)
        self.assertEqual(receipt["schema_version"], 2)
        self.assertTrue(receipt["closure_evidence"]["retrieval_evidence"]["exact_retrieval"])
        self.assertFalse(list(release.rglob("__pycache__")))

        audited = self.run_consumer_args(
            "audit-task", "--team-root", str(self.team), "--task", "TASK-E2E",
            "--require", "john:implementation")
        self.assertEqual(audited.returncode, 0, audited.stderr)
        self.assertTrue(json.loads(audited.stdout)["ok"])
        self.assertTrue(verify_live_consumer(self.contract, manifest, PROJECT)["ok"])
        audited_again = self.run_consumer_args(
            "audit-task", "--team-root", str(self.team), "--task", "TASK-E2E",
            "--require", "john:implementation")
        self.assertEqual(audited_again.returncode, 0, audited_again.stderr)
        self.assertTrue(json.loads(audited_again.stdout)["ok"])
        self.assertFalse(list(release.rglob("__pycache__")))
        self.assertTrue(verify_live_consumer(self.contract, manifest, PROJECT)["ok"])

    def test_release_switch_and_exact_rollback_change_only_audit_target(self) -> None:
        source_a, manifest_a, release_a, _ = self.prepare_release("release-a", "a" * 40, "release-a")
        adapter_receipt = self.base / "adapter.json"
        install_adapter(self.contract, release_a / "scripts" / "learning_loop_adapter.py", adapter_receipt)
        source_b, manifest_b, _release_b, activation_b = self.prepare_release("release-b", "b" * 40, "release-b")
        self.assertEqual(self.run_consumer("bootstrap").stdout.strip(), "legacy:bootstrap")
        self.assertEqual(self.run_consumer("audit-task").stdout.strip(), "release-b:audit-task")
        self.assertTrue(verify_live_consumer(self.contract, manifest_b, source_b)["ok"])

        receipt = json.loads(activation_b.read_text(encoding="utf-8"))
        receipt["previous_target"] = None
        self.write_json(activation_b, receipt)
        with self.assertRaisesRegex(ControlPlaneError, "does not match activated release metadata"):
            rollback_release(self.contract, activation_b)
        receipt["previous_target"] = "releases/release-a"
        self.write_json(activation_b, receipt)

        rolled_back = rollback_release(self.contract, activation_b)
        self.assertEqual(rolled_back["active_target"], "releases/release-a")
        self.assertEqual(self.run_consumer("audit-system").stdout.strip(), "release-a:audit-system")
        self.assertTrue(verify_live_consumer(self.contract, manifest_a, source_a)["ok"])

    def test_existing_receipts_fail_before_activation_or_adapter_side_effects(self) -> None:
        source, manifest, release = self.stage_release("release-a", "release-a")
        activation_receipt = self.base / "activation-existing.json"
        activation_receipt.write_text("reserved\n", encoding="utf-8")
        with self.assertRaisesRegex(ControlPlaneError, "receipt path must not already exist"):
            activate_release(
                self.contract, manifest, source, release, "a" * 40, activation_receipt)
        self.assertFalse((release / "release-metadata.json").exists())
        self.assertFalse((release / "deployment-manifest.json").exists())
        self.assertFalse((self.runtime / "active").exists())

        activation_receipt.unlink()
        activate_release(self.contract, manifest, source, release, "a" * 40, activation_receipt)
        adapter_receipt = self.base / "adapter-existing.json"
        adapter_receipt.write_text("reserved\n", encoding="utf-8")
        original = self.consumer.read_bytes()
        with self.assertRaisesRegex(ControlPlaneError, "receipt path must not already exist"):
            install_adapter(
                self.contract, release / "scripts" / "learning_loop_adapter.py", adapter_receipt)
        self.assertEqual(self.consumer.read_bytes(), original)
        self.assertFalse((self.runtime / "compat").exists())
        self.assertFalse((self.runtime / "runtime-contract.json").exists())

    def test_consumer_and_selector_drift_fail_closed(self) -> None:
        source, manifest, release, activation = self.prepare_release("release-a", "a" * 40, "release-a")
        install_adapter(self.contract, release / "scripts" / "learning_loop_adapter.py", self.base / "adapter.json")
        self.consumer.write_text("print('drift')\n", encoding="utf-8")
        result = verify_live_consumer(self.contract, manifest, source)
        self.assertFalse(result["ok"])
        self.assertTrue(any("canonical consumer" in item for item in result["failures"]))
        (self.runtime / "active").unlink()
        (self.runtime / "active").symlink_to("../../escape")
        with self.assertRaisesRegex(ControlPlaneError, "unsafe target"):
            rollback_release(self.contract, activation)

    def test_adapter_receipt_rejects_managed_namespace_overlap_without_side_effects(self) -> None:
        _source, _manifest, release, _activation = self.prepare_release(
            "release-a", "a" * 40, "release-a")
        adapter = release / "scripts" / "learning_loop_adapter.py"
        original_consumer = self.consumer.read_bytes()
        managed_outputs = (
            self.runtime / "compat" / "v1" / "learning_loop.py",
            self.runtime / "compat" / "v1" / "legacy-metadata.json",
            self.runtime / "runtime-contract.json",
        )
        for receipt in (self.consumer, *managed_outputs):
            with self.subTest(receipt=receipt):
                with self.assertRaisesRegex(ControlPlaneError, "managed adapter paths must be distinct"):
                    install_adapter(self.contract, adapter, receipt)
                self.assertEqual(self.consumer.read_bytes(), original_consumer)
                self.assertTrue(all(not path.exists() for path in managed_outputs))

        legacy = managed_outputs[0]
        for receipt in (legacy.parent, legacy / "receipt.json"):
            with self.subTest(receipt=receipt):
                with self.assertRaisesRegex(ControlPlaneError, "managed adapter paths must be distinct"):
                    install_adapter(self.contract, adapter, receipt)
                self.assertEqual(self.consumer.read_bytes(), original_consumer)
                self.assertTrue(all(not path.exists() for path in managed_outputs))
                self.assertFalse((self.runtime / "compat").exists())

        inode_receipt = self.base / "adapter-inode-alias.json"
        runtime_contract = self.runtime / "runtime-contract.json"
        runtime_contract.write_text("{}\n", encoding="utf-8")
        inode_receipt.hardlink_to(runtime_contract)
        with self.assertRaisesRegex(ControlPlaneError, "managed adapter paths must be distinct"):
            install_adapter(self.contract, adapter, inode_receipt)
        self.assertEqual(self.consumer.read_bytes(), original_consumer)
        self.assertEqual(runtime_contract.read_text(encoding="utf-8"), "{}\n")
        self.assertFalse((self.runtime / "compat" / "v1" / "learning_loop.py").exists())
        self.assertFalse((self.runtime / "compat" / "v1" / "legacy-metadata.json").exists())

    def test_adapter_mode_drift_fails_recovery_rollback_and_live_verification(self) -> None:
        source, manifest, release, _activation = self.prepare_release(
            "release-a", "a" * 40, "release-a")
        adapter = release / "scripts" / "learning_loop_adapter.py"
        receipt = self.base / "adapter-mode.json"
        install_adapter(self.contract, adapter, receipt)
        self.assertEqual(stat.S_IMODE(self.consumer.stat().st_mode), 0o755)

        self.consumer.chmod(0o644)
        with self.assertRaisesRegex(ControlPlaneError, "mode drifted during adapter recovery"):
            install_adapter(self.contract, adapter, receipt)
        with self.assertRaisesRegex(ControlPlaneError, "installed canonical consumer mode drifted"):
            rollback_adapter(self.contract, receipt)
        verified = verify_live_consumer(self.contract, manifest, source)
        self.assertFalse(verified["ok"])
        self.assertIn("canonical consumer mode must be 0755", verified["failures"])

    def test_live_verification_rechecks_consumer_identity_and_mode_after_inspection(self) -> None:
        source, manifest, release, _activation = self.prepare_release(
            "release-a", "a" * 40, "release-a")
        install_adapter(
            self.contract, release / "scripts" / "learning_loop_adapter.py",
            self.base / "adapter-race-mode.json")
        original_run = subprocess.run

        def chmod_before_inspection(*args, **kwargs):
            self.consumer.chmod(0o644)
            return original_run(*args, **kwargs)

        with mock.patch.object(
                release_adapter_module.subprocess, "run",
                side_effect=chmod_before_inspection):
            verified = verify_live_consumer(self.contract, manifest, source)

        self.assertFalse(verified["ok"])
        self.assertIn(
            "canonical consumer changed during inspection", verified["failures"])
        self.assertEqual(stat.S_IMODE(self.consumer.stat().st_mode), 0o644)

    def test_live_verification_rechecks_canonical_path_identity_after_inspection(self) -> None:
        source, manifest, release, _activation = self.prepare_release(
            "release-a", "a" * 40, "release-a")
        install_adapter(
            self.contract, release / "scripts" / "learning_loop_adapter.py",
            self.base / "adapter-race-path.json")
        displaced = self.consumer.with_name("learning_loop.displaced.py")
        original_run = subprocess.run

        def replace_before_inspection(*args, **kwargs):
            self.consumer.rename(displaced)
            shutil.copyfile(displaced, self.consumer)
            self.consumer.chmod(0o755)
            return original_run(*args, **kwargs)

        with mock.patch.object(
                release_adapter_module.subprocess, "run",
                side_effect=replace_before_inspection):
            verified = verify_live_consumer(self.contract, manifest, source)

        self.assertFalse(verified["ok"])
        self.assertIn(
            "canonical consumer changed during inspection", verified["failures"])
        self.assertNotEqual(self.consumer.stat().st_ino, displaced.stat().st_ino)

    def test_live_verification_rejects_same_inode_rewrite_after_snapshot(self) -> None:
        source, manifest, release, _activation = self.prepare_release(
            "release-a", "a" * 40, "release-a")
        install_adapter(
            self.contract, release / "scripts" / "learning_loop_adapter.py",
            self.base / "adapter-race-bytes.json")
        forged_inspection = self.run_consumer("--adapter-inspect").stdout
        original_status = self.consumer.stat()
        original_run = subprocess.run

        def rewrite_before_inspection(*args, **kwargs):
            self.consumer.write_text(
                f"print({forged_inspection!r}, end='')\n", encoding="utf-8")
            return original_run(*args, **kwargs)

        with mock.patch.object(
                release_adapter_module.subprocess, "run",
                side_effect=rewrite_before_inspection):
            verified = verify_live_consumer(self.contract, manifest, source)

        rewritten_status = self.consumer.stat()
        self.assertEqual(
            (rewritten_status.st_dev, rewritten_status.st_ino,
             stat.S_IFMT(rewritten_status.st_mode), stat.S_IMODE(rewritten_status.st_mode)),
            (original_status.st_dev, original_status.st_ino,
             stat.S_IFMT(original_status.st_mode), stat.S_IMODE(original_status.st_mode)),
        )
        self.assertFalse(verified["ok"])
        self.assertIn(
            "canonical consumer changed during inspection", verified["failures"])
        self.assertNotEqual(
            hashlib.sha256(self.consumer.read_bytes()).hexdigest(),
            hashlib.sha256(
                (release / "scripts" / "learning_loop_adapter.py").read_bytes()).hexdigest(),
        )

    def test_contract_rejects_command_or_canonical_path_drift(self) -> None:
        payload = json.loads(self.contract.read_text(encoding="utf-8"))
        payload["release_commands"] = ["audit-task"]
        self.contract.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ControlPlaneError, "frozen compatibility surface"):
            load_runtime_contract(self.contract)
        payload["release_commands"] = ["audit-task", "audit-system"]
        payload["canonical_consumer"] = str(self.team / "other.py")
        self.contract.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ControlPlaneError, "canonical_consumer"):
            load_runtime_contract(self.contract)

    def test_release_rollback_rejects_malformed_or_traversing_receipt_fields(self) -> None:
        _source, _manifest, release, activation = self.prepare_release(
            "release-a", "a" * 40, "release-a")
        valid = json.loads(activation.read_text(encoding="utf-8"))
        cases = (
            ("previous_target", "../../escape", "safe relative POSIX path"),
            ("activated_target", "/tmp/escape", "safe relative POSIX path"),
            ("source_revision", "A" * 40, "source_revision is malformed"),
            ("manifest_sha256", "0" * 63, "manifest_sha256 is malformed"),
        )
        for field, value, message in cases:
            with self.subTest(field=field):
                payload = dict(valid)
                payload[field] = value
                self.write_json(activation, payload)
                with self.assertRaisesRegex(ControlPlaneError, message):
                    rollback_release(self.contract, activation)
                self.assertEqual((self.runtime / "active").readlink(), Path("releases/release-a"))

        payload = dict(valid)
        payload["unexpected"] = True
        self.write_json(activation, payload)
        with self.assertRaisesRegex(ControlPlaneError, "fields mismatch"):
            rollback_release(self.contract, activation)

        self.write_json(activation, valid)
        metadata = release / "release-metadata.json"
        drifted = json.loads(metadata.read_text(encoding="utf-8"))
        drifted["source_revision"] = "b" * 40
        self.write_json(metadata, drifted)
        with self.assertRaisesRegex(ControlPlaneError, "does not match activated release metadata"):
            rollback_release(self.contract, activation)
        self.assertTrue((self.runtime / "active").is_symlink())

    def test_adapter_rollback_rejects_malformed_receipt_and_selector_mismatch(self) -> None:
        _source_a, _manifest_a, release_a, _activation_a = self.prepare_release(
            "release-a", "a" * 40, "release-a")
        adapter_receipt = self.base / "adapter-a.json"
        install_adapter(
            self.contract, release_a / "scripts" / "learning_loop_adapter.py", adapter_receipt)
        installed = self.consumer.read_bytes()
        valid = json.loads(adapter_receipt.read_text(encoding="utf-8"))
        cases = (
            ("before_mode", "755", "before_mode is malformed"),
            ("before_sha256", "not-a-hash", "before_sha256 is malformed"),
            ("active_target", "../release-a", "safe relative POSIX path"),
        )
        for field, value, message in cases:
            with self.subTest(field=field):
                payload = dict(valid)
                payload[field] = value
                self.write_json(adapter_receipt, payload)
                with self.assertRaisesRegex(ControlPlaneError, message):
                    rollback_adapter(self.contract, adapter_receipt)
                self.assertEqual(self.consumer.read_bytes(), installed)

        payload = dict(valid)
        payload["unexpected"] = True
        self.write_json(adapter_receipt, payload)
        with self.assertRaisesRegex(ControlPlaneError, "fields mismatch"):
            rollback_adapter(self.contract, adapter_receipt)

        self.write_json(adapter_receipt, valid)
        _source_b, _manifest_b, _release_b, activation_b = self.prepare_release(
            "release-b", "b" * 40, "release-b")
        with self.assertRaisesRegex(ControlPlaneError, "selector does not match adapter receipt"):
            rollback_adapter(self.contract, adapter_receipt)
        self.assertEqual(self.consumer.read_bytes(), installed)
        rollback_release(self.contract, activation_b)
        self.assertFalse(rollback_adapter(self.contract, adapter_receipt)["idempotent"])

    def test_release_and_receipt_write_paths_reject_symlink_ancestors(self) -> None:
        source, manifest, release = self.stage_release("release-a", "release-a")
        releases = self.runtime / "releases"
        outside_releases = self.base / "outside-releases"
        releases.rename(outside_releases)
        releases.symlink_to(outside_releases, target_is_directory=True)
        receipt = self.base / "activation.json"
        with self.assertRaisesRegex(ControlPlaneError, "symlink path component"):
            activate_release(self.contract, manifest, source, release, "a" * 40, receipt)
        self.assertFalse(receipt.exists())
        releases.unlink()
        outside_releases.rename(releases)

        outside_receipts = self.base / "outside-receipts"
        outside_receipts.mkdir()
        receipt_parent = self.base / "receipt-link"
        receipt_parent.symlink_to(outside_receipts, target_is_directory=True)
        with self.assertRaisesRegex(ControlPlaneError, "symlink path component"):
            activate_release(
                self.contract, manifest, source, release, "a" * 40,
                receipt_parent / "activation.json")
        self.assertEqual(list(outside_receipts.iterdir()), [])

    def test_adapter_write_paths_reject_symlink_ancestors_and_leaf_targets(self) -> None:
        _source, _manifest, release, _activation = self.prepare_release(
            "release-a", "a" * 40, "release-a")
        adapter = release / "scripts" / "learning_loop_adapter.py"
        original = self.consumer.read_bytes()
        outside = self.base / "outside"
        outside.mkdir()

        compat = self.runtime / "compat"
        compat.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ControlPlaneError, "symlink path component"):
            install_adapter(self.contract, adapter, self.base / "adapter-compat.json")
        self.assertEqual(list(outside.iterdir()), [])
        compat.unlink()

        legacy_parent = compat / "v1"
        legacy_parent.mkdir(parents=True)
        metadata = legacy_parent / "legacy-metadata.json"
        metadata.symlink_to(outside / "metadata.json")
        with self.assertRaisesRegex(ControlPlaneError, "metadata must not be a symlink"):
            install_adapter(self.contract, adapter, self.base / "adapter-metadata.json")
        metadata.unlink()

        runtime_contract = self.runtime / "runtime-contract.json"
        runtime_contract.symlink_to(outside / "runtime-contract.json")
        with self.assertRaisesRegex(ControlPlaneError, "runtime contract copy must not be a symlink"):
            install_adapter(self.contract, adapter, self.base / "adapter-contract.json")
        runtime_contract.unlink()

        receipt_parent = self.base / "adapter-receipts"
        receipt_parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ControlPlaneError, "symlink path component"):
            install_adapter(self.contract, adapter, receipt_parent / "adapter.json")
        self.assertEqual(self.consumer.read_bytes(), original)
        self.assertEqual(list(outside.iterdir()), [])

    def test_adapter_install_check_swap_use_does_not_write_through_symlink(self) -> None:
        _source, _manifest, release, _activation = self.prepare_release(
            "release-a", "a" * 40, "release-a")
        adapter = release / "scripts" / "learning_loop_adapter.py"
        original_consumer = self.consumer.read_bytes()
        outside = self.base / "outside-install"
        outside_legacy = outside / "v1"
        outside_legacy.mkdir(parents=True)
        outside_files = {
            outside_legacy / "learning_loop.py": b"outside-legacy\n",
            outside_legacy / "legacy-metadata.json": b"outside-metadata\n",
        }
        for path, content in outside_files.items():
            path.write_bytes(content)
        compat = self.runtime / "compat"
        displaced = self.runtime / "compat-displaced"
        original_write = release_adapter_module._atomic_write_at
        swapped = False

        def swap_before_write(directory_fd, name, content, *, mode, field, replace):
            nonlocal swapped
            if not swapped and field == "legacy compatibility target":
                compat.rename(displaced)
                compat.symlink_to(outside, target_is_directory=True)
                swapped = True
            return original_write(
                directory_fd, name, content, mode=mode, field=field, replace=replace)

        with mock.patch.object(
                release_adapter_module, "_atomic_write_at", side_effect=swap_before_write):
            with self.assertRaisesRegex(ControlPlaneError, "unsafe directory component"):
                install_adapter(self.contract, adapter, self.base / "adapter-race.json")

        self.assertTrue(swapped)
        self.assertEqual(self.consumer.read_bytes(), original_consumer)
        for path, content in outside_files.items():
            self.assertEqual(path.read_bytes(), content)

    def test_adapter_rollback_check_swap_use_does_not_unlink_through_symlink(self) -> None:
        _source, _manifest, release, _activation = self.prepare_release(
            "release-a", "a" * 40, "release-a")
        receipt = self.base / "adapter-race-rollback.json"
        original_consumer = self.consumer.read_bytes()
        install_adapter(
            self.contract, release / "scripts" / "learning_loop_adapter.py", receipt)
        outside = self.base / "outside-rollback"
        outside_legacy = outside / "v1"
        outside_legacy.mkdir(parents=True)
        outside_files = {
            outside_legacy / "learning_loop.py": b"outside-legacy\n",
            outside_legacy / "legacy-metadata.json": b"outside-metadata\n",
        }
        for path, content in outside_files.items():
            path.write_bytes(content)
        compat = self.runtime / "compat"
        displaced = self.runtime / "compat-displaced"
        original_unlink = release_adapter_module._unlink_at
        swapped = False

        def swap_before_unlink(directory_fd, name, field, *, allow_symlink=False):
            nonlocal swapped
            if not swapped and field == "legacy compatibility metadata":
                compat.rename(displaced)
                compat.symlink_to(outside, target_is_directory=True)
                swapped = True
            return original_unlink(
                directory_fd, name, field, allow_symlink=allow_symlink)

        with mock.patch.object(
                release_adapter_module, "_unlink_at", side_effect=swap_before_unlink):
            with self.assertRaisesRegex(ControlPlaneError, "symlink path component"):
                rollback_adapter(self.contract, receipt)

        self.assertTrue(swapped)
        self.assertEqual(self.consumer.read_bytes(), original_consumer)
        for path, content in outside_files.items():
            self.assertEqual(path.read_bytes(), content)

    def test_selector_transition_lock_serializes_competing_activation(self) -> None:
        self.prepare_release("release-a", "a" * 40, "release-a")
        _source_b, _manifest_b, _release_b, activation_b = self.prepare_release(
            "release-b", "b" * 40, "release-b")
        source_c, manifest_c, release_c = self.stage_release("release-c", "release-c")
        activation_c = self.base / "activate-release-c.json"
        attempted = threading.Event()
        completed = threading.Event()
        failures = []
        worker = None
        original_link = release_adapter_module._atomic_link_at

        def activate_c() -> None:
            attempted.set()
            try:
                activate_release(
                    self.contract, manifest_c, source_c, release_c, "c" * 40, activation_c)
            except Exception as exc:  # pragma: no cover - asserted below
                failures.append(exc)
            finally:
                completed.set()

        def compete_before_publish(directory_fd, name, target):
            nonlocal worker
            if target == "releases/release-a":
                worker = threading.Thread(target=activate_c)
                worker.start()
                self.assertTrue(attempted.wait(1))
                self.assertFalse(completed.wait(0.05))
            return original_link(directory_fd, name, target)

        with mock.patch.object(
                release_adapter_module, "_atomic_link_at", side_effect=compete_before_publish):
            rolled_back = rollback_release(self.contract, activation_b)
        self.assertEqual(rolled_back["active_target"], "releases/release-a")
        self.assertIsNotNone(worker)
        self.assertTrue(completed.wait(2))
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(
            (self.runtime / "active").readlink(), Path("releases/release-c"))

    def test_install_reenters_after_receipt_boundary_failure(self) -> None:
        self.assert_install_reenters_after("adapter receipt", json_boundary=True)

    def test_install_reenters_after_legacy_boundary_failure(self) -> None:
        self.assert_install_reenters_after("legacy compatibility target")

    def test_install_reenters_after_metadata_boundary_failure(self) -> None:
        self.assert_install_reenters_after(
            "legacy compatibility metadata", json_boundary=True)

    def test_install_reenters_after_contract_boundary_failure(self) -> None:
        self.assert_install_reenters_after("runtime contract copy")

    def test_install_reenters_after_consumer_boundary_failure(self) -> None:
        self.assert_install_reenters_after("canonical consumer")

    def test_consumer_publish_failure_can_rollback_and_redeploy(self) -> None:
        _source, _manifest, release, _activation = self.prepare_release(
            "release-a", "a" * 40, "release-a")
        adapter = release / "scripts" / "learning_loop_adapter.py"
        receipt = self.base / "adapter-publish-failure.json"
        original = release_adapter_module._atomic_write_at

        def fail_before_publish(*args, **kwargs):
            if kwargs.get("field") == "canonical consumer":
                raise OSError("injected consumer publication failure")
            return original(*args, **kwargs)

        with mock.patch.object(
                release_adapter_module, "_atomic_write_at", side_effect=fail_before_publish):
            with self.assertRaisesRegex(OSError, "consumer publication failure"):
                install_adapter(self.contract, adapter, receipt)
        self.assertTrue(rollback_adapter(self.contract, receipt)["ok"])
        self.assertTrue(install_adapter(
            self.contract, adapter, self.base / "adapter-after-publish-failure.json")["ok"])

    def test_rollback_reenters_after_consumer_boundary_failure(self) -> None:
        self.assert_rollback_reenters_after("canonical consumer", unlink_boundary=False)

    def test_rollback_reenters_after_contract_cleanup_failure(self) -> None:
        self.assert_rollback_reenters_after("runtime contract copy", unlink_boundary=True)

    def test_rollback_reenters_after_metadata_cleanup_failure(self) -> None:
        self.assert_rollback_reenters_after(
            "legacy compatibility metadata", unlink_boundary=True)

    def test_rollback_retry_recovers_when_second_unlink_fails_before_mutation(self) -> None:
        _source, _manifest, release, _activation = self.prepare_release(
            "release-a", "a" * 40, "release-a")
        adapter = release / "scripts" / "learning_loop_adapter.py"
        receipt = self.base / "adapter-second-unlink.json"
        install_adapter(self.contract, adapter, receipt)
        original = release_adapter_module._unlink_at
        calls = 0

        def fail_second_unlink(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected second unlink failure")
            return original(*args, **kwargs)

        with mock.patch.object(
                release_adapter_module, "_unlink_at", side_effect=fail_second_unlink):
            with self.assertRaisesRegex(OSError, "second unlink failure"):
                rollback_adapter(self.contract, receipt)
        self.assertTrue(rollback_adapter(self.contract, receipt)["ok"])
        self.assertTrue(install_adapter(
            self.contract, adapter, self.base / "adapter-after-second-unlink.json")["ok"])

    def test_rollback_reenters_after_legacy_cleanup_failure(self) -> None:
        self.assert_rollback_reenters_after(
            "legacy compatibility target", unlink_boundary=True)

    def test_first_rollback_cleans_compatibility_state_and_allows_redeploy(self) -> None:
        source_a, manifest_a, release_a, activation_a = self.prepare_release(
            "release-a", "a" * 40, "release-a")
        adapter_a = self.base / "adapter-a.json"
        original = self.consumer.read_bytes()
        install_adapter(self.contract, release_a / "scripts" / "learning_loop_adapter.py", adapter_a)
        self.assertTrue(verify_live_consumer(self.contract, manifest_a, source_a)["ok"])

        self.assertFalse(rollback_adapter(self.contract, adapter_a)["idempotent"])
        self.assertEqual(self.consumer.read_bytes(), original)
        for path in (
            self.runtime / "compat" / "v1" / "learning_loop.py",
            self.runtime / "compat" / "v1" / "legacy-metadata.json",
            self.runtime / "runtime-contract.json",
        ):
            self.assertFalse(path.exists() or path.is_symlink())
        self.assertTrue(rollback_adapter(self.contract, adapter_a)["idempotent"])
        rollback_release(self.contract, activation_a)

        source_b, manifest_b, release_b, _activation_b = self.prepare_release(
            "release-b", "b" * 40, "release-b")
        adapter_b = self.base / "adapter-b.json"
        install_adapter(self.contract, release_b / "scripts" / "learning_loop_adapter.py", adapter_b)
        self.assertEqual(self.run_consumer("bootstrap").stdout.strip(), "legacy:bootstrap")
        self.assertEqual(self.run_consumer("audit-system").stdout.strip(), "release-b:audit-system")
        self.assertTrue(verify_live_consumer(self.contract, manifest_b, source_b)["ok"])


if __name__ == "__main__":
    unittest.main()
