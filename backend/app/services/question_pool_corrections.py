"""Apply explicit, source-checked corrections without solving or guessing."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import re
from typing import Any

from app.services.question_pool_quality import pool_answer_issues

VERSION = "question-pool-governance-v1"
TARGET_TYPES = {"choice", "fill_blank", "true_false"}


def apply_pool_corrections(records: list[dict[str, Any]], config_path: Path) -> int:
    if not config_path.is_file():
        return 0
    config = json.loads(config_path.read_text(encoding="utf-8"))
    applied = 0
    for record in records:
        correction = config["questions"].get(record["original_qid"])
        if correction is None:
            continue
        manual_reference = correction.get("mode") == "manual_reference"
        final_answer_only = correction.get("mode") == "final_answer_only"
        asset_only = correction.get("mode") == "asset_only"
        if manual_reference and record["question_type"] not in {"calculation", "solution"}:
            raise ValueError("Manual reference mode requires a manual question type")
        if final_answer_only and record["question_type"] != "calculation":
            raise ValueError("Final-answer mode is restricted to calculation questions")
        if record["question_type"] not in TARGET_TYPES and not manual_reference and not final_answer_only and not asset_only:
            raise ValueError("Correction outside approved question types: " + record["original_qid"])
        if asset_only:
            allowed = {"mode", "note", "replacements", "additional_assets", "resolved_import_issues"}
            if set(correction) - allowed:
                raise ValueError("Asset-only corrections cannot change answers or grading")
            if not correction.get("additional_assets") or not correction.get("replacements") or any(
                old != "[ASSET:unknown][ASSET:unknown]" or new != f"[ASSET:{correction['additional_assets'][0]['path']}]"
                for old, new in correction.get("replacements", [])
            ):
                raise ValueError("Asset-only corrections require a source-checked asset replacement")
        for key, value in correction.get("fields", {}).items():
            if key not in {"question_text", "options", "reference_answer_raw", "solution"}:
                raise ValueError("Unsupported correction field: " + key)
            record[key] = deepcopy(value)
        for old, new in correction.get("replacements", []):
            if old.startswith("[ASSET:rId"):
                rid = old.removeprefix("[ASSET:").removesuffix("]")
                pattern = r"\[ASSET:(?:[^\]]*/" + re.escape(record["original_qid"]) + r"_)?" + re.escape(rid) + r"(?:\.[^\]]+)?\]"
                record["question_text"] = re.sub(pattern, lambda _: new, record["question_text"])
                if new not in record["question_text"]:
                    raise ValueError("Missing source table label: " + record["original_qid"])
                continue
            if old in new and new in record["question_text"]:
                continue
            if old in record["question_text"]:
                record["question_text"] = record["question_text"].replace(old, new)
            elif new not in record["question_text"]:
                raise ValueError(f"Correction no longer matches source: {record['original_qid']}: {old}")
        for key in ("append_instruction", "append_text"):
            text = correction.get(key)
            if text and text.strip() not in record["question_text"]:
                record["question_text"] += "\n" + text.strip()
        for asset in correction.get("additional_assets", []):
            path = asset.get("path", "")
            chapter = record["original_qid"].split(".")[3]
            if chapter not in {"rational", "expr"} or not path.startswith(f"assets/{chapter}/{record['original_qid']}_") or not path.endswith(".svg") or "/../" in path:
                raise ValueError("Unsupported recovered asset path: " + path)
            existing = next((item for item in record["assets"] if item["path"] == path), None)
            if existing is None:
                record["assets"].append(deepcopy(asset))
            elif existing != asset:
                raise ValueError("Recovered asset metadata conflict: " + path)
        resolved = set(correction.get("resolved_import_issues", []))
        if resolved - {"asset_relationship_missing", "pool_missing_source_content"}:
            raise ValueError("Unsupported resolved import issue")
        if asset_only and resolved - {"asset_relationship_missing"}:
            raise ValueError("Asset-only correction cannot resolve answer or source-content issues")
        if resolved:
            if "[ASSET:unknown]" in record["question_text"]:
                raise ValueError("Cannot resolve missing assets while unknown markers remain")
            record["import_issues"] = [issue for issue in record["import_issues"] if issue not in resolved]
        if asset_only:
            applied += 1
            continue
        if "answers" in correction:
            items = []
            for subquestion_no, answers in correction["answers"].items():
                for blank_no, answer in enumerate(answers, 1):
                    value, match_type, *aliases = answer
                    item = {
                        "item_id": f"sub_{subquestion_no}.blank_{blank_no}",
                        "subquestion_id": f"sub_{subquestion_no}",
                        "subquestion_no": int(subquestion_no),
                        "blank_id": f"blank_{blank_no}",
                        "blank_no": blank_no,
                        "order_no": len(items) + 1,
                        "answer": value,
                        "answer_match_type": match_type,
                        "reference_answer_raw": value,
                    }
                    if aliases:
                        item["accepted_answers"] = list(dict.fromkeys([value, *aliases]))
                    items.append(item)
            record["correct_answer"] = {"kind": "group", "schema_version": "answer-units-v1", "items": items}
            record["answer_match_type"] = "group"
            record["reference_answer_raw"] = "\n".join(
                f"第{item['subquestion_no']}小题第{item['blank_no']}空：{item['answer']}"
                for item in items
            )
            record["answer_normalization_rule"] = {
                "answer_schema_version": "answer-units-v1",
                "trim_whitespace": True,
                "normalize_fullwidth_symbols": True,
                "ignore_whitespace": True,
                "uppercase": True,
                "allow_fraction_decimal_equivalence": True,
                "allow_equivalent_fraction": True,
                "item_order_matters": True,
                "partial_credit": True,
                **correction.get("grading_rules", {}),
            }
        blocked = list(correction.get("blocked_reasons", []))
        if record["question_type"] not in TARGET_TYPES and not manual_reference:
            if "pool_profile_type_unsupported" not in blocked:
                blocked.append("pool_profile_type_unsupported")
        if manual_reference:
            items = record["correct_answer"]["items"]
            if not items or any(item["answer_match_type"] != "manual" or not isinstance(item["answer"], str) or not item["answer"].strip() or "[ASSET:" in item["answer"] for item in items):
                raise ValueError("Manual reference corrections must have complete textual references")
            record["grading_readiness"] = "raw_answer_only"
            record["import_issues"] = [issue for issue in record["import_issues"] if issue not in {"answer_units_need_review", "answer_items_not_structured"}]
        elif blocked:
            record["grading_readiness"] = "raw_answer_only"
        else:
            issues = pool_answer_issues(record["correct_answer"], record["answer_match_type"], record["answer_normalization_rule"])
            if issues:
                raise ValueError(f"{record['original_qid']}: {issues}")
            record["grading_readiness"] = "machine_ready"
            record["import_issues"] = [issue for issue in record["import_issues"] if issue not in {
                "answer_items_not_structured", "answer_units_need_review", "source_contains_ai_draft_marker",
                "pool_source_conflict", "pool_manual_reasoning_required", "pool_missing_source_content",
                "pool_manual_calculation",
            }]
        record["import_issues"] = list(dict.fromkeys([*record["import_issues"], *blocked]))
        record["answer_structure_source"] = VERSION
        record["pool_governance"] = {"version": VERSION, "note": correction["note"], "blocked_reasons": blocked}
        applied += 1
    return applied
