"""Read the checked-in JSON knowledge catalog used at runtime."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


CATALOG_PATH = Path(__file__).resolve().parents[3] / "data" / "knowledge_graph_catalog.json"


@lru_cache(maxsize=1)
def load_catalog() -> dict:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if catalog.get("schema_version") != "knowledge-graph-catalog-v1":
        raise ValueError("Unsupported knowledge graph catalog version")
    return catalog


@lru_cache(maxsize=1)
def formal_kps_by_code() -> dict[str, dict]:
    return {
        point["code"]: {
            **point,
            "grade": chapter["grade"],
            "chapter_code": chapter["code"],
            "chapter_name": chapter["name"],
        }
        for chapter in load_catalog()["chapters"]
        for point in chapter["knowledge_points"]
        if point["code"] is not None
    }


def formal_kp(code: str) -> dict:
    try:
        return formal_kps_by_code()[code]
    except KeyError as exc:
        raise ValueError(f"KP is absent from the formal JSON catalog: {code}") from exc


def sections_for_tree(tree: dict) -> dict[str, str]:
    matches = [
        chapter for chapter in load_catalog()["chapters"]
        if chapter["grade"] == tree["grade"] and chapter["name"] == tree["chapter"]["name"]
    ]
    if len(matches) != 1:
        raise ValueError("Chapter is absent or duplicated in the JSON knowledge catalog")
    chapter = matches[0]
    if not chapter["available_for_import"] or chapter["code"] != tree["chapter"]["id"]:
        raise ValueError("Chapter has no formal KP codes in the JSON knowledge catalog")
    if [item["name"] for item in tree["sections"]] != chapter["sections"]:
        raise ValueError("Tree sections differ from the JSON knowledge catalog")
    points = tree["knowledge_points"]
    canonical = chapter["knowledge_points"]
    if len(points) != len(canonical):
        raise ValueError("KP count differs from the JSON knowledge catalog")
    result = {}
    for point, expected in zip(points, canonical):
        if point["id"] != expected["code"] or point["name"] != expected["name"]:
            raise ValueError(f"KP order/code/name differs from the JSON knowledge catalog: {point['id']}")
        result[point["id"]] = expected["section"]
    return result
