from __future__ import annotations

import uuid
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import case, distinct, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.database import get_db
from app.models import KnowledgePoint, Question
from app.schemas.common import Page
from app.schemas.questions import (
    QuestionArchiveRequest,
    AssetOut,
    QuestionCatalogChapter,
    QuestionCatalogKnowledgePoint,
    QuestionCatalogStats,
    QuestionDetail,
    QuestionListItem,
    QuestionReviewRequest,
    QuestionUpdateRequest,
    QuestionWithdrawRequest,
    TagOut,
)
from app.services.assets import (
    actionable_import_issues,
    cleanup_orphan_teacher_assets,
    delete_teacher_asset_if_unreferenced,
    question_asset_exists,
    resolve_assets,
    store_teacher_asset,
)
from app.services.knowledge_catalog import formal_kp, formal_literacy_dimensions, formal_question_literacy
from app.services.question_import import content_fingerprint
from app.services.question_review import (
    QuestionReviewConflict,
    QuestionReviewValidation,
    promote_question,
    save_review_draft,
    withdraw_question,
)


router = APIRouter(prefix="/questions", tags=["questions"])

ALLOWED_QUESTION_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _question_text_with_assets(
    question_text: str, assets: list[dict[str, str]]
) -> str:
    retained = {asset["path"] for asset in assets}

    def keep_imported_asset(match: re.Match[str]) -> str:
        path = match.group(1)
        if path in retained and path.startswith(("assets/rational/", "assets/expr/")):
            return match.group(0)
        return ""

    clean = re.sub(r"\[ASSET:([^\]]+)\]", keep_imported_asset, question_text)
    for path in (
        asset["path"] for asset in assets
        if asset["path"].startswith("assets/teacher/")
    ):
        clean = f"{clean.rstrip()}\n[ASSET:{path}]"
    return clean.strip()


def _summary(question: Question) -> QuestionListItem:
    import_issues = actionable_import_issues(
        question.import_issues or [],
        question.assets or [],
        question.grading_readiness,
    )
    return QuestionListItem(
        id=question.id,
        original_qid=question.original_qid,
        question_type=question.question_type,
        difficulty=question.difficulty,
        grade=question.grade,
        section=question.section,
        question_text=question.question_text,
        main_knowledge_point=TagOut(
            code=question.main_knowledge_point.code,
            name=question.main_knowledge_point.name,
        ),
        core_literacy=question.core_literacy,
        is_required=question.is_required,
        has_image=question.has_image,
        has_equation=question.has_equation,
        asset_count=len(question.assets or []),
        grading_readiness=question.grading_readiness,
        import_status=question.import_status,
        import_issue_count=len(import_issues),
    )


def _base_query():
    return select(Question).options(
        selectinload(Question.main_knowledge_point),
    ).where(Question.archived_at.is_(None))


def _detail(db: Session, question_id: uuid.UUID) -> QuestionDetail:
    question = db.scalar(_base_query().where(Question.id == question_id))
    if question is None:
        raise HTTPException(status_code=404, detail="question not found")
    summary = _summary(question)
    return QuestionDetail(
        **summary.model_dump(),
        chapter=TagOut(code=question.chapter_code, name=question.chapter_name),
        options=question.options,
        correct_answer=question.correct_answer,
        reference_answer_raw=question.reference_answer_raw,
        answer_match_type=question.answer_match_type,
        answer_normalization_rule=question.answer_normalization_rule,
        solution=question.solution,
        wrong_analysis=question.wrong_analysis,
        estimated_score_rate=question.estimated_score_rate,
        assets=resolve_assets(question.assets or []),
        source_file=question.source_file,
        source_question_number=question.source_question_number,
        source_review_status=question.source_review_status,
        import_issues=actionable_import_issues(
            question.import_issues or [],
            question.assets or [],
            question.grading_readiness,
        ),
        review_revision=question.review_revision,
        review_history=question.review_history or [],
        reviewed_by=question.reviewed_by,
        reviewed_at=question.reviewed_at,
        created_at=question.created_at,
        updated_at=question.updated_at,
    )


def _review_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, QuestionReviewConflict):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


