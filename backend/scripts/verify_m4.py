from __future__ import annotations

import json

from sqlalchemy import select, text

from app.core.database import SessionLocal
from app.models import (
    Attempt, AttemptRevision, PaperInstance, PaperInstanceItem, Student,
    StudentMastery, Submission,
)
from app.services.student_profile import MASTERY_FIELDS, load_context


def main() -> None:
    errors: list[str] = []
    with SessionLocal() as db:
        migration = db.scalar(text("SELECT version_num FROM alembic_version"))
        if migration != "0016":
            errors.append(f"migration is {migration}, expected 0016")

        submissions = list(db.scalars(select(Submission)))
        attempts = list(db.scalars(select(Attempt)))
        revisions = list(db.scalars(select(AttemptRevision)))
        papers = {item.id: item for item in db.scalars(select(PaperInstance))}
        items = {item.id: item for item in db.scalars(select(PaperInstanceItem))}
        submissions_by_id = {item.id: item for item in submissions}
        revisions_by_attempt: dict = {}
        for revision in revisions:
            revisions_by_attempt.setdefault(revision.attempt_id, []).append(revision)

        ownership_errors = 0
        revision_errors = 0
        state_errors = 0
        fingerprint_errors = 0
        operator_errors = 0
        for submission in submissions:
            paper = papers.get(submission.paper_instance_id)
            if paper is None or paper.student_id != submission.student_id:
                ownership_errors += 1
            if (not submission.idempotency_key.startswith("SEED-")
                    and submission.source == "manual" and not submission.request_fingerprint):
                fingerprint_errors += 1
        for attempt in attempts:
            submission = submissions_by_id.get(attempt.submission_id)
            item = items.get(attempt.paper_instance_item_id)
            if (submission is None or item is None or attempt.student_id != submission.student_id
                    or submission.paper_instance_id != item.paper_instance_id
                    or attempt.question_id != item.question_id):
                ownership_errors += 1
            current = revisions_by_attempt.get(attempt.id, [])
            numbers = sorted(value.revision_no for value in current)
            if numbers != list(range(1, attempt.revision_no + 1)):
                revision_errors += 1
            if ((attempt.grade_status == "needs_review") != (attempt.is_correct is None)):
                state_errors += 1
        operator_errors = sum(
            revision.operator_type in {"system", "teacher"} and not revision.operator_id
            for revision in revisions
        )

        mastery_mismatches = 0
        students_checked = 0
        active_students = list(db.scalars(select(Student).where(Student.archived_at.is_(None))))
        for student in active_students:
            context = load_context(db, student.id)
            if not context.records:
                continue
            students_checked += 1
            stored = {
                row.exam_point_id: row for row in db.scalars(
                    select(StudentMastery).where(StudentMastery.student_id == student.id)
                )
            }
            expected = {exam_id: result for (_, exam_id), result in context.results.items()}
            if set(stored) != set(expected):
                mastery_mismatches += 1
                continue
            if any(
                any(getattr(stored[exam_id], field) != getattr(result, field)
                    for field in MASTERY_FIELDS)
                for exam_id, result in expected.items()
            ):
                mastery_mismatches += 1

        for name, count in {
            "ownership errors": ownership_errors,
            "revision errors": revision_errors,
            "state errors": state_errors,
            "fingerprint errors": fingerprint_errors,
            "operator errors": operator_errors,
            "mastery mismatches": mastery_mismatches,
        }.items():
            if count:
                errors.append(f"{name}: {count}")

        output = {
            "migration": migration,
            "submission_count": len(submissions),
            "attempt_count": len(attempts),
            "revision_count": len(revisions),
            "pending_review_count": sum(item.grade_status == "needs_review" for item in attempts),
            "mastery_students_checked": students_checked,
            "ownership_errors": ownership_errors,
            "revision_errors": revision_errors,
            "state_errors": state_errors,
            "fingerprint_errors": fingerprint_errors,
            "operator_errors": operator_errors,
            "mastery_mismatches": mastery_mismatches,
            "validation_errors": len(errors),
            "warnings": ["尚无 M4 教师提交"] if not any(
                value.request_fingerprint for value in submissions
            ) else [],
            "errors": errors,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if errors:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
