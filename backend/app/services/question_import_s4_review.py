"""Persist S4 catalog checks without treating model or index rankings as decisions."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import (
    Question, QuestionImportBatch, QuestionImportCatalogCheck,
    QuestionImportCatalogMatch, QuestionImportItem,
)
from app.services.question_fingerprint_v2 import catalog_content_key, fingerprint, normalized_text
from app.services.question_import import OWNER_SCOPE, ImportProblem, active_candidate, get_batch
from app.services.question_import_s4_exact import _candidate_key
from app.services.question_import_s4_near import catalog_near_matches


def _candidate_signature(db: Session, batch_id: uuid.UUID, item: QuestionImportItem) -> str:
    key = _candidate_key(db, batch_id, item)
    if key is not None:
        return fingerprint(key)  # type: ignore[return-value]
    payload = item.payload or {}
    content = payload.get("content") or {}
    extraction = payload.get("extraction") or {}
    raw = {"content": content, "figure_status": extraction.get("figure_status"),
           "diagram_clues": extraction.get("diagram_clues")}
    return hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()


def _catalog_signature(question: Question) -> str | None:
    return fingerprint(catalog_content_key(question.question_text, question.options,
                                           question.assets, has_image=question.has_image))


def _answer_text_differs(item: QuestionImportItem, question: Question) -> bool | None:
    payload = item.payload or {}
    selected = next((answer.get("text") for answer in payload.get("answers") or []
                     if answer.get("answer_id") == payload.get("selected_answer_id")), None)
    reference = question.reference_answer_raw
    if not isinstance(selected, str) or not selected.strip() or not isinstance(reference, str) or not reference.strip():
        return None
    return normalized_text(selected) != normalized_text(reference)


def _latest_checks(db: Session, batch_id: uuid.UUID) -> dict[uuid.UUID, QuestionImportCatalogCheck]:
    latest = select(
        QuestionImportCatalogCheck.item_id,
        func.max(QuestionImportCatalogCheck.check_no).label("last_no"),
    ).where(
        QuestionImportCatalogCheck.batch_id == batch_id
    ).group_by(QuestionImportCatalogCheck.item_id).subquery()
    checks = db.scalars(select(QuestionImportCatalogCheck).join(latest, and_(
        QuestionImportCatalogCheck.item_id == latest.c.item_id,
        QuestionImportCatalogCheck.check_no == latest.c.last_no,
    )))
    return {check.item_id: check for check in checks}


def run_catalog_review(db: Session, batch_id: uuid.UUID) -> dict[str, Any]:
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id,
        QuestionImportBatch.owner_scope == OWNER_SCOPE,
    ).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if batch.status != "review_pending" or batch.stage != "s2_completed":
        raise ImportProblem("COMMIT_CONFLICT", "当前批次不可检查题库重复", 409)
    result = catalog_near_matches(db, batch_id)
    items = {item.id: item for item in db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch_id
    ).with_for_update()) if active_candidate(item) and not item.is_fake}
    prior = _latest_checks(db, batch_id)
    prior_matches: dict[uuid.UUID, dict[uuid.UUID, QuestionImportCatalogMatch]] = {}
    if prior:
        for match in db.scalars(select(QuestionImportCatalogMatch).where(
            QuestionImportCatalogMatch.check_id.in_([check.id for check in prior.values()])
        )):
            prior_matches.setdefault(match.check_id, {})[match.question_id] = match
    exact_by_item = {row["item_id"]: row for row in result["exact_results"]}
    near_by_item = {row["item_id"]: row for row in result["results"]}
    question_ids = {uuid.UUID(match["question_id"])
                    for row in result["exact_results"] for match in row["matches"]}
    question_ids.update(uuid.UUID(match["question_id"])
                        for row in result["results"] for match in row["near_matches"])
    questions = {question.id: question for question in db.scalars(select(Question).where(
        Question.id.in_(question_ids)
    ))} if question_ids else {}
    for item in items.values():
        item_id = str(item.id)
        signature = _candidate_signature(db, batch_id, item)
        old = prior.get(item.id)
        check = QuestionImportCatalogCheck(
            batch_id=batch_id, item_id=item.id, check_no=(old.check_no + 1 if old else 1),
            candidate_revision=item.revision, candidate_signature=signature,
            exact_version=result["exact_version"], near_version=result["version"],
        )
        db.add(check)
        db.flush()
        exact_rows = [("exact", row) for row in exact_by_item[item_id]["matches"]]
        near_rows = [("nearby", row) for row in near_by_item[item_id]["near_matches"]]
        preserved_skip = False
        for kind, row in [*exact_rows, *near_rows]:
            question_id = uuid.UUID(row["question_id"])
            question = questions[question_id]
            catalog_signature = _catalog_signature(question)
            old_match = (prior_matches.get(old.id, {}).get(question_id) if old else None)
            preserve = (old is not None and old.candidate_signature == signature
                        and old_match is not None and old_match.kind == kind
                        and catalog_signature is not None
                        and old_match.catalog_signature == catalog_signature)
            decision = old_match.decision if preserve else "pending"
            preserved_skip |= decision == "skip_existing"
            evidence = {"differences": row.get("differences") or [],
                        "image_relation": row.get("image_relation"),
                        "answer_text_differs": row.get("answer_text_differs")}
            db.add(QuestionImportCatalogMatch(
                check_id=check.id, question_id=question_id,
                original_qid=question.original_qid,
                catalog_review_revision=question.review_revision,
                catalog_signature=catalog_signature, kind=kind,
                similarity=1.0 if kind == "exact" else row["trigram_similarity"],
                evidence=evidence, decision=decision,
                reason=old_match.reason if preserve else None,
                decided_at=old_match.decided_at if preserve else None,
                decision_scope=old_match.decision_scope if preserve else None,
            ))
        if (item.payload or {}).get("teacher_decision") == "skipped_duplicate" and not preserved_skip:
            item.revision += 1
            item.payload = {**item.payload, "teacher_decision": "pending", "revision": item.revision}
            check.candidate_revision = item.revision
    db.flush()
    return read_catalog_review(db, batch_id)


def read_catalog_review(db: Session, batch_id: uuid.UUID) -> dict[str, Any]:
    get_batch(db, batch_id)
    checks = _latest_checks(db, batch_id)
    if not checks:
        return {"batch_id": str(batch_id), "items": []}
    items = {item.id: item for item in db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.id.in_(checks)
    ))}
    matches_by_check: dict[uuid.UUID, list[QuestionImportCatalogMatch]] = {}
    all_matches = list(db.scalars(select(QuestionImportCatalogMatch).where(
        QuestionImportCatalogMatch.check_id.in_([check.id for check in checks.values()])
    ).order_by(QuestionImportCatalogMatch.kind, QuestionImportCatalogMatch.original_qid)))
    for match in all_matches:
        matches_by_check.setdefault(match.check_id, []).append(match)
    questions = {question.id: question for question in db.scalars(select(Question).where(
        Question.id.in_([match.question_id for match in all_matches if match.question_id is not None])
    ))} if all_matches else {}
    output = []
    for item_id, check in checks.items():
        item = items.get(item_id)
        if item is None or not active_candidate(item) or item.is_fake:
            continue
        candidate_stale = _candidate_signature(db, batch_id, item) != check.candidate_signature
        rows = []
        catalog_stale = False
        for match in matches_by_check.get(check.id, []):
            question = questions.get(match.question_id)
            stale = (question is None or question.archived_at is not None
                     or (match.catalog_signature is None
                         and question.review_revision != match.catalog_review_revision)
                     or (match.catalog_signature is not None
                         and _catalog_signature(question) != match.catalog_signature))
            catalog_stale |= stale
            evidence = dict(match.evidence or {})
            evidence.pop("image_visual_distance", None)
            if question is not None:
                evidence["answer_text_differs"] = _answer_text_differs(item, question)
                content = (item.payload or {}).get("content") or {}
                same_stem = (normalized_text(content.get("question_text") or "", strip_question_number=True)
                             == normalized_text(question.question_text, strip_question_number=True))
                if not stale and same_stem and evidence.get("image_relation") == "题图文件不同":
                    evidence["differences"] = [difference for difference in evidence.get("differences") or []
                                               if "题图文件不同" not in difference]
                    evidence["differences"].append("题干文字相同，但题图文件不同，请人工核对是否为同一道题")
            rows.append({
                "id": str(match.id), "question_id": str(match.question_id) if match.question_id else None,
                "original_qid": match.original_qid, "kind": match.kind,
                "similarity": match.similarity, "evidence": evidence,
                "decision": match.decision, "reason": match.reason,
                "decided_at": match.decided_at.isoformat() if match.decided_at else None,
                "catalog_review_revision": match.catalog_review_revision,
                "catalog_stale": stale,
                "catalog_unverifiable": match.catalog_signature is None,
                "question_text": question.question_text if question else None,
                "options": question.options if question else [],
                "reference_answer_raw": question.reference_answer_raw if question else None,
            })
        output.append({"item_id": str(item_id), "check_id": str(check.id),
                       "check_no": check.check_no, "candidate_revision": check.candidate_revision,
                       "current_revision": item.revision,
                       "candidate_stale": candidate_stale, "catalog_stale": catalog_stale,
                       "checked_at": check.checked_at.isoformat() if check.checked_at else None,
                       "matches": rows})
    return {"batch_id": str(batch_id), "items": output}


def decide_catalog_match(db: Session, batch_id: uuid.UUID, item_id: uuid.UUID,
                         match_id: uuid.UUID, *, expected_revision: int,
                         decision: str, reason: str | None) -> dict[str, Any]:
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id,
        QuestionImportBatch.owner_scope == OWNER_SCOPE,
    ).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if batch.status != "review_pending":
        raise ImportProblem("COMMIT_CONFLICT", "当前批次不可作查重决定", 409)
    item = db.scalar(select(QuestionImportItem).where(
        QuestionImportItem.id == item_id, QuestionImportItem.batch_id == batch_id
    ).with_for_update())
    if item is None or not active_candidate(item) or item.is_fake:
        raise ImportProblem("NOT_FOUND", "候选题不存在", 404)
    if item.revision != expected_revision:
        raise ImportProblem("REVISION_CONFLICT", "候选题已修改，请刷新", 409)
    check = _latest_checks(db, batch_id).get(item_id)
    if check is None or _candidate_signature(db, batch_id, item) != check.candidate_signature:
        raise ImportProblem("DUPLICATE_DECISION_REQUIRED", "查重结果已过期，请重新检查", 409)
    match = db.scalar(select(QuestionImportCatalogMatch).where(
        QuestionImportCatalogMatch.id == match_id,
        QuestionImportCatalogMatch.check_id == check.id,
    ).with_for_update())
    if match is None:
        raise ImportProblem("NOT_FOUND", "查重记录不存在或已被新结果替换", 404)
    question = db.get(Question, match.question_id)
    if (question is None or question.archived_at is not None
            or match.catalog_signature is None
            or _catalog_signature(question) != match.catalog_signature):
        raise ImportProblem("CATALOG_CHANGED", "正式题已变化，请重新检查", 409)
    if decision not in {"pending", "skip_existing", "keep_variant", "unrelated"}:
        raise ImportProblem("INVALID_CANDIDATE", "查重决定无效")
    if match.kind == "exact" and decision in {"keep_variant", "unrelated"}:
        raise ImportProblem("EXACT_DUPLICATE", "精确重复题只能跳过或保持待决定", 409)
    reason = reason.strip() if isinstance(reason, str) else None
    if decision == "keep_variant" and not reason:
        raise ImportProblem("DUPLICATE_DECISION_REQUIRED", "确认独立变式须填写理由")
    if decision == "skip_existing" and match.kind == "nearby" and not reason:
        raise ImportProblem("DUPLICATE_DECISION_REQUIRED", "跳过相近题须填写理由")
    if reason and len(reason) > 500:
        raise ImportProblem("INVALID_CANDIDATE", "理由不能超过 500 字")
    match.decision = decision
    match.reason = reason if decision != "pending" else None
    match.decided_at = datetime.now(timezone.utc) if decision != "pending" else None
    match.decision_scope = OWNER_SCOPE if decision != "pending" else None
    db.flush()
    any_skip = db.scalar(select(QuestionImportCatalogMatch.id).where(
        QuestionImportCatalogMatch.check_id == check.id,
        QuestionImportCatalogMatch.decision == "skip_existing",
    ).limit(1)) is not None
    current_decision = (item.payload or {}).get("teacher_decision", "pending")
    if any_skip and current_decision != "skipped_duplicate":
        item.revision += 1
        item.payload = {**item.payload, "teacher_decision": "skipped_duplicate", "revision": item.revision}
    elif not any_skip and current_decision == "skipped_duplicate":
        item.revision += 1
        item.payload = {**item.payload, "teacher_decision": "pending", "revision": item.revision}
    db.flush()
    return read_catalog_review(db, batch_id)
