from pytest import approx, raises

from app.services.literacy_rules import (
    cap_daily_change,
    expected_probability,
    learning_rate,
    question_mix,
    target_difficulty,
    update_multi_literacy,
    update_score,
)


def test_expected_probability_matches_pdf_examples():
    assert expected_probability(60, 1) == approx(0.8808, abs=0.001)
    assert expected_probability(60, 3) == approx(0.5)
    assert expected_probability(60, 5) == approx(0.1192, abs=0.001)


def test_choice_probability_includes_guessing_floor():
    assert expected_probability(60, 3, choice=True) == approx(0.625)


def test_learning_rate_decreases_and_has_floor():
    assert learning_rate(0) == 12
    assert learning_rate(30) == 6
    assert learning_rate(10_000) == 3


def test_updates_reward_unexpected_success_and_penalize_unexpected_failure():
    high_star_success = update_score(60, 5, True, 0)
    low_star_failure = update_score(60, 1, False, 0)
    assert high_star_success > 60
    assert low_star_failure < 60
    assert high_star_success - 60 == approx(10.57, abs=0.02)
    assert 60 - low_star_failure == approx(10.57, abs=0.02)


def test_target_difficulty_and_daily_cap():
    assert target_difficulty(60) == 2
    assert cap_daily_change(60, 90, 8) == 68
    assert cap_daily_change(60, 55, 8) == 55


def test_multi_literacy_normalizes_weights():
    updated = update_multi_literacy(
        {"运算": 60, "逻辑": 55}, {"运算": 0, "逻辑": 0},
        {"运算": 0.7, "逻辑": 0.3}, 3, True,
    )
    assert updated["运算"] > 60
    assert updated["逻辑"] > 55
    assert updated["运算"] - 60 > updated["逻辑"] - 55


def test_question_mix_is_complete_and_validates_input():
    mix = question_mix(10)
    assert mix == {"main": 7, "challenge": 2, "review": 1}
    with raises(ValueError):
        question_mix(-1)
