"""Cumulative accuracy is a ratio of current grades, independent of KP weights."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.api.routes.profile import student_context
from app.core.database import get_db
from app.main import app
from app.models import Assignment, Attempt, AttemptRevision, PaperInstance, PaperInstanceItem, Submission
from app.schemas.profile import KnowledgePointOut
from app.services.student_profile import _context_from_rows, build_profile, historical_accuracy, load_context
from tests.test_stage6_s1 import context, protected_counts


STUDENT = uuid4()
START = datetime(2026, 11, 1, tzinfo=timezone.utc)


def catalog():
    return [KnowledgePointOut(id=uuid4(), code=f"accuracy.chapter{n}.kp", name=f"知识点{n}",
        chapter_code=f"accuracy.chapter{n}", chapter_name=f"章节{n}", grade=f"{n + 5}年级",
        core_competencies=[], available_question_count=0) for n in (1, 2)]


def row(point, correct=True, *, question_type="fill_blank", question_id=None, status=None, day=0):
    paper_id, question_id = uuid4(), question_id or uuid4()
    item = SimpleNamespace(id=uuid4(), paper_instance_id=paper_id, question_id=question_id,
        question_snapshot={"original_qid": "accuracy-fixture", "question_text": "fixture",
                           "question_type": question_type, "difficulty": 2},
        knowledge_point_snapshot={"id": point.code})
    attempt = SimpleNamespace(id=uuid4(), student_id=STUDENT, question_id=question_id,
        paper_instance_item_id=item.id, answered_at=START + timedelta(days=day), revision_no=1,
        grade_status=status or ("needs_review" if correct is None else "auto_confirmed"), is_correct=correct)
    paper = SimpleNamespace(id=paper_id, student_id=STUDENT, status="published", invalidated_at=None)
    submission = SimpleNamespace(student_id=STUDENT, paper_instance_id=paper_id)
    revision = SimpleNamespace(revision_no=1)
    return attempt, item, paper, submission, revision


def profile_context(points, rows):
    return _context_from_rows(SimpleNamespace(id=STUDENT), points, rows)


def test_global_ratio_uses_all_answers_instead_of_average_of_knowledge_point_rates():
    points = catalog()
    rows = [row(points[0], True, day=n) for n in range(9)] + [row(points[0], False), row(points[1], False)]
    result = build_profile(profile_context(points, rows))
    assert result.historical_accuracy.accuracy == pytest.approx(9 / 11)
    assert result.historical_accuracy.confirmed_attempt_count == 11
    assert result.historical_accuracy.correct_count == 9
    assert result.historical_accuracy.incorrect_count == 2
    assert result.historical_accuracy.scope == "all_history"
    assert result.historical_accuracy.counting_unit == "attempt_question_group"


@pytest.mark.parametrize("values,expected", [([], None), ([None], None), ([False], 0.0), ([True], 1.0), ([True, False], .5)])
def test_unknown_history_is_not_zero_and_confirmed_wrong_is_zero(values, expected):
    points = catalog()
    result = historical_accuracy(profile_context(points, [row(points[0], value) for value in values]))
    assert result.accuracy == expected
    assert result.confirmed_attempt_count == sum(v is not None for v in values)
    assert result.pending_review_count == values.count(None)
    assert result.correct_count + result.incorrect_count == result.confirmed_attempt_count


def test_repeated_question_counts_each_practice_once_but_has_one_distinct_question():
    points = catalog(); question_id = uuid4()
    rows = [row(points[0], True, question_id=question_id), row(points[0], False, question_id=question_id, day=1)]
    result = historical_accuracy(profile_context(points, rows))
    assert result.confirmed_attempt_count == 2 and result.distinct_question_count == 1
    assert result.accuracy == .5


def test_manual_reference_types_count_towards_all_history_without_changing_mastery():
    points = catalog()
    rows = [row(points[0], True), row(points[0], True, question_type="calculation", status="teacher_confirmed"),
            row(points[1], False, question_type="solution", status="teacher_confirmed")]
    result = build_profile(profile_context(points, rows))
    assert result.historical_accuracy.confirmed_attempt_count == 3
    assert result.historical_accuracy.accuracy == pytest.approx(2 / 3)
    assert result.summary.valid_attempt_count == 1
    assert sum(m.attempt_count for m in result.masteries) == 1


@pytest.mark.parametrize("problem", ["withdrawn", "superseded", "draft", "missing_revision", "mismatched_owner", "bad_snapshot", "missing_kp", "bad_verdict"])
def test_invalid_evidence_and_inactive_papers_are_excluded(problem):
    points = catalog(); current = list(row(points[0], True))
    if problem in {"withdrawn", "superseded"}:
        current[2].invalidated_at = START
    elif problem == "draft": current[2].status = "draft"
    elif problem == "missing_revision": current[4] = None
    elif problem == "mismatched_owner": current[3].student_id = uuid4()
    elif problem == "bad_snapshot": current[1].question_snapshot = {}
    elif problem == "missing_kp": current[1].knowledge_point_snapshot = {}
    else: current[0].is_correct = 1
    result = historical_accuracy(profile_context(points, [tuple(current)]))
    assert result.confirmed_attempt_count == 0 and result.accuracy is None
    assert result.excluded_attempt_count == 1 and result.pending_review_count == 0


def persisted_attempt(db, student, question, point, correct, *, question_type="fill_blank", pending=False):
    assignment = Assignment(title="累计正确率fixture", chapter_code=point.chapter_code)
    db.add(assignment); db.flush()
    paper = PaperInstance(assignment_id=assignment.id, student_id=student.id,
                          version_no=1, status="draft", instance_code="ACC-" + uuid4().hex)
    db.add(paper); db.flush()
    item = PaperInstanceItem(paper_instance_id=paper.id, question_id=question.id, order_no=1,
        question_snapshot={"original_qid": question.original_qid, "question_text": "fixture",
                           "question_type": question_type, "difficulty": 2},
        answer_snapshot={"kind": "group", "schema_version": "answer-units-v1", "items": [
            {"item_id": f"sub_1.blank_{n}", "subquestion_no": 1, "blank_no": n,
             "answer": n, "answer_match_type": "numeric"} for n in (1, 2, 3)]},
        answer_rule_snapshot={"answer_match_type": "group", "answer_normalization_rule": {}},
        knowledge_point_snapshot={"id": point.code, "name": point.name}, selection_audit={"source_module": "required"})
    db.add(item); db.flush()
    paper.status, paper.published_at = "published", START
    submission = Submission(student_id=student.id, paper_instance_id=paper.id, source="manual",
        idempotency_key=uuid4().hex, submitted_at=START)
    db.add(submission); db.flush()
    attempt = Attempt(submission_id=submission.id, student_id=student.id, paper_instance_item_id=item.id,
        question_id=question.id, grade_status="needs_review" if pending else "teacher_confirmed",
        is_correct=None if pending else correct, answered_at=START, revision_no=1,
        student_answer=None, normalized_answer=None, grading_details={})
    db.add(attempt); db.flush()
    db.add(AttemptRevision(attempt_id=attempt.id, revision_no=1, after_state={"is_correct": attempt.is_correct},
                           reason="fixture", operator_type="system"))
    db.flush()
    return attempt, paper


def test_api_reads_current_revision_only_and_updates_after_correction_reopen_and_withdrawal(context, monkeypatch):
    db, _, paper_fixture, _, question = context
    from app.models import Student
    student = db.get(Student, paper_fixture.student_id)
    points = catalog()
    import app.services.student_profile as service
    monkeypatch.setattr(service, "knowledge_point_catalog", lambda *_args, **_kwargs: points)
    first, paper = persisted_attempt(db, student, question, points[0], True)
    second, _ = persisted_attempt(db, student, question, points[1], False)
    third, _ = persisted_attempt(db, student, question, points[1], False, pending=True)
    fourth, _ = persisted_attempt(db, student, question, points[0], True, question_type="calculation")
    before = protected_counts(db)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[student_context] = lambda: load_context(db, student.id, include_literacy=False)
    try:
        client = TestClient(app)  # no lifespan / model workers
        path = f"/api/v1/students/{student.id}/profile"
        result = client.get(path)
        assert result.status_code == 200, result.text
        stats = result.json()["historical_accuracy"]
        assert stats["confirmed_attempt_count"] == 3 and stats["correct_count"] == 2
        assert stats["distinct_question_count"] == 1 and stats["pending_review_count"] == 1
        assert result.json()["summary"]["valid_attempt_count"] == 2
        assert protected_counts(db) == before  # GET creates no cached records or grades.
        # Multiple blanks and revisions still contribute just one current grade.
        first.is_correct, first.revision_no = False, 2
        db.add(AttemptRevision(attempt_id=first.id, revision_no=2, before_state={"is_correct": True},
            after_state={"is_correct": False}, reason="fixture correction", operator_type="teacher")); db.flush()
        updated = client.get(path).json()["historical_accuracy"]
        assert updated["confirmed_attempt_count"] == 3 and updated["correct_count"] == 1
        second.grade_status, second.is_correct, second.revision_no = "needs_review", None, 2
        db.add(AttemptRevision(attempt_id=second.id, revision_no=2, before_state={"is_correct": False},
            after_state={"is_correct": None}, reason="fixture reopen", operator_type="teacher")); db.flush()
        reopened = client.get(path).json()["historical_accuracy"]
        assert reopened["confirmed_attempt_count"] == 2 and reopened["pending_review_count"] == 2
        assert reopened["accuracy"] == .5
        paper.invalidated_at = START; db.flush()
        excluded = client.get(path).json()["historical_accuracy"]
        assert excluded["confirmed_attempt_count"] == 1 and excluded["accuracy"] == 1
        assert excluded["excluded_attempt_count"] == 1
        # Archiving/editing the current question does not rewrite published history.
        question.archived_at = START
        question.difficulty = 5
        db.flush()
        assert client.get(path).json()["historical_accuracy"] == excluded
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(student_context, None)
