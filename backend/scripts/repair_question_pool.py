"""Preview or apply source-checked question-pool corrections.

Configured choice/fill-blank/true-false questions can enter the pool. Explicit
manual_reference corrections only repair calculation/solution references.
The final_answer_only mode can opt a reviewed calculation question into
deterministic final-result grading without evaluating its working steps.
Database
updates use optimistic comparison, append full before/after audit events, and
refuse to overwrite teacher reviews. Published paper snapshots are untouched.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.question_pool_corrections import VERSION, apply_pool_corrections

CONFIG_PATH = ROOT / "data/config/question_pool_corrections.json"
DATABASE_FIELDS = ("question_text", "assets", "options", "reference_answer_raw", "solution", "correct_answer",
                   "answer_match_type", "answer_normalization_rule", "grading_readiness", "import_issues")


def write_json(path: Path, value: object) -> None:
    rendered = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == rendered:
        return
    newline = "\r\n" if path.exists() and b"\r\n" in path.read_bytes() else "\n"
    path.write_text(rendered, encoding="utf-8", newline=newline)


def repair_files(*, apply: bool) -> dict:
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_rational_m0 as builder

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["questions"]
    output = {}
    for chapter in ("rational", "expr"):
        path = ROOT / f"data/interim/{chapter}_all.json"
        records = json.loads(path.read_text(encoding="utf-8"))
        before = deepcopy(records)
        refresh_ids = {record["original_qid"] for record in records if config.get(record["original_qid"], {}).get("refresh_source_text")}
        source_questions = {}
        if refresh_ids:
            source = ROOT / records[0]["source_file"]
            source_questions = {question.metadata["original_qid"]: question for question in builder.parse_review_docx(source)}
        for record in records:
            if record["original_qid"] in refresh_ids:
                parsed = source_questions[record["original_qid"]]
                text = "\n".join(parsed.fields["question_text"])
                mapping = {asset["relationship_id"]: asset["path"] for asset in record["assets"]}
                record["question_text"] = builder.replace_asset_markers(text, mapping)
        apply_pool_corrections(records, CONFIG_PATH)
        changed = [record["original_qid"] for record, original in zip(records, before) if record != original]
        promoted = [record["original_qid"] for record, original in zip(records, before) if record["grading_readiness"] == "machine_ready" and original["grading_readiness"] != "machine_ready"]
        demoted = [record["original_qid"] for record, original in zip(records, before) if record["grading_readiness"] != "machine_ready" and original["grading_readiness"] == "machine_ready"]
        # The seed is a bounded sample; the importer imports the full catalog,
        # so every ready catalog question enters the pool regardless of this cap.
        seed = builder.select_diverse(records, limit=50)
        if apply:
            write_json(path, records)
            write_json(ROOT / f"data/seed/{chapter}_questions.json", seed)
            builder.write_audit_csv(ROOT / f"data/audit/{chapter}_import_audit.csv", records)
            report_path = ROOT / f"data/audit/{chapter}_m0_report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report.update({
                "seed_candidate_count": len(seed),
                "eligible_count_before_limit": sum(builder.eligible_for_seed(record) for record in records),
                "seed_type_counts": dict(Counter(record["question_type"] for record in seed)),
                "seed_knowledge_point_count": len({record["main_knowledge_point"]["id"] for record in seed}),
                "seed_knowledge_point_counts": dict(Counter(record["main_knowledge_point"]["name"] for record in seed)),
                "seed_machine_ready_count": len(seed),
                "catalog_machine_ready_count": sum(record["grading_readiness"] == "machine_ready" for record in records),
                "answer_unit_unresolved_count": sum(item.get("answer") is None for record in records for item in record["correct_answer"]["items"]),
                "answer_unit_manual_count": sum(item.get("answer_match_type") == "manual" for record in records for item in record["correct_answer"]["items"]),
                "issue_counts": dict(Counter(issue for record in records for issue in record["import_issues"])),
                "pool_governance_version": VERSION,
            })
            write_json(report_path, report)
        output[chapter] = {"changed": changed, "promoted": promoted, "demoted": demoted, "seed_count": len(seed)}
    if apply:
        unresolved_questions = []
        total_units = total_records = 0
        for directory, filename in (("interim", "rational_all"), ("seed", "rational_questions"), ("interim", "expr_all"), ("seed", "expr_questions")):
            path = ROOT / f"data/{directory}/{filename}.json"
            for record in json.loads(path.read_text(encoding="utf-8")):
                total_records += 1
                items = record["correct_answer"]["items"]
                total_units += len(items)
                unresolved_ids = [item["item_id"] for item in items if item.get("answer") is None]
                if unresolved_ids:
                    unresolved_questions.append({"file": path.relative_to(ROOT).as_posix(), "original_qid": record["original_qid"], "question_type": record["question_type"], "unresolved_item_ids": unresolved_ids})
        report_path = ROOT / "data/audit/answer_unit_normalization_report.json"
        unit_report = {
            "schema_version": "answer-units-v1", "scope": "repository_catalog_files",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "question_record_count": total_records, "answer_unit_count": total_units,
            "unresolved_question_count": len(unresolved_questions),
            "unresolved_item_count": sum(len(item["unresolved_item_ids"]) for item in unresolved_questions),
            "unresolved_questions": unresolved_questions,
        }
        if report_path.exists():
            previous = json.loads(report_path.read_text(encoding="utf-8"))
            if {key: value for key, value in previous.items() if key != "generated_at"} == {key: value for key, value in unit_report.items() if key != "generated_at"}:
                unit_report["generated_at"] = previous["generated_at"]
        write_json(report_path, unit_report)
    return output


def repair_database(*, apply: bool, question_ids: list[str] | None = None) -> dict:
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models import Question

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["questions"]
    if question_ids:
        unknown = set(question_ids) - set(config)
        if unknown:
            raise ValueError(f"Unknown correction IDs: {sorted(unknown)}")
        config = {qid: config[qid] for qid in dict.fromkeys(question_ids)}
    desired = {record["original_qid"]: record for chapter in ("rational", "expr")
               for record in json.loads((ROOT / f"data/interim/{chapter}_all.json").read_text(encoding="utf-8")) if record["original_qid"] in config}
    missing = set(config) - set(desired)
    if missing:
        raise ValueError(f"Correction IDs missing from catalog: {sorted(missing)}")
    changes, conflicts = [], []
    with SessionLocal() as db:
        def business_fingerprints():
            result = {}
            for name in ("paper_instances", "paper_instance_items", "submissions", "attempts", "attempt_revisions", "student_masteries", "student_literacy_scores", "student_knowledge_statuses"):
                table = Question.metadata.tables[name]
                data = [dict(row) for row in db.execute(select(table).order_by(*table.primary_key.columns)).mappings()]
                result[name] = {"count": len(data), "sha256": hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()}
            return result

        business_before = business_fingerprints()
        rows = {question.original_qid: question for question in db.scalars(select(Question).where(Question.original_qid.in_(desired)).order_by(Question.original_qid).with_for_update())}
        if set(rows) != set(desired):
            raise ValueError(f"Database questions missing: {sorted(set(desired) - set(rows))}")
        for qid, record in desired.items():
            question = rows[qid]
            if question.archived_at is not None:
                conflicts.append({"original_qid": qid, "reason": "archived"})
                continue
            before = {field: getattr(question, field) for field in DATABASE_FIELDS}
            after = {field: record.get(field) for field in DATABASE_FIELDS}
            if before == after:
                continue
            history = question.review_history or []
            if any(event.get("type") not in {"answer_structure_normalized", "pool_governance_applied"} for event in history):
                conflicts.append({"original_qid": qid, "reason": "teacher_review_exists"})
                continue
            changes.append({"original_qid": qid, "before_readiness": before["grading_readiness"], "after_readiness": after["grading_readiness"],
                            "changed_fields": [field for field in DATABASE_FIELDS if before[field] != after[field]]})
            if apply:
                at = datetime.now(timezone.utc)
                revision = question.review_revision
                for field, value in after.items():
                    setattr(question, field, value)
                question.review_revision = revision + 1
                question.reviewed_by = VERSION
                question.reviewed_at = at
                question.review_history = [*history, {
                    "event_id": str(uuid4()), "type": "pool_governance_applied", "at": at.isoformat(),
                    "reviewer": VERSION, "note": config[qid]["note"],
                    "before_revision": revision, "after_revision": revision + 1,
                    "before": before, "after": after,
                }]
        if apply:
            if conflicts:
                raise ValueError(f"Database conflicts; transaction rolled back: {conflicts}")
            db.flush()
            business_after = business_fingerprints()
            if business_before != business_after:
                raise ValueError("Business data changed during governance; transaction rolled back")
            db.commit()
        else:
            db.rollback()
    return {"changes": changes, "conflicts": conflicts, "applied": apply, "business_integrity": business_before}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply-files", action="store_true")
    parser.add_argument("--database", action="store_true")
    parser.add_argument("--apply-database", action="store_true")
    parser.add_argument("--question-id", action="append", help="Limit database repair to selected correction IDs; may be repeated")
    args = parser.parse_args()
    if args.question_id and not (args.database or args.apply_database):
        parser.error("--question-id requires --database or --apply-database")
    result = repair_database(apply=args.apply_database, question_ids=args.question_id) if args.database or args.apply_database else repair_files(apply=args.apply_files)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
