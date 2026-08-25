from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from learning_control_plane.common import ControlPlaneError
from learning_control_plane.deployment import verify_deployment


PROJECT = Path(__file__).parents[1]


class DeploymentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.source = self.base / "source"
        self.runtime = self.base / "runtime"
        self.source.mkdir()
        self.runtime.mkdir()
        (self.source / "control.py").write_text("safe = True\n", encoding="utf-8")
        shutil.copyfile(self.source / "control.py", self.runtime / "control.py")
        digest = hashlib.sha256((self.source / "control.py").read_bytes()).hexdigest()
        self.manifest = self.base / "manifest.json"
        self.manifest.write_text(json.dumps({"schema_version": 1, "files": [
            {"source": "control.py", "runtime": "control.py", "sha256": digest}
        ]}), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_source_to_runtime_hash_passes_and_runtime_drift_fails(self) -> None:
        self.assertTrue(verify_deployment(self.manifest, self.source, self.runtime)["ok"])
        (self.runtime / "control.py").write_text("safe = False\n", encoding="utf-8")
        result = verify_deployment(self.manifest, self.source, self.runtime)
        self.assertFalse(result["ok"])
        self.assertIn("runtime hash mismatch", result["failures"][0])

    def test_source_drift_path_traversal_and_symlink_fail(self) -> None:
        (self.source / "control.py").write_text("drift\n", encoding="utf-8")
        self.assertFalse(verify_deployment(self.manifest, self.source, self.runtime)["ok"])
        payload = json.loads(self.manifest.read_text())
        payload["files"][0]["runtime"] = "../outside.py"
        self.manifest.write_text(json.dumps(payload), encoding="utf-8")
        self.assertFalse(verify_deployment(self.manifest, self.source, self.runtime)["ok"])
        payload["files"][0]["runtime"] = "control.py"
        self.manifest.write_text(json.dumps(payload), encoding="utf-8")
        (self.source / "control.py").write_text("safe = True\n", encoding="utf-8")
        (self.runtime / "control.py").unlink()
        (self.runtime / "control.py").symlink_to(self.source / "control.py")
        self.assertFalse(verify_deployment(self.manifest, self.source, self.runtime)["ok"])

    def test_committed_manifest_covers_runtime_sources_with_exact_hashes(self) -> None:
        manifest = PROJECT / "release" / "deployment-manifest.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        result = verify_deployment(manifest, PROJECT, PROJECT)
        self.assertTrue(result["ok"], result)
        covered = {item["source"] for item in payload["files"]}
        expected = {
            path.relative_to(PROJECT).as_posix()
            for path in (PROJECT / "src" / "learning_control_plane").glob("*.py")
        } | {"scripts/learning_control_plane.py"}
        self.assertEqual(covered, expected)


if __name__ == "__main__":
    unittest.main()
