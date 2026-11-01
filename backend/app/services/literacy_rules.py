"""Small, deterministic implementation of the literacy scoring rules.

The legacy profile code stores a knowledge-point EMA in the range ``0..1``.
This module deliberately keeps the PDF rule separate and uses its native
``0..100`` score.  It can therefore be introduced incrementally without
changing the existing ``student_masteries`` table or replay semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from math import exp
from typing import Iterable, Mapping


STAR_DIFFICULTIES: dict[int, float] = {
    1: 30.0,
    2: 45.0,
    3: 60.0,
    4: 75.0,
    5: 90.0,
}
DEFAULT_SCORE = 60.0
DEFAULT_SCALE = 15.0
DEFAULT_LEARNING_RATE = 12.0
DEFAULT_LEARNING_DECAY = 30.0
MIN_LEARNING_RATE = 3.0
DEFAULT_DAILY_LIMIT = 8.0
DEFAULT_REGRESSION_RATE = 0.01
DEFAULT_CHOICE_GUESS_PROBABILITY = 0.25


def clamp_score(score: float) -> float:
    """Keep a score in the documented 0..100 range."""

    return max(0.0, min(100.0, float(score)))


def difficulty_value(star: int) -> float:
    """Convert a 1..5 star difficulty to the PDF's b value."""

    try:
        return STAR_DIFFICULTIES[int(star)]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("difficulty star must be an integer from 1 to 5") from exc


def expected_probability(
    score: float,
    star: int,
    *,
    choice: bool = False,
    guess_probability: float = DEFAULT_CHOICE_GUESS_PROBABILITY,
    scale: float = DEFAULT_SCALE,
) -> float:
    """Return the expected probability of a correct answer.

    For choice questions, the result includes the guessing floor described in
    the PDF: ``guess + (1 - guess) * p``.
    """

    if scale <= 0:
        raise ValueError("scale must be positive")
    if not 0 <= guess_probability < 1:
        raise ValueError("guess_probability must be in [0, 1)")
    margin = (clamp_score(score) - difficulty_value(star)) / scale
    # Avoid overflow for very large margins while preserving the logistic
    # curve's limiting values.
    if margin >= 0:
        base = 1.0 / (1.0 + exp(-margin))
    else:
        e = exp(margin)
        base = e / (1.0 + e)
    return guess_probability + (1.0 - guess_probability) * base if choice else base


def learning_rate(
    attempt_count: int,
    *,
    initial: float = DEFAULT_LEARNING_RATE,
    decay: float = DEFAULT_LEARNING_DECAY,
    minimum: float = MIN_LEARNING_RATE,
) -> float:
    """Decrease the single-answer learning rate as evidence accumulates."""

    if attempt_count < 0:
        raise ValueError("attempt_count cannot be negative")
    if initial <= 0 or decay <= 0 or minimum < 0:
        raise ValueError("learning-rate parameters must be non-negative")
    return max(minimum, initial / (1.0 + attempt_count / decay))


def update_score(
    score: float,
    star: int,
    is_correct: bool,
    attempt_count: int,
    *,
    weight: float = 1.0,
    choice: bool = False,
    guess_probability: float = DEFAULT_CHOICE_GUESS_PROBABILITY,
    scale: float = DEFAULT_SCALE,
) -> float:
    """Apply one answer and return the updated score.

    ``weight`` is the question's share of a multi-literacy item.  The caller
    can apply :func:`cap_daily_change` after processing all answers for the
    day.
    """

    if not 0 <= weight <= 1:
        raise ValueError("weight must be in [0, 1]")
    probability = expected_probability(
        score,
        star,
        choice=choice,
        guess_probability=guess_probability,
        scale=scale,
    )
    observed = 1.0 if is_correct else 0.0
    delta = learning_rate(attempt_count) * weight * (observed - probability)
    return clamp_score(score + delta)


