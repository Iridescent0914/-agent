"""Normalize repository and database question answers to subquestion/blank units."""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.answer_units import (
    ANSWER_SCHEMA_VERSION,
    AnswerNormalization,
    normalize_answer_structure,
)


CODE_ROOT = BACKEND_ROOT.parent
DATA_FILES = (
    CODE_ROOT / "data" / "interim" / "rational_all.json",
    CODE_ROOT / "data" / "interim" / "expr_all.json",
    CODE_ROOT / "data" / "seed" / "rational_questions.json",
    CODE_ROOT / "data" / "seed" / "expr_questions.json",
)
ANSWER_CONFIG_FILE = CODE_ROOT / "data" / "config" / "rational_answer_structures.json"
AUDIT_REPORT_FILE = CODE_ROOT / "data" / "audit" / "answer_unit_normalization_report.json"


def _rules(current: Any) -> dict[str, Any]:
    result = dict(current) if isinstance(current, dict) else {}
    result.update({
        "answer_schema_version": ANSWER_SCHEMA_VERSION,
        "item_order_matters": True,
        "partial_credit": True,
    })
    return result


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _normalize_values(values: dict[str, Any]) -> tuple[dict[str, Any], AnswerNormalization]:
    normalized = normalize_answer_structure(
        question_type=str(values.get("question_type") or "unknown"),
        question_text=str(values.get("question_text") or ""),
        correct_answer=values.get("correct_answer"),
        answer_match_type=values.get("answer_match_type"),
        reference_answer_raw=str(values.get("reference_answer_raw") or ""),
    )
    updates = {
        "correct_answer": normalized.answer,
        "answer_match_type": "group",
        "answer_normalization_rule": _rules(
            values.get("answer_normalization_rule")
        ),
    }
    if normalized.manual_item_count or normalized.unresolved_item_count:
        updates["grading_readiness"] = "raw_answer_only"
    return updates, normalized


def normalize_files(*, write: bool) -> dict[str, Any]:
    output: dict[str, Any] = {}
    normalized_records: dict[Path, list[dict[str, Any]]] = {}
    unresolved_questions: list[dict[str, Any]] = []
    total_units = 0
    for path in DATA_FILES:
        records = json.loads(path.read_text(encoding="utf-8"))
        normalized_records[path] = records
        changed = 0
        split = 0
        accepted = 0
        manual = 0
        unresolved = 0
        for record in records:
            updates, result = _normalize_values(record)
            if any(record.get(key) != value for key, value in updates.items()):
                changed += 1
                record.update(updates)
                record["answer_structure_source"] = "normalized_answer_units_v1"
                issues = list(record.get("import_issues") or [])
                marker = "answer_units_need_review"
                if result.unresolved_item_count and marker not in issues:
                    issues.append(marker)
                elif not result.unresolved_item_count:
                    issues = [issue for issue in issues if issue != marker]
                record["import_issues"] = issues
            split += result.split_item_count
            accepted += result.accepted_answer_item_count
            manual += result.manual_item_count
            unresolved += result.unresolved_item_count
            items = result.answer["items"]
            total_units += len(items)
            unresolved_ids = [
                item["item_id"] for item in items if item.get("answer") is None
            ]
            if unresolved_ids:
                unresolved_questions.append({
                    "file": path.relative_to(CODE_ROOT).as_posix(),
                    "original_qid": record.get("original_qid"),
                    "question_type": record.get("question_type"),
                    "unresolved_item_ids": unresolved_ids,
                })
        if write:
            _write_json(path, records)
        output[path.relative_to(CODE_ROOT).as_posix()] = {
            "total": len(records),
            "changed": changed,
            "split_extra_items": split,
            "accepted_answer_items": accepted,
            "manual_answer_items": manual,
            "unresolved_items": unresolved,
            "written": write,
        }

    # Keep the source-derived override file on the same contract as generated
    # catalog files so future builds no longer reintroduce legacy arrays/sets.
    config = json.loads(ANSWER_CONFIG_FILE.read_text(encoding="utf-8-sig"))
    rational_by_qid = {
        record["original_qid"]: record
        for record in normalized_records[DATA_FILES[0]]
    }
    config_changed = 0
    normalized_overrides: dict[str, Any] = {}
    for qid, old_override in config.get("questions", {}).items():
        record = rational_by_qid[qid]
        new_override = {
            "schema_version": ANSWER_SCHEMA_VERSION,
            "items": deepcopy(record["correct_answer"]["items"]),
        }
        if old_override.get("drop_unknown_assets"):
            new_override["drop_unknown_assets"] = True
        normalized_overrides[qid] = new_override
        if old_override != new_override:
            config_changed += 1
    normalized_config = {
        "schema_version": ANSWER_SCHEMA_VERSION,
        "description": "校本审核答案的确定性小题-空判分单元；可接受别名存于 accepted_answers。",
        "questions": normalized_overrides,
    }
    if write:
        _write_json(ANSWER_CONFIG_FILE, normalized_config)
    output[ANSWER_CONFIG_FILE.relative_to(CODE_ROOT).as_posix()] = {
        "total": len(normalized_overrides),
        "changed": config_changed,
        "written": write,
    }

    audit_report = {
        "schema_version": ANSWER_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "repository_catalog_files",
        "question_record_count": sum(
            len(normalized_records[path]) for path in DATA_FILES
        ),
        "answer_unit_count": total_units,
        "unresolved_question_count": len(unresolved_questions),
        "unresolved_item_count": sum(
            len(item["unresolved_item_ids"]) for item in unresolved_questions
        ),
        "unresolved_questions": unresolved_questions,
        "note": "未解决项保留为空并退出自动判分池，需依据原卷人工审核；未猜测答案。",
    }
    if write:
        _write_json(AUDIT_REPORT_FILE, audit_report)
    output["audit_report"] = {
        "path": AUDIT_REPORT_FILE.relative_to(CODE_ROOT).as_posix(),
        "unresolved_question_count": audit_report["unresolved_question_count"],
        "unresolved_item_count": audit_report["unresolved_item_count"],
        "written": write,
    }
    return output


