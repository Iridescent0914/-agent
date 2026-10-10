from __future__ import annotations

import json
import uuid
from io import BytesIO
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import get_settings


@lru_cache(maxsize=1)
def _manifest() -> dict[str, dict[str, Any]]:
    path = get_settings().web_asset_dir / "asset_manifest.json"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    entries = raw.get("assets", raw) if isinstance(raw, dict) else raw
    return {item["source_path"]: item for item in entries}


def resolve_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    settings = get_settings()
    manifest = _manifest()
    result: list[dict[str, Any]] = []
    for asset in assets:
        source_path = asset.get("path", "")
        teacher_filename = _teacher_asset_filename(source_path)
        if teacher_filename is not None:
            target = settings.teacher_asset_dir / teacher_filename
            result.append({
                "path": source_path,
                "web_url": (
                    f"{settings.public_teacher_asset_base_url.rstrip('/')}/{teacher_filename}"
                    if target.is_file() else None
                ),
                "conversion_status": "uploaded" if target.is_file() else "missing",
            })
            continue
        entry = manifest.get(source_path)
        web_url = None
        status = "missing"
        if entry is not None:
            status = entry.get("status", "missing")
            web_path = entry.get("web_path")
            if status == "converted" and web_path:
                web_url = f"{settings.public_asset_base_url.rstrip('/')}/{Path(web_path).name}"
        result.append(
            {"path": source_path, "web_url": web_url, "conversion_status": status}
        )
    return result


def actionable_import_issues(
    import_issues: list[str],
    assets: list[dict[str, Any]],
    grading_readiness: str,
) -> list[str]:
    """Return current actionable issues instead of stale extraction notes."""
    resolved_assets = resolve_assets(assets)
    has_unavailable_asset = any(
        asset["conversion_status"] not in {"converted", "uploaded"}
        or not asset["web_url"]
        for asset in resolved_assets
    )
    result: list[str] = []
    for issue in import_issues:
        if issue == "contains_asset":
            continue
        if issue == "asset_needs_web_conversion":
            if has_unavailable_asset and "asset_unavailable" not in result:
                result.append("asset_unavailable")
            continue
        if issue not in result:
            result.append(issue)
    if grading_readiness == "machine_ready":
        # Promotion is the teacher's confirmation that imported answer/type
        # concerns are resolved. Only faults that still break use stay open.
        current_technical_issues = {
            "asset_unavailable",
            "missing_question_text",
            "missing_main_knowledge_point",
            "missing_main_exam_point",
            "missing_difficulty",
        }
        result = [issue for issue in result if issue in current_technical_issues]
    return result


def _teacher_asset_filename(source_path: str) -> str | None:
    prefix = "assets/teacher/"
    if not source_path.startswith(prefix):
        return None
    filename = source_path.removeprefix(prefix)
    if Path(filename).name != filename:
        return None
    if len(filename) != 36 or not filename.endswith(".png"):
        return None
    try:
        uuid.UUID(hex=filename[:-4])
    except ValueError:
        return None
    return filename


def teacher_asset_exists(source_path: str) -> bool:
    filename = _teacher_asset_filename(source_path)
    return bool(filename and (get_settings().teacher_asset_dir / filename).is_file())


def store_teacher_asset(payload: bytes) -> dict[str, str]:
    """Validate and normalize a teacher-uploaded image into a safe PNG asset."""
    settings = get_settings()
    try:
        with Image.open(BytesIO(payload)) as source:
            source.load()
            if source.width * source.height > settings.question_asset_max_pixels:
                raise ValueError("图片像素过大")
            image = ImageOps.exif_transpose(source)
            has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
            image = image.convert("RGBA" if has_alpha else "RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("文件不是有效图片") from exc

    settings.teacher_asset_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.png"
    target = settings.teacher_asset_dir / filename
    temporary = target.with_suffix(".tmp")
    try:
        image.save(temporary, format="PNG", optimize=True)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)

    source_path = f"assets/teacher/{filename}"
    return {
        "path": source_path,
        "web_url": f"{settings.public_teacher_asset_base_url.rstrip('/')}/{filename}",
        "conversion_status": "uploaded",
    }
