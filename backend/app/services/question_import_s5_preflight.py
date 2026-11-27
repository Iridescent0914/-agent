from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import QuestionImportItem
from app.schemas.question_import_s5 import S5PreflightRequest, S5PreflightSelection
from app.services.answer_units import _expected_coordinates, _prefix_subquestion_markers
from app.services.knowledge_catalog import CATALOG_PATH
from app.services.profile_evidence import PROFILE_QUESTION_TYPES
from app.services.question_import import active_candidate, get_batch
from app.services.question_import_s3_images import _asset_path, list_images
from app.services.question_import_s3_labels import _allowed_kps
from app.services.question_import_figure_review import figure_text_confirmed
from app.services.question_import_s3_review import list_review_issues
from app.services.question_import_s4_near import VERSION as NEAR_VERSION
from app.services.question_import_s4_review import read_catalog_review
from app.services.question_fingerprint_v2 import VERSION as EXACT_VERSION
from app.services.question_review import QuestionReviewValidation, prepare_pool_review


QUESTION_TYPES = frozenset({"choice", "fill_blank", "true_false", "calculation", "solution"})


def _problem(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _context(db: Session, batch_id: uuid.UUID) -> dict[str, Any]:
    batch = get_batch(db, batch_id)
    items = list(db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch_id
    ).order_by(QuestionImportItem.ordinal)))
    review = read_catalog_review(db, batch_id)
    return {
        "batch": batch,
        "items": items,
        "item_by_id": {item.id: item for item in items},
        "review_by_id": {row["item_id"]: row for row in review["items"]},
        "allowed_kps": _allowed_kps(db),
        "catalog_current": batch.catalog_snapshot_sha256 == hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest(),
        "image_assets": {str(asset["asset_id"]): asset for asset in
                         (((batch.checkpoint or {}).get("s3") or {}).get("images") or {}).get("assets") or []},
        "issues": list_review_issues(db, batch_id),
    }


def _question_issues(ctx: dict[str, Any], item: QuestionImportItem,
                     blockers: list[dict[str, str]], warnings: list[dict[str, str]]) -> str | None:
    payload = item.payload or {}
    content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
    tags = payload.get("tags") if isinstance(payload.get("tags"), dict) else {}
    batch = ctx["batch"]

    text = content.get("question_text")
    options = content.get("options")
    if not isinstance(text, str) or not text.strip() or len(text) > 30000:
        blockers.append(_problem("CONTENT_INCOMPLETE", "题干为空或超过长度限制"))
    if (not isinstance(options, list) or len(options) > 20
            or any(not isinstance(option, dict) or not isinstance(option.get("key"), str)
                   or not option["key"].strip() or not isinstance(option.get("text"), str)
                   or not option["text"].strip() or len(option["text"]) > 10000
                   for option in options)
            or (isinstance(options, list) and len({str(option.get("key", "")).strip()
                                                       for option in options if isinstance(option, dict)}) != len(options))):
        blockers.append(_problem("CONTENT_INCOMPLETE", "选项为空、重复或结构无效"))

    question_type = tags.get("question_type")
    if question_type not in QUESTION_TYPES:
        blockers.append(_problem("TAG_CONFLICT", "题型缺失或不支持入库"))
    elif question_type == "choice" and isinstance(options, list) and len(options) < 2:
        blockers.append(_problem("CONTENT_INCOMPLETE", "选择题至少需要两个选项"))
    if type(tags.get("difficulty")) is not int or not 1 <= tags["difficulty"] <= 5:
        blockers.append(_problem("TAG_CONFLICT", "难度必须为 1～5 星"))
    if type(tags.get("is_required")) is not bool:
        blockers.append(_problem("TAG_CONFLICT", "必做标记缺失或无效"))

    kp_code = tags.get("main_kp_code")
    kp = ctx["allowed_kps"].get(kp_code) if isinstance(kp_code, str) else None
    if kp is None:
        blockers.append(_problem("TAG_OUT_OF_SCOPE", "主属知识点不在当前开放的正式目录及数据库中"))
    elif (tags.get("grade") != kp["grade"]
          or tags.get("chapter_code") != kp["chapter_code"]
          or tags.get("section") != kp["section"]
          or tags.get("core_literacies") != kp["core_competencies"]):
        blockers.append(_problem("TAG_CONFLICT", "年级、章、节或核心素养与正式知识点目录不一致"))

    asset_ids = content.get("asset_ids")
    if not isinstance(asset_ids, list) or len(set(map(str, asset_ids))) != len(asset_ids):
        blockers.append(_problem("ASSET_UNASSIGNED", "题图关联列表无效"))
        asset_ids = []
    extraction = payload.get("extraction") if isinstance(payload.get("extraction"), dict) else {}
    needs_figure = bool(extraction.get("diagram_clues")) or extraction.get("figure_status") in {"complete", "uncertain"}
    if needs_figure and not asset_ids and not figure_text_confirmed(payload):
        blockers.append(_problem("ASSET_UNASSIGNED", "题干所需的关键题图尚未关联"))
    for asset_id in asset_ids:
        asset = ctx["image_assets"].get(str(asset_id))
        try:
            asset_uuid = uuid.UUID(str(asset_id))
        except (ValueError, TypeError, AttributeError):
            asset_uuid = None
        if (asset is None or asset_uuid is None or batch.files_purged_at is not None
                or batch.expires_at <= datetime.now(timezone.utc)
                or not _asset_path(batch.id, asset_uuid).is_file()):
            blockers.append(_problem("ASSET_UNASSIGNED", "已关联题图不存在或已过期"))
            break
        source_file_id = (payload.get("source") or {}).get("file_id")
        if source_file_id and asset.get("source_file_id") != source_file_id:
            blockers.append(_problem("ASSET_UNASSIGNED", "题图与候选题来源文件不一致"))
            break
    if extraction.get("figure_status") == "uncertain" and asset_ids:
        warnings.append(_problem("FIGURE_REVIEW_REQUIRED", "题图已关联，仍需教师核对是否与题干对应"))
    return question_type if isinstance(question_type, str) else None


