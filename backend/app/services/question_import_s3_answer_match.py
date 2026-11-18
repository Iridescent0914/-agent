"""Budgeted, resumable text-only model assistance for source-answer assignment."""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import QuestionImportBatch, QuestionImportItem
from app.services.question_import import OWNER_SCOPE, ImportProblem, _path_for, active_candidate, require_s2_operator_token
from app.services.question_import_s2 import FLASH_MODEL
from app.services.question_import_s3 import (
    _number_key, _replaceable_source_answer, _source_pdf_path, _source_sections, _text_key,
    linked_source_answer_item, reconcile_source_answers, source_answer_identity,
)

VERSION = "s3-answer-model-match-v1"
MAX_OUTPUT_TOKENS = 4096
MAX_GROUP_ANSWERS = 10
MAX_GROUP_CANDIDATES = 80
MAX_GROUP_CHARS = 35000
logger = logging.getLogger(__name__)


class MatchDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer_id: str
    candidate_item_id: str | None
    evidence_quote: str = Field(max_length=300)
    reason: str = Field(min_length=1, max_length=500)


class MatchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decisions: list[MatchDecision]


def _price_snapshot() -> dict[str, float]:
    settings = get_settings()
    return {"input_million_cny": settings.question_import_s2_input_million_price_cny,
            "output_million_cny": settings.question_import_s2_output_million_price_cny}


def _text_cost(usage: dict[str, Any], prices: dict[str, float] | None = None) -> float | None:
    prices = prices or _price_snapshot()
    try:
        prompt, completion = int(usage["prompt_tokens"]), int(usage["completion_tokens"])
        if min(prompt, completion) < 0:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return round((prompt * prices["input_million_cny"]
                  + completion * prices["output_million_cny"]) / 1_000_000, 6)


