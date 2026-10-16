from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_and_readiness() -> None:
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json()['migration'] == '0022'


def test_question_list_filters_and_detail_assets() -> None:
    response = client.get("/api/v1/questions", params={"page_size": 100})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 139
    assert len(body["items"]) == 100

    choice = client.get("/api/v1/questions", params={"question_type": "choice"}).json()
    assert choice["total"] == 11
    true_false = client.get(
        "/api/v1/questions", params={"question_type": "true_false"}
    ).json()
    assert true_false["total"] == 2
    assert all(item["question_type"] == "choice" for item in choice["items"])

    ready = client.get(
        "/api/v1/questions", params={"grading_readiness": "machine_ready", "page_size": 100}
    ).json()
    assert 0 <= ready["total"] <= 139
    assert all(item["grading_readiness"] == "machine_ready" for item in ready["items"])
    assert all(item["import_issue_count"] == 0 for item in ready["items"])
    stats = client.get("/api/v1/questions/stats").json()
    assert stats["total"] == 139
    assert stats["ready_count"] == ready["total"]
    assert stats["ready_count"] + stats["pending_count"] == stats["total"]

    with_assets = next(item for item in body["items"] if item["asset_count"] > 0)
    detail_response = client.get(f"/api/v1/questions/{with_assets['id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["main_exam_point"]["code"]
    assert detail["grade"] == "六年级上册"
    assert detail["core_literacy"]
    assert type(detail["is_required"]) is bool
    assert detail["assets"]
    assert all(asset["conversion_status"] == "converted" for asset in detail["assets"])
    assert all(asset["web_url"] for asset in detail["assets"])
    assert "contains_asset" not in detail["import_issues"]
    assert "asset_needs_web_conversion" not in detail["import_issues"]
    assert len(detail["import_issues"]) == len(set(detail["import_issues"]))


def test_question_catalog_supports_cascading_filters() -> None:
    response = client.get('/api/v1/questions/catalog')
    assert response.status_code == 200
    chapters = response.json()
    assert chapters
    chapter = chapters[0]
    assert chapter['code'] and chapter['knowledge_points']
    knowledge_point = chapter['knowledge_points'][0]
    assert knowledge_point['code'] and knowledge_point['exam_points']
    exam_point = knowledge_point['exam_points'][0]

    by_chapter = client.get('/api/v1/questions', params={
        'chapter_code': chapter['code'],
        'page_size': 100,
    }).json()
    assert by_chapter['total'] > 0

    by_knowledge = client.get('/api/v1/questions', params={
        'knowledge_point_code': knowledge_point['code'],
        'page_size': 100,
    }).json()
    assert by_knowledge['total'] > 0
    assert all(
        item['main_knowledge_point']['code'] == knowledge_point['code']
        for item in by_knowledge['items']
    )

    by_exam = client.get('/api/v1/questions', params={
        'exam_point_code': exam_point['code'],
        'page_size': 100,
    }).json()
    assert by_exam['total'] > 0
    assert all(
        item['main_exam_point']['code'] == exam_point['code']
        for item in by_exam['items']
    )


def test_student_history_is_traceable() -> None:
    response = client.get("/api/v1/students", params={"page_size": 100})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 5
    # The demo database is intentionally reusable: later M4 submissions append
    # attempts without replacing the 30 seeded history records per student.
    seeded = [item for item in body["items"] if item["student_code"] in {"S001", "S002", "S003", "S004", "S005"}]
    assert len(seeded) == 5
    assert all(item["attempt_count"] >= 30 for item in seeded)

    student = seeded[0]
    detail = client.get(f"/api/v1/students/{student['id']}")
    assert detail.status_code == 200
    history = client.get(
        f"/api/v1/students/{student['id']}/attempts", params={"page_size": 100}
    )
    assert history.status_code == 200
    history_body = history.json()
    assert history_body["total"] == student["attempt_count"]
    assert all(item["paper_instance_id"] for item in history_body["items"])
    assert all(item["paper_instance_item_id"] for item in history_body["items"])


def test_missing_entities_return_structured_404() -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    response = client.get(f"/api/v1/questions/{missing}")
    assert response.status_code == 404
    assert response.json()["code"] == "http_404"
