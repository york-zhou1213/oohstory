from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Dict, List


class ReaderHeadingIndexMixin:
    def _reader_heading_candidates(
        cls,
        source_path: Path,
    ) -> tuple[List[Dict[str, Any]], str, List[int]]:
        """识别正文真实章节行，并避免把正文内重复标题当成新章节。

        txt80 历史文件至少存在两类目录格式：
        1. ``第一章 标题`` / ``第1章 标题``；
        2. ``001 标题``，部分文件会在后段额外插入一份 ``第738章`` 标题。

        旧实现只识别第一类，而且允许任意缩进，导致数字目录整段漏失、正文
        内四空格重复标题又被二次切章。这里先分别收集两类候选，再用连续性
        判断主目录格式；可靠的数字序列优先覆盖源站后加的零散标准标题。
        """
        from .electronic_library import (
            READER_BODY_NUMBER_HEADING_LINE,
            READER_BRACKET_NUMERIC_HEADING_LINE,
            READER_DECORATED_HEADING_LINE,
            READER_HEADING_LINE,
            READER_NUMERIC_HEADING_LINE,
            READER_SPECIAL_HEADING_LINE,
            READER_SUFFIX_NUMBER_HEADING_LINE,
            _reader_label_number,
        )

        named: List[Dict[str, Any]] = []
        numeric_by_style: Dict[str, List[Dict[str, Any]]] = {
            "numeric": [],
            "bracket_number": [],
            "body_number": [],
            "suffix_number": [],
        }
        decorated_unnumbered: List[Dict[str, Any]] = []
        with source_path.open("rb") as handle:
            while True:
                offset = handle.tell()
                raw_line = handle.readline()
                if not raw_line:
                    break
                decoded = raw_line.decode("utf-8", errors="replace").lstrip("\ufeff")
                line = decoded.rstrip("\r\n")
                stripped = line.strip()
                if not stripped or len(stripped) > 220:
                    continue

                # 站点整理文本常把章节名在正文开头再缩进复述一遍。结构标题
                # 通常顶格或至多两个空格，四空格以上的行按正文处理。
                leading_spaces = len(line) - len(line.lstrip(" \t"))
                if leading_spaces > 2:
                    continue

                match = READER_HEADING_LINE.fullmatch(stripped)
                if match:
                    title_prefix = stripped[match.end("label") :]
                    if (
                        match.group("unit") == "节"
                        and title_prefix
                        and not (
                            title_prefix[0].isspace()
                            or title_prefix[0] in ":：、，,；;。—-·."
                        )
                    ):
                        # ``第一节课她就……`` is normal prose, not a
                        # section heading.  Chapter/回 forms without a
                        # separator remain supported for legacy TXT files.
                        continue
                    named.append(
                        {
                            "offset": offset,
                            "label": re.sub(r"\s+", "", match.group("label")),
                            "title": cls._clean_reader_title(match.group("title")),
                        }
                    )
                    continue

                match = READER_SPECIAL_HEADING_LINE.fullmatch(stripped)
                if match:
                    named.append(
                        {
                            "offset": offset,
                            "label": re.sub(r"\s+", "", match.group("label")),
                            "title": cls._clean_reader_title(match.group("title")),
                            "special": True,
                        }
                    )
                    continue

                match = READER_BODY_NUMBER_HEADING_LINE.fullmatch(stripped)
                if match:
                    chapter_number = int(match.group("number"))
                    prefix = cls._clean_reader_title(match.group("prefix"))
                    extra_title = cls._clean_reader_title(match.group("title"))
                    numeric_by_style["body_number"].append(
                        {
                            "offset": offset,
                            "number": chapter_number,
                            "label": f"第{chapter_number}章",
                            "title": extra_title or f"{prefix}{chapter_number}",
                            "high_confidence": True,
                        }
                    )
                    continue

                match = READER_BRACKET_NUMERIC_HEADING_LINE.fullmatch(stripped)
                if match:
                    chapter_number = int(match.group("number"))
                    numeric_by_style["bracket_number"].append(
                        {
                            "offset": offset,
                            "number": chapter_number,
                            "label": f"第{chapter_number}章",
                            "title": cls._clean_reader_title(match.group("title")),
                            "high_confidence": True,
                        }
                    )
                    continue

                match = READER_NUMERIC_HEADING_LINE.fullmatch(stripped)
                if match:
                    chapter_number = int(match.group("number"))
                    title = cls._clean_reader_title(match.group("title"))
                    structural_marker = bool(
                        re.search(r"(?:章|回|节|[☆★◆◇●○◎※▶▷])", stripped)
                    )
                    numeric_by_style["numeric"].append(
                        {
                            "offset": offset,
                            "number": chapter_number,
                            "label": f"第{chapter_number}章",
                            "title": title,
                            "high_confidence": bool(structural_marker or title),
                        }
                    )
                    continue

                match = READER_SUFFIX_NUMBER_HEADING_LINE.fullmatch(stripped)
                if match:
                    number_text = match.group("bracket_number") or match.group("number")
                    chapter_number = int(number_text)
                    base_title = cls._clean_reader_title(match.group("title"))
                    if base_title and not base_title.isdigit():
                        display_title = (
                            f"{base_title}【{chapter_number}】"
                            if match.group("bracket_number")
                            else f"{base_title}{chapter_number}"
                        )
                        numeric_by_style["suffix_number"].append(
                            {
                                "offset": offset,
                                "number": chapter_number,
                                "label": f"第{chapter_number}章",
                                "title": display_title,
                                "base_title": base_title,
                                "high_confidence": bool(match.group("bracket_number")),
                            }
                        )
                    continue

                match = READER_DECORATED_HEADING_LINE.fullmatch(stripped)
                if match:
                    title = cls._clean_reader_title(match.group("title"))
                    if title:
                        decorated_unnumbered.append(
                            {
                                "offset": offset,
                                "title": title,
                            }
                        )

        deduped_named: List[Dict[str, Any]] = []
        for item in named:
            previous = deduped_named[-1] if deduped_named else None
            if (
                previous
                and previous["label"] == item["label"]
                and previous["title"] == item["title"]
                and int(item["offset"]) - int(previous["offset"]) <= 4096
            ):
                continue
            deduped_named.append(item)

        def candidate_runs(
            candidates: List[Dict[str, Any]],
        ) -> List[List[Dict[str, Any]]]:
            """Return plausible monotonic chapter runs for one source style."""

            runs: List[List[Dict[str, Any]]] = []
            for start_index, first in enumerate(candidates):
                if int(first["number"]) > 5:
                    continue
                run = [first]
                previous_number = int(first["number"])
                tail = candidates[start_index + 1 :]
                for index, item in enumerate(tail):
                    number = int(item["number"])
                    if number <= previous_number:
                        # A reset normally means a duplicated table of
                        # contents or another volume.  Score runs separately
                        # instead of silently keeping a dense TOC.
                        if number <= 5 and len(run) >= 2:
                            break
                        continue
                    if number - previous_number > 50:
                        continue
                    if number - previous_number > 1:
                        # 更新公告偶尔被误标成后续章节号，例如第817章后先出现
                        # “828.今天更新晚一点”，随后才是真正的第818章。
                        has_closer_candidate = any(
                            previous_number < int(candidate["number"]) < number
                            for candidate in tail[index + 1 : index + 65]
                        )
                        if has_closer_candidate:
                            continue
                    run.append(item)
                    previous_number = number
                runs.append(run)
            return runs

        def eligible_run(
            run: List[Dict[str, Any]],
        ) -> tuple[bool, float, int]:
            if not run:
                return False, 0.0, 0
            numbers = [int(item["number"]) for item in run]
            adjacent_steps = sum(
                1
                for previous, current in zip(numbers, numbers[1:])
                if current - previous == 1
            )
            continuity = adjacent_steps / max(len(numbers) - 1, 1)
            span = int(run[-1]["offset"]) - int(run[0]["offset"])
            if len(run) >= 8:
                return continuity >= 0.55, continuity, span
            if len(run) >= 3:
                return (
                    (numbers[0] <= 3 and continuity >= 0.8 and span >= 512),
                    continuity,
                    span,
                )
            if len(run) == 2:
                return (numbers == [1, 2] and span >= 512), continuity, span
            stat_size = source_path.stat().st_size
            return (
                (
                    numbers[0] == 1
                    and bool(run[0].get("high_confidence"))
                    and int(run[0]["offset"]) <= 64 * 1024
                    and stat_size - int(run[0]["offset"]) >= 512
                ),
                continuity,
                span,
            )

        numeric_sequence: List[Dict[str, Any]] = []
        numeric_style = "numeric"
        numeric_score: tuple[int, float, int] = (0, 0.0, 0)
        # Some legacy romance exports use local part numbers in the title,
        # e.g. ``一夜风流1`` / ``一夜风流2`` followed by another title that
        # restarts at 1.  Qualify each repeated base title independently,
        # then combine those structural groups in file order.
        suffix_groups: Dict[str, List[Dict[str, Any]]] = {}
        for item in numeric_by_style["suffix_number"]:
            suffix_groups.setdefault(str(item["base_title"]), []).append(item)
        qualified_suffixes: List[Dict[str, Any]] = []
        for items in suffix_groups.values():
            runs = candidate_runs(items)
            best_run = max(runs, key=len, default=[])
            eligible, _continuity, _span = eligible_run(best_run)
            if eligible or (
                len(best_run) >= 2
                and [int(item["number"]) for item in best_run[:2]] == [1, 2]
            ):
                qualified_suffixes.extend(best_run)
        qualified_suffixes.sort(key=lambda item: int(item["offset"]))
        if len(qualified_suffixes) >= 2:
            numeric_sequence = [
                {
                    **item,
                    "number": index,
                    "label": f"第{index}章",
                }
                for index, item in enumerate(qualified_suffixes, start=1)
            ]
            numeric_style = "suffix_number"
            numeric_score = (
                len(numeric_sequence),
                1.0,
                int(numeric_sequence[-1]["offset"])
                - int(numeric_sequence[0]["offset"]),
            )

        for style, candidates in numeric_by_style.items():
            for run in candidate_runs(candidates):
                eligible, continuity, span = eligible_run(run)
                if not eligible:
                    continue
                score = (len(run), continuity, span)
                if score > numeric_score:
                    numeric_sequence = run
                    numeric_style = style
                    numeric_score = score

        # Some exports use a decoration as the only chapter marker, for
        # example ``☆、时医生``.  Accept it only when multiple markers span
        # meaningful body content; this keeps ordinary bullet lists out.
        if len(decorated_unnumbered) >= 2:
            decorated_span = int(decorated_unnumbered[-1]["offset"]) - int(
                decorated_unnumbered[0]["offset"]
            )
            unique_titles = {str(item["title"]) for item in decorated_unnumbered}
            minimum_span = 512 if len(decorated_unnumbered) == 2 else 1024
            if (
                decorated_span >= minimum_span
                and len(unique_titles) >= min(len(decorated_unnumbered), 3)
                and len(decorated_unnumbered) > len(numeric_sequence)
            ):
                numeric_sequence = [
                    {
                        **item,
                        "number": index,
                        "label": f"第{index}章",
                    }
                    for index, item in enumerate(
                        decorated_unnumbered,
                        start=1,
                    )
                ]
                numeric_style = "decorated"
                numeric_score = (
                    len(numeric_sequence),
                    1.0,
                    decorated_span,
                )

        # Standard ``第X章`` headings are stronger evidence than isolated
        # numeric body lines.  Long Fanqie exports can contain hundreds of
        # standalone numbers (stats, dates, coordinates); when the named
        # sequence overwhelmingly dominates, do not let those false numeric
        # candidates replace the real chapter catalog.
        if len(deduped_named) >= 8 and len(deduped_named) >= max(
            len(numeric_sequence) * 2, 8
        ):
            return (
                deduped_named,
                "named",
                [int(item["offset"]) for item in deduped_named],
            )

        if numeric_sequence:
            numeric_numbers = {int(item["number"]) for item in numeric_sequence}
            first_number = int(numeric_sequence[0]["number"])
            last_number = int(numeric_sequence[-1]["number"])
            merged = list(numeric_sequence)
            for item in deduped_named:
                if item.get("special"):
                    merged.append(item)
                    continue
                chapter_number = _reader_label_number(item["label"])
                if chapter_number is None:
                    continue
                if (
                    chapter_number in numeric_numbers
                    or chapter_number < first_number
                    or chapter_number > last_number + 50
                ):
                    continue
                merged.append({**item, "number": chapter_number})
                numeric_numbers.add(chapter_number)
            merged.sort(key=lambda item: int(item["offset"]))
            boundaries = sorted(
                {int(item["offset"]) for item in [*merged, *deduped_named]}
            )
            return merged, numeric_style, boundaries

        return (
            deduped_named,
            "named",
            [int(item["offset"]) for item in deduped_named],
        )
