from __future__ import annotations

import ast
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import math
import re
from typing import Any

from app.services.grading.normalize import NormalizationError, normalize_text


MAX_AST_NODES = 64
MAX_DEPTH = 12
MAX_EXPONENT = 12
MAX_INTEGER_DIGITS = 120
ALLOWED_PATTERN = re.compile(r"^[0-9+\-*/().\s^]+$")
SIMPLE_FRACTION = re.compile(r"^([+-]?\d+)\s*/\s*([+-]?\d+)$")
SIMPLE_DECIMAL = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+)$")
SIMPLE_INTEGER = re.compile(r"^[+-]?\d+$")


class NumericParseError(ValueError):
    reason_code = "numeric_parse_failed"


class NumericDivisionByZero(NumericParseError):
    reason_code = "numeric_division_by_zero"


class NumericComplexityError(NumericParseError):
    reason_code = "numeric_complexity_exceeded"


@dataclass(frozen=True)
class ParsedNumeric:
    value: Fraction
    normalized: dict[str, Any]
    source: str
    format_kind: str
    simplified_fraction: bool | None


def _bounded(value: Fraction) -> Fraction:
    if len(str(abs(value.numerator))) > MAX_INTEGER_DIGITS or len(str(value.denominator)) > MAX_INTEGER_DIGITS:
        raise NumericComplexityError("numeric result is too large")
    return value


def _literal(source: str) -> Fraction:
    try:
        if "." in source:
            return Fraction(Decimal(source))
        return Fraction(int(source), 1)
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise NumericParseError("invalid numeric literal") from exc


def _evaluate(node: ast.AST, source: str, depth: int = 0) -> Fraction:
    if depth > MAX_DEPTH:
        raise NumericComplexityError("numeric expression is too deep")
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, source, depth + 1)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        segment = ast.get_source_segment(source, node)
        if segment is None:
            raise NumericParseError("numeric literal is unavailable")
        return _bounded(_literal(segment))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _evaluate(node.operand, source, depth + 1)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp) and isinstance(
        node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
    ):
        left = _evaluate(node.left, source, depth + 1)
        right = _evaluate(node.right, source, depth + 1)
        try:
            if isinstance(node.op, ast.Add):
                return _bounded(left + right)
            if isinstance(node.op, ast.Sub):
                return _bounded(left - right)
            if isinstance(node.op, ast.Mult):
                return _bounded(left * right)
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise NumericDivisionByZero("division by zero")
                return _bounded(left / right)
            if right.denominator != 1 or abs(right.numerator) > MAX_EXPONENT:
                raise NumericComplexityError("exponent is outside the allowed range")
            if left == 0 and right.numerator < 0:
                raise NumericDivisionByZero("division by zero")
            return _bounded(left ** right.numerator)
        except ZeroDivisionError as exc:
            raise NumericDivisionByZero("division by zero") from exc
    raise NumericParseError("unsupported numeric expression")


def parse_numeric(value: Any, rule: dict[str, Any]) -> ParsedNumeric:
    try:
        text = normalize_text(value, rule)
    except NormalizationError as exc:
        raise NumericParseError(str(exc)) from exc
    if not text or not ALLOWED_PATTERN.fullmatch(text):
        raise NumericParseError("numeric answer contains unsupported characters")
    nesting = 0
    for character in text:
        if character == "(":
            nesting += 1
            if nesting > MAX_DEPTH:
                raise NumericComplexityError("numeric expression is too deeply nested")
        elif character == ")":
            nesting -= 1
            if nesting < 0:
                raise NumericParseError("numeric expression has invalid parentheses")
    if nesting != 0:
        raise NumericParseError("numeric expression has invalid parentheses")
    source = text.replace("^", "**")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise NumericParseError("numeric expression has invalid syntax") from exc
    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        raise NumericComplexityError("numeric expression has too many nodes")
    value_result = _evaluate(tree, source)

    fraction_match = SIMPLE_FRACTION.fullmatch(text)
    simplified = None
    if fraction_match:
        numerator, denominator = int(fraction_match.group(1)), int(fraction_match.group(2))
        if denominator == 0:
            raise NumericDivisionByZero("division by zero")
        simplified = math.gcd(abs(numerator), abs(denominator)) == 1
        format_kind = "fraction"
    elif SIMPLE_DECIMAL.fullmatch(text):
        format_kind = "decimal"
    elif SIMPLE_INTEGER.fullmatch(text):
        format_kind = "integer"
    else:
        format_kind = "expression"
    return ParsedNumeric(
        value=value_result,
        normalized={
            "kind": "rational",
            "numerator": value_result.numerator,
            "denominator": value_result.denominator,
            "source_format": format_kind,
        },
        source=text,
        format_kind=format_kind,
        simplified_fraction=simplified,
    )
