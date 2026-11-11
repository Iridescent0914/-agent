from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Student, StudentLiteracyScore
from app.services.literacy_scoring import persist_literacy_scores
from app.services.maintenance import (
    refresh_stale_literacy_scores,
    seconds_until_next_business_midnight,
)


TZ = ZoneInfo("Asia/Shanghai")


def test_stale_literacy_rows_are_advanced_once_per_business_day():
    with SessionLocal() as db:
        try:
            student = Student(
                student_code="LIT-MAINT-" + uuid4().hex[:12],
                display_name="每日回归维护测试学生",
                class_name="素养维护测试班",
                is_demo=True,
            )
            db.add(student)
            db.flush()
            persist_literacy_scores(
                db,
                student.id,
                as_of=datetime(2026, 10, 31, 12, 0, tzinfo=TZ),
            )

            first = refresh_stale_literacy_scores(
                db,
                as_of=datetime(2026, 11, 1, 0, 0, 1, tzinfo=TZ),
                student_ids=[student.id],
            )
            assert first == {
                "student_count": 1,
                "refreshed_students": 1,
                "current_students": 0,
                "skipped_students": 0,
                "failed_students": 0,
            }
            rows = list(db.scalars(select(StudentLiteracyScore).where(
                StudentLiteracyScore.student_id == student.id
            )))
            assert len(rows) == 9
            assert {row.score_as_of_date.isoformat() for row in rows} == {"2026-11-01"}

            second = refresh_stale_literacy_scores(
                db,
                as_of=datetime(2026, 11, 1, 12, 0, tzinfo=TZ),
                student_ids=[student.id],
            )
            assert second == {
                "student_count": 1,
                "refreshed_students": 0,
                "current_students": 1,
                "skipped_students": 0,
                "failed_students": 0,
            }
        finally:
            db.rollback()


def test_next_business_midnight_delay_uses_shanghai_date():
    now = datetime(2026, 11, 1, 23, 59, 30, tzinfo=TZ)
    assert seconds_until_next_business_midnight(now) == 30
