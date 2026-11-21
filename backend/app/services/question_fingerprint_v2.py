"""Version 2 exact-content fingerprint. Keep its rules stable after migration 0034."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.assets import _manifest_entry, _teacher_asset_filename


VERSION = "s4-exact-v2"
_ASSET_MARKER = re.compile(r"\[ASSET:[^\]]+\]")
_LEADING_NUMBER = re.compile(r"^\s*(?:第\s*\d+\s*题\s*[.．、:：]?|\d+\s*[、．]|\d+\s*\.\s*(?!\d))\s*")
_FIGURE_CUE = re.compile(r"(?:如图|下图|图中|见图|如下图|右图|左图)")


def _safe_width_and_scripts(value: str) -> str:
    parts: list[str] = []
    prior_script = ""
    for character in value:
        name = unicodedata.name(character, "")
        script = ("^" if "SUPERSCRIPT" in name or name.startswith("MODIFIER LETTER") else
                  "_" if "SUBSCRIPT" in name else "")
        if script:
            if script != prior_script:
                parts.append(script)
            parts.append(unicodedata.normalize("NFKC", character).replace("−", "-"))
        elif 0xFF01 <= ord(character) <= 0xFF5E:
            parts.append(chr(ord(character) - 0xFEE0))
        else:
            parts.append({"\u3000": " ", "−": "-", "℃": "°C", "℉": "°F"}.get(character, character))
        prior_script = script
    return "".join(parts)


def normalized_text(value: str, *, strip_question_number: bool = False) -> str:
    # Blanket NFKC silently changes 10⁸ to 108 and ① to 1.
    value = _safe_width_and_scripts(value)
    value = _ASSET_MARKER.sub("[题图]", value)
    if strip_question_number:
        value = _LEADING_NUMBER.sub("", value, count=1)
    return re.sub(r"\s+", " ", value).strip()


def normalized_options(options: Any) -> list[list[str]] | None:
    if options is None or options == []:
        return []
    if not isinstance(options, list):
        return None
    result: list[list[str]] = []
    for index, option in enumerate(options):
        if isinstance(option, str):
            result.append([chr(65 + index), normalized_text(option)])
        elif isinstance(option, dict) and isinstance(option.get("text"), str):
            key = option.get("key", chr(65 + index))
            if not isinstance(key, str):
                return None
            result.append([normalized_text(key), normalized_text(option["text"])])
        else:
            return None
    return result


def content_key(question_text: str, options: Any, image_hashes: list[str], *,
                missing_image: bool = False) -> dict[str, Any] | None:
    if missing_image or not isinstance(question_text, str) or not question_text.strip():
        return None
    stem = normalized_text(question_text, strip_question_number=True)
    choices = normalized_options(options)
    if not stem or choices is None:
        return None
    if (_FIGURE_CUE.search(stem) or _ASSET_MARKER.search(question_text)) and not image_hashes:
        return None
    return {"version": VERSION, "text": stem, "options": choices, "images": image_hashes}


def fingerprint(key: dict[str, Any] | None) -> str | None:
    if key is None:
        return None
    raw = json.dumps(key, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def catalog_image_bytes(assets: Any, *, has_image: bool = False) -> list[bytes] | None:
    if not isinstance(assets, list) or (has_image and not assets):
        return None
    settings = get_settings()
    result: list[bytes] = []
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("path"), str):
            return None
        source_path = asset["path"]
        teacher_name = _teacher_asset_filename(source_path)
        if teacher_name is not None:
            path = settings.teacher_asset_dir / teacher_name
        else:
            entry, _ = _manifest_entry(source_path)
            if entry is None or entry.get("status") != "converted":
                return None
            web_path = entry.get("web_path")
            if not isinstance(web_path, str) or Path(web_path).is_absolute() or ".." in Path(web_path).parts:
                return None
            path = settings.web_asset_dir / web_path
        try:
            result.append(path.read_bytes())
        except OSError:
            return None
    return result


def catalog_image_hashes(assets: Any, *, has_image: bool = False) -> list[str] | None:
    images = catalog_image_bytes(assets, has_image=has_image)
    return None if images is None else [hashlib.sha256(data).hexdigest() for data in images]


def catalog_content_key(question_text: str, options: Any, assets: Any, *,
                        has_image: bool = False) -> dict[str, Any] | None:
    image_hashes = catalog_image_hashes(assets, has_image=has_image)
    return content_key(question_text, options, image_hashes or [],
                       missing_image=image_hashes is None)
