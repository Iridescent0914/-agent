"""Private, reviewable image staging for S3 question imports.

Embedded images are inventoried by their actual PDF placement. Missing regions
are cropped from model coordinates. Teachers can upload or unlink candidate images.
"""

from __future__ import annotations

import hashlib
import math
import os
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import pymupdf as fitz
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import QuestionImportBatch, QuestionImportItem
from app.services.question_import import OWNER_SCOPE, ImportProblem, _editable_after_dedupe, active_candidate, get_batch
from app.services.question_import_s2 import prepare_pdf


IMAGE_VERSION = "s3-question-images-v1"
MAX_STAGED_IMAGES = 500
REVIEW_WARNING = "题图已关联，须对照原页核对图片内容和完整性"
UNEXTRACTED_WARNING = "题图尚未裁剪；请对照原页复核"


def _live_batch(db: Session, batch_id: uuid.UUID, *, lock: bool = False,
                s2_worker_token: uuid.UUID | None = None) -> QuestionImportBatch:
    if s2_worker_token is not None:
        batch = db.scalar(select(QuestionImportBatch).where(
            QuestionImportBatch.id == batch_id,
            QuestionImportBatch.owner_scope == OWNER_SCOPE,
            QuestionImportBatch.worker_token == s2_worker_token,
            QuestionImportBatch.status == "parsing",
            QuestionImportBatch.stage == "s2_processing",
        ).with_for_update())
        if batch is None:
            raise ImportProblem("COMMIT_CONFLICT", "S2 题图处理任务已失效", 409)
    elif lock:
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
    if s2_worker_token is None and (batch.status != "review_pending" or batch.stage != "s2_completed"):
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
    items = list(db.scalars(select(QuestionImportItem).where(QuestionImportItem.batch_id == batch_id)))
    active = {str(item.id): item for item in items if active_candidate(item)}
    deleted_ids = {str(item.id) for item in items if not active_candidate(item)}
    assets = [asset for asset in state.get("assets") or []
              if not (asset.get("kind") == "upload" and asset.get("uploaded_for_item_id") in deleted_ids)]
    hashes = {asset["asset_id"]: asset.get("sha256") for asset in assets}
    removed_rows = ((batch.checkpoint or {}).get("s3") or {}).get("model_removed_duplicates") or {}
    removed_to_kept = {row["removed_item_id"]: row["kept_item_id"] for row in removed_rows.values()}
    removed_images: dict[str, set[str]] = {}
    for row in removed_rows.values():
        kept_id = row["kept_item_id"]
        for _ in range(len(removed_to_kept) + 1):
            if kept_id not in removed_to_kept:
                break
            kept_id = removed_to_kept[kept_id]
        if kept_id in active:
            for asset_id in (row.get("removed_payload") or {}).get("content", {}).get("asset_ids") or []:
                removed_images.setdefault(str(asset_id), set()).add(kept_id)
    for item in items:
        for asset_id in (item.payload.get("content") or {}).get("asset_ids") or []:
            linked.setdefault(str(asset_id), []).append(str(item.id))
    def decorated(asset: dict[str, Any]) -> dict[str, Any]:
        asset_id = asset["asset_id"]
        keepers = sorted(removed_images.get(asset_id) or [])
        exact_duplicate = any(
            hashes.get(asset_id) is not None and hashes[asset_id] == hashes.get(kept_asset_id)
            for kept_id in keepers
            for kept_asset_id in (active[kept_id].payload.get("content") or {}).get("asset_ids") or []
        )
        return {**asset, "candidate_item_ids": linked.get(asset_id, []),
                "removed_candidate_for_item_ids": keepers,
                "duplicate_of_kept_image": exact_duplicate}
    return {"version": state.get("version"), "page_count": state.get("page_count"),
            "embedded_scanned": state.get("embedded_scanned", bool(state.get("version"))),
            "files_available": batch.files_purged_at is None and batch.expires_at > datetime.now(timezone.utc),
            "assets": [decorated(asset) for asset in assets]}


def extract_images(db: Session, batch_id: uuid.UUID, *,
                   s2_worker_token: uuid.UUID | None = None) -> dict[str, Any]:
    batch = _live_batch(db, batch_id, lock=True, s2_worker_token=s2_worker_token)
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


