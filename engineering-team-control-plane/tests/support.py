from __future__ import annotations
import hashlib
import json
from pathlib import Path
AGENTS = ("ken", "john", "jucy", "bob", "mus")
def prepare_root(base: Path) -> Path:
    root = base / "team"
    for index, agent in enumerate(AGENTS, start=1):
        lessons = root / agent / "learnings"; lessons.mkdir(parents=True)
        (lessons / "LEARNINGS.md").write_text("# Learnings\n", encoding="utf-8")
        (lessons / "ERRORS.md").write_text(f"# Errors\n\n## [ERR-20260825-{index:03d}] fixture\n\nResolved.\n", encoding="utf-8")
        (lessons / "FEATURE_REQUESTS.md").write_text("# Feature Requests\n", encoding="utf-8")
        (root / agent / "memory").mkdir()
    shared = root / "team-learnings"; (shared / "receipts").mkdir(parents=True)
    (shared / "TEAM_LEARNINGS.md").write_text("# Team Learnings\n\n## Development → Review\n\n- Ready.\n", encoding="utf-8")
    (shared / "LEARNING_STATE.json").write_text(json.dumps({"schema_version": 1, "lessons": {}}) + "\n", encoding="utf-8")
    (root / "tasks" / "active").mkdir(parents=True); (root / "tasks" / "archive").mkdir(parents=True)
    return root
def add_task(root: Path, task: str, *, state: str = "IMPLEMENTING", archived: bool = False,
             requirements: tuple[str, ...] = ("john:implementation",)) -> None:
    bucket = "archive" if archived else "active"
    required = "".join(f"  - {item}\n" for item in requirements)
    (root / "tasks" / bucket / f"{task}.md").write_text(
        f"---\ntask-id: {task}\nstate: {state}\nlearning-requirements:\n{required}---\n",
        encoding="utf-8",
    )
def make_receipt(root: Path, *, task: str = "TASK-TEST", agent: str = "john", stage: str = "implementation",
                 status: str = "closed", day: str = "2026-08-25", mode: str = "semantic_recall"):
    memory = root / agent / "memory" / f"{day}.md"; memory.write_text(f"# {agent} memory\n\nTask evidence for {task}.\n", encoding="utf-8")
    digest = hashlib.sha256(memory.read_bytes()).hexdigest()
    exact = {"path": f"{agent}/memory/{day}.md", "sha256": digest, "line_start": 1, "line_end": 3}
    recall = {"status": "ok", "query": f"{task} risk stage", "hits": [f"{agent}/memory/{day}.md:1-3"]}
    retrieval = {"mode": mode, "semantic_recall": recall, "exact_retrieval": [exact]}
    if mode == "exact_file_fallback":
        recall.clear(); recall.update({"status": "unavailable", "query": f"{task} risk stage", "reason": "provider timeout"}); retrieval["fallback_files"] = [f"{agent}/memory/{day}.md"]
    receipt = {"schema_version": 2, "task_id": task, "agent": agent, "stage": stage, "status": status,
        "opened_at": f"{day}T09:00:00+00:00", "closed_at": f"{day}T10:00:00+00:00",
        "closure_evidence": {"schema_version": 1, "owner_day_memory": {"date": day, "path": f"{agent}/memory/{day}.md",
            "bytes": memory.stat().st_size, "sha256": digest}, "retrieval_evidence": retrieval}}
    path = root / "team-learnings" / "receipts" / task / f"{agent}-{stage}.json"; path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8"); return path, receipt
