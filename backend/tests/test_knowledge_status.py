from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

from app.services.knowledge_status import AttemptFact, _valid_history_source


BASE_TIME = datetime(2026, 11, 5, 8, 0, tzinfo=timezone.utc)
KNOWLEDGE_ID = UUID(int=100)


def _fact(
    value: int,
    *,
    minute: int,
    source_module: str,
    is_correct: bool,
    knowledge_point_id: UUID = KNOWLEDGE_ID,
    evidence: list[dict] | None = None,
) -> AttemptFact:
    attempt = SimpleNamespace(
        id=UUID(int=value),
        answered_at=BASE_TIME + timedelta(minutes=minute),
        grade_status="teacher_confirmed",
        is_correct=is_correct,
        revision_no=1,
    )
    item = SimpleNamespace(selection_audit={
        "source_module": source_module,
        "evidence": evidence or [],
    })
    assignment = SimpleNamespace(id=UUID(int=1000 + value), assignment_type="practice")
    return AttemptFact(attempt, item, assignment, knowledge_point_id)


def _history(source: AttemptFact) -> AttemptFact:
    return _fact(
        2,
        minute=20,
        source_module="history",
        is_correct=False,
        evidence=[{
            "attempt_id": str(source.attempt.id),
            "revision_no": source.attempt.revision_no,
        }],
    )


def test_history_status_requires_current_wrong_source_and_same_knowledge_point():
    source = _fact(1, minute=0, source_module="required", is_correct=False)
    history = _history(source)
    assert _valid_history_source(
        history,
        by_attempt_id={source.attempt.id: source, history.attempt.id: history},
        facts=[source, history],
    ) is source

    source.attempt.is_correct = True
    assert _valid_history_source(
        history,
        by_attempt_id={source.attempt.id: source, history.attempt.id: history},
        facts=[source, history],
    ) is None
    source.attempt.is_correct = False

    mismatched = AttemptFact(
        source.attempt,
        source.item,
        source.assignment,
        UUID(int=101),
    )
    assert _valid_history_source(
        history,
        by_attempt_id={mismatched.attempt.id: mismatched},
        facts=[mismatched, history],
    ) is None


def test_confirmed_correct_answer_between_two_errors_breaks_consecutive_chain():
    source = _fact(1, minute=0, source_module="required", is_correct=False)
    corrected_between = _fact(
        3, minute=10, source_module="required", is_correct=True,
    )
    history = _history(source)
    facts = [source, corrected_between, history]
    assert _valid_history_source(
        history,
        by_attempt_id={fact.attempt.id: fact for fact in facts},
        facts=facts,
    ) is None


def test_missing_or_invalidated_source_cannot_create_history_status():
    source = _fact(1, minute=0, source_module="required", is_correct=False)
    history = _history(source)
    assert _valid_history_source(
        history,
        by_attempt_id={history.attempt.id: history},
        facts=[history],
    ) is None
