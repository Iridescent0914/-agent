from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import distinct, func, select, text

from app.core.database import SessionLocal
from app.models import KnowledgePoint, Question


CODE_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = CODE_ROOT / "data" / "web" / "rational" / "asset_manifest.json"
CHAPTER_CODE = "math.g6.expr"


def main() -> int:
    errors: list[str] = []
    with SessionLocal() as session:
        migration = session.scalar(text("SELECT version_num FROM alembic_version"))
        questions = list(
            session.scalars(
                select(Question).where(Question.chapter_code == CHAPTER_CODE)
            )
        )
        knowledge_points = session.scalar(
            select(func.count(KnowledgePoint.id)).where(
                KnowledgePoint.chapter_code == CHAPTER_CODE
            )
        ) or 0
        used_knowledge_points = session.scalar(
            select(func.count(distinct(Question.main_knowledge_point_id))).where(
                Question.chapter_code == CHAPTER_CODE
            )
        ) or 0

    qids = [question.original_qid for question in questions]
    machine_ready = sum(question.grading_readiness == "machine_ready" for question in questions)
    required = sum(bool(question.is_required) for question in questions)
    if migration != "0023":
        errors.append(f"migration is {migration}, expected 0023")
    if len(questions) != 41:
        errors.append(f"expr questions: expected 41, got {len(questions)}")
    if len(set(qids)) != len(qids):
        errors.append("expr original_qid values are not unique")
    if knowledge_points != 8:
        errors.append(f"expr knowledge points: expected 8, got {knowledge_points}")
    if used_knowledge_points != 6:
        errors.append(f"expr used knowledge points: expected 6, got {used_knowledge_points}")
    if machine_ready != 9:
        errors.append(f"expr machine_ready questions: expected 9, got {machine_ready}")
    if required != 35:
        errors.append(f"expr required questions: expected 35, got {required}")
    if any(question.source_review_status != "reviewed" for question in questions):
        errors.append("expr questions contain non-reviewed source records")

    manifest = {}
    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if int(manifest.get("failed_count", -1)) != 0:
        errors.append(f"web asset conversion failures: {manifest.get('failed_count')}")

    output = {
        "schema_at_head": migration == "0023",
        "chapter_code": CHAPTER_CODE,
        "questions": len(questions),
        "knowledge_points": knowledge_points,
        "used_knowledge_points": used_knowledge_points,
        "machine_ready": machine_ready,
        "required_questions": required,
        "web_asset_failures": manifest.get("failed_count", -1),
        "validation_errors": len(errors),
        "errors": errors,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
