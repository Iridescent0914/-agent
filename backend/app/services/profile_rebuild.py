"""Inspectable, per-student atomic rebuilding of all profile materializations."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Student, StudentMastery, StudentLiteracyScore, StudentKnowledgeStatus
from app.services.profile_refresh import refresh_student_profiles


def materialized_profile(db: Session, student_id: UUID) -> dict:
    return {
        "mastery": {
            str(row.knowledge_point_id): {
                "score": str(row.mastery), "attempt_count": row.attempt_count,
                "correct_count": row.correct_count, "recent_wrong_count": row.recent_wrong_count,
                "confidence_level": row.confidence_level,
                "last_practiced_at": row.last_practiced_at.isoformat() if row.last_practiced_at else None,
            }
            for row in db.scalars(select(StudentMastery).where(StudentMastery.student_id == student_id))
        },
        "literacy": {
            row.literacy_name: {
                "score": str(row.score), "attempt_count": row.attempt_count,
                "as_of_date": row.score_as_of_date.isoformat(), "rule_version": row.rule_version,
                "last_practiced_at": row.last_practiced_at.isoformat() if row.last_practiced_at else None,
            }
            for row in db.scalars(select(StudentLiteracyScore).where(StudentLiteracyScore.student_id == student_id))
        },
        "knowledge_status": {
            str(row.knowledge_point_id): {
                "status": row.status, "attempt_id": str(row.source_attempt_id),
                "revision_no": row.source_revision_no,
            }
            for row in db.scalars(select(StudentKnowledgeStatus).where(StudentKnowledgeStatus.student_id == student_id))
        },
    }


def rebuild_student_profile(db: Session, student_id: UUID, *, as_of: datetime, dry_run: bool) -> dict:
    transaction = db.begin_nested()
    try:
        student = db.scalar(select(Student).where(Student.id == student_id).with_for_update())
        if student is None or student.archived_at is not None:
            raise ValueError("学生不存在或已归档；重建仅处理在读学生")
        before = materialized_profile(db, student_id)
        stats = refresh_student_profiles(db, student_id, as_of=as_of)
        after = materialized_profile(db, student_id)
        report = {
            "student_id": str(student_id), "student_code": student.student_code,
            "status": "ok", "stats": stats,
            "changed_components": [name for name in before if before[name] != after[name]],
            "before": before, "after": after,
        }
        if dry_run:
            transaction.rollback()
        else:
            transaction.commit()
        return report
    except Exception:
        transaction.rollback()
        raise
