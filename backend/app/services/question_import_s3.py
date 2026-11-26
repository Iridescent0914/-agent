"""S3 reconciliation of answers extracted from combined or separate files."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import QuestionImportBatch, QuestionImportItem
from app.services.question_import import DOCX_CONVERSION_VERSION, ImportProblem, OWNER_SCOPE, _path_for, active_candidate
from app.services.question_import_s2 import COMBINED_PROMPT_VERSION


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


def source_answer_identity(answer: dict[str, Any]) -> str:
    fields = ("source_file_id", "page_start", "page_end", "original_number",
              "subquestion_number", "text")
    content = json.dumps({field: answer.get(field) for field in fields},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:24]


def _text_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).lower()
    normalized = normalized.replace("\\times", "×").replace("﹣", "-").replace("−", "-")
    return re.sub(r"[\s\$\\{}|*_，。,.：:；;（）()\[\]【】]+", "", normalized)


def _matching_excerpt(answer: dict[str, Any], targets: list[QuestionImportItem]) -> list[QuestionImportItem]:
    excerpt = _text_key(answer.get("question_excerpt"))
    if len(excerpt) < 4:
        return []
    return [item for item in targets if excerpt in _text_key(item.payload["content"]["question_text"])
            or _text_key(item.payload["content"]["question_text"]) in excerpt]


def _substantive_excerpt(value: object) -> bool:
    """A question number alone cannot corroborate or contradict a question stem."""
    excerpt = _text_key(value)
    if len(excerpt) < 4:
        return False
    return re.fullmatch(r"(?:第)?(?:\d+|[一二三四五六七八九十百零两]+)(?:小?题)?", excerpt) is None


def _source_pdf_path(batch: QuestionImportBatch, role: str) -> Path | None:
    """Use the PDF already read by S2/S3, including a cached DOCX conversion."""
    source = next((file for file in batch.files if file["role"] == role), None)
    if not source:
        return None
    path = _path_for(batch.id, uuid.UUID(source["file_id"]))
    if source["media_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        path = path.with_name(path.name + f".{DOCX_CONVERSION_VERSION}.converted.pdf")
    elif source["media_type"] != "application/pdf":
        return None
    return path if path.is_file() else None


def _source_sections(batch: QuestionImportBatch, role: str) -> dict[int, str]:
    """Read explicit exercise identifiers from PDF page headers, carrying them over continuation pages."""
    path = _source_pdf_path(batch, role)
    if path is None:
        return {}
    sections: dict[int, str] = {}
    current: str | None = None
    try:
        for page_number, page in enumerate(PdfReader(str(path)).pages, start=1):
            header = unicodedata.normalize("NFKC", (page.extract_text() or "")[:500])
            keys = {match.lower() for match in re.findall(
                r"(?:^|\n)\s*习题\s*([A-Za-z]\d+(?:[-.]\d+)+)", header,
            )}
            keys.update(
                f"l{series}-extension-lecture-{lecture}"
                for series, lecture in re.findall(
                    r"(?:^|\n)\s*L\s*(\d+)\s*拓展篇\s*第\s*([一二三四五六七八九十百零两\d]+)\s*讲",
                    header, flags=re.IGNORECASE,
                )
            )
            if len(keys) > 1:
                current = None
            elif keys:
                current = next(iter(keys))
            if current:
                sections[page_number] = current
    except (OSError, PdfReadError, ValueError):
        return {}
    return sections


def _replaceable_source_answer(item: QuestionImportItem) -> bool:
    payload = item.payload
    answers = payload.get("answers") or []
    auto_revision = payload.get("answer_match_auto_revision") == item.revision
    selected = payload.get("selected_answer_id")
    auto_selected = (auto_revision and len(answers) == 1
                     and selected == answers[0].get("answer_id")
                     and answers[0].get("answer_source") == "source_extracted")
    return ((item.revision <= 2 or auto_revision)
            and payload.get("teacher_decision") == "pending"
            and (not selected or auto_selected)
            and all(answer.get("answer_source") == "source_extracted"
                    and not answer.get("teacher_confirmed") for answer in answers))


def _select_sole_linked_source_answer(item: QuestionImportItem) -> bool:
    """Backfill a uniquely linked source answer without overriding teacher edits."""
    payload = item.payload or {}
    answers = payload.get("answers") or []
    if (payload.get("selected_answer_id") or payload.get("teacher_decision") != "pending"
            or payload.get("answer_match_status") != "matched" or len(answers) != 1
            or answers[0].get("answer_source") != "source_extracted"
            or not str(answers[0].get("text") or "").strip()):
        return False
    updated = dict(payload)
    item.revision += 1
    updated["selected_answer_id"] = answers[0]["answer_id"]
    updated["revision"] = item.revision
    updated["answer_match_auto_revision"] = item.revision
    item.payload = updated
    return True


def _already_linked_source_answer(item: QuestionImportItem, version: dict[str, Any]) -> bool:
    """Recognize the same source answer without changing a later teacher edit."""
    return any(
        answer.get("answer_source") == "source_extracted"
        and all(answer.get(field) == version[field] for field in ("answer_id", "text", "evidence"))
        for answer in (item.payload.get("answers") or [])
    )


def linked_source_answer_item(answer: dict[str, Any], items: list[QuestionImportItem]) -> QuestionImportItem | None:
    """Find an existing exact source link, including one made by original-page review."""
    source_text = (f"{answer['subquestion_number']}：{answer['text']}"
                   if answer.get("subquestion_number") else answer.get("text"))
    for item in items:
        for version in item.payload.get("answers") or []:
            if version.get("answer_source") != "source_extracted" or version.get("text") != source_text:
                continue
            if (version.get("answer_id") == _answer_id([answer])
                    or version.get("source_identity") == source_answer_identity(answer)):
                return item
    return None


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


def reconcile_source_answers(db: Session, batch_id: uuid.UUID, *, force: bool = False) -> dict[str, Any]:
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id,
        QuestionImportBatch.owner_scope == OWNER_SCOPE,
    ).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if batch.status != "review_pending" or batch.stage != "s2_completed":
        raise ImportProblem("COMMIT_CONFLICT", "请先完成 S2 切题", 409)
    if batch.source_mode not in {"combined", "separate"}:
        raise ImportProblem("S3_SOURCE_MODE", "此批次没有待关联的资料答案", 409)
    s2 = (batch.checkpoint or {}).get("s2") or {}
    existing_s3 = (batch.checkpoint or {}).get("s3") or {}
    if batch.source_mode == "combined":
        if s2.get("prompt_version") != COMBINED_PROMPT_VERSION:
            raise ImportProblem("S3_SOURCE_ANSWERS_MISSING", "本批次未提取资料答案，请重新上传并识别", 409)
        source_units = s2
        source_version = s2.get("prompt_version")
    else:
        source_units = existing_s3.get("separate_answers") or {}
        if source_units.get("status") != "completed":
            raise ImportProblem("S3_SOURCE_ANSWERS_MISSING", "请先完成答案文件识别", 409)
        source_version = source_units.get("version")
    previous_result = existing_s3.get("source_answer_reconciliation") or {}
    if not force and previous_result.get("schema_version") == "s3-source-answer-reconciliation-v3":
        return previous_result
    source = next(file for file in batch.files if file["role"] == (
        "questions" if batch.source_mode == "combined" else "answers"))
    if (source["media_type"] != "application/pdf"
            and source_units.get("conversion_version") != DOCX_CONVERSION_VERSION):
        raise ImportProblem("S3_CONFIG_CHANGED",
                            "DOCX 转换器已更新，请重新上传并完整重跑 S2，避免新旧 PDF 页码混用", 409)

    items = [item for item in db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch.id,
    ).order_by(QuestionImportItem.ordinal).with_for_update()) if active_candidate(item)]
    # S3 does not delete candidates based on page labels.
    by_number: dict[str, list[QuestionImportItem]] = defaultdict(list)
    for item in items:
        key = _number_key((item.payload.get("source") or {}).get("original_number"))
        if key:
            by_number[key].append(item)
    question_sections = _source_sections(batch, "questions") if batch.source_mode == "separate" else {}
    answer_sections = _source_sections(batch, "answers") if question_sections else {}
    model_assignments = (existing_s3.get("answer_model_matching") or {}).get("assignments") or {}
    item_by_id = {str(item.id): item for item in items}

    matched: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    already_linked_item_ids: set[uuid.UUID] = set()
    linked_count = 0
    unassigned: list[dict[str, Any]] = []
    answer_count = 0
    duplicate_answer_count = 0
    seen_source_answers: set[tuple[Any, ...]] = set()
    for unit in source_units.get("units") or []:
        for answer in unit.get("source_answers") or []:
            answer_count += 1
            # Only collapse repeated transcriptions at the same source position.
            # Identical short answers in different exercises are independent.
            source_signature = (
                answer.get("source_file_id"), answer.get("page_start"), answer.get("page_end"),
                _number_key(answer.get("original_number")),
                _number_key(answer.get("subquestion_number")),
                _text_key(answer.get("question_excerpt")), _text_key(answer.get("text")),
                answer.get("answer_form"),
            )
            if source_signature in seen_source_answers:
                duplicate_answer_count += 1
                continue
            seen_source_answers.add(source_signature)
            linked_item = linked_source_answer_item(answer, items)
            if linked_item is not None:
                _select_sole_linked_source_answer(linked_item)
                if model_assignments and _replaceable_source_answer(linked_item):
                    matched[linked_item.id].append(answer)
                else:
                    already_linked_item_ids.add(linked_item.id)
                    linked_count += 1
                continue
            assignment = item_by_id.get(model_assignments.get(source_answer_identity(answer)))
            if assignment is not None:
                matched[assignment.id].append(answer)
                continue
            key = _number_key(answer.get("original_number"))
            targets = by_number.get(key, []) if key else []
            answer_section = answer_sections.get(answer.get("page_start"))
            section_unique = False
            if answer_section and targets:
                section_matches = [item for item in targets if question_sections.get(
                    (item.payload.get("source") or {}).get("page_start")) == answer_section]
                if section_matches:
                    targets = section_matches
                    section_unique = len(section_matches) == 1
                elif all(question_sections.get((item.payload.get("source") or {}).get("page_start"))
                         for item in targets):
                    unassigned.append({**answer, "reason": "section_conflict",
                                       "candidate_item_ids": [str(item.id) for item in targets]})
                    continue
            excerpt = answer.get("question_excerpt")
            if not section_unique and _substantive_excerpt(excerpt):
                excerpt_matches = _matching_excerpt(answer, targets if key else items)
                if len(targets) != 1:
                    targets = excerpt_matches
                elif targets[0] not in excerpt_matches:
                    unassigned.append({**answer, "reason": "question_excerpt_conflict",
                                       "candidate_item_ids": [str(targets[0].id)]})
                    continue
            reason = None
            if not key:
                if len(targets) != 1:
                    reason = "missing_question_number"
            elif not targets:
                reason = "question_not_found"
            elif len(targets) != 1:
                reason = "repeated_question_number"
            if reason:
                unassigned.append({**answer, "reason": reason,
                                   "candidate_item_ids": [str(item.id) for item in targets]})
            else:
                matched[targets[0].id].append(answer)

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
        if any(source_answer_identity(answer) in model_assignments for answer in ordered):
            version["match_method"] = "model_assisted"
        if len(ordered) == 1:
            version["source_identity"] = source_answer_identity(ordered[0])
        if _already_linked_source_answer(item, version):
            _select_sole_linked_source_answer(item)
            linked_count += len(ordered)
            continue
        if not _replaceable_source_answer(item):
            unassigned.extend({**answer, "reason": "candidate_already_edited",
                               "candidate_item_ids": [str(item.id)]} for answer in ordered)
            del matched[item.id]
            continue
        payload = dict(item.payload)
        payload["answers"] = [version]
        payload["selected_answer_id"] = version["answer_id"]
        payload["answer_match_status"] = (
            "uncertain" if any(answer.get("uncertain_fragments") for answer in ordered) else "matched"
        )
        payload["warnings"] = [warning for warning in (payload.get("warnings") or [])
                               if warning != "资料答案已关联，须对照原页由教师确认"]
        payload["warnings"].append("资料答案已关联，须对照原页由教师确认")
        if (item.payload.get("answers") != [version]
                or item.payload.get("answer_match_status") != payload["answer_match_status"]
                or item.payload.get("selected_answer_id") != version["answer_id"]):
            item.revision += 1
            payload["revision"] = item.revision
            payload["answer_match_auto_revision"] = item.revision
            item.payload = payload
        linked_count += len(ordered)

    remaining_unassigned = []
    for answer in unassigned:
        linked_item = linked_source_answer_item(answer, items)
        if linked_item is None:
            remaining_unassigned.append(answer)
        else:
            linked_count += 1
            already_linked_item_ids.add(linked_item.id)
    result = {"schema_version": "s3-source-answer-reconciliation-v3",
              "source_answer_count": answer_count, "linked_answer_count": linked_count,
              "linked_question_count": len(set(matched) | already_linked_item_ids),
              "deduplicated_answer_count": duplicate_answer_count,
              "unassigned_answers": remaining_unassigned,
              "source_prompt_version": source_version}
    result = _classify_overlap_duplicates(source_units, result, items)
    checkpoint = dict(batch.checkpoint or {})
    checkpoint["s3"] = {**existing_s3, "source_answer_reconciliation": result}
    batch.checkpoint = checkpoint
    return result
