"""Static literacy dimensions used before dynamic scoring is available.

The values are deliberately not persisted yet.  This keeps the dashboard
useful while the scoring algorithm is still being specified, and gives the
future computed-score implementation a stable response contract.
"""

from __future__ import annotations

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
