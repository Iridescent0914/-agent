from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from app.services.mastery import Evidence, confidence_level, evidence_problem, replay


def evidence(index: int, correct: bool | None = True, **changes) -> Evidence:
    item = Evidence(
        attempt_id=UUID(int=index), student_id=UUID(int=100), question_id=UUID(int=index + 200),
        paper_instance_item_id=UUID(int=index + 300), knowledge_point_id=UUID(int=400),
        knowledge_point_code="kp.a", answered_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        revision_no=1, grade_status="teacher_confirmed", is_correct=correct,
        question_type="fill_blank", difficulty=2,
    )
    return replace(item, **changes)


def result(items):
    return next(iter(replay(items).values()))


def test_hand_calculation_and_stable_uuid_order():
    items = [evidence(1), evidence(2, False), evidence(3)]
    actual = result(list(reversed(items)))
    assert actual == result(items)
    assert actual.mastery == Decimal("0.5840")
    assert [point.mastery_after for point in actual.history] == [Decimal("0.6000"), Decimal("0.4800"), Decimal("0.5840")]
    assert (actual.attempt_count, actual.correct_count, actual.recent_wrong_count) == (3, 2, 1)
    assert actual.confidence_level == "medium"
    assert actual.distinct_question_count == 3


@pytest.mark.parametrize("count,expected", [(0, "low"), (2, "low"), (3, "medium"), (7, "medium"), (8, "high")])
def test_confidence_boundaries(count, expected):
    assert confidence_level(count) == expected


def test_unknown_results_and_empty_history():
    assert replay([]) == {}
    pending = evidence(1, None, grade_status="needs_review")
    invalid = evidence(2, None)
    assert evidence_problem(pending) == "needs_review"
    assert evidence_problem(invalid) == "confirmed_without_result"
    assert replay([pending, invalid]) == {}


def test_revision_replay_and_withdrawal_do_not_add_attempts():
    items = [evidence(1), evidence(2, False), evidence(3)]
    changed = [items[0], replace(items[1], is_correct=True, revision_no=2), items[2]]
    actual = result(changed)
    assert actual.mastery == Decimal("0.7440")
    assert actual.attempt_count == 3
    assert actual.history[1].revision_no == 2
    assert actual.history[1].answered_at == items[1].answered_at
    changed[1] = replace(changed[1], grade_status="needs_review", is_correct=None)
    assert result(changed).mastery == Decimal("0.6800")
    assert result(changed).attempt_count == 2


def test_deduplicate_current_attempt_but_count_real_repractice():
    first = evidence(1)
    second = evidence(2, question_id=first.question_id)
    actual = result([first, first, second])
    assert actual.attempt_count == 2
    assert actual.distinct_question_count == 1
    with pytest.raises(ValueError, match="Conflicting"):
        replay([first, replace(first, revision_no=2)])


def test_recent_window_is_last_five_and_uses_answered_time():
    items = [evidence(i, i > 3, answered_at=datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(days=i))
             for i in range(1, 8)]
    actual = result(items)
    assert actual.recent_attempt_count == 5
    assert actual.recent_wrong_count == 1
    assert actual.last_practiced_at == items[-1].answered_at


def test_separate_students_and_main_knowledge_points():
    first = evidence(1)
    second = evidence(2, knowledge_point_id=UUID(int=401), knowledge_point_code="kp.b")
    third = evidence(3, student_id=UUID(int=101))
    assert len(replay([first, second, third])) == 3
    assert sum(value.attempt_count for value in replay([first, second, third]).values()) == 3
