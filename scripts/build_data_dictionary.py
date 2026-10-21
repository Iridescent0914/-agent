#!/usr/bin/env python3
"""Build an Excel data dictionary from the current SQLAlchemy model.

The project intentionally does not require openpyxl/xlsxwriter, so this script
writes a small standards-compliant XLSX package with the Python standard
library.  Structural metadata comes from ``backend/app/models/entities.py``;
row-count/null-rate columns are left as pending live-database statistics.
"""

from __future__ import annotations

import ast
import html
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "backend" / "app" / "models" / "entities.py"
OUTPUT_PATH = ROOT / "docs" / "数据字典_数学教学Agent.xlsx"


TABLE_INFO: dict[str, tuple[str, str, str, str]] = {
    "knowledge_points": ("知识点", "题库基础", "正式知识点目录及层级关系", "id"),
    "questions": ("题目", "题库基础", "题目内容、答案、审核与导入追溯信息", "id"),
    "school_classes": ("班级", "教学组织", "学校班级及教师信息", "id"),
    "students": ("学生", "教学组织", "学生基础信息与归档状态", "id"),
    "student_masteries": ("学生知识点掌握度", "学习画像", "学生-知识点维度的掌握度汇总", "student_id + knowledge_point_id"),
    "assignments": ("作业", "作业组卷", "作业配置及教学范围", "id"),
    "paper_instances": ("试卷实例", "作业组卷", "学生试卷版本及发布生命周期", "id"),
    "paper_instance_items": ("试卷题目快照", "作业组卷", "试卷中的题目顺序、答案和知识点快照", "id"),
    "submissions": ("提交记录", "作答批改", "学生提交及附件元数据", "id"),
    "recognition_runs": ("识别运行", "图像识别", "答题卡图片识别任务、模型与留存状态", "id"),
    "recognition_items": ("识别题目结果", "图像识别", "识别任务中每道题的抄读结果", "id"),
    "attempts": ("作答记录", "作答批改", "逐题作答、评分和人工复核结果", "id"),
    "attempt_revisions": ("作答修订审计", "作答批改", "作答状态变更前后快照与操作人", "id"),
}

TABLE_SOURCE: dict[str, str] = {
    "knowledge_points": "data/seed/*_knowledge_tree.json；题库导入服务",
    "questions": "data/seed/*_questions.json；题库审核 API",
    "school_classes": "班级管理 API",
    "students": "学生管理/演示场景 API",
    "student_masteries": "批改汇总服务 mastery.py",
    "assignments": "作业创建与组卷服务",
    "paper_instances": "paper_workflow.py / paper_snapshots.py",
    "paper_instance_items": "paper_generation.py（写入不可变快照）",
    "submissions": "提交 API（manual/image）",
    "recognition_runs": "recognition_workflow.py",
    "recognition_items": "视觉识别服务/教师复核",
    "attempts": "submission_workflow.py / grading engine",
    "attempt_revisions": "改判、撤销确认审计逻辑",
}

