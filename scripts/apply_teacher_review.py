#!/usr/bin/env python3
"""Apply teacher edits from the M0 audit CSV to reviewed JSON outputs."""

from __future__ import annotations

import csv
import json
from pathlib import Path


ALLOWED_STATUS = {"pending", "verified", "needs_teacher_review", "excluded"}


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
        status = row["audit_status"].strip()
        if status not in ALLOWED_STATUS:
            errors.append(f"CSV 第 {row_number} 行 audit_status 无效：{status}")
        if status == "verified" and not (row["teacher_final_answer"].strip() or row["extracted_answer"].strip()):
            errors.append(f"CSV 第 {row_number} 行标为 verified，但没有答案")
    if errors:
        print("\n".join(f"ERROR: {message}" for message in errors))
        return 1

    def apply(records: list[dict]) -> list[dict]:
        for record in records:
            row = by_qid.get(record["original_qid"])
            if not row:
                continue
            answer = parse_answer(row["teacher_final_answer"])
            if answer is not None:
                record["correct_answer"] = answer
                record["grading_readiness"] = "machine_ready"
            record["audit_status"] = row["audit_status"].strip()
            record["teacher_comment"] = row["teacher_comment"].strip() or None
        return records

    all_records = apply(load_json(all_path))
    seed_records = apply(load_json(seed_path))
    all_output = data_root / "interim" / "rational_all_reviewed.json"
    seed_output = data_root / "seed" / "rational_questions_reviewed.json"
    write_json(all_output, all_records)
    write_json(seed_output, seed_records)

    summary = {
        "seed_total": len(seed_records),
        "seed_verified": sum(item["audit_status"] == "verified" for item in seed_records),
        "seed_excluded": sum(item["audit_status"] == "excluded" for item in seed_records),
        "seed_needs_review": sum(item["audit_status"] in {"pending", "needs_teacher_review"} for item in seed_records),
        "outputs": [
            all_output.relative_to(code_root).as_posix(),
            seed_output.relative_to(code_root).as_posix(),
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
