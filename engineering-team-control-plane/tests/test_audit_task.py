from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from learning_control_plane.audit import audit_task
from .support import add_task, make_receipt, prepare_root
class AuditTaskTests(unittest.TestCase):
    def setUp(self): self.temporary = tempfile.TemporaryDirectory(); self.root = prepare_root(Path(self.temporary.name)); add_task(self.root, "TASK-TEST")
    def tearDown(self): self.temporary.cleanup()
    def test_valid_semantic_and_explicit_fallback_pass(self):
        make_receipt(self.root); self.assertTrue(audit_task(self.root, "TASK-TEST", ["john:implementation"])["ok"])
        add_task(self.root, "TASK-TEST", requirements=("john:review",))
        make_receipt(self.root, stage="review", mode="exact_file_fallback"); self.assertTrue(audit_task(self.root, "TASK-TEST", ["john:review"])["ok"])
    def test_authoritative_requirements_reject_caller_omission_and_extra_open_receipt(self):
        add_task(self.root, "TASK-TEST", requirements=("john:implementation", "bob:review"))
        make_receipt(self.root)
        make_receipt(self.root, agent="bob", stage="review")
        omitted = audit_task(self.root, "TASK-TEST", ["john:implementation"])
        self.assertFalse(omitted["ok"])
        self.assertIn("bob:review", omitted["failures"]["requested_requirements"][0])
        self.assertTrue(audit_task(self.root, "TASK-TEST", ["john:implementation", "bob:review"])["ok"])
        make_receipt(self.root, agent="mus", stage="test", status="open")
        result = audit_task(self.root, "TASK-TEST", ["john:implementation", "bob:review"])
        self.assertFalse(result["ok"])
        self.assertIn("open_receipt:mus:test", result["failures"])
    def test_missing_authoritative_participation_record_fails_closed(self):
        task = self.root / "tasks" / "active" / "TASK-TEST.md"
        task.write_text("---\ntask-id: TASK-TEST\nstate: IMPLEMENTING\n---\n", encoding="utf-8")
        make_receipt(self.root)
        result = audit_task(self.root, "TASK-TEST", ["john:implementation"])
        self.assertFalse(result["ok"])
        self.assertIn("authoritative_participation", result["failures"])
    def test_missing_and_open_receipts_fail(self):
        self.assertFalse(audit_task(self.root, "TASK-TEST", ["john:implementation"])["ok"]); make_receipt(self.root, status="open"); self.assertFalse(audit_task(self.root, "TASK-TEST", ["john:implementation"])["ok"])
    def test_empty_memory_and_hash_drift_fail(self):
        make_receipt(self.root); memory = self.root / "john" / "memory" / "2026-08-25.md"; memory.write_text("", encoding="utf-8"); self.assertFalse(audit_task(self.root, "TASK-TEST", ["john:implementation"])["ok"])
        memory.write_text("changed\n", encoding="utf-8"); self.assertFalse(audit_task(self.root, "TASK-TEST", ["john:implementation"])["ok"])
    def test_cross_owner_and_wrong_day_fail(self):
        path, receipt = make_receipt(self.root); receipt["closure_evidence"]["owner_day_memory"]["path"] = "ken/memory/2026-08-25.md"; path.write_text(json.dumps(receipt), encoding="utf-8"); self.assertFalse(audit_task(self.root, "TASK-TEST", ["john:implementation"])["ok"])
        path, receipt = make_receipt(self.root); receipt["closure_evidence"]["owner_day_memory"]["date"] = "2026-08-24"; path.write_text(json.dumps(receipt), encoding="utf-8"); self.assertFalse(audit_task(self.root, "TASK-TEST", ["john:implementation"])["ok"])
    def test_missing_recall_exact_and_malformed_evidence_fail(self):
        mutations = [lambda e: e["retrieval_evidence"].pop("semantic_recall"), lambda e: e["retrieval_evidence"].update(exact_retrieval=[]), lambda e: e.update(schema_version="2")]
        for mutation in mutations:
            path, receipt = make_receipt(self.root); mutation(receipt["closure_evidence"]); path.write_text(json.dumps(receipt), encoding="utf-8"); self.assertFalse(audit_task(self.root, "TASK-TEST", ["john:implementation"])["ok"])
    def test_schema_v1_is_parseable_but_cannot_satisfy_gate(self):
        path, receipt = make_receipt(self.root); receipt["schema_version"] = 1; receipt.pop("closure_evidence"); path.write_text(json.dumps(receipt), encoding="utf-8")
        result = audit_task(self.root, "TASK-TEST", ["john:implementation"]); self.assertFalse(result["ok"]); self.assertIn("schema_version 2", result["failures"]["john:implementation"][0])
    def test_owner_day_uses_receipt_offset_calendar_date(self):
        path, receipt = make_receipt(self.root, day="2026-08-25")
        receipt["closed_at"] = "2026-08-25T00:10:00+08:00"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        self.assertTrue(audit_task(self.root, "TASK-TEST", ["john:implementation"])["ok"])
    def test_malformed_json_and_symlink_memory_fail_closed(self):
        path, _ = make_receipt(self.root); path.write_text("{", encoding="utf-8"); self.assertFalse(audit_task(self.root, "TASK-TEST", ["john:implementation"])["ok"])
        path, receipt = make_receipt(self.root); memory = self.root / "john" / "memory" / "2026-08-25.md"; target = self.root / "outside.md"; target.write_text("secret\n", encoding="utf-8"); memory.unlink(); memory.symlink_to(target); path.write_text(json.dumps(receipt), encoding="utf-8"); self.assertFalse(audit_task(self.root, "TASK-TEST", ["john:implementation"])["ok"])
    def test_non_utf8_memory_fails_closed(self):
        path, _ = make_receipt(self.root)
        (self.root / "john" / "memory" / "2026-08-25.md").write_bytes(b"\xff\xfe")
        self.assertFalse(audit_task(self.root, "TASK-TEST", ["john:implementation"])["ok"])
if __name__ == "__main__": unittest.main()
