from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Question
from app.schemas.questions import QuestionReviewRequest, QuestionWithdrawRequest
from app.services.literacy_catalog import normalize_literacy_label


class QuestionReviewConflict(RuntimeError):
    pass


class QuestionReviewValidation(ValueError):
    pass


_CHOICE_RE = re.compile(r"^[A-H]$")
_NUMERIC_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")


def validate_review_items(question_type: str, items: list[dict[str, Any]]) -> None:
    if question_type not in {
        "choice", "fill_blank", "true_false", "calculation", "solution"
    }:
        raise QuestionReviewValidation("当前题型不支持结构化答案审核")
    if not items:
        raise QuestionReviewValidation("至少需要一个答案项")

    for item in items:
        answer = item["answer"].strip()
        candidate_answers = [answer, *item.get("accepted_answers", [])]
        match_type = item["answer_match_type"]
        if any("[ASSET:" in candidate for candidate in candidate_answers):
            raise QuestionReviewValidation(
                f"答案项 {item['item_id']} 仍引用原始图片，请改为文字、数值或 LaTeX 公式"
            )
        if question_type == "choice":
            if match_type != "choice":
                raise QuestionReviewValidation("选择题只能使用选项匹配")
            if any(
                not _CHOICE_RE.fullmatch(candidate.upper())
                for candidate in candidate_answers
            ):
                raise QuestionReviewValidation(
                    f"答案项 {item['item_id']} 必须是 A-H 中的单个选项"
                )
        elif question_type == "true_false":
            if (
                match_type != "exact"
                or any(
                    candidate not in {"正确", "错误"}
                    for candidate in candidate_answers
                )
            ):
                raise QuestionReviewValidation("判断题答案必须是“正确”或“错误”")
        elif question_type in {"calculation", "solution"}:
            if match_type != "manual":
                raise QuestionReviewValidation(
                    "计算题和简答题当前只能保存人工评分参考答案"
                )
        elif match_type not in {"numeric", "exact", "latex_exact"}:
            raise QuestionReviewValidation("填空题匹配类型只能是数值、精确文本或公式")
        elif match_type == "numeric" and any(
            not _NUMERIC_RE.fullmatch(candidate)
            for candidate in candidate_answers
        ):
            raise QuestionReviewValidation(
                f"答案项 {item['item_id']} 不是标准整数或小数；分数/公式请使用公式匹配"
            )


def _locked_question(db: Session, question_id: UUID) -> Question:
    question = db.scalar(
        select(Question).where(
            Question.id == question_id,
            Question.archived_at.is_(None),
        ).with_for_update()
    )
    if question is None:
        raise LookupError("question not found")
    return question


def _answer_payload(request: QuestionReviewRequest) -> dict[str, Any]:
    next_blank_by_subquestion: dict[int, int] = {}
    payload_items = []
    for index, item in enumerate(request.items, start=1):
        subquestion_no = item.subquestion_no or 1
        blank_no = item.blank_no or next_blank_by_subquestion.get(
            subquestion_no, 1
        )
        next_blank_by_subquestion[subquestion_no] = blank_no + 1
        answer = (
            item.answer.upper()
            if item.answer_match_type == "choice"
            else item.answer
        )
        payload_item = {
            "item_id": f"sub_{subquestion_no}.blank_{blank_no}",
            "subquestion_id": f"sub_{subquestion_no}",
            "subquestion_no": subquestion_no,
            "blank_id": f"blank_{blank_no}",
            "blank_no": blank_no,
            "order_no": index,
            "answer": answer,
            "answer_match_type": item.answer_match_type,
            "reference_answer_raw": item.answer,
        }
        accepted_answers = list(dict.fromkeys([
            answer,
            *(
                value.upper()
                if item.answer_match_type == "choice"
                else value
                for value in item.accepted_answers
            ),
        ]))
        if len(accepted_answers) > 1:
            payload_item["accepted_answers"] = accepted_answers
        payload_items.append(payload_item)
    return {
        "kind": "group",
        "schema_version": "answer-units-v1",
        "items": payload_items,
    }