FIELD_ZH: dict[str, str] = {
    "id": "主键标识", "created_at": "创建时间", "updated_at": "更新时间",
    "code": "编码", "name": "名称", "chapter_code": "章节编码", "chapter_name": "章节名称",
    "section": "小节", "sort_order": "排序号", "mastery_level": "掌握层级",
    "core_competencies": "核心素养", "curriculum_basis": "课程标准依据", "parent_id": "父知识点标识",
    "original_qid": "来源题目标识", "schema_version": "数据结构版本", "grade": "年级",
    "question_type": "题型", "difficulty": "难度等级", "main_knowledge_point_id": "主知识点标识",
    "question_text": "题干", "options": "选项", "correct_answer": "标准答案",
    "reference_answer_raw": "原始参考答案", "answer_match_type": "答案匹配类型",
    "answer_normalization_rule": "答案归一化规则", "solution": "解析", "wrong_analysis": "易错分析",
    "estimated_score_rate": "预估得分率", "core_literacy": "核心素养标签", "is_required": "是否必做",
    "has_image": "是否含图片", "has_equation": "是否含公式", "assets": "资源清单",
    "source_file": "来源文件", "source_question_number": "来源题号", "source_review_status": "来源审核状态",
    "grading_readiness": "评分就绪状态", "import_status": "导入状态", "import_issues": "导入问题",
    "review_revision": "审核修订号", "review_history": "审核历史", "reviewed_by": "审核人",
    "reviewed_at": "审核时间", "archived_at": "归档时间", "archived_by": "归档人",
    "teacher_name": "教师姓名", "school_year": "学年", "student_code": "学生编码",
    "display_name": "显示姓名", "class_id": "班级标识", "class_name": "班级名称", "is_demo": "是否演示数据",
    "student_id": "学生标识", "knowledge_point_id": "知识点标识", "mastery": "掌握度",
    "confidence_level": "掌握度置信等级", "attempt_count": "作答次数", "correct_count": "答对次数",
    "recent_wrong_count": "近期错题次数", "last_practiced_at": "最近练习时间", "seed_key": "种子键",
    "idempotency_key": "幂等键", "title": "标题", "teaching_scope": "教学范围",
    "generation_config": "组卷配置", "assignment_type": "作业类型", "assignment_id": "作业标识",
    "version_no": "版本号", "status": "状态", "instance_code": "试卷实例编码",
    "supersedes_instance_id": "被替代实例标识", "recommendation_audit": "推荐审计",
    "edit_history": "编辑历史", "edit_revision": "编辑修订号", "published_at": "发布时间",
    "invalidated_at": "失效时间", "invalidation_type": "失效类型", "invalidation_reason": "失效原因",
    "invalidated_by": "失效操作人", "superseded_by_instance_id": "替代实例标识",
    "paper_instance_id": "试卷实例标识", "question_id": "题目标识", "order_no": "题目顺序号",
    "question_snapshot": "题目快照", "options_snapshot": "选项快照", "answer_snapshot": "答案快照",
    "answer_rule_snapshot": "答案规则快照", "knowledge_point_snapshot": "知识点快照",
    "answer_region": "答题区域", "selection_audit": "选题审计", "source": "提交来源",
    "request_fingerprint": "请求指纹", "attachments": "附件清单", "submitted_at": "提交时间",
    "recognition_run_id": "识别运行标识", "submission_id": "提交标识", "provider": "识别服务商",
    "model": "识别模型", "prompt_version": "提示词版本", "image_storage_key": "图片存储键",
    "image_sha256": "图片 SHA-256", "original_filename": "原始文件名", "content_type": "内容类型",
    "byte_size": "文件字节数", "pixel_width": "图片宽度", "pixel_height": "图片高度",
    "raw_response": "原始识别响应", "error_code": "错误码", "error_message": "错误信息",
    "processing_token": "处理令牌", "started_at": "开始时间", "completed_at": "完成时间",
    "committed_at": "提交入库时间", "retention_until": "留存截止时间", "image_purged_at": "图片清理时间",
    "paper_instance_item_id": "试卷题目标识", "recognition_status": "识别状态", "recognized_answer": "识别答案",
    "confidence": "识别置信度", "provider_payload": "服务商返回载荷", "student_answer": "学生答案",
    "normalized_answer": "归一化答案", "grading_details": "评分明细", "grade_status": "评分状态",
    "is_correct": "是否正确", "ocr_confidence": "OCR 置信度", "manual_override": "是否人工改判",
    "revision_no": "修订号", "answered_at": "作答时间", "attempt_id": "作答标识",
    "before_state": "修改前状态", "after_state": "修改后状态", "operator_type": "操作人类型",
    "operator_id": "操作人标识", "reason": "操作原因",
}

TABLE_RULES: dict[str, str] = {
    "knowledge_points": "code 唯一；parent_id 自关联，删除父节点时置空",
    "questions": "difficulty 取 1~5；question_type、评分就绪状态受 CHECK 约束；主知识点外键 RESTRICT",
    "school_classes": "name 唯一",
    "students": "student_code 唯一；删除班级时 class_id 置空",
    "student_masteries": "student_id + knowledge_point_id 复合主键；mastery 取 0~1",
    "assignments": "seed_key、idempotency_key（非空时）唯一；class_id 外键 RESTRICT",
    "paper_instances": "assignment_id + student_id + version_no 唯一；已发布实例受触发器保护",
    "paper_instance_items": "paper_instance_id + order_no 唯一；已发布题目快照不可修改",
    "submissions": "paper_instance_id、idempotency_key 唯一；source 仅 manual/image",
    "recognition_runs": "同一试卷最多一个活动识别任务；图片尺寸和字节数必须为正",
    "recognition_items": "recognition_run_id + paper_instance_item_id/order_no 唯一；confidence 取 0~1",
    "attempts": "每个试卷题目仅一条作答；评分状态与 is_correct 联动校验",
    "attempt_revisions": "attempt_id + revision_no 唯一；修订号必须为正",
}