@router.post('/assets', response_model=AssetOut, status_code=201)
async def upload_question_asset(
    file: UploadFile = File(...), db: Session = Depends(get_db)
) -> AssetOut:
    if file.content_type not in ALLOWED_QUESTION_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail='仅支持 JPG、PNG 或 WebP 图片',
        )
    settings = get_settings()
    payload = await file.read(settings.question_asset_max_upload_bytes + 1)
    await file.close()
    if not payload:
        raise HTTPException(status_code=422, detail='图片文件不能为空')
    if len(payload) > settings.question_asset_max_upload_bytes:
        raise HTTPException(status_code=413, detail='单张图片不能超过 8MB')
    try:
        cleanup_orphan_teacher_assets(db)
        return AssetOut(**store_teacher_asset(payload))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete('/assets/{filename}', status_code=204)
def delete_question_asset(
    filename: str, db: Session = Depends(get_db)
) -> Response:
    try:
        delete_teacher_asset_if_unreferenced(db, f"assets/teacher/{filename}")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)


@router.put('/{question_id}', response_model=QuestionDetail)
def update_question(
    question_id: uuid.UUID,
    request: QuestionUpdateRequest,
    db: Session = Depends(get_db),
) -> QuestionDetail:
    question = db.scalar(
        _base_query().where(Question.id == question_id).with_for_update()
    )
    if question is None:
        raise HTTPException(status_code=404, detail='question not found')
    if question.review_revision != request.expected_review_revision:
        raise HTTPException(status_code=409, detail='题目已被其他操作修改，请刷新后重试')

    knowledge_point = db.scalar(
        select(KnowledgePoint).where(
            KnowledgePoint.code == request.main_knowledge_point_code
        )
    )
    if knowledge_point is None:
        raise HTTPException(status_code=422, detail='节内主属 KP 不存在')
    try:
        canonical_kp = formal_kp(knowledge_point.code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail='节内主属 KP 不在正式知识图谱中') from exc
    if (
        knowledge_point.name != canonical_kp['name']
        or knowledge_point.chapter_code != canonical_kp['chapter_code']
        or knowledge_point.chapter_name != canonical_kp['chapter_name']
    ):
        raise HTTPException(status_code=409, detail='数据库 KP 与正式知识图谱不一致，请先修复目录')
    original_qid = request.original_qid or question.original_qid
    duplicate = db.scalar(
        select(Question.id).where(
            Question.original_qid == original_qid,
            Question.id != question.id,
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail='题目编号已存在')
    missing_assets = [
        asset.path for asset in request.assets if not question_asset_exists(asset.path)
    ]
    if missing_assets:
        raise HTTPException(status_code=422, detail='题目图片不存在或已失效，请重新上传')

    asset_records = [asset.model_dump() for asset in request.assets]
    before_revision = question.review_revision
    now = datetime.now(timezone.utc)
    question.original_qid = original_qid
    question.chapter_code = canonical_kp['chapter_code']
    question.chapter_name = canonical_kp['chapter_name']
    question.grade = canonical_kp['grade']
    question.section = canonical_kp['section']
    try:
        question.core_literacy = formal_question_literacy(knowledge_point.code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail='该 KP 的核心素养尚未映射到正式题目标签') from exc
    question.question_type = request.question_type
    question.difficulty = request.difficulty
    question.main_knowledge_point_id = knowledge_point.id
    question.question_text = _question_text_with_assets(
        request.question_text, asset_records
    )
    question.options = request.options
    question.reference_answer_raw = request.reference_answer_raw
    question.solution = request.solution
    question.wrong_analysis = request.wrong_analysis
    question.estimated_score_rate = request.estimated_score_rate
    question.is_required = request.is_required
    question.has_image = bool(asset_records)
    question.has_equation = any(
        mark in (question.question_text + (question.solution or ""))
        for mark in ('$', '\\(', '\\[')
    )
    question.assets = asset_records
    question.content_fingerprint = content_fingerprint(
        question.question_text, question.options, asset_records
    )
    # Content changes invalidate the previously structured answer. Published
    # papers keep their immutable snapshots and are not affected.
    question.correct_answer = None
    question.answer_match_type = None
    question.answer_normalization_rule = {}
    question.grading_readiness = 'raw_answer_only'
    question.source_review_status = 'reviewed'
    question.import_issues = ['answer_items_not_structured']
    question.review_revision += 1
    question.reviewed_by = request.operator_name
    question.reviewed_at = now
    question.review_history = [
        *(question.review_history or []),
        {
            'event_id': str(uuid.uuid4()),
            'type': 'content_updated',
            'at': now.isoformat(),
            'reviewer': request.operator_name,
            'note': '教师编辑题目内容，结构化答案已重置',
            'before_revision': before_revision,
            'after_revision': question.review_revision,
        },
    ]
    db.flush()
    db.expire(question, ['main_knowledge_point'])
    return _detail(db, question.id)


@router.delete('/{question_id}')
def archive_question(
    question_id: uuid.UUID,
    request: QuestionArchiveRequest,
    db: Session = Depends(get_db),
):
    question = db.scalar(
        select(Question).where(
            Question.id == question_id,
            Question.archived_at.is_(None),
        ).with_for_update()
    )
    if question is None:
        raise HTTPException(status_code=404, detail='question not found')
    question.archived_at = datetime.now(timezone.utc)
    question.archived_by = request.operator_name
    db.flush()
    return {'deleted_id': str(question.id)}


@router.get("", response_model=Page[QuestionListItem])
def list_questions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    question_type: str | None = Query(
        None,
        pattern="^(choice|fill_blank|calculation|solution|true_false|unknown)$",
    ),
    grading_readiness: str | None = Query(
        None, pattern="^(machine_ready|raw_answer_only)$"
    ),
    difficulty: int | None = Query(None, ge=1, le=5),
    chapter_code: str | None = None,
    section: str | None = Query(None, max_length=255),
    knowledge_point_code: str | None = None,
    keyword: str | None = Query(None, max_length=100),
    db: Session = Depends(get_db),
) -> Page[QuestionListItem]:
    filters = [Question.archived_at.is_(None)]
    if question_type:
        filters.append(Question.question_type == question_type)
    if grading_readiness:
        filters.append(Question.grading_readiness == grading_readiness)
    if difficulty is not None:
        filters.append(Question.difficulty == difficulty)
    if chapter_code:
        filters.append(Question.chapter_code == chapter_code)
    if section:
        filters.append(Question.section == section)
    if knowledge_point_code:
        filters.append(KnowledgePoint.code == knowledge_point_code)
    if keyword:
        escaped = keyword.replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        filters.append(
            or_(Question.original_qid.ilike(pattern, escape="\\"), Question.question_text.ilike(pattern, escape="\\"))
        )

    query = _base_query().join(Question.main_knowledge_point)
    count_query = select(func.count(distinct(Question.id))).select_from(Question).join(
        Question.main_knowledge_point
    )
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)

    total = db.scalar(count_query) or 0
    rows = db.scalars(
        query.order_by(
            case((Question.grading_readiness == "machine_ready", 0), else_=1),
            case(
                (Question.question_type == "choice", 0),
                (Question.question_type == "fill_blank", 1),
                (Question.question_type == "calculation", 2),
                (Question.question_type == "solution", 3),
                (Question.question_type == "true_false", 4),
                else_=5,
            ),
            Question.source_question_number,
            Question.id,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Page(
        items=[_summary(question) for question in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/stats", response_model=QuestionCatalogStats)
def question_catalog_stats(db: Session = Depends(get_db)) -> QuestionCatalogStats:
    rows = db.execute(
        select(
            Question.question_type,
            Question.grading_readiness,
            func.count(Question.id),
        ).where(Question.archived_at.is_(None)).group_by(
            Question.question_type, Question.grading_readiness
        )
    ).all()
    type_counts: dict[str, int] = {}
    ready_count = 0
    total = 0
    for question_type, readiness, count in rows:
        value = int(count)
        total += value
        type_counts[question_type] = type_counts.get(question_type, 0) + value
        if readiness == "machine_ready":
            ready_count += value
    return QuestionCatalogStats(
        total=total,
        ready_count=ready_count,
        pending_count=total - ready_count,
        type_counts=type_counts,
    )


@router.get('/literacy-dimensions', response_model=list[str])
def question_literacy_dimensions() -> list[str]:
    return list(formal_literacy_dimensions())


@router.get('/catalog', response_model=list[QuestionCatalogChapter])
def question_filter_catalog(db: Session = Depends(get_db)) -> list[QuestionCatalogChapter]:
    active_chapter_codes = select(Question.chapter_code).where(
        Question.archived_at.is_(None)
    ).distinct()
    knowledge_points = db.scalars(
        select(KnowledgePoint)
        # The catalog also drives profile chapter filters.  Include formal
        # points without a current question so the selected chapter remains
        # complete and those points can be shown as 0% / unassessed.
        .where(KnowledgePoint.chapter_code.in_(active_chapter_codes))
        .order_by(
            KnowledgePoint.chapter_name,
            KnowledgePoint.sort_order,
            KnowledgePoint.name,
        )
    ).unique().all()
    chapters: dict[str, QuestionCatalogChapter] = {}
    for knowledge_point in knowledge_points:
        chapter = chapters.setdefault(
            knowledge_point.chapter_code,
            QuestionCatalogChapter(
                code=knowledge_point.chapter_code,
                name=knowledge_point.chapter_name,
                sections=[],
                knowledge_points=[],
                grades=[],
                core_literacies=[],
            ),
        )
        canonical_kp = formal_kp(knowledge_point.code)
        formal_section = canonical_kp['section']
        chapter.knowledge_points.append(QuestionCatalogKnowledgePoint(
            code=knowledge_point.code,
            name=knowledge_point.name,
            section=formal_section,
            mastery_level=knowledge_point.mastery_level,
            core_competencies=knowledge_point.core_competencies,
        ))
        if formal_section and formal_section not in chapter.sections:
            chapter.sections.append(formal_section)
    metadata_rows = db.execute(
        select(Question.chapter_code, Question.grade, Question.core_literacy)
        .where(Question.archived_at.is_(None))
        .distinct()
        .order_by(Question.chapter_code, Question.grade, Question.core_literacy)
    ).all()
    for chapter_code, grade, core_literacy in metadata_rows:
        chapter = chapters.get(chapter_code)
        if chapter is None:
            continue
        if grade and grade not in chapter.grades:
            chapter.grades.append(grade)
        if core_literacy and core_literacy not in chapter.core_literacies:
            chapter.core_literacies.append(core_literacy)
    return list(chapters.values())


@router.get("/{question_id}", response_model=QuestionDetail)
def get_question(question_id: uuid.UUID, db: Session = Depends(get_db)) -> QuestionDetail:
    return _detail(db, question_id)


@router.put("/{question_id}/review-draft", response_model=QuestionDetail)
def update_review_draft(
    question_id: uuid.UUID,
    request: QuestionReviewRequest,
    db: Session = Depends(get_db),
) -> QuestionDetail:
    try:
        save_review_draft(db, question_id, request)
        return _detail(db, question_id)
    except (LookupError, QuestionReviewConflict, QuestionReviewValidation) as exc:
        raise _review_error(exc) from exc


@router.post("/{question_id}/promote", response_model=QuestionDetail)
def confirm_question_for_pool(
    question_id: uuid.UUID,
    request: QuestionReviewRequest,
    db: Session = Depends(get_db),
) -> QuestionDetail:
    try:
        promote_question(db, question_id, request)
        return _detail(db, question_id)
    except (LookupError, QuestionReviewConflict, QuestionReviewValidation) as exc:
        raise _review_error(exc) from exc


@router.post("/{question_id}/withdraw", response_model=QuestionDetail)
def withdraw_question_from_pool(
    question_id: uuid.UUID,
    request: QuestionWithdrawRequest,
    db: Session = Depends(get_db),
) -> QuestionDetail:
    try:
        withdraw_question(db, question_id, request)
        return _detail(db, question_id)
    except (LookupError, QuestionReviewConflict, QuestionReviewValidation) as exc:
        raise _review_error(exc) from exc
