from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import PaperInstanceItem, Question


_STUDENT_QUESTION_KEYS = {
    "schema_version",
    "original_qid",
    "chapter_code",
    "chapter_name",
    "grade",
    "section",
    "core_literacy",
    "is_required",
    "question_text",
    "question_type",
    "difficulty",
    "assets",
}
_STUDENT_PRIVATE_NESTED_KEYS = {
    "answer",
    "answer_snapshot",
    "answer_rule_snapshot",
    "correct",
    "correct_answer",
    "is_correct",
    "solution",
    "wrong_analysis",
}


def student_safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: student_safe_value(item)
            for key, item in value.items()
            if key not in _STUDENT_PRIVATE_NESTED_KEYS
        }
    if isinstance(value, list):
        return [student_safe_value(item) for item in value]
    return deepcopy(value)


def student_safe_question_snapshot(snapshot: Any) -> dict[str, Any]:
    """Return only question fields that are safe to expose to a student."""
    if not isinstance(snapshot, dict):
        return {}
    return {
        key: student_safe_value(value)
        for key, value in snapshot.items()
        if key in _STUDENT_QUESTION_KEYS
    }


def load_question(db: Session, question_id: UUID) -> Question:
    question = db.scalar(
        select(Question)
        .options(selectinload(Question.main_knowledge_point))
        .where(Question.id == question_id, Question.archived_at.is_(None))
    )
    if question is None:
        raise LookupError("question not found")
    return question


def load_questions(db: Session, question_ids: set[UUID]) -> dict[UUID, Question]:
    if not question_ids:
        return {}
    questions = list(db.scalars(
        select(Question)
        .options(selectinload(Question.main_knowledge_point))
        .where(Question.id.in_(question_ids), Question.archived_at.is_(None))
    ).unique())
    if len(questions) != len(question_ids):
        raise LookupError("one or more questions not found")
    return {question.id: question for question in questions}


def snapshot_payload(question: Question) -> dict[str, Any]:
    knowledge_point = question.main_knowledge_point
    if knowledge_point is None:
        raise ValueError(f"question {question.original_qid} has no knowledge point")
    return {
        "question_id": question.id,
        "question_snapshot": {
            "schema_version": question.schema_version,
            "original_qid": question.original_qid,
            "chapter_code": question.chapter_code,
            "chapter_name": question.chapter_name,
            "grade": question.grade,
            "section": question.section,
            "core_literacy": question.core_literacy,
            "is_required": question.is_required,
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
        "knowledge_point_snapshot": {
            "id": knowledge_point.code,
            "name": knowledge_point.name,
        },
    }


def published_knowledge_point_snapshot(
    db: Session,
    question_id: UUID,
    fallback: Any,
) -> dict[str, Any]:
    """Preserve the published KP in teaching views and version copies.

    Catalog edits or archiving affect future selections, not past evidence.
    Unknown legacy codes must be explicitly migrated, never guessed from the
    current question's classification.
    """
    return deepcopy(fallback) if isinstance(fallback, dict) else {}


def current_selection_audit(
    audit: Any,
    knowledge_point_snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(audit, dict):
        return None
    value = deepcopy(audit)
    value["knowledge_point_code"] = knowledge_point_snapshot.get("id")
    value["knowledge_point_name"] = knowledge_point_snapshot.get("name")
    return value


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
        knowledge_point_snapshot=deepcopy(payload["knowledge_point_snapshot"]),
        selection_audit=deepcopy(selection_audit),
    )


def copy_item(
    *,
    source: PaperInstanceItem,
    paper_id: UUID,
    knowledge_point_snapshot: dict[str, Any] | None = None,
) -> PaperInstanceItem:
    resolved_snapshot = deepcopy(
        knowledge_point_snapshot or source.knowledge_point_snapshot
    )
    audit = current_selection_audit(source.selection_audit or {}, resolved_snapshot) or {}
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
        knowledge_point_snapshot=resolved_snapshot,
        answer_region=deepcopy(source.answer_region),
        selection_audit=audit,
    )