ENUMS: list[tuple[str, str, str, str, str]] = [
    ("questions", "question_type", "choice / fill_blank / calculation / solution / true_false / unknown", "题型枚举", "代码 CHECK ck_questions_type"),
    ("questions", "grading_readiness", "machine_ready / needs_review / manual_only（业务状态）", "评分就绪状态", "machine_ready 要求答案与匹配规则非空"),
    ("questions", "source_review_status", "代码未限定；常用 reviewed / pending", "来源审核状态", "由导入和审核服务维护"),
    ("student_masteries", "confidence_level", "low / medium / high", "掌握度置信等级", "代码 CHECK ck_confidence_level"),
    ("paper_instances", "status", "draft / published", "试卷状态", "代码 CHECK ck_paper_status"),
    ("submissions", "source", "manual / image", "提交来源", "代码 CHECK ck_submission_source"),
    ("recognition_runs", "status", "uploaded / processing / ready_for_review / failed / committed / discarded", "识别运行状态", "代码 CHECK ck_recognition_run_status"),
    ("recognition_items", "recognition_status", "readable / blank / missing / illegible / ambiguous", "单题识别状态", "代码 CHECK ck_recognition_item_status"),
    ("attempts", "grade_status", "auto_confirmed / needs_review / teacher_confirmed", "评分状态", "代码 CHECK ck_attempt_grade_status"),
]

FK_RULES: list[tuple[str, str, str, str]] = [
    ("knowledge_points", "parent_id", "knowledge_points", "id (ON DELETE SET NULL)"),
    ("questions", "main_knowledge_point_id", "knowledge_points", "id (ON DELETE RESTRICT)"),
    ("students", "class_id", "school_classes", "id (ON DELETE SET NULL)"),
    ("student_masteries", "student_id", "students", "id (ON DELETE CASCADE)"),
    ("student_masteries", "knowledge_point_id", "knowledge_points", "id (ON DELETE CASCADE)"),
    ("assignments", "class_id", "school_classes", "id (ON DELETE RESTRICT)"),
    ("paper_instances", "assignment_id", "assignments", "id (ON DELETE RESTRICT)"),
    ("paper_instances", "student_id", "students", "id (ON DELETE RESTRICT)"),
    ("paper_instances", "supersedes_instance_id", "paper_instances", "id (ON DELETE SET NULL)"),
    ("paper_instances", "superseded_by_instance_id", "paper_instances", "id (ON DELETE RESTRICT)"),
    ("paper_instance_items", "paper_instance_id", "paper_instances", "id (ON DELETE CASCADE)"),
    ("paper_instance_items", "question_id", "questions", "id (ON DELETE RESTRICT)"),
    ("submissions", "student_id", "students", "id (ON DELETE RESTRICT)"),
    ("submissions", "paper_instance_id", "paper_instances", "id (ON DELETE RESTRICT)"),
    ("recognition_runs", "paper_instance_id", "paper_instances", "id (ON DELETE RESTRICT)"),
    ("recognition_runs", "student_id", "students", "id (ON DELETE RESTRICT)"),
    ("recognition_runs", "submission_id", "submissions", "id (ON DELETE RESTRICT)"),
    ("recognition_items", "recognition_run_id", "recognition_runs", "id (ON DELETE CASCADE)"),
    ("recognition_items", "paper_instance_item_id", "paper_instance_items", "id (ON DELETE RESTRICT)"),
    ("attempts", "submission_id", "submissions", "id (ON DELETE CASCADE)"),
    ("attempts", "student_id", "students", "id (ON DELETE RESTRICT)"),
    ("attempts", "paper_instance_item_id", "paper_instance_items", "id (ON DELETE RESTRICT)"),
    ("attempts", "question_id", "questions", "id (ON DELETE RESTRICT)"),
    ("attempt_revisions", "attempt_id", "attempts", "id (ON DELETE CASCADE)"),
]