def cap_daily_change(before: float, after: float, limit: float = DEFAULT_DAILY_LIMIT) -> float:
    """Limit one day's net movement to ``+/- limit`` points."""

    if limit < 0:
        raise ValueError("limit cannot be negative")
    before = clamp_score(before)
    after = clamp_score(after)
    delta = after - before
    if abs(delta) <= limit:
        return after
    return clamp_score(before + (limit if delta > 0 else -limit))


def base_star_for_score(score: float) -> int:
    """Map the half-up rounded business score to its fixed base-star band."""

    value = rounded_display_score(score)
    if value < 60:
        return 1
    if value < 70:
        return 2
    if value < 80:
        return 3
    if value < 90:
        return 4
    return 5


def personalized_stars(score: float) -> tuple[int, int, int]:
    """Return the base, base+1 and base+2 slots, capped at five stars."""

    base = base_star_for_score(score)
    return base, min(base + 1, 5), min(base + 2, 5)


def choice_guess_probability(option_count: int | None) -> float:
    """Use ``1 / option_count`` and fall back to the documented four-choice floor."""

    if option_count is None:
        return DEFAULT_CHOICE_GUESS_PROBABILITY
    if isinstance(option_count, bool) or option_count < 2:
        raise ValueError("choice option count must be at least 2")
    return 1.0 / option_count


def regress_score(
    score: float,
    *,
    baseline: float = DEFAULT_SCORE,
    rate: float = DEFAULT_REGRESSION_RATE,
) -> float:
    """Apply the small daily regression towards the baseline."""

    if not 0 <= rate <= 1:
        raise ValueError("rate must be in [0, 1]")
    return clamp_score(score + rate * (baseline - score))


def rounded_display_score(score: float) -> int:
    """Round only for display while keeping calculation values at full precision."""

    value = Decimal(str(clamp_score(score))).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return int(value)


def equal_literacy_weights(literacies: Iterable[str]) -> dict[str, float]:
    """Deduplicate non-empty literacy names and assign equal weights."""

    names: list[str] = []
    for value in literacies:
        if not isinstance(value, str):
            raise ValueError("literacy names must be strings")
        name = value.strip()
        if name and name not in names:
            names.append(name)
    if not names:
        raise ValueError("at least one literacy is required")
    weight = 1.0 / len(names)
    return {name: weight for name in names}


@dataclass(frozen=True)
class LiteracyUpdate:
    score_before: float
    score_after: float
    expected_probability: float
    delta: float
    learning_rate: float


def update_with_details(
    score: float,
    star: int,
    is_correct: bool,
    attempt_count: int,
    *,
    weight: float = 1.0,
    choice: bool = False,
    guess_probability: float = DEFAULT_CHOICE_GUESS_PROBABILITY,
    scale: float = DEFAULT_SCALE,
) -> LiteracyUpdate:
    """Same update as :func:`update_score`, with audit-friendly details."""

    if not 0 <= weight <= 1:
        raise ValueError("weight must be in [0, 1]")
    before = clamp_score(score)
    probability = expected_probability(
        before,
        star,
        choice=choice,
        guess_probability=guess_probability,
        scale=scale,
    )
    rate = learning_rate(attempt_count)
    delta = rate * weight * ((1.0 if is_correct else 0.0) - probability)
    return LiteracyUpdate(
        score_before=before,
        score_after=clamp_score(before + delta),
        expected_probability=probability,
        delta=delta,
        learning_rate=rate,
    )


def update_multi_literacy(
    scores: Mapping[str, float],
    attempts: Mapping[str, int],
    literacies: Iterable[str],
    star: int,
    is_correct: bool,
    *,
    choice: bool = False,
    guess_probability: float = DEFAULT_CHOICE_GUESS_PROBABILITY,
    scale: float = DEFAULT_SCALE,
) -> dict[str, float]:
    """Update all attached literacies using the required equal weighting."""

    weights = equal_literacy_weights(literacies)
    updated = dict(scores)
    for name, weight in weights.items():
        updated[name] = update_score(
            scores.get(name, DEFAULT_SCORE), star, is_correct,
            attempts.get(name, 0),
            weight=weight,
            choice=choice,
            guess_probability=guess_probability,
            scale=scale,
        )
    return updated
