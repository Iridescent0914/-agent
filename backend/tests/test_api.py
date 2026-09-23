from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_and_readiness() -> None:
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json()["migration"] == "0004"


def test_question_list_filters_and_detail_assets() -> None:
    response = client.get("/api/v1/questions", params={"page_size": 100})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 30
    assert len(body["items"]) == 30

    choice = client.get("/api/v1/questions", params={"question_type": "choice"}).json()
    assert choice["total"] == 10
    assert all(item["question_type"] == "choice" for item in choice["items"])

    with_assets = next(item for item in body["items"] if item["asset_count"] > 0)
    detail_response = client.get(f"/api/v1/questions/{with_assets['id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["main_exam_point"]["code"]
    assert detail["assets"]
    assert all(asset["conversion_status"] == "converted" for asset in detail["assets"])
    assert all(asset["web_url"] for asset in detail["assets"])


def test_student_history_is_traceable() -> None:
    response = client.get("/api/v1/students", params={"page_size": 100})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert all(item["attempt_count"] == 30 for item in body["items"])

    student = body["items"][0]
    detail = client.get(f"/api/v1/students/{student['id']}")
    assert detail.status_code == 200
    history = client.get(
        f"/api/v1/students/{student['id']}/attempts", params={"page_size": 100}
    )
    assert history.status_code == 200
    history_body = history.json()
    assert history_body["total"] == 30
    assert all(item["paper_instance_id"] for item in history_body["items"])
    assert all(item["paper_instance_item_id"] for item in history_body["items"])


def test_missing_entities_return_structured_404() -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    response = client.get(f"/api/v1/questions/{missing}")
    assert response.status_code == 404
    assert response.json()["code"] == "http_404"
