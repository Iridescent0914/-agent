from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from sqlalchemy import func, select, text

from app.core.database import SessionLocal
from app.models import Attempt, AttemptRevision, Student, StudentMastery
from app.schemas.recommendations import RecommendationRequest
from app.services.recommendations import preview
from app.services.student_profile import MASTERY_FIELDS, build_profile, history_for, load_context


def main() -> int:
    errors = []
    counts_per_student = {}
    valid = invalid = mismatches = unassessed = 0
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        students = db.scalars(select(Student).order_by(Student.student_code)).all()
        summaries = {(row.student_id, row.exam_point_id): row for row in db.scalars(select(StudentMastery))}
        expected_keys = set()
        for student in students:
            context = load_context(db, student.id)
            profile = build_profile(context)
            valid += profile.summary.valid_attempt_count
            invalid += profile.summary.invalid_evidence_count
            unassessed += profile.summary.unassessed_exam_point_count
            counts_per_student[student.student_code] = profile.summary.assessed_exam_point_count
            for key, result in context.results.items():
                expected_keys.add(key)
                stored = summaries.get(key)
                if stored is None or any(getattr(stored, field) != getattr(result, field) for field in MASTERY_FIELDS):
                    mismatches += 1
                points = history_for(context, result.exam_point_code).points
                if len(points) != result.attempt_count or points[-1].mastery_after != float(result.mastery):
                    errors.append(f"{student.student_code}: history mismatch")
                if not (0 <= result.correct_count <= result.attempt_count
                        and 0 <= result.recent_wrong_count <= result.recent_attempt_count <= 5):
                    errors.append(f"{student.student_code}: invalid counts")
                for point in points:
                    record = next((r for r in context.records if r.attempt.id == point.attempt_id), None)
                    if record is None or record.problem or record.attempt.revision_no != point.revision_no:
                        errors.append(f"{student.student_code}: invalid evidence reference")
            if any(item.mastery is not None for item in profile.masteries if item.attempt_count == 0):
                errors.append(f"{student.student_code}: unassessed value must be null")
            params = RecommendationRequest(review_exam_point_codes=[exam.code for exam in context.catalog
                                                                    if exam.available_question_count], review_count=6)
            try:
                at = datetime(2026, 9, 23, tzinfo=timezone.utc)
                output = preview(db, context, params, as_of=at)
                if output != preview(db, context, params, as_of=at) or output.selected_count != 6:
                    errors.append(f"{student.student_code}: recommendation mismatch")
            except ValueError as exc:
                errors.append(f"{student.student_code}: {exc}")
        mismatches += len(set(summaries) - expected_keys)
        report = {
            "students": len(students), "valid_attempts": valid,
            "attempts": db.scalar(select(func.count(Attempt.id))),
            "attempt_revisions": db.scalar(select(func.count(AttemptRevision.id))),
            "materialized_mastery_rows": len(summaries), "assessed_exam_points_per_student": counts_per_student,
            "sum_mastery_attempt_counts": sum(row.attempt_count for row in summaries.values()),
            "unassessed_student_exam_pairs": unassessed, "invalid_evidence": invalid,
            "mastery_replay_mismatches": mismatches,
        }
    expected = {"students": 5, "valid_attempts": 150, "attempts": 150, "attempt_revisions": 150,
                "materialized_mastery_rows": 50, "sum_mastery_attempt_counts": 150,
                "unassessed_student_exam_pairs": 220, "invalid_evidence": 0, "mastery_replay_mismatches": 0}
    for key, value in expected.items():
        if report[key] != value:
            errors.append(f"{key}: expected {value}, got {report[key]}")
    if any(count != 10 for count in counts_per_student.values()):
        errors.append("Expected 10 assessed exam points per seed student")
    report["validation_errors"] = len(errors)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
