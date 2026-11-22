"""Indexed, read-only catalog neighbours for teacher duplicate review."""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

from sqlalchemy import literal_column, select
from sqlalchemy.orm import Session

from app.models import Question, QuestionImportItem
from app.services.question_fingerprint_v2 import (
    catalog_content_key, normalized_options, normalized_text,
)
from app.services.question_import import active_candidate
from app.services.question_import_s4_exact import _candidate_key, exact_catalog_matches


VERSION = "s4-near-v2"
RECALL_LIMIT = 32
DISPLAY_LIMIT = 6
_NUMBER = re.compile(r"(?<![\w])[-+]?\d+(?:\.\d+)?(?:\^[-+]?\d+)?")
_SEARCH_EXPRESSION = literal_column("(questions.question_text || ' ' || coalesce(questions.options::text, ''))")


def _search_text(question_text: str, options: Any) -> str:
    stem = normalized_text(question_text, strip_question_number=True)
    choices = normalized_options(options) or []
    return " ".join([stem, *(" ".join(choice) for choice in choices)]).strip()


def _differences(candidate: dict[str, Any], question: Question,
                 candidate_key: dict[str, Any] | None,
                 formal_key: dict[str, Any] | None) -> tuple[list[str], str]:
    content = candidate.get("content") or {}
    left = normalized_text(content.get("question_text") or "", strip_question_number=True)
    right = normalized_text(question.question_text, strip_question_number=True)
    differences: list[str] = []
    if _NUMBER.findall(left) != _NUMBER.findall(right):
        differences.append("题干中的数字或指数不同")
    if left != right:
        differences.append("题干文字或公式不同")
    if normalized_options(content.get("options")) != normalized_options(question.options):
        differences.append("选项内容或顺序不同")

    if formal_key is None or candidate_key is None:
        image_relation = "无法可靠比对"
    elif bool(candidate_key["images"]) != bool(formal_key["images"]):
        image_relation = "一侧缺图"
    elif not candidate_key["images"]:
        image_relation = "均无题图"
    elif sorted(candidate_key["images"]) == sorted(formal_key["images"]):
        image_relation = "题图文件相同"
    else:
        image_relation = "题图文件不同"
    if image_relation not in {"均无题图", "题图文件相同"}:
        differences.append("题干文字相同，但题图文件不同，请人工核对是否为同一道题"
                           if left == right and image_relation == "题图文件不同"
                           else image_relation)
    return differences, image_relation


def catalog_near_matches(db: Session, batch_id: uuid.UUID) -> dict[str, Any]:
    """Use pg_trgm GiST KNN; scores are ranking hints, not duplicate decisions."""
    exact = exact_catalog_matches(db, batch_id)
    exact_by_item = {row["item_id"]: row for row in exact["results"]}
    items = {str(item.id): item for item in db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch_id
    )) if active_candidate(item) and not item.is_fake}
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    formal_keys: dict[uuid.UUID, dict[str, Any] | None] = {}
    for item_id, exact_row in exact_by_item.items():
        item = items[item_id]
        payload = item.payload or {}
        content = payload.get("content") or {}
        query_text = _search_text(content.get("question_text") or "", content.get("options"))
        candidate_key = _candidate_key(db, batch_id, item)
        if not query_text:
            results.append({"item_id": item_id, "revision": item.revision,
                            "near_matches": [], "query_ms": 0})
            continue
        exact_ids = {match["question_id"] for match in exact_row["matches"]}
        distance = _SEARCH_EXPRESSION.op("<->")(query_text)
        query_started = time.perf_counter()
        neighbors = db.execute(select(Question, distance).where(
            Question.archived_at.is_(None)
        ).order_by(distance).limit(RECALL_LIMIT)).all()
        query_ms = round((time.perf_counter() - query_started) * 1000, 2)
        matches: list[dict[str, Any]] = []
        for question, trigram_distance in neighbors:
            if str(question.id) in exact_ids:
                continue
            trigram_similarity = round(max(0.0, 1.0 - float(trigram_distance)), 3)
            if question.id not in formal_keys:
                formal_keys[question.id] = catalog_content_key(
                    question.question_text, question.options, question.assets, has_image=question.has_image
                )
            formal_key = formal_keys[question.id]
            differences, image_relation = _differences(payload, question, candidate_key, formal_key)
            matches.append({
                "question_id": str(question.id),
                "original_qid": question.original_qid,
                "question_text": question.question_text,
                "options": question.options or [],
                "review_revision": question.review_revision,
                "trigram_similarity": trigram_similarity,
                "differences": differences,
                "image_relation": image_relation,
            })
        matches.sort(key=lambda row: (-row["trigram_similarity"], row["original_qid"]))
        results.append({"item_id": item_id, "revision": item.revision,
                        "near_matches": matches[:DISPLAY_LIMIT], "query_ms": query_ms})
    return {"version": VERSION, "exact_version": exact["version"], "batch_id": str(batch_id),
            "unindexed_catalog_count": exact["unindexed_catalog_count"],
            "exact_results": exact["results"], "results": results,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "recall_limit": RECALL_LIMIT,
            "calibrated": False}
