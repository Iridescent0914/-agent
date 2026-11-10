"""Generate the S0 PDF page annotation index from repo data.

The CSV is not a human-reviewed gold standard. Run from any working directory.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from pypdf import PdfReader


HERE = Path(__file__).resolve().parent
CODE_ROOT = HERE.parents[3]
PDF_ROOT = CODE_ROOT.parent / "校本" / "题"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    pdf_files = sorted(PDF_ROOT.glob("*.pdf"))
    if not pdf_files:
        raise FileNotFoundError(f"No PDF samples found in {PDF_ROOT}")
    fields = [
        "page_id", "source_file", "source_sha256", "pdf_role", "pair_group",
        "page_number", "page_count", "extracted_text_chars", "extraction_hint",
        "annotation_status", "gold_question_boundaries", "gold_answer_matches",
        "gold_main_kp_codes", "annotator_notes",
    ]
    page_count = 0
    csv_path = HERE / "评测候选清单.csv"
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8-sig", newline="") as existing:
            for row in csv.DictReader(existing):
                if row.get("annotation_status") not in ("", "pending") or any(
                    row.get(field) for field in ("gold_question_boundaries", "gold_answer_matches", "gold_main_kp_codes", "annotator_notes")
                ):
                    raise RuntimeError("Existing CSV has annotations; copy it aside before regenerating")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
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
    print(f"PDF annotation index: {len(pdf_files)} files, {page_count} pages")


if __name__ == "__main__":
    main()
