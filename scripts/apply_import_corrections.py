#!/usr/bin/env python3
"""Apply optional technical corrections from the M0 import CSV."""

from __future__ import annotations

import csv
import json
from pathlib import Path


ALLOWED_STATUS = {"parsed", "needs_technical_cleanup", "excluded"}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_answer(value: str):
    value = value.strip()
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    code_root = Path(__file__).resolve().parents[1]
    data_root = code_root / "data"
    audit_path = data_root / "audit" / "rational_import_audit.csv"
    all_path = data_root / "interim" / "rational_all.json"
    seed_path = data_root / "seed" / "rational_questions.json"

    with audit_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    by_qid = {row["original_qid"]: row for row in rows}

    errors = []
    for row_number, row in enumerate(rows, start=2):
        status = row["import_status"].strip()
        if status not in ALLOWED_STATUS:
            errors.append(f"CSV 第 {row_number} 行 import_status 无效：{status}")
    if errors:
        print("\n".join(f"ERROR: {message}" for message in errors))
        return 1

    all_source = load_json(all_path)
    seed_source = load_json(seed_path)
    source_by_qid = {record["original_qid"]: record for record in all_source}
    changed_rows = [
        row
        for row in rows
        if row["corrected_answer"].strip()
        or row["technical_note"].strip()
        or row["import_status"].strip()
        != source_by_qid.get(row["original_qid"], {}).get("import_status")
    ]
    if not changed_rows:
        print("导入检查表没有技术修正，不生成重复的 corrected JSON。")
        return 0

    def apply(records: list[dict]) -> list[dict]:
        for record in records:
            row = by_qid.get(record["original_qid"])
            if not row:
                continue
            answer = parse_answer(row["corrected_answer"])
            if answer is not None:
                record["correct_answer"] = answer
                record["grading_readiness"] = "machine_ready"
            record["import_status"] = row["import_status"].strip()
            record["technical_note"] = row["technical_note"].strip() or None
        return records

    all_records = apply(all_source)
    seed_records = apply(seed_source)
    all_output = data_root / "interim" / "rational_all_corrected.json"
    seed_output = data_root / "seed" / "rational_questions_corrected.json"
    write_json(all_output, all_records)
    write_json(seed_output, seed_records)

    summary = {
        "seed_total": len(seed_records),
        "source_review_status": "reviewed",
        "seed_machine_ready": sum(item["grading_readiness"] == "machine_ready" for item in seed_records),
        "seed_raw_answer_only": sum(item["grading_readiness"] == "raw_answer_only" for item in seed_records),
        "seed_excluded": sum(item["import_status"] == "excluded" for item in seed_records),
        "outputs": [
            all_output.relative_to(code_root).as_posix(),
            seed_output.relative_to(code_root).as_posix(),
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
