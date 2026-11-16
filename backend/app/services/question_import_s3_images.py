"""Private, reviewable image staging for S3 question imports.

Embedded images are inventoried by their actual PDF placement. Vector drawings
and mixed page content are captured only from a rectangle selected on the page.
Neither path guesses which question owns an image.
"""

from __future__ import annotations

import hashlib
import math
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymupdf as fitz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import QuestionImportBatch, QuestionImportItem
from app.services.question_import import OWNER_SCOPE, ImportProblem, get_batch
from app.services.question_import_s2 import prepare_pdf


IMAGE_VERSION = "s3-question-images-v1"
MAX_STAGED_IMAGES = 500
REVIEW_WARNING = "题图已关联，须对照原页核对图片内容和完整性"
UNEXTRACTED_WARNING = "题图尚未裁剪；请对照原页复核"


def _live_batch(db: Session, batch_id: uuid.UUID, *, lock: bool = False) -> QuestionImportBatch:
    if lock:
        batch = db.scalar(select(QuestionImportBatch).where(
            QuestionImportBatch.id == batch_id,
            QuestionImportBatch.owner_scope == OWNER_SCOPE,
        ).with_for_update())
        if batch is None:
            raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    else:
        batch = get_batch(db, batch_id)
    if batch.files_purged_at is not None or batch.expires_at <= datetime.now(timezone.utc):
        raise ImportProblem("FILE_EXPIRED", "原文件和临时题图已过期，请重新上传", 409)
    if batch.status != "review_pending" or batch.stage != "s2_completed":
        raise ImportProblem("COMMIT_CONFLICT", "请先完成题目文件识别", 409)
    return batch


def _question_file(batch: QuestionImportBatch) -> dict[str, Any]:
    return next(file for file in batch.files if file["role"] == "questions")


def _image_state(batch: QuestionImportBatch) -> dict[str, Any]:
    return ((batch.checkpoint or {}).get("s3") or {}).get("images") or {}


def _asset_path(batch_id: uuid.UUID, asset_id: uuid.UUID) -> Path:
    # Flat files are removed by the existing 72-hour import purge.
    return get_settings().question_import_dir / str(batch_id) / f"s3-image-{asset_id.hex}.png"


def _save_png(batch_id: uuid.UUID, asset_id: uuid.UUID, data: bytes) -> None:
    target = _asset_path(batch_id, asset_id)
    temporary = target.with_name(target.name + ".tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _normalized_box(rect: fitz.Rect, page_rect: fitz.Rect) -> list[float]:
    return [round((rect.x0 - page_rect.x0) / page_rect.width, 6),
            round((rect.y0 - page_rect.y0) / page_rect.height, 6),
            round((rect.x1 - page_rect.x0) / page_rect.width, 6),
            round((rect.y1 - page_rect.y0) / page_rect.height, 6)]


def _render(page: fitz.Page, rect: fitz.Rect, *, scale: float = 2.0) -> tuple[bytes, int, int]:
    pixels = math.ceil(rect.width * scale) * math.ceil(rect.height * scale)
    if pixels < 16 or pixels > get_settings().question_asset_max_pixels:
        raise ImportProblem("IMAGE_LIMIT_EXCEEDED", "题图区域过小或像素超过上限", 413)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False)
    return pixmap.tobytes("png"), pixmap.width, pixmap.height


def _visible_rect(page: fitz.Page, box: tuple[float, float, float, float]) -> fitz.Rect | None:
    rect = fitz.Rect(box) & page.rect
    if rect.is_empty or rect.width < 2 or rect.height < 2:
        return None
    return rect


def list_images(db: Session, batch_id: uuid.UUID) -> dict[str, Any]:
    batch = get_batch(db, batch_id)
    state = _image_state(batch)
    linked: dict[str, list[str]] = {}
    for item in db.scalars(select(QuestionImportItem).where(QuestionImportItem.batch_id == batch_id)):
        for asset_id in (item.payload.get("content") or {}).get("asset_ids") or []:
            linked.setdefault(str(asset_id), []).append(str(item.id))
    return {"version": state.get("version"), "page_count": state.get("page_count"),
            "embedded_scanned": state.get("embedded_scanned", bool(state.get("version"))),
            "files_available": batch.files_purged_at is None and batch.expires_at > datetime.now(timezone.utc),
            "assets": [{**asset, "candidate_item_ids": linked.get(asset["asset_id"], [])}
                       for asset in state.get("assets") or []]}


