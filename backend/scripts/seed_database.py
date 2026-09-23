from __future__ import annotations

import json
from pathlib import Path

from app.core.database import SessionLocal
from app.services.seed_importer import import_seed_data


CODE_ROOT = Path(__file__).resolve().parents[2]
QUESTIONS_PATH = CODE_ROOT / "data" / "seed" / "rational_questions.json"
KNOWLEDGE_TREE_PATH = CODE_ROOT / "data" / "seed" / "rational_knowledge_tree.json"


def main() -> None:
    with SessionLocal() as session:
        stats = import_seed_data(session, QUESTIONS_PATH, KNOWLEDGE_TREE_PATH)
    print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