def _duplicate_issues(ctx: dict[str, Any], item: QuestionImportItem,
                      blockers: list[dict[str, str]]) -> None:
    row = ctx["review_by_id"].get(str(item.id))
    if row is None:
        blockers.append(_problem("DUPLICATE_CHECK_REQUIRED", "请先检查题库重复"))
        return
    if row["candidate_stale"] or row["catalog_stale"]:
        blockers.append(_problem("DUPLICATE_CHECK_STALE", "题目或命中的正式题已变化，请重新检查题库重复"))
    if row.get("exact_version") != EXACT_VERSION or row.get("near_version") != NEAR_VERSION:
        blockers.append(_problem("DUPLICATE_CHECK_STALE", "查重规则已更新，请重新检查题库重复"))
    for match in row["matches"]:
        if match["catalog_unverifiable"] and match["decision"] == "pending":
            blockers.append(_problem("DUPLICATE_UNVERIFIABLE", "相近的正式题内容无法自动核验，请人工核对并作出判断"))
        if match["decision"] == "skip_existing":
            blockers.append(_problem("SKIPPED_DUPLICATE", "该题已决定关联旧题，不应再次建题"))
        elif match["kind"] == "exact":
            blockers.append(_problem("EXACT_DUPLICATE", "题库已有精确重复题，请跳过候选题"))
        elif match["decision"] == "pending":
            blockers.append(_problem("DUPLICATE_DECISION_REQUIRED", "仍有相近题待教师判断"))


