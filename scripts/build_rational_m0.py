#!/usr/bin/env python3
"""Build the M0 rational-number question dataset from the reviewed DOCX.

The source DOCX is an Office Open XML zip archive.  This script intentionally
uses only the Python standard library so the first data pass is reproducible in
the current environment without installing python-docx.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import posixpath
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET
from zipfile import ZipFile


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
NS = {"w": W_NS, "m": M_NS}

FIELD_HEADINGS = {
    "题干": "question_text",
    "参考答案": "reference_answer",
    "答案": "reference_answer",
    "解析": "solution",
    "易错分析": "wrong_analysis",
}

QUESTION_TYPE_MAP = {
    "选择题": "choice",
    "填空题": "fill_blank",
    "解答题": "solution",
    "计算题": "calculation",
    "证明题": "proof",
}

LABELS = {
    "题目 ID": "original_qid",
    "题型": "question_type_source",
    "难度": "difficulty_source",
    "主属 KP": "main_kp_source",
    "关联 KP": "related_kp_source",
    "主属考点": "main_exam_point_source",
    "关联考点": "related_exam_point_source",
    "预估得分率": "estimated_score_rate",
    "分值": "score",
}


@dataclass
class ParsedQuestion:
    number: int
    metadata: dict[str, str]
    fields: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    has_image: bool = False
    has_equation: bool = False


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def compact_space(value: str) -> str:
    value = value.replace("\u00a0", " ").replace("\u3000", " ")
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()


def render_math(node: ET.Element) -> str:
    """Render common OMML constructs to readable plain math text."""
    name = local_name(node.tag)
    if name in {"t", "delText"}:
        return node.text or ""
    if name == "tab":
        return "\t"
    if name == "br":
        return "\n"
    if name in {"drawing", "pict", "object"}:
        relationship_ids = []
        for descendant in node.iter():
            for attr_name, attr_value in descendant.attrib.items():
                if local_name(attr_name) in {"embed", "id"} and attr_value.startswith("rId"):
                    relationship_ids.append(attr_value)
        relationship_id = relationship_ids[0] if relationship_ids else "unknown"
        return f"[ASSET:{relationship_id}]"

    def child_text(child_name: str) -> str:
        child = next((item for item in node if local_name(item.tag) == child_name), None)
        return render_math(child).strip() if child is not None else ""

    if name == "f":
        numerator = child_text("num")
        denominator = child_text("den")
        return f"({numerator})/({denominator})"
    if name == "sSup":
        return f"{child_text('e')}^({child_text('sup')})"
    if name == "sSub":
        return f"{child_text('e')}_({child_text('sub')})"
    if name == "sSubSup":
        return f"{child_text('e')}_({child_text('sub')})^({child_text('sup')})"
    if name == "rad":
        degree = child_text("deg")
        body = child_text("e")
        return f"root[{degree}]({body})" if degree else f"sqrt({body})"
    if name == "nary":
        body = child_text("e")
        sub = child_text("sub")
        sup = child_text("sup")
        limits = ""
        if sub:
            limits += f"_({sub})"
        if sup:
            limits += f"^({sup})"
        return f"NARY{limits}({body})"
    if name == "d":
        bodies = [render_math(item).strip() for item in node if local_name(item.tag) == "e"]
        return "(" + ",".join(item for item in bodies if item) + ")"

    return "".join(render_math(child) for child in node)


def element_text(node: ET.Element) -> str:
    return compact_space(render_math(node))


def table_metadata(table: ET.Element) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in table.findall("w:tr", NS):
        cells = [element_text(cell) for cell in row.findall("w:tc", NS)]
        if len(cells) < 2:
            continue
        label = compact_space(cells[0])
        key = LABELS.get(label)
        if key:
            result[key] = compact_space(cells[1])
    return result


def parse_review_docx(source: Path) -> list[ParsedQuestion]:
    with ZipFile(source) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))

    body = root.find("w:body", NS)
    if body is None:
        raise ValueError("DOCX 中缺少 word/document.xml 的 w:body")

    questions: list[ParsedQuestion] = []
    current: ParsedQuestion | None = None
    current_field: str | None = None

    for node in body:
        name = local_name(node.tag)
        if name == "tbl":
            metadata = table_metadata(node)
            if metadata.get("original_qid"):
                current = ParsedQuestion(number=len(questions) + 1, metadata=metadata)
                questions.append(current)
                current_field = None
            continue

        if name != "p" or current is None:
            continue

        text = element_text(node)
        normalized_heading = re.sub(r"[：:]$", "", text).strip()
        if normalized_heading in FIELD_HEADINGS:
            current_field = FIELD_HEADINGS[normalized_heading]
            continue

        if current_field and text:
            current.fields[current_field].append(text)
            current.has_image = current.has_image or "[ASSET:" in text
            current.has_equation = current.has_equation or node.find(".//m:oMath", NS) is not None

    return questions


def parse_named_id(value: str) -> tuple[str, str | None]:
    value = compact_space(value)
    if not value or value in {"—", "-", "/"}:
        return "", None
    match = re.match(r"^(.*?)\s*[（(]\s*([a-zA-Z][\w.-]+)\s*[）)]\s*$", value)
    if match:
        return compact_space(match.group(1)), match.group(2)
    return value, None


def split_related(value: str) -> list[dict[str, str | None]]:
    if not value or compact_space(value) in {"—", "-", "/"}:
        return []
    parts = re.split(r"[；;\n]+", value)
    result = []
    for part in parts:
        name, item_id = parse_named_id(part)
        if name:
            result.append({"id": item_id, "name": name})
    return result


def strip_ai_marker(value: str) -> str:
    return re.sub(r"^【AI\s*草拟[^】]*】\s*", "", value.strip())


def count_blanks(text: str) -> int:
    underline = len(re.findall(r"_{3,}|＿{2,}", text))
    bracket = len(re.findall(r"[（(]\s{2,}[）)]", text))
    return underline + bracket


def has_subquestions(text: str, answer: str) -> bool:
    combined = text + "\n" + answer
    patterns = [
        r"（\s*[1-9]\d*\s*）",
        r"(?m)^\s*\(\s*[1-9]\d*\s*\)",
        r"(?m)^\s*[1-9]\d*[）)]",
        r"[①②③④⑤⑥⑦⑧⑨⑩]",
    ]
    return any(len(re.findall(pattern, combined)) >= 2 for pattern in patterns)


def parse_options(text: str) -> dict[str, str]:
    normalized = text.replace("\t", " ")
    matches = list(
        re.finditer(
            r"(?<![A-Za-z])(?:[（(]\s*([A-D])\s*[）)]|([A-D])[\.．、:])\s*",
            normalized,
        )
    )
    if len(matches) < 2:
        return {}
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        value = compact_space(normalized[match.end():end]).rstrip("；;。")
        label = match.group(1) or match.group(2)
        if label not in result and value:
            result[label] = value
    return result


def extract_choice_answer(answer: str) -> str | None:
    cleaned = strip_ai_marker(answer)
    cleaned = re.sub(r"^\s*答案\s*[：:]\s*", "", cleaned)
    match = re.match(r"^\s*(?:[（(]?\s*\d+\s*[）)]\s*)?([A-D])(?:\b|[。．、，,；;：:])", cleaned)
    return match.group(1) if match else None


def extract_fill_answer(answer: str) -> str | None:
    cleaned = strip_ai_marker(answer)
    cleaned = re.sub(r"^\s*答案\s*[：:]\s*", "", cleaned)
    if not cleaned or has_subquestions("", cleaned):
        return None
    first = re.split(r"[。\n]", cleaned, maxsplit=1)[0].strip()
    first = re.sub(r"^[：:]", "", first).strip()
    if not first or len(first) > 80 or re.search(r"[；;]", first):
        return None
    return first


def simple_fill_values(segment: str) -> str | list[str] | None:
    first = re.split(r"[。\n]", strip_ai_marker(segment), maxsplit=1)[0].strip()
    parts = [compact_space(item) for item in re.split(r"[；;]", first) if compact_space(item)]
    if not parts or any(len(item) > 24 for item in parts):
        return None
    explanatory = ("等于", "得", "故", "因为", "取", "并把", "时", "分别为", "答案")
    if any(any(word in item for word in explanatory) for item in parts):
        return None
    return parts[0] if len(parts) == 1 else parts


def split_answer_items(answer: str, question_type: str, question_text: str = "") -> list[dict]:
    cleaned = strip_ai_marker(answer)
    circled = {char: str(index + 1) for index, char in enumerate("①②③④⑤⑥⑦⑧⑨⑩")}
    pattern = re.compile(r"(?:[（(]\s*(\d+)\s*[）)]|([①②③④⑤⑥⑦⑧⑨⑩]))")
    matches = list(pattern.finditer(cleaned))

    def make_item(item_id: str, raw: str) -> dict:
        raw = re.sub(r"^\s*\d+[.．、]\s*", "", raw).strip(" \n。；;")
        if question_type == "choice":
            value = extract_choice_answer(raw)
            item_match_type = "choice" if value else None
        else:
            value = simple_fill_values(raw)
            if isinstance(value, str) and numeric_answer(value):
                item_match_type = "numeric"
            elif value is not None:
                item_match_type = "exact"
            else:
                item_match_type = None
        return {
            "item_id": item_id,
            "answer": value,
            "answer_match_type": item_match_type,
            "reference_answer_raw": raw,
        }

    if len(matches) < 2:
        blank_total = count_blanks(question_text)
        first_answer_line = re.split(r"[。\n]", cleaned, maxsplit=1)[0].strip()
        flat_values = [compact_space(value) for value in re.split(r"[，,；;]", first_answer_line) if compact_space(value)]
        if question_type == "fill_blank" and blank_total > 1 and len(flat_values) == blank_total:
            return [make_item(f"blank_{index}", value) for index, value in enumerate(flat_values, start=1)]

        question_markers = re.findall(r"[（(]\s*(\d+)\s*[）)]", question_text)
        answer_lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        if len(question_markers) >= 2 and len(answer_lines) == len(question_markers):
            return [make_item(item_id, raw) for item_id, raw in zip(question_markers, answer_lines)]
        return []

    items = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
        raw = cleaned[match.end():end].strip(" \n。；;")
        item_id = match.group(1) or circled[match.group(2)]
        items.append(make_item(item_id, raw))
    return items


def numeric_answer(answer: str) -> bool:
    normalized = answer.strip().replace("−", "-").replace("＋", "+").replace("％", "%")
    return bool(
        re.fullmatch(r"[+-]?(?:\d+(?:\.\d+)?|\d+\s*/\s*\d+)(?:%|℃|度|米|元)?", normalized)
    )


def deterministic_answer_structure(correct_answer: object) -> bool:
    if correct_answer is None:
        return False
    if "[ASSET:" in json.dumps(correct_answer, ensure_ascii=False):
        return False
    if isinstance(correct_answer, dict) and correct_answer.get("kind") == "group":
        items = correct_answer.get("items") or []
        item_ids = [item.get("item_id") for item in items]
        if not items or len(item_ids) != len(set(item_ids)):
            return False
        return all(item.get("answer") is not None and item.get("answer_match_type") for item in items)
    return True


def stable_exam_id(chapter_id: str, name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    return f"{chapter_id}.exam.local.{digest}"


def normalized_type(source_type: str) -> str:
    for source, target in QUESTION_TYPE_MAP.items():
        if source in source_type:
            return target
    return "unknown"


def infer_type(source_type: str, question_text: str) -> tuple[str, bool]:
    declared = normalized_type(source_type)
    prefix = compact_space(question_text[:100])
    inferred = None
    if re.search(r"(?:^|[、：:])\s*选择\s*[：:]", prefix):
        inferred = "choice"
    elif re.search(r"(?:^|[、：:])\s*填空\s*[：:]", prefix):
        inferred = "fill_blank"
    elif re.search(r"(?:^|[、：:])\s*判断\s*[：:]", prefix):
        inferred = "true_false"
    elif re.search(r"(?:^|[、：:])\s*计算\s*[：:]", prefix):
        inferred = "calculation"
    if inferred is None and len(parse_options(question_text)) >= 2:
        inferred = "choice"
    if inferred and inferred != declared:
        return inferred, True
    return declared, False


def to_record(parsed: ParsedQuestion, source: Path, project_root: Path) -> dict:
    metadata = parsed.metadata
    question_text = "\n".join(parsed.fields.get("question_text", [])).strip()
    reference_answer = "\n".join(parsed.fields.get("reference_answer", [])).strip()
    solution = "\n".join(parsed.fields.get("solution", [])).strip()
    wrong_analysis = "\n".join(parsed.fields.get("wrong_analysis", [])).strip()
    qtype, type_mismatch = infer_type(metadata.get("question_type_source", ""), question_text)

    main_kp_name, main_kp_id = parse_named_id(metadata.get("main_kp_source", ""))
    main_exam_name, main_exam_id = parse_named_id(metadata.get("main_exam_point_source", ""))
    chapter_id = "math.g6.rational"
    if main_exam_name and not main_exam_id:
        main_exam_id = stable_exam_id(chapter_id, main_exam_name)

    difficulty = metadata.get("difficulty_source", "").count("★") or None
    multiple = has_subquestions(question_text, reference_answer) or count_blanks(question_text) > 1
    if qtype == "choice" and extract_choice_answer(reference_answer):
        option_marker_count = len(re.findall(r"(?:^|\s)[A-D][.．、]", question_text))
        if option_marker_count <= 4:
            multiple = False
    options = parse_options(question_text) if qtype == "choice" and not multiple else {}
    correct_answer = None
    match_type = None
    grading_readiness = "raw_answer_only"

    if multiple and qtype in {"choice", "fill_blank"}:
        answer_items = split_answer_items(reference_answer, qtype, question_text)
        if not answer_items:
            answer_items = [
                {
                    "item_id": "whole",
                    "answer": None,
                    "answer_match_type": None,
                    "reference_answer_raw": strip_ai_marker(reference_answer),
                }
            ]
        correct_answer = {"kind": "group", "items": answer_items}
        match_type = "group"
        if all(item["answer"] is not None for item in answer_items):
            grading_readiness = "machine_ready"
    elif qtype == "choice":
        correct_answer = extract_choice_answer(reference_answer)
        match_type = "choice" if correct_answer else None
        grading_readiness = "machine_ready" if correct_answer else "raw_answer_only"
    elif qtype == "fill_blank":
        correct_answer = extract_fill_answer(reference_answer)
        match_type = "numeric" if correct_answer and numeric_answer(correct_answer) else "exact"
        grading_readiness = "machine_ready" if correct_answer else "raw_answer_only"

    if grading_readiness == "machine_ready" and not deterministic_answer_structure(correct_answer):
        grading_readiness = "raw_answer_only"

    issues: list[str] = []
    if not question_text:
        issues.append("missing_question_text")
    if not reference_answer:
        issues.append("missing_reference_answer")
    if not main_kp_name:
        issues.append("missing_main_knowledge_point")
    if not main_exam_name:
        issues.append("missing_main_exam_point")
    if difficulty is None:
        issues.append("missing_difficulty")
    if parsed.has_image:
        issues.append("contains_asset")
    if multiple:
        issues.append("multiple_subquestions")
    if type_mismatch:
        issues.append("question_type_mismatch")
    if qtype == "choice" and not multiple and len(options) < 2:
        issues.append("options_not_structured")
    if qtype in {"choice", "fill_blank"} and grading_readiness != "machine_ready":
        issues.append("answer_items_not_structured")
    if "【AI 草拟" in reference_answer or "【AI 草拟" in wrong_analysis:
        issues.append("source_contains_ai_draft_marker")

    if match_type == "choice":
        rules = {"trim_whitespace": True, "normalize_fullwidth_symbols": True, "uppercase": True}
    elif match_type == "numeric":
        rules = {
            "trim_whitespace": True,
            "normalize_fullwidth_symbols": True,
            "allow_fraction_decimal_equivalence": False,
            "allow_equivalent_fraction": False,
            "require_simplified_fraction": False,
            "require_unit": bool(re.search(r"[℃度米元]$", correct_answer or "")),
        }
    elif match_type == "group":
        rules = {
            "item_order_matters": True,
            "partial_credit": True,
            "unparsed_items_use_reference_answer_raw": True,
        }
    else:
        rules = {"trim_whitespace": True, "normalize_fullwidth_symbols": True}

    import_status = "parsed"
    if any(issue in issues for issue in ("answer_items_not_structured", "question_type_mismatch")):
        import_status = "needs_technical_cleanup"

    return {
        "schema_version": "1.2",
        "original_qid": metadata.get("original_qid", ""),
        "chapter": {"id": chapter_id, "name": "有理数"},
        "section": main_kp_name or None,
        "question_type": qtype,
        "question_type_source": metadata.get("question_type_source", ""),
        "difficulty": difficulty,
        "question_text": question_text,
        "options": options or None,
        "correct_answer": correct_answer,
        "reference_answer_raw": reference_answer,
        "answer_match_type": match_type,
        "answer_normalization_rule": rules,
        "grading_readiness": grading_readiness,
        "main_knowledge_point": {"id": main_kp_id, "name": main_kp_name},
        "related_knowledge_points": split_related(metadata.get("related_kp_source", "")),
        "main_exam_point": {"id": main_exam_id, "name": main_exam_name},
        "related_exam_points": split_related(metadata.get("related_exam_point_source", "")),
        "solution": solution or None,
        "wrong_analysis": strip_ai_marker(wrong_analysis) or None,
        "estimated_score_rate": metadata.get("estimated_score_rate") or None,
        "has_image": parsed.has_image,
        "has_equation": parsed.has_equation,
        "assets": [],
        "source_file": source.relative_to(project_root).as_posix(),
        "source_question_number": parsed.number,
        "source_review_status": "reviewed",
        "import_status": import_status,
        "import_issues": issues,
        "is_seed_candidate": False,
    }


def apply_answer_structures(records: list[dict], structure_path: Path) -> int:
    if not structure_path.is_file():
        return 0
    payload = json.loads(structure_path.read_text(encoding="utf-8-sig"))
    overrides = payload.get("questions", {})
    by_qid = {record["original_qid"]: record for record in records}
    applied = 0
    for qid, override in overrides.items():
        if qid not in by_qid:
            raise ValueError(f"答案结构映射引用了不存在的题目：{qid}")
        items = override.get("items") or []
        correct_answer = {"kind": "group", "items": items}
        if not deterministic_answer_structure(correct_answer):
            raise ValueError(f"答案结构映射不满足确定性判分要求：{qid}")
        record = by_qid[qid]
        record["correct_answer"] = correct_answer
        record["answer_match_type"] = "group"
        record["answer_normalization_rule"] = {
            "item_order_matters": True,
            "partial_credit": True,
            "unparsed_items_use_reference_answer_raw": False,
        }
        record["grading_readiness"] = "machine_ready"
        record["answer_structure_source"] = "source_derived_override"
        record["import_issues"] = [issue for issue in record["import_issues"] if issue != "answer_items_not_structured"]
        if "question_type_mismatch" not in record["import_issues"]:
            record["import_status"] = "parsed"
        applied += 1
    return applied


def eligible_for_seed(record: dict) -> bool:
    blocking = {
        "missing_question_text",
        "missing_reference_answer",
        "missing_main_knowledge_point",
        "missing_main_exam_point",
        "missing_difficulty",
    }
    if record["question_type"] not in {"choice", "fill_blank"}:
        return False
    if record["grading_readiness"] != "machine_ready":
        return False
    if blocking.intersection(record["import_issues"]):
        return False
    if not record["correct_answer"]:
        return False
    if (
        record["question_type"] == "choice"
        and record["answer_match_type"] == "choice"
        and record["correct_answer"] not in (record["options"] or {})
    ):
        return False
    return True


def select_diverse(records: list[dict], limit: int) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        if eligible_for_seed(record):
            groups[record["main_exam_point"]["id"]].append(record)

    for items in groups.values():
        items.sort(
            key=lambda record: (
                record["grading_readiness"] != "machine_ready",
                record["has_image"],
                "source_contains_ai_draft_marker" in record["import_issues"],
                record["source_question_number"],
            )
        )

    selected: list[dict] = []
    ranked_keys = sorted(groups, key=lambda key: (len(groups[key]), key), reverse=True)
    keys = ranked_keys[:10]
    while keys and len(selected) < limit:
        next_keys = []
        for key in keys:
            if groups[key] and len(selected) < limit:
                selected.append(groups[key].pop(0))
            if groups[key]:
                next_keys.append(key)
        keys = next_keys
    for record in selected:
        record["is_seed_candidate"] = True
    return selected


def build_knowledge_tree(records: Iterable[dict]) -> dict:
    kp_map: dict[str, dict] = {}
    for record in records:
        kp = record["main_knowledge_point"]
        if not kp["name"]:
            continue
        kp_id = kp["id"] or f"math.g6.rational.kp.local.{hashlib.sha1(kp['name'].encode('utf-8')).hexdigest()[:10]}"
        node = kp_map.setdefault(kp_id, {"id": kp_id, "name": kp["name"], "exam_points": {}})
        exam = record["main_exam_point"]
        if exam["name"]:
            node["exam_points"].setdefault(exam["id"], {"id": exam["id"], "name": exam["name"]})
    knowledge_points = []
    for node in sorted(kp_map.values(), key=lambda item: (item["name"], item["id"])):
        node["exam_points"] = sorted(node["exam_points"].values(), key=lambda item: (item["name"], item["id"]))
        knowledge_points.append(node)
    return {
        "schema_version": "1.0",
        "chapter": {"id": "math.g6.rational", "name": "有理数"},
        "knowledge_points": knowledge_points,
        "notes": [
            "层级来自校本审核 DOCX 的主属 KP 与主属考点字段。",
            "源文件未提供考点 ID 时，使用基于考点名称生成的稳定 local ID；教师确认正式编号后可迁移。",
            "M0 仅固定首版结构，不代表全部考点已经完成教学专家审核。",
        ],
    }


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def replace_asset_markers(value, replacements: dict[str, str]):
    if isinstance(value, str):
        for relationship_id, asset_path in replacements.items():
            value = value.replace(f"[ASSET:{relationship_id}]", f"[ASSET:{asset_path}]")
        return value
    if isinstance(value, list):
        return [replace_asset_markers(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: replace_asset_markers(item, replacements) for key, item in value.items()}
    return value


def export_assets(source: Path, output_root: Path, records: list[dict]) -> dict:
    asset_root = output_root / "assets" / "rational"
    asset_root.mkdir(parents=True, exist_ok=True)
    extension_counts: Counter[str] = Counter()
    exported = 0

    with ZipFile(source) as archive:
        rels_root = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
        relationships = {
            relation.attrib["Id"]: relation.attrib
            for relation in rels_root
            if relation.attrib.get("Id")
        }
        archive_names = set(archive.namelist())

        for record in records:
            searchable = "\n".join(
                value
                for value in (
                    record.get("question_text"),
                    record.get("reference_answer_raw"),
                    record.get("solution"),
                    record.get("wrong_analysis"),
                )
                if isinstance(value, str)
            )
            if "[ASSET:unknown]" in searchable:
                record["import_issues"].append("asset_relationship_missing")
                record["import_status"] = "needs_technical_cleanup"
            relationship_ids = sorted(set(re.findall(r"\[ASSET:(rId\d+)\]", searchable)))
            replacements: dict[str, str] = {}
            assets = []
            for relationship_id in relationship_ids:
                relation = relationships.get(relationship_id)
                if not relation or relation.get("TargetMode") == "External":
                    record["import_issues"].append("asset_relationship_missing")
                    record["import_status"] = "needs_technical_cleanup"
                    continue
                target = relation.get("Target", "")
                archive_path = posixpath.normpath(posixpath.join("word", target))
                if not archive_path.startswith("word/") or archive_path not in archive_names:
                    record["import_issues"].append("asset_file_missing")
                    record["import_status"] = "needs_technical_cleanup"
                    continue
                suffix = Path(target).suffix.lower() or ".bin"
                filename = f"{record['original_qid']}_{relationship_id}{suffix}"
                destination = asset_root / filename
                destination.write_bytes(archive.read(archive_path))
                relative_path = destination.relative_to(output_root).as_posix()
                replacements[relationship_id] = relative_path
                assets.append(
                    {
                        "relationship_id": relationship_id,
                        "path": relative_path,
                        "extension": suffix,
                    }
                )
                extension_counts[suffix] += 1
                exported += 1
                if suffix in {".emf", ".wmf"}:
                    record["import_issues"].append("asset_needs_web_conversion")
                    record["import_status"] = "needs_technical_cleanup"

            for key in ("question_text", "reference_answer_raw", "solution", "wrong_analysis", "correct_answer"):
                record[key] = replace_asset_markers(record.get(key), replacements)
            record["assets"] = assets

    return {
        "exported_asset_references": exported,
        "asset_extension_counts": dict(sorted(extension_counts.items())),
    }


def write_audit_csv(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "source_review_status",
        "import_status",
        "include_in_seed",
        "original_qid",
        "source_question_number",
        "question_type",
        "difficulty",
        "main_knowledge_point",
        "main_exam_point",
        "question_preview",
        "extracted_answer",
        "answer_match_type",
        "grading_readiness",
        "has_equation",
        "has_image",
        "asset_paths",
        "import_issues",
        "corrected_answer",
        "technical_note",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "source_review_status": record["source_review_status"],
                    "import_status": record["import_status"],
                    "include_in_seed": "yes" if record["is_seed_candidate"] else "no",
                    "original_qid": record["original_qid"],
                    "source_question_number": record["source_question_number"],
                    "question_type": record["question_type"],
                    "difficulty": record["difficulty"],
                    "main_knowledge_point": record["main_knowledge_point"]["name"],
                    "main_exam_point": record["main_exam_point"]["name"],
                    "question_preview": record["question_text"].replace("\n", " ")[:160],
                    "extracted_answer": json.dumps(record["correct_answer"], ensure_ascii=False) if record["correct_answer"] is not None else "",
                    "answer_match_type": record["answer_match_type"] or "",
                    "grading_readiness": record["grading_readiness"],
                    "has_equation": record["has_equation"],
                    "has_image": record["has_image"],
                    "asset_paths": ";".join(asset["path"] for asset in record["assets"]),
                    "import_issues": ";".join(record["import_issues"]),
                    "corrected_answer": "",
                    "technical_note": "",
                }
            )


def find_default_source(project_root: Path) -> Path:
    candidates = [
        path
        for path in project_root.rglob("rational_*校本审核*.docx")
        if "__MACOSX" not in path.parts
    ]
    if len(candidates) != 1:
        raise FileNotFoundError(f"预期找到 1 个有理数校本审核 DOCX，实际找到 {len(candidates)} 个：{candidates}")
    return candidates[0]


def build(source: Path, output_root: Path, project_root: Path, limit: int) -> dict:
    parsed = parse_review_docx(source)
    records = [to_record(item, source, project_root) for item in parsed]
    structure_path = output_root / "config" / "rational_answer_structures.json"
    answer_structure_override_count = apply_answer_structures(records, structure_path)
    qids = [record["original_qid"] for record in records]
    duplicate_qids = sorted(qid for qid, count in Counter(qids).items() if count > 1)
    if duplicate_qids:
        raise ValueError(f"发现重复题目 ID：{duplicate_qids}")

    asset_report = export_assets(source, output_root, records)
    selected = select_diverse(records, limit=limit)
    tree = build_knowledge_tree(records)
    issue_counts = Counter(issue for record in records for issue in record["import_issues"])
    report = {
        "schema_version": "1.0",
        "source_file": source.relative_to(project_root).as_posix(),
        "total_extracted": len(records),
        "seed_candidate_count": len(selected),
        "eligible_count_before_limit": sum(eligible_for_seed(record) for record in records),
        "question_type_counts": dict(sorted(Counter(record["question_type"] for record in records).items())),
        "seed_type_counts": dict(sorted(Counter(record["question_type"] for record in selected).items())),
        "seed_exam_point_count": len({record["main_exam_point"]["id"] for record in selected}),
        "seed_exam_point_counts": dict(sorted(Counter(record["main_exam_point"]["name"] for record in selected).items())),
        "seed_machine_ready_count": sum(record["grading_readiness"] == "machine_ready" for record in selected),
        "seed_raw_answer_only_count": sum(record["grading_readiness"] == "raw_answer_only" for record in selected),
        "answer_structure_override_count": answer_structure_override_count,
        **asset_report,
        "issue_counts": dict(sorted(issue_counts.items())),
        "source_status_note": "校本审核 DOCX 作为权威来源，题目与参考答案默认已审核；import_status 仅描述程序抽取和结构化状态。",
    }

    write_json(output_root / "interim" / "rational_all.json", records)
    write_json(output_root / "seed" / "rational_questions.json", selected)
    write_json(output_root / "seed" / "rational_knowledge_tree.json", tree)
    write_audit_csv(output_root / "audit" / "rational_import_audit.csv", records)
    write_json(output_root / "audit" / "rational_m0_report.json", report)
    return report


def parse_args() -> argparse.Namespace:
    script = Path(__file__).resolve()
    default_project_root = script.parents[2]
    default_code_root = script.parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="有理数校本审核 DOCX；默认自动查找")
    parser.add_argument("--output-root", type=Path, default=default_code_root / "data")
    parser.add_argument("--project-root", type=Path, default=default_project_root)
    parser.add_argument("--limit", type=int, default=40, help="首批候选题上限，必须为 30–50")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 30 <= args.limit <= 50:
        raise SystemExit("--limit 必须在 30–50 之间")
    project_root = args.project_root.resolve()
    source = args.source.resolve() if args.source else find_default_source(project_root)
    report = build(source, args.output_root.resolve(), project_root, args.limit)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