def _validate_response(body: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        choice = body["choices"][0]
        if choice["finish_reason"] != "stop":
            raise ValueError("truncated")
        output = MatchOutput.model_validate_json(choice["message"]["content"])
    except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
        raise ImportProblem("S3_MATCH_OUTPUT_INVALID", "答案配对模型输出无效；本次调用可能已产生费用") from exc
    answers = {row["answer_id"] for row in context["answers"]}
    candidates = {row["item_id"] for row in context["questions"]}
    seen: set[str] = set()
    for row in output.decisions:
        if (row.answer_id not in answers or row.answer_id in seen
                or row.candidate_item_id is not None and row.candidate_item_id not in candidates):
            raise ImportProblem("S3_MATCH_OUTPUT_INVALID", "答案配对模型引用了无效或重复的 ID")
        seen.add(row.answer_id)
    if seen != answers:
        raise ImportProblem("S3_MATCH_OUTPUT_INVALID", "答案配对模型未逐条返回判断")
    return [row.model_dump(mode="json") for row in output.decisions]


class QwenAnswerMatchProvider:
    def __init__(self, *, transport: httpx.BaseTransport | None = None):
        self.settings = get_settings()
        self.transport = transport

    def review(self, context: dict[str, Any], *, capture_response=None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        prompt = (
            "你负责将数学资料中已转录的来源答案与候选题对应。只根据给出的文字和位置判断，"
            "不要假定同题号一定相同；题号可能在不同讲次重复。page 在不同文件中分别计数，"
            "不能直接按页码相等配对。page_heading 是程序从页首摘出的短文字，可能不是标题。"
            "section 是从页面识别的讲次线索；同页混排时可能不适用于每条题目或答案，不能仅凭它配对。"
            "必须为每个 answer_id 返回恰好一项判断。candidate_item_id 只能从 questions 选择，"
            "证据不足时返回 null。尤其短答案、缺题号、缺题干摘录时，不可仅凭猜测或题目顺序强配。"
            "若当前已有 current_item_id，请核查该关联；不确定时返回 null。"
            "evidence_quote 应逐字摘录所选题目题干、答案所附题干摘录或页首标题中的关键文字；"
            "无法提供原文依据时返回 null。reason 简述判断。不要改写答案或创造候选 ID。\n"
            + json.dumps(context, ensure_ascii=False)
        )
        payload = {"model": self.settings.question_import_s2_model,
                   "messages": [{"role": "user", "content": prompt}],
                   "response_format": {"type": "json_schema", "json_schema": {
                       "name": "source_answer_matching", "strict": True,
                       "schema": MatchOutput.model_json_schema()}},
                   "include_tool_usage": True,
                   "max_tokens": min(MAX_OUTPUT_TOKENS, self.settings.question_import_s2_max_output_tokens)}
        started = time.monotonic()
        try:
            with httpx.Client(timeout=self.settings.question_import_s2_timeout_seconds,
                              transport=self.transport) as client:
                response = client.post(self.settings.question_import_s2_base_url.rstrip("/") + "/chat/completions",
                                       headers={"Authorization": "Bearer " + self.settings.question_import_s2_api_key},
                                       json=payload)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ImportProblem("MODEL_REQUEST_FAILED", "答案配对模型请求失败；请核对账单") from exc
        if capture_response is not None:
            capture_response(body)
        decisions = _validate_response(body, context)
        usage = body.get("usage") or {}
        return decisions, {"usage": usage, "estimated_cny": _text_cost(usage),
                           "response_id": body.get("id"), "model": self.settings.question_import_s2_model,
                           "price_snapshot": _price_snapshot(),
                           "elapsed_seconds": round(time.monotonic() - started, 3)}


def _answers(batch: QuestionImportBatch) -> list[dict[str, Any]]:
    checkpoint = batch.checkpoint or {}
    source = (checkpoint.get("s2") or {}) if batch.source_mode == "combined" else (
        (checkpoint.get("s3") or {}).get("separate_answers") or {})
    distinct: dict[str, dict[str, Any]] = {}
    for unit in source.get("units") or []:
        for answer in unit.get("source_answers") or []:
            distinct.setdefault(source_answer_identity(answer), answer)
    return list(distinct.values())


def _page_headings(batch: QuestionImportBatch, role: str) -> dict[int, str]:
    """Read only short page-leading text locally; no pages are sent to the model."""
    path = _source_pdf_path(batch, role)
    if path is None:
        return {}
    try:
        return {page_number: next((line.strip()[:120] for line in (page.extract_text() or "").splitlines()
                                   if line.strip()), "")
                for page_number, page in enumerate(PdfReader(str(path)).pages, start=1)}
    except (OSError, PdfReadError, ValueError):
        return {}


def _has_unique_rule_anchor(answer: dict[str, Any], linked: QuestionImportItem,
                            items: list[QuestionImportItem], same_number: list[QuestionImportItem],
                            question_sections: dict[int, str],
                            answer_sections: dict[int, str]) -> bool:
    """A repeated printed number is safe when an independent clue uniquely agrees."""
    excerpt = _text_key(answer.get("question_excerpt"))
    excerpt_matches = [item for item in items if len(excerpt) >= 8
                       and excerpt in _text_key((item.payload.get("content") or {}).get("question_text"))]
    if excerpt_matches and (len(excerpt_matches) != 1 or excerpt_matches[0].id != linked.id):
        return False
    section = answer_sections.get(answer.get("page_start"))
    linked_section = question_sections.get((linked.payload.get("source") or {}).get("page_start"))
    if section and linked_section and section != linked_section:
        return False
    section_matches = [item for item in same_number if section and question_sections.get(
        (item.payload.get("source") or {}).get("page_start")) == section]
    if section and any(question_sections.get((item.payload.get("source") or {}).get("page_start"))
                       for item in same_number):
        if section_matches and (len(section_matches) != 1 or section_matches[0].id != linked.id):
            return False
        if not section_matches and all(question_sections.get(
                (item.payload.get("source") or {}).get("page_start")) for item in same_number):
            return False
    return bool((len(excerpt_matches) == 1 and excerpt_matches[0].id == linked.id)
                or (len(section_matches) == 1 and section_matches[0].id == linked.id))


def _context_fits(answers: list[dict[str, Any]], questions: list[dict[str, Any]]) -> bool:
    return (bool(questions) and len(answers) <= MAX_GROUP_ANSWERS
            and len(questions) <= MAX_GROUP_CANDIDATES
            and len(json.dumps({"answers": answers, "questions": questions}, ensure_ascii=False)) <= MAX_GROUP_CHARS)


def _question_shards(answer: dict[str, Any], questions: list[dict[str, Any]]) -> list[list[dict[str, Any]]] | None:
    """Repeat the current association as an anchor while covering every other candidate."""
    current = answer.get("current_item_id")
    anchors = [row for row in questions if row["item_id"] == current]
    remaining = [row for row in questions if row["item_id"] != current]
    if not remaining:
        return [anchors] if _context_fits([answer], anchors) else None
    shards: list[list[dict[str, Any]]] = []
    shard = list(anchors)
    for question in remaining:
        proposed = [*shard, question]
        if not _context_fits([answer], proposed):
            if len(shard) == len(anchors) or not _context_fits([answer], [*anchors, question]):
                return None
            shards.append(shard)
            shard = [*anchors, question]
        else:
            shard = proposed
    shards.append(shard)
    return shards


def _groups(batch: QuestionImportBatch, items: list[QuestionImportItem]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    reconciliation = ((batch.checkpoint or {}).get("s3") or {}).get("source_answer_reconciliation") or {}
    unresolved = {source_answer_identity(row): row.get("reason")
                  for row in reconciliation.get("unassigned_answers") or []}
    by_number: dict[str, list[QuestionImportItem]] = defaultdict(list)
    for item in items:
        number = _number_key((item.payload.get("source") or {}).get("original_number"))
        if number:
            by_number[number].append(item)
    question_sections = _source_sections(batch, "questions")
    answer_sections = _source_sections(batch, "answers") if batch.source_mode == "separate" else question_sections
    question_headings = _page_headings(batch, "questions")
    answer_headings = _page_headings(batch, "answers") if batch.source_mode == "separate" else question_headings
    selected: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for answer in _answers(batch):
        answer_id = source_answer_identity(answer)
        number = _number_key(answer.get("original_number"))
        linked = linked_source_answer_item(answer, items)
        if linked is not None and not _replaceable_source_answer(linked):
            continue
        high_risk = bool(linked and number and len(by_number[number]) > 1
                         and not _has_unique_rule_anchor(answer, linked, items, by_number[number],
                                                         question_sections if batch.source_mode == "separate" else {},
                                                         answer_sections if batch.source_mode == "separate" else {}))
        if answer_id not in unresolved and not high_risk:
            continue
        if unresolved.get(answer_id) == "candidate_already_edited":
            continue
        selected[number or f"missing:{answer_id}"].append({
            "answer_id": answer_id, "number": answer.get("original_number"),
            "subquestion_number": answer.get("subquestion_number"),
            "text": str(answer.get("text") or "")[:500],
            "question_excerpt": str(answer.get("question_excerpt") or "")[:350],
            "page": answer.get("page_start"),
            "section": answer_sections.get(answer.get("page_start")),
            "page_heading": answer_headings.get(answer.get("page_start"), ""),
            "current_item_id": str(linked.id) if linked else None,
        })
    groups: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for number, answers in selected.items():
        pool = by_number.get(number) if not number.startswith("missing:") else None
        if not pool:
            pool = items
        # Excerpts can rescue a wrong or missing printed number without excluding the number pool.
        for answer in answers:
            excerpt = _text_key(answer.get("question_excerpt"))
            if len(excerpt) >= 8:
                present = {str(item.id) for item in pool}
                pool = [*pool, *(item for item in items if str(item.id) not in present
                    and excerpt in _text_key((item.payload.get("content") or {}).get("question_text")))]
            current = answer.get("current_item_id")
            if current and all(str(item.id) != current for item in pool):
                pool = [*pool, *(item for item in items if str(item.id) == current)]
        questions = [{"item_id": str(item.id), "revision": item.revision,
                      "number": (item.payload.get("source") or {}).get("original_number"),
                      "page": (item.payload.get("source") or {}).get("page_start"),
                      "section": question_sections.get((item.payload.get("source") or {}).get("page_start")),
                      "page_heading": question_headings.get((item.payload.get("source") or {}).get("page_start"), ""),
                      "stem": str((item.payload.get("content") or {}).get("question_text") or "")[:600],
                      "options": (item.payload.get("content") or {}).get("options", [])[:8]}
                     for item in pool]
        pending_answers: list[dict[str, Any]] = []

        def flush_answers() -> None:
            if pending_answers:
                groups.append({"index": len(groups), "status": "pending",
                               "context": {"answers": list(pending_answers), "questions": questions}})
                pending_answers.clear()

        for answer in answers:
            if _context_fits([answer], questions):
                if pending_answers and not _context_fits([*pending_answers, answer], questions):
                    flush_answers()
                pending_answers.append(answer)
                continue
            flush_answers()
            shards = _question_shards(answer, questions)
            if not shards:
                skipped.append({"answer_id": answer["answer_id"], "reason": "single_record_too_large",
                                "current_item_id": answer.get("current_item_id")})
                continue
            bundle_id = answer["answer_id"]
            for shard in shards:
                groups.append({"index": len(groups), "status": "pending", "bundle_id": bundle_id,
                               "context": {"answers": [answer], "questions": shard}})
        flush_answers()
    return groups, skipped


def _authorized(batch: QuestionImportBatch) -> bool:
    checkpoint = batch.checkpoint or {}
    if batch.source_mode == "combined":
        return bool((checkpoint.get("s2") or {}).get("auto_answer_matching"))
    if batch.source_mode == "separate":
        return bool((((checkpoint.get("s3") or {}).get("separate_answers") or {}).get("auto_answer_matching")))
    return False


def _ready(batch: QuestionImportBatch) -> bool:
    s3 = (batch.checkpoint or {}).get("s3") or {}
    return (batch.status == "review_pending" and batch.stage == "s2_completed"
            and batch.source_mode in {"combined", "separate"}
            and (s3.get("global_dedupe") or {}).get("status") == "completed"
            and bool(s3.get("source_answer_reconciliation"))
            and (batch.source_mode != "separate"
                 or (s3.get("separate_answers") or {}).get("status") == "completed"))


def _automatic_budget(batch: QuestionImportBatch) -> float:
    checkpoint = batch.checkpoint or {}
    s3 = checkpoint.get("s3") or {}
    if batch.source_mode == "combined":
        source = checkpoint.get("s2") or {}
        dedupe = s3.get("global_dedupe") or {}
        return max(0.0, float(source.get("budget_cny") or 0)
                   - float(source.get("estimated_cny") or 0)
                   - float(dedupe.get("estimated_cny") or dedupe.get("reserved_cny") or 0))
    source = s3.get("separate_answers") or {}
    return max(0.0, float(source.get("budget_cny") or 0) - float(source.get("estimated_cny") or 0))


def _new_state(batch: QuestionImportBatch, items: list[QuestionImportItem], budget_cny: float) -> dict[str, Any]:
    groups, skipped = _groups(batch, items)
    return {"version": VERSION, "status": "queued" if groups and budget_cny > 0 else
            "pending_budget" if groups else "completed",
            "budget_cny": budget_cny, "estimated_cny": 0.0, "calls": 0,
            "model": get_settings().question_import_s2_model, "price_snapshot": _price_snapshot(),
            "groups": groups, "skipped": skipped, "assignments": {},
            "suggestions": [], "review_warnings": _skipped_warnings(skipped)}


def _skipped_warnings(skipped: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"answer_id": row["answer_id"], "current_item_id": row["current_item_id"],
             "suggested_item_id": None, "kind": "uncertain", "evidence_quote": "",
             "reason": "单条答案或候选题文字超过模型输入上限，请人工核对"}
            for row in skipped if row.get("current_item_id")]


def start_answer_matching(db: Session, batch_id: uuid.UUID, *, budget_cny: float,
                          operator_token: str | None = None) -> QuestionImportBatch:
    require_s2_operator_token(operator_token)
    settings = get_settings()
    if (not settings.question_import_s2_enabled or not settings.question_import_s2_api_key
            or not settings.question_import_s2_base_url.startswith("https://")
            or settings.question_import_s2_model != FLASH_MODEL):
        raise ImportProblem("S3_MATCH_NOT_CONFIGURED", "答案配对模型尚未配置", 409)
    if not 0 < budget_cny <= settings.question_import_s2_max_budget_cny:
        raise ImportProblem("INVALID_S3_BUDGET", "答案配对预算超出允许范围")
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if not _ready(batch):
        raise ImportProblem("S3_MATCH_UNAVAILABLE", "请先完成去重、答案识别及程序关联", 409)
    checkpoint = dict(batch.checkpoint or {})
    s3 = dict(checkpoint.get("s3") or {})
    if (s3.get("labels") or {}).get("status") in {"queued", "processing"}:
        raise ImportProblem("COMMIT_CONFLICT", "标签建议任务运行期间不能重新规划答案配对", 409)
    state = dict(s3.get("answer_model_matching") or {})
    if state.get("status") in {"queued", "processing", "completed"}:
        raise ImportProblem("COMMIT_CONFLICT", "模型辅助配对已排队、进行中或完成", 409)
    if state.get("cost_unknown"):
        raise ImportProblem("S3_MATCH_COST_UNKNOWN", "上次调用费用未知，请先核对账单", 409)
    if budget_cny <= float(state.get("estimated_cny") or 0):
        raise ImportProblem("INVALID_S3_BUDGET", "新预算须高于已记录费用")
    if not state:
        items = [item for item in db.scalars(select(QuestionImportItem).where(
            QuestionImportItem.batch_id == batch.id).order_by(QuestionImportItem.ordinal)) if active_candidate(item)]
        state = _new_state(batch, items, budget_cny)
    else:
        state["budget_cny"] = budget_cny
        state["status"] = "queued"
        state.pop("error_code", None)
        state.pop("error_message", None)
        for group in state.get("groups") or []:
            if group["status"] == "failed":
                group["status"] = "pending"
                artifact = _artifact_path(batch, group["index"])
                if artifact.exists():
                    prior = artifact.with_name(artifact.name + ".prior.json")
                    suffix = 2
                    while prior.exists():
                        prior = artifact.with_name(artifact.name + f".prior{suffix}.json")
                        suffix += 1
                    os.replace(artifact, prior)
    s3["answer_model_matching"] = state
    checkpoint["s3"] = s3
    batch.checkpoint = checkpoint
    return batch


def _artifact_path(batch: QuestionImportBatch, index: int) -> Path:
    role = "questions" if batch.source_mode == "combined" else "answers"
    source = next(row for row in batch.files if row["role"] == role)
    path = _path_for(batch.id, uuid.UUID(source["file_id"]))
    return path.with_name(path.name + f".s3-match-group{index}.json")


def _save_artifact(path: Path, value: dict[str, Any]) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def _claim(db: Session) -> tuple[uuid.UUID, str] | None:
    now = datetime.now(timezone.utc)
    rows = db.scalars(select(QuestionImportBatch).where(
        QuestionImportBatch.owner_scope == OWNER_SCOPE,
        QuestionImportBatch.status == "review_pending",
        QuestionImportBatch.stage == "s2_completed",
        QuestionImportBatch.files_purged_at.is_(None),
        QuestionImportBatch.expires_at > now,
    ).order_by(QuestionImportBatch.created_at).with_for_update(skip_locked=True))
    for batch in rows:
        if not _ready(batch):
            continue
        checkpoint = dict(batch.checkpoint or {})
        s3 = dict(checkpoint.get("s3") or {})
        state = dict(s3.get("answer_model_matching") or {})
        if not state:
            if not _authorized(batch):
                continue
            items = [item for item in db.scalars(select(QuestionImportItem).where(
                QuestionImportItem.batch_id == batch.id).order_by(QuestionImportItem.ordinal)) if active_candidate(item)]
            state = _new_state(batch, items, _automatic_budget(batch))
            s3["answer_model_matching"] = state
            checkpoint["s3"] = s3
            batch.checkpoint = checkpoint
            if state["status"] != "queued":
                return batch.id, ""
        elif state.get("status") == "processing":
            try:
                if datetime.fromisoformat(state["lease_until"]) >= now:
                    continue
            except (KeyError, TypeError, ValueError):
                pass
        elif state.get("status") != "queued":
            continue
        token = str(uuid.uuid4())
        state["status"] = "processing"
        state["worker_token"] = token
        state["lease_until"] = (now + timedelta(seconds=max(600, int(get_settings().question_import_s2_timeout_seconds) + 120))).isoformat()
        s3["answer_model_matching"] = state
        checkpoint["s3"] = s3
        batch.checkpoint = checkpoint
        return batch.id, token
    return None


def _apply_decisions(batch: QuestionImportBatch, state: dict[str, Any], group: dict[str, Any],
                     decisions: list[dict[str, Any]], items: list[QuestionImportItem]) -> None:
    by_id = {str(item.id): item for item in items}
    by_answer = {row["answer_id"]: row for row in group["context"]["answers"]}
    by_question = {row["item_id"]: row for row in group["context"]["questions"]}
    warnings = list(state.get("review_warnings") or [])
    assignments = dict(state.get("assignments") or {})
    suggestions = list(state.get("suggestions") or [])
    for decision in decisions:
        answer = by_answer[decision["answer_id"]]
        proposed = decision["candidate_item_id"]
        current = answer.get("current_item_id")
        question = by_question.get(proposed) if proposed else None
        quote = _text_key(decision["evidence_quote"])
        quoted_text = " ".join(str(value or "") for value in (
            question.get("stem") if question else None,
            question.get("page_heading") if question else None,
            answer.get("question_excerpt"), answer.get("page_heading")))
        credible = bool(proposed and len(quote) >= 4 and quote in _text_key(quoted_text))
        item = by_id.get(proposed) if proposed else None
        if item is None:
            credible = False
        auto_updated = (item is not None and item.payload.get("answer_match_auto_revision") == item.revision
                        and str((item.payload.get("content") or {}).get("question_text") or "")[:600] == question["stem"])
        if item and ((item.revision != question["revision"] and not auto_updated)
                     or not _replaceable_source_answer(item)):
            credible = False
        if current:
            if proposed != current or not credible:
                warnings.append({"answer_id": decision["answer_id"], "current_item_id": current,
                                 "suggested_item_id": proposed if credible else None,
                                 "reason": decision["reason"], "page": answer.get("page"),
                                 "evidence_quote": decision["evidence_quote"],
                                 "kind": "disagreement" if proposed != current and credible else "uncertain"})
        elif credible:
            if not (item.payload.get("answers") or []):
                assignments[decision["answer_id"]] = proposed
            else:
                suggestions.append({"answer_id": decision["answer_id"], "suggested_item_id": proposed,
                                    "reason": "建议目标已有其他答案，需教师核对：" + decision["reason"]})
        elif proposed:
            suggestions.append({"answer_id": decision["answer_id"], "suggested_item_id": proposed,
                                "reason": "模型建议缺少可核验依据或目标已被编辑：" + decision["reason"]})
    state["assignments"] = assignments
    state["suggestions"] = suggestions
    state["review_warnings"] = warnings


def _apply_completed_shards(batch: QuestionImportBatch, state: dict[str, Any], bundle_id: str,
                            items: list[QuestionImportItem]) -> None:
    shards = [row for row in state["groups"] if row.get("bundle_id") == bundle_id]
    if not shards or any(row["status"] != "completed" for row in shards):
        return
    answer = shards[0]["context"]["answers"][0]
    questions = {question["item_id"]: question for shard in shards
                 for question in shard["context"]["questions"]}
    combined = {"context": {"answers": [answer], "questions": list(questions.values())}}
    decisions = [shard["decisions"][0] for shard in shards]
    proposals = {row["candidate_item_id"] for row in decisions if row["candidate_item_id"]}

    def supported(row: dict[str, Any]) -> bool:
        question = questions.get(row["candidate_item_id"])
        quote = _text_key(row.get("evidence_quote"))
        source = " ".join(str(value or "") for value in (
            question.get("stem") if question else None,
            question.get("page_heading") if question else None,
            answer.get("question_excerpt"), answer.get("page_heading")))
        return bool(question and len(quote) >= 4 and quote in _text_key(source))

    if len(proposals) > 1:
        if answer.get("current_item_id"):
            alternatives = [row for row in decisions
                            if row["candidate_item_id"] not in {None, answer["current_item_id"]}]
            choice = next((row for row in alternatives if supported(row)), alternatives[0])
            _apply_decisions(batch, state, combined, [{**choice,
                "reason": "不同候选题分片给出不同建议，请人工核对；" + choice["reason"]}], items)
        else:
            choice = next((row for row in decisions if supported(row)), decisions[0])
            state["suggestions"] = [*(state.get("suggestions") or []), {
                "answer_id": bundle_id, "suggested_item_id": choice["candidate_item_id"],
                "reason": "不同候选题分片给出冲突建议，请人工核对"}]
        return
    if not proposals:
        choice = decisions[0]
    else:
        choice = next((row for row in decisions if row["candidate_item_id"] in proposals
                       and supported(row)),
                      next(row for row in decisions if row["candidate_item_id"] in proposals))
    if (answer.get("current_item_id") and choice["candidate_item_id"] == answer["current_item_id"]
            and any(row["candidate_item_id"] is None for row in decisions)):
        state["review_warnings"] = [*(state.get("review_warnings") or []), {
            "answer_id": bundle_id, "current_item_id": answer["current_item_id"],
            "suggested_item_id": None, "kind": "uncertain", "evidence_quote": "",
            "reason": "部分候选题分片无法确定现有关联，请人工核对"}]
        return
    _apply_decisions(batch, state, combined, [choice], items)


def process_answer_matching_one() -> bool:
    with SessionLocal() as db:
        claimed = _claim(db)
        db.commit()
    if claimed is None:
        return False
    batch_id, token = claimed
    if not token:
        return True
    with SessionLocal() as db:
        batch = db.get(QuestionImportBatch, batch_id)
        state = dict(((batch.checkpoint or {}).get("s3") or {}).get("answer_model_matching") or {})
        group = next((row for row in state.get("groups") or [] if row["status"] == "pending"), None)
        if group is None:
            state["status"] = "completed"
            state.pop("worker_token", None)
            state.pop("lease_until", None)
            checkpoint = dict(batch.checkpoint or {})
            s3 = dict(checkpoint.get("s3") or {})
            s3["answer_model_matching"] = state
            checkpoint["s3"] = s3
            batch.checkpoint = checkpoint
            db.commit()
            return True
        artifact = _artifact_path(batch, group["index"])
        context = group["context"]
    settings = get_settings()
    reserve = round((len(json.dumps(context, ensure_ascii=False)) / 2
                     * settings.question_import_s2_input_million_price_cny
                     + min(MAX_OUTPUT_TOKENS, settings.question_import_s2_max_output_tokens)
                     * settings.question_import_s2_output_million_price_cny) / 1_000_000, 6)
    if float(state["estimated_cny"]) + reserve > float(state["budget_cny"]):
        _finish_group(batch_id, token, group["index"], None, None, "pending_budget",
                      "答案模型配对预算不足，请提高预算后继续")
        return True
    try:
        if artifact.is_file():
            saved = json.loads(artifact.read_text(encoding="utf-8"))
            if saved.get("version") != VERSION or saved.get("context") != context:
                raise ImportProblem("S3_MATCH_ARTIFACT_CHANGED", "配对检查点与原始响应不一致，请联系管理员")
            body = saved["response"]
            decisions = _validate_response(body, context)
            prices = saved.get("price_snapshot") or _price_snapshot()
            meta = {"usage": body.get("usage") or {}, "estimated_cny": _text_cost(body.get("usage") or {}, prices),
                    "response_id": body.get("id"), "model": saved.get("model"), "price_snapshot": prices}
        else:
            decisions, meta = QwenAnswerMatchProvider().review(
                context, capture_response=lambda body: _save_artifact(
                    artifact, {"version": VERSION, "context": context, "response": body,
                               "model": settings.question_import_s2_model,
                               "price_snapshot": _price_snapshot()}))
        if meta["estimated_cny"] is None:
            _finish_group(batch_id, token, group["index"], None, meta, "failed",
                          "答案配对模型用量未知，请先核对账单")
        else:
            _finish_group(batch_id, token, group["index"], decisions, meta, None, None)
    except Exception as exc:
        logger.exception("Answer matching failed for batch %s group %s", batch_id, group["index"])
        usage = {}
        prices = _price_snapshot()
        if artifact.is_file():
            try:
                saved = json.loads(artifact.read_text(encoding="utf-8"))
                usage = (saved.get("response") or {}).get("usage") or {}
                prices = saved.get("price_snapshot") or prices
            except (OSError, ValueError):
                pass
        cost = _text_cost(usage, prices)
        _finish_group(batch_id, token, group["index"], None,
                      {"usage": usage, "estimated_cny": cost,
                       "model": settings.question_import_s2_model, "price_snapshot": prices}, "failed",
                      str(exc) if isinstance(exc, ImportProblem) else "答案配对失败，请核对账单后重试")
    return True


def _finish_group(batch_id: uuid.UUID, token: str, index: int,
                  decisions: list[dict[str, Any]] | None, meta: dict[str, Any] | None,
                  error_status: str | None, error_message: str | None) -> None:
    with SessionLocal() as db:
        batch = db.scalar(select(QuestionImportBatch).where(
            QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE).with_for_update())
        if batch is None:
            return
        checkpoint = dict(batch.checkpoint or {})
        s3 = dict(checkpoint.get("s3") or {})
        state = dict(s3.get("answer_model_matching") or {})
        if state.get("worker_token") != token:
            return
        group = next(row for row in state["groups"] if row["index"] == index)
        if meta is not None:
            state["calls"] = int(state["calls"]) + 1
            cost = meta.get("estimated_cny")
            if cost is None:
                state["cost_unknown"] = True
            else:
                state["estimated_cny"] = round(float(state["estimated_cny"]) + float(cost), 6)
            group["usage"] = meta.get("usage") or {}
            group["response_id"] = meta.get("response_id")
            group["model"] = meta.get("model") or get_settings().question_import_s2_model
            group["price_snapshot"] = meta.get("price_snapshot") or _price_snapshot()
        if decisions is not None:
            items = [item for item in db.scalars(select(QuestionImportItem).where(
                QuestionImportItem.batch_id == batch.id).order_by(QuestionImportItem.ordinal).with_for_update())
                     if active_candidate(item)]
            group["status"] = "completed"
            group["decisions"] = decisions
            if group.get("bundle_id"):
                _apply_completed_shards(batch, state, group["bundle_id"], items)
            else:
                _apply_decisions(batch, state, group, decisions, items)
        elif error_status == "failed":
            group["status"] = "failed"
        state["status"] = error_status or (
            "completed" if all(row["status"] == "completed" for row in state["groups"]) else "queued")
        if error_status:
            state["error_message"] = error_message
            state["error_code"] = "S3_MATCH_BUDGET" if error_status == "pending_budget" else "S3_MATCH_FAILED"
        state.pop("worker_token", None)
        state.pop("lease_until", None)
        s3["answer_model_matching"] = state
        checkpoint["s3"] = s3
        batch.checkpoint = checkpoint
        if decisions is not None and state.get("assignments"):
            reconcile_source_answers(db, batch_id, force=True)
        db.commit()
