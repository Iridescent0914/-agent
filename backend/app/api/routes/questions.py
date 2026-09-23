from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.models import ExamPoint, KnowledgePoint, Question, QuestionExamPoint
from app.schemas.common import Page
from app.schemas.questions import QuestionDetail, QuestionListItem, TagOut
from app.services.assets import resolve_assets


router = APIRouter(prefix="/questions", tags=["questions"])


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
    )


def _base_query():
    return select(Question).options(
        selectinload(Question.main_knowledge_point),
        selectinload(Question.exam_point_links).selectinload(QuestionExamPoint.exam_point),
    )


@router.get("", response_model=Page[QuestionListItem])
def list_questions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    question_type: str | None = Query(None, pattern="^(choice|fill_blank)$"),
    difficulty: int | None = Query(None, ge=1, le=5),
    knowledge_point_code: str | None = None,
    exam_point_code: str | None = None,
    keyword: str | None = Query(None, max_length=100),
    db: Session = Depends(get_db),
) -> Page[QuestionListItem]:
    filters = []
    if question_type:
        filters.append(Question.question_type == question_type)
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
        query.distinct()
        .order_by(Question.original_qid, Question.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Page(
        items=[_summary(question) for question in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{question_id}", response_model=QuestionDetail)
def get_question(question_id: uuid.UUID, db: Session = Depends(get_db)) -> QuestionDetail:
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
        grading_readiness=question.grading_readiness,
        created_at=question.created_at,
        updated_at=question.updated_at,
    )