def _answer_issues(selection: S5PreflightSelection, item: QuestionImportItem,
                   question_type: str | None, blockers: list[dict[str, str]],
                   warnings: list[dict[str, str]]) -> None:
    if selection.route == "library_only":
        warnings.append(_problem("ANSWER_PENDING", "仅题目入库后答案仍需单独审核；允许暂缺答案"))
        return
    if question_type not in PROFILE_QUESTION_TYPES:
        blockers.append(_problem("POOL_TYPE_UNSUPPORTED", "当前自动组卷池仅支持选择、填空、判断题"))
    payload = item.payload or {}
    selected_id = payload.get("selected_answer_id")
    answer = next((row for row in payload.get("answers") or []
                   if isinstance(row, dict) and row.get("answer_id") == selected_id), None)
    if not selected_id or answer is None or not isinstance(answer.get("text"), str) or not answer["text"].strip():
        blockers.append(_problem("ANSWER_MISSING", "直接入池需要选择有内容的答案版本"))
    elif (payload.get("answer_match_status") in {"uncertain", "unmatched", "conflict"}
          and not answer.get("teacher_confirmed")):
        blockers.append(_problem("ANSWER_SOURCE_UNCERTAIN", "答案与题目的关联尚未确认"))
    if answer is not None and not answer.get("teacher_confirmed"):
        warnings.append(_problem("ANSWER_CONFIRMATION_REQUIRED", "请核对答案草稿；保存直接入池审核时会记录教师对答案内容的确认"))
    if not selection.answer_review_items:
        blockers.append(_problem("ANSWER_UNITS_REQUIRED", "直接入池需要逐小题或空的结构化答案审核草稿"))
    elif question_type in PROFILE_QUESTION_TYPES:
        try:
            review_items = selection.answer_review_items
            if question_type == "choice" and len(review_items) == 1:
                only_answer = review_items[0]
                if only_answer.subquestion_no is not None or only_answer.blank_no is not None:
                    raise QuestionReviewValidation("单道选择题的小题号和空号请留空；有多个小题时请分别添加答案项")
            if len(review_items) > 1 and any(
                row.subquestion_no is None or row.blank_no is None for row in review_items
            ):
                raise QuestionReviewValidation("多个答案项必须逐项填写小题号和空号；未填完整时请选择仅题目入库")
            content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
            question_text = content.get("question_text") if isinstance(content.get("question_text"), str) else ""
            source_answer_text = answer.get("text") if isinstance(answer, dict) else None
            source_has_subanswers = (
                question_type == "choice" and isinstance(source_answer_text, str)
                and len(_prefix_subquestion_markers(source_answer_text)) > 1
            )
            expected = _expected_coordinates(
                question_type, question_text,
                grouped_answer=len(review_items) > 1 or source_has_subanswers,
            )
            if len(expected) > 1:
                actual = [(row.subquestion_no, row.blank_no) for row in review_items]
                if len(review_items) != len(expected) or set(actual) != set(expected):
                    raise QuestionReviewValidation("答案项与题干中识别到的小题或空位不一致；请核对编号，或选择仅题目入库")
            elif len(review_items) > 1:
                coordinates = {(row.subquestion_no, row.blank_no) for row in review_items}
                subquestions = {sub for sub, _ in coordinates}
                if subquestions != set(range(1, max(subquestions) + 1)) or any(
                    {blank for sub, blank in coordinates if sub == number}
                    != set(range(1, max(blank for sub, blank in coordinates if sub == number) + 1))
                    for number in subquestions
                ):
                    raise QuestionReviewValidation("小题号和空号必须从 1 开始连续编号；请核对题目结构")
            if question_type == "choice":
                options = content.get("options") if isinstance(content.get("options"), list) else []
                valid_keys = {option["key"].strip().upper() for option in options
                              if isinstance(option, dict) and isinstance(option.get("key"), str)}
                if any(value.strip().upper() not in valid_keys
                       for row in review_items for value in [row.answer, *row.accepted_answers]):
                    raise QuestionReviewValidation("选择题答案必须是本题实际存在的选项字母")
            prepare_pool_review(question_type, review_items)
        except QuestionReviewValidation as exc:
            blockers.append(_problem("ANSWER_REVIEW_INVALID", str(exc)))


def _issue_issues(ctx: dict[str, Any], selection: S5PreflightSelection,
                  blockers: list[dict[str, str]]) -> None:
    for issue in ctx["issues"]:
        if str(selection.item_id) not in issue["candidate_item_ids"]:
            continue
        if issue["kind"] != "overlap" and not (
            selection.route == "direct_pool" and issue["kind"] in {
                "answer_match_warning", "answer_match_disagreement"
            }
        ):
            continue
        handling = issue["handling"]
        if not (handling and handling["valid"] and handling["decision"] in {"acknowledged", "ignored"}):
            blockers.append(_problem("REVIEW_ISSUE_PENDING", "相关的疑似重复或答案关联问题尚未人工处理"))


def _evaluate(ctx: dict[str, Any], selection: S5PreflightSelection) -> dict[str, Any]:
    item = ctx["item_by_id"].get(selection.item_id)
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    batch = ctx["batch"]
    s3 = (batch.checkpoint or {}).get("s3") or {}
    if (batch.status != "review_pending" or batch.stage != "s2_completed"
            or (s3.get("global_dedupe") or {}).get("status") != "completed"
            or (batch.source_mode == "separate" and
                (s3.get("separate_answers") or {}).get("status") != "completed")
            or any((s3.get(task) or {}).get("status") in {"queued", "processing"}
                   for task in ("answer_model_matching", "answer_generation", "labels"))):
        blockers.append(_problem("BATCH_NOT_READY", "识别与必要的整批去重任务尚未完成"))
    if not ctx["catalog_current"]:
        blockers.append(_problem("CATALOG_CHANGED", "批次冻结的正式标签目录已变化"))
    if item is None or not active_candidate(item) or item.is_fake:
        blockers.append(_problem("CANDIDATE_UNAVAILABLE", "候选题不存在、已删除或是测试题"))
    elif item.revision != selection.expected_revision:
        blockers.append(_problem("REVISION_CONFLICT", "候选题版本已变化，请刷新后重新核对"))
    else:
        question_type = _question_issues(ctx, item, blockers, warnings)
        _duplicate_issues(ctx, item, blockers)
        _answer_issues(selection, item, question_type, blockers, warnings)
        _issue_issues(ctx, selection, blockers)
        if (item.payload or {}).get("teacher_decision") in {"rejected", "skipped_duplicate"}:
            blockers.append(_problem("CANDIDATE_NOT_SELECTED", "已拒绝或跳过的题目不能按新题确认"))
    return {
        "item_id": str(selection.item_id), "ordinal": item.ordinal if item else None,
        "expected_revision": selection.expected_revision,
        "current_revision": item.revision if item else None,
        "route": selection.route, "batch_eligible": not blockers,
        "blockers": blockers, "warnings": warnings,
        "teacher_decision": (item.payload or {}).get("teacher_decision", "pending") if item else None,
        "confirmation_pending": True,
    }


