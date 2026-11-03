"""Pure, deterministic selection rules for the three personalized questions."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.services.literacy_catalog import LITERACY_DIMENSIONS
from app.services.literacy_rules import base_star_for_score, personalized_stars
from app.services.recommendations import Candidate


@dataclass(frozen=True)
class PersonalizedQuestionSlot:
    slot_no: int
    target_star: int
    candidate: Candidate | None


@dataclass(frozen=True)
class PersonalizedDifficultyDiagnostic:
    difficulty: int
    requested_count: int
    exact_candidate_count: int
    ready_candidate_count: int
    available_candidate_count: int
    selected_count: int
    shortfall: int
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class PersonalizedSelectionResult:
    student_id: UUID
    literacy_name: str
    literacy_score: int
    base_star: int
    target_stars: tuple[int, int, int]
    knowledge_point_codes: tuple[str, ...]
    slots: tuple[PersonalizedQuestionSlot, ...]
    diagnostics: tuple[PersonalizedDifficultyDiagnostic, ...]
    status: Literal["ready", "insufficient_candidates"]

    @property
    def selected_count(self) -> int:
        return sum(slot.candidate is not None for slot in self.slots)

    @property
    def shortfall(self) -> int:
        return len(self.slots) - self.selected_count

    @property
    def selected_question_ids(self) -> tuple[UUID, ...]:
        return tuple(
            slot.candidate.question_id
            for slot in self.slots
            if slot.candidate is not None
        )


def _validate_business_inputs(
    literacy_name: str,
    literacy_score: int,
    knowledge_point_codes: Iterable[str],
) -> tuple[str, ...]:
    if literacy_name not in LITERACY_DIMENSIONS:
        raise ValueError("主要能力必须是正式核心素养维度")
    if isinstance(literacy_score, bool) or not isinstance(literacy_score, int):
        raise ValueError("个性化组卷只能使用四舍五入后的整数素养分")
    if not 0 <= literacy_score <= 100:
        raise ValueError("整数素养分必须位于 0 到 100")
    codes = tuple(knowledge_point_codes)
    if not codes or any(not isinstance(code, str) or not code.strip() for code in codes):
        raise ValueError("个性化组卷必须选择至少一个有效知识点")
    if len(set(codes)) != len(codes):
        raise ValueError("个性化知识点不能重复")
    return codes


def _stable_unique(candidates: Iterable[Candidate]) -> list[Candidate]:
    ordered = sorted(
        candidates,
        key=lambda item: (item.original_qid, item.question_id.int),
    )
    unique: dict[UUID, Candidate] = {}
    for candidate in ordered:
        unique.setdefault(candidate.question_id, candidate)
    return list(unique.values())


def _shortfall_reasons(
    *,
    requested: int,
    exact: int,
    ready: int,
    available: int,
) -> tuple[str, ...]:
    if available >= requested:
        return ()
    reasons = ["insufficient_exact_difficulty_inventory"]
    if exact == 0:
        reasons.append("no_exact_difficulty_candidates")
    elif ready < min(exact, requested):
        reasons.append("insufficient_machine_ready_inventory")
    if available < min(ready, requested):
        reasons.append("excluded_question_conflict")
    return tuple(reasons)


def select_personalized_questions(
    *,
    student_id: UUID,
    literacy_name: str,
    literacy_score: int,
    knowledge_point_codes: Iterable[str],
    candidates: Iterable[Candidate],
    excluded_question_ids: Collection[UUID] = (),
) -> PersonalizedSelectionResult:
    """Select the exact base/+1/+2-star slots without fallback or duplicates.

    ``literacy_score`` is the single public business integer.  Decimal scoring
    precision remains an implementation detail of the profile service and is
    deliberately not accepted by this boundary.
    """

    codes = _validate_business_inputs(
        literacy_name, literacy_score, knowledge_point_codes
    )
    knowledge_scope = set(codes)
    excluded = set(excluded_question_ids)
    unique = _stable_unique(candidates)
    scoped = [
        candidate
        for candidate in unique
        if candidate.knowledge_point_code in knowledge_scope
    ]
    ready = [candidate for candidate in scoped if candidate.ready]
    available = [
        candidate for candidate in ready
        if candidate.question_id not in excluded
    ]

    target_stars = personalized_stars(literacy_score)
    used = set(excluded)
    slots: list[PersonalizedQuestionSlot] = []
    for slot_no, target_star in enumerate(target_stars, start=1):
        chosen = next(
            (
                candidate
                for candidate in available
                if candidate.difficulty == target_star
                and candidate.question_id not in used
            ),
            None,
        )
        if chosen is not None:
            used.add(chosen.question_id)
        slots.append(PersonalizedQuestionSlot(
            slot_no=slot_no,
            target_star=target_star,
            candidate=chosen,
        ))

    requested_by_star = Counter(target_stars)
    selected_by_star = Counter(
        slot.target_star for slot in slots if slot.candidate is not None
    )
    diagnostics = tuple(
        PersonalizedDifficultyDiagnostic(
            difficulty=difficulty,
            requested_count=requested,
            exact_candidate_count=sum(
                candidate.difficulty == difficulty for candidate in scoped
            ),
            ready_candidate_count=sum(
                candidate.difficulty == difficulty for candidate in ready
            ),
            available_candidate_count=sum(
                candidate.difficulty == difficulty for candidate in available
            ),
            selected_count=selected_by_star[difficulty],
            shortfall=requested - selected_by_star[difficulty],
            reason_codes=_shortfall_reasons(
                requested=requested,
                exact=sum(candidate.difficulty == difficulty for candidate in scoped),
                ready=sum(candidate.difficulty == difficulty for candidate in ready),
                available=sum(
                    candidate.difficulty == difficulty for candidate in available
                ),
            ),
        )
        for difficulty, requested in sorted(requested_by_star.items())
    )
    status: Literal["ready", "insufficient_candidates"] = (
        "ready" if all(slot.candidate is not None for slot in slots)
        else "insufficient_candidates"
    )
    return PersonalizedSelectionResult(
        student_id=student_id,
        literacy_name=literacy_name,
        literacy_score=literacy_score,
        base_star=base_star_for_score(literacy_score),
        target_stars=target_stars,
        knowledge_point_codes=codes,
        slots=tuple(slots),
        diagnostics=diagnostics,
        status=status,
    )
