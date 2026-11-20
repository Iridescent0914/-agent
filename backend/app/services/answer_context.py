"""Bounded text context from the published stem, with a verified target blank.

No current question table, answer values, solutions, identity or asset URLs.
"""
import re

from app.schemas.assessment import AnswerContext
from app.services.assessment_protocol import input_hash

CONTEXT_VERSION = "published-answer-context-v1"
_MARKER = re.compile(r"(?<![/^_])[（(]\s*(\d{1,3})\s*[）)](?!\s*/)")
_BLANK = re.compile(r"_{2,}")
_ASSET = re.compile(r"\[ASSET:|<img\b|!\[[^\]]*\]\(|\\(?:includegraphics|placeholder)\b|如图|下图|图中|示意图|题目图片或公式", re.I)
_DEPENDENCY = re.compile(r"上述|上题|上一|前一|前面|以上|第[一二三四五六七八九十\d]+[小题问]|所有|各小题|均需|统一|注意|注[:：]|保留|精确到|最简|单位为|用分数|用小数|如图|下图|如表|下表")


class ContextUnavailable(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def build_context(item, coordinates, item_id):
    snapshot = item.question_snapshot or {}
    text = snapshot.get("question_text")
    if not isinstance(text, str) or not text.strip():
        raise ContextUnavailable("context_missing", "发布题干缺失，请人工复核本题")
    if len(text) > 24000 or "【本空】" in text:
        raise ContextUnavailable("context_ambiguous", "发布题干过长或含歧义目标标记，请人工复核本题")
    sub, blank = coordinates[item_id]
    source_text = text
    if len(coordinates) == 1 and (sub, blank) == (1, 1) and not _BLANK.search(text):
        # A scalar answer has one unambiguous response location even if the
        # source asks a question without printing an underline.
        text += "\n本题唯一作答位置：________"
    by_sub = {}
    for number, position in coordinates.values():
        by_sub.setdefault(number, []).append(position)
    for positions in by_sub.values():
        if sorted(positions) != list(range(1, len(positions) + 1)):
            raise ContextUnavailable("context_ambiguous", "空位编号不连续，无法可靠定位题干，请人工复核本题")
    markers = list(_MARKER.finditer(text))
    sections = {}
    if markers and [int(m[1]) for m in markers] == sorted(by_sub):
        prefix = text[:markers[0].start()]
        if _BLANK.search(prefix):
            raise ContextUnavailable("context_ambiguous", "公共题干中含未对应空位，请人工复核本题")
        for i, marker in enumerate(markers):
            end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
            sections[int(marker[1])] = (marker.start(), end)
    elif len(by_sub) == 1:
        prefix = ""
        sections[sub] = (0, len(text))
    else:
        raise ContextUnavailable("context_ambiguous", "小题编号与发布空位无法可靠对应，请人工复核本题")
    for number, (start, end) in sections.items():
        if len(list(_BLANK.finditer(text[start:end]))) != len(by_sub[number]):
            raise ContextUnavailable("context_ambiguous", "题干空位数与发布清单不一致，请人工复核本题")
    start, end = sections[sub]
    matches = list(_BLANK.finditer(text[start:end]))
    target = matches[blank - 1]
    target_start, target_end = start + target.start(), start + target.end()
    # Keep the whole group if another clause or a trailing instruction might
    # qualify this answer. Otherwise retain all common conditions plus this part.
    line_markers = all(not text[text.rfind("\n", 0, m.start()) + 1:m.start()].strip() for m in markers)
    group = len(sections) == 1 or not line_markers or bool(_DEPENDENCY.search(text))
    if group:
        selected = text[:target_start] + "【本空】" + text[target_end:]
        scope = "question_group"
    else:
        selected = prefix + text[start:target_start] + "【本空】" + text[target_end:end]
        scope = "subquestion"
    if _ASSET.search(selected) or (snapshot.get("assets") and not _ASSET.search(text)):
        raise ContextUnavailable("context_incomplete", "本空上下文包含未文本化的题图或公式，请人工复核本题")
    if len(selected) > 12000:
        raise ContextUnavailable("context_too_long", "本空上下文超出长度限制，请人工复核本题")
    context = AnswerContext(scope=scope, question_text=selected, target_subquestion_no=sub, target_blank_no=blank).model_dump()
    return {"version": CONTEXT_VERSION, "answer_context": context,
        "context_sha256": input_hash(context),
        "source_sha256": input_hash({"question_text": source_text, "assets": snapshot.get("assets") or [],
                                      "coordinates": coordinates})}
