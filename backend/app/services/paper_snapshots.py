from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import PaperInstanceItem, Question, QuestionExamPoint


def load_question(db: Session, question_id: UUID) -> Question:
    question = db.scalar(
        select(Question)
        .options(selectinload(Question.exam_point_links).selectinload(QuestionExamPoint.exam_point))
        .where(Question.id == question_id, Question.archived_at.is_(None))
    )
    if question is None:
        raise LookupError("question not found")
    return question


def snapshot_payload(question: Question) -> dict[str, Any]:
    main = None
    related = []
    for link in question.exam_point_links:
        value = {"id": link.exam_point.code, "name": link.exam_point.name}
        if link.relation_type == "main":
            main = value
        else:
            related.append(value)
    if main is None:
        raise ValueError(f"question {question.original_qid} has no main exam point")
    return {
        "question_id": question.id,
        "question_snapshot": {
            "schema_version": question.schema_version,
            "original_qid": question.original_qid,
            "chapter_code": question.chapter_code,
            "chapter_name": question.chapter_name,
            "section": question.section,
            "question_text": question.question_text,
            "question_type": question.question_type,
            "difficulty": question.difficulty,
            "assets": deepcopy(question.assets or []),
            "solution": question.solution,
            "wrong_analysis": question.wrong_analysis,
        },
        "options_snapshot": deepcopy(question.options),
        "answer_snapshot": deepcopy(question.correct_answer),
        "answer_rule_snapshot": {
            "answer_match_type": question.answer_match_type,
            "answer_normalization_rule": deepcopy(question.answer_normalization_rule),
        },
        "exam_point_snapshot": {"main": main, "related": sorted(related, key=lambda item: item["id"])},
    }


def new_item(*, paper_id: UUID, order_no: int, payload: dict[str, Any],
             selection_audit: dict[str, Any] | None) -> PaperInstanceItem:
    return PaperInstanceItem(
        paper_instance_id=paper_id,
        question_id=payload["question_id"],
        order_no=order_no,
        question_snapshot=deepcopy(payload["question_snapshot"]),
        options_snapshot=deepcopy(payload["options_snapshot"]),
        answer_snapshot=deepcopy(payload["answer_snapshot"]),
        answer_rule_snapshot=deepcopy(payload["answer_rule_snapshot"]),
        exam_point_snapshot=deepcopy(payload["exam_point_snapshot"]),
        selection_audit=deepcopy(selection_audit),
    )


def copy_item(*, source: PaperInstanceItem, paper_id: UUID) -> PaperInstanceItem:
    audit = deepcopy(source.selection_audit or {})
    audit["source"] = "version_copy"
    audit["copied_from_item_id"] = str(source.id)
    return PaperInstanceItem(
        paper_instance_id=paper_id,
        question_id=source.question_id,
        order_no=source.order_no,
        question_snapshot=deepcopy(source.question_snapshot),
        options_snapshot=deepcopy(source.options_snapshot),
        answer_snapshot=deepcopy(source.answer_snapshot),
        answer_rule_snapshot=deepcopy(source.answer_rule_snapshot),
        exam_point_snapshot=deepcopy(source.exam_point_snapshot),
        answer_region=deepcopy(source.answer_region),
        selection_audit=audit,
    )
