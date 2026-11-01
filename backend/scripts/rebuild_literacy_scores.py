from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Student
from app.services.literacy_scoring import persist_literacy_scores


BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _as_of(value: str | None) -> datetime:
    if value is None:
        return datetime.now(BUSINESS_TIMEZONE)
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=BUSINESS_TIMEZONE) if parsed.tzinfo is None else parsed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild dynamic literacy scores from current confirmed attempts"
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", action="store_true")
    target.add_argument("--student-code")
    parser.add_argument("--as-of", help="ISO datetime; naive values use Asia/Shanghai")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        calculated_at = _as_of(args.as_of)
        with SessionLocal() as db:
            query = (
                select(Student)
                .where(Student.archived_at.is_(None))
                .order_by(Student.student_code)
                .with_for_update()
            )
            if args.student_code:
                query = query.where(Student.student_code == args.student_code)
            students = list(db.scalars(query))
            if not students:
                raise ValueError("No matching students")
            report = {}
            for student in students:
                stats, result = persist_literacy_scores(
                    db, student.id, as_of=calculated_at
                )
                report[student.student_code] = {
                    "stats": stats,
                    "as_of_date": result.as_of_date.isoformat(),
                    "evidence_count": result.evidence_count,
                    "scores": {
                        name: round(state.score, 4)
                        for name, state in result.states.items()
                    },
                }
            if args.dry_run:
                db.rollback()
            else:
                db.commit()
            print(json.dumps({
                "dry_run": args.dry_run,
                "calculated_at": calculated_at.isoformat(),
                "students": report,
            }, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"Rebuild failed, transaction rolled back: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
