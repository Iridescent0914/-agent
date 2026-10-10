from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal, get_db
from app.main import app
from app.models import Student


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


def _overview(client: TestClient) -> dict:
    response = client.get(
        "/api/v1/analytics/class-overview",
        params={
            "class_name": "六年级 Demo 班",
            "chapter_code": "math.g6.rational",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _published_paper(client: TestClient, db) -> tuple[Student, dict]:
    student = db.scalar(select(Student).where(Student.student_code == "S002"))
    response = client.post(
        "/api/v1/assignments",
        json={
            "idempotency_key": "m5-assignment-" + uuid4().hex,
            "title": "M5 学情聚合测试",
            "student_ids": [str(student.id)],
            "recommendation_parameters": {
                "review_exam_point_codes": [
                    "math.g6.rational.exam.local.a61b42d3fd",
                    "math.g6.rational.exam.local.dae9bb3283",
                ],
                "review_count": 2,
                "avoid_recent_days": 0,
                "max_exam_point_ratio": 1,
            },
        },
    )
    assert response.status_code == 201, response.text
    paper = response.json()["papers"][0]
    published = client.post(
        f"/api/v1/paper-instances/{paper['id']}/publish",
        json={"expected_edit_revision": paper["edit_revision"]},
    )
    assert published.status_code == 200, published.text
    return student, published.json()


def test_class_overview_contract_and_drilldown(client):
    classes = client.get("/api/v1/analytics/classes")
    assert classes.status_code == 200
    demo = next(item for item in classes.json() if item["class_name"] == "六年级 Demo 班")
    assert demo["student_count"] == 5

    body = _overview(client)
    assert body["summary"]["student_count"] == 5
    assert body["summary"]["confirmed_attempt_count"] == sum(
        item["confirmed_attempt_count"] for item in body["exam_points"]
    )
    assert body["summary"]["covered_exam_point_count"] + body["summary"][
        "uncovered_exam_point_count"
    ] == len(body["exam_points"])
    for item in body["exam_points"]:
        assert item["correct_attempt_count"] + item["incorrect_attempt_count"] == item[
            "confirmed_attempt_count"
        ]
        assert item["evidence_student_count"] + item["unassessed_student_count"] == 5
        assert (item["accuracy"] is None) == (item["confirmed_attempt_count"] == 0)
        assert (item["average_mastery"] is None) == (item["evidence_student_count"] == 0)

    target = body["exam_points"][0]
    drilldown = client.get(
        f"/api/v1/analytics/classes/exam-points/{target['exam_point_code']}/students",
        params={
            "class_name": "六年级 Demo 班",
            "chapter_code": "math.g6.rational",
        },
    )
    assert drilldown.status_code == 200, drilldown.text
    assert len(drilldown.json()["items"]) == 5
    assert all(
        (item["accuracy"] is None) == (item["attempt_count"] == 0)
        for item in drilldown.json()["items"]
    )


def test_unassessed_student_does_not_lower_class_mastery(client, db):
    before = _overview(client)
    db.add(Student(
        student_code="M5EMPTY-" + uuid4().hex[:8],
        display_name="M5 未评估学生",
        class_name="六年级 Demo 班",
        is_demo=True,
    ))
    db.flush()
    after = _overview(client)
    assert after["summary"]["student_count"] == before["summary"]["student_count"] + 1
    before_rows = {item["exam_point_code"]: item for item in before["exam_points"]}
    for row in after["exam_points"]:
        previous = before_rows[row["exam_point_code"]]
        assert row["average_mastery"] == previous["average_mastery"]
        assert row["evidence_student_count"] == previous["evidence_student_count"]
        assert row["unassessed_student_count"] == previous["unassessed_student_count"] + 1


def test_pending_review_leaves_class_denominator_and_updates_assignment(client, db):
    student, paper = _published_paper(client, db)
    assignment_before = client.get(f"/api/v1/analytics/assignments/{paper['assignment_id']}")
    assert assignment_before.status_code == 200, assignment_before.text
    assert assignment_before.json()["summary"]["unsubmitted_count"] == 1

    submitted = client.post(
        f"/api/v1/paper-instances/{paper['id']}/submissions/manual",
        json={
            "idempotency_key": "m5-submission-" + uuid4().hex,
            "student_id": str(student.id),
            "answers": [
                {
                    "paper_instance_item_id": item["id"],
                    "order_no": item["order_no"],
                    "response_status": "answered",
                    "answer": item["answer_snapshot"],
                }
                for item in paper["items"]
            ],
        },
    )
    assert submitted.status_code == 201, submitted.text
    after_submit = _overview(client)
    attempt = submitted.json()["items"][0]["attempt"]
    reopened = client.patch(
        f"/api/v1/attempts/{attempt['id']}/review",
        json={
            "action": "reopen",
            "expected_revision_no": attempt["revision_no"],
            "is_correct": None,
            "reason": "M5 聚合测试撤销确认",
        },
    )
    assert reopened.status_code == 200, reopened.text
    after_reopen = _overview(client)
    assert after_reopen["summary"]["pending_review_count"] == (
        after_submit["summary"]["pending_review_count"] + 1
    )
    assert after_reopen["summary"]["confirmed_attempt_count"] == (
        after_submit["summary"]["confirmed_attempt_count"] - 1
    )

    assignment_after = client.get(f"/api/v1/analytics/assignments/{paper['assignment_id']}")
    assert assignment_after.status_code == 200, assignment_after.text
    summary = assignment_after.json()["summary"]
    assert summary["submitted_count"] == 1
    assert summary["needs_review_count"] == 1
    assert summary["completed_count"] == 0
    assert summary["submission_rate"] == 1
    assert summary["completion_rate"] == 0


def test_superseded_version_is_not_a_second_assignment_target(client, db):
    _, paper = _published_paper(client, db)
    revised = client.post(f"/api/v1/paper-instances/{paper['id']}/revise")
    assert revised.status_code == 201, revised.text
    version_two = revised.json()
    published = client.post(
        f"/api/v1/paper-instances/{version_two['id']}/publish",
        json={"expected_edit_revision": version_two["edit_revision"]},
    )
    assert published.status_code == 200, published.text
    analytics = client.get(f"/api/v1/analytics/assignments/{paper['assignment_id']}")
    assert analytics.status_code == 200, analytics.text
    body = analytics.json()
    assert body["summary"]["active_published_count"] == 1
    assert body["summary"]["historical_invalidated_count"] == 1
    assert len(body["students"]) == 1
    assert body["students"][0]["version_no"] == 2


def test_assignment_progress_is_filtered_by_class(client, db):
    _, demo_paper = _published_paper(client, db)
    class_response = client.post(
        "/api/v1/classes",
        json={"name": "其他测试班", "grade": "六年级", "school_year": "2026-2027"},
    )
    assert class_response.status_code == 201, class_response.text
    other_class = class_response.json()
    other_student = Student(
        student_code="OTHER-" + uuid4().hex[:8],
        display_name="其他班学生",
        class_id=UUID(other_class["id"]),
        class_name="其他测试班",
        is_demo=True,
    )
    db.add(other_student)
    db.flush()
    created = client.post(
        "/api/v1/assignments",
        json={
            "idempotency_key": "class-filter-" + uuid4().hex,
            "title": "其他班过滤测试",
            "class_id": other_class["id"],
            "student_ids": [str(other_student.id)],
            "recommendation_parameters": {
                "review_exam_point_codes": [
                    "math.g6.rational.exam.local.a61b42d3fd",
                ],
                "review_count": 1,
                "avoid_recent_days": 0,
                "max_exam_point_ratio": 1,
            },
        },
    )
    assert created.status_code == 201, created.text
    assignment = created.json()
    paper = assignment["papers"][0]
    published = client.post(
        f"/api/v1/paper-instances/{paper['id']}/publish",
        json={"expected_edit_revision": paper["edit_revision"]},
    )
    assert published.status_code == 200, published.text

    demo_list = client.get(
        "/api/v1/assignments",
        params={"page_size": 100, "class_name": "六年级 Demo 班"},
    )
    demo_ids = {item["id"] for item in demo_list.json()["items"]}
    assert demo_paper["assignment_id"] in demo_ids
    assert assignment["id"] not in demo_ids

    demo_progress = client.get(
        f"/api/v1/analytics/assignments/{demo_paper['assignment_id']}",
        params={"class_name": "六年级 Demo 班"},
    )
    assert demo_progress.status_code == 200, demo_progress.text
    assert demo_progress.json()["summary"]["student_count"] == 1
    assert [item["student_code"] for item in demo_progress.json()["students"]] == ["S002"]

    other_list = client.get(
        "/api/v1/assignments",
        params={"page_size": 100, "class_name": "其他测试班"},
    )
    assert [item["id"] for item in other_list.json()["items"]] == [assignment["id"]]

    other_progress = client.get(
        f"/api/v1/analytics/assignments/{assignment['id']}",
        params={"class_name": "其他测试班"},
    )
    assert other_progress.status_code == 200, other_progress.text
    assert [item["student_code"] for item in other_progress.json()["students"]] == [
        other_student.student_code
    ]


def test_empty_assignment_can_be_deleted(client, db):
    student = db.scalar(select(Student).where(Student.student_code == "S002"))
    created = client.post(
        "/api/v1/assignments",
        json={
            "idempotency_key": "delete-empty-" + uuid4().hex,
            "title": "待删除空作业",
            "class_id": str(student.class_id),
            "student_ids": [str(student.id)],
            "recommendation_parameters": {
                "review_exam_point_codes": ["math.g6.rational.exam.local.a61b42d3fd"],
                "review_count": 1,
                "avoid_recent_days": 0,
                "max_exam_point_ratio": 1,
            },
        },
    )
    assert created.status_code == 201, created.text
    assignment = created.json()
    paper = assignment["papers"][0]
    removed = client.request(
        "DELETE",
        f"/api/v1/paper-instances/{paper['id']}",
        json={"expected_edit_revision": paper["edit_revision"]},
    )
    assert removed.status_code == 200, removed.text
    deleted = client.delete(f"/api/v1/assignments/{assignment['id']}")
    assert deleted.status_code == 204, deleted.text
    assert client.get(f"/api/v1/assignments/{assignment['id']}").status_code == 404


def test_analytics_missing_entities_are_structured(client):
    missing_class = client.get(
        "/api/v1/analytics/class-overview",
        params={"class_name": "不存在班级", "chapter_code": "math.g6.rational"},
    )
    assert missing_class.status_code == 404
    assert missing_class.json()["code"] == "http_404"

    missing_assignment = client.get(
        "/api/v1/analytics/assignments/00000000-0000-0000-0000-000000000000"
    )
    assert missing_assignment.status_code == 404

