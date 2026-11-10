"""Read the canonical chapter/section/KP relationship from the HTML graph."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path


GRAPH_PATH = Path(__file__).resolve().parents[3] / "上海市五四制初中数学知识图谱.html"


@lru_cache(maxsize=1)
def chapter_sections() -> dict[tuple[str, str], tuple[tuple[str, str], ...]]:
    html = GRAPH_PATH.read_text(encoding="utf-8")
    source = html.split("const SECTION_BY_KP = [", 1)[1].split("];", 1)[0]
    indexes = [
        json.loads(match.group(1))
        for match in re.finditer(r"/\*[^*]+\*/\s*(\[[0-9, ]+\])", source)
    ]
    chapter_pattern = re.compile(
        r'\{grade:"([^"]+)",ch:"([^"]+)".*?'
        r'sec:(\[[^\]]*\]),\s*kps:\[(.*?)\]\s*\}',
        re.S,
    )
    chapters = list(chapter_pattern.finditer(html))
    if len(chapters) != len(indexes):
        raise ValueError("HTML chapter count does not match SECTION_BY_KP")
    result: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {}
    for chapter, mapping in zip(chapters, indexes):
        grade, name = chapter.group(1), chapter.group(2)
        sections = json.loads(chapter.group(3))
        kp_names = re.findall(r'^\s*\["([^"]+)",', chapter.group(4), re.M)
        if len(kp_names) != len(mapping) or any(
            not isinstance(index, int) or not 0 <= index < len(sections)
            for index in mapping
        ):
            raise ValueError(f"Invalid HTML SECTION_BY_KP: {grade} {name}")
        key = (grade, name)
        if key in result:
            raise ValueError(f"Duplicate HTML chapter: {grade} {name}")
        result[key] = tuple(
            (kp_name, sections[index])
            for kp_name, index in zip(kp_names, mapping)
        )
    return result


def sections_for_tree(tree: dict) -> dict[str, str]:
    key = (tree["grade"], tree["chapter"]["name"])
    try:
        canonical = chapter_sections()[key]
    except KeyError as exc:
        raise ValueError(f"Chapter is absent from HTML graph: {key}") from exc
    points = tree["knowledge_points"]
    if len(points) != len(canonical):
        raise ValueError(f"KP count differs from HTML graph: {key}")
    section_names = [section["name"] for section in tree["sections"]]
    result = {}
    for point, (html_name, html_section) in zip(points, canonical):
        if point["name"] != html_name:
            raise ValueError(f"KP order/name differs from HTML graph: {point['id']}")
        if html_section not in section_names:
            raise ValueError(f"HTML section is absent from tree: {html_section}")
        result[point["id"]] = html_section
    return result
