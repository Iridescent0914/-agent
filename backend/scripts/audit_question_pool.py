"""Read-only audit of choice, fill-blank and true/false question data.

Database access is opt-in. Reports go to stdout unless --output is supplied;
this command never changes question, paper or attempt rows.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.answer_units import AUTO_MATCH_TYPES, is_canonical_answer_structure
from app.services.grading.engine import grade_answer

QUESTION_TYPES = {"choice", "fill_blank", "true_false"}
ASSET_RE = re.compile(r"\[ASSET:([^\]]+)\]")


def inspect_question(record: dict[str, Any], *, root: Path = ROOT) -> dict[str, Any]:
    issues: list[str] = []
    answer = record.get("correct_answer")
    items = answer.get("items", []) if isinstance(answer, dict) else []
    if not is_canonical_answer_structure(answer):
        issues.append("invalid_answer_structure")
    if any(not isinstance(item.get("answer"), str) or not item["answer"].strip() for item in items):
        issues.append("missing_answer_units")
    if any(item.get("answer_match_type") not in AUTO_MATCH_TYPES for item in items):
        issues.append("non_automatic_answer_units")
    if "[ASSET:" in json.dumps(answer, ensure_ascii=False):
        issues.append("image_in_structured_answer")
    if not record.get("reference_answer_raw"):
        issues.append("missing_reference_answer")

    manifests: dict[str, dict[str, Any]] = {}
    for chapter in ("rational", "expr"):
        manifest = json.loads((root / "data/web" / chapter / "asset_manifest.json").read_text(encoding="utf-8"))
        entries = manifest.get("assets", []) if isinstance(manifest, dict) else manifest
        manifests.update({entry["source_path"]: entry for entry in entries})
    referenced = set(ASSET_RE.findall(record.get("question_text", "")))
    options = record.get("options")
    referenced.update(ASSET_RE.findall(json.dumps(options, ensure_ascii=False)))
    declared = {asset["path"] for asset in record.get("assets", [])}
    if referenced - declared:
        issues.append("undeclared_body_assets")
    missing_assets = []
    for source in sorted(referenced):
        entry = manifests.get(source)
        if not entry or entry.get("status") != "converted" or not entry.get("web_path") or not (root / "data/web" / entry["web_path"]).is_file():
            missing_assets.append(source)
    if missing_assets:
        issues.append("unavailable_body_assets")

    suspicious = [item["item_id"] for item in items if isinstance(item.get("answer"), str) and (
        len(item["answer"]) > 40 or re.search(r"因为|所以|答案|理由|例如|原式|分别为|应填|【AI", item["answer"])
    )]
    if suspicious:
        issues.append("reference_prose_in_answer")
    self_grade = grade_answer(answer, answer, {
        "answer_match_type": record.get("answer_match_type"),
        "answer_normalization_rule": record.get("answer_normalization_rule", {}),
    }, response_status="answered")
    if self_grade.is_correct is not True:
        issues.append("standard_answer_does_not_self_grade")
    return {
        "original_qid": record["original_qid"],
        "question_type": record["question_type"],
        "grading_readiness": record["grading_readiness"],
        "issues": issues,
        "import_issues": record.get("import_issues", []),
        "governance": record.get("pool_governance"),
        "missing_assets": missing_assets,
        "suspicious_answer_units": suspicious,
        "self_grade": self_grade.details(),
        "question_text": record["question_text"],
        "options": options,
        "reference_answer_raw": record.get("reference_answer_raw"),
        "correct_answer": answer,
    }


def audit(*, database: bool = False, objective_only: bool = False) -> dict[str, Any]:
    records = []
    for chapter in ("rational", "expr"):
        records.extend(json.loads((ROOT / f"data/interim/{chapter}_all.json").read_text(encoding="utf-8")))
    if database:
        from sqlalchemy import select
        from app.core.database import SessionLocal
        from app.models import Question

        with SessionLocal() as db:
            records = [{column.name: getattr(question, column.name) for column in Question.__table__.columns}
                       for question in db.scalars(select(Question).where(Question.archived_at.is_(None)).order_by(Question.original_qid))]
    rows = [inspect_question(record) for record in records if record["question_type"] in QUESTION_TYPES or (not objective_only and record.get("answer_normalization_rule", {}).get("final_answer_only") is True)]
    return {
        "scope": "database" if database else "repository",
        "question_count": len(rows),
        "status_counts": dict(Counter(row["grading_readiness"] for row in rows)),
        "issue_counts_by_status": {status: dict(Counter(issue for row in rows if row["grading_readiness"] == status for issue in row["issues"]))
                                   for status in ("machine_ready", "raw_answer_only")},
        "questions": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", action="store_true")
    parser.add_argument("--objective-only", action="store_true", help="Restrict inspection to choice, fill_blank and true_false")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit(database=args.database, objective_only=args.objective_only)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({key: value for key, value in report.items() if key != "questions"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
