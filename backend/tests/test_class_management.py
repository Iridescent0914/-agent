from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.main import app
from app.models import Assignment


@pytest.fixture()
def db() -> Session:
    with SessionLocal() as session:
        transaction = session.begin()
        yield session
        transaction.rollback()


@pytest.fixture()
def client(db: Session) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_class_crud_and_student_assignment(client: TestClient, db: Session) -> None:
    suffix = uuid4().hex[:8]
    class_name = f"测试班级-{suffix}"
    created = client.post(
        "/api/v1/classes",
        json={
            "name": class_name,
            "grade": "六年级",
            "school_year": "2026-2027",
            "teacher_name": "测试教师",
        },
    )
    assert created.status_code == 201
    school_class = created.json()
    assert school_class["student_count"] == 0

    student = client.post(
        "/api/v1/students",
        json={
            "student_code": f"T-{suffix}",
            "display_name": "测试学生",
            "class_id": school_class["id"],
        },
    )
    assert student.status_code == 201
    assert student.json()["class_id"] == school_class["id"]
    assert student.json()["class_name"] == class_name
    class_students = client.get(
        "/api/v1/students", params={"page_size": 100, "class_id": school_class["id"]}
    )
    assert class_students.status_code == 200
    assert [item["id"] for item in class_students.json()["items"]] == [student.json()["id"]]

    renamed = client.patch(
        f"/api/v1/classes/{school_class['id']}",
        json={"name": f"测试新班-{suffix}"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["student_count"] == 1
    refreshed_student = client.get(f"/api/v1/students/{student.json()['id']}")
    assert refreshed_student.json()["class_name"] == f"测试新班-{suffix}"

    blocked = client.delete(f"/api/v1/classes/{school_class['id']}")
    assert blocked.status_code == 409

    transfer = client.patch(
        f"/api/v1/students/{student.json()['id']}/class",
        json={"class_id": school_class["id"]},
    )
    assert transfer.status_code == 404

    archived = client.request(
        "DELETE",
        f"/api/v1/students/{student.json()['id']}",
        json={"operator_name": "测试教师"},
    )
    assert archived.status_code == 200
    db.add(Assignment(
        idempotency_key=f"class-delete-{suffix}",
        title="历史作业",
        class_id=school_class["id"],
        chapter_code="math.g6.rational",
        teaching_scope={},
        assignment_type="practice",
    ))
    db.flush()
    historical = client.delete(f"/api/v1/classes/{school_class['id']}")
    assert historical.status_code == 409
    assert historical.json()["message"] == "班级已有历史作业，不能删除"

    empty = client.post(
        "/api/v1/classes",
        json={"name": f"空班-{suffix}", "grade": "六年级", "school_year": "2026-2027"},
    ).json()
    assert client.delete(f"/api/v1/classes/{empty['id']}").status_code == 204
