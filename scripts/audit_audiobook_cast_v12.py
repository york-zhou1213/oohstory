#!/usr/bin/env python3
"""Bounded live audit for the books that have entered audiobook prewarming."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from uuid import uuid4

import httpx


BOOK_IDS = (
    "ujTyG7AEA-iFCBbcN9EP9w",
    "s7MHR4LQHaqOZwVbzeVlXA",
    "Vx4izt0Y61JZGLZ1gGKuoQ",
    "STE4870kuq85gRhysYJkoQ",
    "jNN5eHQmdPfyNxXILakcRA",
    "eUoDQTrB-0QbhKMHxhMglQ",
    "oV5RFWgNywznCvqzb2Iqdg",
    "GJYen4P3hTWOYHPPOICyCA",
    "AcfIlKg8P6VIXytZ85VchQ",
)


def sample_chapters(chapters: list[dict]) -> list[int]:
    readable = [item for item in chapters if not bool(item.get("is_front_matter"))]
    if not readable:
        readable = chapters
    indexes = sorted({0, len(readable) // 2, len(readable) - 1})
    return [int(readable[index]["id"]) for index in indexes]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8091")
    args = parser.parse_args()
    collisions: list[dict] = []
    narrator_mismatches: list[dict] = []
    dialogue_narrator_collisions: list[dict] = []
    rejected_identity_candidate_count = 0
    rejected_identity_candidate_samples: list[dict] = []
    audited = 0
    with httpx.Client(
        base_url=args.base_url,
        timeout=30,
        trust_env=False,
        headers={"User-Agent": "Mozilla/5.0 OOHStory-v12-audit"},
    ) as client:
        for book_id in BOOK_IDS:
            catalog = client.get(f"/api/v1/books/{book_id}/chapters")
            catalog.raise_for_status()
            chapters = catalog.json().get("chapters") or []
            for chapter_id in sample_chapters(chapters):
                client_id = f"v12audit{uuid4().hex}"
                proxy_headers = {
                    "X-Forwarded-For": f"198.18.{audited // 250}.{audited % 250 + 1}",
                    "X-Audiobook-Client": client_id,
                }
                response = client.post(
                    "/api/v1/audiobook/sessions",
                    json={
                        "book_id": book_id,
                        "chapter_id": chapter_id,
                        "client_id": client_id,
                        "mode": "smart",
                        "narrator": "lingxian",
                        "voice": "nuanxi",
                        "emotion": "auto",
                        "rate": 1.0,
                        "resume": False,
                    },
                    headers=proxy_headers,
                )
                response.raise_for_status()
                payload = response.json()
                manifest = payload["current"]
                if (
                    manifest.get("requested_narrator") != "lingxian"
                    or manifest.get("effective_narrator") != "lingxian"
                ):
                    narrator_mismatches.append({
                        "book_id": book_id,
                        "chapter_id": chapter_id,
                        "requested": manifest.get("requested_narrator"),
                        "effective": manifest.get("effective_narrator"),
                    })
                by_voice: dict[str, set[str]] = defaultdict(set)
                for segment in manifest.get("segments") or []:
                    speaker = str(segment.get("speaker") or "")
                    voice = str(segment.get("voice") or "")
                    if segment.get("kind") == "dialogue" and speaker and voice:
                        by_voice[voice].add(speaker)
                    if segment.get("identity_candidate_rejected") is True:
                        rejected_identity_candidate_count += 1
                        if len(rejected_identity_candidate_samples) < 25:
                            rejected_identity_candidate_samples.append({
                                "book_id": book_id,
                                "chapter_id": chapter_id,
                                "segment_index": segment.get("index"),
                                "speaker_source": segment.get("speaker_source"),
                            })
                    if segment.get("kind") == "dialogue" and voice == "lingxian":
                        dialogue_narrator_collisions.append({
                            "book_id": book_id,
                            "chapter_id": chapter_id,
                            "speaker": speaker,
                            "segment_index": segment.get("index"),
                        })
                for voice, speakers in by_voice.items():
                    if len(speakers) > 1:
                        collisions.append({
                            "book_id": book_id,
                            "chapter_id": chapter_id,
                            "voice": voice,
                            "speakers": sorted(speakers),
                        })
                cancelled = client.delete(
                    f"/api/v1/audiobook/sessions/{payload['session_id']}",
                    headers=proxy_headers,
                )
                cancelled.raise_for_status()
                audited += 1
    print(json.dumps({
        "books": len(BOOK_IDS),
        "chapters": audited,
        "narrator_mismatches": narrator_mismatches,
        "dialogue_narrator_collisions": dialogue_narrator_collisions,
        "rejected_identity_candidate_count": rejected_identity_candidate_count,
        "rejected_identity_candidate_samples": rejected_identity_candidate_samples,
        "same_chapter_voice_collisions": collisions,
    }, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