def _literal(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return repr(ast.literal_eval(node))
    except Exception:
        return ast.unparse(node)


def _type_info(call: ast.Call | None) -> tuple[str, str, str]:
    if not call or not call.args:
        return "", "", ""
    node = call.args[0]
    name = ast.unparse(node)
    short = name.split("(", 1)[0].split(".")[-1]
    mapping = {
        "String": "VARCHAR", "Text": "TEXT", "Integer": "INTEGER", "SmallInteger": "SMALLINT",
        "BigInteger": "BIGINT", "Boolean": "BOOLEAN", "UUID": "UUID", "JSONB": "JSONB",
        "Numeric": "NUMERIC", "DateTime": "TIMESTAMP WITH TIME ZONE",
    }
    dtype = mapping.get(short, short.upper())
    length = precision = ""
    if short == "String" and getattr(node, "args", None):
        length = _literal(node.args[0]).strip("'")
    if short == "Numeric" and getattr(node, "args", None):
        nums = [_literal(arg) for arg in node.args]
        if nums:
            precision = ", ".join(nums)
    return dtype, length, precision


def _kw(call: ast.Call, key: str) -> ast.AST | None:
    for item in call.keywords:
        if item.arg == key:
            return item.value
    return None


def extract_fields() -> list[dict[str, str]]:
    tree = ast.parse(MODEL_PATH.read_text(encoding="utf-8"))
    rows: list[dict[str, str]] = []
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef):
            continue
        table = ""
        for stmt in cls.body:
            if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in stmt.targets):
                table = ast.literal_eval(stmt.value)
                break
        if not table:
            continue
        for stmt in cls.body:
            if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                continue
            if not isinstance(stmt.value, ast.Call) or ast.unparse(stmt.value.func).split(".")[-1] != "mapped_column":
                continue
            field = stmt.target.id
            dtype, length, precision = _type_info(stmt.value)
            ann = ast.unparse(stmt.annotation)
            nullable = _kw(stmt.value, "nullable")
            if nullable is None:
                nullable_text = "否" if "| None" not in ann else "是"
            else:
                nullable_text = "是" if ast.literal_eval(nullable) else "否"
            pk = "是" if _kw(stmt.value, "primary_key") is not None and ast.literal_eval(_kw(stmt.value, "primary_key")) else "否"
            unique = "是" if _kw(stmt.value, "unique") is not None and ast.literal_eval(_kw(stmt.value, "unique")) else "否"
            default = _literal(_kw(stmt.value, "default")) or _literal(_kw(stmt.value, "server_default"))
            rows.append({
                "table": table, "field": field, "dtype": dtype, "length": length, "precision": precision,
                "pk": pk, "nullable": nullable_text, "unique": unique, "default": default,
            })

        # TimestampMixin is inherited by these four tables.
        if any(isinstance(base, ast.Name) and base.id == "TimestampMixin" for base in cls.bases):
            for field, dtype, default in (("created_at", "TIMESTAMP WITH TIME ZONE", "func.now()"), ("updated_at", "TIMESTAMP WITH TIME ZONE", "func.now()")):
                if not any(row["table"] == table and row["field"] == field for row in rows):
                    rows.insert(0 if field == "created_at" else 999999, {"table": table, "field": field, "dtype": dtype, "length": "", "precision": "", "pk": "否", "nullable": "否", "unique": "否", "default": default})
    # Stable model order and field order as declared (created_at first for mixin tables).
    table_order = {name: i for i, name in enumerate(TABLE_INFO)}
    return sorted(rows, key=lambda r: (table_order.get(r["table"], 999), r["field"]))


def _sample(field: str, dtype: str) -> str:
    if field.endswith("_at") or field in {"created_at", "updated_at"}:
        return "2026-10-20 06:45:00+08"
    if dtype == "UUID":
        return "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    if dtype == "BOOLEAN":
        return "true"
    if dtype in {"INTEGER", "SMALLINT", "BIGINT"}:
        return "1"
    if dtype == "NUMERIC":
        return "0.8500"
    if dtype == "JSONB":
        return "{} / []"
    return "示例值"


def _source_field(table: str, field: str) -> str:
    if table in {"questions", "knowledge_points"}:
        return f"{TABLE_SOURCE[table]}（{field}）"
    return f"{TABLE_SOURCE.get(table, '业务服务')}（{field}）"


