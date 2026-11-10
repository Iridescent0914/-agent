"""Generate the S0 pilot catalog and PDF page annotation index from repo data.

The CSV is not a human-reviewed gold standard. Run from any working directory.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

from pypdf import PdfReader


HERE = Path(__file__).resolve().parent
CODE_ROOT = HERE.parents[3]
GRAPH = CODE_ROOT / "上海市五四制初中数学知识图谱.html"
PDF_ROOT = CODE_ROOT.parent / "校本" / "题"
STEMS = ("rational", "expr")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    html = GRAPH.read_text(encoding="utf-8")
    mapping_source = html.split("const SECTION_BY_KP = [", 1)[1].split("];", 1)[0]
    maps = [
        [int(value) for value in re.findall(r"\d+", match.group(1))]
        for match in re.finditer(r"/\*[^*]+\*/\s*(\[[0-9, ]+\])", mapping_source)
    ]
    html_kp_names = re.findall(
        r'^\s*\["([^"]+)","[^"]+","[^"]+","[^"]+"\]', html, re.M
    )
    html_chapters = re.findall(r'\{grade:"([^"]+)",ch:"([^"]+)"', html)
    assert len(maps) == len(html_chapters) == 41, "HTML chapter mapping shape changed"

    chapters = []
    source_hashes = {str(GRAPH.relative_to(CODE_ROOT)): digest(GRAPH)}
    verified_question_count = 0
    name_offset = 0
    for chapter_index, stem in enumerate(STEMS):
        tree_path = CODE_ROOT / "data" / "seed" / f"{stem}_knowledge_tree.json"
        questions_path = CODE_ROOT / "data" / "interim" / f"{stem}_all.json"
        tree = load_json(tree_path)
        questions = load_json(questions_path)
        source_hashes[str(tree_path.relative_to(CODE_ROOT))] = digest(tree_path)
        kps, sections, indexes = tree["knowledge_points"], tree["sections"], maps[chapter_index]
        assert len(kps) == len(indexes), f"{stem}: KP mapping count mismatch"
        assert (tree["grade"], tree["chapter"]["name"]) == html_chapters[chapter_index]
        catalog_kps = []
        for kp, section_index in zip(kps, indexes):
            assert kp["name"] == html_kp_names[name_offset], f"{stem}: KP order/name mismatch"
            assert 0 <= section_index < len(sections), f"{stem}: invalid section index"
            assert kp["section"] == sections[section_index]["name"], f"{stem}: KP section drift"
            catalog_kps.append(
                {
                    "code": kp["id"],
                    "name": kp["name"],
                    "section": sections[section_index]["name"],
                    "core_competencies": kp["core_competencies"],
                    "curriculum_basis": kp["curriculum_basis"],
                }
            )
            name_offset += 1
        section_by_kp = {kp["code"]: kp["section"] for kp in catalog_kps}
        assert all(
            q["section"] == section_by_kp[q["main_knowledge_point"]["id"]]
            for q in questions
        ), f"{stem}: existing question section conflicts with HTML"
        chapters.append(
            {
                "grade": tree["grade"],
                "chapter_code": tree["chapter"]["id"],
                "chapter_name": tree["chapter"]["name"],
                "sections": [s["name"] for s in sections],
                "knowledge_points": catalog_kps,
            }
        )
        verified_question_count += len(questions)
    catalog = {
        "schema_version": "s0-label-catalog-v1",
        "status": "pilot_snapshot_not_runtime_database",
        "source_sha256": source_hashes,
        "chapters": chapters,
    }
    (HERE / "标签目录快照.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    pdf_files = sorted(PDF_ROOT.glob("*.pdf"))
    assert len(pdf_files) == 7, "PDF sample inventory changed; review evaluation scope"
    fields = [
        "page_id", "source_file", "source_sha256", "pdf_role", "pair_group",
        "page_number", "page_count", "extracted_text_chars", "extraction_hint",
        "annotation_status", "gold_question_boundaries", "gold_answer_matches",
        "gold_main_kp_codes", "annotator_notes",
    ]
    page_count = 0
    with (HERE / "评测候选清单.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for path in pdf_files:
            reader = PdfReader(str(path), strict=False)
            file_hash = digest(path)
            name = path.name
            if "习题解析" in name:
                role = "question_with_answer"
            elif "例题" in name:
                role = "example_questions"
            else:
                role = "questions_only"
            if "基础篇" in name and ("习题" in name):
                pair_group = "L7-basic-exercises"
            elif "拓展篇" in name and ("习题" in name):
                pair_group = "L7-advanced-exercises"
            else:
                pair_group = ""
            source_file = path.relative_to(CODE_ROOT.parent).as_posix()
            for page_number, page in enumerate(reader.pages, start=1):
                text_length = len((page.extract_text() or "").strip())
                writer.writerow(
                    {
                        "page_id": f"{file_hash[:12]}:{page_number}",
                        "source_file": source_file,
                        "source_sha256": file_hash,
                        "pdf_role": role,
                        "pair_group": pair_group,
                        "page_number": page_number,
                        "page_count": len(reader.pages),
                        "extracted_text_chars": text_length,
                        "extraction_hint": "low_text_check_visual" if text_length < 50 else "text_layer_present",
                        "annotation_status": "pending",
                        "gold_question_boundaries": "",
                        "gold_answer_matches": "",
                        "gold_main_kp_codes": "",
                        "annotator_notes": "",
                    }
                )
                page_count += 1
    print(f"catalog: {len(chapters)} chapters, {sum(len(c['knowledge_points']) for c in chapters)} KPs")
    print(f"verified existing question sections: {verified_question_count}")
    print(f"PDF annotation index: {len(pdf_files)} files, {page_count} pages")


if __name__ == "__main__":
    main()
