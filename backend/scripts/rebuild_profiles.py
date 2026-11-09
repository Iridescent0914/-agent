"""Rebuild mastery, literacy scores and review labels for active students."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Student, SchoolClass
from app.services.profile_rebuild import rebuild_student_profile


def selected_students(db, *, student_code=None, class_id=None):
    query = select(Student.id, Student.student_code).where(Student.archived_at.is_(None))
    if student_code:
        student = db.scalar(select(Student).where(Student.student_code == student_code))
        if student is None or student.archived_at is not None:
            raise ValueError("指定学生不存在或已归档；重建仅处理在读学生")
        query = query.where(Student.student_code == student_code)
    if class_id:
        if db.get(SchoolClass, class_id) is None:
            raise ValueError("指定班级不存在")
        query = query.where(Student.class_id == class_id)
    return list(db.execute(query.order_by(Student.student_code, Student.id)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", action="store_true", help="All active students")
    target.add_argument("--student-code")
    target.add_argument("--class-id", type=UUID)
    parser.add_argument("--as-of", help="Calculation cutoff using current revisions, not a historical revision restore")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        as_of = datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(ZoneInfo("Asia/Shanghai"))
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        with SessionLocal() as db:
            students = selected_students(db, student_code=args.student_code, class_id=args.class_id)
        results = []
        for student_id, student_code in students:
            try:
                # Each student holds locks and commits in its own short transaction.
                with SessionLocal() as db:
                    result = rebuild_student_profile(db, student_id, as_of=as_of, dry_run=args.dry_run)
                    if args.dry_run:
                        db.rollback()
                    else:
                        db.commit()
                results.append(result)
            except Exception as exc:
                results.append({"student_id": str(student_id), "student_code": student_code,
                                "status": "failed", "error": str(exc)})
        failures = sum(result["status"] == "failed" for result in results)
        print(json.dumps({"dry_run": args.dry_run, "as_of": as_of.isoformat(),
                          "student_count": len(students), "failed_count": failures,
                          "students": results}, ensure_ascii=False, indent=2))
        return 1 if failures else 0
    except Exception as exc:
        print(f"画像重建失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
