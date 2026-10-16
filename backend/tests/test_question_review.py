from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal, get_db
from app.main import app
from app.models import Question
from app.services.question_review import QuestionReviewValidation, validate_review_items
from app.services.seed_importer import _preserve_reviewed_answer


@pytest.fixture
def db():
    with SessionLocal() as session:
        try:
            yield session
        finally:
            session.rollback()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as value:
            yield value
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_review_validation_is_scoped_and_rejects_unresolved_assets():
    validate_review_items(
        "fill_blank",
        [{"item_id": "1", "answer": r"\frac{1}{2}", "answer_match_type": "latex_exact"}],
    )
    with pytest.raises(QuestionReviewValidation):
        validate_review_items(
            "solution",
            [{"item_id": "1", "answer": "42", "answer_match_type": "exact"}],
        )
    with pytest.raises(QuestionReviewValidation):
        validate_review_items(
            "fill_blank",
            [{"item_id": "1", "answer": "[ASSET:missing.wmf]", "answer_match_type": "exact"}],
        )


def test_teacher_can_save_draft_then_promote_fill_blank(client, db):
    question = db.scalar(
        select(Question)
        .where(
            Question.grading_readiness == "raw_answer_only",
            Question.question_type == "fill_blank",
        )
        .order_by(Question.source_question_number)
        .limit(1)
    )
    assert question is not None
    original_revision = question.review_revision
    body = {
        "expected_review_revision": original_revision,
        "reviewer_name": "测试教师",
        "review_note": "事务内测试，不写入演示库",
        "items": [
            {"item_id": "blank_1", "answer": "-2.5", "answer_match_type": "numeric"},
            {
                "item_id": "blank_2",
                "answer": r"\frac{1}{2}",
                "answer_match_type": "latex_exact",
            },
        ],
    }

    saved = client.put(f"/api/v1/questions/{question.id}/review-draft", json=body)
    assert saved.status_code == 200, saved.text
    saved_body = saved.json()
    assert saved_body["grading_readiness"] == "raw_answer_only"
    assert saved_body["review_revision"] == original_revision + 1
    assert saved_body["review_history"][-1]["type"] == "draft_saved"

    stale = client.put(
        f"/api/v1/questions/{question.id}/review-draft",
        json=body | {"reviewer_name": "另一位教师"},
    )
    assert stale.status_code == 409

    promoted = client.post(
        f"/api/v1/questions/{question.id}/promote",
        json=body | {
            "expected_review_revision": saved_body["review_revision"],
            "review_note": "已逐项确认",
        },
    )
    assert promoted.status_code == 200, promoted.text
    promoted_body = promoted.json()
    assert promoted_body["grading_readiness"] == "machine_ready"
    assert promoted_body["review_history"][-1]["type"] == "promoted"
    assert promoted_body["reviewed_by"] == "测试教师"

    withdrawn = client.post(
        f"/api/v1/questions/{question.id}/withdraw",
        json={
            "expected_review_revision": promoted_body["review_revision"],
            "reviewer_name": "测试教师",
            "review_note": "发现答案需要再次核对",
        },
    )
    assert withdrawn.status_code == 200, withdrawn.text
    withdrawn_body = withdrawn.json()
    assert withdrawn_body["grading_readiness"] == "raw_answer_only"
    assert withdrawn_body["review_history"][-1]["type"] == "withdrawn"

    resaved = client.put(
        f"/api/v1/questions/{question.id}/review-draft",
        json=body | {"expected_review_revision": withdrawn_body["review_revision"]},
    )
    assert resaved.status_code == 200, resaved.text
    assert resaved.json()["review_history"][-1]["type"] == "draft_saved"


def test_choice_answer_must_be_single_option():
    with pytest.raises(QuestionReviewValidation):
        validate_review_items(
            "choice",
            [{"item_id": uuid4().hex, "answer": "AB", "answer_match_type": "choice"}],
        )


def test_true_false_review_accepts_only_canonical_answers():
    validate_review_items(
        "true_false",
        [
            {"item_id": "1", "answer": "正确", "answer_match_type": "exact"},
            {"item_id": "2", "answer": "错误", "answer_match_type": "exact"},
        ],
    )
    with pytest.raises(QuestionReviewValidation):
        validate_review_items(
            "true_false",
            [{"item_id": "1", "answer": "√", "answer_match_type": "exact"}],
        )


def test_seed_refresh_preserves_teacher_reviewed_answer():
    question = type("ReviewedQuestion", (), {"review_history": [{"type": "draft_saved"}]})()
    values = {
        "question_text": "可更新的来源题干",
        "correct_answer": "来源答案",
        "answer_match_type": "exact",
        "answer_normalization_rule": {},
        "grading_readiness": "raw_answer_only",
        "import_issues": ["answer_items_not_structured"],
    }
    _preserve_reviewed_answer(question, values)
    assert values == {"question_text": "可更新的来源题干"}
