#!/usr/bin/env python3
"""Build the reviewed Grade 6 Chapter 2 algebra-expression dataset.

The DOCX parser and answer-structure rules are shared with the original M0
rational-number builder. Outputs use an ``expr_*`` prefix, so this script does
not overwrite the existing Chapter 1 artifacts. Imported image assets are
stored under the chapter-specific ``assets/expr`` directory.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_rational_m0 as builder


CHAPTER_CODE = "math.g6.expr"
CHAPTER_NAME = "第2章 简单的代数式"

SECTIONS = [
    {"id": f"{CHAPTER_CODE}.sec.2_1", "name": "2.1 用字母表示数", "order_no": 1},
    {"id": f"{CHAPTER_CODE}.sec.2_2", "name": "2.2 代数式与代数式的值", "order_no": 2},
    {"id": f"{CHAPTER_CODE}.sec.2_3", "name": "2.3 一次式", "order_no": 3},
    {
        "id": f"{CHAPTER_CODE}.sec.reading_divisibility",
        "name": "阅读材料：能被 3、5、9 整除的数的特征",
        "order_no": 4,
    },
]

KNOWLEDGE_POINTS = [
    (
        "01_literal_relations",
        "用字母表示数与数量关系",
        "理解",
        ["抽象能力"],
        "进一步理解用字母表示数的意义，体验用数学符号表达数量关系的过程",
    ),
    (
        "02_expression_concept",
        "代数式的概念",
        "理解",
        ["抽象能力"],
        "借助现实情境了解代数式",
    ),
    (
        "03_expression_quantity",
        "用代数式表示具体问题中的简单数量关系",
        "掌握",
        ["抽象能力", "模型观念"],
        "能分析具体问题中的简单数量关系，并用代数式表示",
    ),
    (
        "04_expression_value",
        "求代数式的值",
        "掌握",
        ["运算能力"],
        "会把具体数代入代数式进行计算，会选择适当的方法求代数式的值",
    ),
    (
        "05_linear_term",
        "一次式及其项、系数、常数项",
        "理解",
        ["抽象能力"],
        "理解一次式相关概念，为整式运算作准备",
    ),
    (
        "06_combine_like_terms",
        "合并同类项",
        "掌握",
        ["运算能力", "推理能力"],
        "掌握合并同类项的法则",
    ),
    (
        "07_remove_parentheses",
        "去括号法则（数与一次式相乘）",
        "掌握",
        ["运算能力", "推理能力"],
        "掌握去括号的法则并会进行相应的化简",
    ),
    (
        "08_divisibility_patterns",
        "能被 3、5、9 整除的数的特征（拓展）",
        "了解",
        ["推理能力", "创新意识"],
        "经历用字母与推理发现数字规律的过程",
    ),
]


def configure_builder() -> None:
    builder.CHAPTER_CODE = CHAPTER_CODE
    builder.CHAPTER_NAME = CHAPTER_NAME
    builder.ASSET_DIR_NAME = "expr"
    builder.OUTPUT_PREFIX = "expr"
    builder.SECTIONS = SECTIONS
    builder.SECTION_BY_NAME = {item["name"]: item for item in SECTIONS}
    builder.KNOWLEDGE_POINTS = KNOWLEDGE_POINTS
    builder.KNOWLEDGE_POINT_BY_NAME = {
        name: {
            "id": f"{CHAPTER_CODE}.kp.{suffix}",
            "name": name,
            "mastery_level": mastery_level,
            "core_competencies": core_competencies,
            "curriculum_basis": curriculum_basis,
            "order_no": order_no,
        }
        for order_no, (suffix, name, mastery_level, core_competencies, curriculum_basis)
        in enumerate(KNOWLEDGE_POINTS, start=1)
    }


def main() -> int:
    configure_builder()
    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / "六年级_数学_校本审核" / "新" / "expr_简单的代数式.docx"
    report = builder.build(source, repo_root / "data", repo_root, limit=50)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
