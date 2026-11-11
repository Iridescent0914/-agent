from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.core.database import Base, engine, get_db
from app.main import app
from app.models import (
    Assignment, Attempt, AttemptRevision, KnowledgePoint,
    PaperInstance, PaperInstanceItem, Question, Student,
    StudentLiteracyScore, StudentMastery, Submission,
)
from app.schemas.submissions import ManualSubmissionRequest
from app.services import submission_workflow
from app.services.knowledge_catalog import formal_kp
from app.services.submission_workflow import create_manual_submission


@pytest.fixture
def isolated_session_factory():
    schema = "m4_tx_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated_engine = create_engine(
        engine.url,
        connect_args={"options": f"-csearch_path={schema}"},
        poolclass=NullPool,
    )
    Base.metadata.create_all(isolated_engine)
    factory = sessionmaker(
        bind=isolated_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    try:
        yield factory
    finally:
        isolated_engine.dispose()
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def _create_published_single_item_paper(factory):
    suffix = uuid4().hex
    now = datetime.now(timezone.utc)
    knowledge_point_id = uuid4()
    question_id = uuid4()
    student_id = uuid4()
    assignment_id = uuid4()
    paper_id = uuid4()
    item_id = uuid4()
    knowledge_code = "math.g6.rational.kp.07_add_subtract"
    kp_metadata = formal_kp(knowledge_code)

    with factory() as db:
        db.add_all([
            KnowledgePoint(
                id=knowledge_point_id,
                code=knowledge_code,
                name=kp_metadata["name"],
                chapter_code=kp_metadata["chapter_code"],
                chapter_name=kp_metadata["chapter_name"],
                section=kp_metadata["section"],
                sort_order=kp_metadata["order"],
                core_competencies=kp_metadata["core_competencies"],
            ),
            Student(
                id=student_id,
                student_code="TX-" + suffix[:16],
                display_name="M4 事务测试学生",
                class_name="测试班",
                is_demo=True,
            ),
            Assignment(
                id=assignment_id,
                idempotency_key="tx-assignment-" + suffix,
                title="M4 并发与回滚测试",
                chapter_code=kp_metadata["chapter_code"],
                teaching_scope={},
                generation_config={},
                assignment_type="practice",
            ),
        ])
        db.flush()
        db.add(Question(
            id=question_id,
            original_qid="tx-question-" + suffix,
            schema_version="m4-test-v1",
            chapter_code=kp_metadata["chapter_code"],
            chapter_name=kp_metadata["chapter_name"],
            grade=kp_metadata["grade"],
            section=kp_metadata["section"],
            question_type="choice",
            difficulty=1,
            main_knowledge_point_id=knowledge_point_id,
            question_text="请选择 A",
            options={"A": "正确", "B": "错误"},
            correct_answer="A",
            reference_answer_raw="A",
            answer_match_type="choice",
            answer_normalization_rule={"trim_whitespace": True, "uppercase": True},
            solution=None,
            wrong_analysis=None,
            estimated_score_rate=None,
            core_literacy="运算能力",
            has_image=False,
            has_equation=False,
            assets=[],
            source_file="m4_transaction_test",
            source_question_number=1,
            source_review_status="reviewed",
            grading_readiness="machine_ready",
            import_status="parsed",
            import_issues=[],
            review_revision=1,
            review_history=[],
            reviewed_by="test",
            reviewed_at=now,
        ))
        db.flush()
        db.add(PaperInstance(
            id=paper_id,
            assignment_id=assignment_id,
            student_id=student_id,
            version_no=1,
            status="published",
            instance_code="TX-" + suffix[:12].upper(),
            recommendation_audit={},
            edit_history=[],
            edit_revision=1,
            published_at=now,
        ))
        db.flush()
        db.add(PaperInstanceItem(
            id=item_id,
            paper_instance_id=paper_id,
            question_id=question_id,
            order_no=1,
            question_snapshot={
                "schema_version": "m4-test-v1",
                "original_qid": "tx-question-" + suffix,
                "chapter_code": "test.m4",
                "chapter_name": "M4 测试",
                "section": "事务",
                "question_text": "请选择 A",
                "question_type": "choice",
                "difficulty": 1,
                "core_literacy": "运算能力",
                "assets": [],
                "solution": None,
                "wrong_analysis": None,
            },
            options_snapshot={"A": "正确", "B": "错误"},
            answer_snapshot="A",
            answer_rule_snapshot={
                "answer_match_type": "choice",
                "answer_normalization_rule": {
                    "trim_whitespace": True,
                    "uppercase": True,
                },
            },
            knowledge_point_snapshot={
                "id": knowledge_code,
                "name": "M4 事务测试知识点",
            },
            selection_audit={"source": "transaction_test"},
        ))
        db.commit()

    return {
        "student_id": student_id,
        "paper_id": paper_id,
        "item_id": item_id,
    }


def _submission_request(ids, *, idempotency_key: str) -> ManualSubmissionRequest:
    return ManualSubmissionRequest.model_validate({
        "idempotency_key": idempotency_key,
        "student_id": str(ids["student_id"]),
        "answers": [{
            "paper_instance_item_id": str(ids["item_id"]),
            "order_no": 1,
            "response_status": "answered",
            "answer": "A",
        }],
    })


def test_concurrent_identical_submission_creates_one_evidence_set(
    isolated_session_factory,
):
    ids = _create_published_single_item_paper(isolated_session_factory)
    request = _submission_request(
        ids,
        idempotency_key="tx-concurrent-" + uuid4().hex,
    )
    barrier = Barrier(2)

    def submit():
        barrier.wait(timeout=10)
        with isolated_session_factory() as db:
            try:
                result = create_manual_submission(db, ids["paper_id"], request)
                db.commit()
                return result.replayed
            except Exception:
                db.rollback()
                raise

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: submit(), range(2)))

    assert sorted(results) == [False, True]
    with isolated_session_factory() as db:
        submission_id = db.scalar(
            select(Submission.id).where(
                Submission.paper_instance_id == ids["paper_id"],
            )
        )
        assert submission_id is not None
        assert db.scalar(
            select(func.count(Attempt.id)).where(
                Attempt.submission_id == submission_id,
            )
        ) == 1
        attempt_id = db.scalar(
            select(Attempt.id).where(Attempt.submission_id == submission_id)
        )
        assert db.scalar(
            select(func.count(AttemptRevision.id)).where(
                AttemptRevision.attempt_id == attempt_id,
            )
        ) == 1
        assert db.scalar(
            select(func.count(StudentMastery.student_id)).where(
                StudentMastery.student_id == ids["student_id"],
            )
        ) == 1
        assert db.scalar(
            select(func.count(StudentLiteracyScore.student_id)).where(
                StudentLiteracyScore.student_id == ids["student_id"],
            )
        ) == 9


