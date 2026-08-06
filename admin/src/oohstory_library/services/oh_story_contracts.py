"""Mechanical acceptance checks for OH-Story analysis artifacts.

These checks intentionally validate only facts that can be proven from files on
disk.  A worker may delete its AI session only after the selected skill's
contract passes here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


SHORT_STAGES = {2, 3, 4, 5, 6}
LONG_STAGES = set(range(7))
LONG_STAGE_NAMES = {
    0: "概要提取 + 章节边界",
    1: "黄金三章",
    2: "逐章摘要",
    3: "聚合分析",
    4: "设定 + 关系",
    5: "汇总报告",
    6: "文风",
}
SHORT_REVERSAL_TYPES = {
    "视角反转",
    "身份反转",
    "动机反转",
    "时间线反转",
    "信息反转",
    "认知反转",
    "无反转",
}
LONG_PLOT_RESERVED_FILES = {
    "README.md",
    "故事线.md",
    "节奏.md",
    "情绪模块.md",
    "散落情节.md",
}
NO_INDEPENDENT_FORCE_MARKERS = (
    "无核心势力",
    "无明显势力",
    "无独立势力",
    "不设独立势力",
)
LONG_OPTIONAL_ARTIFACT_CATEGORIES = {
    "plot_units",
    "characters",
    "relationships",
    "worldbuilding",
    "factions",
}


def _nonempty(path: Path, minimum_bytes: int = 1) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= minimum_bytes
    except OSError:
        return False


def _has_nonempty_original(output_dir: Path) -> bool:
    original_dir = output_dir / "原文"
    if not original_dir.is_dir():
        return False
    return any(
        _nonempty(path)
        for path in original_dir.iterdir()
        if path.is_file() and not path.name.endswith(".bak")
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _nonempty_markdown_files(
    directory: Path,
    *,
    minimum_bytes: int,
    excluded_names: set[str] | None = None,
) -> list[Path]:
    excluded = excluded_names or set()
    if not directory.is_dir():
        return []
    return sorted(
        (
            path
            for path in directory.iterdir()
            if (
                path.suffix.casefold() == ".md"
                and not path.name.startswith("_")
                and path.name not in excluded
                and _nonempty(path, minimum_bytes)
            )
        ),
        key=lambda path: path.name,
    )


def read_long_artifact_manifest(output_dir: Path) -> dict[str, Any]:
    path = output_dir / "_artifact_manifest.json"
    if not _nonempty(path, 20):
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _valid_not_applicable_declaration(
    manifest: dict[str, Any],
    category: str,
) -> bool:
    """Allow genuinely absent story material only with an auditable decision."""

    categories = manifest.get("categories")
    if (
        str(manifest.get("schema_version") or "").strip() != "1"
        or not isinstance(categories, dict)
        or category not in LONG_OPTIONAL_ARTIFACT_CATEGORIES
    ):
        return False
    item = categories.get(category)
    if not isinstance(item, dict):
        return False
    if str(item.get("status") or "").strip() != "not_applicable":
        return False
    if "count" not in item or str(item.get("count")).strip() != "0":
        return False
    reason = str(item.get("reason") or "").strip()
    evidence = item.get("evidence")
    return bool(
        len(reason) >= 8
        and isinstance(evidence, list)
        and any(len(str(value).strip()) >= 4 for value in evidence)
    )


def _normalized_plot_title(value: str) -> str:
    text = str(value or "").strip().strip("`*_ ")
    if text.casefold().endswith(".md"):
        text = text[:-3]
    return re.sub(r"\s+", "", text.replace("/", "／"))


def _indexed_plot_titles(readme_text: str) -> list[str]:
    """Read the plot-unit inventory from the README contract section."""

    section = re.search(
        r"(?ms)^##[^\n]*剧情单元清单[^\n]*\n(.*?)(?=^##\s|\Z)",
        readme_text,
    )
    if not section:
        return []
    title_column: int | None = None
    titles: list[str] = []
    for raw_line in section.group(1).splitlines():
        if not raw_line.lstrip().startswith("|"):
            continue
        cells = [
            part.strip()
            for part in raw_line.strip().strip("|").split("|")
        ]
        if title_column is None:
            for index, cell in enumerate(cells):
                if cell in {"剧情单元", "单元标题"}:
                    title_column = index
                    break
            continue
        if title_column >= len(cells):
            continue
        title = cells[title_column].strip().strip("`*_ ")
        if not title or re.fullmatch(r"[-:： ]+", title):
            continue
        normalized = _normalized_plot_title(title)
        if normalized and normalized not in {
            _normalized_plot_title(value) for value in titles
        }:
            titles.append(title)
    return titles


def _missing_markers(text: str, markers: tuple[str, ...]) -> list[str]:
    return [marker for marker in markers if marker not in text]


def read_short_meta(output_dir: Path) -> dict[str, Any]:
    path = output_dir / "_meta.json"
    if not _nonempty(path):
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def short_pipeline_stages(meta: dict[str, Any]) -> list[dict[str, Any]]:
    names = {
        2: "结构与情节节点",
        3: "情感线与爆点",
        4: "反转与写作手法",
        5: "人物与开头结尾",
        6: "综合评估与验收",
    }
    completed = {
        int(value)
        for value in (meta.get("stages_completed") or [])
        if str(value).isdigit()
    }
    in_progress = meta.get("last_stage_in_progress")
    try:
        current = int(in_progress) if in_progress is not None else None
    except (TypeError, ValueError):
        current = None
    return [
        {
            "stage": stage,
            "name": names[stage],
            "status": (
                "completed"
                if stage in completed
                else "running"
                if stage == current
                else "pending"
            ),
            "status_text": (
                "✅ 完成"
                if stage in completed
                else "🔄 进行中"
                if stage == current
                else "⬜ 未开始"
            ),
        }
        for stage in sorted(names)
    ]


def validate_short_output_contract(output_dir: Path) -> list[str]:
    """Validate the story-short-analyze v3 output contract."""
    errors: list[str] = []
    required_files = {
        "拆文报告.md": 800,
        "情节节点.md": 300,
        "写作手法.md": 300,
        "_meta.json": 20,
    }
    if not _has_nonempty_original(output_dir):
        errors.append("原文/ 缺少非空原文备份")
    for name, minimum in required_files.items():
        if not _nonempty(output_dir / name, minimum):
            errors.append(f"{name} 缺失或内容过短")

    meta = read_short_meta(output_dir)
    if not meta:
        errors.append("_meta.json 无法解析")
        return errors
    completed = {
        int(value)
        for value in (meta.get("stages_completed") or [])
        if str(value).isdigit()
    }
    missing_stages = sorted(SHORT_STAGES - completed)
    if missing_stages:
        errors.append(f"_meta.json 缺少已完成 Stage：{missing_stages}")
    if meta.get("last_stage_in_progress") is not None:
        errors.append("_meta.json 仍有 last_stage_in_progress")
    if int(meta.get("word_count") or 0) <= 0:
        errors.append("_meta.json.word_count 无效")
    if not str(meta.get("genre_detected") or "").strip():
        errors.append("_meta.json.genre_detected 为空")

    counts = meta.get("structure_counts")
    if not isinstance(counts, dict):
        errors.append("_meta.json.structure_counts 缺失")
        return errors
    thresholds = {
        "beats": 4,
        "hooks": 3,
        "character_archetypes": 2,
        "reusable_structures": 3,
    }
    for key, minimum in thresholds.items():
        if int(counts.get(key) or 0) < minimum:
            errors.append(f"structure_counts.{key} 低于 {minimum}")
    reversal_type = str(counts.get("reversal_type") or "").strip()
    if reversal_type not in SHORT_REVERSAL_TYPES:
        errors.append("structure_counts.reversal_type 不在允许枚举")
    if (
        reversal_type != "无反转"
        and int(counts.get("setup_clues") or 0) < 3
    ):
        errors.append("structure_counts.setup_clues 低于 3")
    return errors


def _normalize_long_stage_status(value: str) -> str | None:
    text = str(value or "").strip()
    folded = text.casefold().replace("-", "_").replace(" ", "_")
    if (
        folded == "completed"
        or "✅" in text
        or "完成" in text
    ):
        return "completed"
    if (
        folded in {"running", "in_progress", "inprogress"}
        or "🔄" in text
        or "进行中" in text
    ):
        return "running"
    if (
        folded in {"error", "failed", "failure"}
        or "⚠" in text
        or "失败" in text
    ):
        return "error"
    if (
        folded in {"pending", "not_started", "notstarted"}
        or "⬜" in text
        or "未开始" in text
        or "等待" in text
    ):
        return "pending"
    return None


def _long_stage_token(value: str) -> tuple[int, str, bool] | None:
    """Parse the stage cell emitted by the real OH-Story skill.

    The skill accepts both ``0`` and ``Stage 0`` and may expose Stage 4 as
    either one row or the three explicit ``4a/4b/4c`` rows.  Descriptive text
    after the token is allowed.  A combined ``4a/4b/4c`` row represents the
    complete Stage 4 group rather than just 4a.
    """

    text = str(value or "").strip()
    match = re.match(
        r"^(?:stage\s*)?([0-6])([abc])?(?:\s*(?:/|／)\s*4[abc])*(?:\s+.*)?$",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    stage = int(match.group(1))
    component = str(match.group(2) or "").casefold()
    combined = bool(stage == 4 and re.search(r"(?:/|／)\s*4[bc]", text, re.I))
    return stage, component, combined


def long_pipeline_stages(progress_text: str) -> list[dict[str, Any]]:
    """Read only the pipeline table, never the later chapter-boundary table."""

    rows: list[dict[str, Any]] = []
    in_section = False
    for raw_line in progress_text.splitlines():
        heading = re.match(r"^##\s*(.+?)\s*$", raw_line.strip())
        if heading:
            title = heading.group(1).strip()
            if in_section:
                break
            in_section = title in {"管道进度", "阶段状态", "管道状态"}
            continue
        if not raw_line.lstrip().startswith("|"):
            continue
        cells = [
            part.strip()
            for part in raw_line.strip().strip("|").split("|")
        ]
        # Early skill versions placed the pipeline table directly below the
        # title without a ``## 管道进度`` heading.  Its explicit Stage header
        # still identifies it unambiguously; later chapter tables use 章号.
        if cells and cells[0].casefold() == "stage":
            in_section = True
            continue
        if not in_section:
            continue
        if len(cells) < 3:
            continue
        token = _long_stage_token(cells[0])
        if not token:
            continue
        stage, component, combined = token
        status = _normalize_long_stage_status(cells[1])
        name = LONG_STAGE_NAMES[stage]
        status_text = cells[1]
        progress = cells[2] if len(cells) >= 3 else ""
        if status is None and len(cells) >= 3:
            # Legacy projection: Stage | name | status.
            status = _normalize_long_stage_status(cells[2])
            name = cells[1] or name
            status_text = cells[2]
            progress = cells[2]
        if status is None:
            continue
        rows.append(
            {
                "stage": stage,
                "component": component,
                "combined": combined,
                "name": name,
                "status": status,
                "status_text": " ".join(
                    value for value in (status_text, progress) if value
                ).strip(),
            }
        )

    grouped: list[dict[str, Any]] = []
    for stage in sorted(LONG_STAGES):
        candidates = [row for row in rows if row["stage"] == stage]
        if not candidates:
            continue
        if stage == 4:
            combined = next(
                (
                    row
                    for row in candidates
                    if row["combined"] or not row["component"]
                ),
                None,
            )
            if combined:
                selected = dict(combined)
            else:
                components = {
                    row["component"]: row
                    for row in candidates
                    if row["component"] in {"a", "b", "c"}
                }
                statuses = [row["status"] for row in components.values()]
                selected = {
                    "stage": 4,
                    "component": "",
                    "combined": True,
                    "name": LONG_STAGE_NAMES[4],
                    "status": (
                        "completed"
                        if set(components) == {"a", "b", "c"}
                        and all(value == "completed" for value in statuses)
                        else "error"
                        if "error" in statuses
                        else "running"
                        if "running" in statuses
                        else "pending"
                    ),
                    "status_text": "；".join(
                        f"4{key} {components[key]['status_text']}"
                        for key in sorted(components)
                    ),
                }
        else:
            selected = dict(candidates[0])
        selected.pop("component", None)
        selected.pop("combined", None)
        grouped.append(selected)
    return grouped


def long_progress_state(progress_text: str) -> tuple[dict[int, str], str]:
    stages = {
        int(item["stage"]): str(item["status"])
        for item in long_pipeline_stages(progress_text)
    }
    final_match = re.search(
        r"^-\s*最终状态\s*[:：]\s*([^\s|]+)",
        progress_text,
        re.MULTILINE,
    )
    if not final_match:
        final_match = re.search(
            r"^##\s*最终状态\s*$\s*^([^\n#]+)",
            progress_text,
            re.MULTILINE,
        )
    final_state = final_match.group(1).strip() if final_match else ""
    # The skill's current progress template commonly renders the state as
    # inline code below the heading (``completed``).  Treat Markdown quoting
    # as presentation, not as part of the lifecycle value.
    final_state = final_state.strip("`*_ ").casefold()
    return stages, final_state


def long_chapter_boundaries(progress_text: str) -> list[dict[str, str]]:
    """Return the authoritative Stage 0 chapter-boundary rows.

    Boundary identifiers are deliberately kept as strings because real books
    can contain a prologue row (``序``) in addition to numbered chapters.
    Counting only ``第*章_摘要.md`` previously turned that valid non-numeric
    boundary into a permanent 981/982 retry loop.
    """

    in_boundaries = False
    boundaries: list[dict[str, str]] = []
    for line in progress_text.splitlines():
        if re.match(r"^##\s*章节边界", line):
            in_boundaries = True
            continue
        if in_boundaries and line.startswith("## "):
            break
        if not in_boundaries or not line.lstrip().startswith("|"):
            continue
        cells = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        identifier = cells[0].strip("` ")
        if not (identifier.isdigit() or identifier in {"序", "序章", "第零章"}):
            continue
        boundaries.append(
            {
                "identifier": identifier,
                "title": cells[1].strip("` "),
                "start_line": cells[2].strip("` "),
                "word_count": cells[3].strip("` "),
            }
        )
    return boundaries


def _summary_candidates(identifier: str) -> tuple[str, ...]:
    value = str(identifier or "").strip()
    if value.isdigit():
        number = int(value)
        if number == 0:
            return ("第0章_摘要.md", "第零章_摘要.md", "序章_摘要.md")
        return (f"第{number}章_摘要.md",)
    if value in {"序", "序章", "第零章"}:
        return ("序章_摘要.md", "第0章_摘要.md", "第零章_摘要.md")
    return (f"{value}_摘要.md",)


def long_summary_coverage(
    output_dir: Path,
    progress_text: str,
) -> tuple[int, int, list[str]]:
    """Return exact Stage 2 coverage against the immutable boundary table."""

    boundaries = long_chapter_boundaries(progress_text)
    missing: list[str] = []
    for boundary in boundaries:
        candidates = _summary_candidates(boundary["identifier"])
        if not any(_nonempty(output_dir / "章节" / name, 100) for name in candidates):
            missing.append(boundary["title"] or boundary["identifier"])
    return len(boundaries) - len(missing), len(boundaries), missing


def validate_long_output_contract(
    output_dir: Path,
    requested_mode: str,
) -> list[str]:
    """Validate story-long-analyze Stage 1 or full Stage 0-6 delivery."""
    errors: list[str] = []
    progress_path = output_dir / "_progress.md"
    progress_text = (
        progress_path.read_text(encoding="utf-8", errors="replace")
        if _nonempty(progress_path)
        else ""
    )
    if not progress_text:
        errors.append("_progress.md 缺失")
        return errors
    if not re.search(r"schema_version\s*:\s*2", progress_text):
        errors.append("_progress.md 缺少 schema_version: 2")
    boundaries = long_chapter_boundaries(progress_text)
    boundary_count = len(boundaries)
    if boundary_count <= 0:
        errors.append("_progress.md 缺少章节边界表")
    if not _has_nonempty_original(output_dir):
        errors.append("原文/ 缺少非空原文备份")
    if not _nonempty(output_dir / "概要.md", 100):
        errors.append("概要.md 缺失或内容过短")

    stages, final_state = long_progress_state(progress_text)
    if requested_mode != "full":
        for stage in (0, 1):
            if stages.get(stage) != "completed":
                errors.append(f"Stage {stage} 未完成")
        if final_state != "paused_after_stage1":
            errors.append("最终状态不是 paused_after_stage1")
        if not _nonempty(output_dir / "快速预览.md", 200):
            errors.append("快速预览.md 缺失或内容过短")
        for stage in range(1, 4):
            if not _nonempty(
                output_dir / "章节" / f"第{stage}章_深度拆解.md",
                200,
            ):
                errors.append(f"第{stage}章_深度拆解.md 缺失或内容过短")
        return errors

    missing_stages = sorted(
        stage for stage in LONG_STAGES if stages.get(stage) != "completed"
    )
    if missing_stages:
        errors.append(f"未完成 Stage：{missing_stages}")
    if final_state not in {"completed", "completed_with_errors"}:
        errors.append("最终状态不是 completed/completed_with_errors")
    required_files = {
        "快速预览.md": (1, 200),
        "拆文报告.md": (5, 800),
        "文风.md": (6, 300),
        "剧情/README.md": (3, 300),
        "剧情/故事线.md": (3, 300),
        "剧情/节奏.md": (3, 300),
        "剧情/情绪模块.md": (3, 300),
    }
    for relative, (stage, minimum) in required_files.items():
        if not _nonempty(output_dir / relative, minimum):
            errors.append(
                f"Stage {stage}: {relative} 缺失或内容过短"
            )
    for chapter in range(1, 4):
        relative = f"章节/第{chapter}章_深度拆解.md"
        if not _nonempty(output_dir / relative, 200):
            errors.append(
                f"Stage 1: {relative} 缺失或内容过短"
            )
    completed_count, _, missing_summaries = long_summary_coverage(
        output_dir,
        progress_text,
    )
    if boundary_count > 0 and missing_summaries:
        errors.append(
            f"Stage 2: 逐章摘要覆盖不足：{completed_count}/{boundary_count}；"
            "缺失：" + "、".join(missing_summaries[:12])
        )

    plot_readme = _read_text(output_dir / "剧情" / "README.md")
    storylines = _read_text(output_dir / "剧情" / "故事线.md")
    rhythm = _read_text(output_dir / "剧情" / "节奏.md")
    emotion = _read_text(output_dir / "剧情" / "情绪模块.md")
    plot_units = _nonempty_markdown_files(
        output_dir / "剧情",
        minimum_bytes=300,
        excluded_names=LONG_PLOT_RESERVED_FILES,
    )
    artifact_manifest = read_long_artifact_manifest(output_dir)
    indexed_titles = _indexed_plot_titles(plot_readme)
    if indexed_titles:
        file_titles = {
            _normalized_plot_title(path.stem)
            for path in plot_units
        }
        missing_plot_files = [
            title
            for title in indexed_titles
            if _normalized_plot_title(title) not in file_titles
        ]
        if missing_plot_files:
            errors.append(
                "Stage 3: 剧情单元清单存在未落盘文件："
                + "、".join(missing_plot_files[:12])
            )
    elif plot_units:
        errors.append("Stage 3: 剧情/README.md 缺少剧情单元清单")
    elif not _valid_not_applicable_declaration(
        artifact_manifest,
        "plot_units",
    ):
        errors.append(
            "Stage 3: 未产出独立剧情单元，且缺少有效的 "
            "_artifact_manifest.json plot_units=not_applicable 审计结论"
        )
    indexed_keys = {
        _normalized_plot_title(title) for title in indexed_titles
    }
    unindexed_plot_files = [
        path.name
        for path in plot_units
        if _normalized_plot_title(path.stem) not in indexed_keys
    ]
    if indexed_titles and unindexed_plot_files:
        errors.append(
            "Stage 3: 存在未写入剧情单元清单的文件："
            + "、".join(unindexed_plot_files[:12])
        )
    for path in plot_units:
        text = _read_text(path)
        missing = _missing_markers(
            text,
            ("核心目标", "核心冲突", "章节范围", "情节点"),
        )
        if missing:
            errors.append(
                f"Stage 3: 剧情/{path.name} 缺少字段："
                + "、".join(missing)
            )
    framework_text = f"{plot_readme}\n{storylines}"
    framework_missing = _missing_markers(
        framework_text,
        ("故事框架", "覆盖率", "粒度", "独立性", "数量"),
    )
    if framework_missing:
        errors.append(
            "Stage 3: 剧情框架质量检查缺少："
            + "、".join(framework_missing)
        )
    rhythm_missing = _missing_markers(
        rhythm,
        (
            "全书情绪节奏总览",
            "循环单元",
            "关键信息推进",
            "爽点循环",
            "情绪触动点",
            "爆发节奏",
        ),
    )
    if rhythm_missing:
        errors.append(
            "Stage 3: 剧情/节奏.md 缺少："
            + "、".join(rhythm_missing)
        )
    emotion_missing = _missing_markers(
        emotion,
        ("读者需求", "情绪引擎", "故事框架", "可复现模块", "重组与复现"),
    )
    if emotion_missing:
        errors.append(
            "Stage 3: 剧情/情绪模块.md 缺少："
            + "、".join(emotion_missing)
        )

    character_profiles = _nonempty_markdown_files(
        output_dir / "角色",
        minimum_bytes=200,
        excluded_names={"角色关系.md"},
    )
    if not character_profiles and not _valid_not_applicable_declaration(
        artifact_manifest,
        "characters",
    ):
        errors.append(
            "Stage 4: 未产出角色档案，且缺少有效的 "
            "characters=not_applicable 审计结论"
        )
    if (
        not _nonempty(output_dir / "角色" / "角色关系.md", 300)
        and not _valid_not_applicable_declaration(
            artifact_manifest,
            "relationships",
        )
    ):
        errors.append(
            "Stage 4: 未产出角色关系，且缺少有效的 "
            "relationships=not_applicable 审计结论"
        )
    background_path = output_dir / "设定" / "世界观" / "背景设定.md"
    background = _read_text(background_path)
    worldbuilding_files = _nonempty_markdown_files(
        output_dir / "设定" / "世界观",
        minimum_bytes=20,
    )
    if not worldbuilding_files and not _valid_not_applicable_declaration(
        artifact_manifest,
        "worldbuilding",
    ):
        errors.append(
            "Stage 4: 未产出世界观设定，且缺少有效的 "
            "worldbuilding=not_applicable 审计结论"
        )
    force_files = _nonempty_markdown_files(
        output_dir / "设定" / "势力",
        minimum_bytes=200,
    )
    if (
        not force_files
        and not any(
            marker in background for marker in NO_INDEPENDENT_FORCE_MARKERS
        )
        and not _valid_not_applicable_declaration(
            artifact_manifest,
            "factions",
        )
    ):
        errors.append(
            "Stage 4: 未产出势力文件，且缺少有效的 "
            "factions=not_applicable 审计结论"
        )

    if (output_dir / "_章节摘要汇总.md").exists():
        errors.append("Stage 6: _章节摘要汇总.md 仍未按管道要求清理")
    return errors


def long_contract_failed_stages(errors: list[str]) -> set[int]:
    """Project mechanical contract errors back to their owning stages."""

    failed: set[int] = set()
    for error in errors:
        text = str(error or "")
        failed.update(
            int(value)
            for value in re.findall(
                r"\bStage\s+([0-6])(?:\s*:|\s+未完成)",
                text,
            )
        )
        missing = re.search(r"未完成 Stage[：:]\s*\[([^]]*)\]", text)
        if missing:
            failed.update(
                int(value)
                for value in re.findall(r"\b([0-6])\b", missing.group(1))
            )
        if any(
            marker in text
            for marker in (
                "_progress.md 缺失",
                "schema_version: 2",
                "章节边界表",
                "原文/ 缺少",
                "概要.md 缺失",
            )
        ):
            failed.add(0)
        if "快速预览.md" in text or "章_深度拆解.md" in text:
            failed.add(1)
        if "最终状态不是" in text:
            failed.add(6)
    return failed


def project_long_contract_failures(
    stages: list[dict[str, Any]],
    errors: list[str],
) -> list[dict[str, Any]]:
    """Ensure a failed artifact stage is never rendered as completed."""

    failed = long_contract_failed_stages(errors)
    projected: list[dict[str, Any]] = []
    for stage in stages:
        row = dict(stage)
        number = int(row.get("stage", -1))
        if number in failed:
            row["status"] = "pending"
            row["status_text"] = "⚠ 产物验收未通过，等待补齐"
        projected.append(row)
    return projected


def validate_output_contract(
    output_dir: Path,
    pipeline: str,
    requested_mode: str,
) -> list[str]:
    if pipeline == "short":
        return validate_short_output_contract(output_dir)
    return validate_long_output_contract(output_dir, requested_mode)


def has_resume_checkpoint(output_dir: Path, pipeline: str) -> bool:
    if pipeline == "short":
        return _nonempty(output_dir / "_meta.json")
    return _nonempty(output_dir / "_progress.md")
