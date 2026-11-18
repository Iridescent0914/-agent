"""Conservative S2 routing. Syntax checks classify a form, never prove equality."""
from __future__ import annotations

import ast
from copy import deepcopy
import re
import unicodedata

from app.services.grading import grade_answer

ROUTING_VERSION = "stage6-equivalence-routing-v1"
_COMMANDS = {"frac", "dfrac", "tfrac", "sqrt", "left", "right", "cdot", "times", "div"}
_NORMALIZATION_KEYS = {
    "normalize_fullwidth_symbols", "ignore_whitespace", "trim_whitespace", "uppercase",
    "answer_schema_version", "item_order_matters", "partial_credit",
    "allow_equivalent_fraction", "allow_fraction_decimal_equivalence",
}


def _expression_form(value) -> bool:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        return False
    text = unicodedata.normalize("NFKC", value).strip()
    if text.startswith("$") and text.endswith("$"):
        text = text[1:-1]
    text = text.replace("−", "-").replace("–", "-").replace("×", "*").replace("÷", "/")
    # Bounded braced LaTeX only. Unknown macros/text, equations, sets and
    # conditions are deliberately outside this first answer-form allowlist.
    commands = re.findall(r"\\([A-Za-z]+)", text)
    if any(command not in _COMMANDS for command in commands):
        return False
    text = re.sub(r"\\(?:left|right)\b", "", text)
    text = re.sub(r"\^\{([^{}]+)\}", r"^(\1)", text)
    for _ in range(8):
        updated = re.sub(r"\\(?:frac|dfrac|tfrac)\{([^{}]+)\}\{([^{}]+)\}", r"((\1)/(\2))", text)
        updated = re.sub(r"\\sqrt\{([^{}]+)\}", r"sqrt(\1)", updated)
        if updated == text:
            break
        text = updated
    text = re.sub(r"\\(?:cdot|times)\b", "*", text)
    text = re.sub(r"\\div\b", "/", text).replace("{", "(").replace("}", ")")
    if re.search(r"\d\s+\d", text):
        return False
    tokens = re.findall(r"sqrt|abs|[a-zA-Z]|\d+(?:\.\d+)?|\.\d+|[()+*/^\-]", text)
    if not tokens or "".join(tokens) != "".join(text.split()):
        return False
    # Reject long prose disguised as a product of individual letters.
    if re.search(r"[A-Za-z]{2,}", re.sub(r"sqrt|abs", "", text)):
        return False
    numeric = lambda token: token[0].isdigit() or token.startswith(".")
    variable = lambda token: len(token) == 1 and token.isalpha()
    explicit = []
    for token in tokens:
        if explicit:
            previous = explicit[-1]
            if ((numeric(previous) or variable(previous) or previous == ")")
                    and (variable(token) or token in {"(", "sqrt", "abs"})):
                explicit.append("*")
        explicit.append(token)
    source = "".join(explicit).replace("^", "**")
    try:
        tree = ast.parse(source, mode="eval")
        nodes = list(ast.walk(tree))
        if len(nodes) > 64:
            return False
        allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow,
                   ast.UAdd, ast.USub, ast.Constant, ast.Name, ast.Load, ast.Call)
        for node in nodes:
            if not isinstance(node, allowed):
                return False
            if isinstance(node, ast.Constant) and (type(node.value) not in {int, float} or len(str(node.value)) > 24):
                return False
            if isinstance(node, ast.Name) and not (variable(node.id) or node.id in {"sqrt", "abs"}):
                return False
            if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or node.func.id not in {"sqrt", "abs"} or len(node.args) != 1 or node.keywords):
                return False
            if isinstance(node, ast.Name) and node.id in {"sqrt", "abs"}:
                if not any(isinstance(parent, ast.Call) and parent.func is node for parent in nodes):
                    return False
    except (SyntaxError, RecursionError, ValueError):
        return False
    return True


def classify_unit(*, question_type, reference, rule, item_id, student_answer,
                  input_confirmed=True, teacher_confirmed=False):
    grouped = isinstance(reference, dict) and reference.get("kind") == "group"
    if grouped:
        parts = [part for part in reference["items"] if str(part["item_id"]) == item_id]
        part = deepcopy(parts[0])
        # Exactly the existing group engine, including aliases and per-unit
        # match types, run on one isolated leaf rather than the group outcome.
        result = grade_answer(
            {"kind": "group", "items": [{"item_id": part["item_id"], "answer": student_answer}]},
            {"kind": "group", "items": [part]}, rule, response_status="answered",
        )
        details = result.item_results[0] if result.item_results else None
        match_type = part.get("answer_match_type")
        expected = part.get("answer")
    else:
        result = grade_answer(student_answer, reference, rule,
                              response_status="blank" if student_answer is None else "answered")
        details = {"grade_status": result.grade_status, "is_correct": result.is_correct,
                   "reason_code": result.reason_code, "grading_details": result.details()}
        match_type, expected = rule.get("answer_match_type"), reference
    base = {"routing_version": ROUTING_VERSION, "rule_result": details,
            "match_type": match_type, "eligible": False}

    def blocked(code, message):
        return {**base, "reason_code": code, "message": message}

    if not input_confirmed:
        return blocked("input_unconfirmed", "请先对照原图确认或修正抄录文本")
    if teacher_confirmed:
        return blocked("teacher_confirmed", "本题已有教师最终判定")
    if not isinstance(student_answer, str) or not student_answer.strip():
        return blocked("blank_or_invalid_answer", "本空留白或答案类型不适合等价比较")
    if details is None:
        return blocked("invalid_rule", "本空评分规则不可用，请人工核对")
    if details["is_correct"] is True:
        return blocked("rule_correct", "规则已确认本空正确（含已登记的答案别名）")
    if details["is_correct"] is False:
        return blocked("rule_incorrect", "规则已有明确结果，请按现有判分或人工复核处理")
    if question_type != "fill_blank" or match_type != "latex_exact":
        return blocked("outside_scope", "首批仅处理公式匹配规则尚未确定的填空答案")
    normalization = rule.get("answer_normalization_rule") or {}
    if not isinstance(normalization, dict):
        return blocked("invalid_rule", "本空评分规则不可用，请人工核对")
    if (set(normalization) - _NORMALIZATION_KEYS
            or normalization.get("allow_equivalent_fraction") is False
            or normalization.get("allow_fraction_decimal_equivalence") is False):
        return blocked("format_review_required", "本空包含额外格式或条件约束，请人工核对")
    numeric = grade_answer(student_answer, expected, {
        "answer_match_type": "numeric", "answer_normalization_rule": normalization,
    }, response_status="answered")
    if numeric.is_correct is not None:
        base["numeric_check"] = numeric.details()
        return blocked("rule_numeric_correct" if numeric.is_correct else "rule_numeric_incorrect",
                       "受限数值规则已能比较本空，无需模型建议；正式判分仍按原流程处理")
    if details["reason_code"] != "formula_not_exact_match":
        return blocked("unsupported_rule_result", "当前待复核原因不在等价建议范围")
    if not _expression_form(expected) or not _expression_form(student_answer):
        return blocked("unsupported_answer_form", "首批仅支持有限长度的代数式、分式、根式或绝对值写法")
    return {**base, "eligible": True, "reason_code": "formula_candidate",
            "message": "本空规则尚未确认，可请求等价建议；条件不足时允许无法确定"}