def build_sheets() -> list[tuple[str, list[list[Any]]]]:
    fields = extract_fields()
    field_rows: list[list[Any]] = [[
        "序号", "表名", "字段中文名称（COMMENT）", "字段名称", "数据类型", "数据长度", "数据精度",
        "是否主键", "是否为空", "是否唯一", "默认值", "转换规则/清洗规则", "备注", "样例数据",
        "源表字段/来源字段", "维表字段/关联字段", "总数量", "非空非重复量", "非空非重复率", "空值量", "空值率", "合规描述",
    ]]
    fk_map = {(t, f): f"{rt}.{rf}" for t, f, rt, rf in FK_RULES}
    for no, row in enumerate(fields, 1):
        table = row["table"]
        field = row["field"]
        rule = "按模型类型直接写入；JSONB 保持结构化对象/数组"
        if field in {"id", "*_id"} or field.endswith("_id"):
            rule = "UUID 关联键；写入前校验关联记录存在"
        if field.endswith("_at"):
            rule = "统一使用带时区时间戳"
        if field in {"question_text", "solution", "wrong_analysis", "reference_answer_raw", "reason"}:
            rule = "保留原始文本，不做截断"
        if field in {"assets", "options", "correct_answer", "teaching_scope", "generation_config", "review_history", "edit_history", "question_snapshot", "options_snapshot", "answer_snapshot", "answer_rule_snapshot", "knowledge_point_snapshot", "selection_audit", "attachments", "raw_response", "provider_payload", "student_answer", "normalized_answer", "grading_details", "before_state", "after_state", "answer_normalization_rule"}:
            rule = "JSONB 结构校验；不转成字符串"
        note = TABLE_RULES.get(table, "") if field in {"id", "status", "question_type", "source", "mastery", "confidence", "revision_no"} else ""
        field_rows.append([
            no, table, FIELD_ZH.get(field, field), field, row["dtype"], row["length"], row["precision"], row["pk"],
            row["nullable"], row["unique"], row["default"], rule, note, _sample(field, row["dtype"]),
            _source_field(table, field), fk_map.get((table, field), ""), "待连接数据库", "待连接数据库", "待计算", "待连接数据库", "待计算",
            "模型定义明确；统计类指标需运行 SQL 复核",
        ])

    overview = [["项目数据字典（结构版）"], ["生成日期", str(date.today())], ["技术栈", "FastAPI + SQLAlchemy + PostgreSQL"], ["模型来源", "backend/app/models/entities.py"], ["迁移基线", "backend/alembic/versions/0023_remove_exam_point_compatibility.py 之后的当前结构"], ["表数量", len(TABLE_INFO)], ["字段数量", len(fields)], ["统计说明", "总数量、非空非重复量、非空非重复率、空值量、空值率不能仅由代码确定，已保留待连接数据库状态。"], ["使用方式", "按‘表目录’了解业务域，按‘字段字典’维护字段，按‘关系与约束’维护外键/唯一/检查约束，按‘枚举与规则’维护状态值。"], ["更新方式", "模型或迁移变更后重新运行：python scripts/build_data_dictionary.py"], ["历史兼容说明", "exam_points、question_exam_points 属于已移除的旧兼容层，不纳入当前字典。"]]

    tables = [[
        "序号", "数据来源库名.表名/文件", "数据目标库名.表名", "中文说明", "业务域", "来源数据库",
        "目标数据库", "来源数据方式", "数据更新方式", "数据更新时间", "存储过程名称", "采集程序名称",
        "采集程序日志", "主键", "主要约束",
    ]]
    for no, (table, (zh, domain, desc, pk)) in enumerate(TABLE_INFO.items(), 1):
        source = TABLE_SOURCE[table]
        tables.append([
            no, source, f"PostgreSQL.public.{table}", desc, domain, "JSON/API/业务服务", "PostgreSQL",
            source, "按业务事件/导入任务", "实时或任务触发", "不固定（以服务日志为准）", "无",
            source.split("；")[0], "详见应用日志与审计字段", pk, TABLE_RULES[table],
        ])

    relations = [["关系类型", "表名", "字段/约束", "目标表/约束定义", "删除/业务规则"]]
    for table, field, target, rule in FK_RULES:
        target_table, target_field = target, "id"
        relations.append(["外键", table, field, f"{target_table}.{target_field}", rule])
    for table, field, values, meaning, source in ENUMS:
        relations.append(["枚举", table, field, values, f"{meaning}；{source}"])
    constraints = [
        ("唯一约束", "knowledge_points", "code", "UNIQUE"), ("唯一约束", "questions", "original_qid", "UNIQUE"),
        ("唯一约束", "school_classes", "name", "UNIQUE"), ("唯一约束", "students", "student_code", "UNIQUE"),
        ("复合唯一", "paper_instances", "assignment_id + student_id + version_no", "UNIQUE"),
        ("复合唯一", "paper_instance_items", "paper_instance_id + order_no", "UNIQUE"),
        ("复合唯一", "recognition_items", "recognition_run_id + paper_instance_item_id / order_no", "UNIQUE"),
        ("复合唯一", "attempt_revisions", "attempt_id + revision_no", "UNIQUE"),
        ("检查约束", "questions", "difficulty", "BETWEEN 1 AND 5"), ("检查约束", "student_masteries", "mastery", "BETWEEN 0 AND 1"),
        ("检查约束", "recognition_items.confidence", "confidence", "NULL 或 BETWEEN 0 AND 1"),
        ("检查约束", "attempts.ocr_confidence", "ocr_confidence", "NULL 或 BETWEEN 0 AND 1"),
        ("索引", "questions", "chapter_code + question_type + difficulty", "ix_questions_filters"),
        ("索引", "attempts", "student_id + answered_at", "ix_attempts_student_answered"),
        ("触发器", "paper_instances / paper_instance_items", "已发布快照", "禁止修改/删除已发布试卷及题目快照"),
    ]
    for typ, table, field, definition in constraints:
        relations.append([typ, table, field, definition, "详见模型 __table_args__ 或 Alembic 迁移"])

    enum_sheet = [["表名", "字段", "允许值/范围", "中文含义", "实现依据"]] + [list(item) for item in ENUMS]

    stats_sql = [["用途", "SQL（在目标 PostgreSQL 数据库执行）"], ["单表总量", "SELECT COUNT(*) AS total_count FROM <table_name>;"], ["字段非空非重复量", "SELECT COUNT(DISTINCT <field_name>) FILTER (WHERE <field_name> IS NOT NULL) AS non_null_distinct_count FROM <table_name>;"], ["字段空值量", "SELECT COUNT(*) FILTER (WHERE <field_name> IS NULL) AS null_count FROM <table_name>;"], ["字段空值率", "SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE <field_name> IS NULL) / NULLIF(COUNT(*),0), 2) AS null_rate_pct FROM <table_name>;"], ["注意", "JSONB 字段的非空非重复统计建议按业务键或 JSONB::text 口径另行定义；不要直接套用所有字段。"]]
    return [("使用说明", overview), ("表目录", tables), ("字段字典", field_rows), ("关系与约束", relations), ("枚举与规则", enum_sheet), ("统计SQL", stats_sql)]


