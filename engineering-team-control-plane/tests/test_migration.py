from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from learning_control_plane.common import ControlPlaneError, sha256_file
from learning_control_plane.migration import apply_migration, plan_migration, rollback_migration

from .support import prepare_root


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = prepare_root(self.base)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_resolvable_duplicate(self) -> tuple[Path, Path]:
        john = self.root / "john" / "learnings" / "LEARNINGS.md"
        bob = self.root / "bob" / "learnings" / "LEARNINGS.md"
        john.write_text("# Learnings\n\n## [LRN-20260825-010] John\n\nSelf LRN-20260825-010.\n", encoding="utf-8")
        bob.write_text("# Learnings\n\n## [LRN-20260825-010] Bob\n\nSelf LRN-20260825-010.\n", encoding="utf-8")
        return john, bob

    def test_dry_run_hashes_every_input_without_writes(self) -> None:
        john, bob = self.add_resolvable_duplicate()
        before = {john: sha256_file(john), bob: sha256_file(bob)}
        plan = plan_migration(self.root)
        self.assertEqual(len(plan["changes"]), 1)
        self.assertGreaterEqual(len(plan["input_hashes"]), 17)
        self.assertEqual({path: sha256_file(path) for path in before}, before)
        self.assertEqual(plan["id_mapping"]["bob/learnings/LEARNINGS.md:3"], "LRN-20260825-010")
        self.assertEqual(plan["id_mapping"]["john/learnings/LEARNINGS.md:3"], "LRN-20260825-011")

    def test_ambiguous_reference_refuses_without_exact_resolution(self) -> None:
        self.add_resolvable_duplicate()
        team = self.root / "team-learnings" / "TEAM_LEARNINGS.md"
        team.write_text("# Team\n\nSee LRN-20260825-010.\n", encoding="utf-8")
        with self.assertRaisesRegex(ControlPlaneError, "ambiguous duplicate references"):
            plan_migration(self.root)
        resolution = {
            "team-learnings/TEAM_LEARNINGS.md:3:5:LRN-20260825-010": "john/learnings/LEARNINGS.md:3"
        }
        plan = plan_migration(self.root, resolutions=resolution)
        self.assertEqual(len(plan["changes"]), 2)

    def test_same_file_reference_requires_resolution_unless_inside_its_definition(self) -> None:
        john, bob = self.add_resolvable_duplicate()
        john.write_text(
            "# Learnings\n\nSee LRN-20260825-010.\n\n## [LRN-20260825-010] John\n\nDetails.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ControlPlaneError, "ambiguous duplicate references"):
            plan_migration(self.root)
        resolution = {
            "john/learnings/LEARNINGS.md:3:5:LRN-20260825-010": "bob/learnings/LEARNINGS.md:3"
        }
        plan = plan_migration(self.root, resolutions=resolution)
        self.assertIn("john/learnings/LEARNINGS.md", {item["path"] for item in plan["changes"]})

    def test_unrelated_peer_heading_ends_structural_self_reference(self) -> None:
        john, _bob = self.add_resolvable_duplicate()
        john.write_text(
            "# Learnings\n\n## [LRN-20260825-010] John\n\nDetails.\n\n"
            "## Unrelated\n\nSee LRN-20260825-010.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ControlPlaneError, "ambiguous duplicate references"):
            plan_migration(self.root)
        resolution = {
            "john/learnings/LEARNINGS.md:9:5:LRN-20260825-010": "bob/learnings/LEARNINGS.md:3"
        }
        self.assertTrue(plan_migration(self.root, resolutions=resolution)["changes"])

    def test_setext_peer_heading_ends_structural_self_reference(self) -> None:
        john, _bob = self.add_resolvable_duplicate()
        john.write_text(
            "# Learnings\n\n## [LRN-20260825-010] John\n\nDetails.\n\n"
            "Unrelated\n---------\n\nSee LRN-20260825-010.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ControlPlaneError, "ambiguous duplicate references"):
            plan_migration(self.root)
        resolution = {
            "john/learnings/LEARNINGS.md:10:5:LRN-20260825-010": "bob/learnings/LEARNINGS.md:3"
        }
        self.assertTrue(plan_migration(self.root, resolutions=resolution)["changes"])

    def test_indented_atx_peer_heading_ends_structural_self_reference(self) -> None:
        john, _bob = self.add_resolvable_duplicate()
        john.write_text(
            "# Learnings\n\n## [LRN-20260825-010] John\n\nDetails.\n\n"
            "   ## Unrelated\n\nSee LRN-20260825-010.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ControlPlaneError, "ambiguous duplicate references"):
            plan_migration(self.root)
        resolution = {
            "john/learnings/LEARNINGS.md:9:5:LRN-20260825-010": "bob/learnings/LEARNINGS.md:3"
        }
        self.assertTrue(plan_migration(self.root, resolutions=resolution)["changes"])

    def test_recursive_role_and_shared_files_are_migration_inputs(self) -> None:
        role = self.root / "john" / "learnings" / "archive" / "old.md"; role.parent.mkdir()
        shared = self.root / "team-learnings" / "archive" / "old.jsonl"; shared.parent.mkdir()
        role.write_text("# Archived\n", encoding="utf-8")
        shared.write_text('{"status":"archived"}\n', encoding="utf-8")
        plan = plan_migration(self.root)
        self.assertIn("john/learnings/archive/old.md", plan["input_hashes"])
        self.assertIn("team-learnings/archive/old.jsonl", plan["input_hashes"])

    def test_write_idempotence_manifest_backup_and_rollback(self) -> None:
        john, _bob = self.add_resolvable_duplicate()
        original = john.read_bytes()
        plan = plan_migration(self.root)
        backup = self.base / "backup"
        result = apply_migration(self.root, plan, backup_dir=backup, max_backup_bytes=100_000)
        self.assertTrue(result["written"])
        self.assertTrue((backup / "manifest.json").is_file())
        self.assertEqual(sha256_file(backup / "manifest.json"), (backup / "manifest.sha256").read_text().strip())
        self.assertFalse(plan_migration(self.root)["changes"])
        second = apply_migration(self.root, plan_migration(self.root), backup_dir=self.base / "unused", max_backup_bytes=100_000)
        self.assertTrue(second["idempotent"])
        rolled_back = rollback_migration(backup / "manifest.json", expected_root=self.root)
        self.assertTrue(rolled_back["rolled_back"])
        self.assertEqual(john.read_bytes(), original)
        self.assertTrue(rollback_migration(backup / "manifest.json", expected_root=self.root)["idempotent"])

    def test_backup_bound_and_source_drift_refuse_before_write(self) -> None:
        john, _ = self.add_resolvable_duplicate()
        plan = plan_migration(self.root)
        with self.assertRaisesRegex(ControlPlaneError, "exceeds bound"):
            apply_migration(self.root, plan, backup_dir=self.base / "too-small", max_backup_bytes=1)
        john.write_text(john.read_text() + "drift\n", encoding="utf-8")
        with self.assertRaisesRegex(ControlPlaneError, "drifted"):
            apply_migration(self.root, plan, backup_dir=self.base / "drift", max_backup_bytes=100_000)

    def test_unchanged_input_drift_also_refuses_stale_plan(self) -> None:
        self.add_resolvable_duplicate()
        plan = plan_migration(self.root)
        untouched = self.root / "mus" / "learnings" / "FEATURE_REQUESTS.md"
        untouched.write_text(untouched.read_text() + "drift\n", encoding="utf-8")
        with self.assertRaisesRegex(ControlPlaneError, "input drifted"):
            apply_migration(self.root, plan, backup_dir=self.base / "drift", max_backup_bytes=100_000)

    def test_manifest_backup_and_current_source_tamper_refuse_rollback(self) -> None:
        john, _ = self.add_resolvable_duplicate()
        backup = self.base / "backup"
        apply_migration(self.root, plan_migration(self.root), backup_dir=backup, max_backup_bytes=100_000)
        manifest = backup / "manifest.json"
        original_manifest = manifest.read_bytes()
        manifest.write_bytes(original_manifest + b" ")
        with self.assertRaisesRegex(ControlPlaneError, "manifest hash"):
            rollback_migration(manifest, expected_root=self.root)
        manifest.write_bytes(original_manifest)
        changed_backup = next((backup / "files").rglob("LEARNINGS.md"))
        original_backup = changed_backup.read_bytes()
        changed_backup.write_bytes(b"tamper")
        with self.assertRaisesRegex(ControlPlaneError, "backup hash"):
            rollback_migration(manifest, expected_root=self.root)
        changed_backup.write_bytes(original_backup)
        john.write_text(john.read_text() + "tamper\n", encoding="utf-8")
        with self.assertRaisesRegex(ControlPlaneError, "source drifted"):
            rollback_migration(manifest, expected_root=self.root)

    def test_partial_write_state_rolls_back_before_and_after_files(self) -> None:
        john, bob = self.add_resolvable_duplicate()
        team = self.root / "team-learnings" / "TEAM_LEARNINGS.md"
        team.write_text("# Team\n\nSee LRN-20260825-010.\n", encoding="utf-8")
        resolution = {"team-learnings/TEAM_LEARNINGS.md:3:5:LRN-20260825-010": "john/learnings/LEARNINGS.md:3"}
        backup = self.base / "backup"
        apply_migration(self.root, plan_migration(self.root, resolutions=resolution), backup_dir=backup, max_backup_bytes=100_000)
        manifest = json.loads((backup / "manifest.json").read_text())
        one = manifest["changes"][0]
        original = backup / one["backup"]
        (self.root / one["path"]).write_bytes(original.read_bytes())
        result = rollback_migration(backup / "manifest.json", expected_root=self.root)
        self.assertTrue(result["rolled_back"])
        self.assertEqual(len(result["restored"]), len(manifest["changes"]) - 1)

    def test_json_and_jsonl_inputs_are_hashed_and_rewritten(self) -> None:
        self.add_resolvable_duplicate()
        state = self.root / "team-learnings" / "extra.json"
        ledger = self.root / "team-learnings" / "extra.jsonl"
        state.write_text('{"ref":"LRN-20260825-010"}\n', encoding="utf-8")
        ledger.write_text('{"ref":"LRN-20260825-010"}\n', encoding="utf-8")
        resolutions = {
            "team-learnings/extra.json:1:9:LRN-20260825-010": "john/learnings/LEARNINGS.md:3",
            "team-learnings/extra.jsonl:1:9:LRN-20260825-010": "john/learnings/LEARNINGS.md:3",
        }
        plan = plan_migration(self.root, resolutions=resolutions)
        self.assertIn("team-learnings/extra.json", plan["input_hashes"])
        self.assertIn("team-learnings/extra.jsonl", plan["input_hashes"])
        changed = {item["path"] for item in plan["changes"]}
        self.assertIn("team-learnings/extra.json", changed)
        self.assertIn("team-learnings/extra.jsonl", changed)

    def test_rollback_path_traversal_and_symlink_refuse(self) -> None:
        self.add_resolvable_duplicate()
        backup = self.base / "backup"
        apply_migration(self.root, plan_migration(self.root), backup_dir=backup, max_backup_bytes=100_000)
        manifest_path = backup / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["changes"][0]["path"] = "../outside"
        payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        manifest_path.write_bytes(payload)
        (backup / "manifest.sha256").write_text(hashlib.sha256(payload).hexdigest() + "\n")
        with self.assertRaisesRegex(ControlPlaneError, "unsafe relative path"):
            rollback_migration(manifest_path, expected_root=self.root)

        other_root = prepare_root(self.base / "other")
        link = self.root / "john" / "learnings" / "linked.md"
        link.symlink_to(other_root / "john" / "learnings" / "LEARNINGS.md")
        with self.assertRaisesRegex(ControlPlaneError, "symlink"):
            plan_migration(self.root)

    def test_backup_parent_symlink_and_in_root_backup_refuse(self) -> None:
        self.add_resolvable_duplicate()
        plan = plan_migration(self.root)
        real_parent = self.base / "real-parent"; real_parent.mkdir()
        linked_parent = self.base / "linked-parent"; linked_parent.symlink_to(real_parent, target_is_directory=True)
        with self.assertRaisesRegex(ControlPlaneError, "symlink"):
            apply_migration(self.root, plan, backup_dir=linked_parent / "backup", max_backup_bytes=100_000)
        with self.assertRaisesRegex(ControlPlaneError, "outside the team root"):
            apply_migration(self.root, plan, backup_dir=self.root / "team-learnings" / "backup", max_backup_bytes=100_000)

    def test_broken_reference_refuses_migration(self) -> None:
        index = self.root / "team-learnings" / "TEAM_LEARNINGS.md"
        index.write_text("# Team\n\nSee FEAT-20260825-999.\n", encoding="utf-8")
        with self.assertRaisesRegex(ControlPlaneError, "broken event references"):
            plan_migration(self.root)

    def test_malformed_jsonl_refuses_migration(self) -> None:
        (self.root / "team-learnings" / "bad.jsonl").write_text('{"ok":true}\n{\n', encoding="utf-8")
        with self.assertRaisesRegex(ControlPlaneError, "malformed JSONL migration input"):
            plan_migration(self.root)

    def test_same_line_duplicate_references_have_distinct_resolution_keys(self) -> None:
        self.add_resolvable_duplicate()
        team = self.root / "team-learnings" / "TEAM_LEARNINGS.md"
        team.write_text("# Team\n\nLRN-20260825-010 then LRN-20260825-010.\n", encoding="utf-8")
        with self.assertRaisesRegex(ControlPlaneError, "ambiguous duplicate references") as caught:
            plan_migration(self.root)
        message = str(caught.exception)
        self.assertIn("TEAM_LEARNINGS.md:3:1:LRN-20260825-010", message)
        self.assertIn("TEAM_LEARNINGS.md:3:23:LRN-20260825-010", message)


if __name__ == "__main__":
    unittest.main()
