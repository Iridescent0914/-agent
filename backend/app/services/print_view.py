from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assignment, PaperInstance, PaperInstanceItem, Student
from app.schemas.recognition import AnswerSlotOut, PrintItemOut, PrintViewOut
from app.services.assets import resolve_assets


_PRIVATE_PRINT_KEYS = {
    "answer", "answer_snapshot", "answer_rule_snapshot", "correct_answer",
    "grading_details", "analysis", "selection_audit", "recommendation_score",
    "is_correct",
}


def _student_safe(value):
    if isinstance(value, dict):
        return {
            key: _student_safe(item)
            for key, item in value.items()
            if key not in _PRIVATE_PRINT_KEYS
        }
    if isinstance(value, list):
        return [_student_safe(item) for item in value]
    return value


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
        answer = item.answer_snapshot
        blank_ids = ["answer"]
        if isinstance(answer, dict) and answer.get("kind") == "group":
            blank_ids = [
                str(part.get("item_id"))
                for part in answer.get("items", [])
                if isinstance(part, dict) and part.get("item_id") is not None
            ] or ["answer"]
        print_items.append(PrintItemOut(
            id=item.id,
            order_no=item.order_no,
            question_snapshot=_student_safe(snapshot),
            options_snapshot=_student_safe(item.options_snapshot),
            assets=resolve_assets(snapshot.get("assets", [])),
        ))
        slots.append(AnswerSlotOut(
            order_no=item.order_no,
            blank_ids=blank_ids,
            kind=str(snapshot.get("question_type") or "fill_blank"),
        ))
    return PrintViewOut(
        paper_instance_id=paper.id,
        assignment_title=assignment.title,
        student_code=student.student_code,
        version_no=paper.version_no,
        instance_code=paper.instance_code,
        is_active=paper.invalidated_at is None,
        items=print_items,
        answer_slots=slots,
    )
