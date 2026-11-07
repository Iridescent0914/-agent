from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import distinct, func, select

from app.core.database import SessionLocal
from app.main import app
from app.models import KnowledgePoint, Question, Student


CHAPTER_CODE = "math.g6.expr"
RATIONAL_CHAPTER_CODE = "math.g6.rational"

client = TestClient(app)


@pytest.fixture(scope="module")
def expr_seeded() -> dict[str, object]:
    """Return stable IDs for the chapter when the integration data is present.

    The normal unit-test database contains the first chapter only.  Skipping
    here keeps that suite valid while still exercising these checks whenever
    the two-chapter development/integration database is loaded.
    """
    with SessionLocal() as db:
        question_count = db.scalar(
            select(func.count(Question.id)).where(
                Question.chapter_code == CHAPTER_CODE,
                Question.archived_at.is_(None),
            )
        ) or 0
        student_id = db.scalar(
            select(Student.id).where(Student.student_code == "DEMO-S001")
        )
        new_code = db.scalar(
            select(KnowledgePoint.code)
            .where(
                KnowledgePoint.chapter_code == CHAPTER_CODE,
                KnowledgePoint.id.in_(
                    select(Question.main_knowledge_point_id).where(
                        Question.chapter_code == CHAPTER_CODE,
                        Question.grading_readiness == "machine_ready",
                        Question.archived_at.is_(None),
                    )
                ),
            )
            .order_by(KnowledgePoint.sort_order, KnowledgePoint.code)
        )

    if not question_count:
        pytest.skip("math.g6.expr seed data is not loaded in this database")
    if student_id is None or new_code is None:
        pytest.fail("expr integration data is incomplete: DEMO-S001 or a machine-ready knowledge point is missing")
    return {"student_id": student_id, "new_code": new_code}


def test_expr_seed_contract(expr_seeded: dict[str, object]) -> None:
    with SessionLocal() as db:
        questions = list(
            db.scalars(
                select(Question).where(
                    Question.chapter_code == CHAPTER_CODE,
                    Question.archived_at.is_(None),
                )
            )
        )
        knowledge_point_count = db.scalar(
            select(func.count(KnowledgePoint.id)).where(
                KnowledgePoint.chapter_code == CHAPTER_CODE
            )
        ) or 0
        used_knowledge_point_count = db.scalar(
            select(func.count(distinct(Question.main_knowledge_point_id))).where(
                Question.chapter_code == CHAPTER_CODE,
                Question.archived_at.is_(None),
            )
        ) or 0

    assert len(questions) == 41
    assert len({question.original_qid for question in questions}) == 41
    assert knowledge_point_count == 8
    assert used_knowledge_point_count == 6
    # q.math.g6.expr.school.028 has a tenth answer slot in the stem but the
    # source reference answer stops at subquestion 9, so it remains review-only.
    assert sum(question.grading_readiness == "machine_ready" for question in questions) == 8
    assert sum(bool(question.is_required) for question in questions) == 35
    assert all(question.source_review_status == "reviewed" for question in questions)


def test_expr_question_and_catalog_filters(expr_seeded: dict[str, object]) -> None:
    response = client.get(
        "/api/v1/questions",
        params={"chapter_code": CHAPTER_CODE, "page_size": 100},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 41
    assert len(body["items"]) == 41

    with SessionLocal() as db:
        expr_question_ids = {
            str(question_id)
            for question_id in db.scalars(
                select(Question.id).where(Question.chapter_code == CHAPTER_CODE)
            )
        }
        expr_knowledge_codes = set(
            db.scalars(
                select(KnowledgePoint.code).where(
                    KnowledgePoint.chapter_code == CHAPTER_CODE
                )
            )
        )
        used_knowledge_codes = set(
            db.scalars(
                select(KnowledgePoint.code)
                .join(Question, Question.main_knowledge_point_id == KnowledgePoint.id)
                .where(
                    Question.chapter_code == CHAPTER_CODE,
                    Question.archived_at.is_(None),
                )
                .distinct()
            )
        )

    # The list response intentionally exposes the main knowledge-point tag,
    # while the database assertion above verifies that every returned ID is
    # from the requested chapter.
    assert {item["main_knowledge_point"]["code"] for item in body["items"]} <= expr_knowledge_codes
    assert {str(item["id"]) for item in body["items"]} <= expr_question_ids

    catalog = client.get("/api/v1/questions/catalog")
    assert catalog.status_code == 200
    expr_chapters = [item for item in catalog.json() if item["code"] == CHAPTER_CODE]
    assert len(expr_chapters) == 1
    # The curriculum catalog remains complete even though only six points
    # currently have active questions.
    assert len(expr_chapters[0]["knowledge_points"]) == 8

    knowledge_points = client.get(
        "/api/v1/knowledge-points", params={"chapter_code": CHAPTER_CODE}
    )
    assert knowledge_points.status_code == 200
    knowledge_point_rows = knowledge_points.json()
    assert len(knowledge_point_rows) == 8
    assert all(item["chapter_code"] == CHAPTER_CODE for item in knowledge_point_rows)
    by_code = {item["code"]: item for item in knowledge_point_rows}
    unused_knowledge_codes = expr_knowledge_codes - used_knowledge_codes
    assert len(unused_knowledge_codes) == 2
    assert all(
        by_code[code]["available_question_count"] == 0
        for code in unused_knowledge_codes
    )


def test_expr_recommendation_is_chapter_scoped(
    expr_seeded: dict[str, object],
) -> None:
    student_id = UUID(str(expr_seeded["student_id"]))
    new_code = str(expr_seeded["new_code"])
    response = client.post(
        f"/api/v1/students/{student_id}/recommendations/preview",
        json={
            "chapter_code": CHAPTER_CODE,
            "new_knowledge_point_codes": [new_code],
            "new_count": 1,
            "review_count": 0,
            "max_knowledge_point_ratio": 1.0,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["parameters"]["chapter_code"] == CHAPTER_CODE

    with SessionLocal() as db:
        selected_ids = [item["question_id"] for item in body["items"]]
        selected_chapters = set(
            db.scalars(
                select(Question.chapter_code).where(Question.id.in_(selected_ids))
            )
        ) if selected_ids else set()
    assert selected_chapters <= {CHAPTER_CODE}


def test_expr_recommendation_rejects_cross_chapter_knowledge_point(
    expr_seeded: dict[str, object],
) -> None:
    student_id = UUID(str(expr_seeded["student_id"]))
    with SessionLocal() as db:
        rational_code = db.scalar(
            select(KnowledgePoint.code)
            .where(KnowledgePoint.chapter_code == RATIONAL_CHAPTER_CODE)
            .order_by(KnowledgePoint.sort_order, KnowledgePoint.code)
        )
    assert rational_code

    response = client.post(
        f"/api/v1/students/{student_id}/recommendations/preview",
        json={
            "chapter_code": CHAPTER_CODE,
            "new_knowledge_point_codes": [rational_code],
            "new_count": 1,
            "review_count": 0,
            "max_knowledge_point_ratio": 1.0,
        },
    )
    assert response.status_code == 422
