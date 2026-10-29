from __future__ import annotations

import json
import argparse
from pathlib import Path

from app.core.database import SessionLocal
from app.services.seed_importer import import_seed_data


CODE_ROOT = Path(__file__).resolve().parents[2]
CHAPTERS = {
    "rational": {
        "questions": CODE_ROOT / "data" / "seed" / "rational_questions.json",
        "tree": CODE_ROOT / "data" / "seed" / "rational_knowledge_tree.json",
        "catalog": CODE_ROOT / "data" / "interim" / "rational_all.json",
        "catalog_count": 139,
        "min_seed": 30,
    },
    "expr": {
        "questions": CODE_ROOT / "data" / "seed" / "expr_questions.json",
        "tree": CODE_ROOT / "data" / "seed" / "expr_knowledge_tree.json",
        "catalog": CODE_ROOT / "data" / "interim" / "expr_all.json",
        "catalog_count": 41,
        "min_seed": 1,
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import one or more chapter seed datasets")
    parser.add_argument(
        "--chapter",
        choices=["rational", "expr", "all"],
        default="rational",
        help="要导入的章节；默认只导入第一章题库",
    )
    parser.add_argument(
        "--include-legacy-demo",
        action="store_true",
        help=(
            "显式导入阶段一旧版 S001-S005 演示班及三次历史作答；"
            "默认只导入题库和知识点"
        ),
    )
    args = parser.parse_args()
    if args.include_legacy_demo and args.chapter == "expr":
        parser.error("--include-legacy-demo requires --chapter rational or --chapter all")
    chapter_names = list(CHAPTERS) if args.chapter == "all" else [args.chapter]
    output = {}
    with SessionLocal() as session:
        for chapter_name in chapter_names:
            config = CHAPTERS[chapter_name]
            stats = import_seed_data(
                session,
                config["questions"],
                config["tree"],
                config["catalog"],
                min_seed_questions=config["min_seed"],
                expected_catalog_count=config["catalog_count"],
                include_demo_students=(
                    args.include_legacy_demo and chapter_name == "rational"
                ),
                include_demo_history=(
                    args.include_legacy_demo and chapter_name == "rational"
                ),
            )
            output[chapter_name] = stats.as_dict()
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
