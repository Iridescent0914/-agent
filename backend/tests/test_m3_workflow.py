from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.database import SessionLocal, get_db
from app.main import app
from app.models import Assignment, PaperInstance, PaperInstanceItem, Student


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
            "review_exam_point_codes": [
                "math.g6.rational.exam.local.a61b42d3fd",
                "math.g6.rational.exam.local.dae9bb3283",
            ],
            "review_count": 4,
            "avoid_recent_days": 0,
            "max_exam_point_ratio": 0.5,
        },
    }


def counts(db):
    return tuple(int(db.scalar(select(func.count()).select_from(model)) or 0)
                 for model in (Assignment, PaperInstance, PaperInstanceItem))


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
    revised = client.post(f"/api/v1/paper-instances/{paper_id}/revise")
    assert revised.status_code == 201, revised.text
    assert revised.json()["version_no"] == 2
    assert revised.json()["supersedes_instance_id"] == paper_id
    assert revised.json()["instance_code"] is None
    assert [item["question_snapshot"] for item in revised.json()["items"]] == [
        item["question_snapshot"] for item in published_body["items"]
    ]
