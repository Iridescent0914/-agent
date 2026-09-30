from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.database import SessionLocal
from app.services.demo_scenarios import prepare_m5_scenario, validate_m5_scenario


CODE_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = CODE_ROOT / "data" / "seed" / "m5_demo_scenarios.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare or verify a deterministic M5 demo scenario")
    parser.add_argument("--scenario", choices=["m5_start", "m5_showcase"], required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    with SessionLocal() as db:
        try:
            summary, created = prepare_m5_scenario(
                db, config, args.scenario, check_only=args.check_only
            )
            errors = validate_m5_scenario(db, config, args.scenario)
            if errors:
                raise RuntimeError("; ".join(errors))
            if not args.check_only:
                db.commit()
        except Exception:
            db.rollback()
            raise
    print(json.dumps({
        "scenario": args.scenario,
        "created": created,
        "validation_errors": 0,
        **summary,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
