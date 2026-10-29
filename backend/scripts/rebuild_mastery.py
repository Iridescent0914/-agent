from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Student
from app.services.knowledge_status import rebuild_knowledge_statuses
from app.services.student_profile import load_context, persist_mastery


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild M2 mastery from current confirmed attempts")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", action="store_true")
    target.add_argument("--student-code")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        with SessionLocal() as db:
            query = select(Student).order_by(Student.student_code).with_for_update()
            if args.student_code:
                query = query.where(Student.student_code == args.student_code)
            students = db.scalars(query).all()
            if not students:
                raise ValueError("No matching students")
            report = {}
            for student in students:
                context = load_context(db, student.id)
                report[student.student_code] = {
                    "mastery": persist_mastery(db, context),
                    "knowledge_status": rebuild_knowledge_statuses(db, student.id),
                }
            if args.dry_run:
                db.rollback()
            else:
                db.commit()
            print(json.dumps({"dry_run": args.dry_run, "students": report}, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"Rebuild failed, transaction rolled back: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
