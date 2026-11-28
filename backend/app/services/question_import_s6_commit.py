"""Transactional formal import of teacher approved S5 candidates."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    KnowledgePoint, Question, QuestionImportAuditEvent, QuestionImportBatch,
    QuestionImportCommit, QuestionImportItem,
)
from app.schemas.question_import_s6 import S6CommitRequest, S6PreflightRequest
from app.schemas.questions import QuestionReviewRequest
from app.services.knowledge_catalog import formal_question_literacy
from app.services.question_import import ImportProblem, OWNER_SCOPE, content_fingerprint
from app.services.question_import_s3_images import image_bytes
from app.services.question_import_s6_preflight import preflight_commit
from app.services.question_review import promote_question


def _request_signature(request: S6CommitRequest) -> str:
    rows = sorted((str(row.item_id), row.expected_revision, str(row.question_event_id))
                  for row in request.items)
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def _source_file(batch: QuestionImportBatch, payload: dict[str, Any]) -> str:
    source = payload.get("source") or {}
    file_id = source.get("file_id")
    for row in batch.files or []:
        if row.get("file_id") == file_id:
            return str(row.get("original_name") or f"bulk-import:{batch.id}")
    return f"bulk-import:{batch.id}"


def _source_number(item: QuestionImportItem) -> int:
    raw = ((item.payload or {}).get("source") or {}).get("original_number")
    found = re.search(r"\d+", str(raw)) if raw is not None else None
    return int(found.group()) if found else item.ordinal


def _copy_assets(db: Session, batch_id: uuid.UUID, item: QuestionImportItem,
                 created: list[Path]) -> list[dict[str, str]]:
    target_dir = get_settings().teacher_asset_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    output = []
    for raw_id in ((item.payload or {}).get("content") or {}).get("asset_ids") or []:
        asset_id = uuid.UUID(str(raw_id))
        data = image_bytes(db, batch_id, asset_id)
        filename = f"{uuid.uuid5(batch_id, str(asset_id)).hex}.png"
        target = target_dir / filename
        try:
            with target.open("xb") as file:
                created.append(target)
                file.write(data)
        except FileExistsError:
            if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(data).digest():
                raise ImportProblem("ASSET_CONFLICT", "正式题图文件冲突，请联系管理员", 409)
        output.append({"path": f"assets/teacher/{filename}"})
    return output


def _answer_event(db: Session, batch_id: uuid.UUID, item_id: uuid.UUID,
                  question_event_id: uuid.UUID) -> QuestionImportAuditEvent:
    question_event = db.get(QuestionImportAuditEvent, question_event_id)
    if question_event is None or question_event.batch_id != batch_id or question_event.item_id != item_id:
        raise ImportProblem("REVIEW_CHANGED", "教师审核记录已变化，请刷新后重试", 409)
    events = db.scalars(select(QuestionImportAuditEvent).where(
        QuestionImportAuditEvent.batch_id == batch_id,
        QuestionImportAuditEvent.item_id == item_id,
        QuestionImportAuditEvent.kind == "answer",
    ).order_by(QuestionImportAuditEvent.created_at.desc(), QuestionImportAuditEvent.id.desc()))
    event = next((row for row in events
                  if row.detail.get("action_id") == question_event.detail.get("action_id")), None)
    if (event is None or event.detail.get("action_id") != question_event.detail.get("action_id")
            or event.signature != question_event.signature
            or event.candidate_revision != question_event.candidate_revision):
        raise ImportProblem("REVIEW_CHANGED", "答案审核记录已变化，请刷新后重试", 409)
    return event


def commit_import(db: Session, batch_id: uuid.UUID, request: S6CommitRequest) -> dict[str, Any]:
    """Commit the selected set once, with fresh checks under catalog and candidate locks."""
    signature = _request_signature(request)
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE,
    ).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    prior = db.scalar(select(QuestionImportCommit).where(
        QuestionImportCommit.batch_id == batch_id,
        QuestionImportCommit.operation_id == request.operation_id,
    ))
    if prior:
        if prior.request_signature != signature:
            raise ImportProblem("OPERATION_CONFLICT", "同一操作编号对应不同题目清单", 409)
        return {**prior.result, "replayed": True}

    ids = [row.item_id for row in request.items]
    locked = {item.id: item for item in db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch_id, QuestionImportItem.id.in_(ids),
    ).order_by(QuestionImportItem.id).with_for_update())}
    # Existing catalog writers use INSERT/UPDATE on questions. This lock serializes
    # the final duplicate check with those writes, including concurrent S6 commits.
    db.execute(text("LOCK TABLE questions IN SHARE ROW EXCLUSIVE MODE"))
    check = preflight_commit(db, batch_id, S6PreflightRequest(items=request.items))
    if check["summary"]["blocked"]:
        raise ImportProblem("S6_COMMIT_BLOCKED", "所选题目有阻断项，本次没有入库", 409,
                            details=check["items"])
    rows_by_id = {row["item_id"]: row for row in check["items"]}
    kp_by_code = {kp.code: kp for kp in db.scalars(select(KnowledgePoint))}
    created_files: list[Path] = []
    saved: list[dict[str, Any]] = []
    try:
        for selection in request.items:
            item = locked[selection.item_id]
            payload = item.payload or {}
            content = payload["content"]
            tags = payload["tags"]
            kp_code = tags["main_kp_code"]
            kp = kp_by_code.get(kp_code)
            if kp is None:
                raise ImportProblem("TAG_OUT_OF_SCOPE", "正式知识点不存在", 409)
            assets = _copy_assets(db, batch_id, item, created_files)
            selected_answer = next((row.get("text") for row in payload.get("answers") or []
                                    if row.get("answer_id") == payload.get("selected_answer_id")), None)
            route = rows_by_id[str(item.id)]["route"]
            question = Question(
                id=uuid.uuid4(), original_qid=f"bulk.{batch_id.hex}.{item.id.hex}",
                schema_version="question-v1", chapter_code=kp.chapter_code,
                chapter_name=kp.chapter_name, grade=tags["grade"], section=tags["section"],
                question_type=tags["question_type"], difficulty=tags["difficulty"],
                main_knowledge_point_id=kp.id, question_text=content["question_text"],
                options=content["options"], correct_answer=None,
                reference_answer_raw=selected_answer or "", answer_match_type=None,
                answer_normalization_rule={}, solution=None, wrong_analysis=None,
                estimated_score_rate=None, core_literacy=formal_question_literacy(kp_code),
                is_required=tags["is_required"], has_image=bool(assets),
                has_equation=any(mark in content["question_text"] for mark in ("$", "\\(", "\\[")),
                assets=assets, source_file=_source_file(batch, payload),
                source_question_number=_source_number(item),
                content_fingerprint=content_fingerprint(content["question_text"], content["options"], assets),
                exact_fingerprint_v2=rows_by_id[str(item.id)]["fingerprint"],
                import_batch_id=batch_id, import_item_id=item.id,
                source_review_status="teacher_approved", grading_readiness="raw_answer_only",
                import_status="reviewed", import_issues=["answer_items_not_structured"] if route == "library_only" else [],
                review_revision=1, review_history=[{
                    "type": "s6_import", "operation_id": str(request.operation_id),
                    "question_event_id": str(selection.question_event_id), "route": route,
                    "candidate_revision": item.revision,
                }],
            )
            db.add(question)
            db.flush()
            if route == "direct_pool":
                answer_event = _answer_event(db, batch_id, item.id, selection.question_event_id)
                promote_question(db, question.id, QuestionReviewRequest(
                    expected_review_revision=1, reviewer_name=OWNER_SCOPE,
                    review_note="S5 教师确认后正式入池",
                    items=answer_event.detail["review_items"],
                ))
            now = datetime.now(timezone.utc).isoformat()
            item.payload = {**payload, "imported_question_id": str(question.id),
                            "imported_at": now, "import_operation_id": str(request.operation_id)}
            db.add(QuestionImportAuditEvent(
                batch_id=batch_id, item_id=item.id, kind="commit", decision="committed",
                candidate_revision=item.revision, signature=signature,
                detail={"operation_id": str(request.operation_id), "question_id": str(question.id),
                        "question_event_id": str(selection.question_event_id), "route": route},
                actor_scope=OWNER_SCOPE,
            ))
            saved.append({"item_id": str(item.id), "question_id": str(question.id),
                          "route": route, "grading_readiness": question.grading_readiness})
        result = {"batch_id": str(batch_id), "operation_id": str(request.operation_id),
                  "count": len(saved), "items": saved, "replayed": False}
        db.add(QuestionImportCommit(batch_id=batch_id, operation_id=request.operation_id,
                                    request_signature=signature, result=result))
        db.commit()
        return result
    except Exception:
        db.rollback()
        for path in created_files:
            path.unlink(missing_ok=True)
        raise
