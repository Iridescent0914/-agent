"""Preview/apply shared question reviews and source-pool corrections, not business data."""
import argparse
import json

from app.core.database import SessionLocal
from app.services.question_catalog_sync import synchronize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="无冲突时备份并提交；默认只读预览")
    args = parser.parse_args()
    with SessionLocal() as db:
        report = synchronize(db, apply=args.apply)
        if report["applied"]:
            db.commit()
        else:
            db.rollback()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["conflicts"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