def preflight_import(db: Session, batch_id: uuid.UUID, request: S5PreflightRequest) -> dict[str, Any]:
    """Read-only readiness for S5 confirmation, not a final S6 commit decision."""
    ctx = _context(db, batch_id)
    rows = [_evaluate(ctx, selection) for selection in request.items]
    return {
        "batch_id": str(batch_id), "check_kind": "s5-confirmation-readiness",
        "items": rows,
        "summary": {
            "selected": len(rows),
            "eligible": sum(row["batch_eligible"] for row in rows),
            "blocked": sum(not row["batch_eligible"] for row in rows),
            "library_only": sum(row["route"] == "library_only" for row in rows),
            "direct_pool": sum(row["route"] == "direct_pool" for row in rows),
        },
    }


def import_review_summary(db: Session, batch_id: uuid.UUID) -> dict[str, Any]:
    from app.services.question_import_s5_review import read_item_reviews

    ctx = _context(db, batch_id)
    real = [item for item in ctx["items"] if active_candidate(item) and not item.is_fake]
    library = [_evaluate(ctx, S5PreflightSelection(
        item_id=item.id, expected_revision=item.revision, route="library_only")) for item in real]
    pool = [_evaluate(ctx, S5PreflightSelection(
        item_id=item.id, expected_revision=item.revision, route="direct_pool")) for item in real]
    checks = [ctx["review_by_id"].get(str(item.id)) for item in real]
    images = list_images(db, batch_id)["assets"]
    issues = ctx["issues"]
    reviews = {row["item_id"]: row for row in read_item_reviews(db, batch_id)["items"]}
    approved = sum((reviews.get(str(item.id)) or {}).get("decision") == "approved" for item in real)
    rejected = sum((reviews.get(str(item.id)) or {}).get("decision") == "rejected" for item in real)
    skipped = sum((item.payload or {}).get("teacher_decision") == "skipped_duplicate" for item in real)
    return {
        "batch_id": str(batch_id), "status": ctx["batch"].status, "stage": ctx["batch"].stage,
        "counts": {
            "active_real": len(real),
            "active_fake": sum(active_candidate(item) and item.is_fake for item in ctx["items"]),
            "pending": len(real) - approved - rejected - skipped,
            "approved": approved,
            "rejected": rejected,
            "skipped_duplicate": skipped,
            "invalid_review_records": sum(bool(row.get("question_event_id") and not row["valid"])
                                          for row in reviews.values()),
            "ready_for_library_confirmation": sum(
                row["batch_eligible"] and (reviews.get(row["item_id"]) or {}).get("decision") == "pending"
                for row in library),
            "ready_for_pool_confirmation": sum(
                row["batch_eligible"] and (reviews.get(row["item_id"]) or {}).get("decision") == "pending"
                for row in pool),
            "needs_answer_review_for_pool": sum(
                (reviews.get(row["item_id"]) or {}).get("decision") == "pending"
                and {problem["code"] for problem in row["blockers"]} == {"ANSWER_UNITS_REQUIRED"}
                for row in pool
            ),
            "missing_catalog_check": sum(row is None for row in checks),
            "stale_catalog_check": sum(bool(row and (row["candidate_stale"] or row["catalog_stale"])) for row in checks),
            "exact_match_items": sum(bool(row and any(match["kind"] == "exact" for match in row["matches"])) for row in checks),
            "near_match_items": sum(bool(row and any(match["kind"] == "nearby" for match in row["matches"])) for row in checks),
            "unassigned_answer_issues": sum(issue["kind"] == "answer" for issue in issues),
            "unassigned_images": sum(not image["candidate_item_ids"] and not image["duplicate_of_kept_image"]
                                     for image in images),
            "review_issues": len(issues),
            "resolved_review_issues": sum(bool(issue["handling"] and issue["handling"]["valid"]
                                               and issue["handling"]["decision"] in {"acknowledged", "ignored"})
                                          for issue in issues),
        },
    }