def normalize_database(*, apply: bool) -> dict[str, Any]:
    # Keep file-only normalization runnable from the host even when backend
    # dependencies are available only inside the application container.
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models import Question

    now = datetime.now(timezone.utc)
    by_type: Counter[str] = Counter()
    changed = 0
    split = 0
    accepted = 0
    manual = 0
    unresolved = 0
    downgraded = 0
    downgraded_questions: list[str] = []
    unresolved_questions: list[dict[str, Any]] = []
    with SessionLocal() as session:
        questions = list(session.scalars(
            select(Question)
            .where(Question.archived_at.is_(None))
            .order_by(Question.original_qid)
            .with_for_update()
        ))
        for question in questions:
            updates, result = _normalize_values({
                "question_type": question.question_type,
                "question_text": question.question_text,
                "correct_answer": question.correct_answer,
                "answer_match_type": question.answer_match_type,
                "answer_normalization_rule": question.answer_normalization_rule,
                "reference_answer_raw": question.reference_answer_raw,
                "grading_readiness": question.grading_readiness,
            })
            split += result.split_item_count
            accepted += result.accepted_answer_item_count
            manual += result.manual_item_count
            unresolved += result.unresolved_item_count
            unresolved_ids = [
                item["item_id"]
                for item in result.answer["items"]
                if item.get("answer") is None
            ]
            if unresolved_ids:
                unresolved_questions.append({
                    "original_qid": question.original_qid,
                    "question_type": question.question_type,
                    "unresolved_item_ids": unresolved_ids,
                })
            values_changed = any(
                getattr(question, key) != value for key, value in updates.items()
            )
            if not values_changed:
                continue
            changed += 1
            by_type[question.question_type] += 1
            if (
                question.grading_readiness == "machine_ready"
                and updates.get("grading_readiness") == "raw_answer_only"
            ):
                downgraded += 1
                downgraded_questions.append(question.original_qid)
            before_revision = question.review_revision
            before_answer = question.correct_answer
            for key, value in updates.items():
                setattr(question, key, value)
            issues = list(question.import_issues or [])
            marker = "answer_units_need_review"
            if result.unresolved_item_count and marker not in issues:
                issues.append(marker)
            elif not result.unresolved_item_count:
                issues = [issue for issue in issues if issue != marker]
            question.import_issues = issues
            question.review_revision += 1
            question.reviewed_by = "system_answer_units_v1"
            question.reviewed_at = now
            history = list(question.review_history or [])
            history.append({
                "event_id": str(uuid4()),
                "type": "answer_structure_normalized",
                "at": now.isoformat(),
                "reviewer": "system_answer_units_v1",
                "note": "统一为小题-空答案单元；不修改已发布试卷快照",
                "before_revision": before_revision,
                "after_revision": question.review_revision,
                "before_answer": before_answer,
                "after_answer": result.answer,
            })
            question.review_history = history
        if apply:
            session.commit()
        else:
            session.rollback()
    return {
        "total": len(questions),
        "changed": changed,
        "changed_by_type": dict(sorted(by_type.items())),
        "split_extra_items": split,
        "accepted_answer_items": accepted,
        "manual_answer_items": manual,
        "unresolved_items": unresolved,
        "downgraded_to_raw_answer_only": downgraded,
        "downgraded_questions": downgraded_questions,
        "unresolved_questions": unresolved_questions,
        "applied": apply,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-files", action="store_true",
        help="重写四份正式 interim/seed JSON；默认只预演",
    )
    parser.add_argument(
        "--apply-database", action="store_true",
        help="提交当前数据库迁移；默认事务回滚",
    )
    parser.add_argument(
        "--skip-files", action="store_true", help="不检查仓库 JSON",
    )
    parser.add_argument(
        "--skip-database", action="store_true", help="不检查当前数据库",
    )
    args = parser.parse_args()
    output = {}
    if not args.skip_files:
        output["files"] = normalize_files(write=args.write_files)
    if not args.skip_database:
        output["database"] = normalize_database(apply=args.apply_database)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
