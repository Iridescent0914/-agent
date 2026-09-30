from __future__ import annotations

import json

from sqlalchemy import func, select, text

from app.core.database import SessionLocal
from app.models import Assignment, PaperInstance, Student, StudentMastery
from app.services.class_analytics import build_assignment_overview, build_class_overview
from app.services.student_profile import MASTERY_FIELDS, exam_point_catalog, load_context


CLASS_NAME = "六年级 Demo 班"
CHAPTER_CODE = "math.g6.rational"


def _exam_code(record: object) -> str | None:
    item = getattr(record, "item", None)
    snapshot = item.exam_point_snapshot if item is not None else None
    main = snapshot.get("main") if isinstance(snapshot, dict) else None
    code = main.get("id") if isinstance(main, dict) else None
    return code if isinstance(code, str) else None


def main() -> None:
    errors: list[str] = []
    warnings: list[str] = []
    with SessionLocal() as db:
        migration = db.scalar(text("SELECT version_num FROM alembic_version"))
        if migration != "0012":
            errors.append(f"migration is {migration}, expected 0012")

        students = list(
            db.scalars(
                select(Student).where(
                    Student.class_name == CLASS_NAME,
                    Student.archived_at.is_(None),
                )
            )
        )
        if len(students) != 5:
            errors.append(f"demo class has {len(students)} active students, expected 5")
        catalog = exam_point_catalog(db, CHAPTER_CODE)
        codes = {item.code for item in catalog}
        overview = build_class_overview(
            db, class_name=CLASS_NAME, chapter_code=CHAPTER_CODE
        )

        aggregate_mismatches = 0
        if overview.summary.confirmed_attempt_count != sum(
            row.confirmed_attempt_count for row in overview.exam_points
        ):
            aggregate_mismatches += 1
        if (
            overview.summary.covered_exam_point_count
            + overview.summary.uncovered_exam_point_count
            != len(overview.exam_points)
        ):
            aggregate_mismatches += 1
        for row in overview.exam_points:
            if row.correct_attempt_count + row.incorrect_attempt_count != row.confirmed_attempt_count:
                aggregate_mismatches += 1
            if row.evidence_student_count + row.unassessed_student_count != len(students):
                aggregate_mismatches += 1
            if (row.accuracy is None) != (row.confirmed_attempt_count == 0):
                aggregate_mismatches += 1
            if (row.average_mastery is None) != (row.evidence_student_count == 0):
                aggregate_mismatches += 1

        independent_confirmed = 0
        independent_pending = 0
        evidence_students = 0
        mastery_mismatches = 0
        for student in students:
            context = load_context(db, student.id)
            valid = [
                record
                for record in context.records
                if record.problem is None and _exam_code(record) in codes
            ]
            pending = [
                record
                for record in context.records
                if record.problem == "needs_review" and _exam_code(record) in codes
            ]
            independent_confirmed += len(valid)
            independent_pending += len(pending)
            evidence_students += bool(valid)
            stored = {
                row.exam_point_id: row
                for row in db.scalars(
                    select(StudentMastery).where(StudentMastery.student_id == student.id)
                )
            }
            expected = {exam_id: result for (_, exam_id), result in context.results.items()}
            if set(stored) != set(expected) or any(
                any(
                    getattr(stored[exam_id], field) != getattr(result, field)
                    for field in MASTERY_FIELDS
                )
                for exam_id, result in expected.items()
                if exam_id in stored
            ):
                mastery_mismatches += 1
        if independent_confirmed != overview.summary.confirmed_attempt_count:
            aggregate_mismatches += 1
        if independent_pending != overview.summary.pending_review_count:
            aggregate_mismatches += 1
        if evidence_students != overview.summary.evidence_student_count:
            aggregate_mismatches += 1

        duplicate_targets = db.execute(
            select(
                PaperInstance.assignment_id,
                PaperInstance.student_id,
                func.count(PaperInstance.id),
            )
            .where(
                PaperInstance.status == "published",
                PaperInstance.invalidated_at.is_(None),
            )
            .group_by(PaperInstance.assignment_id, PaperInstance.student_id)
            .having(func.count(PaperInstance.id) > 1)
        ).all()

        assignment_checks = 0
        for assignment in db.scalars(
            select(Assignment).where(Assignment.assignment_type != "historical_seed")
        ):
            build_assignment_overview(db, assignment.id)
            assignment_checks += 1
        demo_scenarios = list(
            db.scalars(select(Assignment).where(Assignment.seed_key.like("M5-DEMO-%")))
        )
        if not demo_scenarios:
            warnings.append("尚未在当前数据库准备 M5 固定演示场景")

        for name, count in {
            "aggregate mismatches": aggregate_mismatches,
            "mastery mismatches": mastery_mismatches,
            "duplicate active paper targets": len(duplicate_targets),
        }.items():
            if count:
                errors.append(f"{name}: {count}")

        output = {
            "schema_head": migration,
            "class_name": CLASS_NAME,
            "student_count": len(students),
            "exam_point_count": len(catalog),
            "confirmed_attempt_count": overview.summary.confirmed_attempt_count,
            "pending_review_count": overview.summary.pending_review_count,
            "invalid_evidence_count": overview.summary.invalid_evidence_count,
            "assignment_checks": assignment_checks,
            "demo_scenario_count": len(demo_scenarios),
            "duplicate_active_paper_targets": len(duplicate_targets),
            "aggregate_mismatches": aggregate_mismatches,
            "mastery_mismatches": mastery_mismatches,
            "validation_errors": len(errors),
            "warnings": warnings,
            "errors": errors,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if errors:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
