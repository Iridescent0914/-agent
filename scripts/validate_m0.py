#!/usr/bin/env python3
"""Validate M0 generated data and its traceability to the source DOCX."""

from __future__ import annotations

import csv
import json
import argparse
from collections import Counter
from pathlib import Path


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def main() -> int:
    code_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="rational", help="题库产物前缀，默认 rational")
    parser.add_argument("--source-root", type=Path, help="用于校验 source_file 的项目根目录")
    parser.add_argument("--chapter-code", default="math.g6.rational")
    parser.add_argument("--min-seed", type=int, default=30)
    parser.add_argument("--max-seed", type=int, default=50)
    parser.add_argument("--expected-sections", type=int, default=6)
    parser.add_argument("--expected-knowledge-points", type=int, default=13)
    args = parser.parse_args()
    project_root = args.source_root.resolve() if args.source_root else code_root.parent
    data_root = code_root / "data"
    prefix = args.prefix
    all_path = data_root / "interim" / f"{prefix}_all.json"
    seed_path = data_root / "seed" / f"{prefix}_questions.json"
    tree_path = data_root / "seed" / f"{prefix}_knowledge_tree.json"
    audit_path = data_root / "audit" / f"{prefix}_import_audit.csv"
    report_path = data_root / "audit" / f"{prefix}_m0_report.json"

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
    require(args.min_seed <= len(seed_records) <= args.max_seed, f"种子题数量应为 {args.min_seed}–{args.max_seed}，实际 {len(seed_records)}", errors)
    require(len(audit_rows) == len(all_records), "核对表行数与完整抽取题数不一致", errors)
    require(report.get("total_extracted") == len(all_records), "报告抽取数与数据不一致", errors)
    require(report.get("seed_candidate_count") == len(seed_records), "报告候选数与数据不一致", errors)

    required = {
        "original_qid",
        "grade",
        "chapter",
        "section",
        "question_type",
        "difficulty",
        "question_text",
        "correct_answer",
        "answer_match_type",
        "answer_normalization_rule",
        "grading_readiness",
        "assets",
        "main_knowledge_point",
        "core_literacy",
        "is_required",
        "source_file",
        "source_question_number",
        "source_review_status",
        "import_status",
    }
    for index, record in enumerate(all_records, start=1):
        missing = sorted(key for key in required if key not in record)
        require(not missing, f"完整题库第 {index} 项缺字段：{missing}", errors)
        qid = record.get("original_qid")
        require(bool(record.get("grade")), f"{qid} 年级为空", errors)
        require(bool(record.get("chapter", {}).get("name")), f"{qid} 章为空", errors)
        require(bool(record.get("section")), f"{qid} 主属节为空", errors)
        require(bool(record.get("main_knowledge_point", {}).get("name")), f"{qid} 节内主属 KP 为空", errors)
        require(
            str(record.get("main_knowledge_point", {}).get("id", "")).startswith(f"{args.chapter_code}.kp."),
            f"{qid} 节内主属 KP 未使用正式知识图谱 ID",
            errors,
        )
        require(bool(record.get("core_literacy")), f"{qid} 核心素养为空", errors)
        require(type(record.get("is_required")) is bool, f"{qid} 必做题标记不是布尔值", errors)

    for index, record in enumerate(seed_records, start=1):
        missing = sorted(key for key in required if key not in record)
        require(not missing, f"种子题第 {index} 项缺字段：{missing}", errors)
        require(record.get("question_type") in {"choice", "fill_blank", "true_false"}, f"{record.get('original_qid')} 题型不在自动判分范围", errors)
        require(record.get("difficulty") in {1, 2, 3, 4, 5}, f"{record.get('original_qid')} 难度无效", errors)
        require(bool(record.get("question_text")), f"{record.get('original_qid')} 缺题干", errors)
        require(bool(record.get("correct_answer")), f"{record.get('original_qid')} 缺参考答案结构", errors)
        require("[ASSET:" not in json.dumps(record.get("correct_answer"), ensure_ascii=False), f"{record.get('original_qid')} 的可判分答案仍含资源占位符", errors)
        require(record.get("grading_readiness") == "machine_ready", f"{record.get('original_qid')} 尚未达到确定性判分状态", errors)
        require(record.get("source_review_status") == "reviewed", f"{record.get('original_qid')} 未继承权威来源状态", errors)
        require(record.get("import_status") in {"parsed", "needs_technical_cleanup", "excluded"}, f"{record.get('original_qid')} 导入状态无效", errors)
        require("[ASSET:unknown]" not in json.dumps(record, ensure_ascii=False), f"{record.get('original_qid')} 存在未关联资源", errors)
        source = project_root / record.get("source_file", "")
        if not source.is_file() and str(record.get("source_file", "")).startswith("code/"):
            # Historical Chapter 1 artifacts used a parent workspace whose
            # source paths began with code/. Also support the current layout.
            source = code_root / str(record.get("source_file", "")).removeprefix("code/")
        require(source.is_file(), f"{record.get('original_qid')} 源文件不存在：{source}", errors)
        for asset in record.get("assets", []):
            asset_path = data_root / asset.get("path", "")
            require(asset_path.is_file(), f"{record.get('original_qid')} 资源文件不存在：{asset_path}", errors)
        answer = record.get("correct_answer")
        if record.get("answer_match_type") == "group":
            require(isinstance(answer, dict) and answer.get("kind") == "group", f"{record.get('original_qid')} 小题组答案结构无效", errors)
            require(answer.get("schema_version") == "answer-units-v1", f"{record.get('original_qid')} 未使用统一小题-空答案版本", errors)
            require(bool(answer.get("items")), f"{record.get('original_qid')} 小题组答案项为空", errors)
            item_ids = [item.get("item_id") for item in answer.get("items", [])]
            require(len(item_ids) == len(set(item_ids)), f"{record.get('original_qid')} 小题组 item_id 不唯一", errors)
            for order_no, item in enumerate(answer.get("items", []), start=1):
                require(item.get("answer") is not None, f"{record.get('original_qid')} 的 {item.get('item_id')} 缺少可判分答案", errors)
                require(isinstance(item.get("answer"), str), f"{record.get('original_qid')} 的 {item.get('item_id')} 答案必须是单个字符串", errors)
                require(item.get("answer_match_type") in {"choice", "numeric", "exact", "latex_exact"}, f"{record.get('original_qid')} 的 {item.get('item_id')} 判分方式无效", errors)
                require(item.get("item_id") == f"sub_{item.get('subquestion_no')}.blank_{item.get('blank_no')}", f"{record.get('original_qid')} 的答案项未按小题-空编号", errors)
                require(item.get("subquestion_id") == f"sub_{item.get('subquestion_no')}", f"{record.get('original_qid')} 的小题编号不一致", errors)
                require(item.get("blank_id") == f"blank_{item.get('blank_no')}", f"{record.get('original_qid')} 的空编号不一致", errors)
                require(item.get("order_no") == order_no, f"{record.get('original_qid')} 的答案项顺序号不连续", errors)
                accepted = item.get("accepted_answers", [])
                require(isinstance(accepted, list) and all(isinstance(value, str) for value in accepted), f"{record.get('original_qid')} 的可接受答案结构无效", errors)
        elif record.get("question_type") == "choice":
            options = record.get("options") or {}
            require(len(options) >= 2, f"{record.get('original_qid')} 选择题选项不足", errors)
            require(answer in options, f"{record.get('original_qid')} 答案不在选项中", errors)

    knowledge_ids = {item["main_knowledge_point"]["id"] for item in seed_records}
    require(bool(knowledge_ids), "自动组卷池未覆盖任何节内主属 KP", errors)
    require(
        report.get("seed_knowledge_point_count") == len(knowledge_ids),
        "报告中的节内主属 KP 数与种子题不一致",
        errors,
    )

    tree_knowledge_ids = {kp["id"] for kp in tree.get("knowledge_points", [])}
    require(knowledge_ids.issubset(tree_knowledge_ids), "种子题节内主属 KP 未全部进入知识树", errors)
    require(len(tree.get("sections", [])) == args.expected_sections, f"知识树应包含 {args.expected_sections} 个正式节节点", errors)
    require(len(tree.get("knowledge_points", [])) == args.expected_knowledge_points, f"知识树应包含 {args.expected_knowledge_points} 个正式知识点", errors)
    require(
        all(kp.get("core_competencies") for kp in tree.get("knowledge_points", [])),
        "知识树知识点缺少核心素养元数据",
        errors,
    )
    require(
        tree.get("semantic_mapping", {}).get("knowledge_points") == "新版 Word 节内主属 KP",
        "知识树缺少新版节内主属 KP 语义映射",
        errors,
    )

    audit_by_qid = {row["original_qid"]: row for row in audit_rows}
    require(set(seed_qids).issubset(audit_by_qid), "种子题未全部进入核对表", errors)
    for qid in seed_qids:
        require(audit_by_qid[qid]["include_in_seed"] == "yes", f"{qid} 在核对表中未标为候选", errors)

    summary = {
        "all_questions": len(all_records),
        "seed_questions": len(seed_records),
        "seed_types": dict(Counter(item["question_type"] for item in seed_records)),
        "seed_knowledge_points": len(knowledge_ids),
        "required_questions": sum(bool(item.get("is_required")) for item in all_records),
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
