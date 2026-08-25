from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from learning_control_plane.common import ControlPlaneError
from learning_control_plane.release_adapter import (
    activate_release,
    install_adapter,
    load_runtime_contract,
    rollback_adapter,
    rollback_release,
    verify_live_consumer,
)


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

    def test_release_switch_and_exact_rollback_change_only_audit_target(self) -> None:
        source_a, manifest_a, release_a, _ = self.prepare_release("release-a", "a" * 40, "release-a")
        adapter_receipt = self.base / "adapter.json"
        install_adapter(self.contract, release_a / "scripts" / "learning_loop_adapter.py", adapter_receipt)
        source_b, manifest_b, _release_b, activation_b = self.prepare_release("release-b", "b" * 40, "release-b")
        self.assertEqual(self.run_consumer("bootstrap").stdout.strip(), "legacy:bootstrap")
        self.assertEqual(self.run_consumer("audit-task").stdout.strip(), "release-b:audit-task")
        self.assertTrue(verify_live_consumer(self.contract, manifest_b, source_b)["ok"])

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


if __name__ == "__main__":
    unittest.main()
