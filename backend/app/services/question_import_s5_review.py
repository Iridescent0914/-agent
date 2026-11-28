"""Version-bound S5 review actions. No formal questions are created here."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    QuestionImportAuditEvent, QuestionImportBatch, QuestionImportCatalogCheck,
    QuestionImportCatalogMatch, QuestionImportItem,
)
from app.schemas.question_import_s5 import (
    S5AnswerClaimRequest, S5AnswerSourceRequest, S5BulkDecisionRequest, S5BulkRevokeRequest,
    S5IssueDecisionRequest, S5ItemDecisionRequest,
    S5PreflightSelection,
)
from app.services.question_import import ImportProblem, OWNER_SCOPE, active_candidate
from app.services.question_import_s3 import linked_source_answer_item, source_answer_identity
from app.services.question_import_s3_review import list_review_issues
from app.services.question_import_s5_preflight import _context, _evaluate
from app.services.question_review import prepare_pool_review


def _signature(item: QuestionImportItem, review: dict[str, Any] | None, *, legacy: bool = False) -> str:
    payload = item.payload or {}
    selected = next((answer for answer in payload.get("answers") or []
                     if isinstance(answer, dict) and answer.get("answer_id") == payload.get("selected_answer_id")), None)
    if legacy:
        # S5 events created before this fix included IDs that change on every S4 recheck.
        s4 = ({"check_id": review.get("check_id"), "candidate_stale": review.get("candidate_stale"),
               "catalog_stale": review.get("catalog_stale"),
               "matches": [(row["id"], row["decision"], row["reason"], row["catalog_stale"])
                           for row in review["matches"]]} if review else None)
    else:
        s4 = ({"candidate_signature": review.get("candidate_signature"),
               "exact_version": review.get("exact_version"), "near_version": review.get("near_version"),
               "candidate_stale": review.get("candidate_stale"), "catalog_stale": review.get("catalog_stale"),
               "matches": sorted(({
                   "question_id": row["question_id"], "kind": row["kind"],
                   "catalog_review_revision": row["catalog_review_revision"],
                   "catalog_signature": row["catalog_signature"],
                   "catalog_unverifiable": row["catalog_unverifiable"],
                   "decision": row["decision"], "reason": row["reason"],
                   "catalog_stale": row["catalog_stale"], "evidence": row["evidence"],
               } for row in review["matches"]), key=lambda row: (row["kind"], row["question_id"] or ""))}
              if review else None)
    raw = {
        "content": payload.get("content"), "tags": payload.get("tags"),
        "selected_answer_id": payload.get("selected_answer_id"),
        "selected_answer": selected, "answer_match_status": payload.get("answer_match_status"),
        "s4": s4,
    }
    return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


def _stored_s4_state(check: QuestionImportCatalogCheck,
                     matches: list[QuestionImportCatalogMatch]) -> tuple[Any, ...]:
    """Compare durable S4 evidence without the IDs of individual check runs."""
    return (check.candidate_signature, check.exact_version, check.near_version,
            tuple(sorted((str(match.question_id), match.original_qid, match.kind,
                          match.catalog_review_revision, match.catalog_signature,
                          match.decision, match.reason,
                          json.dumps(match.evidence or {}, sort_keys=True, ensure_ascii=False))
                         for match in matches)))


def _legacy_review(check: QuestionImportCatalogCheck,
                   matches: list[QuestionImportCatalogMatch]) -> dict[str, Any]:
    # An approved review could only have been saved while this check was fresh.
    ordered = sorted(matches, key=lambda match: (match.kind, match.original_qid))
    return {"check_id": str(check.id), "candidate_stale": False, "catalog_stale": False,
            "matches": [{"id": str(match.id), "decision": match.decision,
                         "reason": match.reason, "catalog_stale": False} for match in ordered]}


def _locked_batch_item(db: Session, batch_id: uuid.UUID, item_id: uuid.UUID,
                       expected_revision: int) -> QuestionImportItem:
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE,
    ).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if batch.status != "review_pending" or batch.stage != "s2_completed":
        raise ImportProblem("COMMIT_CONFLICT", "当前批次不可审核", 409)
    item = db.scalar(select(QuestionImportItem).where(
        QuestionImportItem.id == item_id, QuestionImportItem.batch_id == batch_id,
    ).with_for_update())
    if item is None or not active_candidate(item) or item.is_fake:
        raise ImportProblem("NOT_FOUND", "真实候选题不存在", 404)
    if item.revision != expected_revision:
        raise ImportProblem("REVISION_CONFLICT", "候选题已修改，请刷新后重新核对", 409)
    return item


def _event(db: Session, *, batch_id: uuid.UUID, item_id: uuid.UUID | None,
           kind: str, decision: str, revision: int | None, signature: str | None,
           issue_key: str | None = None, reason: str | None = None,
           detail: dict[str, Any] | None = None) -> QuestionImportAuditEvent:
    event = QuestionImportAuditEvent(
        id=uuid.uuid4(), batch_id=batch_id, item_id=item_id, kind=kind,
        decision=decision, candidate_revision=revision, signature=signature,
        issue_key=issue_key, reason=reason, detail=detail or {}, actor_scope=OWNER_SCOPE,
        created_at=datetime.now(timezone.utc),
    )
    db.add(event)
    return event


def decide_item(db: Session, batch_id: uuid.UUID, item_id: uuid.UUID,
                request: S5ItemDecisionRequest, batch_action_id: str | None = None,
                reverts_batch_action_id: str | None = None,
                reverts_question_event_id: str | None = None) -> dict[str, Any]:
    item = _locked_batch_item(db, batch_id, item_id, request.expected_revision)
    ctx = _context(db, batch_id)
    if (item.payload or {}).get("teacher_decision") == "skipped_duplicate":
        raise ImportProblem("DUPLICATE_DECISION_REQUIRED", "请先在题库查重结果中撤销跳过旧题的决定", 409)
    if request.decision == "approved":
        selection = S5PreflightSelection(item_id=item_id, expected_revision=item.revision,
                                         route=request.route, answer_review_items=request.answer_review_items)
        result = _evaluate(ctx, selection)
        if result["blockers"]:
            raise ImportProblem("S5_PREFLIGHT_BLOCKED", "题目尚不能确认：" + "；".join(
                row["message"] for row in result["blockers"]), 409)
    payload = dict(item.payload or {})
    item.revision += 1
    payload.update({"teacher_decision": request.decision,
                    "commit_intent": request.route if request.decision == "approved" else None,
                    "revision": item.revision})
    item.payload = payload
    db.flush()
    signature = _signature(item, ctx["review_by_id"].get(str(item_id)))
    action_id = str(uuid.uuid4())
    detail: dict[str, Any] = {"action_id": action_id, "route": request.route,
                              "signature_version": 2,
                              "s4_check_id": (ctx["review_by_id"].get(str(item_id)) or {}).get("check_id")}
    if batch_action_id:
        detail["batch_action_id"] = batch_action_id
    if reverts_batch_action_id:
        detail["reverts_batch_action_id"] = reverts_batch_action_id
    if reverts_question_event_id:
        detail["reverts_question_event_id"] = reverts_question_event_id
    question = _event(db, batch_id=batch_id, item_id=item_id, kind="question",
                      decision=request.decision, revision=item.revision, signature=signature,
                      reason=(request.reason or "").strip() or None, detail=detail)
    if request.decision == "approved" and request.route == "direct_pool":
        answer, rules = prepare_pool_review(payload["tags"]["question_type"], request.answer_review_items)
        _event(db, batch_id=batch_id, item_id=item_id, kind="answer", decision="approved",
               revision=item.revision, signature=signature,
               detail={"action_id": action_id, "batch_action_id": batch_action_id,
                       "selected_answer_id": payload["selected_answer_id"],
                       "review_items": [row.model_dump(mode="json") for row in request.answer_review_items],
                       "canonical_answer": answer, "normalization_rules": rules})
    db.flush()
    return {"item_id": str(item_id), "revision": item.revision,
            "decision": request.decision, "route": request.route,
            "question_event_id": str(question.id), "answer_confirmed": request.route == "direct_pool"}


def decide_items_bulk(db: Session, batch_id: uuid.UUID,
                      request: S5BulkDecisionRequest) -> dict[str, Any]:
    """Validate every selected item under locks, then write one atomic review action."""
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE,
    ).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if batch.status != "review_pending" or batch.stage != "s2_completed":
        raise ImportProblem("COMMIT_CONFLICT", "当前批次不可审核", 409)
    selected_ids = {row.item_id for row in request.items}
    locked = {item.id: item for item in db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch_id, QuestionImportItem.id.in_(selected_ids),
    ).order_by(QuestionImportItem.id).with_for_update())}
    ctx = _context(db, batch_id)
    effective_reviews = {uuid.UUID(row["item_id"]): row
                         for row in read_item_reviews(db, batch_id)["items"]}
    results: list[dict[str, Any]] = []
    for row in request.items:
        item = locked.get(row.item_id)
        blockers: list[dict[str, str]] = []
        if item is None or not active_candidate(item) or item.is_fake:
            blockers.append({"code": "CANDIDATE_UNAVAILABLE", "message": "候选题不可用"})
        elif item.revision != row.expected_revision:
            blockers.append({"code": "REVISION_CONFLICT", "message": "候选题版本已变化，请刷新"})
        elif (item.payload or {}).get("teacher_decision") == "skipped_duplicate":
            blockers.append({"code": "ALREADY_DECIDED", "message": "此题已决定跳过旧题，请先撤销查重决定"})
        elif ((item.payload or {}).get("teacher_decision") in {"approved", "rejected"}
              and not (effective_reviews.get(item.id) or {}).get("question_event_id")):
            blockers.append({"code": "ALREADY_DECIDED", "message": "此题缺少可核对的审核记录，请单独处理"})
        elif ((effective_reviews.get(item.id) or {}).get("valid")
              and effective_reviews[item.id]["decision"] in {"approved", "rejected"}):
            blockers.append({"code": "ALREADY_DECIDED", "message": "此题已有审核决定，请单独处理"})
        if request.decision == "approved" and not blockers:
            check = _evaluate(ctx, S5PreflightSelection(
                item_id=row.item_id, expected_revision=row.expected_revision,
                route=request.route, answer_review_items=row.answer_review_items))
            blockers.extend(check["blockers"])
        results.append({"item_id": str(row.item_id), "ordinal": item.ordinal if item else None,
                        "blockers": blockers})
    if any(row["blockers"] for row in results):
        raise ImportProblem("S5_BULK_BLOCKED", "所选题目有阻断项，本次批量审核未保存", 409,
                            details=results)
    batch_action_id = str(uuid.uuid4())
    with db.begin_nested():
        saved = [decide_item(db, batch_id, row.item_id, S5ItemDecisionRequest(
            expected_revision=row.expected_revision, decision=request.decision,
            route=request.route, reason=request.reason,
            answer_review_items=row.answer_review_items), batch_action_id=batch_action_id)
                 for row in request.items]
    return {"batch_id": str(batch_id), "batch_action_id": batch_action_id,
            "decision": request.decision, "route": request.route,
            "count": len(saved), "items": saved}


def revoke_bulk_decision(db: Session, batch_id: uuid.UUID,
                         request: S5BulkRevokeRequest) -> dict[str, Any]:
    """Atomically withdraw every currently effective proposed import in this batch."""
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE,
    ).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if batch.status != "review_pending" or batch.stage != "s2_completed":
        raise ImportProblem("COMMIT_CONFLICT", "当前批次不可撤回审核", 409)
    selected_ids = {row.item_id for row in request.items}
    locked = {item.id: item for item in db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch_id, QuestionImportItem.id.in_(selected_ids),
    ).order_by(QuestionImportItem.id).with_for_update())}
    approved = {uuid.UUID(row["item_id"]): row for row in read_item_reviews(db, batch_id)["items"]
                if row["valid"] and row["decision"] == "approved"}
    if not approved or set(approved) != selected_ids or any(
        (item := locked.get(row.item_id)) is None or item.revision != row.expected_revision
        or not active_candidate(item) or item.is_fake
        for row in request.items
    ):
        raise ImportProblem("S5_BULK_REVOKE_CONFLICT",
                            "拟入库清单或题目版本已变化，请刷新后重试", 409)
    undo_action_id = str(uuid.uuid4())
    with db.begin_nested():
        saved = [decide_item(db, batch_id, row.item_id, S5ItemDecisionRequest(
            expected_revision=row.expected_revision, decision="pending"),
            batch_action_id=undo_action_id,
            reverts_batch_action_id=approved[row.item_id].get("batch_action_id"),
            reverts_question_event_id=approved[row.item_id]["question_event_id"])
                 for row in request.items]
    return {"batch_id": str(batch_id), "batch_action_id": undo_action_id,
            "count": len(saved), "items": saved}


def read_item_reviews(db: Session, batch_id: uuid.UUID) -> dict[str, Any]:
    ctx = _context(db, batch_id)
    events = list(db.scalars(select(QuestionImportAuditEvent).where(
        QuestionImportAuditEvent.batch_id == batch_id,
        QuestionImportAuditEvent.kind.in_(["question", "answer"]),
    ).order_by(QuestionImportAuditEvent.created_at.desc(), QuestionImportAuditEvent.id.desc())))
    latest_question: dict[uuid.UUID, QuestionImportAuditEvent] = {}
    answers: dict[str, QuestionImportAuditEvent] = {}
    for event in events:
        if event.kind == "question":
            latest_question.setdefault(event.item_id, event)
        elif event.kind == "answer":
            answers[event.detail.get("action_id")] = event
    legacy_ids = {uuid.UUID(check_id) for event in latest_question.values()
                  if event.detail.get("signature_version") != 2
                  if (check_id := event.detail.get("s4_check_id"))}
    if legacy_ids:
        legacy_ids.update(uuid.UUID(row["check_id"]) for row in ctx["review_by_id"].values()
                          if row.get("check_id"))
    checks = {check.id: check for check in db.scalars(select(QuestionImportCatalogCheck).where(
        QuestionImportCatalogCheck.batch_id == batch_id,
        QuestionImportCatalogCheck.id.in_(legacy_ids),
    ))} if legacy_ids else {}
    matches_by_check: dict[uuid.UUID, list[QuestionImportCatalogMatch]] = {}
    if checks:
        for match in db.scalars(select(QuestionImportCatalogMatch).where(
            QuestionImportCatalogMatch.check_id.in_(checks)
        ).order_by(QuestionImportCatalogMatch.kind, QuestionImportCatalogMatch.original_qid)):
            matches_by_check.setdefault(match.check_id, []).append(match)
    rows = []
    for item in ctx["items"]:
        if not active_candidate(item) or item.is_fake:
            continue
        event = latest_question.get(item.id)
        if event is None:
            rows.append({"item_id": str(item.id), "decision": "pending", "valid": False,
                         "reason": None, "route": None, "question_event_id": None,
                         "batch_action_id": None,
                         "answer_confirmed": False})
            continue
        route = event.detail.get("route")
        current_review = ctx["review_by_id"].get(str(item.id))
        legacy = event.detail.get("signature_version") != 2
        signature_valid = (event.candidate_revision == item.revision
                           and event.signature == _signature(item, current_review, legacy=legacy)
                           and (item.payload or {}).get("teacher_decision") == event.decision)
        if not signature_valid and legacy and event.candidate_revision == item.revision \
                and (item.payload or {}).get("teacher_decision") == event.decision:
            old_check_id = event.detail.get("s4_check_id")
            old_check = checks.get(uuid.UUID(old_check_id)) if old_check_id else None
            current_check = checks.get(uuid.UUID(current_review["check_id"])) if current_review else None
            old_review = (_legacy_review(old_check, matches_by_check.get(old_check.id, []))
                          if old_check else None)
            original_signature_valid = event.signature == _signature(item, old_review, legacy=True)
            s4_unchanged = (event.decision != "approved" or bool(old_check and current_check
                            and _stored_s4_state(old_check, matches_by_check.get(old_check.id, []))
                            == _stored_s4_state(current_check, matches_by_check.get(current_check.id, []))))
            signature_valid = original_signature_valid and s4_unchanged
        answer_event = answers.get(event.detail.get("action_id"))
        answer_valid = (route != "direct_pool" or bool(answer_event
                        and answer_event.candidate_revision == item.revision
                        and answer_event.signature == event.signature
                        and answer_event.detail.get("selected_answer_id") ==
                        (item.payload or {}).get("selected_answer_id")))
        preflight_valid = True
        if event.decision == "approved":
            selection = S5PreflightSelection(
                item_id=item.id, expected_revision=item.revision, route=route,
                answer_review_items=(answer_event.detail.get("review_items") if answer_event else None))
            preflight_valid = _evaluate(ctx, selection)["batch_eligible"]
        valid = bool(signature_valid and answer_valid and preflight_valid)
        rows.append({"item_id": str(item.id), "decision": event.decision if valid else "pending",
                     "recorded_decision": event.decision, "valid": valid,
                     "reason": event.reason, "route": route if valid else None,
                     "batch_action_id": event.detail.get("batch_action_id"),
                     "question_event_id": str(event.id), "candidate_revision": event.candidate_revision,
                     "answer_confirmed": bool(valid and route == "direct_pool" and answer_event),
                     "created_at": event.created_at.isoformat()})
    return {"batch_id": str(batch_id), "items": rows}


def decide_issue(db: Session, batch_id: uuid.UUID,
                 request: S5IssueDecisionRequest) -> dict[str, Any]:
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE,
    ).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if batch.status != "review_pending":
        raise ImportProblem("COMMIT_CONFLICT", "当前批次不可处理问题", 409)
    issue = next((row for row in list_review_issues(db, batch_id)
                  if row["key"] == request.issue_key), None)
    if issue is None:
        raise ImportProblem("NOT_FOUND", "问题已不存在，请刷新", 404)
    if issue["signature"] != request.signature:
        raise ImportProblem("REVISION_CONFLICT", "问题证据已变化，请刷新后重新判断", 409)
    event = _event(db, batch_id=batch_id, item_id=None, kind="issue",
                   decision=request.decision, revision=None, signature=issue["signature"],
                   issue_key=issue["key"], reason=(request.reason or "").strip() or None,
                   detail={"issue_kind": issue["kind"], "candidate_item_ids": issue["candidate_item_ids"]})
    db.flush()
    return {"issue_key": issue["key"], "decision": request.decision,
            "event_id": str(event.id), "signature": issue["signature"]}


def claim_source_answer(db: Session, batch_id: uuid.UUID,
                        request: S5AnswerClaimRequest) -> dict[str, Any]:
    item = _locked_batch_item(db, batch_id, request.item_id, request.expected_revision)
    issue = next((row for row in list_review_issues(db, batch_id)
                  if row["key"] == request.issue_key and row["kind"] == "answer"), None)
    if issue is None:
        raise ImportProblem("NOT_FOUND", "待认领答案已不存在，请刷新", 404)
    if issue["signature"] != request.signature:
        raise ImportProblem("REVISION_CONFLICT", "答案证据已变化，请刷新后重新核对", 409)
    source = issue["source_answer"]
    if not isinstance(source.get("text"), str) or not source["text"].strip():
        raise ImportProblem("ANSWER_MISSING", "来源答案内容为空")
    batch = db.get(QuestionImportBatch, batch_id)
    if source.get("source_file_id") not in {row.get("file_id") for row in batch.files}:
        raise ImportProblem("ANSWER_SOURCE_UNCERTAIN", "答案来源文件不属于当前批次", 409)
    identity = source_answer_identity(source)
    all_items = list(db.scalars(select(QuestionImportItem).where(QuestionImportItem.batch_id == batch_id)))
    if linked_source_answer_item(source, all_items) is not None:
        raise ImportProblem("ANSWER_ALREADY_LINKED", "此来源答案已被其他候选题认领", 409)
    payload = dict(item.payload or {})
    source_text = (f"{source['subquestion_number']}：{source['text']}"
                   if source.get("subquestion_number") else source["text"])
    version = {"answer_id": "teacher-source-" + uuid.uuid4().hex,
               "answer_source": "source_extracted", "source_identity": identity,
               "text": source_text.strip(), "teacher_confirmed": False,
               "evidence": [{"file_id": source["source_file_id"], "page": source.get("page_start"),
                             "block_id": None, "quote": source["text"][:300]}]}
    item.revision += 1
    payload.update({"answers": [*(payload.get("answers") or []), version],
                    "selected_answer_id": version["answer_id"], "answer_match_status": "matched",
                    "teacher_decision": "pending", "commit_intent": None, "revision": item.revision})
    item.payload = payload
    event = _event(db, batch_id=batch_id, item_id=item.id, kind="answer_claim",
                   decision="claimed", revision=item.revision, signature=_signature(item, None),
                   issue_key=issue["key"], detail={"source_identity": identity,
                                                     "answer_id": version["answer_id"]})
    db.flush()
    return {"item_id": str(item.id), "revision": item.revision,
            "answer_id": version["answer_id"], "event_id": str(event.id)}


def confirm_answer_source(db: Session, batch_id: uuid.UUID, item_id: uuid.UUID,
                          request: S5AnswerSourceRequest) -> dict[str, Any]:
    """Confirm only the answer-to-question association, not the answer correctness."""
    item = _locked_batch_item(db, batch_id, item_id, request.expected_revision)
    payload = dict(item.payload or {})
    if payload.get("selected_answer_id") != request.selected_answer_id:
        raise ImportProblem("REVISION_CONFLICT", "所选答案版本已变化，请刷新", 409)
    answers = list(payload.get("answers") or [])
    if any(not isinstance(answer, dict) for answer in answers):
        raise ImportProblem("ANSWER_MISSING", "答案版本结构无效", 409)
    target = next((answer for answer in answers if isinstance(answer, dict)
                   and answer.get("answer_id") == request.selected_answer_id), None)
    if target is None or not isinstance(target.get("text"), str) or not target["text"].strip():
        raise ImportProblem("ANSWER_MISSING", "所选答案不存在或内容为空", 409)
    if payload.get("answer_match_status") in {"conflict", "unmatched"} and not (request.reason or "").strip():
        raise ImportProblem("ANSWER_SOURCE_UNCERTAIN", "冲突或未匹配答案需要填写人工关联理由")
    answers = [{**answer, "teacher_confirmed": True}
               if answer.get("answer_id") == request.selected_answer_id else answer
               for answer in answers]
    item.revision += 1
    payload.update({"answers": answers, "answer_match_status": "matched",
                    "teacher_decision": "pending", "commit_intent": None,
                    "revision": item.revision})
    item.payload = payload
    event = _event(db, batch_id=batch_id, item_id=item_id, kind="answer_source",
                   decision="confirmed", revision=item.revision, signature=_signature(item, None),
                   reason=(request.reason or "").strip() or None,
                   detail={"selected_answer_id": request.selected_answer_id})
    db.flush()
    return {"item_id": str(item_id), "revision": item.revision,
            "answer_id": request.selected_answer_id, "source_confirmed": True,
            "answer_content_confirmed": False, "event_id": str(event.id)}