def test_failure_after_attempt_flush_rolls_back_submission_revision_and_mastery(
    isolated_session_factory, monkeypatch,
):
    ids = _create_published_single_item_paper(isolated_session_factory)
    request = _submission_request(
        ids,
        idempotency_key="tx-rollback-" + uuid4().hex,
    )

    def fail_mastery(*_args, **_kwargs):
        raise RuntimeError("injected mastery failure")

    def isolated_get_db():
        db = isolated_session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    monkeypatch.setattr(submission_workflow, "refresh_student_profiles", fail_mastery)
    app.dependency_overrides[get_db] = isolated_get_db
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/paper-instances/{ids['paper_id']}/submissions/manual",
                json=request.model_dump(mode="json"),
            )
        assert response.status_code == 422
        assert "injected mastery failure" in response.json()["message"]
    finally:
        app.dependency_overrides.pop(get_db, None)

    with isolated_session_factory() as db:
        assert db.scalar(
            select(func.count(Submission.id)).where(
                Submission.paper_instance_id == ids["paper_id"],
            )
        ) == 0
        assert db.scalar(
            select(func.count(Attempt.id)).where(
                Attempt.paper_instance_item_id == ids["item_id"],
            )
        ) == 0
        assert db.scalar(
            select(func.count(StudentMastery.student_id)).where(
                StudentMastery.student_id == ids["student_id"],
            )
        ) == 0
        assert db.scalar(
            select(func.count(StudentLiteracyScore.student_id)).where(
                StudentLiteracyScore.student_id == ids["student_id"],
            )
        ) == 0
