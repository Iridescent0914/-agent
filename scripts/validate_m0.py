#!/usr/bin/env python3
"""Validate M0 generated data and its traceability to the source DOCX."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def main() -> int:
    code_root = Path(__file__).resolve().parents[1]
    project_root = code_root.parent
    data_root = code_root / "data"
    all_path = data_root / "interim" / "rational_all.json"
    seed_path = data_root / "seed" / "rational_questions.json"
    tree_path = data_root / "seed" / "rational_knowledge_tree.json"
    audit_path = data_root / "audit" / "rational_import_audit.csv"
    report_path = data_root / "audit" / "rational_m0_report.json"

    errors: list[str] = []
    for path in [all_path, seed_path, tree_path, audit_path, report_path]:
        require(path.is_file(), f"缺少输出文件：{path.relative_to(code_root)}", errors)
    if errors:
        print("\n".join(f"ERROR: {item}" for item in errors))
        return 1

    all_records = load_json(all_path)
    seed_records = load_json(seed_path)
    tree = load_json(tree_path)
    report = load_json(report_path)
    with audit_path.open(encoding="utf-8-sig", newline="") as handle:
        audit_rows = list(csv.DictReader(handle))

    all_qids = [item.get("original_qid") for item in all_records]
    seed_qids = [item.get("original_qid") for item in seed_records]
    require(len(all_qids) == len(set(all_qids)), "完整抽取数据中题目 ID 不唯一", errors)
    require(len(seed_qids) == len(set(seed_qids)), "种子数据中题目 ID 不唯一", errors)
    require(set(seed_qids).issubset(set(all_qids)), "种子题目无法在完整抽取数据中追溯", errors)
    require(30 <= len(seed_records) <= 50, f"种子题数量应为 30–50，实际 {len(seed_records)}", errors)
    require(len(audit_rows) == len(all_records), "核对表行数与完整抽取题数不一致", errors)
    require(report.get("total_extracted") == len(all_records), "报告抽取数与数据不一致", errors)
    require(report.get("seed_candidate_count") == len(seed_records), "报告候选数与数据不一致", errors)

    required = {
        "original_qid",
        "chapter",
        "question_type",
        "difficulty",
        "question_text",
        "correct_answer",
        "answer_match_type",
        "answer_normalization_rule",
        "grading_readiness",
        "assets",
        "main_knowledge_point",
        "main_exam_point",
        "source_file",
        "source_question_number",
        "source_review_status",
        "import_status",
    }
    for index, record in enumerate(seed_records, start=1):
        missing = sorted(key for key in required if key not in record)
        require(not missing, f"种子题第 {index} 项缺字段：{missing}", errors)
        require(record.get("question_type") in {"choice", "fill_blank"}, f"{record.get('original_qid')} 题型不在首批范围", errors)
        require(record.get("difficulty") in {1, 2, 3, 4, 5}, f"{record.get('original_qid')} 难度无效", errors)
        require(bool(record.get("question_text")), f"{record.get('original_qid')} 缺题干", errors)
        require(bool(record.get("correct_answer")), f"{record.get('original_qid')} 缺参考答案结构", errors)
        require("[ASSET:" not in json.dumps(record.get("correct_answer"), ensure_ascii=False), f"{record.get('original_qid')} 的可判分答案仍含资源占位符", errors)
        require(record.get("grading_readiness") == "machine_ready", f"{record.get('original_qid')} 尚未达到确定性判分状态", errors)
        require(record.get("source_review_status") == "reviewed", f"{record.get('original_qid')} 未继承权威来源状态", errors)
        require(record.get("import_status") in {"parsed", "needs_technical_cleanup", "excluded"}, f"{record.get('original_qid')} 导入状态无效", errors)
        require("[ASSET:unknown]" not in json.dumps(record, ensure_ascii=False), f"{record.get('original_qid')} 存在未关联资源", errors)
        source = project_root / record.get("source_file", "")
        require(source.is_file(), f"{record.get('original_qid')} 源文件不存在：{source}", errors)
        for asset in record.get("assets", []):
            asset_path = data_root / asset.get("path", "")
            require(asset_path.is_file(), f"{record.get('original_qid')} 资源文件不存在：{asset_path}", errors)
        answer = record.get("correct_answer")
        if record.get("answer_match_type") == "group":
            require(isinstance(answer, dict) and answer.get("kind") == "group", f"{record.get('original_qid')} 小题组答案结构无效", errors)
            require(bool(answer.get("items")), f"{record.get('original_qid')} 小题组答案项为空", errors)
            item_ids = [item.get("item_id") for item in answer.get("items", [])]
            require(len(item_ids) == len(set(item_ids)), f"{record.get('original_qid')} 小题组 item_id 不唯一", errors)
            for item in answer.get("items", []):
                require(item.get("answer") is not None, f"{record.get('original_qid')} 的 {item.get('item_id')} 缺少可判分答案", errors)
                require(bool(item.get("answer_match_type")), f"{record.get('original_qid')} 的 {item.get('item_id')} 缺少判分方式", errors)
        elif record.get("question_type") == "choice":
            options = record.get("options") or {}
            require(len(options) >= 2, f"{record.get('original_qid')} 选择题选项不足", errors)
            require(answer in options, f"{record.get('original_qid')} 答案不在选项中", errors)

    exam_ids = {item["main_exam_point"]["id"] for item in seed_records}
    require(len(exam_ids) == 21, f"自动组卷池应覆盖 21 个考点，实际 {len(exam_ids)}", errors)

    tree_exam_ids = {
        exam["id"]
        for kp in tree.get("knowledge_points", [])
        for exam in kp.get("exam_points", [])
    }
    require(exam_ids.issubset(tree_exam_ids), "种子题考点未全部进入知识树", errors)

    audit_by_qid = {row["original_qid"]: row for row in audit_rows}
    require(set(seed_qids).issubset(audit_by_qid), "种子题未全部进入核对表", errors)
    for qid in seed_qids:
        require(audit_by_qid[qid]["include_in_seed"] == "yes", f"{qid} 在核对表中未标为候选", errors)

    summary = {
        "all_questions": len(all_records),
        "seed_questions": len(seed_records),
        "seed_types": dict(Counter(item["question_type"] for item in seed_records)),
        "seed_exam_points": len(exam_ids),
        "machine_ready": sum(item.get("grading_readiness") == "machine_ready" for item in seed_records),
        "raw_answer_only": sum(item.get("grading_readiness") == "raw_answer_only" for item in seed_records),
        "source_reviewed": sum(item.get("source_review_status") == "reviewed" for item in seed_records),
        "validation_errors": len(errors),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if errors:
        print("\n".join(f"ERROR: {item}" for item in errors))
        return 1
    print("M0 数据结构校验通过；题目与答案继承校本审核 DOCX 的权威状态。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
