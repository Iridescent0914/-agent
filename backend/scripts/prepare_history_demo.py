from __future__ import annotations

import argparse
import json

from app.core.database import SessionLocal
from app.services.history_demo import (
    DEFAULT_CLASS_NAME, DEFAULT_SEED_PREFIX, prepare_history_demo,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare completed assignments for testing history review",
    )
    parser.add_argument("--class-name", default=DEFAULT_CLASS_NAME)
    parser.add_argument("--seed-prefix", default=DEFAULT_SEED_PREFIX)
    args = parser.parse_args()

    with SessionLocal() as db:
        try:
            assignments, created_count = prepare_history_demo(
                db,
                class_name=args.class_name,
                seed_prefix=args.seed_prefix,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

    print(json.dumps({
        "class_name": args.class_name,
        "created_count": created_count,
        "assignments": assignments,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
