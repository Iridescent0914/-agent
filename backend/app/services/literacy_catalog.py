"""Canonical literacy dimensions and label validation."""

from __future__ import annotations

import re

from app.schemas.profile import LiteracyScoreOut


LITERACY_DIMENSIONS: tuple[str, ...] = (
    "抽象能力",
    "运算能力",
    "几何直观",
    "推理能力",
    "模型观念",
    "数据意识",
    "应用意识",
    "创新意识",
)
LITERACY_SEPARATOR = re.compile(r"[、,，;；/|]+")
UNLABELED_LITERACY = "待标注"
BASELINE_SCORE = 60.0
BASELINE_SOURCE = "baseline"
BASELINE_RULE_VERSION = "literacy-baseline-v1"


def baseline_literacies() -> list[LiteracyScoreOut]:
    return [
        LiteracyScoreOut(
            name=name,
            score=BASELINE_SCORE,
            score_source=BASELINE_SOURCE,
            rule_version=BASELINE_RULE_VERSION,
        )
        for name in LITERACY_DIMENSIONS
    ]


def parse_literacy_names(value: object) -> tuple[str, ...]:
    """Normalize a persisted label and reject values outside the formal catalog."""

    if not isinstance(value, str):
        raise ValueError("核心素养必须是文本")
    names = [name.strip() for name in LITERACY_SEPARATOR.split(value) if name.strip()]
    names = list(dict.fromkeys(names))
    if not names:
        raise ValueError("核心素养不能为空")
    unknown = [name for name in names if name not in LITERACY_DIMENSIONS]
    if unknown:
        raise ValueError(
            "未知核心素养：" + "、".join(unknown)
            + "；仅允许：" + "、".join(LITERACY_DIMENSIONS)
        )
    return tuple(names)


def normalize_literacy_label(value: object, *, allow_unlabeled: bool = False) -> str:
    """Return the canonical separator form used by question records."""

    if allow_unlabeled and isinstance(value, str) and value.strip() == UNLABELED_LITERACY:
        return UNLABELED_LITERACY
    return "、".join(parse_literacy_names(value))
