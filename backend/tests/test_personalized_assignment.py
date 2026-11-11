from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.database import SessionLocal, get_db
from app.main import app
from app.models import Assignment, KnowledgePoint, PaperInstance, PaperInstanceItem, Question, Student
from app.services.literacy_catalog import parse_literacy_names


PERSONALIZED_KP = "math.g6.rational.kp.08_multiply_divide"
LITERACY = "运算能力"


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


def _scope(db):
    student = db.scalar(select(Student).where(
        Student.student_code == "DEMO-S001",
        Student.archived_at.is_(None),
    ))
    personalized_kp = db.scalar(select(KnowledgePoint).where(
        KnowledgePoint.code == PERSONALIZED_KP,
    ))
    required_row = db.execute(
        select(Question, KnowledgePoint)
        .join(KnowledgePoint, KnowledgePoint.id == Question.main_knowledge_point_id)
        .where(
            Question.archived_at.is_(None),
            Question.is_required.is_(True),
            Question.grading_readiness == "machine_ready",
            Question.source_review_status == "reviewed",
            Question.chapter_code == personalized_kp.chapter_code,
            Question.grade == "六年级上册",
            KnowledgePoint.code != PERSONALIZED_KP,
        )
        .order_by(Question.original_qid)
    ).first()
    assert student is not None and personalized_kp is not None and required_row is not None
    return student, personalized_kp, required_row


def _request(db, *, key: str, create: bool = False):
    student, personalized_kp, required_row = _scope(db)
    required_question, required_kp = required_row
    body = {
        "class_id": str(student.class_id),
        "student_scope": "selected",
        "student_ids": [str(student.id)],
        "recommendation_parameters": {
            "chapter_code": required_question.chapter_code,
            "chapter_codes": [required_question.chapter_code],
            "grade_codes": [required_question.grade],
            "new_knowledge_point_codes": [required_kp.code],
            "review_knowledge_point_codes": [],
            "new_count": 1,
            "review_count": 0,
            "avoid_recent_days": 0,
            "max_knowledge_point_ratio": 1,
        },
        "filter_config": {
            "grade_codes": [required_question.grade],
            "chapter_codes": [required_question.chapter_code],
            "required": {
                "knowledge_point_codes": [required_kp.code],
                "core_literacies": [],
                "difficulty_counts": {str(required_question.difficulty): 1},
            },
            "personalized": {
                "knowledge_point_codes": [personalized_kp.code],
                "primary_competency": LITERACY,
                "question_count": 3,
            },
            "history": {"assignment_id": None},
        },
    }
    if create:
        body.update({
            "title": "个性化正式创建测试",
            "idempotency_key": key,
        })
    return body


def _business_counts(db):
    return tuple(
        int(db.scalar(select(func.count()).select_from(model)) or 0)
        for model in (Assignment, PaperInstance, PaperInstanceItem)
    )


def test_unified_preview_returns_three_personalized_items_and_integer_summary(
    client, db, monkeypatch,
):
    monkeypatch.setattr(
        "app.services.personalized_preview.effective_literacy_scores",
        lambda session, student_id, *, as_of: {LITERACY: 64.4386},
    )
    before = _business_counts(db)
    response = client.post(
        "/api/v1/assignments/preview",
        json=_request(db, key="unused"),
    )

    assert response.status_code == 200, response.text
    assert _business_counts(db) == before
    student = response.json()["students"][0]
    personalized = student["personalized"]
    assert personalized["literacy_score"] == 64
    assert personalized["base_star"] == 2
    assert personalized["target_stars"] == [2, 3, 4]
    assert personalized["selected_count"] == 3
    assert personalized["shortfall"] == 0
    items = student["recommendation"]["items"]
    personalized_items = [
        item for item in items if item["source_module"] == "personalized"
    ]
    assert len(personalized_items) == 3
    assert [item["difficulty"] for item in personalized_items] == [2, 3, 4]
    assert len({item["question_id"] for item in items}) == len(items)
    assert student["recommendation"]["personalized_shortfall"] == 0
    assert response.json()["personalized_reused_question_count"] == 0
    assert response.json()["personalized_max_class_usage_count"] == 1


