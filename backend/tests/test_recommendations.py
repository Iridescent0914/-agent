from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.schemas.recommendations import RecommendationRequest
from app.services.recommendations import Candidate, score_candidate, select_recommendations
from app.services.mastery import replay
from tests.test_mastery import evidence


NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)
STUDENT = UUID(int=100)


def candidates():
    return [Candidate(UUID(int=i), f"q.{i:03}", "choice", 2, UUID(int=400 + (i > 3)),
                       "exam.a" if i <= 3 else "exam.b", "考点 A" if i <= 3 else "考点 B")
            for i in range(1, 7)]


def params(**changes):
    return RecommendationRequest(**({"review_exam_point_codes": ["exam.a", "exam.b"],
                                     "review_count": 4} | changes))


def select(items=None, settings=None, practiced=None):
    return select_recommendations(student_id=STUDENT, candidates=items or candidates(), results={},
                                  practiced=practiced or [], params=settings or params(), as_of=NOW)


def test_constraints_determinism_and_diagnostic_difficulty():
    output = select()
    assert output == select(list(reversed(candidates())))
    assert output.selected_count == 4
    assert output.selected_per_exam_point == {"exam.a": 2, "exam.b": 2}
    assert len({item.question_id for item in output.items}) == 4
    for item in output.items:
        assert item.strategy == "diagnostic" and item.target_difficulty == 2
        assert item.components.weakness == item.components.recent_wrong == item.components.forgetting == 0
        assert item.score == pytest.approx(sum(item.contributions.model_dump().values()))
        assert item.evidence == []


def test_recent_window_includes_boundary_and_pending_contact():
    practiced = [(item.question_id, NOW - timedelta(days=7)) for item in candidates()]
    output = select(practiced=practiced)
    assert output.selected_count == 0 and output.shortfall == 4
    assert "recently_practiced" in output.diagnostics[1].reason_codes
    assert select(practiced=practiced, settings=params(avoid_recent_days=0)).selected_count == 4
    outside = [(item.question_id, NOW - timedelta(days=7, seconds=1)) for item in candidates()]
    assert select(practiced=outside).selected_count == 4


def test_no_cross_bucket_fill_and_cap_shortage():
    output = select(settings=params(new_exam_point_codes=["exam.empty"], new_count=2,
                                    review_exam_point_codes=["exam.a"], review_count=4))
    assert output.new_shortfall == 2 and output.review_shortfall == 1
    assert output.selected_count == 3
    assert "no_questions_in_scope" in output.diagnostics[0].reason_codes
    cap = select(settings=params(review_exam_point_codes=["exam.a"], review_count=4))
    assert cap.selected_count == 2
    assert "exam_point_cap" in cap.diagnostics[1].reason_codes


def test_readiness_type_difficulty_filters_do_not_relax():
    pool = candidates()
    pool[0] = replace(pool[0], ready=False)
    pool[1] = replace(pool[1], question_type="fill_blank")
    pool[2] = replace(pool[2], difficulty=5)
    output = select(pool, params(question_types=["choice"], max_difficulty=3))
    assert all(item.question_id.int > 3 for item in output.items)
    assert output.shortfall == 2
    assert sum(stage.removed for stage in output.diagnostics[1].stages) == 3


def test_true_false_can_be_selected_when_requested():
    pool = [replace(candidates()[0], question_type="true_false")]
    output = select(
        pool,
        params(
            review_exam_point_codes=["exam.a"],
            review_count=1,
            question_types=["true_false"],
            max_exam_point_ratio=1,
        ),
    )
    assert output.selected_count == 1
    assert output.items[0].question_type == "true_false"


def test_personalized_components_and_review_time_do_not_change_mastery():
    items = [evidence(1, False), evidence(2, False), evidence(3)]
    result = next(iter(replay(items).values()))
    original = result.mastery
    scored = score_candidate(candidates()[0], result, params(), "review", NOW)
    assert scored.strategy == "personalized"
    assert scored.target_difficulty == 3
    assert scored.components.recent_wrong == pytest.approx(2 / 3)
    assert scored.components.forgetting == pytest.approx(22 / 30)
    assert len(scored.evidence) == 3
    future = score_candidate(candidates()[0], result, params(), "review", NOW + timedelta(days=60))
    assert future.components.forgetting == 1
    assert result.mastery == original
    new = score_candidate(candidates()[0], result, params(), "new", NOW)
    assert new.components.forgetting == 0


@pytest.mark.parametrize("changes", [
    {"review_count": 0}, {"review_count": 31}, {"review_count": 1, "max_exam_point_ratio": 0.5},
    {"review_exam_point_codes": []}, {"new_exam_point_codes": ["exam.a"], "new_count": 1},
    {"question_types": []}, {"base_difficulty": 5, "max_difficulty": 3},
    {"avoid_recent_days": -1}, {"review_count": True}, {"chapter_code": ""},
])
def test_parameter_validation(changes):
    with pytest.raises(ValidationError):
        params(**changes)