def extract_images(db: Session, batch_id: uuid.UUID) -> dict[str, Any]:
    batch = _live_batch(db, batch_id, lock=True)
    source = _question_file(batch)
    state = _image_state(batch)
    if state.get("version") and (state.get("version") != IMAGE_VERSION or
                                 state.get("source_sha256") != source["sha256"]):
        raise ImportProblem("SOURCE_CHANGED", "题图暂存与当前题目文件不一致，请重新上传", 409)
    if state.get("version") == IMAGE_VERSION:
        if not all(_asset_path(batch_id, uuid.UUID(row["asset_id"])).is_file()
                   for row in state.get("assets") or []):
            raise ImportProblem("FILE_EXPIRED", "已暂存题图文件缺失，请重新上传", 409)
        if state.get("embedded_scanned", True):
            return list_images(db, batch_id)
    pdf, _, _ = prepare_pdf(batch_id, source)
    assets: list[dict[str, Any]] = list(state.get("assets") or [])
    with fitz.open(pdf) as document:
        for page_number, page in enumerate(document, start=1):
            seen: set[tuple[float, float, float, float]] = set()
            for info in page.get_image_info(xrefs=True):
                rect = _visible_rect(page, info["bbox"])
                if rect is None:
                    continue
                box = tuple(_normalized_box(rect, page.rect))
                if box in seen:
                    continue
                seen.add(box)
                if len(assets) >= MAX_STAGED_IMAGES:
                    raise ImportProblem("IMAGE_LIMIT_EXCEEDED", "嵌入图片超过单批上限，请拆分文件", 413)
                asset_id = uuid.uuid5(batch_id, f"{IMAGE_VERSION}:{source['sha256']}:{page_number}:{box}")
                data, width, height = _render(page, rect)
                _save_png(batch_id, asset_id, data)
                assets.append({"asset_id": str(asset_id), "kind": "embedded", "source_file_id": source["file_id"],
                               "page": page_number, "bbox": list(box), "sha256": hashlib.sha256(data).hexdigest(),
                               "width": width, "height": height})
        page_count = len(document)
    checkpoint = dict(batch.checkpoint or {})
    s3 = dict(checkpoint.get("s3") or {})
    s3["images"] = {"version": IMAGE_VERSION, "source_sha256": source["sha256"],
                    "source_file_id": source["file_id"], "page_count": page_count,
                    "embedded_scanned": True, "assets": assets}
    checkpoint["s3"] = s3
    batch.checkpoint = checkpoint
    db.flush()
    return list_images(db, batch_id)


def render_page(db: Session, batch_id: uuid.UUID, page_number: int) -> bytes:
    batch = _live_batch(db, batch_id)
    source = _question_file(batch)
    pdf, _, _ = prepare_pdf(batch_id, source)
    with fitz.open(pdf) as document:
        if page_number < 1 or page_number > len(document):
            raise ImportProblem("INVALID_PAGE", "原页页码越界", 404)
        page = document[page_number - 1]
        return _render(page, page.rect, scale=1.5)[0]


