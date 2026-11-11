"""Audit or apply the nine-dimension score rebuild without relabeling history."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from decimal import Decimal
import json
import hashlib
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from app.core.database import SessionLocal
from app.models import PaperInstanceItem, Student, StudentLiteracyScore
from app.services.literacy_catalog import LITERACY_DIMENSIONS
from app.services.literacy_rules import DEFAULT_SCORE
from app.services.literacy_scoring import (
    RULE_VERSION, load_literacy_evidence_many, persist_literacy_scores,
)


def score_rows(db, student_ids):
    grouped = {student_id: [] for student_id in student_ids}
    for row in db.scalars(select(StudentLiteracyScore).where(
        StudentLiteracyScore.student_id.in_(student_ids)
    ).order_by(StudentLiteracyScore.student_id, StudentLiteracyScore.literacy_name)):
        grouped[row.student_id].append(row)
    return grouped


def snapshot_scores(rows):
    return {
        row.literacy_name: {
            "score": str(row.score), "attempt_count": row.attempt_count,
            "last_practiced_at": row.last_practiced_at.isoformat() if row.last_practiced_at else None,
            "score_as_of_date": row.score_as_of_date.isoformat(), "rule_version": row.rule_version,
        }
        for row in rows
    }


def business_fingerprints(db):
    result = {}
    for table in (
        "assignments", "paper_instances", "paper_instance_items", "submissions",
        "attempts", "attempt_revisions", "student_masteries", "student_knowledge_statuses",
    ):
        rows = sorted(json.dumps(row, ensure_ascii=False, sort_keys=True)
                      for row in db.execute(text(f'SELECT to_jsonb(t) FROM "{table}" AS t')).scalars())
        result[table] = {"count": len(rows), "sha256": hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()}
    return result


def transition(db, *, as_of: datetime, apply: bool) -> dict:
    fingerprints = business_fingerprints(db)
    query = select(Student).where(Student.archived_at.is_(None)).order_by(Student.id)
    if apply:
        query = query.with_for_update()
    students = list(db.scalars(query))
    ids = [student.id for student in students]
    rows = score_rows(db, ids)
    evidence = load_literacy_evidence_many(db, ids, as_of=as_of)
    labels = Counter(
        (snapshot or {}).get("core_literacy")
        for snapshot in db.scalars(select(PaperInstanceItem.question_snapshot))
    )
    reports = []
    for student in students:
        blocked_scores = [
            row.literacy_name for row in rows[student.id]
            if row.literacy_name not in LITERACY_DIMENSIONS
            and (row.attempt_count != 0 or row.score != Decimal(str(DEFAULT_SCORE))
                 or row.last_practiced_at is not None)
        ]
        invalid = evidence[student.id].invalid
        reports.append({
            "student_id": str(student.id), "student_code": student.student_code,
            "before": snapshot_scores(rows[student.id]),
            "blocking_legacy_scores": blocked_scores,
            "blocking_evidence": [
                {"attempt_id": str(item.attempt_id), "revision_no": item.revision_no, "reason": item.reason}
                for item in invalid
            ],
        })
    blocked = sum(bool(item["blocking_legacy_scores"] or item["blocking_evidence"]) for item in reports)
    report = {
        "apply": apply, "as_of": as_of.isoformat(), "rule_version": RULE_VERSION,
        "dimensions": list(LITERACY_DIMENSIONS), "student_count": len(students),
        "blocked_student_count": blocked,
        "legacy_snapshot_labels": {
            label: count for label, count in labels.items()
            if isinstance(label, str) and "数据意识" in label
        },
        "students": reports, "applied": False,
        "protected_tables": fingerprints,
    }
    if blocked:
        return report
    # The caller controls commit/rollback; a failed student rolls back the batch.
    for student, item in zip(students, reports):
        item["stats"], _ = persist_literacy_scores(db, student.id, as_of=as_of)
    after = score_rows(db, ids)
    for student, item in zip(students, reports):
        item["after"] = snapshot_scores(after[student.id])
    if business_fingerprints(db) != fingerprints:
        raise RuntimeError("Score transition changed protected teaching records; rolling back")
    report["protected_tables_unchanged"] = True
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit after auditing every active student; default is dry-run")
    parser.add_argument("--as-of", help="ISO cutoff using current final revisions")
    parser.add_argument("--output", type=Path, help="Save the full audit to a private report file")
    args = parser.parse_args(argv)
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(ZoneInfo("Asia/Shanghai"))
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    with SessionLocal() as db:
        report = transition(db, as_of=as_of, apply=args.apply)
        if args.apply and not report["blocked_student_count"]:
            db.commit()
            report["applied"] = True
        else:
            db.rollback()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    summary = {key: value for key, value in report.items() if key not in ("students", "protected_tables")} if args.output else report
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if report["blocked_student_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
