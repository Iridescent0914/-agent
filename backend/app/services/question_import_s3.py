"""S3 reconciliation of source answers from combined question and answer files."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import QuestionImportBatch, QuestionImportItem
from app.services.question_import import DOCX_CONVERSION_VERSION, ImportProblem, OWNER_SCOPE
from app.services.question_import_s2 import COMBINED_PROMPT_VERSIONS


def _number_key(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub(r"^\(([^()]+)\)$", r"\1", normalized)
    normalized = re.sub(r"[.．、:：)）]+$", "", normalized)
    return normalized or None


def _answer_id(answers: list[dict[str, Any]]) -> str:
    content = json.dumps(answers, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "source-" + hashlib.sha256(content.encode("utf-8")).hexdigest()[:24]


def _text_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).lower()
    normalized = normalized.replace("\\times", "×").replace("﹣", "-").replace("−", "-")
    return re.sub(r"[\s\$\\{}|*_，。,.：:；;（）()\[\]【】]+", "", normalized)


def _matching_excerpt(answer: dict[str, Any], targets: list[QuestionImportItem]) -> list[QuestionImportItem]:
    excerpt = _text_key(answer.get("question_excerpt"))
    if len(excerpt) < 16:
        return []
    return [item for item in targets if excerpt in _text_key(item.payload["content"]["question_text"])
            or _text_key(item.payload["content"]["question_text"]) in excerpt]


def _replaceable_source_answer(item: QuestionImportItem) -> bool:
    payload = item.payload
    answers = payload.get("answers") or []
    return (item.revision <= 2 and payload.get("teacher_decision") == "pending"
            and not payload.get("selected_answer_id")
            and all(answer.get("answer_source") == "source_extracted"
                    and not answer.get("teacher_confirmed") for answer in answers))


def _classify_overlap_duplicates(s2: dict[str, Any], result: dict[str, Any],
                                 items: list[QuestionImportItem]) -> dict[str, Any]:
    """Separate repeated overlap context from answers that really need assignment.

    Keep the S2 source records intact. A missing-number fragment is only classified
    when it is the first answer of a unit and exactly repeats the last numbered
    answer of the preceding unit on the immediately following source page.
    """
    linked_by_number: dict[str, list[QuestionImportItem]] = defaultdict(list)
    for item in items:
        key = _number_key((item.payload.get("source") or {}).get("original_number"))
        if key:
            linked_by_number[key].append(item)

    duplicate_keys: dict[tuple[str, int, str], dict[str, Any]] = {}
    units = s2.get("units") or []
    for previous_unit, current_unit in zip(units, units[1:]):
        previous_answers = previous_unit.get("source_answers") or []
        current_answers = current_unit.get("source_answers") or []
        if not previous_answers or not current_answers:
            continue
        previous, current = previous_answers[-1], current_answers[0]
        number = _number_key(previous.get("original_number"))
        if (not number or current.get("original_number")
                or previous.get("source_file_id") != current.get("source_file_id")
                or current.get("page_start") != previous.get("page_start", 0) + 1
                or not _text_key(previous.get("text"))
                or _text_key(previous.get("text")) != _text_key(current.get("text"))):
            continue
        targets = linked_by_number.get(number, [])
        if len(targets) != 1 or not any(
            answer.get("answer_source") == "source_extracted"
            and _text_key(answer.get("text")) == _text_key(previous.get("text"))
            for answer in (targets[0].payload.get("answers") or [])
        ):
            continue
        signature = (current["source_file_id"], current["page_start"],
                     _text_key(current["text"]))
        duplicate_keys[signature] = {**current,
                                     "matched_original_number": previous["original_number"],
                                     "matched_page": previous["page_start"],
                                     "reason": "overlap_duplicate"}

    remaining = []
    duplicates = []
    for answer in result.get("unassigned_answers") or []:
        signature = (answer.get("source_file_id"), answer.get("page_start"),
                     _text_key(answer.get("text")))
        duplicate = duplicate_keys.get(signature)
        if answer.get("reason") == "missing_question_number" and duplicate:
            duplicates.append(duplicate)
        else:
            remaining.append(answer)
    return {**result, "schema_version": "s3-source-answer-reconciliation-v3",
            "unassigned_answers": remaining, "overlap_duplicate_answers": duplicates}


def reconcile_source_answers(db: Session, batch_id: uuid.UUID) -> dict[str, Any]:
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id,
        QuestionImportBatch.owner_scope == OWNER_SCOPE,
    ).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if batch.status != "review_pending" or batch.stage != "s2_completed":
        raise ImportProblem("COMMIT_CONFLICT", "请先完成 S2 切题", 409)
    if batch.source_mode != "combined":
        raise ImportProblem("S3_SOURCE_MODE", "此入口仅处理题答同文件批次", 409)
    s2 = (batch.checkpoint or {}).get("s2") or {}
    if s2.get("prompt_version") not in COMBINED_PROMPT_VERSIONS:
        raise ImportProblem("S3_SOURCE_ANSWERS_MISSING", "旧批次未在一次提取中读取答案，需单独处理答案页", 409)
    existing_s3 = (batch.checkpoint or {}).get("s3") or {}
    previous_result = existing_s3.get("source_answer_reconciliation") or {}
    if previous_result.get("schema_version") == "s3-source-answer-reconciliation-v3":
        return previous_result
    source = next(file for file in batch.files if file["role"] == "questions")
    if (source["media_type"] != "application/pdf"
            and s2.get("conversion_version") != DOCX_CONVERSION_VERSION
            and previous_result.get("schema_version") != "s3-source-answer-reconciliation-v2"):
        raise ImportProblem("S3_CONFIG_CHANGED",
                            "DOCX 转换器已更新，请重新上传并完整重跑 S2，避免新旧 PDF 页码混用", 409)

    items = list(db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch.id,
    ).order_by(QuestionImportItem.ordinal).with_for_update()))
    if previous_result.get("schema_version") == "s3-source-answer-reconciliation-v2":
        result = _classify_overlap_duplicates(s2, previous_result, items)
        checkpoint = dict(batch.checkpoint or {})
        checkpoint["s3"] = {**existing_s3, "source_answer_reconciliation": result}
        batch.checkpoint = checkpoint
        return result
    # Candidate classification belongs to the one-pass S2 model. S3 must not
    # delete a candidate based on a page label, including legacy batches.
    by_number: dict[str, list[QuestionImportItem]] = defaultdict(list)
    for item in items:
        key = _number_key((item.payload.get("source") or {}).get("original_number"))
        if key:
            by_number[key].append(item)

    matched: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    unassigned: list[dict[str, Any]] = []
    answer_count = 0
    for unit in s2.get("units") or []:
        for answer in unit.get("source_answers") or []:
            answer_count += 1
            key = _number_key(answer.get("original_number"))
            targets = by_number.get(key, []) if key else []
            if len(targets) != 1 and answer.get("question_excerpt"):
                targets = _matching_excerpt(answer, targets if key else items)
            reason = None
            if not key:
                if len(targets) != 1:
                    reason = "missing_question_number"
            elif not targets:
                reason = "question_not_found"
            elif len(targets) != 1:
                reason = "repeated_question_number"
            if reason is None and not _replaceable_source_answer(targets[0]):
                reason = "candidate_already_edited"
            if reason:
                unassigned.append({**answer, "reason": reason})
            else:
                matched[targets[0].id].append(answer)

    duplicate_answer_count = 0
    # Identical transcriptions of one numbered answer are one answer; conflicting
    # values for the same position remain unassigned for teacher review.
    for item_id, answers in list(matched.items()):
        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for answer in answers:
            position = _number_key(answer.get("subquestion_number")) or "whole_question"
            signature = (position, _text_key(answer.get("text")))
            if signature in unique:
                duplicate_answer_count += 1
            else:
                unique[signature] = answer
        answers = list(unique.values())
        positions = [_number_key(answer.get("subquestion_number")) or "whole_question"
                     for answer in answers]
        if len(positions) != len(set(positions)):
            unassigned.extend({**answer, "reason": "repeated_answer_position"} for answer in answers)
            del matched[item_id]
        else:
            matched[item_id] = answers

    linked_count = 0
    for item in items:
        answers = matched.get(item.id)
        if not answers:
            continue
        ordered = sorted(answers, key=lambda value: (value["page_start"],
                                                     _number_key(value.get("subquestion_number")) or ""))
        text_parts = [
            f"{answer['subquestion_number']}：{answer['text']}"
            if answer.get("subquestion_number") else answer["text"]
            for answer in ordered
        ]
        evidence = [{"file_id": answer["source_file_id"], "page": answer["page_start"],
                     "block_id": None, "quote": answer["text"][:300]}
                    for answer in ordered]
        version = {"answer_id": _answer_id(ordered), "answer_source": "source_extracted",
                   "text": "\n".join(text_parts), "evidence": evidence,
                   "teacher_confirmed": False}
        payload = dict(item.payload)
        payload["answers"] = [version]
        payload["answer_match_status"] = (
            "uncertain" if any(answer.get("uncertain_fragments") for answer in ordered) else "matched"
        )
        payload["warnings"] = [warning for warning in (payload.get("warnings") or [])
                               if warning != "资料答案已关联，须对照原页由教师确认"]
        payload["warnings"].append("资料答案已关联，须对照原页由教师确认")
        item.revision += 1
        payload["revision"] = item.revision
        item.payload = payload
        linked_count += len(ordered)

    result = {"schema_version": "s3-source-answer-reconciliation-v2",
              "source_answer_count": answer_count, "linked_answer_count": linked_count,
              "linked_question_count": len(matched),
              "removed_nonquestion_candidate_count": 0,
              "deduplicated_answer_count": duplicate_answer_count,
              "unassigned_answers": unassigned,
              "source_prompt_version": s2.get("prompt_version")}
    result = _classify_overlap_duplicates(s2, result, items)
    checkpoint = dict(batch.checkpoint or {})
    checkpoint["s3"] = {**existing_s3, "source_answer_reconciliation": result}
    batch.checkpoint = checkpoint
    return result
