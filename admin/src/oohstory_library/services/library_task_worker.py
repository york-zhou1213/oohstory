"""全局电子书库 oh-story 独立任务进程。"""

from __future__ import annotations

from .error_boundaries import RECOVERABLE_OPERATION_ERRORS

import argparse
import json
import os
import re
import select
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from oohstory_library.services.codex_cli import codex_env, ensure_codex_cli
from oohstory_library.services.electronic_library import ElectronicLibraryService
from oohstory_library.services.library_task_runners import (
    cli_runtime_env,
    ensure_openclaw_task_agent,
    find_cli,
    openclaw_session_lineage_state,
    rotate_openclaw_session,
)
from oohstory_library.services.oh_story_contracts import (
    has_resume_checkpoint,
    long_pipeline_stages,
    project_long_contract_failures,
    read_short_meta,
    short_pipeline_stages,
    validate_output_contract,
)

SHORT_ANALYSIS_WORD_LIMIT = 30_000
MEDIUM_ANALYSIS_WORD_LIMIT = 150_000
TOKEN_EXHAUSTION_PATTERNS = (
    re.compile(r"subscription usage limit", re.IGNORECASE),
    re.compile(r"usage limit (?:has been )?(?:reached|exceeded)", re.IGNORECASE),
    re.compile(r"insufficient[_\s-]*quota", re.IGNORECASE),
    re.compile(r"quota (?:has been )?(?:exhausted|exceeded)", re.IGNORECASE),
    re.compile(r"(?:token|credit)s?.*(?:exhausted|depleted|insufficient)", re.IGNORECASE),
    re.compile(r"(?:额度|配额).*(?:耗尽|用尽|不足|超限)"),
)
TOKEN_EXHAUSTION_ERROR_CONTEXT = re.compile(
    r"(?:error|exception|failover|http\s*(?:402|429)|"
    r"status\s*[=:]\s*(?:402|429)|错误|异常)",
    re.IGNORECASE,
)
TOKEN_EXHAUSTION_PROMPT_FIELDS = (
    "finalPromptText",
    "systemPrompt",
    "userPrompt",
)


class ArtifactValidationError(RuntimeError):
    """The AI process exited, but the OH-Story delivery contract did not pass."""


class ModelContractError(RuntimeError):
    """A parent or child AI session escaped the user-selected model."""


OPENCLAW_QUIESCENCE_GRACE_SECONDS = 12
OPENCLAW_MAX_AUTOMATIC_CONTINUATIONS = 500
OPENCLAW_MAX_NO_PROGRESS_TURNS = 2
OPENCLAW_LONG_STAGE2_CHAPTERS_PER_TURN = 16
OPENCLAW_LONG_STAGE2_CHAPTERS_PER_CHILD = 4
LONG_BOUNDARY_SNAPSHOT = ".webnovel-chapter-boundaries.md"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _long_boundary_section(progress_text: str) -> str:
    lines = progress_text.splitlines()
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        if re.match(r"^##\s*章节边界", line.strip()):
            start = index
            continue
        if start is not None and index > start and line.startswith("## "):
            end = index
            break
    if start is None:
        return ""
    section = "\n".join(lines[start:end]).strip()
    return section if re.search(r"^\|\s*(?:序|序章|第零章|\d+)\s*\|", section, re.M) else ""


def _long_boundary_section_from_tsv(output_dir: Path) -> str:
    sidecar = output_dir / "_chapter_bounds.tsv"
    if not sidecar.is_file():
        return ""
    rows: list[str] = []
    try:
        for raw_line in sidecar.read_text(encoding="utf-8").splitlines():
            cells = [value.strip().replace("|", "\\|") for value in raw_line.split("\t")]
            if len(cells) < 4:
                continue
            identifier = cells[0]
            if not (identifier.isdigit() or identifier in {"序", "序章", "第零章"}):
                continue
            rows.append(f"| {identifier} | {cells[1]} | {cells[2]} | {cells[3]} |")
    except (OSError, UnicodeError):
        return ""
    if not rows:
        return ""
    return "\n".join(
        [
            "## 章节边界",
            "",
            "| 章号 | 标题 | 起始行 | 字数 |",
            "|---|---|---|---|",
            *rows,
        ]
    )


def preserve_long_chapter_boundaries(output_dir: Path) -> bool:
    """Snapshot and restore the Stage 0 boundary table around AI turns.

    Background sessions are allowed to update progress state, but the Stage 0
    boundary table is immutable input for Stages 1/2/6.  A model must never be
    able to make an incomplete book pass by replacing 982 rows with prose.
    """

    progress_path = output_dir / "_progress.md"
    snapshot_path = output_dir / LONG_BOUNDARY_SNAPSHOT
    if not progress_path.is_file():
        return False
    try:
        progress_text = progress_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    current = _long_boundary_section(progress_text)
    snapshot = ""
    if snapshot_path.is_file():
        try:
            snapshot = _long_boundary_section(
                snapshot_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError):
            snapshot = ""
    if not snapshot:
        snapshot = current or _long_boundary_section_from_tsv(output_dir)
        if not snapshot:
            return False
        snapshot_path.write_text(snapshot.rstrip() + "\n", encoding="utf-8")
    if current == snapshot:
        return False

    lines = progress_text.splitlines()
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        if re.match(r"^##\s*章节边界", line.strip()):
            start = index
            continue
        if start is not None and index > start and line.startswith("## "):
            end = index
            break
    replacement = snapshot.splitlines()
    if start is None:
        final_index = next(
            (
                index
                for index, line in enumerate(lines)
                if re.match(r"^##\s*最终状态", line.strip())
            ),
            len(lines),
        )
        lines[final_index:final_index] = [*replacement, ""]
    else:
        lines[start:end] = [*replacement, ""]
    progress_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return True


