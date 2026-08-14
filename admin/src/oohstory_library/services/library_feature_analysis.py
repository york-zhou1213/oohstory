from __future__ import annotations

from collections import Counter
import re
from typing import Any, Dict, List, Optional


class LibraryFeatureAnalysisMixin:
    def _summary_from_sample(sample: str) -> str:
        """Return a real synopsis or the first chapter's first 300 characters."""
        from .electronic_library import (
            CHAPTER_HEADING_TOKEN_RE,
            LOCAL_MEDIA_MARKER_RE,
            SUMMARY_HEADING_RE,
            SUMMARY_METADATA_RE,
        )

        def normalized_lines(value: str) -> List[str]:
            return [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]

        def is_noise(line: str) -> bool:
            return bool(
                not line
                or line.startswith("<<<TONE_SAMPLE:")
                or LOCAL_MEDIA_MARKER_RE.fullmatch(line)
                or line in {"分卷封面", "插画", "插图", "展开", "收起"}
                or re.fullmatch(r"[（(]?插图\s*\d+[）)]?", line)
            )

        # Explicit source synopsis wins. Stop at a volume/chapter/media boundary
        # so local asset references can never leak into public metadata.
        intro_lines: List[str] = []
        capturing = False
        for line in normalized_lines(sample):
            match = SUMMARY_HEADING_RE.match(line)
            if match:
                capturing = True
                remainder = match.group(1).strip()
                if remainder and not is_noise(remainder):
                    intro_lines.append(remainder)
                continue
            if not capturing:
                if (
                    line.startswith("【")
                    or CHAPTER_HEADING_TOKEN_RE.search(line)
                    or LOCAL_MEDIA_MARKER_RE.fullmatch(line)
                    or line in {"分卷封面", "插画", "插图"}
                ):
                    break
                continue
            if (
                line.startswith("<<<TONE_SAMPLE:")
                or line.startswith("【")
                or CHAPTER_HEADING_TOKEN_RE.search(line)
                or LOCAL_MEDIA_MARKER_RE.fullmatch(line)
                or line in {"分卷封面", "插画", "插图"}
            ):
                break
            if not is_noise(line):
                intro_lines.append(line)
            if len("\n".join(intro_lines)) >= 600:
                break
        explicit = "\n".join(intro_lines).strip()[:600].rstrip()
        if explicit:
            return explicit

        # For large files only the 00% section is guaranteed to contain the
        # first chapter. Later tone samples must never become the fallback.
        marked_sections = re.split(
            r"(?m)^<<<TONE_SAMPLE:\d+>>>\s*$",
            sample,
        )
        leading = next((part for part in marked_sections if part.strip()), sample)
        lines = normalized_lines(leading)
        start = None
        for index, line in enumerate(lines):
            if CHAPTER_HEADING_TOKEN_RE.search(line) and "人物介绍" not in line:
                start = index + 1
                break
        candidates = lines[start:] if start is not None else lines
        body_lines: List[str] = []
        for line in candidates:
            if start is not None and CHAPTER_HEADING_TOKEN_RE.search(line):
                break
            if (
                is_noise(line)
                or SUMMARY_METADATA_RE.match(line)
                or SUMMARY_HEADING_RE.match(line)
                or line.startswith("【")
                or line.startswith("声明：本书为")
                or line.startswith("用户上传之内容")
            ):
                continue
            body_lines.append(line)
            compact = re.sub(r"\s+", " ", " ".join(body_lines)).strip()
            if len(compact) >= 300:
                return compact[:300].rstrip()
        return re.sub(r"\s+", " ", " ".join(body_lines)).strip()[:300].rstrip()

    def _extract_features(
        title: str,
        category: str,
        sample: str,
        tone_document_frequency: Optional[Dict[str, int]] = None,
        corpus_size: int = 0,
    ) -> Dict[str, Any]:
        from .electronic_library import (
            CATEGORY_GENRES,
            CATEGORY_TONE_PRIORS,
            DEFAULT_TONE_PRIORS,
            GENRE_KEYWORD_PATTERN,
            GENRE_KEYWORDS,
            TONE_KEYWORD_PATTERN,
            TONE_RULES,
            ElectronicLibraryService,
        )

        sections = ElectronicLibraryService._tone_sections(sample)
        clean_sample = "\n".join(sections)
        haystack = f"{title}\n{category}\n{clean_sample}"
        genre_scores: Counter[str] = Counter()
        tone_scores: Counter[str] = Counter()
        keyword_counts: Dict[str, int] = {}
        genre_match_counts = Counter(
            match.group(0) for match in GENRE_KEYWORD_PATTERN.finditer(haystack)
        )
        title_tone_counts = Counter(
            match.group(0) for match in TONE_KEYWORD_PATTERN.finditer(title)
        )
        section_tone_counts = [
            Counter(match.group(0) for match in TONE_KEYWORD_PATTERN.finditer(section))
            for section in sections
        ]
        body_tone_counts: Counter[str] = Counter()
        for counts in section_tone_counts:
            body_tone_counts.update(counts)

        primary_genres = list(CATEGORY_GENRES.get(category, ()))
        for genre in primary_genres:
            genre_scores[genre] += 30
        for genre, keywords in GENRE_KEYWORDS.items():
            score = 0
            for keyword in keywords:
                count = genre_match_counts.get(keyword, 0)
                if count:
                    keyword_counts[keyword] = count
                    score += min(count, 8)
            if score:
                genre_scores[genre] += score
        # 规则证据先用语料逆文档频率降噪，再与题材先验合成候选。
        # 先验只提供低置信度兜底，确保每本小说至少有一个主基调。
        tone_evidence: Dict[str, Dict[str, Any]] = {}
        for tone, rule in TONE_RULES.items():
            evidence_score = 0.0
            distinct_hits = 0
            strong_hits = 0
            title_hits = 0
            matched: Dict[str, int] = {}
            covered_sections: set[int] = set()
            for level, body_weight, title_weight, cap in (
                ("strong", 12.0, 24.0, 3),
                ("medium", 5.0, 18.0, 4),
                ("weak", 2.0, 10.0, 3),
            ):
                for keyword in rule.get(level, ()):
                    title_count = title_tone_counts.get(keyword, 0)
                    body_count = body_tone_counts.get(keyword, 0)
                    total_count = title_count + body_count
                    if not total_count:
                        continue
                    matched[keyword] = total_count
                    keyword_counts[keyword] = max(
                        keyword_counts.get(keyword, 0), total_count
                    )
                    distinct_hits += 1
                    title_hits += title_count
                    if level == "strong":
                        strong_hits += total_count
                    idf = ElectronicLibraryService._tone_idf(
                        keyword, tone_document_frequency, corpus_size
                    )
                    evidence_score += min(body_count, cap) * body_weight * idf
                    if title_count:
                        evidence_score += title_weight * idf
                    for index, section_counts in enumerate(section_tone_counts):
                        if section_counts.get(keyword, 0):
                            covered_sections.add(index)
            if distinct_hits >= 2:
                evidence_score += min(distinct_hits - 1, 4) * 2
            if len(covered_sections) >= 2:
                evidence_score += min(len(covered_sections) - 1, 4) * 2
            eligible = bool(
                evidence_score >= 18
                and (strong_hits or title_hits or distinct_hits >= 2)
            )
            if eligible:
                tone_scores[tone] = round(evidence_score, 2)
                tone_evidence[tone] = {
                    "evidence_score": round(evidence_score, 2),
                    "matched": matched,
                    "section_coverage": len(covered_sections),
                    "title_hits": title_hits,
                }

        inferred_genres = [
            name
            for name, score in genre_scores.most_common()
            if name not in primary_genres and score >= 10
        ]
        genre_tags = [*primary_genres, *inferred_genres[:2]]
        if not genre_tags:
            genre_tags = [category or "网络小说"]
        priors = CATEGORY_TONE_PRIORS.get(category, DEFAULT_TONE_PRIORS)
        candidate_scores: Dict[str, float] = {
            tone: float(score) for tone, score in tone_scores.items()
        }
        for index, tone in enumerate(priors):
            candidate_scores[tone] = candidate_scores.get(tone, 0.0) + max(
                2.0, 8.0 - index * 1.5
            )
        ranked_tones = sorted(
            candidate_scores.items(),
            key=lambda item: (-item[1], list(TONE_RULES).index(item[0])),
        )
        if not ranked_tones:
            ranked_tones = [(DEFAULT_TONE_PRIORS[0], 1.0)]
        top_score = ranked_tones[0][1]
        candidate_names = [name for name, _ in ranked_tones[:5]]
        primary_tones = [candidate_names[0]]
        if (
            len(ranked_tones) > 1
            and ranked_tones[1][1] >= top_score * 0.82
            and ranked_tones[1][0] in tone_scores
        ):
            primary_tones.append(ranked_tones[1][0])
        secondary_floor = max(18.0, float(tone_scores.get(primary_tones[0], 0)) * 0.55)
        secondary_tones = [
            name
            for name in candidate_names
            if (
                name not in primary_tones
                and name in tone_scores
                and float(tone_scores[name]) >= secondary_floor
            )
        ][:3]
        tone_tags = [*primary_tones, *secondary_tones]

        top_name = primary_tones[0]
        top_rule_score = float(tone_scores.get(top_name, 0))
        if top_rule_score >= 50:
            tone_confidence = 0.9
        elif top_rule_score >= 32:
            tone_confidence = 0.8
        elif top_rule_score >= 18:
            tone_confidence = 0.68
        else:
            tone_confidence = 0.38
        if tone_evidence.get(top_name, {}).get("title_hits"):
            tone_confidence = max(tone_confidence, 0.84)
        if tone_evidence.get(top_name, {}).get("section_coverage", 0) >= 3:
            tone_confidence = min(0.95, tone_confidence + 0.05)
        score_gap = (
            top_score - ranked_tones[1][1] if len(ranked_tones) > 1 else top_score
        )
        review_required = bool(
            tone_confidence < 0.72 or score_gap < max(4.0, top_score * 0.12)
        )
        tone_source = "rule_evidence" if top_rule_score else "category_prior"

        # 模型只接收短代表片段，不接收全文。
        representative_fragments: List[str] = []
        for section in sections:
            compact = re.sub(r"\s+", " ", section).strip()
            if len(compact) >= 80:
                representative_fragments.append(compact[:240])
            if len(representative_fragments) >= 4:
                break
        candidate_details = []
        for name, score in ranked_tones[:5]:
            detail = tone_evidence.get(name, {})
            candidate_details.append(
                {
                    "name": name,
                    "score": round(float(score), 2),
                    "matched": detail.get("matched", {}),
                    "section_coverage": detail.get("section_coverage", 0),
                }
            )
        tone_evidence_payload = {
            "primary": primary_tones,
            "secondary": secondary_tones,
            "candidates": candidate_details,
            "fragments": representative_fragments,
            "local_confidence": round(tone_confidence, 3),
            "source": tone_source,
            "review_required": review_required,
        }
        summary = ElectronicLibraryService._summary_from_sample(sample)
        searchable = f"{title} {category} {' '.join(genre_tags)} {' '.join(tone_tags)} {summary}"[
            :5000
        ]
        return {
            "genre_tags": genre_tags,
            "tone_tags": tone_tags,
            "primary_tone_tags": primary_tones,
            "secondary_tone_tags": secondary_tones,
            "tone_confidence": round(tone_confidence, 3),
            "tone_source": tone_source,
            "tone_evidence": tone_evidence_payload,
            "tone_review_status": "pending" if review_required else "not_needed",
            "keyword_counts": keyword_counts,
            "summary": summary,
            "searchable_text": searchable,
        }