def _cell(value: Any, style: int = 0) -> str:
    text = "" if value is None else str(value)
    escaped = html.escape(text, quote=False)
    return f'<c s="{style}" t="inlineStr"><is><t xml:space="preserve">{escaped}</t></is></c>'


def _sheet_xml(rows: list[list[Any]], header: bool = True) -> str:
    max_cols = max((len(row) for row in rows), default=1)
    widths = []
    for col in range(max_cols):
        longest = max((len(str(row[col])) if col < len(row) and row[col] is not None else 0 for row in rows), default=8)
        widths.append(min(42, max(10, longest * 1.25 + 2)))
    cols_xml = '<cols>' + ''.join(f'<col min="{i + 1}" max="{i + 1}" width="{width:.1f}" customWidth="1"/>' for i, width in enumerate(widths)) + '</cols>'
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>', cols_xml, '<sheetData>']
    for ridx, row in enumerate(rows, 1):
        style = 1 if (header and ridx == 1) else 0
        cells = "".join(_cell(v, style) for v in row)
        out.append(f'<row r="{ridx}">{cells}</row>')
    out.append('</sheetData><autoFilter ref="A1:V%d"/><pageSetup orientation="landscape" fitToWidth="1"/></worksheet>' % max(1, len(rows)))
    return "".join(out)


def write_xlsx(sheets: list[tuple[str, list[list[Any]]]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        zf.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        zf.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + ''.join(f'<sheet name="{html.escape(name, quote=True)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(sheets, 1)) + '</sheets></workbook>')
        zf.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets)+1)) + '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        zf.writestr("xl/styles.xml", '<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="10"/><name val="Microsoft YaHei"/></font><font><b/><sz val="10"/><name val="Microsoft YaHei"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="solid"><fgColor rgb="D9EAF7"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="1" borderId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs></styleSheet>')
        for i, (_, rows) in enumerate(sheets, 1):
            zf.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(rows))


def main() -> None:
    sheets = build_sheets()
    write_xlsx(sheets, OUTPUT_PATH)
    print(f"wrote {OUTPUT_PATH} ({len(extract_fields())} fields, {len(TABLE_INFO)} tables)")


if __name__ == "__main__":
    main()
