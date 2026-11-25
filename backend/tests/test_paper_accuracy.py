"""Comparison denominators, baseline isolation and GET side-effect protection."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event

from app.core.database import get_db
from app.main import app
from app.models import AttemptRevision, Student
from app.services.paper_accuracy import build_accuracy_comparison
from tests.test_profile_historical_accuracy import catalog, persisted_attempt, profile_context, row
from tests.test_stage6_s1 import context, protected_counts


def compare(points, current, history=(), extra_items=()):
    paper = current[0][2]
    # Several rows represent separate question groups on the same paper.
    for record in current:
        record[1].paper_instance_id = paper.id
        record[2].id = paper.id
        record[3].paper_instance_id = paper.id
    ctx = profile_context(points, [*current, *history])
    return build_accuracy_comparison(ctx, paper, [r[1] for r in current] + list(extra_items))


def test_current_paper_is_excluded_from_history_and_groups_are_not_blanks():
    points = catalog()
    current = [row(points[0], False), row(points[0], None)]
    current[0][1].answer_snapshot = {"kind": "group", "items": [{"item_id": str(n)} for n in range(12)]}
    history = [row(points[0], True) for _ in range(9)] + [row(points[1], False)]
    result = compare(points, current, history)
    assert result.paper.accuracy == 0 and result.paper.confirmed_attempt_count == 1
    assert result.paper.pending_review_count == 1 and result.paper.judged_coverage == .5
    assert result.history.confirmed_attempt_count == 10 and result.history.accuracy == .9
    assert result.knowledge_coverage.points[0].historical_confirmed_attempt_count == 9
    assert result.data_status == "incomplete_paper"


def test_history_coverage_ignores_pending_invalid_and_other_knowledge_points():
    points = catalog()
    invalid = row(points[1], True); invalid[2].invalidated_at = datetime.now(timezone.utc)
    result = compare(points, [row(points[0]), row(points[1])],
                     [row(points[0]), row(points[1], None), invalid])
    assert result.knowledge_coverage.seen_question_count == 1
    assert result.knowledge_coverage.unseen_question_count == 1
    assert result.history.pending_review_count == 1 and result.history.excluded_attempt_count == 1
    assert [p.history_status for p in result.knowledge_coverage.points] == ["seen", "unseen"]


def test_unsubmitted_and_unknown_knowledge_point_are_separate_from_wrong():
    points = catalog()
    missing = SimpleNamespace(id=uuid4(), knowledge_point_snapshot={})
    result = compare(points, [row(points[0], True)], [row(points[0], True)], [missing])
    assert result.paper.total_question_count == 2 and result.paper.unsubmitted_question_count == 1
    assert result.paper.accuracy == 1 and result.paper.incorrect_count == 0
    assert result.paper.judged_coverage == .5
    assert result.knowledge_coverage.unmapped_question_count == 1
    assert result.knowledge_coverage.unseen_question_count == 0


@pytest.mark.parametrize("values,expected_status,expected_accuracy", [
    ([], "no_history", None), ([None], "no_history", None),
    ([False], "available", 0), ([True], "available", 1),
])
def test_no_history_is_unknown_but_one_confirmed_sample_is_not_declared_sufficient(values, expected_status, expected_accuracy):
    points = catalog()
    result = compare(points, [row(points[0])], [row(points[0], value) for value in values])
    assert result.data_status == expected_status and result.history.accuracy == expected_accuracy
    assert result.read_only is True
    assert set(result.model_dump()) == {
        "paper_instance_id", "computed_at", "counting_unit", "history_scope", "read_only",
        "data_status", "paper", "history", "knowledge_coverage",
    }  # No threshold, alarm, acceptance or grading decision.


def test_reference_types_repeated_questions_and_current_revision_share_profile_denominator():
    points = catalog(); question = uuid4()
    history = [row(points[0], True, question_type="calculation", question_id=question),
               row(points[0], False, question_type="solution", question_id=question)]
    history[0][0].revision_no = 4
    result = compare(points, [row(points[0])], history)
    assert result.history.confirmed_attempt_count == 2 and result.history.distinct_question_count == 1
    assert result.history.accuracy == .5
    assert result.knowledge_coverage.points[0].historical_confirmed_attempt_count == 2


@pytest.mark.parametrize("invalid", ["invalidated", "draft", "missing_revision", "owner"])
def test_invalid_current_results_are_never_reintroduced_into_the_baseline(invalid):
    points = catalog(); current = list(row(points[0]))
    if invalid == "invalidated": current[2].invalidated_at = datetime.now(timezone.utc)
    elif invalid == "draft": current[2].status = "draft"
    elif invalid == "missing_revision": current[4] = None
    else: current[3].student_id = uuid4()
    result = compare(points, [tuple(current)], [row(points[1], False)])
    assert result.paper.accuracy is None and result.paper.excluded_attempt_count == 1
    assert result.history.confirmed_attempt_count == 1 and result.history.accuracy == 0
    assert result.data_status == ("inactive_paper" if invalid in {"invalidated", "draft"} else "no_paper_results")


def test_api_refreshes_after_revision_reopen_invalidation_and_never_writes(context, monkeypatch):
    db, _, fixture_paper, _, question = context
    student = db.get(Student, fixture_paper.student_id); points = catalog()
    import app.services.student_profile as service
    monkeypatch.setattr(service, "knowledge_point_catalog", lambda *_args, **_kwargs: points)
    current, paper = persisted_attempt(db, student, question, points[0], True)
    historical, history_paper = persisted_attempt(db, student, question, points[0], False)
    other = Student(student_code="OTHER-" + uuid4().hex[:12], display_name="other", class_name="test")
    db.add(other); db.flush()
    persisted_attempt(db, other, question, points[0], True)  # Other student must not affect baseline.
    client = TestClient(app)  # No lifespan / background workers.
    app.dependency_overrides[get_db] = lambda: db
    statements = []
    def observe(_conn, _cursor, statement, _params, _ctx, _many):
        statements.append(statement.strip().split()[0].upper())
    def read():
        before = protected_counts(db)
        event.listen(db.bind, "before_cursor_execute", observe)
        try:
            response = client.get(f"/api/v1/paper-instances/{paper.id}/accuracy-comparison")
            assert response.status_code == 200, response.text
        finally:
            event.remove(db.bind, "before_cursor_execute", observe)
        assert set(statements) <= {"SELECT"}
        assert not db.new and not db.dirty and protected_counts(db) == before
        return response.json()
    try:
        first = read()
        assert first["history"]["confirmed_attempt_count"] == 1 and first["history"]["accuracy"] == 0
        assert first["paper"]["accuracy"] == 1 and first["paper"]["unsubmitted_question_count"] == 0
        current.is_correct = False; current.revision_no = 2
        db.add(AttemptRevision(attempt_id=current.id, revision_no=2, after_state={"is_correct": False}, reason="correction", operator_type="teacher")); db.flush()
        assert read()["paper"]["accuracy"] == 0
        historical.grade_status = "needs_review"; historical.is_correct = None; historical.revision_no = 2
        db.add(AttemptRevision(attempt_id=historical.id, revision_no=2, after_state={"is_correct": None}, reason="reopen", operator_type="teacher")); db.flush()
        reopened = read()
        assert reopened["history"]["accuracy"] is None and reopened["history"]["pending_review_count"] == 1
        assert reopened["knowledge_coverage"]["seen_question_count"] == 0
        history_paper.invalidated_at = datetime.now(timezone.utc); db.flush()
        assert read()["history"]["excluded_attempt_count"] == 1
        question.archived_at = datetime.now(timezone.utc); db.flush()
        assert read()["paper"]["accuracy"] == 0  # Catalog edits do not rewrite frozen evidence.
        assert client.get(f"/api/v1/paper-instances/{uuid4()}/accuracy-comparison").status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)