def crop_page(db: Session, batch_id: uuid.UUID, page_number: int, box: list[float]) -> dict[str, Any]:
    batch = _live_batch(db, batch_id, lock=True)
    if len(box) != 4 or any(not isinstance(value, (float, int)) or not math.isfinite(value)
                            or not 0 <= value <= 1 for value in box):
        raise ImportProblem("INVALID_IMAGE_BOX", "裁剪框必须是原页内的比例坐标")
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        raise ImportProblem("INVALID_IMAGE_BOX", "裁剪框宽高必须大于零")
    state = _image_state(batch)
    source = _question_file(batch)
    if state.get("version") and (state.get("version") != IMAGE_VERSION or
                                 state.get("source_sha256") != source["sha256"]):
        raise ImportProblem("SOURCE_CHANGED", "题图暂存与当前题目文件不一致，请重新上传", 409)
    assets = list(state.get("assets") or [])
    if len(assets) >= MAX_STAGED_IMAGES:
        raise ImportProblem("IMAGE_LIMIT_EXCEEDED", "题图数量达到单批上限", 413)
    pdf, _, _ = prepare_pdf(batch_id, source)
    with fitz.open(pdf) as document:
        if page_number < 1 or page_number > len(document):
            raise ImportProblem("INVALID_PAGE", "原页页码越界", 404)
        page = document[page_number - 1]
        rect = fitz.Rect(page.rect.x0 + x0 * page.rect.width, page.rect.y0 + y0 * page.rect.height,
                         page.rect.x0 + x1 * page.rect.width, page.rect.y0 + y1 * page.rect.height)
        data, width, height = _render(page, rect)
        page_count = len(document)
    asset_id = uuid.uuid4()
    _save_png(batch_id, asset_id, data)
    asset = {"asset_id": str(asset_id), "kind": "crop", "source_file_id": source["file_id"],
             "page": page_number, "bbox": [round(float(value), 6) for value in box],
             "sha256": hashlib.sha256(data).hexdigest(), "width": width, "height": height}
    assets.append(asset)
    checkpoint = dict(batch.checkpoint or {})
    s3 = dict(checkpoint.get("s3") or {})
    s3["images"] = {**state, "version": IMAGE_VERSION, "source_sha256": source["sha256"],
                    "source_file_id": source["file_id"], "page_count": page_count,
                    "embedded_scanned": state.get("embedded_scanned", bool(state.get("version"))),
                    "assets": assets}
    checkpoint["s3"] = s3
    batch.checkpoint = checkpoint
    db.flush()
    return asset


def image_bytes(db: Session, batch_id: uuid.UUID, asset_id: uuid.UUID) -> bytes:
    batch = _live_batch(db, batch_id)
    if not any(row["asset_id"] == str(asset_id) for row in _image_state(batch).get("assets") or []):
        raise ImportProblem("NOT_FOUND", "题图不存在", 404)
    path = _asset_path(batch_id, asset_id)
    if not path.is_file():
        raise ImportProblem("FILE_EXPIRED", "题图文件已不存在", 409)
    return path.read_bytes()


def set_candidate_image(db: Session, batch_id: uuid.UUID, item_id: uuid.UUID,
                        asset_id: uuid.UUID, *, expected_revision: int, linked: bool) -> QuestionImportItem:
    batch = _live_batch(db, batch_id, lock=True)
    asset = next((row for row in _image_state(batch).get("assets") or []
                  if row["asset_id"] == str(asset_id)), None)
    if asset is None or not _asset_path(batch_id, asset_id).is_file():
        raise ImportProblem("NOT_FOUND", "题图不存在或已过期", 404)
    item = db.scalar(select(QuestionImportItem).where(
        QuestionImportItem.id == item_id, QuestionImportItem.batch_id == batch_id,
    ).with_for_update())
    if item is None:
        raise ImportProblem("NOT_FOUND", "候选题不存在", 404)
    if item.revision != expected_revision:
        raise ImportProblem("REVISION_CONFLICT", "候选题已被修改，请刷新", 409)
    source = item.payload.get("source") or {}
    if (source.get("file_id") != asset["source_file_id"] or
            not isinstance(source.get("page_start"), int) or
            not isinstance(source.get("page_end"), int) or
            not source["page_start"] <= asset["page"] <= source["page_end"]):
        raise ImportProblem("IMAGE_SOURCE_CONFLICT", "题图不在该题的原文件页段内，请核对原页", 409)
    payload = dict(item.payload)
    content = dict(payload.get("content") or {})
    asset_ids = list(content.get("asset_ids") or [])
    if linked and str(asset_id) not in asset_ids:
        asset_ids.append(str(asset_id))
    if not linked:
        asset_ids = [value for value in asset_ids if value != str(asset_id)]
    content["asset_ids"] = asset_ids
    payload["content"] = content
    warnings = [warning for warning in payload.get("warnings") or []
                if warning not in {UNEXTRACTED_WARNING, REVIEW_WARNING}]
    if (payload.get("extraction") or {}).get("diagram_clues"):
        warnings.append(REVIEW_WARNING if asset_ids else UNEXTRACTED_WARNING)
    payload["warnings"] = warnings
    item.revision += 1
    payload["revision"] = item.revision
    item.payload = payload
    db.flush()
    return item
