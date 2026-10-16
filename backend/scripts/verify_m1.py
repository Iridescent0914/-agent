from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import distinct, func, select, text

from app.core.database import SessionLocal
from app.models import (
    Attempt,
    PaperInstance,
    PaperInstanceItem,
    Question,
    QuestionExamPoint,
    Student,
    Submission,
)


CODE_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = CODE_ROOT / "data" / "web" / "rational" / "asset_manifest.json"


def main() -> int:
    errors: list[str] = []
    with SessionLocal() as session:
        migration = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        question_count = session.scalar(select(func.count(Question.id))) or 0
        fill_count = session.scalar(
            select(func.count(Question.id)).where(Question.question_type == "fill_blank")
        ) or 0
        choice_count = session.scalar(
            select(func.count(Question.id)).where(Question.question_type == "choice")
        ) or 0
        true_false_count = session.scalar(
            select(func.count(Question.id)).where(Question.question_type == "true_false")
        ) or 0
        exam_count = session.scalar(
            select(func.count(distinct(QuestionExamPoint.exam_point_id))).where(
                QuestionExamPoint.relation_type == "main"
            )
        ) or 0
        question_asset_count = session.scalar(
            select(func.count(Question.id)).where(func.jsonb_array_length(Question.assets) > 0)
        ) or 0
        student_count = session.scalar(
            select(func.count(Student.id)).where(Student.is_demo.is_(True))
        ) or 0
        students_with_attempts = session.scalar(
            select(func.count(distinct(Attempt.student_id)))
            .join(Student, Student.id == Attempt.student_id)
            .where(Student.is_demo.is_(True))
        ) or 0
        attempt_count = session.scalar(select(func.count(Attempt.id))) or 0
        orphan_attempts = session.scalar(
            select(func.count(Attempt.id))
            .outerjoin(Student, Student.id == Attempt.student_id)
            .outerjoin(Submission, Submission.id == Attempt.submission_id)
            .outerjoin(PaperInstanceItem, PaperInstanceItem.id == Attempt.paper_instance_item_id)
            .where(
                (Student.id.is_(None))
                | (Submission.id.is_(None))
                | (PaperInstanceItem.id.is_(None))
            )
        ) or 0
        ownership_mismatches = session.scalar(
            select(func.count(Attempt.id))
            .join(Submission, Submission.id == Attempt.submission_id)
            .join(PaperInstance, PaperInstance.id == Submission.paper_instance_id)
            .where(
                (Attempt.student_id != Submission.student_id)
                | (Attempt.student_id != PaperInstance.student_id)
            )
        ) or 0
        not_ready = session.scalar(
            select(func.count(Question.id)).where(
                (Question.grading_readiness != "machine_ready")
                | (Question.source_review_status != "reviewed")
            )
        ) or 0

    manifest = {}
    if MANIFEST_PATH.exists():
        with MANIFEST_PATH.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    failed_assets = int(manifest.get("failed_count", -1))

    checks = {
        'schema_at_head': migration == '0022',
        "questions": question_count,
        "fill_blank": fill_count,
        "choice": choice_count,
        "true_false": true_false_count,
        "catalog_knowledge_points": exam_count,
        "questions_with_assets": question_asset_count,
        "unique_web_assets": manifest.get("unique_asset_count", 0),
        "missing_web_assets": failed_assets,
        "seed_students": student_count,
        "seed_students_with_attempts": students_with_attempts,
        "attempts": attempt_count,
        "orphan_records": orphan_attempts,
        "ownership_mismatches": ownership_mismatches,
        "pending_questions": not_ready,
    }
    expected = {
        "schema_at_head": True,
        "questions": 139,
        "fill_blank": 50,
        "choice": 11,
        "true_false": 2,
        "catalog_knowledge_points": 12,
        "questions_with_assets": 107,
        "unique_web_assets": 564,
        "missing_web_assets": 0,
        "seed_students": 5,
        "seed_students_with_attempts": 5,
        "orphan_records": 0,
        "ownership_mismatches": 0,
    }
    for key, wanted in expected.items():
        if checks[key] != wanted:
            errors.append(f"{key}: expected {wanted!r}, got {checks[key]!r}")
    if attempt_count == 0:
        errors.append("attempts: expected demo history, got 0")
    if not 0 <= not_ready <= question_count:
        errors.append(
            f"pending_questions: expected between 0 and {question_count}, got {not_ready}"
        )
    checks["validation_errors"] = len(errors)
    print(json.dumps(checks, ensure_ascii=False, indent=2, default=str))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
