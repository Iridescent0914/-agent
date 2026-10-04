from __future__ import annotations

import argparse
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import RecognitionRun


def main() -> int:
    parser = argparse.ArgumentParser(description="Purge expired M6 images; dry-run by default")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    settings = get_settings()
    with SessionLocal() as db:
        runs = list(db.scalars(select(RecognitionRun).where(
            RecognitionRun.retention_until.is_not(None),
            RecognitionRun.retention_until < now,
        )))
        removed = 0
        for run in runs:
            path = settings.recognition_upload_dir / run.image_storage_key
            print(f"{'DELETE' if args.confirm else 'DRY-RUN'} {run.id} {path.name}")
            if args.confirm and path.is_file():
                path.unlink()
                removed += 1
        if args.confirm:
            db.commit()
    print({"expired": len(runs), "removed": removed, "dry_run": not args.confirm})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
