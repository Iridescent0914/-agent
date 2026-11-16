from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assignment, PaperInstance, PaperInstanceItem, Student
from app.schemas.recognition import (
    AnswerSlotOut, PrintAnswerBlankOut, PrintAnswerSubquestionOut, PrintItemOut,
    PrintSourceModule, PrintViewOut,
)
from app.services.assets import resolve_assets
from app.services.paper_snapshots import student_safe_question_snapshot, student_safe_value


def _positive_int(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def answer_subquestions(answer: Any, question_type: str) -> list[PrintAnswerSubquestionOut]:
    """Project reference-answer coordinates without exposing answer values or rules.

    Canonical references carry explicit coordinates. Older published groups may
    only have item IDs; keep those IDs and derive display coordinates locally,
    without normalizing or rewriting the published reference.
    """
    if isinstance(answer, dict) and answer.get("kind") == "group":
        parts = answer.get("items")
        if not isinstance(parts, list) or not parts:
            raise ValueError("参考答案缺少作答单元，无法生成答题卡")
    else:
        parts = [{"item_id": "answer", "subquestion_no": 1, "blank_no": 1}]
    groups: dict[int, list[PrintAnswerBlankOut]] = {}
    used_ids: set[str] = set()
    used_coordinates: set[tuple[int, int]] = set()
    for position, part in enumerate(parts, start=1):
        if not isinstance(part, dict) or part.get("item_id") is None:
            raise ValueError("参考答案作答单元缺少编号，无法生成答题卡")
        item_id = str(part["item_id"])
        if not item_id.strip() or item_id in used_ids:
            raise ValueError("参考答案作答单元编号为空或重复，无法生成答题卡")
        canonical = re.fullmatch(r"sub_([1-9]\d*)\.blank_([1-9]\d*)", item_id)
        subquestion_no = _positive_int(part.get("subquestion_no"))
        blank_no = _positive_int(part.get("blank_no"))
        if canonical:
            coordinates = (int(canonical[1]), int(canonical[2]))
            if ((subquestion_no is not None and subquestion_no != coordinates[0])
                    or (blank_no is not None and blank_no != coordinates[1])):
                raise ValueError("参考答案的小题和空号不一致，无法生成答题卡")
            subquestion_no, blank_no = coordinates
        else:
            subquestion_no = subquestion_no or _positive_int(
                int(item_id) if item_id.isdecimal() else None
            ) or position
            blank_no = blank_no or 1
        coordinate = (subquestion_no, blank_no)
        if coordinate in used_coordinates:
            raise ValueError("参考答案的小题和空号重复，无法生成答题卡")
        kind = question_type if question_type in {"choice", "fill_blank", "true_false"} else "manual"
        if part.get("answer_match_type") == "manual":
            kind = "manual"
        groups.setdefault(subquestion_no, []).append(PrintAnswerBlankOut(
            item_id=item_id, blank_no=blank_no, kind=kind,
        ))
        used_ids.add(item_id)
        used_coordinates.add(coordinate)
    return [
        PrintAnswerSubquestionOut(
            subquestion_no=number,
            blanks=sorted(blanks, key=lambda blank: blank.blank_no),
        )
        for number, blanks in sorted(groups.items())
    ]


def print_source_module(audit: Any) -> PrintSourceModule:
    source = audit.get("source_module") if isinstance(audit, dict) else None
    if source in {"required", "personalized", "history", "weekly_summary"}:
        return source
    # An old question's current is_required flag cannot identify its role in
    # a historical paper. Do not guess from the current question catalog.
    return "unclassified"


def build_print_view(db: Session, paper_id: UUID) -> PrintViewOut:
    paper = db.get(PaperInstance, paper_id)
    if paper is None:
        raise LookupError("paper instance not found")
    if paper.status != "published" or paper.instance_code is None:
        raise ValueError("only published paper instances have a formal print view")
    student = db.get(Student, paper.student_id)
    assignment = db.get(Assignment, paper.assignment_id)
    if student is None or assignment is None:
        raise ValueError("paper ownership is incomplete")
    items = list(db.scalars(
        select(PaperInstanceItem).where(
            PaperInstanceItem.paper_instance_id == paper.id
        ).order_by(PaperInstanceItem.order_no)
    ))
    print_items = []
    slots = []
    for item in items:
        snapshot = item.question_snapshot or {}
        question_type = str(snapshot.get("question_type") or "fill_blank")
        subquestions = answer_subquestions(item.answer_snapshot, question_type)
        print_items.append(PrintItemOut(
            id=item.id,
            order_no=item.order_no,
            question_snapshot=student_safe_question_snapshot(snapshot),
            options_snapshot=student_safe_value(item.options_snapshot),
            assets=resolve_assets(snapshot.get("assets", [])),
        ))
        slots.append(AnswerSlotOut(
            order_no=item.order_no,
            blank_ids=[blank.item_id for row in subquestions for blank in row.blanks],
            kind=question_type,
            source_module=print_source_module(item.selection_audit),
            subquestions=subquestions,
        ))
    return PrintViewOut(
        paper_instance_id=paper.id,
        assignment_title=assignment.title,
        student_code=student.student_code,
        version_no=paper.version_no,
        instance_code=paper.instance_code,
        is_active=paper.invalidated_at is None,
        assignment_type=assignment.assignment_type,
        items=print_items,
        answer_slots=slots,
    )
