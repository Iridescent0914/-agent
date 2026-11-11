from collections import Counter
from uuid import UUID, uuid4

import pytest

from app.services.personalized_selection import select_personalized_questions
from app.services.recommendations import Candidate


STUDENT_ID = uuid4()
KNOWLEDGE_POINT_ID = uuid4()
KNOWLEDGE_POINT_CODE = "math.g6.personalized.kp"


def candidate(
    number: int,
    difficulty: int,
    *,
    ready: bool = True,
    knowledge_point_code: str = KNOWLEDGE_POINT_CODE,
) -> Candidate:
    return Candidate(
        question_id=UUID(int=number),
        original_qid=f"PERSONALIZED-{number:03d}",
        question_type="fill_blank",
        difficulty=difficulty,
        knowledge_point_id=KNOWLEDGE_POINT_ID,
        knowledge_point_code=knowledge_point_code,
        knowledge_point_name="个性化测试知识点",
        ready=ready,
    )


def select(score: int, candidates: list[Candidate], **kwargs):
    return select_personalized_questions(
        student_id=STUDENT_ID,
        literacy_name="运算能力",
        literacy_score=score,
        knowledge_point_codes=[KNOWLEDGE_POINT_CODE],
        candidates=candidates,
        **kwargs,
    )


@pytest.mark.parametrize("name", ["空间观念", "数据观念", "创新意识"])
def test_selects_three_exact_slots_for_formal_dimensions(name):
    result = select_personalized_questions(
        student_id=STUDENT_ID, literacy_name=name, literacy_score=60,
        knowledge_point_codes=[KNOWLEDGE_POINT_CODE],
        candidates=[candidate(star, star) for star in range(1, 6)],
    )
    assert result.target_stars == (2, 3, 4)
    assert result.selected_count == 3 and result.shortfall == 0


def test_selects_exact_three_star_sequence_from_business_integer():
    result = select(64, [candidate(star, star) for star in range(1, 6)])

    assert result.literacy_score == 64
    assert result.base_star == 2
    assert result.target_stars == (2, 3, 4)
    assert [slot.target_star for slot in result.slots] == [2, 3, 4]
    assert [slot.candidate.difficulty for slot in result.slots] == [2, 3, 4]
    assert result.selected_count == 3
    assert result.shortfall == 0
    assert result.status == "ready"


def test_five_star_cap_selects_three_distinct_questions_deterministically():
    pool = [candidate(30, 5), candidate(10, 5), candidate(20, 5)]

    forward = select(95, pool)
    reversed_result = select(95, list(reversed(pool)))

    assert forward.target_stars == (5, 5, 5)
    assert forward.selected_question_ids == (
        UUID(int=10), UUID(int=20), UUID(int=30),
    )
    assert reversed_result.selected_question_ids == forward.selected_question_ids
    assert len(set(forward.selected_question_ids)) == 3


def test_prefers_least_used_candidates_before_reusing_across_students():
    pool = [
        candidate(20, 2), candidate(21, 2),
        candidate(30, 3), candidate(31, 3),
        candidate(40, 4), candidate(41, 4),
    ]
    first = select(64, pool)
    usage = Counter(first.selected_question_ids)
    second = select(64, list(reversed(pool)), candidate_usage_counts=usage)

    assert first.selected_question_ids == (
        UUID(int=20), UUID(int=30), UUID(int=40),
    )
    assert second.selected_question_ids == (
        UUID(int=21), UUID(int=31), UUID(int=41),
    )
    assert set(first.selected_question_ids).isdisjoint(second.selected_question_ids)


def test_four_star_base_uses_one_four_star_and_two_distinct_five_star_questions():
    result = select(
        85,
        [candidate(40, 4), candidate(51, 5), candidate(52, 5), candidate(3, 3)],
    )

    assert result.base_star == 4
    assert result.target_stars == (4, 5, 5)
    assert [slot.candidate.difficulty for slot in result.slots] == [4, 5, 5]
    assert len(set(result.selected_question_ids)) == 3


def test_scope_readiness_and_exclusions_never_fall_back():
    excluded = candidate(3, 3)
    result = select(
        64,
        [
            candidate(2, 2, ready=False),
            excluded,
            candidate(4, 4),
            candidate(5, 5),
            candidate(24, 2, knowledge_point_code="outside.scope"),
        ],
        excluded_question_ids={excluded.question_id},
    )

    assert [
        slot.candidate.question_id if slot.candidate else None
        for slot in result.slots
    ] == [None, None, UUID(int=4)]
    assert result.target_stars == (2, 3, 4)
    assert result.shortfall == 2
    assert result.status == "insufficient_candidates"
    by_star = {item.difficulty: item for item in result.diagnostics}
    assert "insufficient_machine_ready_inventory" in by_star[2].reason_codes
    assert "excluded_question_conflict" in by_star[3].reason_codes
    assert by_star[4].reason_codes == ()


def test_repeated_five_star_shortfall_is_reported_without_reuse():
    result = select(90, [candidate(1, 5), candidate(2, 5)])

    assert result.target_stars == (5, 5, 5)
    assert result.selected_count == 2
    assert result.shortfall == 1
    assert result.slots[-1].candidate is None
    diagnostic = result.diagnostics[0]
    assert diagnostic.requested_count == 3
    assert diagnostic.available_candidate_count == 2
    assert diagnostic.selected_count == 2
    assert diagnostic.shortfall == 1
    assert diagnostic.reason_codes == ("insufficient_exact_difficulty_inventory",)


@pytest.mark.parametrize("score", [64.4, True, -1, 101])
def test_rejects_non_integer_or_out_of_range_business_scores(score):
    with pytest.raises(ValueError):
        select_personalized_questions(
            student_id=STUDENT_ID,
            literacy_name="运算能力",
            literacy_score=score,
            knowledge_point_codes=[KNOWLEDGE_POINT_CODE],
            candidates=[],
        )


def test_rejects_unknown_literacy_and_invalid_knowledge_scope():
    with pytest.raises(ValueError):
        select_personalized_questions(
            student_id=STUDENT_ID,
            literacy_name="不存在的能力",
            literacy_score=64,
            knowledge_point_codes=[KNOWLEDGE_POINT_CODE],
            candidates=[],
        )
    with pytest.raises(ValueError):
        select_personalized_questions(
            student_id=STUDENT_ID,
            literacy_name="运算能力",
            literacy_score=64,
            knowledge_point_codes=[],
            candidates=[],
        )
