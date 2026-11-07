"""Bounded exact rational functions of one positive-integer variable.

Equality uses Fraction polynomial cross multiplication, without eval or
numerical sampling. Unproven denominator domains require teacher review.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from fractions import Fraction
import re
import unicodedata

MAX_DEGREE = 8


class ExpressionReview(ValueError):
    pass


def polynomial(values):
    result = list(values)
    while len(result) > 1 and result[-1] == 0:
        result.pop()
    if len(result) > MAX_DEGREE + 1 or any(x.numerator.bit_length() > 512 or x.denominator.bit_length() > 512 for x in result):
        raise ExpressionReview("expression_complexity_exceeded")
    return tuple(result)


def add(a, b):
    return polynomial((a[i] if i < len(a) else 0) + (b[i] if i < len(b) else 0) for i in range(max(len(a), len(b))))


def multiply(a, b):
    if len(a) + len(b) - 2 > MAX_DEGREE:
        raise ExpressionReview("expression_complexity_exceeded")
    values = [Fraction()] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            values[i + j] += x * y
    return polynomial(values)


@dataclass(frozen=True)
class Rational:
    numerator: tuple[Fraction, ...]
    denominator: tuple[Fraction, ...] = (Fraction(1),)

    def negative(self):
        return Rational(tuple(-x for x in self.numerator), self.denominator)

    def plus(self, other):
        return Rational(add(multiply(self.numerator, other.denominator), multiply(other.numerator, self.denominator)), multiply(self.denominator, other.denominator))

    def times(self, other):
        return Rational(multiply(self.numerator, other.numerator), multiply(self.denominator, other.denominator))

    def divided_by(self, other):
        terms = other.numerator
        # Uniform coefficient signs prove nonzero for every n>0, including
        # factored/expanded n(n+1)(n+2). Mixed signs are deliberately deferred.
        if not any(terms) or not (all(x >= 0 for x in terms) or all(x <= 0 for x in terms)):
            raise ExpressionReview("expression_domain_requires_review")
        return Rational(multiply(self.numerator, other.denominator), multiply(self.denominator, other.numerator))


def parse_expression(value: object) -> Rational:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ExpressionReview("expression_parse_failed")
    text = unicodedata.normalize("NFKC", value).replace("−", "-").replace("–", "-").replace("×", "*").replace("÷", "/")
    if re.search(r"\d\s+\d", text):
        raise ExpressionReview("expression_parse_failed")
    tokens = re.findall(r"\d+(?:\.\d+)?|\.\d+|n|[()+*/^\-]", text)
    if "".join(tokens) != "".join(text.split()):
        raise ExpressionReview("expression_parse_failed")
    result = []
    numeric = lambda token: token[0].isdigit() or token.startswith(".")
    for token in tokens:
        if result:
            previous = result[-1]
            if (numeric(previous) or previous in {"n", ")"}) and (token in {"n", "("} or (previous in {"n", ")"} and numeric(token))):
                result.append("*")
        result.append(token)
    source = "".join(result).replace("^", "**")
    try:
        tree = ast.parse(source, mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise ExpressionReview("expression_parse_failed") from exc
    if sum(1 for _ in ast.walk(tree)) > 96:
        raise ExpressionReview("expression_complexity_exceeded")

    def visit(node, depth=0):
        if depth > 16:
            raise ExpressionReview("expression_complexity_exceeded")
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            literal = ast.get_source_segment(source, node)
            if literal is None or len(literal) > 64:
                raise ExpressionReview("expression_complexity_exceeded")
            try:
                return Rational(polynomial([Fraction(literal)]))
            except (ValueError, ZeroDivisionError) as exc:
                raise ExpressionReview("expression_parse_failed") from exc
        if isinstance(node, ast.Name) and node.id == "n":
            return Rational((Fraction(), Fraction(1)))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand, depth + 1)
            return value.negative() if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp):
            left = visit(node.left, depth + 1)
            if isinstance(node.op, ast.Pow):
                if not isinstance(node.right, ast.Constant) or type(node.right.value) is not int or not 0 <= node.right.value <= MAX_DEGREE:
                    raise ExpressionReview("expression_complexity_exceeded")
                result = Rational((Fraction(1),))
                for _ in range(node.right.value):
                    result = result.times(left)
                return result
            right = visit(node.right, depth + 1)
            if isinstance(node.op, ast.Add):
                return left.plus(right)
            if isinstance(node.op, ast.Sub):
                return left.plus(right.negative())
            if isinstance(node.op, ast.Mult):
                return left.times(right)
            if isinstance(node.op, ast.Div):
                return left.divided_by(right)
        raise ExpressionReview("expression_parse_failed")

    return visit(tree.body)


def equivalent(actual: object, expected: object, *, variable: str, domain: str) -> bool:
    if variable != "n" or domain != "positive_integer":
        raise ExpressionReview("invalid_expression_rule")
    left, right = parse_expression(actual), parse_expression(expected)
    return multiply(left.numerator, right.denominator) == multiply(right.numerator, left.denominator)
