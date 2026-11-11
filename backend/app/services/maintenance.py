from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models import Student, StudentLiteracyScore
from app.services.assets import cleanup_orphan_teacher_assets
from app.services.literacy_catalog import LITERACY_DIMENSIONS
from app.services.literacy_scoring import (
    BUSINESS_TIMEZONE,
    RULE_VERSION as LITERACY_RULE_VERSION,
    persist_literacy_scores,
)
from app.services.recognition_workflow import purge_expired_recognition_images
from app.services.question_import import purge_expired_import_files


logger = logging.getLogger(__name__)


def run_file_maintenance() -> dict[str, int]:
    """Run idempotent retention work in its own short database transaction."""
    with SessionLocal() as db:
        recognition_images = purge_expired_recognition_images(db)
        teacher_assets = cleanup_orphan_teacher_assets(db)
        question_import_files = purge_expired_import_files(db)
        db.commit()
    return {
        "recognition_images": recognition_images,
        "teacher_assets": teacher_assets,
        "question_import_files": question_import_files,
    }


def refresh_stale_literacy_scores(
    db: Session,
    *,
    as_of: datetime,
    student_ids: list[UUID] | None = None,
) -> dict[str, int]:
    """Advance stale materialized scores to the current Shanghai business date."""

    target_date = as_of.astimezone(BUSINESS_TIMEZONE).date()
    query = select(Student.id, Student.student_code).where(
        Student.archived_at.is_(None)
    )
    if student_ids is not None:
        query = query.where(Student.id.in_(student_ids))
    students = list(db.execute(query.order_by(Student.student_code, Student.id)))
    report = {
        "student_count": len(students),
        "refreshed_students": 0,
        "current_students": 0,
        "skipped_students": 0,
        "failed_students": 0,
    }
    for student_id, student_code in students:
        try:
            with db.begin_nested():
                student = db.scalar(
                    select(Student)
                    .where(Student.id == student_id, Student.archived_at.is_(None))
                    .with_for_update(skip_locked=True)
                )
                if student is None:
                    report["skipped_students"] += 1
                    continue
                rows = list(db.scalars(select(StudentLiteracyScore).where(
                    StudentLiteracyScore.student_id == student_id
                )))
                current = (
                    {row.literacy_name for row in rows} == set(LITERACY_DIMENSIONS)
                    and all(row.rule_version == LITERACY_RULE_VERSION for row in rows)
                    and all(row.score_as_of_date == target_date for row in rows)
                )
                if current:
                    report["current_students"] += 1
                    continue
                persist_literacy_scores(db, student_id, as_of=as_of)
                report["refreshed_students"] += 1
        except Exception:
            report["failed_students"] += 1
            logger.exception(
                "literacy score maintenance failed for student %s", student_code
            )
    return report


def run_literacy_score_maintenance(
    *, as_of: datetime | None = None,
) -> dict[str, int]:
    """Refresh stale score rows in one restart-safe maintenance transaction."""

    calculated_at = as_of or datetime.now(timezone.utc)
    with SessionLocal() as db:
        report = refresh_stale_literacy_scores(db, as_of=calculated_at)
        db.commit()
    return report


def seconds_until_next_business_midnight(now: datetime | None = None) -> float:
    """Return the delay until the next Asia/Shanghai natural day begins."""

    local_now = (now or datetime.now(timezone.utc)).astimezone(BUSINESS_TIMEZONE)
    next_midnight = datetime.combine(
        local_now.date() + timedelta(days=1), time.min, tzinfo=BUSINESS_TIMEZONE
    )
    return max((next_midnight - local_now).total_seconds(), 1.0)


def run_system_maintenance() -> dict[str, dict[str, int]]:
    """Run file retention and daily literacy materialization catch-up."""

    return {
        "files": run_file_maintenance(),
        "literacy_scores": run_literacy_score_maintenance(),
    }