def _crop_model_figure(db: Session, batch_id: uuid.UUID, page_number: int, box: list[float],
                       *, s2_worker_token: uuid.UUID) -> dict[str, Any]:
    """Render only a validated model region during the active S2 job."""
    batch = _live_batch(db, batch_id, lock=True, s2_worker_token=s2_worker_token)
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
    asset = {"asset_id": str(asset_id), "kind": "model_crop", "source_file_id": source["file_id"],
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


def question_figure_assets(db: Session, batch_id: uuid.UUID, s2_worker_token: uuid.UUID,
                           source_file_id: str, unit_first_page: int, question: Any) -> tuple[list[str], list[str]]:
    """Resolve one S2 question's model figure references without trusting coordinates or IDs."""
    batch = _live_batch(db, batch_id, s2_worker_token=s2_worker_token)
    existing = {row["asset_id"]: row for row in _image_state(batch).get("assets") or []
                if row["kind"] == "embedded" and row["source_file_id"] == source_file_id}
    asset_ids: list[str] = []
    issues: list[str] = []
    if len(question.figures) > 20:
        return [], ["模型给出的单题题图超过 20 处，未自动关联"]
    if question.figure_status == "no_figure" and question.figures:
        return [], ["模型题图状态与返回的图片不一致，未自动关联"]
    for figure in question.figures:
        absolute_page = unit_first_page + figure.page - 1
        if not question.page_start <= figure.page <= question.page_end:
            issues.append("模型题图页码不在该题来源页段内")
            continue
        if figure.asset_id is not None:
            asset = existing.get(figure.asset_id)
            if asset is None or asset["page"] != absolute_page or figure.bbox is not None:
                issues.append("模型引用的自动图片与来源页不一致")
                continue
            embedded_box = asset["bbox"]
            if embedded_box[2] - embedded_box[0] >= 0.9 and embedded_box[3] - embedded_box[1] >= 0.9:
                issues.append("模型引用的是整页扫描图，无法作为独立题图关联")
                continue
        else:
            box = figure.bbox
            if (box is None or len(box) != 4 or any(not isinstance(value, (int, float))
                                                   or isinstance(value, bool) or not math.isfinite(value)
                                                   or not 0 <= value <= 999 for value in box)
                    or box[2] - box[0] < 5 or box[3] - box[1] < 5):
                issues.append("模型题图坐标无效")
                continue
            try:
                asset = _crop_model_figure(db, batch_id, absolute_page,
                                           [round(value / 999, 6) for value in box],
                                           s2_worker_token=s2_worker_token)
            except ImportProblem as exc:
                issues.append(f"模型题图裁剪失败：{exc.code}")
                continue
        if asset["asset_id"] not in asset_ids:
            asset_ids.append(asset["asset_id"])
    if (question.figure_status == "uncertain" and (question.diagram_clues or question.figures)
            or (question.diagram_clues and not asset_ids)):
        issues.append("模型未确认全部题图已定位")
    return asset_ids, issues


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
    _editable_after_dedupe(batch)
    asset = next((row for row in _image_state(batch).get("assets") or []
                  if row["asset_id"] == str(asset_id)), None)
    if asset is None or not _asset_path(batch_id, asset_id).is_file():
        raise ImportProblem("NOT_FOUND", "题图不存在或已过期", 404)
    item = db.scalar(select(QuestionImportItem).where(
        QuestionImportItem.id == item_id, QuestionImportItem.batch_id == batch_id,
    ).with_for_update())
    if item is None or not active_candidate(item):
        raise ImportProblem("NOT_FOUND", "候选题不存在", 404)
    if item.revision != expected_revision:
        raise ImportProblem("REVISION_CONFLICT", "候选题已被修改，请刷新", 409)
    if asset.get("kind") == "upload" and asset.get("uploaded_for_item_id") != str(item_id):
        raise ImportProblem("IMAGE_SOURCE_CONFLICT", "上传图片只可关联到原候选题", 409)
    source = item.payload.get("source") or {}
    # A teacher may match a figure repeated later in the same question file to an earlier question.
    if linked and source.get("file_id") != asset["source_file_id"]:
        raise ImportProblem("IMAGE_SOURCE_CONFLICT", "题图与候选题不属于同一题目文件", 409)
    payload = dict(item.payload)
    content = dict(payload.get("content") or {})
    asset_ids = list(content.get("asset_ids") or [])
    was_linked = str(asset_id) in asset_ids
    if linked and str(asset_id) not in asset_ids:
        asset_ids.append(str(asset_id))
    if not linked:
        asset_ids = [value for value in asset_ids if value != str(asset_id)]
    content["asset_ids"] = asset_ids
    payload["content"] = content
    warnings = [warning for warning in payload.get("warnings") or []
                if warning not in {UNEXTRACTED_WARNING, REVIEW_WARNING}]
    if ((payload.get("extraction") or {}).get("diagram_clues") or
            (payload.get("extraction") or {}).get("figure_status") in {"complete", "uncertain"}):
        warnings.append(REVIEW_WARNING if asset_ids else UNEXTRACTED_WARNING)
    payload["warnings"] = warnings
    item.revision += 1
    payload["revision"] = item.revision
    if payload.get("label_suggestion"):
        payload["label_suggestion"] = {**payload["label_suggestion"], "revision": item.revision}
    item.payload = payload
    if not linked and was_linked and asset.get("kind") == "upload":
        checkpoint = dict(batch.checkpoint or {})
        s3 = dict(checkpoint.get("s3") or {})
        images = dict(s3.get("images") or {})
        images["assets"] = [row for row in images.get("assets") or [] if row["asset_id"] != str(asset_id)]
        s3["images"] = images
        checkpoint["s3"] = s3
        batch.checkpoint = checkpoint
    db.flush()
    if not linked and was_linked and asset.get("kind") == "upload":
        _asset_path(batch_id, asset_id).unlink(missing_ok=True)
    return item


def upload_candidate_image(db: Session, batch_id: uuid.UUID, item_id: uuid.UUID,
                           payload_bytes: bytes, *, expected_revision: int) -> QuestionImportItem:
    """Normalize a teacher image and attach it to exactly one staged candidate."""
    batch = _live_batch(db, batch_id, lock=True)
    _editable_after_dedupe(batch)
    item = db.scalar(select(QuestionImportItem).where(
        QuestionImportItem.id == item_id, QuestionImportItem.batch_id == batch_id,
    ).with_for_update())
    if item is None or not active_candidate(item):
        raise ImportProblem("NOT_FOUND", "候选题不存在", 404)
    if item.revision != expected_revision:
        raise ImportProblem("REVISION_CONFLICT", "候选题已被修改，请刷新", 409)
    state = _image_state(batch)
    source_file = _question_file(batch)
    if state.get("version") and (state.get("version") != IMAGE_VERSION or
                                 state.get("source_sha256") != source_file["sha256"]):
        raise ImportProblem("SOURCE_CHANGED", "题图暂存与当前题目文件不一致，请重新上传", 409)
    if len(state.get("assets") or []) >= MAX_STAGED_IMAGES:
        raise ImportProblem("IMAGE_LIMIT_EXCEEDED", "题图数量达到暂存上限", 413)
    try:
        with Image.open(BytesIO(payload_bytes)) as source:
            if source.width * source.height > get_settings().question_asset_max_pixels:
                raise ImportProblem("IMAGE_LIMIT_EXCEEDED", "图片像素过大", 413)
            source.load()
            image = ImageOps.exif_transpose(source)
            has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
            image = image.convert("RGBA" if has_alpha else "RGB")
            output = BytesIO()
            image.save(output, format="PNG", optimize=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImportProblem("INVALID_IMAGE", "文件不是有效图片", 422) from exc
    data = output.getvalue()
    asset_id = uuid.uuid4()
    _save_png(batch_id, asset_id, data)
    source = item.payload.get("source") or {}
    asset = {"asset_id": str(asset_id), "kind": "upload", "source_file_id": source.get("file_id"),
             "uploaded_for_item_id": str(item_id),
             "page": source.get("page_start"), "bbox": None,
             "sha256": hashlib.sha256(data).hexdigest(), "width": image.width, "height": image.height}
    checkpoint = dict(batch.checkpoint or {})
    s3 = dict(checkpoint.get("s3") or {})
    s3["images"] = {**state, "version": IMAGE_VERSION,
                    "source_sha256": source_file["sha256"],
                    "source_file_id": source_file["file_id"],
                    "embedded_scanned": state.get("embedded_scanned", False),
                    "assets": [*(state.get("assets") or []), asset]}
    checkpoint["s3"] = s3
    batch.checkpoint = checkpoint
    candidate_payload = dict(item.payload)
    content = dict(candidate_payload.get("content") or {})
    content["asset_ids"] = [*(content.get("asset_ids") or []), str(asset_id)]
    candidate_payload["content"] = content
    candidate_payload["warnings"] = [warning for warning in candidate_payload.get("warnings") or []
                                     if warning not in {UNEXTRACTED_WARNING, REVIEW_WARNING}]
    item.revision += 1
    candidate_payload["revision"] = item.revision
    if candidate_payload.get("label_suggestion"):
        candidate_payload["label_suggestion"] = {**candidate_payload["label_suggestion"],
                                                 "revision": item.revision}
    item.payload = candidate_payload
    db.flush()
    return item