def test_create_recomputes_score_and_freezes_personalized_audit(
    client, db, monkeypatch,
):
    calls = 0

    def changing_scores(session, student_id, *, as_of):
        nonlocal calls
        calls += 1
        return {LITERACY: 55.0 if calls == 1 else 64.0}

    monkeypatch.setattr(
        "app.services.personalized_preview.effective_literacy_scores",
        changing_scores,
    )
    preview = client.post(
        "/api/v1/assignments/preview",
        json=_request(db, key="unused"),
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["students"][0]["personalized"]["literacy_score"] == 55

    created = client.post(
        "/api/v1/assignments",
        json=_request(db, key="personalized-create-" + uuid4().hex, create=True),
    )
    assert created.status_code == 201, created.text
    assert calls == 2
    detail = created.json()
    policy = detail["generation_config"]["personalized_score_policy"]
    assert policy == {
        "score_source": "computed",
        "score_type": "rounded_integer",
        "rule_version": "literacy-dynamic-v2",
        "recomputed_at_creation": True,
    }
    paper = client.get(
        f"/api/v1/paper-instances/{detail['papers'][0]['id']}"
    ).json()
    assert paper["recommendation_audit"]["personalized"]["literacy_score"] == 64
    personalized_items = [
        item for item in paper["items"]
        if item["selection_audit"]["source_module"] == "personalized"
    ]
    assert len(personalized_items) == 3
    assert [item["question_snapshot"]["difficulty"] for item in personalized_items] == [2, 3, 4]
    for slot_no, item in enumerate(personalized_items, start=1):
        audit = item["selection_audit"]["personalized_audit"]
        assert audit["slot_no"] == slot_no
        assert audit["literacy_score"] == 64
        assert audit["score_source"] == "computed"
        assert audit["base_star"] == 2
        assert audit["target_stars"] == [2, 3, 4]
        assert audit["rule_version"] == "literacy-dynamic-v2"
        assert audit["class_usage_count"] == 1
        assert datetime.fromisoformat(
            audit["generated_at"].replace("Z", "+00:00")
        ) == datetime.fromisoformat(detail["generation_config"]["as_of"])
        assert "internal_score" not in audit

    replaceable = personalized_items[0]
    delete_rejected = client.request(
        "DELETE",
        f"/api/v1/paper-instances/{paper['id']}/items/{replaceable['id']}",
        json={"expected_edit_revision": paper["edit_revision"]},
    )
    assert delete_rejected.status_code == 422, delete_rejected.text
    assert "固定为 3 道" in delete_rejected.text
    unchanged = client.get(f"/api/v1/paper-instances/{paper['id']}").json()
    assert unchanged["edit_revision"] == paper["edit_revision"]
    assert len([
        item for item in unchanged["items"]
        if item["selection_audit"]["source_module"] == "personalized"
    ]) == 3

    alternatives = client.get(
        f"/api/v1/paper-instances/{paper['id']}/items/{replaceable['id']}/alternatives"
    )
    assert alternatives.status_code == 200, alternatives.text
    choices = alternatives.json()["items"]
    diagnostic = alternatives.json()["diagnostic"]
    assert choices
    assert diagnostic["source_module"] == "personalized"
    assert diagnostic["literacy_name"] == LITERACY
    assert diagnostic["target_difficulty"] == 2
    assert diagnostic["available_candidate_count"] >= len(choices)
    assert "同主要素养、同星级" in diagnostic["message"]
    assert all(
        item["knowledge_point_code"] == PERSONALIZED_KP
        and item["difficulty"] == replaceable["question_snapshot"]["difficulty"]
        and item["target_difficulty"] == replaceable["selection_audit"]["personalized_audit"]["target_star"]
        and item["strategy"] == "personalized"
        and item["source_module"] == "personalized"
        for item in choices
    )
    choice_ids = [item["question_id"] for item in choices]
    choice_rows = db.execute(
        select(Question, KnowledgePoint)
        .join(KnowledgePoint, KnowledgePoint.id == Question.main_knowledge_point_id)
        .where(Question.id.in_(choice_ids))
    ).all()
    assert len(choice_rows) == len(choice_ids)
    assert all(
        LITERACY in (knowledge_point.core_competencies or [])
        and LITERACY in parse_literacy_names(question.core_literacy)
        for question, knowledge_point in choice_rows
    )
    assert all("证据不足" not in item["reason_text"] for item in choices)
    assert all(
        item["reason_codes"] == [
            "personalized_literacy_score",
            "teacher_knowledge_scope",
            "exact_target_difficulty",
            "teacher_replacement_candidate",
        ]
        and "当前整数分64" in item["reason_text"]
        and "保持2星" in item["reason_text"]
        and item["evidence"] == []
        for item in choices
    )
    replaced = client.post(
        f"/api/v1/paper-instances/{paper['id']}/items/{replaceable['id']}/replace",
        json={
            "replacement_question_id": choices[0]["question_id"],
            "expected_edit_revision": paper["edit_revision"],
        },
    )
    assert replaced.status_code == 200, replaced.text
    replaced_item = next(
        item for item in replaced.json()["items"] if item["id"] == replaceable["id"]
    )
    assert replaced_item["selection_audit"]["source_module"] == "personalized"
    assert replaced_item["selection_audit"]["personalized_audit"] == (
        replaceable["selection_audit"]["personalized_audit"]
    )
    first_audit = replaced_item["selection_audit"]
    assert first_audit["reason_text"] == choices[0]["reason_text"]
    assert first_audit["initial_selection"] == {
        "question_id": replaceable["question_id"],
        "original_qid": replaceable["question_snapshot"]["original_qid"],
        "reason_text": replaceable["selection_audit"]["reason_text"],
        "reason_codes": replaceable["selection_audit"]["reason_codes"],
        "selected_at": replaceable["selection_audit"]["selected_at"],
        "class_usage_count": replaceable["selection_audit"][
            "personalized_audit"
        ]["class_usage_count"],
    }
    assert len(first_audit["replacement_history"]) == 1
    assert first_audit["replacement_history"][0]["replacement_question_id"] == (
        choices[0]["question_id"]
    )
    assert first_audit["replacement_history"][0]["reason_text"] == (
        choices[0]["reason_text"]
    )
    assert first_audit["replacement_history"][0][
        "class_usage_count_at_replacement"
    ] >= 1

    second_alternatives = client.get(
        f"/api/v1/paper-instances/{paper['id']}/items/{replaceable['id']}/alternatives"
    )
    assert second_alternatives.status_code == 200, second_alternatives.text
    second_choice = second_alternatives.json()["items"][0]
    replaced = client.post(
        f"/api/v1/paper-instances/{paper['id']}/items/{replaceable['id']}/replace",
        json={
            "replacement_question_id": second_choice["question_id"],
            "expected_edit_revision": replaced.json()["edit_revision"],
        },
    )
    assert replaced.status_code == 200, replaced.text
    replaced_item = next(
        item for item in replaced.json()["items"] if item["id"] == replaceable["id"]
    )
    second_audit = replaced_item["selection_audit"]
    assert second_audit["reason_text"] == second_choice["reason_text"]
    assert second_audit["initial_selection"] == first_audit["initial_selection"]
    assert second_audit["personalized_audit"] == first_audit["personalized_audit"]
    assert len(second_audit["replacement_history"]) == 2
    assert second_audit["replacement_history"][0] == first_audit[
        "replacement_history"
    ][0]
    assert second_audit["replacement_history"][1]["sequence_no"] == 2
    assert second_audit["replacement_history"][1]["replacement_question_id"] == (
        second_choice["question_id"]
    )
    assert second_audit["replacement_history"][1]["reason_text"] == (
        second_choice["reason_text"]
    )

    # Even a malformed/legacy draft cannot bypass the three-item invariant
    # by acknowledging a generic total-count shortfall at publish time.
    persisted = db.get(PaperInstanceItem, UUID(personalized_items[-1]["id"]))
    assert persisted is not None
    db.delete(persisted)
    db.flush()
    publish_rejected = client.post(
        f"/api/v1/paper-instances/{paper['id']}/publish",
        json={
            "expected_edit_revision": replaced.json()["edit_revision"],
            "acknowledge_shortfall": True,
            "acknowledgement_reason": "教师确认当前题量",
        },
    )
    assert publish_rejected.status_code == 422, publish_rejected.text
    assert "必须保留完整 3 道题" in publish_rejected.text


def test_personalized_shortfall_previews_but_blocks_create_without_writes(
    client, db, monkeypatch,
):
    student, _, required_row = _scope(db)
    required_question, required_kp = required_row
    short_kp = db.scalar(select(KnowledgePoint).where(
        KnowledgePoint.code == "math.g6.rational.kp.07_add_subtract",
    ))
    monkeypatch.setattr(
        "app.services.personalized_preview.effective_literacy_scores",
        lambda session, student_id, *, as_of: {LITERACY: 64.0},
    )
    body = _request(db, key="unused")
    body["filter_config"]["personalized"]["knowledge_point_codes"] = [short_kp.code]
    preview = client.post("/api/v1/assignments/preview", json=body)
    assert preview.status_code == 200, preview.text
    result = preview.json()["students"][0]["personalized"]
    assert result["target_stars"] == [2, 3, 4]
    assert result["shortfall"] > 0
    assert any(
        item["difficulty"] == 4 and item["shortfall"] == 1
        for item in result["diagnostics"]
    )

    body.update({
        "title": "应被缺题阻断的个性化作业",
        "idempotency_key": "personalized-shortfall-" + uuid4().hex,
    })
    before = _business_counts(db)
    rejected = client.post("/api/v1/assignments", json=body)
    assert rejected.status_code == 422, rejected.text
    assert "个性化题库存不足" in rejected.text
    assert _business_counts(db) == before


def test_preview_excludes_questions_from_recent_assignments(client, db, monkeypatch):
    monkeypatch.setattr(
        "app.services.personalized_preview.effective_literacy_scores",
        lambda session, student_id, *, as_of: {LITERACY: 64.0},
    )
    created = client.post(
        "/api/v1/assignments",
        json=_request(
            db,
            key="personalized-recent-source-" + uuid4().hex,
            create=True,
        ),
    )
    assert created.status_code == 201, created.text
    source_paper = client.get(
        f"/api/v1/paper-instances/{created.json()['papers'][0]['id']}"
    ).json()
    source_ids = {
        item["question_id"]
        for item in source_paper["items"]
        if item["selection_audit"]["source_module"] == "personalized"
    }
    assert len(source_ids) == 3

    request = _request(db, key="unused")
    request["recommendation_parameters"]["avoid_recent_days"] = 3650
    preview = client.post("/api/v1/assignments/preview", json=request)
    assert preview.status_code == 200, preview.text
    next_ids = {
        item["question_id"]
        for item in preview.json()["students"][0]["recommendation"]["items"]
        if item["source_module"] == "personalized"
    }
    assert source_ids.isdisjoint(next_ids)
