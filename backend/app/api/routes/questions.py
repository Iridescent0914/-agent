from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import case, distinct, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.database import get_db
from app.models import ExamPoint, KnowledgePoint, Question, QuestionExamPoint
from app.schemas.common import Page
from app.schemas.questions import (
    QuestionArchiveRequest,
    AssetOut,
    QuestionCatalogStats,
    QuestionCreateRequest,
    QuestionDetail,
    QuestionListItem,
    QuestionReviewRequest,
    QuestionWithdrawRequest,
    TagOut,
)
from app.services.assets import resolve_assets, store_teacher_asset, teacher_asset_exists
from app.services.question_review import (
    QuestionReviewConflict,
    QuestionReviewValidation,
    promote_question,
    save_review_draft,
    withdraw_question,
)


router = APIRouter(prefix="/questions", tags=["questions"])

ALLOWED_QUESTION_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _main_exam(question: Question) -> ExamPoint:
    for link in question.exam_point_links:
        if link.relation_type == "main":
            return link.exam_point
    raise RuntimeError(f"Question {question.original_qid} has no main exam point")


def _summary(question: Question) -> QuestionListItem:
    exam = _main_exam(question)
    return QuestionListItem(
        id=question.id,
        original_qid=question.original_qid,
        question_type=question.question_type,
        difficulty=question.difficulty,
        section=question.section,
        question_text=question.question_text,
        main_knowledge_point=TagOut(
            code=question.main_knowledge_point.code,
            name=question.main_knowledge_point.name,
        ),
        main_exam_point=TagOut(code=exam.code, name=exam.name),
        has_image=question.has_image,
        has_equation=question.has_equation,
        asset_count=len(question.assets or []),
        grading_readiness=question.grading_readiness,
        import_status=question.import_status,
        import_issue_count=len(question.import_issues or []),
    )


def _base_query():
    return select(Question).options(
        selectinload(Question.main_knowledge_point),
        selectinload(Question.exam_point_links).selectinload(QuestionExamPoint.exam_point),
    ).where(Question.archived_at.is_(None))


def _detail(db: Session, question_id: uuid.UUID) -> QuestionDetail:
    question = db.scalar(_base_query().where(Question.id == question_id))
    if question is None:
        raise HTTPException(status_code=404, detail="question not found")
    summary = _summary(question)
    related = [
        TagOut(code=link.exam_point.code, name=link.exam_point.name)
        for link in question.exam_point_links
        if link.relation_type == "related"
    ]
    return QuestionDetail(
        **summary.model_dump(),
        chapter=TagOut(code=question.chapter_code, name=question.chapter_name),
        options=question.options,
        correct_answer=question.correct_answer,
        reference_answer_raw=question.reference_answer_raw,
        answer_match_type=question.answer_match_type,
        answer_normalization_rule=question.answer_normalization_rule,
        related_exam_points=related,
        solution=question.solution,
        wrong_analysis=question.wrong_analysis,
        estimated_score_rate=question.estimated_score_rate,
        assets=resolve_assets(question.assets or []),
        source_file=question.source_file,
        source_question_number=question.source_question_number,
        source_review_status=question.source_review_status,
        import_issues=question.import_issues or [],
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
async def upload_question_asset(file: UploadFile = File(...)) -> AssetOut:
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
        return AssetOut(**store_teacher_asset(payload))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post('', response_model=QuestionDetail, status_code=201)
def create_question(
    request: QuestionCreateRequest,
    db: Session = Depends(get_db),
) -> QuestionDetail:
    exam = db.scalar(
        select(ExamPoint)
        .options(selectinload(ExamPoint.knowledge_point))
        .where(ExamPoint.code == request.main_exam_point_code)
    )
    if exam is None:
        raise HTTPException(status_code=422, detail='主考点不存在')
    original_qid = request.original_qid or (
        f'q.{exam.knowledge_point.chapter_code}.teacher.{uuid.uuid4().hex[:12]}'
    )
    if db.scalar(select(Question.id).where(Question.original_qid == original_qid)):
        raise HTTPException(status_code=409, detail='题目编号已存在')

    missing_assets = [
        asset.path for asset in request.assets if not teacher_asset_exists(asset.path)
    ]
    if missing_assets:
        raise HTTPException(status_code=422, detail='题目图片不存在或已失效，请重新上传')

    asset_records = [asset.model_dump() for asset in request.assets]
    question_text = request.question_text
    for asset in asset_records:
        marker = f"[ASSET:{asset['path']}]"
        if marker not in question_text:
            question_text = f"{question_text.rstrip()}\n{marker}"

    source_number = int(db.scalar(
        select(func.max(Question.source_question_number)).where(
            Question.source_file == 'teacher_upload'
        )
    ) or 0) + 1
    now = datetime.now(timezone.utc)
    question = Question(
        original_qid=original_qid,
        schema_version='teacher-question-v1',
        chapter_code=exam.knowledge_point.chapter_code,
        chapter_name=exam.knowledge_point.chapter_name,
        section=request.section,
        question_type=request.question_type,
        difficulty=request.difficulty,
        main_knowledge_point_id=exam.knowledge_point_id,
        question_text=question_text,
        options=request.options,
        correct_answer=None,
        reference_answer_raw=request.reference_answer_raw,
        answer_match_type=None,
        answer_normalization_rule={},
        solution=request.solution,
        wrong_analysis=request.wrong_analysis,
        estimated_score_rate=None,
        has_image=bool(asset_records),
        has_equation=any(mark in question_text for mark in ('$', '\\(', '\\[')),
        assets=asset_records,
        source_file='teacher_upload',
        source_question_number=source_number,
        source_review_status='reviewed',
        grading_readiness='raw_answer_only',
        import_status='teacher_created',
        import_issues=['answer_items_not_structured'],
        review_revision=1,
        review_history=[{
            'event_id': str(uuid.uuid4()),
            'type': 'created',
            'at': now.isoformat(),
            'reviewer': request.operator_name,
            'note': '教师手动新增题目',
            'before_revision': 0,
            'after_revision': 1,
        }],
        reviewed_by=request.operator_name,
        reviewed_at=now,
    )
    db.add(question)
    db.flush()
    db.add(QuestionExamPoint(
        question_id=question.id,
        exam_point_id=exam.id,
        relation_type='main',
    ))
    db.flush()
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
    knowledge_point_code: str | None = None,
    exam_point_code: str | None = None,
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
    if knowledge_point_code:
        filters.append(KnowledgePoint.code == knowledge_point_code)
    if exam_point_code:
        filters.append(ExamPoint.code == exam_point_code)
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
    if exam_point_code:
        query = query.join(Question.exam_point_links).join(QuestionExamPoint.exam_point)
        count_query = count_query.join(Question.exam_point_links).join(QuestionExamPoint.exam_point)
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
