from pathlib import Path

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models import (
    Assignment, SchoolClass, Student, StudentKnowledgeStatus, StudentLiteracyScore,
)
from app.services.current_demo import load_demo_config, prepare_current_demo


CONFIG_PATH = (
    Path(__file__).resolve().parents[2]
    / "data" / "seed" / "current_demo_scenario.json"
)


def test_current_demo_is_complete_and_idempotent() -> None:
    config = load_demo_config(CONFIG_PATH)
    with SessionLocal() as db:
        try:
            first = prepare_current_demo(db, config)
            second = prepare_current_demo(db, config)

            school_class = db.scalar(select(SchoolClass).where(
                SchoolClass.name == config["class"]["name"]
            ))
            assert school_class is not None
            assert school_class.grade == "六年级"
            assert school_class.school_year == "2026-2027"
            assert len(list(db.scalars(select(Student).where(
                Student.class_id == school_class.id,
                Student.archived_at.is_(None),
            )))) == 5

            expected_seed_keys = {
                "CURRENT-DEMO-REQUIRED-1",
                "CURRENT-DEMO-REQUIRED-2",
                "CURRENT-DEMO-HISTORY",
                "CURRENT-DEMO-WEEKLY",
            }
            actual_seed_keys = set(db.scalars(select(Assignment.seed_key).where(
                Assignment.seed_key.in_(expected_seed_keys)
            )))
            assert actual_seed_keys == expected_seed_keys

            statuses = set(db.scalars(select(StudentKnowledgeStatus.status).join(
                Student, Student.id == StudentKnowledgeStatus.student_id
            ).where(Student.class_id == school_class.id)))
            assert {"yes", "no", "weak"} <= statuses
            assert db.scalar(select(func.count(StudentLiteracyScore.student_id)).join(
                Student, Student.id == StudentLiteracyScore.student_id
            ).where(Student.class_id == school_class.id)) == 45

            assert first["student_count"] == 5
            assert second["class_created"] is False
            assert second["students_created"] == 0
            assert second["source_assignments_created"] == 0
            assert second["history_assignment_created"] is False
            assert second["history_submissions_created"] == 0
            assert second["weekly_assignment_created"] is False
            assert second["weekly_submissions_created"] == 0
            assert all(
                stats == {
                    "inserted": 0, "updated": 0,
                    "deleted": 0, "unchanged": 9,
                }
                for stats in second["literacy_score_stats"].values()
            )
        finally:
            db.rollback()
