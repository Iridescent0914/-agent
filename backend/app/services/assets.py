from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import PaperInstanceItem, Question


@lru_cache(maxsize=4)
def _manifest(chapter: str) -> dict[str, dict[str, Any]]:
    if chapter not in {"rational", "expr"}:
        return {}
    path = get_settings().web_asset_dir / chapter / "asset_manifest.json"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    entries = raw.get("assets", raw) if isinstance(raw, dict) else raw
    return {item["source_path"]: item for item in entries}


def _chapter_for_source_path(source_path: str) -> str | None:
    parts = PurePosixPath(source_path).parts
    if len(parts) >= 3 and parts[0] == "assets" and parts[1] in {"rational", "expr"}:
        return parts[1]
    return None


def resolve_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    settings = get_settings()
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
        chapter = _chapter_for_source_path(source_path)
        entry = _manifest(chapter).get(source_path) if chapter else None
        web_url = None
        status = "missing"
        if entry is not None:
            status = entry.get("status", "missing")
            web_path = entry.get("web_path")
            if status == "converted" and web_path:
                web_url = f"{settings.public_asset_base_url.rstrip('/')}/{web_path.lstrip('/')}"
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


def question_asset_exists(source_path: str) -> bool:
    """Validate both immutable imported assets and teacher-uploaded assets."""
    if teacher_asset_exists(source_path):
        return True
    chapter = _chapter_for_source_path(source_path)
    entry = _manifest(chapter).get(source_path) if chapter else None
    if entry is None or entry.get("status") != "converted":
        return False
    web_path = entry.get("web_path")
    return bool(
        isinstance(web_path, str)
        and (get_settings().web_asset_dir / Path(web_path)).is_file()
    )


def _asset_paths(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        str(item["path"])
        for item in value
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }


def referenced_teacher_assets(db: Session) -> set[str]:
    """Collect live and historical references so published paper snapshots stay valid."""
    paths: set[str] = set()
    for assets in db.scalars(select(Question.assets)):
        paths.update(_asset_paths(assets))
    for snapshot in db.scalars(select(PaperInstanceItem.question_snapshot)):
        if isinstance(snapshot, dict):
            paths.update(_asset_paths(snapshot.get("assets")))
    return {path for path in paths if _teacher_asset_filename(path) is not None}


def delete_teacher_asset_if_unreferenced(db: Session, source_path: str) -> bool:
    filename = _teacher_asset_filename(source_path)
    if filename is None:
        raise ValueError("题目图片路径无效")
    if source_path in referenced_teacher_assets(db):
        raise RuntimeError("题目图片仍被题目或历史试卷引用")
    target = get_settings().teacher_asset_dir / filename
    existed = target.is_file()
    target.unlink(missing_ok=True)
    return existed


def cleanup_orphan_teacher_assets(
    db: Session, *, now: datetime | None = None
) -> int:
    settings = get_settings()
    directory = settings.teacher_asset_dir
    if not directory.exists():
        return 0
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(
        hours=settings.teacher_asset_orphan_grace_hours
    )
    referenced = referenced_teacher_assets(db)
    removed = 0
    for target in directory.iterdir():
        if not target.is_file():
            continue
        source_path = f"assets/teacher/{target.name}"
        is_managed = _teacher_asset_filename(source_path) is not None
        is_temporary = target.name.endswith(".tmp")
        if not is_managed and not is_temporary:
            continue
        modified_at = datetime.fromtimestamp(target.stat().st_mtime, timezone.utc)
        if modified_at > cutoff or source_path in referenced:
            continue
        target.unlink(missing_ok=True)
        removed += 1
    return removed


def store_teacher_asset(payload: bytes) -> dict[str, str]:
    """Validate and normalize a teacher-uploaded image into a safe PNG asset."""
    settings = get_settings()
    try:
        with Image.open(BytesIO(payload)) as source:
            if source.width * source.height > settings.question_asset_max_pixels:
                raise ValueError("图片像素过大")
            source.load()
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
