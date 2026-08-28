from __future__ import annotations

import json
import multiprocessing
import tempfile
import unittest
from pathlib import Path

from learning_control_plane.allocator import allocate_id

from .support import prepare_root


def _reserve(arguments: tuple[str, str]) -> str:
    root, owner = arguments
    return allocate_id(Path(root), kind="ERR", owner=owner, day="20260825")["id"]


class AllocatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = prepare_root(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_scans_all_role_stores_and_persists_reservations(self) -> None:
        (self.root / "mus" / "learnings" / "ERRORS.md").write_text(
            "# Errors\n\n## [ERR-20260825-099] existing\n", encoding="utf-8"
        )
        first = allocate_id(self.root, kind="ERR", owner="john", day="20260825")
        second = allocate_id(self.root, kind="ERR", owner="bob", day="20260825")
        self.assertEqual(first["id"], "ERR-20260825-100")
        self.assertEqual(second["id"], "ERR-20260825-101")
        store = json.loads((self.root / "team-learnings" / "ID_RESERVATIONS.json").read_text())
        self.assertEqual([item["id"] for item in store["reservations"]], [first["id"], second["id"]])

    def test_concurrent_calls_never_return_duplicate_unwritten_ids(self) -> None:
        owners = ["ken", "john", "jucy", "bob", "mus"] * 4
        context = multiprocessing.get_context("spawn")
        with context.Pool(processes=5) as pool:
            allocated = pool.map(_reserve, [(str(self.root), owner) for owner in owners])
        self.assertEqual(len(allocated), 20)
        self.assertEqual(len(set(allocated)), 20)
        self.assertEqual(sorted(allocated)[0], "ERR-20260825-006")
        reservations = json.loads((self.root / "team-learnings" / "ID_RESERVATIONS.json").read_text())
        self.assertEqual({item["id"] for item in reservations["reservations"]}, set(allocated))

    def test_recursive_role_and_shared_definitions_are_reserved(self) -> None:
        nested = self.root / "john" / "learnings" / "archive" / "old.md"
        nested.parent.mkdir()
        nested.write_text("## [ERR-20260825-099] nested\n", encoding="utf-8")
        shared = self.root / "team-learnings" / "history" / "old.md"
        shared.parent.mkdir()
        shared.write_text(
            "## [ERR-20260825-100] shared\n\n```text\n## [ERR-20260825-999] example\n```\n",
            encoding="utf-8",
        )
        result = allocate_id(self.root, kind="ERR", owner="john", day="20260825")
        self.assertEqual(result["id"], "ERR-20260825-101")

    def test_symlink_lock_and_reservation_store_fail_closed(self) -> None:
        target = self.root / "lock-target"
        target.write_text("", encoding="utf-8")
        (self.root / "team-learnings" / ".learning-id-allocation.lock").symlink_to(target)
        with self.assertRaisesRegex(ValueError, "lock"):
            allocate_id(self.root, kind="ERR", owner="john", day="20260825")


if __name__ == "__main__":
    unittest.main()
