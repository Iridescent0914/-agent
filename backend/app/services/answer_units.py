"""Normalize question answers into stable subquestion/blank leaf units."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any


ANSWER_SCHEMA_VERSION = "answer-units-v1"
AUTO_MATCH_TYPES = frozenset({"choice", "numeric", "exact", "latex_exact"})
MANUAL_QUESTION_TYPES = frozenset({"calculation", "solution"})

_MARKER_RE = re.compile(r"(?<![/^_])[（(]\s*(\d{1,2})\s*[）)](?!\s*/)")
_LINE_MARKER_TEMPLATE = r"(?m)^\s*[（(]\s*{number}\s*[）)]\s*"
_BLANK_RE = re.compile(r"_{2,}")


@dataclass(frozen=True)
class AnswerNormalization:
    answer: dict[str, Any]
    split_item_count: int
    accepted_answer_item_count: int
    manual_item_count: int
    unresolved_item_count: int


def _prefix_subquestion_markers(text: str) -> list[tuple[int, int, int]]:
    """Return the longest ordered 1..N marker prefix found in source text."""

    expected = 1
    result: list[tuple[int, int, int]] = []
    for match in _MARKER_RE.finditer(text or ""):
        number = int(match.group(1))
        if number == expected:
            result.append((number, match.start(), match.end()))
            expected += 1
        elif number > expected:
            break
    return result


def _blank_location_by_global_index(text: str) -> dict[int, tuple[int, int]]:
    markers = _prefix_subquestion_markers(text)
    if not markers:
        return {
            index: (1, index)
            for index, _ in enumerate(_BLANK_RE.finditer(text or ""), start=1)
        }

    result: dict[int, tuple[int, int]] = {}
    global_index = 1
    first_start = markers[0][1]
    prefix_count = len(list(_BLANK_RE.finditer((text or "")[:first_start])))
    for local_index in range(1, prefix_count + 1):
        result[global_index] = (1, local_index)
        global_index += 1
    for marker_index, (number, _start, end) in enumerate(markers):
        section_end = (
            markers[marker_index + 1][1]
            if marker_index + 1 < len(markers)
            else len(text or "")
        )
        count = len(list(_BLANK_RE.finditer((text or "")[end:section_end])))
        for local_index in range(1, count + 1):
            result[global_index] = (number, local_index)
            global_index += 1
    return result


def _expected_coordinates(
    question_type: str, question_text: str, *, grouped_answer: bool
) -> list[tuple[int, int]]:
    """Return answer slots implied by the question body, in display order."""

    # A scalar choice answer means the numbered clauses in the stem are
    # propositions to evaluate before selecting one option, not separately
    # answered subquestions (for example "四个说法中正确的个数是...").
    if question_type == "choice" and not grouped_answer:
        return [(1, 1)]

    blank_locations = _blank_location_by_global_index(question_text)
    if blank_locations:
        return list(dict.fromkeys(blank_locations.values()))

    marker_count = len(_prefix_subquestion_markers(question_text))
    if marker_count:
        return [(number, 1) for number in range(1, marker_count + 1)]
    return [(1, 1)]


def _split_answer_for_blanks(value: Any, count: int) -> list[str] | None:
    """Split only when punctuation gives exactly one value per known blank."""

    text = _text_answer(value)
    if text is None or count <= 1:
        return None
    parts = [part.strip() for part in re.split(r"[；;，,]", text)]
    if len(parts) != count or any(not part for part in parts):
        return None
    return parts


def _manual_reference_parts(reference: str, count: int) -> list[str | None]:
    clean = (reference or "").strip()
    if count <= 1:
        return [clean or None]

    ordered_markers = _prefix_subquestion_markers(clean)
    if len(ordered_markers) == count:
        parts = []
        for index, (_number, _start, end) in enumerate(ordered_markers):
            section_end = (
                ordered_markers[index + 1][1]
                if index + 1 < len(ordered_markers)
                else len(clean)
            )
            parts.append(clean[end:section_end].strip(" \n。") or None)
        return parts

    # Some source answers omit the first marker and begin "answer 1; (2)...".
    later_markers = []
    search_start = 0
    for number in range(2, count + 1):
        match = re.search(
            rf"[（(]\s*{number}\s*[）)]", clean[search_start:]
        )
        if match is None:
            later_markers = []
            break
        absolute_start = search_start + match.start()
        absolute_end = search_start + match.end()
        later_markers.append((absolute_start, absolute_end))
        search_start = absolute_end
    if len(later_markers) == count - 1:
        parts = [clean[:later_markers[0][0]].strip(" \n。；;") or None]
        for index, (_start, end) in enumerate(later_markers):
            section_end = (
                later_markers[index + 1][0]
                if index + 1 < len(later_markers)
                else len(clean)
            )
            parts.append(clean[end:section_end].strip(" \n。；;") or None)
        return parts

    marker_matches = []
    for number in range(1, count + 1):
        match = re.search(
            _LINE_MARKER_TEMPLATE.format(number=number), clean
        )
        if match is None:
            marker_matches = []
            break
        marker_matches.append(match)
    if marker_matches and all(
        marker_matches[index].start() < marker_matches[index + 1].start()
        for index in range(len(marker_matches) - 1)
    ):
        parts = []
        for index, match in enumerate(marker_matches):
            end = (
                marker_matches[index + 1].start()
                if index + 1 < len(marker_matches)
                else len(clean)
            )
            parts.append(clean[match.end():end].strip(" \n。") or None)
        return parts

    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    if len(lines) == count:
        return lines
    separated = [
        part.strip() for part in re.split(r"[；;]", clean) if part.strip()
    ]
    if len(separated) == count:
        return separated
    return [None] * count


def _text_answer(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    return None


def _leaf(
    *,
    subquestion_no: int,
    blank_no: int,
    order_no: int,
    answer: str | None,
    match_type: str,
    reference_answer_raw: str | None,
    accepted_answers: list[str] | None = None,
) -> dict[str, Any]:
    item = {
        "item_id": f"sub_{subquestion_no}.blank_{blank_no}",
        "subquestion_id": f"sub_{subquestion_no}",
        "subquestion_no": subquestion_no,
        "blank_id": f"blank_{blank_no}",
        "blank_no": blank_no,
        "order_no": order_no,
        "answer": answer,
        "answer_match_type": match_type,
        "reference_answer_raw": reference_answer_raw,
    }
    if accepted_answers:
        item["accepted_answers"] = accepted_answers
    return item


def normalize_answer_structure(
    *,
    question_type: str,
    question_text: str,
    correct_answer: Any,
    answer_match_type: str | None,
    reference_answer_raw: str,
) -> AnswerNormalization:
    """Return the canonical answer-unit structure without inventing answers."""

    source = deepcopy(correct_answer)
    source_items = (
        source.get("items", [])
        if isinstance(source, dict) and source.get("kind") == "group"
        else None
    )
    blank_locations = _blank_location_by_global_index(question_text)
    expected_coordinates = _expected_coordinates(
        question_type,
        question_text,
        grouped_answer=source_items is not None and len(source_items) > 1,
    )
    expected_blank_count: dict[int, int] = {}
    for subquestion_no, blank_no in expected_coordinates:
        expected_blank_count[subquestion_no] = max(
            expected_blank_count.get(subquestion_no, 0), blank_no
        )
    marker_count = len(_prefix_subquestion_markers(question_text))
    leaves: list[dict[str, Any]] = []
    next_blank_by_subquestion: dict[int, int] = {}
    split_count = 0
    accepted_count = 0
    stored_items_by_subquestion: dict[int, int] = {}
    if source_items is not None:
        for source_item in source_items:
            if not isinstance(source_item, dict):
                continue
            source_subquestion = source_item.get("subquestion_no")
            if isinstance(source_subquestion, int) and source_subquestion >= 1:
                stored_items_by_subquestion[source_subquestion] = (
                    stored_items_by_subquestion.get(source_subquestion, 0) + 1
                )

    def reserve_blank(subquestion_no: int, suggested: int | None = None) -> int:
        current = next_blank_by_subquestion.get(subquestion_no, 1)
        blank_no = max(current, suggested or current)
        next_blank_by_subquestion[subquestion_no] = blank_no + 1
        return blank_no

    if source_items is not None:
        for position, raw_item in enumerate(source_items, start=1):
            item = raw_item if isinstance(raw_item, dict) else {}
            old_id = str(item.get("item_id") or position)
            numeric_id = re.fullmatch(r"\d+", old_id)
            global_blank = re.fullmatch(r"blank_(\d+)", old_id)
            suggested_blank = None
            stored_subquestion_no = item.get("subquestion_no")
            stored_blank_no = item.get("blank_no")
            if (
                isinstance(stored_subquestion_no, int)
                and stored_subquestion_no >= 1
                and isinstance(stored_blank_no, int)
                and stored_blank_no >= 1
            ):
                subquestion_no = stored_subquestion_no
                suggested_blank = stored_blank_no
            elif global_blank:
                global_index = int(global_blank.group(1))
                subquestion_no, suggested_blank = blank_locations.get(
                    global_index, (1, global_index)
                )
            elif numeric_id:
                number = max(1, int(old_id))
                if marker_count >= number or question_type in {"choice", "true_false"}:
                    subquestion_no = number
                else:
                    subquestion_no = 1
                    suggested_blank = number
            elif question_type in {"choice", "true_false"} and len(source_items) > 1:
                subquestion_no = position
            else:
                subquestion_no = 1

            raw_answer = item.get("answer")
            raw_match_type = str(item.get("answer_match_type") or "manual")
            reference = _text_answer(item.get("reference_answer_raw"))
            if isinstance(raw_answer, list) and raw_match_type == "set":
                candidates = [
                    answer for value in raw_answer
                    if (answer := _text_answer(value)) is not None
                ]
                canonical = candidates[0] if candidates else None
                blank_no = reserve_blank(subquestion_no, suggested_blank)
                leaves.append(_leaf(
                    subquestion_no=subquestion_no,
                    blank_no=blank_no,
                    order_no=len(leaves) + 1,
                    answer=canonical,
                    match_type="exact" if canonical is not None else "manual",
                    reference_answer_raw=reference or canonical,
                    accepted_answers=list(dict.fromkeys(candidates)) or None,
                ))
                accepted_count += 1
                continue

            # Legacy grouped answers often stored all blanks of one
            # subquestion in a comma/semicolon-delimited scalar.  Split only
            # when the known blank count and delimiter count agree exactly.
            stored_single_combined_item = (
                isinstance(stored_subquestion_no, int)
                and stored_blank_no == 1
                and stored_items_by_subquestion.get(stored_subquestion_no) == 1
            )
            if (
                not (
                    isinstance(stored_subquestion_no, int)
                    and isinstance(stored_blank_no, int)
                )
                or stored_single_combined_item
            ):
                split_values = _split_answer_for_blanks(
                    raw_answer, expected_blank_count.get(subquestion_no, 1)
                )
                if split_values is not None:
                    raw_answer = split_values

            if isinstance(raw_answer, list):
                split_count += max(0, len(raw_answer) - 1)
                for value_index, value in enumerate(raw_answer, start=1):
                    answer = _text_answer(value)
                    blank_no = reserve_blank(
                        subquestion_no,
                        suggested_blank if value_index == 1 else None,
                    )
                    leaves.append(_leaf(
                        subquestion_no=subquestion_no,
                        blank_no=blank_no,
                        order_no=len(leaves) + 1,
                        answer=answer,
                        match_type=(
                            raw_match_type
                            if raw_match_type in AUTO_MATCH_TYPES and answer is not None
                            else "manual"
                        ),
                        reference_answer_raw=answer,
                    ))
                continue

            answer = _text_answer(raw_answer)
            blank_no = reserve_blank(subquestion_no, suggested_blank)
            stored_accepted_answers = item.get("accepted_answers")
            accepted_answers = (
                [
                    value for candidate in stored_accepted_answers
                    if (value := _text_answer(candidate)) is not None
                ]
                if isinstance(stored_accepted_answers, list)
                else []
            )
            leaves.append(_leaf(
                subquestion_no=subquestion_no,
                blank_no=blank_no,
                order_no=len(leaves) + 1,
                answer=answer,
                match_type=(
                    raw_match_type
                    if raw_match_type in AUTO_MATCH_TYPES and answer is not None
                    else "manual"
                ),
                reference_answer_raw=reference or answer,
                accepted_answers=(
                    list(dict.fromkeys([
                        *([answer] if answer is not None else []),
                        *accepted_answers,
                    ]))
                    if accepted_answers or raw_match_type == "set"
                    else None
                ),
            ))
    elif source is not None:
        answer = _text_answer(source)
        scalar_match_type = str(answer_match_type or "manual")
        leaves.append(_leaf(
            subquestion_no=1,
            blank_no=1,
            order_no=1,
            answer=answer,
            match_type=(
                "manual"
                if question_type in MANUAL_QUESTION_TYPES
                else scalar_match_type if scalar_match_type in AUTO_MATCH_TYPES
                else "manual"
            ),
            reference_answer_raw=(reference_answer_raw or answer),
        ))
    else:
        subquestion_count = marker_count or 1
        references = _manual_reference_parts(reference_answer_raw, subquestion_count)
        for subquestion_no, reference in enumerate(references, start=1):
            leaves.append(_leaf(
                subquestion_no=subquestion_no,
                blank_no=1,
                order_no=len(leaves) + 1,
                answer=reference,
                match_type="manual",
                reference_answer_raw=reference,
            ))

    # The question body is the authoritative slot skeleton.  Old answer
    # records sometimes omitted an entire subquestion; retain that slot as an
    # explicit unresolved item instead of silently hiding it from review.
    occupied = {
        (item["subquestion_no"], item["blank_no"])
        for item in leaves
    }
    for subquestion_no, blank_no in expected_coordinates:
        if (subquestion_no, blank_no) in occupied:
            continue
        leaves.append(_leaf(
            subquestion_no=subquestion_no,
            blank_no=blank_no,
            order_no=len(leaves) + 1,
            answer=None,
            match_type="manual",
            reference_answer_raw=None,
        ))
        occupied.add((subquestion_no, blank_no))

    # Resolve accidental duplicate identifiers from legacy imports without
    # changing answer order or content.
    used_ids: set[str] = set()
    for item in leaves:
        while item["item_id"] in used_ids:
            item["blank_no"] += 1
            item["blank_id"] = f"blank_{item['blank_no']}"
            item["item_id"] = (
                f"sub_{item['subquestion_no']}.blank_{item['blank_no']}"
            )
        used_ids.add(item["item_id"])

    # A legacy grouped answer may contain null manual items even though the
    # question-level reference can be split safely. Apply the same enrichment
    # to every input shape so one normalization pass is already idempotent.
    if leaves:
        subquestion_count = max(item["subquestion_no"] for item in leaves)
        manual_parts = _manual_reference_parts(
            reference_answer_raw, subquestion_count
        )
        for item in leaves:
            if (
                item["answer"] is None
                and item["answer_match_type"] == "manual"
                and item["blank_no"] == 1
                and item["subquestion_no"] <= len(manual_parts)
            ):
                reference = manual_parts[item["subquestion_no"] - 1]
                if reference is not None:
                    item["answer"] = reference
                    item["reference_answer_raw"] = reference
                    if question_type not in MANUAL_QUESTION_TYPES:
                        item["answer_match_type"] = "exact"

        # A common extraction shape omits marker (1), then starts with (2).
        # The prefix before (2) is still an unambiguous answer for subquestion
        # 1 even when a later answer (for example subquestion 10) is absent.
        second_marker = re.search(r"[（(]\s*2\s*[）)]", reference_answer_raw)
        if second_marker is not None:
            first_reference = reference_answer_raw[:second_marker.start()].strip(
                " \n。；;"
            )
            if first_reference:
                for item in leaves:
                    if (
                        item["subquestion_no"] == 1
                        and item["blank_no"] == 1
                        and item["answer"] is None
                    ):
                        item["answer"] = first_reference
                        item["reference_answer_raw"] = first_reference
                        if question_type not in MANUAL_QUESTION_TYPES:
                            item["answer_match_type"] = "exact"
                        break

    leaves.sort(key=lambda item: (item["subquestion_no"], item["blank_no"]))
    for order_no, item in enumerate(leaves, start=1):
        item["order_no"] = order_no

    manual = sum(item["answer_match_type"] == "manual" for item in leaves)
    unresolved = sum(item["answer"] is None for item in leaves)
    return AnswerNormalization(
        answer={
            "kind": "group",
            "schema_version": ANSWER_SCHEMA_VERSION,
            "items": leaves,
        },
        split_item_count=split_count,
        accepted_answer_item_count=accepted_count,
        manual_item_count=manual,
        unresolved_item_count=unresolved,
    )


def is_canonical_answer_structure(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("kind") != "group" or value.get("schema_version") != ANSWER_SCHEMA_VERSION:
        return False
    items = value.get("items")
    if not isinstance(items, list) or not items:
        return False
    ids = []
    for order_no, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            return False
        ids.append(item.get("item_id"))
        if (
            item.get("subquestion_id") != f"sub_{item.get('subquestion_no')}"
            or item.get("blank_id") != f"blank_{item.get('blank_no')}"
            or item.get("item_id")
            != f"{item.get('subquestion_id')}.{item.get('blank_id')}"
            or item.get("order_no") != order_no
            or isinstance(item.get("answer"), (list, dict))
        ):
            return False
    return len(ids) == len(set(ids))
