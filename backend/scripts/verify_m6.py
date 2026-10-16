from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import Attempt, RecognitionItem, RecognitionRun, Submission


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only M6 validation")
    parser.add_argument("--require-image-scenario", action="store_true")
    args = parser.parse_args()
    errors: list[str] = []
    warnings: list[str] = []
    settings = get_settings()
    with SessionLocal() as db:
        migration = db.scalar(text("SELECT version_num FROM alembic_version"))
        if migration != "0022":
            errors.append(f"migration is {migration}, expected 0022")
        runs = list(db.scalars(select(RecognitionRun)))
        now = datetime.now(timezone.utc)
        run_ids = {run.id for run in runs}
        items = list(db.scalars(select(RecognitionItem)))
        image_submissions = list(db.scalars(select(Submission).where(Submission.source == "image")))
        submission_ids = {row.id for row in image_submissions}
        committed_real_runs = [
            run for run in runs
            if run.status == "committed"
            and run.submission_id in submission_ids
            and run.provider.strip().lower() != "fake"
        ]
        attempts = list(db.scalars(select(Attempt).where(Attempt.submission_id.in_(submission_ids)))) if submission_ids else []
        pending_submission_ids = {
            attempt.submission_id
            for attempt in attempts
            if attempt.grade_status == "needs_review"
        }
        duplicate_items = db.execute(
            select(RecognitionItem.recognition_run_id, RecognitionItem.order_no, func.count())
            .group_by(RecognitionItem.recognition_run_id, RecognitionItem.order_no)
            .having(func.count() > 1)
        ).all()
        ownership_errors = sum(
            item.recognition_run_id not in run_ids for item in items
        )
        link_errors = sum(
            run.status == "committed" and run.submission_id not in submission_ids
            for run in runs
        )
        active_missing_files = sum(
            not (settings.recognition_upload_dir / run.image_storage_key).is_file()
            for run in runs
            if run.status != "discarded"
            and (
                run.retention_until is None
                or run.retention_until >= now
                or run.status == "ready_for_review"
                or run.submission_id in pending_submission_ids
            )
        )
        expired_missing_files = sum(
            not (settings.recognition_upload_dir / run.image_storage_key).is_file()
            for run in runs
            if run.status != "discarded"
            and run.retention_until is not None
            and run.retention_until < now
            and run.status != "ready_for_review"
            and run.submission_id not in pending_submission_ids
        )
        confirmed_without_revision = sum(
            attempt.grade_status == "teacher_confirmed" and attempt.revision_no < 2
            for attempt in attempts
        )
        if args.require_image_scenario and not committed_real_runs:
            errors.append("no committed real-provider image submission exists")
        elif not image_submissions:
            warnings.append("当前数据库尚未准备 M6 图片演示场景")
        elif not committed_real_runs:
            warnings.append("当前数据库只有 fake 图片场景，不能用于 M6 真实识别验收")
        for name, count in {
            "duplicate recognition items": len(duplicate_items),
            "run item ownership errors": ownership_errors,
            "submission run link errors": link_errors,
            "unexpired missing image files": active_missing_files,
            "confirmed image attempts without review revision": confirmed_without_revision,
        }.items():
            if count:
                errors.append(f"{name}: {count}")
        output = {
            "schema_head": migration,
            "recognition_run_count": len(runs),
            "ready_run_count": sum(run.status == "ready_for_review" for run in runs),
            "failed_run_count": sum(run.status == "failed" for run in runs),
            "committed_run_count": sum(run.status == "committed" for run in runs),
            "committed_real_run_count": len(committed_real_runs),
            "image_submission_count": len(image_submissions),
            "image_attempt_count": len(attempts),
            "pending_image_attempt_count": sum(a.grade_status == "needs_review" for a in attempts),
            "confirmed_image_attempt_count": sum(a.grade_status == "teacher_confirmed" for a in attempts),
            "active_missing_image_files": active_missing_files,
            "expired_purged_image_files": expired_missing_files,
            "validation_errors": len(errors),
            "warnings": warnings,
            "errors": errors,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
