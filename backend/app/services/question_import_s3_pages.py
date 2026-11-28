"""Page planning for separate answer PDFs; every answer page uses the model."""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf as fitz

from app.services.question_import_s2 import plan_units


_NEW_SECTION = re.compile(r"^(?:L\d+\s*拓展篇)?第\s*[一二三四五六七八九十百\d]+\s*(?:讲|章|节)")
_NEW_ANSWER = re.compile(r"^[1-9]\d{0,2}\s*[.．、]")
_UNCERTAIN = re.compile(r"[�?？@【】]|\(cid:|[\ue000-\uf8ff]|(?:解析|分析|证明|过程|见图|如图|略)")


def _content_lines(page) -> list[str]:
    lines = [raw.strip() for raw in page.get_text("text", sort=True).splitlines() if raw.strip()]
    # The final standalone number is a footer only when its PDF position proves it.
    if lines and lines[-1].isdecimal():
        footer = lines[-1]
        for block in page.get_text("dict", sort=True)["blocks"]:
            for row in block.get("lines", []):
                value = "".join(span["text"] for span in row.get("spans", [])).strip()
                if value == footer and row["bbox"][1] >= page.rect.height * 0.93:
                    lines.pop()
                    break
            else:
                continue
            break
    return [line for line in lines if not line.startswith("@")]


def _closed_boundary(page, following) -> bool:
    """Skip an overlap page only at a visibly closed answer boundary."""
    before = _content_lines(page)
    after = _content_lines(following)
    if not before or not after:
        return False
    if not re.search(r"[。.!！;；．]$", before[-1]) or _UNCERTAIN.search(before[-1]):
        return False
    return bool(_NEW_SECTION.match(after[0]) or _NEW_ANSWER.match(after[0]))


def plan_answer_units(pdf: Path, *, pages_per_unit: int, overlap: int) -> list[dict]:
    """Use the original core chunks, omitting only clearly unneeded context."""
    with fitz.open(str(pdf)) as document:
        units = plan_units(len(document), pages_per_unit=pages_per_unit, overlap=overlap)
        result = []
        for unit in units:
            last_page = unit.last_page
            if (last_page > unit.core_last_page
                    and _closed_boundary(document[unit.core_last_page - 1],
                                         document[unit.core_last_page])):
                last_page = unit.core_last_page
            result.append({**unit.__dict__, "last_page": last_page, "status": "pending"})
        return result
