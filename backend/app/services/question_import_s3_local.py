"""Conservative, free local extraction and page planning for answer-only PDFs."""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf as fitz

from app.services.question_import_s2 import PageUnit


_ANSWER_LINE = re.compile(r"^([1-9]\d{0,2})\s*[.．、]\s*(?:答案\s*[:：]\s*)?(.+)$")
_HEADING = re.compile(r"^(?:参考答案|习题答案|答案|第\s*[一二三四五六七八九十百\d]+\s*(?:讲|章|节)(?:答案)?)$")
_NEW_SECTION = re.compile(r"^(?:L\d+\s*拓展篇)?第\s*[一二三四五六七八九十百\d]+\s*(?:讲|章|节)")
_NEW_ANSWER = re.compile(r"^[1-9]\d{0,2}\s*[.．、]")
_UNCERTAIN = re.compile(r"[�?？@]|\(cid:|[\ue000-\uf8ff]|(?:解析|证明|过程|见图|如图|略)")
_MARKED_ANSWER = re.compile(r"^【答案】\s*(.+)$")


def _content_lines(page) -> list[str]:
    return [line for raw in page.get_text("text", sort=True).splitlines()
            if (line := raw.strip()) and not line.isdecimal() and not line.startswith("@")]


def _marked_answers(lines: list[str]) -> list[dict] | None:
    """Read explicit answer labels only when every question starts on this page."""
    answers = []
    current = None
    first_question_seen = False
    for line in lines:
        match = _NEW_ANSWER.match(line)
        if match:
            if current is not None:
                return None  # A question without a complete marked answer.
            current = re.match(r"^([1-9]\d{0,2})", line).group(1)
            first_question_seen = True
            continue
        marked = _MARKED_ANSWER.match(line)
        if marked:
            value = marked.group(1).strip()
            if (current is None or not 1 <= len(value) <= 80
                    or _UNCERTAIN.search(value) or "见分析" in value
                    or value.count("(") != value.count(")")):
                return None
            answers.append({"original_number": current, "answer_text": value})
            current = None
        elif not first_question_seen and not (_NEW_SECTION.match(line) or
                                              line in {"参考答案与试题解析", "参考答案", "答案"}):
            return None
    if current is not None or not answers or len({a["original_number"] for a in answers}) != len(answers):
        return None
    return answers


def _plain_answers(page) -> list[dict] | None:
    """Accept only self-contained, numbered, one-line answers with no graphics."""
    blocks = page.get_text("dict", sort=True)["blocks"]
    if any(block["type"] != 0 for block in blocks) or page.get_drawings():
        return None
    lines = _content_lines(page)
    if not lines or len(lines) > 36:
        return None
    answers = []
    seen = set()
    for line in lines:
        if _HEADING.fullmatch(line):
            continue
        match = _ANSWER_LINE.fullmatch(line)
        if not match:
            return _marked_answers(lines)
        number, value = match.groups()
        value = value.strip()
        if (number in seen or not 1 <= len(value) <= 80 or _UNCERTAIN.search(value)
                or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
                or value.count("(") != value.count(")")
                or value.count("[") != value.count("]")):
            return None
        seen.add(number)
        answers.append({"original_number": number, "answer_text": value})
    return answers or None


def _closed_boundary(page, following) -> bool:
    """Omit model context only at a visibly closed answer/section boundary."""
    before = _content_lines(page)
    after = _content_lines(following)
    if not before or not after:
        return False
    if _NEW_SECTION.match(after[0]):
        return True
    return (bool(_NEW_ANSWER.match(after[0]))
            and bool(re.search(r"[。.!！;；．]$", before[-1]))
            and not _UNCERTAIN.search(before[-1]))


def _starts_section(page) -> bool:
    lines = _content_lines(page)
    return bool(lines and _NEW_SECTION.match(lines[0]))


def plan_answer_units(pdf: Path, *, pages_per_unit: int, overlap: int) -> list[dict]:
    """Group clear text pages locally, sending only uncertain runs to the model."""
    if pages_per_unit < 1 or overlap < 0 or overlap >= pages_per_unit:
        raise ValueError("invalid answer page plan")
    with fitz.open(str(pdf)) as document:
        local = {index + 1: _plain_answers(page) for index, page in enumerate(document)}
        count = len(document)
        units = []
        page = 1
        while page <= count:
            start = page
            is_local = local[page] is not None
            while (page <= count and page - start < pages_per_unit
                   and (local[page] is not None) == is_local
                   and (page == start or not _starts_section(document[page - 1]))):
                page += 1
            end = page - 1
            last = end
            if not is_local and end < count and not _closed_boundary(document[end - 1], document[end]):
                last = min(count, end + overlap)
            unit = PageUnit(len(units), start, last, start, end).__dict__
            unit["status"] = "pending"
            if is_local:
                unit["method"] = "local_text"
                unit["local_answers"] = [
                    {**answer, "page_start": source_page - start + 1,
                     "page_end": source_page - start + 1}
                    for source_page in range(start, end + 1)
                    for answer in local[source_page]
                ]
            units.append(unit)
        return units
