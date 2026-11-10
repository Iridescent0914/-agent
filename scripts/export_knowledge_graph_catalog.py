"""Export the full HTML knowledge graph as the runtime JSON catalog.

The HTML file is an authoring source. Runtime services read the exported JSON.
Use --check in development/CI after editing the HTML or pilot seed trees.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


CODE_ROOT = Path(__file__).resolve().parents[1]
GRAPH = CODE_ROOT / "上海市五四制初中数学知识图谱.html"
OUTPUT = CODE_ROOT / "data" / "knowledge_graph_catalog.json"
SEED_STEMS = ("rational", "expr")


def _constant_array(html: str, name: str) -> list[str]:
    match = re.search(rf"const {name}\s*=\s*(\[[^;]+\]);", html)
    if match is None:
        raise ValueError(f"Missing HTML array {name}")
    return json.loads(match.group(1))


def build_catalog() -> dict:
    html = GRAPH.read_text(encoding="utf-8")
    mapping_source = html.split("const SECTION_BY_KP = [", 1)[1].split("];", 1)[0]
    mappings = [
        json.loads(match.group(1))
        for match in re.finditer(r"/\*[^*]+\*/\s*(\[[0-9, ]+\])", mapping_source)
    ]
    chapter_pattern = re.compile(
        r'\{grade:"([^"]+)",ch:"([^"]+)",domain:"([^"]+)",\s*'
        r'sec:(\[[^\]]*\]),\s*kps:\[(.*?)\]\s*\}',
        re.S,
    )
    matches = list(chapter_pattern.finditer(html))
    if len(matches) != len(mappings) or not matches:
        raise ValueError("HTML chapters do not align with SECTION_BY_KP")

    grades = _constant_array(html, "GRADES")
    domains = _constant_array(html, "DOMAINS")
    levels = _constant_array(html, "LEVELS")
    literacies = _constant_array(html, "COMPETENCIES")
    pilot = {}
    for stem in SEED_STEMS:
        tree = json.loads(
            (CODE_ROOT / "data" / "seed" / f"{stem}_knowledge_tree.json").read_text(encoding="utf-8")
        )
        pilot[(tree["grade"], tree["chapter"]["name"])] = tree

    chapters = []
    seen = set()
    for order, (match, indexes) in enumerate(zip(matches, mappings), start=1):
        grade, name, domain = match.group(1, 2, 3)
        if grade not in grades or domain not in domains or (grade, name) in seen:
            raise ValueError(f"Invalid or duplicate HTML chapter: {grade} {name}")
        seen.add((grade, name))
        sections = json.loads(match.group(4))
        kp_rows = json.loads("[" + match.group(5).strip().rstrip(",") + "]")
        if len(kp_rows) != len(indexes):
            raise ValueError(f"KP count differs from SECTION_BY_KP: {grade} {name}")
        tree = pilot.get((grade, name))
        if tree is not None:
            if [s["name"] for s in tree["sections"]] != sections:
                raise ValueError(f"Pilot section names differ from HTML: {grade} {name}")
            if len(tree["knowledge_points"]) != len(kp_rows):
                raise ValueError(f"Pilot KP count differs from HTML: {grade} {name}")
        points = []
        for kp_order, (row, section_index) in enumerate(zip(kp_rows, indexes), start=1):
            if len(row) != 4 or row[1] not in levels or not isinstance(section_index, int) or not 0 <= section_index < len(sections):
                raise ValueError(f"Invalid HTML KP: {grade} {name} #{kp_order}")
            competencies = [item.strip() for item in row[2].split("、")]
            if not competencies or any(item not in literacies for item in competencies):
                raise ValueError(f"Invalid HTML literacy: {grade} {name} #{kp_order}")
            code = None
            if tree is not None:
                seeded = tree["knowledge_points"][kp_order - 1]
                if (
                    seeded["name"] != row[0]
                    or seeded["section"] != sections[section_index]
                    or seeded["mastery_level"] != row[1]
                    or seeded["core_competencies"] != competencies
                    or seeded["curriculum_basis"] != row[3]
                ):
                    raise ValueError(f"Pilot KP differs from HTML: {grade} {name} #{kp_order}")
                code = seeded["id"]
            points.append({
                "order": kp_order,
                "code": code,
                "name": row[0],
                "section": sections[section_index],
                "mastery_level": row[1],
                "core_competencies": competencies,
                "curriculum_basis": row[3],
            })
        chapters.append({
            "order": order,
            "grade": grade,
            "name": name,
            "domain": domain,
            "code": tree["chapter"]["id"] if tree is not None else None,
            "available_for_import": tree is not None,
            "sections": sections,
            "knowledge_points": points,
        })
    if len(seen) != len(matches) or len(pilot) != sum(chapter["available_for_import"] for chapter in chapters):
        raise ValueError("Pilot chapters are missing from the HTML graph")
    return {
        "schema_version": "knowledge-graph-catalog-v1",
        "source_file": GRAPH.name,
        "source_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        "grades": grades,
        "domains": domains,
        "mastery_levels": levels,
        "html_core_competencies": literacies,
        "formal_question_labels": {
            "question_types": ["choice", "fill_blank", "calculation", "solution", "true_false"],
            "difficulty_levels": [1, 2, 3, 4, 5],
            "core_literacies": [
                "抽象能力", "运算能力", "几何直观", "推理能力",
                "模型观念", "数据意识", "应用意识", "创新意识",
            ],
            "is_required": [False, True],
        },
        "chapters": chapters,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Fail if the checked-in JSON differs from HTML and pilot seeds")
    args = parser.parse_args()
    payload = json.dumps(build_catalog(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != payload:
            raise SystemExit(f"Catalog is stale; run python {Path(__file__).name}")
    else:
        OUTPUT.write_text(payload, encoding="utf-8")
    catalog = json.loads(payload)
    print(
        f"catalog: {len(catalog['chapters'])} chapters, "
        f"{sum(len(ch['knowledge_points']) for ch in catalog['chapters'])} KPs, "
        f"{sum(ch['available_for_import'] for ch in catalog['chapters'])} active chapters"
    )


if __name__ == "__main__":
    main()
