from pytest import approx, raises

from app.services.literacy_rules import (
    base_star_for_score,
    cap_daily_change,
    choice_guess_probability,
    equal_literacy_weights,
    expected_probability,
    learning_rate,
    personalized_stars,
    regress_score,
    rounded_display_score,
    update_multi_literacy,
    update_score,
    update_with_details,
)


def test_expected_probability_matches_pdf_examples():
    assert expected_probability(60, 1) == approx(0.8808, abs=0.001)
    assert expected_probability(60, 3) == approx(0.5)
    assert expected_probability(60, 5) == approx(0.1192, abs=0.001)


def test_choice_probability_includes_guessing_floor():
    assert expected_probability(60, 3, choice=True) == approx(0.625)
    five_choice_guess = choice_guess_probability(5)
    assert five_choice_guess == approx(0.2)
    assert expected_probability(
        60, 3, choice=True, guess_probability=five_choice_guess
    ) == approx(0.6)
    assert choice_guess_probability(None) == approx(0.25)
    with raises(ValueError):
        choice_guess_probability(1)


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


def test_fixed_base_star_bands_and_personalized_cap():
    cases = {
        0: 1, 59.49: 1,
        59.5: 2, 69.49: 2,
        69.5: 3, 79.49: 3,
        79.5: 4, 89.49: 4,
        89.5: 5,
        90: 5, 100: 5,
    }
    assert {score: base_star_for_score(score) for score in cases} == cases
    assert personalized_stars(45) == (1, 2, 3)
    assert personalized_stars(60) == (2, 3, 4)
    assert personalized_stars(75) == (3, 4, 5)
    assert personalized_stars(85) == (4, 5, 5)
    assert personalized_stars(98) == (5, 5, 5)


def test_daily_cap_regression_and_display_rounding():
    assert cap_daily_change(60, 90, 8) == 68
    assert cap_daily_change(60, 55, 8) == 55
    assert regress_score(80) == approx(79.8)
    assert regress_score(40) == approx(40.2)
    assert regress_score(60) == 60
    assert rounded_display_score(60.49) == 60
    assert rounded_display_score(60.5) == 61


def test_multi_literacy_normalizes_weights():
    assert equal_literacy_weights(["运算", "逻辑", "运算"]) == {
        "运算": 0.5,
        "逻辑": 0.5,
    }
    updated = update_multi_literacy(
        {"运算": 60, "逻辑": 55}, {"运算": 0, "逻辑": 0},
        ["运算", "逻辑"], 3, True,
    )
    assert updated["运算"] > 60
    assert updated["逻辑"] > 55
    assert updated["逻辑"] - 55 > updated["运算"] - 60
    with raises(ValueError):
        equal_literacy_weights([])


def test_invalid_inputs_are_rejected():
    with raises(ValueError):
        expected_probability(60, 0)
    with raises(ValueError):
        learning_rate(-1)
    with raises(ValueError):
        update_score(60, 3, True, 0, weight=1.1)
    with raises(ValueError):
        update_with_details(60, 3, True, 0, weight=-0.1)
    with raises(ValueError):
        regress_score(60, rate=1.1)
