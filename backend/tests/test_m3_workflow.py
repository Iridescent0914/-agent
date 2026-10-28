from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.database import SessionLocal, get_db
from app.main import app
from app.models import (
    Assignment, Attempt, KnowledgePoint, PaperInstance, PaperInstanceItem,
    Question, Student, Submission,
)
from app.services.paper_snapshots import new_item, snapshot_payload
from app.services.recommendations import (
    history_wrong_answers, recent_completed_assignment_ids,
    required_question_candidates,
)


@pytest.fixture
def db():
    with SessionLocal() as session:
        try:
            yield session
        finally:
            session.rollback()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as value:
            yield value
    finally:
        app.dependency_overrides.pop(get_db, None)


def request_body(db, *, key: str):
    students = list(db.scalars(select(Student).order_by(Student.student_code).limit(2)))
    return {
        "idempotency_key": key,
        "title": "M3 事务测试作业",
        "student_ids": [str(student.id) for student in students],
        "recommendation_parameters": {
            "review_knowledge_point_codes": [
                "math.g6.rational.kp.08_multiply_divide",
                "math.g6.rational.kp.07_add_subtract",
            ],
            "review_count": 4,
            "avoid_recent_days": 0,
            "max_knowledge_point_ratio": 0.5,
        },
    }


def counts(db):
    return tuple(int(db.scalar(select(func.count()).select_from(model)) or 0)
                 for model in (Assignment, PaperInstance, PaperInstanceItem))


def completed_required_source(
    db, student, *, minimum_group_size: int = 2,
    minimum_required_count: int = 0,
    source_module: str = "required",
):
    grouped = defaultdict(list)
    rows = db.execute(
        select(Question, KnowledgePoint)
        .join(KnowledgePoint, KnowledgePoint.id == Question.main_knowledge_point_id)
        .where(
            Question.archived_at.is_(None),
            Question.grading_readiness == "machine_ready",
            Question.source_review_status == "reviewed",
            Question.difficulty <= 3,
        )
        .order_by(Question.original_qid)
    ).all()
    for question, knowledge_point in rows:
        grouped[(
            question.grade, question.chapter_code, question.difficulty,
            knowledge_point.code,
        )].append((question, knowledge_point))
    group = next(
        items for items in grouped.values()
        if len(items) >= minimum_group_size
        and sum(question.is_required for question, _ in items) >= minimum_required_count
    )
    source_question, knowledge_point = group[0]
    now = datetime.now(timezone.utc)
    assignment = Assignment(
        title="已批改历史来源作业",
        class_id=student.class_id,
        chapter_code=source_question.chapter_code,
        teaching_scope={},
        generation_config={},
        assignment_type="practice",
    )
    db.add(assignment)
    db.flush()
    paper = PaperInstance(
        assignment_id=assignment.id,
        student_id=student.id,
        version_no=1,
        status="draft",
        recommendation_audit={},
        edit_history=[],
        edit_revision=1,
    )
    db.add(paper)
    db.flush()
    item = new_item(
        paper_id=paper.id,
        order_no=1,
        payload=snapshot_payload(source_question),
        selection_audit={
            "source": "recommendation",
            "source_module": source_module,
            "reason_codes": (
                ["required_question"] if source_module == "required" else []
            ),
        },
    )
    db.add(item)
    db.flush()
    paper.status = "published"
    paper.instance_code = f"HIS-{uuid4().hex[:12].upper()}"
    paper.published_at = now
    db.flush()
    submission = Submission(
        student_id=student.id,
        paper_instance_id=paper.id,
        source="manual",
        idempotency_key="history-source-" + uuid4().hex,
        request_fingerprint=uuid4().hex,
        attachments=[],
        submitted_at=now,
    )
    db.add(submission)
    db.flush()
    db.add(Attempt(
        submission_id=submission.id,
        student_id=student.id,
        paper_instance_item_id=item.id,
        question_id=source_question.id,
        student_answer="wrong",
        normalized_answer="wrong",
        grading_details={},
        grade_status="teacher_confirmed",
        is_correct=False,
        manual_override=True,
        revision_no=1,
        answered_at=now,
        reviewed_at=now,
    ))
    db.flush()
    return assignment, source_question, knowledge_point


