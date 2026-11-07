from __future__ import annotations

from typing import Any
import unicodedata


class NormalizationError(ValueError):
    pass


def normalize_text(value: Any, rule: dict[str, Any], *, max_length: int = 128) -> str:
    if not isinstance(value, str):
        raise NormalizationError("answer must be text")
    if len(value) > max_length:
        raise NormalizationError("answer is too long")
    result = value
    if rule.get("normalize_fullwidth_symbols", False):
        result = unicodedata.normalize("NFKC", result).replace("×", "*").replace("÷", "/").replace("−", "-").replace("–", "-")
    if rule.get("ignore_whitespace", False):
        result = "".join(result.split())
    if rule.get("trim_whitespace", False):
        result = result.strip()
    return result


def normalize_choice(value: Any, rule: dict[str, Any]) -> str:
    result = normalize_text(value, rule)
    if rule.get("uppercase", False):
        result = result.upper()
    if len(result) != 1 or not ("A" <= result <= "Z"):
        raise NormalizationError("choice answer must be one letter")
    return result