def read_task(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_task(path: Path, task: Dict[str, Any]) -> None:
    task["updated_at"] = now()
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def ensure_readonly_source_link(source_path: Path, link_path: Path) -> Path:
    """Atomically expose the canonical library TXT through a relative symlink.

    The deconstruction store must not retain a second full-text copy.  A
    relative link also keeps the reference valid when the electronic-library
    mount is reached through the canonical project symlink.
    """
    source_path = source_path.expanduser().resolve()
    if not source_path.is_file() or source_path.stat().st_size <= 0:
        raise ValueError("电子书库原文不存在或为空")

    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.is_symlink():
        try:
            if link_path.resolve(strict=True) == source_path:
                return link_path
        except OSError:
            pass
    elif link_path.exists() and not link_path.is_file():
        raise ValueError("全局拆书库原文引用不是文件或软链接")

    relative_target = os.path.relpath(source_path, start=link_path.parent)
    temporary_link = link_path.with_name(
        f".{link_path.name}.link-{os.getpid()}"
    )
    if temporary_link.exists() or temporary_link.is_symlink():
        temporary_link.unlink()
    temporary_link.symlink_to(relative_target)
    os.replace(temporary_link, link_path)
    return link_path


def set_step(task: Dict[str, Any], step_id: str, status: str) -> None:
    for step in task.get("steps", []):
        if step.get("id") == step_id:
            step["status"] = status
            if status == "running":
                step["started_at"] = now()
            elif status in {"completed", "error"}:
                step["finished_at"] = now()
            break


def initialize_pipeline_stages(task: Dict[str, Any], pipeline: str) -> None:
    if pipeline == "short":
        definitions = [
            (2, "结构与情节节点"),
            (3, "情感线与爆点"),
            (4, "反转与写作手法"),
            (5, "人物与开头结尾"),
            (6, "综合评估与验收"),
        ]
    else:
        definitions = [
            (0, "概要与章节边界"),
            (1, "黄金三章与快速预览"),
            (2, "逐章摘要"),
            (3, "剧情、节奏与情绪聚合"),
            (4, "设定与角色关系"),
            (5, "汇总拆文报告"),
            (6, "文风画像"),
        ]
    task["pipeline_stages"] = [
        {"stage": stage, "name": name, "status": "pending"}
        for stage, name in definitions
    ]


def sync_pipeline_stage(
    task: Dict[str, Any],
    stage_text: str,
    *,
    completed_all: bool = False,
    paused_after_stage1: bool = False,
) -> None:
    if completed_all:
        for item in task.get("pipeline_stages", []):
            item["status"] = "completed"
        return
    if paused_after_stage1:
        for item in task.get("pipeline_stages", []):
            item["status"] = "completed" if item["stage"] <= 1 else "pending"
        return
    match = re.search(r"Stage\s+(\d+)", stage_text or "")
    if not match:
        return
    current = int(match.group(1))
    for item in task.get("pipeline_stages", []):
        item["status"] = (
            "completed"
            if item["stage"] < current
            else "running"
            if item["stage"] == current
            else "pending"
        )


def refresh_pipeline_stages_from_artifacts(
    task: Dict[str, Any],
    output_dir: Path,
    pipeline: str,
) -> None:
    """Project the terminal stage snapshot from durable OH-Story artifacts.

    A successful contract check must never be followed by blindly rewriting
    every cached task stage to ``completed``.  The progress/meta artifact is
    the stage truth and also carries the human-readable status text.
    """

    if pipeline == "short":
        stages = short_pipeline_stages(read_short_meta(output_dir))
    else:
        progress_path = output_dir / "_progress.md"
        try:
            progress_text = progress_path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            progress_text = ""
        stages = long_pipeline_stages(progress_text)
    if stages:
        task["pipeline_stages"] = stages


def compact_word_count(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            count += len(re.sub(r"\s+", "", chunk))
    return count


def output_artifact_marker(output_dir: Path, pipeline: str) -> str:
    """Fingerprint durable skill output without counting progress-only edits."""

    candidates: list[Path] = []
    if pipeline == "short":
        for relative in (
            "_meta.json",
            "拆文报告.md",
            "情节节点.md",
            "写作手法.md",
        ):
            candidates.append(output_dir / relative)
    else:
        for relative in (
            "概要.md",
            "快速预览.md",
            "_章节摘要汇总.md",
            "拆文报告.md",
            "文风.md",
            "剧情/节奏.md",
            "剧情/情绪模块.md",
        ):
            candidates.append(output_dir / relative)
        for directory in ("章节", "剧情", "设定", "角色"):
            root = output_dir / directory
            if root.is_dir():
                candidates.extend(root.rglob("*.md"))
    facts: list[tuple[str, int, int]] = []
    for path in sorted(set(candidates), key=lambda item: str(item)):
        try:
            if path.is_file():
                stat = path.stat()
                facts.append(
                    (
                        str(path.relative_to(output_dir)),
                        int(stat.st_size),
                        int(stat.st_mtime_ns),
                    )
                )
        except (OSError, ValueError):
            continue
    return json.dumps(facts, ensure_ascii=False, separators=(",", ":"))


def contract_progress_score(errors: list[str]) -> tuple[int, int, float]:
    """Rank mechanical contract deficits without trusting artifact mtimes."""

    missing_stages = 0
    coverage = 0.0
    for error in errors:
        if "未完成 Stage" in error:
            missing_stages += len(re.findall(r"\d+", error))
        match = re.search(r"逐章摘要覆盖不足：(\d+)\s*/\s*(\d+)", error)
        if match and int(match.group(2)) > 0:
            coverage = max(
                coverage,
                int(match.group(1)) / int(match.group(2)),
            )
    # Fewer independent contract failures dominates all secondary progress;
    # at equal error count, fewer missing stages and greater chapter coverage
    # are the only model-authored changes that justify another turn.
    return (-len(errors), -missing_stages, coverage)


def contract_errors_improved(before: list[str], after: list[str]) -> bool:
    return contract_progress_score(after) > contract_progress_score(before)


def resolve_analysis_band(word_count: int) -> str:
    """Classify one work without leaving gaps at the product boundaries."""
    count = max(int(word_count), 0)
    if count < SHORT_ANALYSIS_WORD_LIMIT:
        return "short"
    if count <= MEDIUM_ANALYSIS_WORD_LIMIT:
        return "medium"
    return "long"


def resolve_execution_mode(requested_mode: str, analysis_band: str) -> str:
    """Keep the user's requested scope immutable while resolving legacy auto."""
    if requested_mode == "auto":
        return "full" if analysis_band in {"short", "medium"} else "scan"
    return requested_mode


def resolve_analysis_pipeline(
    word_count: int,
    requested_mode: str = "full",
) -> str:
    """Resolve the OH-Story skill without broadening a golden-three request."""
    analysis_band = resolve_analysis_band(word_count)
    execution_mode = resolve_execution_mode(requested_mode, analysis_band)
    if execution_mode == "scan":
        # story-short-analyze has no Stage 0-1 / golden-three stop.  A scan must
        # therefore use the only real OH-Story contract that owns that scope.
        return "long"
    return "long" if analysis_band == "long" else "short"


def is_token_exhaustion(text: str) -> bool:
    """Return true only for an explicit provider/runtime quota failure.

    OpenClaw result logs also contain the complete task prompt.  That prompt
    deliberately says "额度不足时暂停", so scanning the whole log with a broad
    quota regex incorrectly labels unrelated incomplete turns as exhausted.
    Evaluate small error-context windows and exclude serialized prompt fields.
    A transient rate limit is not account/token exhaustion either.
    """
    lines = str(text or "").splitlines()
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line or any(field in line for field in TOKEN_EXHAUSTION_PROMPT_FIELDS):
            continue
        window_lines = lines[max(0, index - 2) : index + 1]
        if any(
            any(field in candidate for field in TOKEN_EXHAUSTION_PROMPT_FIELDS)
            for candidate in window_lines
        ):
            continue
        window = "\n".join(window_lines)
        if not TOKEN_EXHAUSTION_ERROR_CONTEXT.search(window):
            continue
        if any(pattern.search(window) for pattern in TOKEN_EXHAUSTION_PATTERNS):
            return True
    return False


def should_auto_continue_openclaw_full(
    *,
    execution_mode: str,
    contract_errors: list[str],
    return_code: int,
    run_log_tail: str,
) -> bool:
    """Use durable artifacts, not the CLI transport exit, to drive full runs.

    OpenClaw can return a non-zero status when a completion announcement or
    transcript compaction fails after valid chapter files were already
    written.  Progress/no-progress accounting below is the safety brake; a
    transport exit alone must not turn an authorized full pipeline into a
    user-operated segmented task.
    """

    del return_code
    return bool(
        execution_mode == "full"
        and contract_errors
        and not is_token_exhaustion(run_log_tail)
    )


def read_log_tail(path: Path, max_chars: int = 12_000) -> str:
    try:
        with path.open("rb") as handle:
            size = path.stat().st_size
            handle.seek(max(0, size - max_chars * 3))
            return handle.read().decode("utf-8", errors="replace")[-max_chars:]
    except OSError:
        return ""


def current_run_log_tail(path: Path, max_chars: int = 12_000) -> str:
    tail = read_log_tail(path, max_chars=max_chars)
    marker = "] command started;"
    return tail.rsplit(marker, 1)[-1] if marker in tail else tail


def detect_progress(output_dir: Path, pipeline: str) -> tuple[int, str]:
    if pipeline == "short":
        if (output_dir / "_meta.json").exists():
            try:
                meta = json.loads((output_dir / "_meta.json").read_text(encoding="utf-8"))
                completed = set(meta.get("stages_completed") or [])
                if 6 in completed:
                    return 100, "Stage 6：综合验收"
                if completed:
                    stage = max(completed)
                    return min(95, 15 + stage * 13), f"Stage {stage} 已完成"
            except RECOVERABLE_OPERATION_ERRORS:
                pass
        if (output_dir / "写作手法.md").exists():
            return 72, "Stage 4：写作手法"
        if (output_dir / "情节节点.md").exists():
            return 42, "Stage 2：情节节点"
        if (output_dir / "拆文报告.md").exists():
            return 32, "生成拆文报告"
        return 18, "准备短篇拆文"

    progress_file = output_dir / "_progress.md"
    if progress_file.is_file():
        try:
            pipeline_stages = long_pipeline_stages(
                progress_file.read_text(encoding="utf-8", errors="replace")
            )
        except OSError:
            pipeline_stages = []
        current = next(
            (
                item
                for item in pipeline_stages
                if item.get("status") == "running"
            ),
            None,
        ) or next(
            (
                item
                for item in pipeline_stages
                if item.get("status") == "pending"
            ),
            None,
        )
        if current:
            stage_number = int(current["stage"])
            stage_progress = {
                0: 22,
                1: 40,
                2: 45,
                3: 72,
                4: 82,
                5: 92,
                6: 96,
            }[stage_number]
            if stage_number == 2:
                summary_count = len(
                    list((output_dir / "章节").glob("*_摘要.md"))
                ) if (output_dir / "章节").exists() else 0
                stage_progress = min(68, 45 + summary_count // 2)
                return (
                    stage_progress,
                    f"Stage 2：逐章摘要（{summary_count} 章）",
                )
            return stage_progress, f"Stage {stage_number}：{current['name']}"
        if pipeline_stages and all(
            item.get("status") == "completed"
            for item in pipeline_stages
        ):
            return 100, "Stage 6：文风"
    if (output_dir / "文风.md").exists():
        return 100, "Stage 6：文风"
    if (output_dir / "拆文报告.md").exists():
        return 92, "Stage 5：汇总报告"
    if (output_dir / "角色").exists() or (output_dir / "设定").exists():
        return 82, "Stage 4：设定与关系"
    if (output_dir / "剧情" / "节奏.md").exists():
        return 72, "Stage 3：聚合分析"
    summary_count = len(list((output_dir / "章节").glob("*_摘要.md"))) if (output_dir / "章节").exists() else 0
    if summary_count:
        return min(68, 45 + summary_count // 2), f"Stage 2：逐章摘要（{summary_count} 章）"
    if (output_dir / "快速预览.md").exists():
        return 40, "Stage 1：黄金三章快速预览"
    if progress_file.exists() or (output_dir / "概要.md").exists():
        return 22, "Stage 0：概要与章节边界"
    return 18, "准备长篇拆文"


def build_prompt(task: Dict[str, Any], copied_source: Path, pipeline: str) -> str:
    output_dir = Path(task["output_dir"])
    entry_mode = str(task.get("entry_mode") or task["requested_mode"])
    execution_mode = str(task.get("execution_mode") or entry_mode)
    selected_model = str(task.get("model_requested") or "").strip()
    full = execution_mode == "full"
    analysis_band = str(task.get("analysis_band") or "")
    resume_instruction = ""
    has_checkpoint = has_resume_checkpoint(output_dir, pipeline)
    if int(task.get("resume_count") or 0) > 0 or has_checkpoint:
        checkpoint = "_meta.json" if pipeline == "short" else "_progress.md"
        contract_errors = [
            str(item).strip()
            for item in (task.get("resume_contract_errors") or [])
            if str(item).strip()
        ]
        error_instruction = ""
        if contract_errors:
            error_instruction = (
                "\n后台机械验收当前仍有以下精确缺口：\n- "
                + "\n- ".join(contract_errors[:20])
                + "\n必须逐项补齐这些真实缺口；禁止通过删除章节边界、改小章节总数、"
                "虚报 Stage 完成或只修改说明文字来绕过验收。"
            )
        resume_instruction = (
            "\n这是同一拆书任务的断点续跑。为控制长篇上下文，后台可能已切换到新的 AI 会话；"
            "现有 OH-Story 产物和进度文件才是唯一续跑真值。"
            f"必须先读取输出目录中的 {checkpoint} 和已有产物，"
            "从精确断点继续；严禁清空目录、重做已完成阶段或从头拆书。"
            "恢复前还必须机械核对技能要求的交付物；如果进度文件把某阶段标为完成、"
            "但该阶段必需文件缺失，只补齐缺失文件，再从真实断点继续。"
            + error_instruction
        )
    bounded_turn_instruction = ""
    if pipeline == "long" and full:
        bounded_turn_instruction = f"""

后台长篇分批硬约束（优先级高于一次跑完的会话粒度，但不缩减最终 full 范围）：
1. 如果 Stage 2 尚未完成，本次主会话最多新增 {OPENCLAW_LONG_STAGE2_CHAPTERS_PER_TURN} 章摘要，然后必须等本批子会话全部结束、机械更新 `_progress.md` 并正常结束本次主会话；剩余章节由后台新会话继续。
2. 每个 Stage 2 子会话必须负责连续的最多 {OPENCLAW_LONG_STAGE2_CHAPTERS_PER_CHILD} 个缺失章节，逐章分别写入标准摘要文件；禁止按一章一个子会话制造大量完成回传。
3. 本次主会话不得在完成上述批次后继续派发下一批，也不得为了“本轮一次跑完”突破上限。
4. Stage 2 已完成时，本次主会话最多完成一个尚未完成的后续 Stage；完成后正常结束，由后台机械验收决定是否开启下一会话。
5. 分批结束不是暂停、失败或缩减用户授权范围；不要询问用户，也不要自行改成黄金三章模式。
"""
    optional_artifact_instruction = ""
    if pipeline == "long" and full:
        optional_artifact_instruction = """

Stage 3/4 条件产物验收规则：
15. 剧情单元、角色档案、角色关系、世界观、势力不是每本小说都必然存在。原文中存在该类可识别内容时，必须按 story-long-analyze 规范分别落盘；聚合 README/报告已经列出的具体剧情单元，必须有同名独立文件，不能只留清单。
16. 经完整原文审计确认某一类别确实不存在时，不得编造内容凑数；必须在输出根目录写入 `_artifact_manifest.json`，使用 `schema_version: 1`，并在 `categories` 中仅为该类别声明 `status: "not_applicable"`、`count: 0`、不少于 8 字的 `reason` 和至少一条可复核的 `evidence`。已有实体文件或清单与 `not_applicable` 自相矛盾时，验收不会放行。
"""
    if pipeline == "short":
        band_instruction = (
            "作品不足 30000 字，属于短篇档"
            if analysis_band == "short"
            else "作品为 30000 至 150000 字（含边界）的中篇档"
        )
        instruction = (
            "必须真正调用 $story-short-analyze 技能完成 Stage 2-6 全量拆文，"
            "不得用通用提示词模拟技能。"
            f"用户已明确授权：{band_instruction}，完整拆书统一使用该技能，"
            "该规则覆盖技能默认的灰区篇幅建议；不要停下来向用户提问。"
        )
    elif full:
        instruction = (
            "必须真正调用 $story-long-analyze 技能做一次完整拆解，"
            "不得用通用提示词模拟技能。"
            "用户已明确要求完整拆解/一次跑完，必须生成快速预览但不要在 Stage 1 停下来提问，"
            "直接续跑 Stage 2-6。"
        )
    else:
        instruction = (
            "必须真正调用 $story-long-analyze 技能执行 Stage 0-1"
            "（概要、章节边界、黄金三章和快速预览），不得用通用提示词模拟技能，"
            "到 paused_after_stage1 停靠即可，不要在终端等待用户回答。"
            "本次用户只授权黄金三章，严禁继续 Stage 2-6，"
            "严禁把 requested_mode/entry_mode 扩大为 full。"
        )
    return f"""你正在执行 Webnovel Writer 的“全局电子书库”本地分析任务。

{instruction}{resume_instruction}{bounded_turn_instruction}

硬性路径边界：
1. 用户合法持有并授权分析该虚构作品。
2. 原文只读软链接：{copied_source}
3. 唯一输出目录：{output_dir}
4. 发起任务的小说项目（仅用于题材上下文）：{task['project_root']}
5. 只能写入上述唯一输出目录和全局任务记录；不得修改书籍来源目录及 catalog.sqlite3。
6. 输出目录位于全局拆书库，覆盖技能默认的项目级“拆文库/{{书名}}”路径。
7. 严格遵循所选 oh-story 技能的目录结构、断点恢复、事实可溯源和验收规范。
8. 拆解成果属于全局只读分析资产，所有新旧小说项目均可召回；禁止写入任何项目私有设定。
9. 用户任务范围：{entry_mode}；实际技能交付范围：{execution_mode}。前者是授权边界，不得扩大。
10. 本任务唯一授权 AI 模型：{selected_model}。主会话、章节提取、块级聚合和任何子会话都必须使用该模型。
11. 调用 sessions_spawn 时必须显式传入 `model: "{selected_model}"`；禁止省略 model，禁止改用默认模型，禁止使用任何 fallback。
12. 如果该模型暂时不可用或额度不足，立即停止并保留断点；严禁回落到 ChatGPT、Claude、Gemini 或其他模型。
13. 技能文件的唯一允许入口是 `/opt/oohstory-agent-skills/{'story-short-analyze' if pipeline == 'short' else 'story-long-analyze'}/SKILL.md`；禁止用 `find /`、`find /mnt` 或其他全盘扫描寻找技能。
14. 文件发现只能限定在原文只读软链接、唯一输出目录、上述精确技能目录和技能明确引用的资源内；禁止遍历工作区、整座电子书库、根目录或其他挂载点。
{optional_artifact_instruction}

作品：{task['title']}
作者：{task.get('author') or '未知'}
书库分类：{task.get('category') or '未分类'}
"""


def codex_logged_in(codex: str) -> bool:
    try:
        result = subprocess.run(
            [codex, "login", "status"],
            cwd="/",
            env=codex_env(),
            capture_output=True,
            text=True,
            timeout=15,
        )
        text = f"{result.stdout}\n{result.stderr}".lower()
        return result.returncode == 0 and "logged in" in text and "not logged in" not in text
    except RECOVERABLE_OPERATION_ERRORS:
        return False


def build_runner_command(
    task: Dict[str, Any],
    workspace_root: Path,
    prompt_path: Path,
) -> tuple[list[str], str, bool]:
    requested = str(task.get("runner_requested") or "auto")
    model = str(task.get("model_requested") or "")
    reasoning = str(task.get("reasoning_requested") or "default").strip().lower()
    ai_session_id = str(
        task.get("ai_session_id") or f"library-{task['id']}"
    )
    if requested == "openclaw":
        openclaw = find_cli("openclaw")
        if not openclaw:
            raise RuntimeError("所选 OpenClaw 当前不可用")
        agent = ensure_openclaw_task_agent(
            openclaw,
            model,
            workspace_root,
        )
        task["openclaw_agent_id"] = agent["agent_id"]
        task["openclaw_agent_dir"] = agent["agent_dir"]
        task["openclaw_session_store"] = agent["session_store"]
        task["model_contract"] = {
            "expected": model,
            "fallbacks_disabled": True,
            "agent_id": agent["agent_id"],
        }
        command = [
            openclaw,
            "agent",
            "--agent",
            agent["agent_id"],
            "--session-id",
            ai_session_id,
            "--message-file",
            str(prompt_path),
            "--timeout",
            "86400",
            "--json",
        ]
        if model and model != "default":
            command.extend(["--model", model])
        if reasoning and reasoning != "default":
            command.extend(["--thinking", reasoning])
        return command, "openclaw", False

    if requested == "claude":
        claude = find_cli("claude")
        if not claude:
            raise RuntimeError("所选 Claude Code 当前不可用")
        plugin_root = Path(__file__).resolve().parents[2] / "plugins" / "oh-story-claudecode"
        command = [
            claude,
            "-p",
            "--permission-mode",
            "bypassPermissions",
            "--output-format",
            "stream-json",
            "--verbose",
            "--name",
            ai_session_id,
            "--plugin-dir",
            str(plugin_root),
            "--add-dir",
            str(Path(task["output_dir"])),
        ]
        if model and model != "default":
            command.extend(["--model", model])
        if reasoning and reasoning != "default":
            command.extend(["--effort", reasoning])
        return command, "claude", True

    if requested == "opencode":
        opencode = find_cli("opencode")
        if not opencode:
            raise RuntimeError("所选 OpenCode 当前未安装")
        command = [opencode, "run", "--format", "json"]
        if model and model != "default":
            command.extend(["--model", model])
        return command, "opencode", True

    codex = ensure_codex_cli(auto_install=True)
    if codex_logged_in(codex):
        return (
            [
                codex,
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                "workspace-write",
                "--ephemeral",
                "--color",
                "never",
                "--json",
                "-C",
                str(workspace_root),
                "-o",
                task["last_message_path"],
                "-",
            ],
            "codex",
            True,
        )

    openclaw = find_cli("openclaw")
    if openclaw:
        return (
            [
                openclaw,
                "agent",
                "--agent",
                "main",
                "--session-id",
                ai_session_id,
                "--message-file",
                str(prompt_path),
                "--thinking",
                "high",
                "--timeout",
                "86400",
                "--json",
            ],
            "openclaw",
            False,
        )
    raise RuntimeError("Codex CLI 未登录，且 OpenClaw Gateway 不可用")


def delete_completed_openclaw_session(
    task: Dict[str, Any],
    runner: str,
) -> None:
    """Best-effort removal of a successful library analysis session."""
    if runner != "openclaw":
        return
    session_id = str(
        task.get("ai_session_id") or f"library-{task['id']}"
    ).strip()
    if not session_id:
        return
    agent_id = str(task.get("openclaw_agent_id") or "main").strip()
    session_key = f"agent:{agent_id}:explicit:{session_id}"
    openclaw = find_cli("openclaw")
    if not openclaw:
        task["ai_session_cleanup"] = "error"
        task["ai_session_cleanup_error"] = "OpenClaw CLI 不可用"
        return
    params = json.dumps(
        {
            "key": session_key,
            "agentId": agent_id,
            "deleteTranscript": True,
            "emitLifecycleHooks": False,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        result = subprocess.run(
            [
                openclaw,
                "gateway",
                "call",
                "sessions.delete",
                "--params",
                params,
                "--timeout",
                "15000",
                "--json",
            ],
            cwd="/",
            env=cli_runtime_env(),
            capture_output=True,
            text=True,
            timeout=25,
        )
    except RECOVERABLE_OPERATION_ERRORS as exc:
        task["ai_session_cleanup"] = "error"
        task["ai_session_cleanup_error"] = str(exc)[:500]
        return
    if result.returncode == 0:
        task["ai_session_cleanup"] = "deleted"
        task["ai_session_deleted_at"] = now()
        task.pop("ai_session_cleanup_error", None)
        return
    detail = (result.stderr or result.stdout or "会话删除失败").strip()
    task["ai_session_cleanup"] = "error"
    task["ai_session_cleanup_error"] = detail[:500]


def openclaw_model_contract_mismatches(task: Dict[str, Any]) -> list[Dict[str, str]]:
    """Inspect the dedicated agent store, including all spawned descendants."""
    expected = str(task.get("model_requested") or "").strip()
    store_path = Path(str(task.get("openclaw_session_store") or "")).expanduser()
    agent_id = str(task.get("openclaw_agent_id") or "").strip()
    session_id = str(task.get("ai_session_id") or "").strip()
    if not expected or not store_path.is_file() or not agent_id or not session_id:
        return []
    try:
        store = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(store, dict):
        return []
    root_key = f"agent:{agent_id}:explicit:{session_id}"
    lineage = {root_key}
    changed = True
    while changed:
        changed = False
        for key, entry in store.items():
            if (
                isinstance(entry, dict)
                and str(entry.get("spawnedBy") or "") in lineage
                and key not in lineage
            ):
                lineage.add(key)
                changed = True
    mismatches: list[Dict[str, str]] = []
    for key in lineage:
        entry = store.get(key)
        if not isinstance(entry, dict):
            continue
        provider = str(
            entry.get("modelProvider")
            or entry.get("providerOverride")
            or ""
        ).strip()
        model_id = str(
            entry.get("model")
            or entry.get("modelOverride")
            or ""
        ).strip()
        if not provider or not model_id:
            continue
        actual = f"{provider}/{model_id}"
        if actual != expected:
            mismatches.append(
                {"session_key": key, "expected": expected, "actual": actual}
            )
    return mismatches


def task_openclaw_lineage_state(task: Dict[str, Any]) -> Dict[str, Any]:
    return openclaw_session_lineage_state(
        agent_id=str(task.get("openclaw_agent_id") or ""),
        session_id=str(task.get("ai_session_id") or ""),
        session_store=str(task.get("openclaw_session_store") or ""),
    )


def abort_openclaw_session(task: Dict[str, Any], session_key: str) -> None:
    openclaw = find_cli("openclaw")
    agent_id = str(task.get("openclaw_agent_id") or "").strip()
    if not openclaw or not agent_id or not session_key:
        return
    params = json.dumps(
        {"key": session_key, "agentId": agent_id},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    subprocess.run(
        [
            openclaw,
            "gateway",
            "call",
            "sessions.abort",
            "--params",
            params,
            "--timeout",
            "15000",
            "--json",
        ],
        cwd="/",
        env=cli_runtime_env(),
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )


def supervise_openclaw_lineage(
    task_file: Path,
    task: Dict[str, Any],
    output_dir: Path,
    pipeline: str,
    execution_mode: str,
    log_path: Path,
) -> None:
    """Wait across ``sessions_yield`` until the whole OpenClaw tree is done.

    The parent can finish one CLI turn with return code 0 while children keep
    working.  Final OH-Story validation is legal only after the lineage is
    quiescent, with a short grace window for the child-result continuation
    event to wake the parent.
    """

    quiescent_since: float | None = None
    last_model_check = 0.0
    last_write = 0.0
    last_active_count: int | None = None
    while True:
        current = time.monotonic()
        lineage = task_openclaw_lineage_state(task)
        active_count = len(lineage.get("active_keys") or [])
        contract_errors = validate_output_contract(
            output_dir,
            pipeline,
            execution_mode,
        )

        if current - last_model_check >= 1:
            mismatches = openclaw_model_contract_mismatches(task)
            if mismatches:
                for mismatch in mismatches:
                    abort_openclaw_session(task, mismatch["session_key"])
                abort_openclaw_session(
                    task,
                    str(lineage.get("root_key") or ""),
                )
                task["model_contract"] = {
                    **dict(task.get("model_contract") or {}),
                    "ok": False,
                    "checked_at": now(),
                    "mismatches": mismatches,
                }
                write_task(task_file, task)
                raise ModelContractError(
                    "检测到子会话使用了未授权模型，任务已立即中止："
                    + "；".join(
                        f"{item['actual']} != {item['expected']}"
                        for item in mismatches[:5]
                    )
                )
            last_model_check = current

        if lineage.get("active"):
            quiescent_since = None
        elif not contract_errors:
            # A complete on-disk contract is stronger than a delayed registry
            # update and does not need the continuation grace period.
            break
        elif quiescent_since is None:
            quiescent_since = current
        elif current - quiescent_since >= OPENCLAW_QUIESCENCE_GRACE_SECONDS:
            break

        if (
            current - last_write >= 2
            or active_count != last_active_count
        ):
            progress, stage = detect_progress(output_dir, pipeline)
            task["progress"] = max(int(task.get("progress") or 0), progress)
            task["current_stage"] = stage
            sync_pipeline_stage(task, stage)
            task["last_activity_at"] = now()
            task["message"] = (
                f"OpenClaw 主会话已让出控制权，"
                f"正在等待 {active_count} 个同模型会话完成并回传"
                if lineage.get("active")
                else "AI 会话已静默，等待最后一轮子会话回传确认"
            )
            task["openclaw_lineage"] = {
                "active": bool(lineage.get("active")),
                "root_status": lineage.get("root_status") or "",
                "session_count": len(lineage.get("lineage_keys") or []),
                "active_count": active_count,
                "checked_at": now(),
            }
            write_task(task_file, task)
            last_write = current
            last_active_count = active_count
        time.sleep(2)

    with log_path.open("a", encoding="utf-8") as log:
        log.write(
            f"[{now()}] OpenClaw lineage quiescent; "
            f"contract_ready={not bool(contract_errors)}\n"
        )


def run(task_file: Path) -> int:
    task = read_task(task_file)
    log_path = Path(task["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    project_root = Path(task["project_root"]).resolve()
    output_dir = Path(task["output_dir"]).resolve()
    source_path = Path(task["source_path"]).resolve()
    library_books_root = ElectronicLibraryService().books_root.resolve()
    global_deconstruction_root = (
        ElectronicLibraryService().global_deconstruction_root.resolve()
    )
    global_task_root = ElectronicLibraryService().global_task_root.resolve()
    workspace_root = Path(__file__).resolve().parents[3]

    try:
        task["status"] = "running"
        task["pid"] = os.getpid()
        task["can_resume"] = False
        task.pop("pause_reason", None)
        task.pop("finished_at", None)
        task.pop("contract_validation", None)
        task["started_at"] = task.get("started_at") or now()
        task["current_stage"] = "验证路径边界"
        task["progress"] = 3
        set_step(task, "validate", "running")
        write_task(task_file, task)

        if not project_root.exists() or not (project_root / ".webnovel").exists():
            raise ValueError("当前小说项目无效")
        if not within(task_file, global_task_root):
            raise ValueError("任务文件不在全局拆书库任务目录")
        if not within(source_path, library_books_root):
            raise ValueError("来源文件不在电子书库书籍目录")
        if not within(output_dir, global_deconstruction_root):
            raise ValueError("输出目录越过全局拆书库边界")
        if not source_path.is_file():
            raise FileNotFoundError("来源作品文件不存在")

        set_step(task, "validate", "completed")
        set_step(task, "copy", "running")
        task["current_stage"] = "链接电子书库只读原文"
        task["progress"] = 7
        write_task(task_file, task)

        original_dir = output_dir / "原文"
        original_dir.mkdir(parents=True, exist_ok=True)
        copied_source = original_dir / "原文.txt"
        ensure_readonly_source_link(source_path, copied_source)
        if copied_source.stat().st_size <= 0:
            raise ValueError("全局拆书库原文软链接为空")
        set_step(task, "copy", "completed")

        set_step(task, "route", "running")
        task["current_stage"] = "按字数选择 oh-story 管道"
        task["progress"] = 12
        write_task(task_file, task)
        word_count = compact_word_count(copied_source)
        entry_mode = str(task.get("entry_mode") or task["requested_mode"])
        task["entry_mode"] = entry_mode
        task["requested_mode"] = entry_mode
        analysis_band = str(
            task.get("analysis_band")
            or resolve_analysis_band(word_count)
        )
        execution_mode = (
            "scan"
            if entry_mode == "scan"
            else str(
                task.get("execution_mode")
                or resolve_execution_mode(entry_mode, analysis_band)
            )
        )
        pipeline = (
            "long"
            if entry_mode == "scan"
            else str(task.get("resolved_pipeline") or "").strip()
        )
        if pipeline not in {"short", "long"}:
            pipeline = resolve_analysis_pipeline(word_count, entry_mode)
        task["word_count"] = word_count
        task["analysis_band"] = analysis_band
        task["execution_mode"] = execution_mode
        task["automatic_full_pipeline"] = bool(
            entry_mode == "full" and execution_mode == "full"
        )
        task["resolved_pipeline"] = pipeline
        task["skill"] = "story-short-analyze" if pipeline == "short" else "story-long-analyze"
        if not task.get("pipeline_stages"):
            initialize_pipeline_stages(task, pipeline)
        set_step(task, "route", "completed")
        set_step(task, "analyze", "running")
        task["current_stage"] = "启动 oh-story"
        task["progress"] = 16
        if pipeline == "long":
            preserve_long_chapter_boundaries(output_dir)
        turn_start_contract_errors = validate_output_contract(
            output_dir,
            pipeline,
            execution_mode,
        )
        task["resume_contract_errors"] = turn_start_contract_errors
        write_task(task_file, task)

        prompt = build_prompt(task, copied_source, pipeline)
        prompt_path = task_file.with_suffix(".request.md")
        prompt_path.write_text(prompt, encoding="utf-8")
        command, runner, prompt_via_stdin = build_runner_command(
            task, workspace_root, prompt_path
        )
        task["runner"] = runner
        runner_messages = {
            "codex": "正在通过 Codex CLI 执行 oh-story",
            "openclaw": "正在通过独立 OpenClaw 会话执行 oh-story",
            "claude": "正在通过独立 Claude Code 会话执行 oh-story",
            "opencode": "正在通过独立 OpenCode 会话执行 oh-story",
        }
        task["message"] = runner_messages.get(runner, "正在执行 oh-story")
        write_task(task_file, task)
        adopt_active_lineage = bool(
            runner == "openclaw"
            and task_openclaw_lineage_state(task).get("active")
        )
        with log_path.open("a", encoding="utf-8") as log:
            if adopt_active_lineage:
                task["codex_pid"] = None
                task["message"] = (
                    "已接管仍在运行的原 OpenClaw 会话树，"
                    "不会重复提交拆书请求"
                )
                write_task(task_file, task)
                log.write(
                    f"\n[{now()}] adopted active OpenClaw lineage; "
                    f"skill={task['skill']}\n"
                )
                return_code = 0
            else:
                log.write(
                    f"\n[{now()}] command started; "
                    f"skill={task['skill']}; runner={runner}\n"
                )
                process = subprocess.Popen(
                    command,
                    cwd=str(workspace_root),
                    env=codex_env() if runner == "codex" else cli_runtime_env(),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                task["codex_pid"] = process.pid
                write_task(task_file, task)
                if prompt_via_stdin:
                    assert process.stdin is not None
                    process.stdin.write(prompt)
                    process.stdin.close()
                elif process.stdin is not None:
                    process.stdin.close()
                assert process.stdout is not None
                last_refresh = 0.0
                last_model_check = 0.0
                while process.poll() is None:
                    readable, _, _ = select.select([process.stdout], [], [], 2.0)
                    if readable:
                        line = process.stdout.readline()
                        if line:
                            log.write(line)
                            log.flush()
                    current = time.monotonic()
                    if current - last_refresh >= 2:
                        progress, stage = detect_progress(output_dir, pipeline)
                        task["progress"] = max(task.get("progress", 0), progress)
                        task["current_stage"] = stage
                        sync_pipeline_stage(task, stage)
                        task["last_activity_at"] = now()
                        write_task(task_file, task)
                        last_refresh = current
                    if runner == "openclaw" and current - last_model_check >= 1:
                        mismatches = openclaw_model_contract_mismatches(task)
                        if mismatches:
                            for mismatch in mismatches:
                                abort_openclaw_session(
                                    task,
                                    mismatch["session_key"],
                                )
                            root_key = (
                                f"agent:{task['openclaw_agent_id']}:explicit:"
                                f"{task['ai_session_id']}"
                            )
                            abort_openclaw_session(task, root_key)
                            process.terminate()
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait(timeout=5)
                            task["model_contract"] = {
                                **dict(task.get("model_contract") or {}),
                                "ok": False,
                                "checked_at": now(),
                                "mismatches": mismatches,
                            }
                            write_task(task_file, task)
                            raise ModelContractError(
                                "检测到子会话使用了未授权模型，任务已立即中止："
                                + "；".join(
                                    f"{item['actual']} != {item['expected']}"
                                    for item in mismatches[:5]
                                )
                            )
                        last_model_check = current
                remainder = process.stdout.read()
                if remainder:
                    log.write(remainder)
                    log.flush()
                return_code = process.wait()
                task["codex_pid"] = None
                write_task(task_file, task)
                log.write(
                    f"[{now()}] command finished; return_code={return_code}\n"
                )

        if runner == "openclaw":
            supervise_openclaw_lineage(
                task_file,
                task,
                output_dir,
                pipeline,
                execution_mode,
                log_path,
            )
            if pipeline == "long":
                preserve_long_chapter_boundaries(output_dir)
            mismatches = openclaw_model_contract_mismatches(task)
            task["model_contract"] = {
                **dict(task.get("model_contract") or {}),
                "ok": not mismatches,
                "checked_at": now(),
                "mismatches": mismatches,
            }
            write_task(task_file, task)
            if mismatches:
                raise ModelContractError(
                    "AI 会话模型验收失败："
                    + "；".join(
                        f"{item['actual']} != {item['expected']}"
                        for item in mismatches[:5]
                    )
                )
            intermediate_contract_errors = validate_output_contract(
                output_dir,
                pipeline,
                execution_mode,
            )
            if should_auto_continue_openclaw_full(
                execution_mode=execution_mode,
                contract_errors=intermediate_contract_errors,
                return_code=return_code,
                run_log_tail=current_run_log_tail(log_path),
            ):
                # A model changing prose or timestamps is not pipeline
                # progress.  Only a smaller/different mechanical error set can
                # justify another automatic turn; this prevents hundreds of
                # retries when a session keeps declaring itself complete.
                made_progress = contract_errors_improved(
                    turn_start_contract_errors,
                    intermediate_contract_errors,
                )
                no_progress_turns = (
                    0
                    if made_progress
                    else int(task.get("auto_no_progress_turns") or 0) + 1
                )
                continuation_count = (
                    int(task.get("auto_continue_count") or 0) + 1
                )
                task["auto_no_progress_turns"] = no_progress_turns
                task["auto_continue_count"] = continuation_count
                task["last_turn_made_progress"] = made_progress
                if (
                    no_progress_turns < OPENCLAW_MAX_NO_PROGRESS_TURNS
                    and continuation_count
                    <= OPENCLAW_MAX_AUTOMATIC_CONTINUATIONS
                ):
                    task["resume_count"] = int(
                        task.get("resume_count") or 0
                    ) + 1
                    task["status"] = "running"
                    task["can_resume"] = False
                    task["codex_pid"] = None
                    task["current_stage"] = (
                        detect_progress(output_dir, pipeline)[1]
                    )
                    previous_session, next_session = rotate_openclaw_session(
                        task,
                    )
                    task["message"] = (
                        "本批子会话已完成，OH-Story 合同尚未齐全；"
                        "正在切换新的 AI 主会话并从现有断点自动续跑下一批"
                    )
                    write_task(task_file, task)
                    with log_path.open("a", encoding="utf-8") as log:
                        log.write(
                            f"[{now()}] automatic bounded-session continuation "
                            f"{continuation_count}; made_progress={made_progress}; "
                            f"return_code={return_code}; "
                            f"previous_session={previous_session}; "
                            f"next_session={next_session}\n"
                        )
                    return run(task_file)

        if pipeline == "long":
            preserve_long_chapter_boundaries(output_dir)
        set_step(task, "analyze", "completed" if return_code == 0 else "error")
        set_step(task, "verify", "running")
        task["current_stage"] = "校验项目内产物"
        task["progress"] = max(task.get("progress", 0), 94)
        write_task(task_file, task)

        contract_errors = validate_output_contract(
            output_dir,
            pipeline,
            execution_mode,
        )
        task["contract_validation"] = {
            "ok": not contract_errors,
            "errors": contract_errors,
            "checked_at": now(),
            "skill": task["skill"],
        }
        write_task(task_file, task)
        if contract_errors:
            if pipeline == "long":
                refresh_pipeline_stages_from_artifacts(
                    task,
                    output_dir,
                    pipeline,
                )
                task["pipeline_stages"] = project_long_contract_failures(
                    task.get("pipeline_stages") or [],
                    contract_errors,
                )
                write_task(task_file, task)
            raise ArtifactValidationError(
                "OH-Story 产物验收未通过："
                + "；".join(contract_errors[:8])
            )

        refresh_pipeline_stages_from_artifacts(task, output_dir, pipeline)

        if pipeline == "long" and execution_mode != "full":
            task["status"] = "paused"
            task["progress"] = 40
            task["current_stage"] = "Stage 1 已停靠"
            task["message"] = "黄金三章扫描完成，可继续全量拆书"
        elif pipeline == "short":
            task["status"] = "completed"
            task["progress"] = 100
            task["current_stage"] = "短篇拆书完成"
            task["message"] = "oh-story 短篇拆书产物已写入全局拆书库"
        elif pipeline == "long":
            task["status"] = "completed"
            task["progress"] = 100
            task["current_stage"] = "长篇拆书完成"
            task["message"] = "oh-story 长篇拆书产物已写入全局拆书库"
        elif return_code != 0:
            raise RuntimeError(f"oh-story 任务退出码：{return_code}")
        else:
            raise RuntimeError("oh-story 已退出，但未检测到完整交付物")

        set_step(task, "verify", "completed")
        task["can_resume"] = False
        task["pid"] = None
        task["codex_pid"] = None
        task.pop("resume_contract_errors", None)
        task.pop("pause_reason", None)
        task["finished_at"] = now()
        write_task(task_file, task)
        delete_completed_openclaw_session(task, runner)
        write_task(task_file, task)
        return 0
    except RECOVERABLE_OPERATION_ERRORS as exc:
        task = read_task(task_file)
        failure_context = f"{exc}\n{current_run_log_tail(log_path)}"
        token_exhausted = is_token_exhaustion(failure_context)
        pipeline = str(task.get("resolved_pipeline") or "long")
        checkpoint_exists = has_resume_checkpoint(output_dir, pipeline)
        validation_failed = isinstance(exc, ArtifactValidationError)
        model_contract_failed = isinstance(exc, ModelContractError)
        recoverable = bool(
            str(task.get("ai_session_id") or "").strip()
            and (
                token_exhausted
                or validation_failed
                or model_contract_failed
                or checkpoint_exists
            )
        )
        task["status"] = "paused" if recoverable else "error"
        task["can_resume"] = recoverable
        task["pause_reason"] = (
            "token_exhausted"
            if token_exhausted
            else "artifact_validation_failed"
            if validation_failed
            else "model_contract_failed"
            if model_contract_failed
            else "task_interrupted"
            if recoverable
            else "task_error"
        )
        if token_exhausted:
            task["message"] = (
                "AI Token/额度已用尽；已保留当前会话和全部拆书断点，"
                "额度恢复后可点击“继续拆书（原会话）”"
            )
            task["current_stage"] = "Token/额度已用尽，等待继续"
        elif validation_failed:
            task["message"] = (
                "AI 已退出，但 OH-Story 交付物验收未通过；"
                "已保留全部断点，可点击“继续拆书（新会话续断点）”补齐"
            )
            task["current_stage"] = "产物验收未通过，等待新会话续断点"
            task["resume_strategy"] = "adopt_checkpoint"
            task["session_rotation_required"] = True
        elif model_contract_failed:
            task["message"] = (
                "检测到 AI 子会话偏离前端选择的模型，已立即停止；"
                "现有 OH-Story 产物与断点均已保留"
            )
            task["current_stage"] = "AI 模型契约违规，任务已停止"
        elif recoverable:
            task["message"] = (
                "任务意外中断；已保留原 AI 会话和 OH-Story 断点，"
                "可点击“继续拆书（原会话）”"
            )
            task["current_stage"] = "任务中断，等待原会话继续"
        else:
            task["message"] = str(exc)[:800]
            task["current_stage"] = "任务失败"
        task["finished_at"] = now()
        task["pid"] = None
        task["codex_pid"] = None
        for step in task.get("steps", []):
            if step.get("status") == "running":
                step["status"] = "error"
                step["finished_at"] = now()
        write_task(task_file, task)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"[{now()}] ERROR: {exc}\n")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-file", required=True)
    args = parser.parse_args()
    return run(Path(args.task_file).expanduser().resolve())


if __name__ == "__main__":
    raise SystemExit(main())