def test_preview_create_idempotency_and_audit(client, db):
    body = request_body(db, key="test-" + uuid4().hex)
    before = counts(db)
    preview_body = {key: value for key, value in body.items() if key not in ("title", "idempotency_key")}
    preview = client.post("/api/v1/assignments/preview", json=preview_body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["student_count"] == 2
    assert len({item["recommendation"]["as_of"] for item in preview.json()["students"]}) == 1
    assert counts(db) == before

    created = client.post("/api/v1/assignments", json=body)
    assert created.status_code == 201, created.text
    assignment = created.json()
    assert assignment["student_count"] == 2 and assignment["draft_count"] == 2
    assert all(paper["item_count"] == 4 for paper in assignment["papers"])
    class_listing = client.get(
        "/api/v1/assignments",
        params={"class_id": assignment["class_id"], "page_size": 100},
    )
    assert class_listing.status_code == 200, class_listing.text
    assert any(item["id"] == assignment["id"] for item in class_listing.json()["items"])
    assert all(
        item["class_id"] == assignment["class_id"]
        for item in class_listing.json()["items"]
    )
    after = counts(db)
    assert after == (before[0] + 1, before[1] + 2, before[2] + 8)
    repeated = client.post("/api/v1/assignments", json=body)
    assert repeated.status_code == 201
    assert repeated.json()["id"] == assignment["id"] and counts(db) == after
    changed = body | {"title": "不同请求"}
    assert client.post("/api/v1/assignments", json=changed).status_code == 409

    paper = client.get(f"/api/v1/paper-instances/{assignment['papers'][0]['id']}").json()
    assert paper["recommendation_audit"]["schema_version"] == "m3-recommendation-audit-v1"
    assert all(item["selection_audit"]["source"] == "recommendation" for item in paper["items"])
    assert all(item["answer_rule_snapshot"] for item in paper["items"])


def test_assignment_can_expand_an_entire_class_without_student_id_limit(client, db):
    student = db.scalar(select(Student).where(Student.archived_at.is_(None)))
    assert student is not None and student.class_id is not None
    expected_count = int(db.scalar(select(func.count(Student.id)).where(
        Student.class_id == student.class_id,
        Student.archived_at.is_(None),
    )) or 0)
    body = request_body(db, key="all-class-" + uuid4().hex)
    body.update({
        "class_id": str(student.class_id),
        "student_scope": "all_class",
        "student_ids": [],
    })
    preview_body = {
        key: value for key, value in body.items()
        if key not in ("title", "idempotency_key")
    }
    preview = client.post("/api/v1/assignments/preview", json=preview_body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["student_count"] == expected_count

    created = client.post("/api/v1/assignments", json=body)
    assert created.status_code == 201, created.text
    assert created.json()["student_count"] == expected_count
    assert created.json()["generation_config"]["student_scope"] == "all_class"
    assert len(created.json()["generation_config"]["resolved_student_ids"]) == expected_count


def test_required_and_selected_history_are_combined_per_student(client, db):
    student = db.scalar(select(Student).where(
        Student.archived_at.is_(None), Student.class_id.is_not(None),
    ).order_by(Student.student_code))
    source, source_question, knowledge_point = completed_required_source(db, student)
    request = {
        "class_id": str(student.class_id),
        "student_scope": "all_class",
        "student_ids": [],
        "recommendation_parameters": {
            "chapter_code": source_question.chapter_code,
            "chapter_codes": [source_question.chapter_code],
            "grade_codes": [source_question.grade],
            "new_knowledge_point_codes": [knowledge_point.code],
            "review_knowledge_point_codes": [],
            "new_count": 1,
            "review_count": 0,
            "avoid_recent_days": 0,
            "max_knowledge_point_ratio": 0.5,
        },
        "filter_config": {
            "grade_codes": [source_question.grade],
            "chapter_codes": [source_question.chapter_code],
            "required": {
                "knowledge_point_codes": [knowledge_point.code],
                "core_literacies": [],
                "difficulty_counts": {str(source_question.difficulty): 1},
            },
            "personalized": {
                "knowledge_point_codes": [knowledge_point.code],
                "primary_competency": (
                    knowledge_point.core_competencies[0]
                    if knowledge_point.core_competencies else "运算能力"
                ),
                "question_count": 3,
            },
            "history": {"assignment_id": str(source.id)},
        },
    }
    preview = client.post("/api/v1/assignments/preview", json=request)
    assert preview.status_code == 200, preview.text
    students = preview.json()["students"]
    source_student = next(item for item in students if item["student_id"] == str(student.id))
    assert [
        item["source_module"] for item in source_student["recommendation"]["items"]
    ] == ["required", "history"]
    history_item = source_student["recommendation"]["items"][1]
    assert history_item["question_id"] != str(source_question.id)
    assert history_item["difficulty"] == source_question.difficulty
    assert history_item["knowledge_point_code"] == knowledge_point.code
    assert history_item["evidence"]
    assert all(
        len(item["recommendation"]["items"]) == (
            2 if item["student_id"] == str(student.id) else 1
        )
        for item in students
    )

    invalid_competency = deepcopy(request)
    invalid_competency["filter_config"]["personalized"]["primary_competency"] = "不存在的能力"
    rejected = client.post("/api/v1/assignments/preview", json=invalid_competency)
    assert rejected.status_code == 422
    assert "主要能力不属于所选知识点" in rejected.text

    recent = client.get(
        "/api/v1/assignments/recent-completed",
        params={"limit": 5, "class_id": str(student.class_id)},
    )
    assert recent.status_code == 200, recent.text
    assert str(source.id) in {item["id"] for item in recent.json()}
    assert all(item["class_id"] == str(student.class_id) for item in recent.json())
    unscoped_recent = client.get(
        "/api/v1/assignments/recent-completed", params={"limit": 5},
    )
    assert unscoped_recent.status_code == 422

    create_request = {
        **request,
        "title": "必做与历史回顾组合测试",
        "idempotency_key": "history-combined-" + uuid4().hex,
    }
    created = client.post("/api/v1/assignments", json=create_request)
    assert created.status_code == 201, created.text
    source_paper = next(
        paper for paper in created.json()["papers"]
        if paper["student_id"] == str(student.id)
    )
    paper = client.get(f"/api/v1/paper-instances/{source_paper['id']}").json()
    assert [item["selection_audit"]["source_module"] for item in paper["items"]] == [
        "required", "history",
    ]


def test_history_scope_is_independent_from_current_grade_and_chapter(client, db):
    student = db.scalar(select(Student).where(
        Student.archived_at.is_(None), Student.class_id.is_not(None),
    ).order_by(Student.student_code))
    source, source_question, source_knowledge_point = completed_required_source(
        db, student, minimum_group_size=4,
    )
    suffix = uuid4().hex
    target_chapter_code = f"test.cross.scope.{suffix}"
    target_knowledge_point = KnowledgePoint(
        code=f"{target_chapter_code}.kp",
        name="Cross-scope current knowledge point",
        chapter_code=target_chapter_code,
        chapter_name="Cross-scope current chapter",
        section="Cross-scope section",
        sort_order=1,
        core_competencies=["Cross-scope competency"],
    )
    db.add(target_knowledge_point)
    db.flush()
    target_question = Question(
        original_qid=f"test.cross.scope.question.{suffix}",
        schema_version=source_question.schema_version,
        chapter_code=target_chapter_code,
        chapter_name="Cross-scope current chapter",
        grade="Cross-scope current grade",
        section="Cross-scope section",
        question_type=source_question.question_type,
        difficulty=2,
        main_knowledge_point_id=target_knowledge_point.id,
        question_text=source_question.question_text,
        options=deepcopy(source_question.options),
        correct_answer=deepcopy(source_question.correct_answer),
        reference_answer_raw=source_question.reference_answer_raw,
        answer_match_type=source_question.answer_match_type,
        answer_normalization_rule=deepcopy(source_question.answer_normalization_rule),
        solution=source_question.solution,
        wrong_analysis=source_question.wrong_analysis,
        estimated_score_rate=source_question.estimated_score_rate,
        core_literacy=source_question.core_literacy,
        is_required=True,
        has_image=source_question.has_image,
        has_equation=source_question.has_equation,
        assets=deepcopy(source_question.assets),
        source_file="cross-scope-history-test",
        source_question_number=1,
        source_review_status="reviewed",
        grading_readiness="machine_ready",
        import_status="parsed",
        import_issues=[],
        review_revision=1,
        review_history=[],
    )
    db.add(target_question)
    db.flush()
    request = {
        "class_id": str(student.class_id),
        "student_scope": "all_class",
        "student_ids": [],
        "recommendation_parameters": {
            "chapter_code": target_question.chapter_code,
            "chapter_codes": [target_question.chapter_code],
            "grade_codes": [target_question.grade],
            "new_knowledge_point_codes": [target_knowledge_point.code],
            "review_knowledge_point_codes": [],
            "new_count": 1,
            "review_count": 0,
            "avoid_recent_days": 0,
            "max_knowledge_point_ratio": 1,
        },
        "filter_config": {
            "grade_codes": [target_question.grade],
            "chapter_codes": [target_question.chapter_code],
            "required": {
                "knowledge_point_codes": [target_knowledge_point.code],
                "core_literacies": [],
                "difficulty_counts": {str(target_question.difficulty): 1},
            },
            "personalized": {
                "knowledge_point_codes": [target_knowledge_point.code],
                "primary_competency": target_knowledge_point.core_competencies[0],
                "question_count": 3,
            },
            "history": {"assignment_id": str(source.id)},
        },
        "title": "cross-scope history test",
        "idempotency_key": "cross-scope-history-" + uuid4().hex,
    }
    created = client.post("/api/v1/assignments", json=request)
    assert created.status_code == 201, created.text
    paper_summary = next(
        value for value in created.json()["papers"]
        if value["student_id"] == str(student.id)
    )
    paper = client.get(
        f"/api/v1/paper-instances/{paper_summary['id']}"
    ).json()
    required_item = next(
        item for item in paper["items"]
        if item["selection_audit"]["source_module"] == "required"
    )
    history_item = next(
        item for item in paper["items"]
        if item["selection_audit"]["source_module"] == "history"
    )
    assert required_item["knowledge_point_snapshot"]["id"] == target_knowledge_point.code
    assert history_item["knowledge_point_snapshot"]["id"] == source_knowledge_point.code
    assert history_item["question_snapshot"]["chapter_code"] == source_question.chapter_code
    assert history_item["question_id"] != str(source_question.id)

    alternatives = client.get(
        f"/api/v1/paper-instances/{paper['id']}/items/{history_item['id']}/alternatives"
    )
    assert alternatives.status_code == 200, alternatives.text
    choices = alternatives.json()["items"]
    assert choices
    assert all(
        item["knowledge_point_code"] == source_knowledge_point.code
        and item["difficulty"] == source_question.difficulty
        for item in choices
    )


def test_required_and_history_replacements_preserve_module_and_history_evidence(client, db):
    student = db.scalar(select(Student).where(
        Student.archived_at.is_(None), Student.class_id.is_not(None),
    ).order_by(Student.student_code))
    source, source_question, knowledge_point = completed_required_source(
        db, student, minimum_group_size=6, minimum_required_count=4,
    )
    competency = knowledge_point.core_competencies[0]
    request = {
        "class_id": str(student.class_id),
        "student_scope": "all_class",
        "student_ids": [],
        "recommendation_parameters": {
            "chapter_code": source_question.chapter_code,
            "chapter_codes": [source_question.chapter_code],
            "grade_codes": [source_question.grade],
            "new_knowledge_point_codes": [knowledge_point.code],
            "review_knowledge_point_codes": [],
            "new_count": 1,
            "review_count": 0,
            "avoid_recent_days": 0,
            "max_knowledge_point_ratio": 0.5,
        },
        "filter_config": {
            "grade_codes": [source_question.grade],
            "chapter_codes": [source_question.chapter_code],
            "required": {
                "knowledge_point_codes": [knowledge_point.code],
                "core_literacies": [],
                "difficulty_counts": {str(source_question.difficulty): 1},
            },
            "personalized": {
                "knowledge_point_codes": [knowledge_point.code],
                "primary_competency": competency,
                "question_count": 3,
            },
            "history": {"assignment_id": str(source.id)},
        },
        "title": "history replacement audit test",
        "idempotency_key": "history-replacement-" + uuid4().hex,
    }
    created = client.post("/api/v1/assignments", json=request)
    assert created.status_code == 201, created.text
    paper_summary = next(
        value for value in created.json()["papers"]
        if value["student_id"] == str(student.id)
    )
    paper = client.get(
        f"/api/v1/paper-instances/{paper_summary['id']}"
    ).json()
    history_item = next(
        item for item in paper["items"]
        if item["selection_audit"]["source_module"] == "history"
    )
    original_evidence = history_item["selection_audit"]["evidence"]
    history_alternatives = client.get(
        f"/api/v1/paper-instances/{paper['id']}/items/{history_item['id']}/alternatives"
    )
    assert history_alternatives.status_code == 200, history_alternatives.text
    history_choices = history_alternatives.json()["items"]
    assert history_choices
    assert str(source_question.id) not in {
        item["question_id"] for item in history_choices
    }
    assert all(
        item["knowledge_point_code"] == knowledge_point.code
        and item["difficulty"] == source_question.difficulty
        for item in history_choices
    )
    replaced_history = client.post(
        f"/api/v1/paper-instances/{paper['id']}/items/{history_item['id']}/replace",
        json={
            "replacement_question_id": history_choices[0]["question_id"],
            "expected_edit_revision": paper["edit_revision"],
        },
    )
    assert replaced_history.status_code == 200, replaced_history.text
    paper = replaced_history.json()
    history_item = next(item for item in paper["items"] if item["id"] == history_item["id"])
    assert history_item["selection_audit"]["source_module"] == "history"
    assert history_item["selection_audit"]["evidence"] == original_evidence
    assert "history_review" in history_item["selection_audit"]["reason_codes"]

    required_item = next(
        item for item in paper["items"]
        if item["selection_audit"]["source_module"] == "required"
    )
    required_alternatives = client.get(
        f"/api/v1/paper-instances/{paper['id']}/items/{required_item['id']}/alternatives"
    )
    assert required_alternatives.status_code == 200, required_alternatives.text
    required_choices = required_alternatives.json()["items"]
    assert required_choices
    required_choice_ids = [item["question_id"] for item in required_choices]
    assert all(db.get(Question, UUID(value)).is_required for value in required_choice_ids)
    replaced_required = client.post(
        f"/api/v1/paper-instances/{paper['id']}/items/{required_item['id']}/replace",
        json={
            "replacement_question_id": required_choices[0]["question_id"],
            "expected_edit_revision": paper["edit_revision"],
        },
    )
    assert replaced_required.status_code == 200, replaced_required.text
    required_item = next(
        item for item in replaced_required.json()["items"]
        if item["id"] == required_item["id"]
    )
    assert required_item["selection_audit"]["source_module"] == "required"
    assert "required_question" in required_item["selection_audit"]["reason_codes"]


def test_history_source_must_be_one_of_latest_five_eligible_assignments(db):
    student = db.scalar(select(Student).where(
        Student.archived_at.is_(None), Student.class_id.is_not(None),
    ).order_by(Student.student_code))
    base = datetime.now(timezone.utc) + timedelta(days=30)
    sources = []
    for index in range(6):
        assignment, _, _ = completed_required_source(db, student)
        assignment.created_at = base + timedelta(minutes=index)
        sources.append(assignment)

    ineligible, _, _ = completed_required_source(
        db, student, source_module="legacy_recommendation",
    )
    ineligible.created_at = base + timedelta(minutes=7)
    db.flush()

    recent_ids = recent_completed_assignment_ids(
        db, class_id=student.class_id, limit=5,
    )
    assert recent_ids == [assignment.id for assignment in reversed(sources[1:])]
    assert ineligible.id not in recent_ids
    with pytest.raises(ValueError, match="最近五次"):
        history_wrong_answers(
            db,
            source_assignment_id=sources[0].id,
            class_id=student.class_id,
            student_ids=[student.id],
        )


def test_required_candidates_exclude_questions_not_marked_required(db):
    question = db.scalar(select(Question).where(
        Question.archived_at.is_(None),
        Question.grading_readiness == "machine_ready",
        Question.source_review_status == "reviewed",
        Question.is_required.is_(True),
    ))
    assert question is not None

    question.is_required = False
    db.flush()

    candidates = required_question_candidates(
        db,
        chapter_codes=[question.chapter_code],
        grades=[question.grade],
    )
    assert question.id not in {candidate.question_id for candidate in candidates}


def test_empty_required_scope_does_not_fall_back_to_legacy_scope(client, db):
    student = db.scalar(select(Student).where(
        Student.archived_at.is_(None), Student.class_id.is_not(None),
    ).order_by(Student.student_code))
    row = db.execute(
        select(Question, KnowledgePoint)
        .join(KnowledgePoint, KnowledgePoint.id == Question.main_knowledge_point_id)
        .where(
            Question.archived_at.is_(None),
            Question.is_required.is_(True),
            Question.grading_readiness == "machine_ready",
            Question.source_review_status == "reviewed",
        )
        .order_by(Question.original_qid)
    ).first()
    assert student is not None and row is not None
    question, knowledge_point = row
    request = {
        "class_id": str(student.class_id),
        "student_scope": "all_class",
        "student_ids": [],
        "recommendation_parameters": {
            "chapter_code": question.chapter_code,
            "chapter_codes": [question.chapter_code],
            "grade_codes": [question.grade],
            "new_knowledge_point_codes": ["legacy.scope.must.not.filter"],
            "review_knowledge_point_codes": [],
            "new_count": 1,
            "review_count": 0,
            "max_knowledge_point_ratio": 0.5,
        },
        "filter_config": {
            "grade_codes": [question.grade],
            "chapter_codes": [question.chapter_code],
            "required": {
                "knowledge_point_codes": [],
                "core_literacies": [],
                "difficulty_counts": {str(question.difficulty): 1},
            },
            "personalized": {
                "knowledge_point_codes": [knowledge_point.code],
                "primary_competency": knowledge_point.core_competencies[0],
                "question_count": 3,
            },
            "history": {"assignment_id": None},
        },
    }
    preview = client.post("/api/v1/assignments/preview", json=request)
    assert preview.status_code == 200, preview.text
    recommendation = preview.json()["students"][0]["recommendation"]
    assert recommendation["selected_count"] == 1
    assert "legacy.scope.must.not.filter" not in recommendation["parameters"]["new_knowledge_point_codes"]
    assert recommendation["items"][0]["knowledge_point_code"] in recommendation["parameters"]["new_knowledge_point_codes"]
    assert recommendation["items"][0]["knowledge_point_code"] != "legacy.scope.must.not.filter"


def test_knowledge_point_catalog_reports_required_inventory(client, db):
    row = db.execute(
        select(Question.main_knowledge_point_id, Question.chapter_code)
        .where(
            Question.archived_at.is_(None),
            Question.grading_readiness == "machine_ready",
            Question.source_review_status == "reviewed",
            Question.is_required.is_(False),
        )
        .order_by(Question.original_qid)
    ).first()
    assert row is not None
    knowledge_point_id, chapter_code = row
    expected_required = int(db.scalar(
        select(func.count(Question.id)).where(
            Question.main_knowledge_point_id == knowledge_point_id,
            Question.archived_at.is_(None),
            Question.grading_readiness == "machine_ready",
            Question.source_review_status == "reviewed",
            Question.is_required.is_(True),
        )
    ) or 0)
    expected_all = int(db.scalar(
        select(func.count(Question.id)).where(
            Question.main_knowledge_point_id == knowledge_point_id,
            Question.archived_at.is_(None),
            Question.grading_readiness == "machine_ready",
            Question.source_review_status == "reviewed",
        )
    ) or 0)
    response = client.get("/api/v1/knowledge-points", params={"chapter_code": chapter_code})
    assert response.status_code == 200, response.text
    item = next(value for value in response.json() if value["id"] == str(knowledge_point_id))
    assert item["available_question_count"] == expected_all
    assert item["required_available_question_count"] == expected_required
    assert expected_required < expected_all


def test_selected_student_scope_accepts_more_than_one_hundred_ids(client, db):
    recommendation = request_body(db, key="unused")["recommendation_parameters"]
    response = client.post("/api/v1/assignments/preview", json={
        "student_scope": "selected",
        "student_ids": [str(uuid4()) for _ in range(101)],
        "recommendation_parameters": recommendation,
    })
    assert response.status_code == 404, response.text


def test_edit_publish_lookup_and_revise(client, db):
    body = request_body(db, key="test-" + uuid4().hex)
    assignment = client.post("/api/v1/assignments", json=body).json()
    paper_id = assignment["papers"][0]["id"]
    paper = client.get(f"/api/v1/paper-instances/{paper_id}").json()
    first = paper["items"][0]
    alternatives = client.get(
        f"/api/v1/paper-instances/{paper_id}/items/{first['id']}/alternatives"
    )
    assert alternatives.status_code == 200, alternatives.text
    assert alternatives.json()["items"]
    replacement_id = alternatives.json()["items"][0]["question_id"]
    replaced = client.post(
        f"/api/v1/paper-instances/{paper_id}/items/{first['id']}/replace",
        json={"replacement_question_id": replacement_id,
              "expected_edit_revision": paper["edit_revision"]},
    )
    assert replaced.status_code == 200, replaced.text
    paper = replaced.json()
    assert paper["items"][0]["question_id"] == replacement_id
    assert paper["items"][0]["selection_audit"]["source"] == "teacher_replacement"

    stale = client.put(
        f"/api/v1/paper-instances/{paper_id}/order",
        json={"item_ids": [item["id"] for item in paper["items"]], "expected_edit_revision": 1},
    )
    assert stale.status_code == 409
    reversed_ids = [item["id"] for item in reversed(paper["items"])]
    reordered = client.put(
        f"/api/v1/paper-instances/{paper_id}/order",
        json={"item_ids": reversed_ids, "expected_edit_revision": paper["edit_revision"]},
    )
    assert reordered.status_code == 200, reordered.text
    paper = reordered.json()
    assert [item["id"] for item in paper["items"]] == reversed_ids

    removed = client.request(
        "DELETE", f"/api/v1/paper-instances/{paper_id}/items/{paper['items'][-1]['id']}",
        json={"expected_edit_revision": paper["edit_revision"]},
    )
    assert removed.status_code == 200, removed.text
    paper = removed.json()
    assert [item["order_no"] for item in paper["items"]] == [1, 2, 3]
    assert client.post(
        f"/api/v1/paper-instances/{paper_id}/publish",
        json={"expected_edit_revision": paper["edit_revision"]},
    ).status_code == 409
    published = client.post(
        f"/api/v1/paper-instances/{paper_id}/publish",
        json={"expected_edit_revision": paper["edit_revision"], "acknowledge_shortfall": True,
              "acknowledgement_reason": "教师确认三题足够"},
    )
    assert published.status_code == 200, published.text
    published_body = published.json()
    assert published_body["status"] == "published"
    code = published_body["instance_code"]
    assert code.startswith("RAT-")
    assert client.request(
        "DELETE", f"/api/v1/paper-instances/{paper_id}/items/{published_body['items'][0]['id']}",
        json={"expected_edit_revision": published_body["edit_revision"]},
    ).status_code == 409

    found = client.get(f"/api/v1/paper-instances/by-code/{code.lower()}")
    assert found.status_code == 200 and found.json()["id"] == paper_id
    student_view = client.get(f"/api/v1/paper-instances/{paper_id}", params={"view": "student"})
    assert student_view.status_code == 200
    assert "answer_snapshot" not in student_view.json()["items"][0]
    assert "solution" not in student_view.json()["items"][0]["question_snapshot"]
    assert "wrong_analysis" not in student_view.json()["items"][0]["question_snapshot"]
    revised = client.post(f"/api/v1/paper-instances/{paper_id}/revise")
    assert revised.status_code == 201, revised.text
    assert revised.json()["version_no"] == 2
    assert revised.json()["supersedes_instance_id"] == paper_id
    assert revised.json()["instance_code"] is None
    assert [item["question_snapshot"] for item in revised.json()["items"]] == [
        item["question_snapshot"] for item in published_body["items"]
    ]

    revised_body = revised.json()
    published_revision = client.post(
        f'/api/v1/paper-instances/{revised_body["id"]}/publish',
        json={
            'expected_edit_revision': revised_body['edit_revision'],
            'acknowledge_shortfall': True,
            'acknowledgement_reason': '教师确认修订版三题足够',
        },
    )
    assert published_revision.status_code == 200, published_revision.text
    new_body = published_revision.json()
    assert new_body['is_active'] is True

    old_body = client.get(f'/api/v1/paper-instances/{paper_id}').json()
    assert old_body['is_active'] is False
    assert old_body['invalidation_type'] == 'superseded'
    assert old_body['superseded_by_instance_id'] == new_body['id']
    old_lookup = client.get(f'/api/v1/paper-instances/by-code/{code}').json()
    assert old_lookup['id'] == paper_id and old_lookup['is_active'] is False
    assert old_lookup['superseded_by_instance_id'] == new_body['id']
    assert client.post(
        f'/api/v1/paper-instances/{paper_id}/republish',
        json={'expected_edit_revision': old_body['edit_revision']},
    ).status_code == 409

    withdrawn = client.post(
        f'/api/v1/paper-instances/{new_body["id"]}/withdraw',
        json={'expected_edit_revision': new_body['edit_revision'], 'reason': '教师发现范围错误'},
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()['is_active'] is False
    assert withdrawn.json()['invalidation_type'] == 'withdrawn'
    assert client.post(
        f'/api/v1/paper-instances/{new_body["id"]}/withdraw',
        json={
            'expected_edit_revision': withdrawn.json()['edit_revision'],
            'reason': '重复撤回',
        },
    ).status_code == 409
    republished = client.post(
        f'/api/v1/paper-instances/{new_body["id"]}/republish',
        json={'expected_edit_revision': withdrawn.json()['edit_revision']},
    )
    assert republished.status_code == 200, republished.text
    republished_body = republished.json()
    assert republished_body['is_active'] is True
    assert republished_body['instance_code'] == new_body['instance_code']
    assert republished_body['invalidated_at'] is None
    assert republished_body['invalidation_type'] is None
    assert republished_body['edit_revision'] == withdrawn.json()['edit_revision'] + 1
    assert republished_body['edit_history'][-1]['type'] == 'republish'
    assert client.post(
        f'/api/v1/paper-instances/{new_body["id"]}/republish',
        json={'expected_edit_revision': republished_body['edit_revision']},
    ).status_code == 409


def test_draft_can_be_deleted_but_published_paper_cannot(client, db):
    body = request_body(db, key='test-' + uuid4().hex)
    assignment = client.post('/api/v1/assignments', json=body).json()
    draft = assignment['papers'][0]
    before = counts(db)
    deleted = client.request(
        'DELETE', f'/api/v1/paper-instances/{draft["id"]}',
        json={'expected_edit_revision': draft['edit_revision']},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()['deleted_id'] == draft['id']
    assert client.get(f'/api/v1/paper-instances/{draft["id"]}').status_code == 404
    after = counts(db)
    assert after[1] == before[1] - 1 and after[2] == before[2] - 4

    published = assignment['papers'][1]
    response = client.post(
        f'/api/v1/paper-instances/{published["id"]}/publish',
        json={'expected_edit_revision': published['edit_revision']},
    )
    assert response.status_code == 200, response.text
    published_body = response.json()
    assert client.request(
        'DELETE', f'/api/v1/paper-instances/{published["id"]}',
        json={'expected_edit_revision': published_body['edit_revision']},
    ).status_code == 409
