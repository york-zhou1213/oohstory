from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


class DeconstructionCatalogMixin:
    def list_global_deconstructions(
        self, project_root: Optional[Path] = None
    ) -> Dict[str, Any]:
        from .electronic_library import Path, _normalize_book_identity, _now, copy

        self.global_deconstruction_root.mkdir(parents=True, exist_ok=True)
        task_project_root = project_root or self.global_deconstruction_root
        managed_tasks = [
            self._reconcile_stalled_managed_task(
                task_project_root,
                task,
                persist=True,
            )
            for task in self._list_managed_tasks(
                task_project_root,
                enrich_artifacts=False,
            )
        ]
        latest_task_by_output: Dict[str, Dict[str, Any]] = {}
        for task in managed_tasks:
            output_key = str(
                Path(task.get("output_dir") or self.global_deconstruction_root)
                .expanduser()
                .resolve()
            )
            latest_task_by_output.setdefault(output_key, task)

        cached = self._load_deconstruction_cache(project_root)
        cached_artifact_signatures = cached.get("artifact_signatures") or {}
        current_artifact_signatures = self._deconstruction_artifact_signatures()
        cached_artifacts_by_output = {
            str(item.get("output_dir") or ""): item
            for item in cached.get("artifact_items") or []
            if isinstance(item, dict) and item.get("output_dir")
        }
        items: List[Dict[str, Any]] = []
        artifact_items: List[Dict[str, Any]] = []
        for output_dir in self.global_deconstruction_root.iterdir():
            if not output_dir.is_dir() or output_dir.name.startswith("."):
                continue
            try:
                output_key = str(output_dir.expanduser().resolve())
                preferred_pipeline = str(
                    latest_task_by_output.get(output_key, {}).get("resolved_pipeline")
                    or ""
                )
                cached_artifact = cached_artifacts_by_output.get(output_key)
                preferred_matches = bool(
                    not preferred_pipeline
                    or not cached_artifact
                    or cached_artifact.get("resolved_pipeline") == preferred_pipeline
                )
                if (
                    cached_artifact
                    and preferred_matches
                    and cached_artifact_signatures.get(output_key)
                    == current_artifact_signatures.get(output_key)
                ):
                    artifact = copy.deepcopy(cached_artifact)
                else:
                    artifact = self._deconstruction_artifact_item(
                        output_dir,
                        preferred_pipeline=(
                            preferred_pipeline
                            if preferred_pipeline in {"short", "long"}
                            else None
                        ),
                    )
                artifact_items.append(copy.deepcopy(artifact))
                items.append(artifact)
            except (OSError, UnicodeError):
                continue

        by_output = {
            str(Path(item["output_dir"]).expanduser().resolve()): item
            for item in items
            if item.get("output_dir")
        }
        merged_task_outputs: set[str] = set()
        for task in managed_tasks:
            output_key = str(
                Path(task.get("output_dir") or self.global_deconstruction_root)
                .expanduser()
                .resolve()
            )
            # _list_managed_tasks is newest-first.  Only the latest task owns
            # the current status projection for one shared artifact directory.
            if output_key in merged_task_outputs:
                continue
            merged_task_outputs.add(output_key)
            artifact = by_output.get(output_key)
            if artifact:
                artifact["origin"] = (
                    "frontend_and_command_line"
                    if artifact.get("progress_source") == "_progress.md"
                    else "frontend"
                )
                artifact["managed_task_id"] = task.get("id")
                artifact["id"] = task.get("id") or artifact["id"]
                artifact["book_id"] = task.get("book_id")
                artifact["catalog_id"] = task.get("book_id")
                for key in (
                    "author",
                    "category",
                    "source_id",
                    "log_path",
                    "last_message_path",
                    "runner_requested",
                    "runner_name",
                    "model_requested",
                    "model_name",
                    "reasoning_requested",
                    "reasoning_name",
                    "ai_session_id",
                    "can_resume",
                    "pause_reason",
                    "resume_count",
                    "resumed_at",
                    "word_count",
                    "analysis_band",
                    "entry_mode",
                    "execution_mode",
                    "requested_mode",
                    "resolved_pipeline",
                    "skill",
                    "resume_strategy",
                    "openclaw_agent_id",
                    "model_contract",
                    "automatic_full_pipeline",
                    "ai_session_generation",
                ):
                    if key in task:
                        artifact[key] = task[key]
                entry_mode = str(
                    task.get("entry_mode") or task.get("requested_mode") or ""
                )
                if entry_mode == "scan":
                    physical_full = bool(artifact.get("has_full_report"))
                    physical_level = artifact.get("artifact_level")
                    scan_accepted = bool(
                        artifact.get("has_quick_preview")
                        or physical_full
                        or (
                            isinstance(task.get("contract_validation"), dict)
                            and task["contract_validation"].get("ok")
                        )
                    )
                    artifact["physical_has_full_report"] = physical_full
                    artifact["physical_artifact_level"] = physical_level
                    artifact["has_full_report"] = False
                    artifact["has_quick_preview"] = scan_accepted
                    artifact["artifact_level"] = (
                        "scan" if scan_accepted else "in_progress"
                    )
                    artifact["global_reuse"] = scan_accepted
                    if scan_accepted and task.get("status") not in {
                        "queued",
                        "running",
                        "error",
                    }:
                        artifact["status"] = "paused"
                        artifact["progress"] = 40
                        artifact["current_stage"] = "黄金三章已完成"
                        artifact["message"] = (
                            "本次只授权黄金三章；磁盘上的额外产物"
                            "不计为完整拆书，需另行启动完整拆书"
                        )
                        artifact["can_resume"] = False
                        artifact["pause_reason"] = "paused_after_stage1"
                # 完整成果是最终事实，不能被残留的前端 task JSON 回写成“运行中”。
                if artifact.get("status") != "completed" and task.get("status") in {
                    "queued",
                    "running",
                    "error",
                }:
                    artifact["status"] = task["status"]
                    artifact["progress"] = max(
                        int(artifact.get("progress") or 0),
                        int(task.get("progress") or 0),
                    )
                    artifact["current_stage"] = task.get(
                        "current_stage"
                    ) or artifact.get("current_stage")
                    artifact["message"] = task.get("message") or artifact.get("message")
                elif (
                    artifact.get("status") != "completed"
                    and task.get("status") == "paused"
                ):
                    artifact["status"] = "paused"
                    artifact["can_resume"] = bool(task.get("can_resume"))
                    artifact["pause_reason"] = task.get("pause_reason") or artifact.get(
                        "pause_reason"
                    )
                    artifact["message"] = task.get("message") or artifact.get("message")
                artifact["updated_at"] = max(
                    str(artifact.get("updated_at") or ""),
                    str(task.get("updated_at") or ""),
                )
                continue
            managed = dict(task)
            task_claimed_completed = task.get("status") == "completed"
            managed.update(
                {
                    "origin": "frontend",
                    "managed_task_id": task.get("id"),
                    "catalog_id": task.get("book_id"),
                    "status": "error" if task_claimed_completed else task.get("status"),
                    "progress": (
                        min(int(task.get("progress") or 0), 99)
                        if task_claimed_completed
                        else task.get("progress")
                    ),
                    "message": (
                        "任务声称完成，但未发现可验收的完整拆书产物"
                        if task_claimed_completed
                        else task.get("message")
                    ),
                    "artifact_level": "in_progress",
                    "has_quick_preview": False,
                    "has_full_report": False,
                    "completed_chapters": 0,
                    "total_chapters": 0,
                    "progress_source": "task_json",
                }
            )
            items.append(managed)

        requested_source_ids = sorted(
            {
                str(item.get("source_id") or "")
                for item in items
                if item.get("source_id") not in (None, "")
            }
        )
        requested_titles = sorted(
            {
                str(item.get("title") or "")
                for item in items
                if str(item.get("title") or "").strip()
            }
        )
        requested_title_keys = sorted(
            {
                _normalize_book_identity(title)
                for title in requested_titles
                if _normalize_book_identity(title)
            }
        )
        catalog_rows: List[Dict[str, Any]] = []
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            catalog_rows = [
                self._materialize_mysql_catalog_row(row)
                for row in self.mysql_catalog.find_book_identities(
                    source_ids=requested_source_ids,
                    titles=requested_titles,
                )
            ]
        elif requested_source_ids or requested_titles:
            with self._catalog_connection() as conn:
                catalog_columns = {
                    str(row["name"]) for row in conn.execute("PRAGMA table_info(books)")
                }
                lookup_conditions: List[str] = []
                lookup_params: List[Any] = []
                if requested_source_ids:
                    placeholders = ",".join("?" for _ in requested_source_ids)
                    lookup_conditions.append(f"source_id IN ({placeholders})")
                    lookup_params.extend(requested_source_ids)
                if "title_key" in catalog_columns and requested_title_keys:
                    placeholders = ",".join("?" for _ in requested_title_keys)
                    lookup_conditions.append(f"title_key IN ({placeholders})")
                    lookup_params.extend(requested_title_keys)
                elif requested_titles:
                    placeholders = ",".join("?" for _ in requested_titles)
                    lookup_conditions.append(f"title IN ({placeholders})")
                    lookup_params.extend(requested_titles)
                catalog_rows = [
                    dict(row)
                    for row in conn.execute(
                        f"""
                        SELECT id AS catalog_id,
                               COALESCE(source_id, id) AS source_id,
                               title, author, category,
                               output_path AS source_path,
                               bytes AS source_bytes
                        FROM books
                        WHERE status != 'duplicate'
                          AND ({" OR ".join(lookup_conditions)})
                        """,
                        lookup_params,
                    )
                ]
        by_source_id = {
            str(item.get("source_id") or ""): item
            for item in catalog_rows
            if item.get("source_id") is not None
        }
        by_title = {
            _normalize_book_identity(item.get("title")): item
            for item in catalog_rows
            if item.get("title")
        }
        for item in items:
            catalog = by_source_id.get(
                str(item.get("source_id") or "")
            ) or by_title.get(_normalize_book_identity(item.get("title")))
            if catalog:
                item["catalog_id"] = int(catalog["catalog_id"])
                item["book_id"] = int(catalog["catalog_id"])
                item["source_id"] = str(catalog["source_id"])
                item["title"] = catalog.get("title") or item.get("title")
                item["author"] = catalog.get("author") or item.get("author") or ""
                item["category"] = catalog.get("category") or item.get("category") or ""
                item["source_path"] = (
                    catalog.get("source_path") or item.get("source_path") or ""
                )
                item["source_bytes"] = int(
                    catalog.get("source_bytes") or item.get("source_bytes") or 0
                )

        metric_items = [item for item in items if int(item.get("catalog_id") or 0) > 0]
        self._apply_content_metrics(
            metric_items,
            include_latest_chapter=False,
        )
        for item in metric_items:
            if str(item.get("resolved_pipeline") or "") != "short":
                continue
            source_chapter_count = int(
                item.get("chapter_count") or item.get("approx_chapter_count") or 0
            )
            item["source_chapter_count"] = source_chapter_count
            if (
                item.get("coverage_scope") == "whole_text"
                and item.get("status") == "completed"
            ):
                chapter_text = (
                    f"覆盖 {source_chapter_count} 章完整原文"
                    if source_chapter_count
                    else "覆盖完整原文"
                )
                node_count = int(item.get("plot_node_count") or 0)
                node_text = f" · {node_count} 个情节节点" if node_count else ""
                item["coverage_label"] = (
                    f"全篇结构拆解已完成 · {chapter_text}{node_text}"
                )

        link_registry = self.list_project_deconstruction_links()
        links_by_output: Dict[str, List[Dict[str, Any]]] = {}
        for link in link_registry["items"]:
            output_key = str(
                Path(str(link.get("global_output_dir") or "")).expanduser().resolve()
            )
            links_by_output.setdefault(output_key, []).append(link)
        current_project_root = (
            str(project_root.expanduser().resolve()) if project_root is not None else ""
        )
        for item in items:
            output_key = str(
                Path(str(item.get("output_dir") or "")).expanduser().resolve()
            )
            source_links = links_by_output.get(output_key, [])
            item["source_projects"] = [
                {
                    "project_id": link.get("project_id") or "",
                    "project_name": link.get("project_name") or "",
                    "project_directory_name": (
                        link.get("project_directory_name") or ""
                    ),
                    "link_status": link.get("link_status") or "missing",
                }
                for link in source_links
            ]
            current_link = next(
                (
                    link
                    for link in source_links
                    if link.get("project_root") == current_project_root
                ),
                None,
            )
            item["linked_to_current_project"] = bool(current_link)
            item["current_project_link"] = (
                {
                    "association_id": current_link.get("id"),
                    "link_name": current_link.get("global_book_key"),
                    "link_status": current_link.get("link_status"),
                }
                if current_link
                else None
            )

        status_order = {
            "running": 0,
            "queued": 1,
            "paused": 2,
            "error": 3,
            "completed": 4,
            "discovered": 5,
        }
        # 同一状态内优先展示最近更新的任务；稳定排序再保证运行中置顶。
        items.sort(
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )
        items.sort(
            key=lambda item: status_order.get(str(item.get("status")), 9),
        )
        running = sum(item.get("status") in {"queued", "running"} for item in items)
        completed = sum(item.get("status") == "completed" for item in items)
        scan_completed = sum(
            bool(item.get("has_quick_preview") or item.get("has_full_report"))
            for item in items
        )
        result = {
            "root": str(self.global_deconstruction_root.resolve()),
            "total": len(items),
            "running": running,
            "completed": completed,
            "scan_completed": scan_completed,
            "items": items,
            "updated_at": _now(),
            "progress_source": "全局拆书库产物 + _progress.md + 前端任务 JSON",
        }
        self._store_deconstruction_cache(
            project_root,
            result,
            artifact_items=artifact_items,
        )
        return result

    def _deconstruction_artifact_item(
        self,
        output_dir: Path,
        preferred_pipeline: Optional[str] = None,
    ) -> Dict[str, Any]:
        from .electronic_library import (
            DECONSTRUCTION_STALE_SECONDS,
            UTC,
            datetime,
            hashlib,
            long_contract_failed_stages,
            long_progress_state,
            long_summary_coverage,
            project_long_contract_failures,
            re,
            read_short_meta,
            short_pipeline_stages,
            validate_long_output_contract,
            validate_short_output_contract,
        )

        progress_path = output_dir / "_progress.md"
        short_meta_path = output_dir / "_meta.json"
        progress_text = ""
        if progress_path.is_file():
            progress_text = progress_path.read_text(encoding="utf-8", errors="replace")
        name = output_dir.name
        source_id = ""
        title = name
        if "__" in name:
            title, source_id = name.rsplit("__", 1)
        heading = re.search(r"^#\s*拆解进度[：:]\s*(.+?)\s*$", progress_text, re.M)
        if heading:
            title = heading.group(1).strip()

        short_meta = read_short_meta(output_dir)
        use_short_meta = bool(short_meta)
        if short_meta and preferred_pipeline == "long":
            use_short_meta = False
        elif short_meta and preferred_pipeline is None and progress_path.is_file():
            try:
                use_short_meta = (
                    short_meta_path.stat().st_mtime >= progress_path.stat().st_mtime
                )
            except OSError:
                pass
        if use_short_meta:
            stages = short_pipeline_stages(short_meta)
            contract_errors = validate_short_output_contract(output_dir)
            full_completed = not contract_errors
            structure_counts = (
                short_meta.get("structure_counts")
                if isinstance(short_meta.get("structure_counts"), dict)
                else {}
            )
            structure_beat_count = int(structure_counts.get("beats") or 0)
            plot_node_count = 0
            plot_nodes_path = output_dir / "情节节点.md"
            if plot_nodes_path.is_file():
                try:
                    plot_node_count = len(
                        re.findall(
                            r"(?m)^N\d+\b",
                            plot_nodes_path.read_text(
                                encoding="utf-8",
                                errors="replace",
                            ),
                        )
                    )
                except OSError:
                    plot_node_count = 0
            completed_stage_count = sum(
                item.get("status") == "completed" for item in stages
            )
            current = next(
                (item for item in stages if item.get("status") == "running"),
                None,
            ) or next(
                (item for item in stages if item.get("status") == "pending"),
                None,
            )
            tracked_paths = [
                path
                for path in (
                    short_meta_path,
                    output_dir / "拆文报告.md",
                    output_dir / "情节节点.md",
                    output_dir / "写作手法.md",
                )
                if path.exists()
            ]
            updated_timestamp = max(
                (path.stat().st_mtime for path in tracked_paths),
                default=output_dir.stat().st_mtime,
            )
            return {
                "id": f"global-{hashlib.sha1(str(output_dir.resolve()).encode()).hexdigest()[:12]}",
                "origin": "command_line",
                "managed_task_id": None,
                "book_id": None,
                "catalog_id": None,
                "source_id": source_id,
                "title": title,
                "author": "",
                "category": "",
                "output_dir": str(output_dir.resolve()),
                "requested_mode": "full",
                "resolved_pipeline": "short",
                "skill": "story-short-analyze",
                "status": "completed" if full_completed else "paused",
                "progress": (
                    100 if full_completed else min(95, 12 + completed_stage_count * 16)
                ),
                "current_stage": (
                    "全篇结构拆解已完成"
                    if full_completed
                    else (
                        f"等待继续 Stage {current['stage']} · {current['name']}"
                        if current
                        else "短篇产物验收未通过"
                    )
                ),
                "message": (
                    (
                        "story-short-analyze Stage 2–6 已覆盖完整原文；"
                        f"{structure_beat_count} 段主结构不是章节数"
                    )
                    if full_completed
                    else "已有短篇拆书断点，但产物契约尚未全部通过"
                ),
                "completion_label": (
                    "story-short-analyze Stage 2–6 全篇结构拆解已完成"
                    if full_completed
                    else ""
                ),
                "created_at": datetime.fromtimestamp(
                    output_dir.stat().st_ctime, UTC
                ).isoformat(timespec="seconds"),
                "updated_at": datetime.fromtimestamp(updated_timestamp, UTC).isoformat(
                    timespec="seconds"
                ),
                "pid": None,
                "steps": [
                    {
                        "id": f"stage-{item['stage']}",
                        "name": f"Stage {item['stage']} · {item['name']}",
                        "status": item["status"],
                    }
                    for item in stages
                ],
                "pipeline_stages": stages,
                "artifact_level": "full" if full_completed else "in_progress",
                "has_quick_preview": False,
                "has_full_report": full_completed,
                "completed_chapters": 0,
                "total_chapters": 0,
                "coverage_scope": "whole_text",
                "structure_beat_count": structure_beat_count,
                "plot_node_count": plot_node_count,
                "progress_path": str(short_meta_path.resolve()),
                "progress_source": "_meta.json",
                "global_reuse": full_completed,
                "can_resume": not full_completed,
                "pause_reason": (
                    None if full_completed else "artifact_validation_failed"
                ),
                "contract_validation": {
                    "ok": full_completed,
                    "errors": contract_errors,
                    "skill": "story-short-analyze",
                },
                "word_count": int(short_meta.get("word_count") or 0),
            }

        stages = self._progress_stage_rows(progress_text)
        stage_two = next((item for item in stages if item.get("stage") == 2), {})
        chapter_match = re.search(
            r"(\d+)\s*/\s*(\d+)", str(stage_two.get("status_text") or "")
        )
        completed_chapters = int(chapter_match.group(1)) if chapter_match else 0
        total_chapters = int(chapter_match.group(2)) if chapter_match else 0
        chapter_summary_count, boundary_total, _ = long_summary_coverage(
            output_dir,
            progress_text,
        )
        if not boundary_total:
            # Compatibility for legacy CLI artifacts created before schema v2
            # introduced the immutable boundary table. New/background runs
            # always use exact boundary-to-file matching above.
            chapter_summary_count = sum(
                1
                for path in (output_dir / "章节").glob("第*章_摘要.md")
                if path.is_file()
            )
        completed_chapters = max(completed_chapters, chapter_summary_count)
        if boundary_total:
            total_chapters = boundary_total
        elif not total_chapters:
            scope_match = re.search(r"Stage 2 只做第\s*1-(\d+)\s*章", progress_text)
            count_match = re.search(r"^- 章节数[：:]\s*(\d+)", progress_text, re.M)
            total_chapters = (
                int((scope_match or count_match).group(1))
                if (scope_match or count_match)
                else chapter_summary_count
            )

        _, final_state = long_progress_state(progress_text)
        has_quick_preview = (output_dir / "快速预览.md").is_file()
        full_contract_errors = validate_long_output_contract(output_dir, "full")
        full_completed = not full_contract_errors
        completion_claimed = final_state in {
            "completed",
            "completed_with_errors",
        }
        failed_artifact_stages = (
            long_contract_failed_stages(full_contract_errors)
            if completion_claimed
            else set()
        )
        if failed_artifact_stages:
            stages = project_long_contract_failures(
                stages,
                full_contract_errors,
            )
        tracked_paths = [
            path
            for path in (
                progress_path,
                output_dir / "快速预览.md",
                output_dir / "拆文报告.md",
            )
            if path.exists()
        ]
        updated_timestamp = max(
            (path.stat().st_mtime for path in tracked_paths),
            default=output_dir.stat().st_mtime,
        )
        has_running_stage = any(item.get("status") == "running" for item in stages)
        stale_running_stage = bool(
            has_running_stage
            and datetime.now(UTC).timestamp() - updated_timestamp
            >= DECONSTRUCTION_STALE_SECONDS
        )
        can_resume = False
        pause_reason: Optional[str] = None
        if full_completed:
            status = "completed"
            current_stage = "完整拆书已完成"
            message = "全局完整拆书成果可供所有项目复用"
        elif final_state == "paused_after_stage1":
            status = "paused"
            current_stage = "黄金三章已完成"
            message = "已停靠在黄金三章，可继续完整拆书"
        elif failed_artifact_stages:
            status = "paused"
            can_resume = True
            pause_reason = "artifact_validation_failed"
            failed_stage = min(failed_artifact_stages)
            failed_row = next(
                (item for item in stages if int(item.get("stage", -1)) == failed_stage),
                {},
            )
            current_stage = (
                f"Stage {failed_stage} · {failed_row.get('name') or '产物'}验收未通过"
            )
            message = "；".join(full_contract_errors[:3])
        elif stale_running_stage:
            status = "paused"
            can_resume = True
            pause_reason = "stalled_checkpoint"
            current = next(
                (item for item in stages if item.get("status") == "running"),
                None,
            )
            current_stage = (
                f"已停滞于 Stage {current['stage']} · {current['name']}"
                if current
                else "拆书任务已停滞"
            )
            message = (
                f"已有 {completed_chapters}/{total_chapters} 章成果，"
                "当前无执行进程，可从现有断点接管继续"
                if total_chapters
                else "当前无执行进程，可从现有 OH-Story 断点接管继续"
            )
        elif has_running_stage:
            status = "running"
            current = next(
                (item for item in stages if item.get("status") == "running"),
                None,
            )
            current_stage = (
                f"Stage {current['stage']} · {current['name']}"
                if current
                else "拆书流程进行中"
            )
            message = (
                f"已完成 {completed_chapters}/{total_chapters} 章"
                if total_chapters
                else "命令行 / 外部任务正在持续写入全局拆书库"
            )
        elif final_state == "pending":
            status = "paused"
            can_resume = True
            pause_reason = "stalled_checkpoint"
            current = next(
                (item for item in stages if item.get("status") == "pending"),
                None,
            )
            current_stage = (
                f"等待继续 Stage {current['stage']} · {current['name']}"
                if current
                else "拆书已暂停，可继续完整拆书"
            )
            message = (
                f"已有 {completed_chapters}/{total_chapters} 章成果，可从现有断点继续"
                if total_chapters
                else "已有部分拆书产物，可从现有断点继续"
            )
        elif has_quick_preview:
            status = "paused"
            can_resume = True
            pause_reason = "paused_after_stage1"
            current_stage = "黄金三章已完成"
            message = "已有黄金三章成果，等待继续完整拆书"
        else:
            status = "discovered"
            current_stage = "已发现拆书目录"
            message = "等待可识别的 oh-story 拆书产物"

        progress = self._deconstruction_progress(
            stages,
            completed_chapters=completed_chapters,
            total_chapters=total_chapters,
            full_completed=full_completed,
        )
        artifact_level = (
            "full"
            if full_completed
            else (
                "in_progress"
                if status == "running"
                else ("scan" if has_quick_preview else "none")
            )
        )
        return {
            "id": f"global-{hashlib.sha1(str(output_dir.resolve()).encode()).hexdigest()[:12]}",
            "origin": "command_line",
            "managed_task_id": None,
            "book_id": None,
            "catalog_id": None,
            "source_id": source_id,
            "title": title,
            "author": "",
            "category": "",
            "output_dir": str(output_dir.resolve()),
            "requested_mode": "full" if status == "running" else artifact_level,
            "resolved_pipeline": "long",
            "skill": "story-long-analyze",
            "status": status,
            "progress": progress,
            "current_stage": current_stage,
            "message": message,
            "completion_label": (
                "story-long-analyze Stage 0–6 已验收完成" if full_completed else ""
            ),
            "created_at": datetime.fromtimestamp(
                output_dir.stat().st_ctime, UTC
            ).isoformat(timespec="seconds"),
            "updated_at": datetime.fromtimestamp(updated_timestamp, UTC).isoformat(
                timespec="seconds"
            ),
            "pid": None,
            "steps": [
                {
                    "id": f"stage-{item['stage']}",
                    "name": f"Stage {item['stage']} · {item['name']}",
                    "status": item["status"],
                }
                for item in stages
            ],
            "pipeline_stages": stages,
            "artifact_level": artifact_level,
            "has_quick_preview": has_quick_preview,
            "has_full_report": full_completed,
            "completed_chapters": completed_chapters,
            "total_chapters": total_chapters,
            "progress_path": str(progress_path.resolve())
            if progress_path.exists()
            else "",
            "progress_source": "_progress.md"
            if progress_path.exists()
            else "artifacts",
            "global_reuse": full_completed or has_quick_preview,
            "can_resume": can_resume,
            "pause_reason": pause_reason,
            "contract_validation": {
                "ok": full_completed,
                "errors": full_contract_errors,
                "skill": "story-long-analyze",
            },
            "resume_strategy": ("adopt_checkpoint" if can_resume else None),
        }

    def list_deconstruction_catalog(
        self,
        project_root: Path,
        *,
        state: str = "all",
        query: str = "",
        category: str = "",
        page: int = 1,
        page_size: int = 24,
    ) -> Dict[str, Any]:
        from .electronic_library import Counter, _now, _read_json_text, sqlite3

        if state not in {"all", "unstarted", "running", "scan", "full", "error"}:
            raise ValueError("未知拆书状态筛选")
        page = max(int(page), 1)
        page_size = min(max(int(page_size), 1), 60)
        query = query.strip().casefold()
        category = category.strip()
        if (
            self.infrastructure_settings.catalog_backend == "mysql"
            and self.mysql_catalog is not None
        ):
            return self._list_deconstruction_catalog_mysql(
                project_root,
                state=state,
                query=query,
                category=category,
                page=page,
                page_size=page_size,
            )
        rows, _ = self._deconstruction_catalog_rows(project_root)

        state_counts = {
            "all": len(rows),
            "unstarted": 0,
            "running": 0,
            "scan": 0,
            "full": 0,
            "error": 0,
            "readable": 0,
        }
        for item in rows:
            deconstruction = item.get("deconstruction")
            for count_state in ("unstarted", "running", "scan", "full", "error"):
                if self._deconstruction_matches_catalog_state(
                    deconstruction, count_state
                ):
                    state_counts[count_state] += 1
            if item["available_for_analysis"]:
                state_counts["readable"] += 1

        filtered: List[Dict[str, Any]] = []
        category_counts: Counter[str] = Counter()
        for item in rows:
            if not self._deconstruction_matches_catalog_state(
                item.get("deconstruction"), state
            ):
                continue
            if query:
                haystack = " ".join(
                    str(item.get(key) or "") for key in ("title", "author", "category")
                ).casefold()
                if query not in haystack:
                    continue
            category_counts[str(item.get("category") or "未分类")] += 1
            if category and str(item.get("category") or "未分类") != category:
                continue
            filtered.append(item)

        state_order = {
            "running": 0,
            "scan": 1,
            "full": 2,
            "error": 3,
            "unstarted": 4,
        }
        filtered.sort(
            key=lambda item: (
                state_order.get(item["deconstruction_state"], 9),
                int(item.get("catalog_id") or 0),
            ),
            reverse=False,
        )
        total = len(filtered)
        offset = (page - 1) * page_size
        items = filtered[offset : offset + page_size]
        self._apply_content_metrics(items, include_latest_chapter=False)

        if items and self.index_path.exists():
            index_uri = f"{self.index_path.as_uri()}?mode=ro"
            with sqlite3.connect(index_uri, uri=True, timeout=15) as conn:
                conn.row_factory = sqlite3.Row
                placeholders = ",".join("?" for _ in items)
                catalog_ids = [int(item["catalog_id"]) for item in items]
                indexed = {
                    int(row["catalog_id"]): dict(row)
                    for row in conn.execute(
                        f"""
                        SELECT catalog_id, summary, approx_word_count,
                               approx_chapter_count, genre_tags, tone_tags
                        FROM library_index
                        WHERE catalog_id IN ({placeholders})
                        """,
                        catalog_ids,
                    )
                }
            for item in items:
                feature = indexed.get(int(item["catalog_id"]), {})
                item["summary"] = str(feature.get("summary") or "")
                item["approx_word_count"] = int(
                    item.get("word_count")
                    or feature.get("approx_word_count")
                    or item.get("approx_word_count")
                    or 0
                )
                item["approx_chapter_count"] = int(
                    item.get("chapter_count")
                    or feature.get("approx_chapter_count")
                    or item.get("approx_chapter_count")
                    or 0
                )
                item["genre_tags"] = _read_json_text(
                    feature.get("genre_tags"), item.get("genre_tags") or []
                )
                item["tone_tags"] = _read_json_text(feature.get("tone_tags"), [])

        for item in items:
            deconstruction = item.get("deconstruction")
            if not int(item.get("approx_chapter_count") or 0) and deconstruction:
                item["approx_chapter_count"] = int(
                    deconstruction.get("total_chapters")
                    or deconstruction.get("completed_chapters")
                    or 0
                )
            if (
                deconstruction
                and str(deconstruction.get("resolved_pipeline") or "") == "short"
            ):
                source_chapter_count = int(
                    item.get("chapter_count") or item.get("approx_chapter_count") or 0
                )
                deconstruction["source_chapter_count"] = source_chapter_count
                if (
                    deconstruction.get("coverage_scope") == "whole_text"
                    and deconstruction.get("status") == "completed"
                ):
                    chapter_text = (
                        f"覆盖 {source_chapter_count} 章完整原文"
                        if source_chapter_count
                        else "覆盖完整原文"
                    )
                    node_count = int(deconstruction.get("plot_node_count") or 0)
                    node_text = f" · {node_count} 个情节节点" if node_count else ""
                    deconstruction["coverage_label"] = (
                        f"全篇结构拆解已完成 · {chapter_text}{node_text}"
                    )
            item["deconstruction"] = (
                {
                    key: deconstruction.get(key)
                    for key in (
                        "id",
                        "status",
                        "progress",
                        "current_stage",
                        "message",
                        "artifact_level",
                        "has_quick_preview",
                        "has_full_report",
                        "completed_chapters",
                        "total_chapters",
                        "updated_at",
                        "runner_name",
                        "model_name",
                        "reasoning_name",
                        "ai_session_id",
                        "managed_task_id",
                        "can_resume",
                        "pause_reason",
                        "resume_count",
                        "word_count",
                        "analysis_band",
                        "entry_mode",
                        "execution_mode",
                        "resolved_pipeline",
                        "skill",
                        "resume_strategy",
                        "coverage_scope",
                        "coverage_label",
                        "source_chapter_count",
                        "structure_beat_count",
                        "plot_node_count",
                    )
                }
                if deconstruction
                else None
            )

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "state": state,
            "query": query,
            "category": category,
            "state_counts": state_counts,
            "categories": [
                {"name": name, "count": count}
                for name, count in sorted(
                    category_counts.items(),
                    key=lambda pair: (-pair[1], pair[0]),
                )
            ],
            "batches": self.list_deconstruction_batches(project_root),
            "updated_at": _now(),
        }