def _normalization_rules(request: QuestionReviewRequest) -> dict[str, Any]:
    types = {item.answer_match_type for item in request.items}
    return {
        "trim_whitespace": True,
        "normalize_full_width": True,
        "case_insensitive": "choice" in types,
        "latex_source_exact": "latex_exact" in types,
    }


def _mutate(
    db: Session,
    question_id: UUID,
    request: QuestionReviewRequest,
    *,
    promote: bool,
) -> Question:
    question = _locked_question(db, question_id)
    if question.grading_readiness != "raw_answer_only":
        raise QuestionReviewConflict("请先将题目撤回到待审核状态，再修改答案")
    if question.review_revision != request.expected_review_revision:
        raise QuestionReviewConflict("题目已被其他人修改，请刷新后再提交")

    items = [item.model_dump() for item in request.items]
    validate_review_items(question.question_type, items)
    if promote:
        if question.question_type in {"calculation", "solution"}:
            raise QuestionReviewValidation(
                "计算题和简答题可以保存结构化参考答案，但暂不进入自动判分池"
            )
        try:
            question.core_literacy = normalize_literacy_label(question.core_literacy)
        except ValueError as exc:
            raise QuestionReviewValidation(
                "题目进入自动组卷池前必须标注正式核心素养：" + str(exc)
            ) from exc
    before_revision = question.review_revision
    before_answer = question.correct_answer
    answer = _answer_payload(request)

    question.correct_answer = answer
    question.answer_match_type = "group"
    question.answer_normalization_rule = _normalization_rules(request)
    question.review_revision += 1
    question.reviewed_by = request.reviewer_name
    question.reviewed_at = datetime.now(timezone.utc)
    question.import_issues = [
        issue for issue in (question.import_issues or [])
        if issue != "answer_items_not_structured"
    ]
    if promote:
        question.grading_readiness = "machine_ready"

    history = list(question.review_history or [])
    history.append(
        {
            "event_id": str(uuid4()),
            "type": "promoted" if promote else "draft_saved",
            "at": question.reviewed_at.isoformat(),
            "reviewer": request.reviewer_name,
            "note": (request.review_note or "").strip() or None,
            "before_revision": before_revision,
            "after_revision": question.review_revision,
            "before_answer": before_answer,
            "after_answer": answer,
        }
    )
    question.review_history = history
    db.flush()
    return question


def save_review_draft(
    db: Session, question_id: UUID, request: QuestionReviewRequest
) -> Question:
    return _mutate(db, question_id, request, promote=False)


def promote_question(
    db: Session, question_id: UUID, request: QuestionReviewRequest
) -> Question:
    return _mutate(db, question_id, request, promote=True)


def withdraw_question(
    db: Session, question_id: UUID, request: QuestionWithdrawRequest
) -> Question:
    question = _locked_question(db, question_id)
    if question.grading_readiness != "machine_ready":
        raise QuestionReviewConflict("该题当前不在自动组卷池中")
    if question.question_type not in {"choice", "fill_blank", "true_false"}:
        raise QuestionReviewValidation("当前题型不在自动判分池中")
    if question.review_revision != request.expected_review_revision:
        raise QuestionReviewConflict("题目已被其他人修改，请刷新后再操作")

    before_revision = question.review_revision
    question.grading_readiness = "raw_answer_only"
    question.review_revision += 1
    question.reviewed_by = request.reviewer_name
    question.reviewed_at = datetime.now(timezone.utc)
    history = list(question.review_history or [])
    history.append(
        {
            "event_id": str(uuid4()),
            "type": "withdrawn",
            "at": question.reviewed_at.isoformat(),
            "reviewer": request.reviewer_name,
            "note": (request.review_note or "").strip() or None,
            "before_revision": before_revision,
            "after_revision": question.review_revision,
            "before_answer": question.correct_answer,
            "after_answer": question.correct_answer,
        }
    )
    question.review_history = history
    db.flush()
    return question
