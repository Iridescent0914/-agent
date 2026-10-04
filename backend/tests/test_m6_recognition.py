from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import SessionLocal, get_db
from app.main import app
from app.models import Attempt, RecognitionRun, Student, StudentMastery
from app.services.recognition_workflow import _expected_vision_item
from app.services.vision_service import (
    OpenAICompatibleVisionService,
    VisionServiceError,
    build_extraction_prompt,
    parse_vision_output,
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


def _published_paper(client: TestClient, db) -> tuple[Student, dict]:
    student = db.scalar(select(Student).where(Student.student_code == "S003"))
    response = client.post("/api/v1/assignments", json={
        "idempotency_key": "m6-assignment-" + uuid4().hex,
        "title": "M6 图片识别测试",
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
    })
    assert response.status_code == 201, response.text
    paper = response.json()["papers"][0]
    published = client.post(
        f"/api/v1/paper-instances/{paper['id']}/publish",
        json={"expected_edit_revision": paper["edit_revision"]},
    )
    assert published.status_code == 200, published.text
    return student, published.json()


def _jpeg() -> bytes:
    output = BytesIO()
    Image.new("RGB", (640, 360), "white").save(output, format="JPEG")
    return output.getvalue()


def test_parser_fills_missing_and_rejects_duplicate_orders():
    values = parse_vision_output({
        "schema_version": "m6-recognition-v1",
        "answers": [{
            "order_no": 1,
            "answer": "B",
            "recognition_status": "readable",
            "confidence": 0.9,
        }],
    }, {1, 2})
    assert values[2]["recognition_status"] == "missing"
    with pytest.raises(VisionServiceError, match="重复题号"):
        parse_vision_output({
            "schema_version": "m6-recognition-v1",
            "answers": [
                {"order_no": 1, "answer": "A", "recognition_status": "readable"},
                {"order_no": 1, "answer": "B", "recognition_status": "readable"},
            ],
        }, {1})


def test_choice_spec_supports_dynamic_letters_without_leaking_answer():
    item = SimpleNamespace(
        order_no=3,
        question_snapshot={"question_type": "choice", "question_text": "请选择"},
        options_snapshot={
            "A": "一", "B": "二", "C": "三", "D": "四", "E": "五",
        },
        answer_snapshot="E",
    )
    spec = _expected_vision_item(item)
    assert spec == {
        "order_no": 3,
        "question_type": "choice",
        "blank_ids": ["answer"],
        "answer_format": "choice_letter",
        "allowed_option_labels": ["A", "B", "C", "D", "E"],
    }
    prompt = build_extraction_prompt([spec])
    assert '"allowed_option_labels": ["A", "B", "C", "D", "E"]' in prompt
    assert '"correct_answer"' not in prompt
    assert '"answer_snapshot"' not in prompt


def test_choice_spec_can_derive_letters_from_legacy_question_text():
    item = SimpleNamespace(
        order_no=1,
        question_snapshot={
            "question_type": "choice",
            "question_text": "A.甲； B.乙； C.丙； D.丁； E.戊； F.己",
        },
        options_snapshot=None,
        answer_snapshot="F",
    )
    assert _expected_vision_item(item)["allowed_option_labels"] == list("ABCDEF")


def test_choice_output_normalizes_and_rejects_out_of_range_letters():
    expected = [{
        "order_no": 1,
        "question_type": "choice",
        "blank_ids": ["answer"],
        "answer_format": "choice_letter",
        "allowed_option_labels": list("ABCDE"),
    }]
    valid = parse_vision_output({
        "schema_version": "m6-recognition-v2",
        "answers": [{
            "order_no": 1, "answer": "ｅ",
            "recognition_status": "readable", "confidence": 0.9,
        }],
    }, expected)
    assert valid[1]["answer"] == "E"
    assert valid[1]["recognition_status"] == "readable"

    invalid = parse_vision_output({
        "schema_version": "m6-recognition-v2",
        "answers": [{
            "order_no": 1, "answer": "Z",
            "recognition_status": "readable", "confidence": 0.8,
        }],
    }, expected)
    assert invalid[1]["answer"] == "Z"
    assert invalid[1]["recognition_status"] == "ambiguous"


def test_group_choice_output_must_cover_every_blank():
    expected = [{
        "order_no": 1,
        "question_type": "choice",
        "blank_ids": ["1", "2"],
        "answer_format": "choice_letter",
        "allowed_option_labels": list("ABCDE"),
    }]
    payload = {
        "schema_version": "m6-recognition-v2",
        "answers": [{
            "order_no": 1,
            "answer": {"kind": "group", "items": [
                {"item_id": "2", "answer": "ｅ"},
                {"item_id": "1", "answer": "b"},
            ]},
            "recognition_status": "readable",
            "confidence": 0.88,
        }],
    }
    result = parse_vision_output(payload, expected)[1]
    assert result["answer"] == {
        "kind": "group",
        "items": [
            {"item_id": "1", "answer": "B"},
            {"item_id": "2", "answer": "E"},
        ],
    }
    payload["answers"][0]["answer"]["items"].pop()
    assert parse_vision_output(payload, expected)[1]["recognition_status"] == "ambiguous"


def test_openai_compatible_local_service_does_not_require_api_key(monkeypatch):
    settings = SimpleNamespace(
        vision_api_base_url="http://localhost:8001/v1",
        vision_api_key="",
        vision_model="Qwen/Qwen3-VL-4B-Instruct",
        vision_max_tokens=2048,
        vision_timeout_seconds=45,
    )
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": """```json
{"schema_version":"m6-recognition-v2","answers":[]}
```"""}}]}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr("app.services.vision_service.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.vision_service.httpx.post", fake_post)
    service = OpenAICompatibleVisionService()
    assert service.extract_answers(
        image_bytes=b"image", content_type="image/jpeg", expected_items=[]
    )["schema_version"] == "m6-recognition-v2"
    assert "Authorization" not in captured["headers"]
    assert captured["json"]["max_tokens"] == 2048


def test_print_view_never_returns_answer_keys(client, db):
    _, paper = _published_paper(client, db)
    response = client.get(f"/api/v1/paper-instances/{paper['id']}/print-view")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["instance_code"] == paper["instance_code"]
    assert [row["order_no"] for row in body["items"]] == [1, 2]
    serialized = json.dumps(body, ensure_ascii=False)
    for private_key in (
        "answer_snapshot", "answer_rule_snapshot", "correct_answer",
        "grading_details", "selection_audit", "recommendation_score",
    ):
        assert private_key not in serialized
    for item in body["items"]:
        assert "answer_snapshot" not in item
        assert "answer_rule_snapshot" not in item
        assert "correct_answer" not in item["question_snapshot"]


def test_image_submission_waits_for_teacher_before_mastery(client, db):
    student, paper = _published_paper(client, db)
    mastery_before = {
        row.exam_point_id: (row.mastery, row.attempt_count)
        for row in db.scalars(select(StudentMastery).where(StudentMastery.student_id == student.id))
    }
    upload = client.post(
        f"/api/v1/paper-instances/{paper['id']}/recognition-runs",
        data={
            "instance_code": paper["instance_code"],
            "idempotency_key": "m6-upload-" + uuid4().hex,
        },
        files={"file": ("answers.jpg", _jpeg(), "image/jpeg")},
    )
    assert upload.status_code == 201, upload.text
    run = upload.json()
    stored = db.get(RecognitionRun, run["id"])
    stored_path = get_settings().recognition_upload_dir / stored.image_storage_key
    try:
        extracted = client.post(f"/api/v1/recognition-runs/{run['id']}/extract")
        assert extracted.status_code == 200, extracted.text
        assert extracted.json()["status"] == "ready_for_review"
        assert all(row["recognition_status"] == "illegible" for row in extracted.json()["items"])

        committed = client.post(f"/api/v1/recognition-runs/{run['id']}/commit")
        assert committed.status_code == 201, committed.text
        submission = committed.json()
        assert submission["source"] == "image"
        assert submission["summary"]["needs_review_count"] == len(paper["items"])
        assert all(row["attempt"]["grade_status"] == "needs_review" for row in submission["items"])
        db.expire_all()
        mastery_pending = {
            row.exam_point_id: (row.mastery, row.attempt_count)
            for row in db.scalars(select(StudentMastery).where(StudentMastery.student_id == student.id))
        }
        assert mastery_pending == mastery_before

        first = submission["items"][0]
        source_item = next(row for row in paper["items"] if row["id"] == first["id"])
        reviewed = client.post(
            f"/api/v1/submissions/{submission['submission_id']}/recognition-review",
            json={
                "reason": "对照原图修正确认",
                "actor": "m6_test_teacher",
                "items": [{
                    "attempt_id": first["attempt"]["id"],
                    "expected_revision_no": first["attempt"]["revision_no"],
                    "action": "correct",
                    "response_status": "answered",
                    "answer": source_item["answer_snapshot"],
                }],
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        updated = next(row for row in reviewed.json()["items"] if row["id"] == first["id"])
        assert updated["attempt"]["grade_status"] == "teacher_confirmed"
        assert updated["attempt"]["revision_no"] == 2
        remaining = [
            row for row in reviewed.json()["items"]
            if row["attempt"]["grade_status"] == "needs_review"
        ]
        if remaining:
            batch = client.post(
                f"/api/v1/submissions/{submission['submission_id']}/recognition-review",
                json={
                    "reason": "batch image transcription correction",
                    "actor": "m6_test_teacher",
                    "items": [{
                        "attempt_id": row["attempt"]["id"],
                        "expected_revision_no": row["attempt"]["revision_no"],
                        "action": "correct",
                        "response_status": "answered",
                        "answer": next(
                            source["answer_snapshot"]
                            for source in paper["items"] if source["id"] == row["id"]
                        ),
                    } for row in remaining],
                },
            )
            assert batch.status_code == 200, batch.text
            assert batch.json()["summary"]["needs_review_count"] == 0
        assert int(db.scalar(select(func.count(Attempt.id)).where(
            Attempt.submission_id == submission["submission_id"]
        ))) == len(paper["items"])
    finally:
        Path(stored_path).unlink(missing_ok=True)


def test_upload_rejects_wrong_instance_code(client, db):
    _, paper = _published_paper(client, db)
    response = client.post(
        f"/api/v1/paper-instances/{paper['id']}/recognition-runs",
        data={"instance_code": "RAT-WRONG-CODE", "idempotency_key": "wrong-" + uuid4().hex},
        files={"file": ("answers.jpg", _jpeg(), "image/jpeg")},
    )
    assert response.status_code == 422


def test_upload_idempotency_replays_the_same_run(client, db):
    _, paper = _published_paper(client, db)
    key = "m6-idempotent-" + uuid4().hex
    request = {
        "data": {"instance_code": paper["instance_code"], "idempotency_key": key},
        "files": {"file": ("answers.jpg", _jpeg(), "image/jpeg")},
    }
    first = client.post(
        f"/api/v1/paper-instances/{paper['id']}/recognition-runs", **request
    )
    second = client.post(
        f"/api/v1/paper-instances/{paper['id']}/recognition-runs", **request
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]
    stored = db.get(RecognitionRun, first.json()["id"])
    (get_settings().recognition_upload_dir / stored.image_storage_key).unlink(
        missing_ok=True
    )


def test_withdrawn_paper_is_rechecked_before_image_commit(client, db):
    _, paper = _published_paper(client, db)
    upload = client.post(
        f"/api/v1/paper-instances/{paper['id']}/recognition-runs",
        data={
            "instance_code": paper["instance_code"],
            "idempotency_key": "m6-withdraw-" + uuid4().hex,
        },
        files={"file": ("answers.jpg", _jpeg(), "image/jpeg")},
    )
    assert upload.status_code == 201, upload.text
    run = upload.json()
    stored = db.get(RecognitionRun, run["id"])
    try:
        extracted = client.post(f"/api/v1/recognition-runs/{run['id']}/extract")
        assert extracted.status_code == 200, extracted.text
        withdrawn = client.post(
            f"/api/v1/paper-instances/{paper['id']}/withdraw",
            json={
                "expected_edit_revision": paper["edit_revision"],
                "reason": "M6 commit state recheck",
            },
        )
        assert withdrawn.status_code == 200, withdrawn.text
        rejected = client.post(f"/api/v1/recognition-runs/{run['id']}/commit")
        assert rejected.status_code == 422, rejected.text
    finally:
        (get_settings().recognition_upload_dir / stored.image_storage_key).unlink(
            missing_ok=True
        )
