from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.database import SessionLocal
from app.services.current_demo import load_demo_config, prepare_current_demo


CODE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = CODE_ROOT / "data" / "seed" / "current_demo_scenario.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create the idempotent demo class and current stage-two workflow data",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    config = load_demo_config(args.config)
    with SessionLocal() as db:
        try:
            result = prepare_current_demo(db, config)
            db.commit()
        except Exception:
            db.rollback()
            raise
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
