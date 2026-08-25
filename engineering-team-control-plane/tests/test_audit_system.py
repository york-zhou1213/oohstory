from __future__ import annotations
import json
import math
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from learning_control_plane.audit import audit_system
from .support import add_task, make_receipt, prepare_root
class AuditSystemTests(unittest.TestCase):
    def setUp(self): self.temporary = tempfile.TemporaryDirectory(); self.root = prepare_root(Path(self.temporary.name))
    def tearDown(self): self.temporary.cleanup()
    def test_clean_store_passes(self):
        result = audit_system(self.root, now=datetime(2026, 8, 25, 12, tzinfo=timezone.utc)); self.assertTrue(result["ok"], result)
    def test_orphan_and_stale_open_receipts(self):
        make_receipt(self.root, task="TASK-ORPHAN", status="open"); add_task(self.root, "TASK-OLD"); make_receipt(self.root, task="TASK-OLD", stage="test", status="open", day="2026-08-23")
        add_task(self.root, "TASK-ARCHIVED", state="CLOSED", archived=True); make_receipt(self.root, task="TASK-ARCHIVED", stage="review", status="open")
        result = audit_system(self.root, stale_hours=24, now=datetime(2026, 8, 25, 12, tzinfo=timezone.utc)); self.assertEqual(len(result["orphan_open_receipts"]), 1); self.assertEqual(len(result["stale_open_receipts"]), 2); self.assertFalse(result["ok"])
    def test_duplicate_and_broken_ids_fail(self):
        (self.root / "john" / "learnings" / "LEARNINGS.md").write_text("# Learnings\n\n## [LRN-20260825-001] A\n", encoding="utf-8")
        (self.root / "bob" / "learnings" / "LEARNINGS.md").write_text("# Learnings\n\n## [LRN-20260825-001] B\n\nSee FEAT-20260825-999.\n", encoding="utf-8")
        result = audit_system(self.root); self.assertIn("LRN-20260825-001", result["duplicate_event_ids"]); self.assertTrue(any("FEAT-20260825-999" in value for value in result["broken_event_references"]))
    def test_nested_role_and_shared_duplicate_ids_fail(self):
        nested = self.root / "john" / "learnings" / "archive" / "old.md"; nested.parent.mkdir()
        shared = self.root / "team-learnings" / "archive" / "old.md"; shared.parent.mkdir()
        nested.write_text("## [LRN-20260825-777] nested\n", encoding="utf-8")
        shared.write_text("## [LRN-20260825-777] shared\n", encoding="utf-8")
        result = audit_system(self.root)
        self.assertIn("LRN-20260825-777", result["duplicate_event_ids"])
    def test_each_individual_empty_error_store_fails(self):
        for agent in ("ken", "john", "jucy", "bob", "mus"):
            with self.subTest(agent=agent):
                path = self.root / agent / "learnings" / "ERRORS.md"
                original = path.read_text(encoding="utf-8")
                path.write_text("# Errors\n", encoding="utf-8")
                result = audit_system(self.root)
                self.assertEqual(result["empty_error_agents"], [agent])
                self.assertFalse(result["ok"])
                path.write_text(original, encoding="utf-8")
    def test_fenced_error_header_does_not_satisfy_role_debt_gate(self):
        path = self.root / "john" / "learnings" / "ERRORS.md"
        path.write_text("# Errors\n\n```text\n## [ERR-20260825-999] example\n```\n", encoding="utf-8")
        result = audit_system(self.root)
        self.assertEqual(result["empty_error_agents"], ["john"])
        self.assertFalse(result["ok"])
    def test_backtick_in_backtick_fence_info_does_not_hide_visible_header(self):
        path = self.root / "john" / "learnings" / "ERRORS.md"
        path.write_text("# Errors\n\n```bad`info\n## [ERR-20260825-999] visible\n```\n", encoding="utf-8")
        result = audit_system(self.root)
        self.assertNotIn("john", result["empty_error_agents"])
    def test_alphanumeric_ids_and_jsonl_references_are_scanned(self):
        (self.root / "john" / "learnings" / "LEARNINGS.md").write_text("# Learnings\n\n## [LRN-20260825-A01] Alpha\n", encoding="utf-8")
        ledger = self.root / "team-learnings" / "LEARNING_LEDGER.jsonl"
        ledger.write_text('{"event_id":"LRN-20260825-A01","guard":"FEAT-20260825-Z99"}\n', encoding="utf-8")
        result = audit_system(self.root)
        self.assertNotIn("LRN-20260825-A01", result["duplicate_event_ids"])
        self.assertTrue(any("FEAT-20260825-Z99" in value for value in result["broken_event_references"]))
    def test_malformed_receipt_and_lifecycle_debt_fail(self):
        path = self.root / "team-learnings" / "receipts" / "TASK-X" / "john-test.json"; path.parent.mkdir(); path.write_text("{", encoding="utf-8"); (self.root / "mus" / "learnings" / "FEATURE_REQUESTS.md").unlink()
        state = {"schema_version": 1, "lessons": {"john/learnings/x.md": {"recurrence_count": 3, "source_tasks": ["TASK-A", "TASK-B"], "guards": [], "promoted_to": []}}}
        (self.root / "team-learnings" / "LEARNING_STATE.json").write_text(json.dumps(state), encoding="utf-8"); result = audit_system(self.root)
        self.assertTrue(result["malformed_receipts"]); self.assertTrue(result["guard_debt"]); self.assertTrue(result["promotion_debt"]); self.assertTrue(result["missing_lifecycle_files"])
    def test_receipt_path_identity_mismatch_fails(self):
        path, receipt = make_receipt(self.root); receipt["agent"] = "bob"; path.write_text(json.dumps(receipt), encoding="utf-8"); self.assertTrue(audit_system(self.root)["malformed_receipts"])
    def test_nested_and_noncanonical_receipt_paths_fail(self):
        nested = self.root / "team-learnings" / "receipts" / "TASK-X" / "nested" / "john-test.json"
        nested.parent.mkdir(parents=True); nested.write_text("{}\n", encoding="utf-8")
        wrong_name = self.root / "team-learnings" / "receipts" / "TASK-X" / "john-test.txt"
        wrong_name.write_text("{}\n", encoding="utf-8")
        result = audit_system(self.root)
        self.assertEqual(result["receipt_count"], 2)
        self.assertEqual(len(result["malformed_receipts"]), 2)
        self.assertFalse(result["ok"])
    def test_task_frontmatter_identity_and_registry_symlink_fail(self):
        add_task(self.root, "TASK-MISMATCH")
        record = self.root / "tasks" / "active" / "TASK-MISMATCH.md"
        record.write_text(record.read_text(encoding="utf-8").replace("task-id: TASK-MISMATCH", "task-id: TASK-OTHER"), encoding="utf-8")
        mismatch = audit_system(self.root)
        self.assertTrue(any("does not match filename" in value for value in mismatch["errors"]))
        record.write_text(record.read_text(encoding="utf-8").replace("task-id: TASK-OTHER", "task-id: TASK-MISMATCH\ntask-id: TASK-MISMATCH"), encoding="utf-8")
        repeated = audit_system(self.root)
        self.assertTrue(any("exactly one frontmatter task-id" in value for value in repeated["errors"]))
        tasks = self.root / "tasks"; outside = Path(self.temporary.name) / "outside-tasks"
        tasks.rename(outside); tasks.symlink_to(outside, target_is_directory=True)
        linked = audit_system(self.root)
        self.assertTrue(any("symlink" in value for value in linked["errors"]))
        self.assertFalse(linked["ok"])
    def test_malformed_shared_jsonl_fails_closed(self):
        (self.root / "team-learnings" / "LEARNING_LEDGER.jsonl").write_text('{"ok":true}\n{\n', encoding="utf-8")
        result = audit_system(self.root)
        self.assertTrue(any("malformed JSONL" in value for value in result["errors"]))
    def test_unmaterialized_reservations_are_not_broken_references(self):
        (self.root / "team-learnings" / "ID_RESERVATIONS.json").write_text(
            json.dumps({"schema_version": 1, "reservations": [{"id": "ERR-20260825-999", "owner": "john"}]}), encoding="utf-8"
        )
        self.assertTrue(audit_system(self.root)["ok"])
    def test_future_open_receipt_and_nonfinite_stale_window_fail_closed(self):
        add_task(self.root, "TASK-FUTURE")
        make_receipt(self.root, task="TASK-FUTURE", status="open", day="2026-08-26")
        result = audit_system(self.root, now=datetime(2026, 8, 25, 12, tzinfo=timezone.utc))
        self.assertTrue(any("in the future" in value for value in result["malformed_receipts"]))
        for value in (math.nan, math.inf, -math.inf):
            with self.assertRaisesRegex(ValueError, "finite non-negative"):
                audit_system(self.root, stale_hours=value)
if __name__ == "__main__": unittest.main()
