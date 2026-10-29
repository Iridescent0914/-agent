from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import engine, get_db
from app.main import app
from app.models import Attempt, RecognitionRun, Student, StudentMastery
from app.services.recognition_workflow import (
    _expected_vision_item,
    extract_run,
    purge_expired_recognition_images,
)
from app.services.submission_workflow import GradingConflict
from app.services.vision_service import (
    OpenAICompatibleVisionService,
    VisionServiceError,
    build_extraction_prompt,
    parse_vision_output,
)


@pytest.fixture
def db():
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as value:
            yield value
    finally:
        app.dependency_overrides.pop(get_db, None)


def _published_paper(client: TestClient, db) -> tuple[Student, dict]:
    student = db.scalar(select(Student).where(Student.student_code == "DEMO-S003"))
    response = client.post("/api/v1/assignments", json={
        "idempotency_key": "m6-assignment-" + uuid4().hex,
        "title": "M6 图片识别测试",
        "student_ids": [str(student.id)],
        "recommendation_parameters": {
            "review_knowledge_point_codes": [
                "math.g6.rational.kp.08_multiply_divide",
                "math.g6.rational.kp.07_add_subtract",
            ],
            "review_count": 2,
            "avoid_recent_days": 0,
            "max_knowledge_point_ratio": 1,
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
        "solution", "wrong_analysis",
    ):
        assert private_key not in serialized
    for item in body["items"]:
        assert "answer_snapshot" not in item
        assert "answer_rule_snapshot" not in item
        assert "correct_answer" not in item["question_snapshot"]


def test_image_submission_waits_for_teacher_before_mastery(client, db):
    student, paper = _published_paper(client, db)
    mastery_before = {
        row.knowledge_point_id: (row.mastery, row.attempt_count)
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
        duplicate_upload = client.post(
            f"/api/v1/paper-instances/{paper['id']}/recognition-runs",
            data={
                "instance_code": paper["instance_code"],
                "idempotency_key": "m6-after-submit-" + uuid4().hex,
            },
            files={"file": ("answers.jpg", _jpeg(), "image/jpeg")},
        )
        assert duplicate_upload.status_code == 409
        db.expire_all()
        mastery_pending = {
            row.knowledge_point_id: (row.mastery, row.attempt_count)
            for row in db.scalars(select(StudentMastery).where(StudentMastery.student_id == student.id))
        }
        assert mastery_pending == mastery_before

        first = submission["items"][0]
        second = submission["items"][1]
        first_attempt = db.get(Attempt, first["attempt"]["id"])
        second_attempt = db.get(Attempt, second["attempt"]["id"])
        first_attempt.recognition_status = "blank"
        first_attempt.student_answer = None
        second_attempt.recognition_status = "blank"
        second_attempt.student_answer = None
        db.flush()
        batch_blank = client.post(
            f"/api/v1/submissions/{submission['submission_id']}/recognition-review",
            json={
                "reason": "blank items require individual confirmation",
                "actor": "m6_test_teacher",
                "items": [{
                    "attempt_id": row["attempt"]["id"],
                    "expected_revision_no": row["attempt"]["revision_no"],
                    "action": "accept",
                    "response_status": "blank",
                    "answer": None,
                } for row in (first, second)],
            },
        )
        assert batch_blank.status_code == 422

        first_attempt.recognition_status = "readable"
        first_attempt.student_answer = "A"
        db.flush()
        dishonest_accept = client.post(
            f"/api/v1/submissions/{submission['submission_id']}/recognition-review",
            json={
                "reason": "accept must preserve recognition",
                "actor": "m6_test_teacher",
                "items": [{
                    "attempt_id": first["attempt"]["id"],
                    "expected_revision_no": first["attempt"]["revision_no"],
                    "action": "accept",
                    "response_status": "answered",
                    "answer": "B",
                }],
            },
        )
        assert dishonest_accept.status_code == 422
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
    current = client.get(
        f"/api/v1/paper-instances/{paper['id']}/recognition-runs/current"
    )
    assert current.status_code == 200
    assert current.json()["id"] == first.json()["id"]
    stored = db.get(RecognitionRun, first.json()["id"])
    (get_settings().recognition_upload_dir / stored.image_storage_key).unlink(
        missing_ok=True
    )


def test_extract_claims_run_before_calling_provider(client, db):
    _, paper = _published_paper(client, db)
    upload = client.post(
        f"/api/v1/paper-instances/{paper['id']}/recognition-runs",
        data={
            "instance_code": paper["instance_code"],
            "idempotency_key": "m6-processing-" + uuid4().hex,
        },
        files={"file": ("answers.jpg", _jpeg(), "image/jpeg")},
    )
    assert upload.status_code == 201, upload.text
    stored = db.get(RecognitionRun, upload.json()["id"])

    class InspectingService:
        def extract_answers(self, *, image_bytes, content_type, expected_items):
            assert image_bytes
            assert content_type == "image/jpeg"
            assert db.get(RecognitionRun, stored.id).status == "processing"
            return {"schema_version": "m6-recognition-v2", "answers": []}

    try:
        result = extract_run(db, stored.id, service=InspectingService())
        assert result.status == "ready_for_review"
        assert all(item.recognition_status == "missing" for item in result.items)
    finally:
        (get_settings().recognition_upload_dir / stored.image_storage_key).unlink(
            missing_ok=True
        )


def test_stale_processing_run_can_be_recovered(client, db, monkeypatch):
    _, paper = _published_paper(client, db)
    upload = client.post(
        f"/api/v1/paper-instances/{paper['id']}/recognition-runs",
        data={
            "instance_code": paper["instance_code"],
            "idempotency_key": "m6-stale-" + uuid4().hex,
        },
        files={"file": ("answers.jpg", _jpeg(), "image/jpeg")},
    )
    assert upload.status_code == 201, upload.text
    stored = db.get(RecognitionRun, upload.json()["id"])
    stored.status = "processing"
    stored.started_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    db.flush()

    class RecoveryService:
        def extract_answers(self, *, image_bytes, content_type, expected_items):
            return {"schema_version": "m6-recognition-v2", "answers": []}

    try:
        recovered = extract_run(db, stored.id, service=RecoveryService())
        assert recovered.status == "ready_for_review"
        assert recovered.processing_stale is False
    finally:
        (get_settings().recognition_upload_dir / stored.image_storage_key).unlink(
            missing_ok=True
        )


def test_reclaimed_processing_run_rejects_the_old_worker_result(client, db):
    _, paper = _published_paper(client, db)
    upload = client.post(
        f"/api/v1/paper-instances/{paper['id']}/recognition-runs",
        data={
            "instance_code": paper["instance_code"],
            "idempotency_key": "m6-claim-token-" + uuid4().hex,
        },
        files={"file": ("answers.jpg", _jpeg(), "image/jpeg")},
    )
    assert upload.status_code == 201, upload.text
    stored = db.get(RecognitionRun, upload.json()["id"])

    class ReclaimedWhileRunningService:
        def extract_answers(self, *, image_bytes, content_type, expected_items):
            stored.processing_token = uuid4()
            db.flush()
            return {"schema_version": "m6-recognition-v2", "answers": []}

    try:
        with pytest.raises(GradingConflict, match="changed while extraction"):
            extract_run(db, stored.id, service=ReclaimedWhileRunningService())
    finally:
        (get_settings().recognition_upload_dir / stored.image_storage_key).unlink(
            missing_ok=True
        )


def test_expired_recognition_image_is_purged_but_run_remains(client, db):
    _, paper = _published_paper(client, db)
    upload = client.post(
        f"/api/v1/paper-instances/{paper['id']}/recognition-runs",
        data={
            "instance_code": paper["instance_code"],
            "idempotency_key": "m6-retention-" + uuid4().hex,
        },
        files={"file": ("answers.jpg", _jpeg(), "image/jpeg")},
    )
    assert upload.status_code == 201, upload.text
    stored = db.get(RecognitionRun, upload.json()["id"])
    path = get_settings().recognition_upload_dir / stored.image_storage_key
    stored.retention_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.flush()

    assert purge_expired_recognition_images(db) == 1
    db.refresh(stored)
    assert stored.image_purged_at is not None
    assert not path.exists()
    detail = client.get(f"/api/v1/recognition-runs/{stored.id}")
    assert detail.status_code == 200
    assert detail.json()["image_url"] is None


def test_expired_image_is_retained_until_teacher_review_finishes(client, db):
    _, paper = _published_paper(client, db)
    upload = client.post(
        f"/api/v1/paper-instances/{paper['id']}/recognition-runs",
        data={
            "instance_code": paper["instance_code"],
            "idempotency_key": "m6-review-retention-" + uuid4().hex,
        },
        files={"file": ("answers.jpg", _jpeg(), "image/jpeg")},
    )
    assert upload.status_code == 201, upload.text
    run_id = upload.json()["id"]
    assert client.post(f"/api/v1/recognition-runs/{run_id}/extract").status_code == 200
    committed = client.post(f"/api/v1/recognition-runs/{run_id}/commit")
    assert committed.status_code == 201, committed.text
    stored = db.get(RecognitionRun, run_id)
    path = get_settings().recognition_upload_dir / stored.image_storage_key
    stored.retention_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.flush()

    try:
        assert purge_expired_recognition_images(db) == 0
        assert path.is_file()
        attempts = list(db.scalars(select(Attempt).where(
            Attempt.submission_id == stored.submission_id
        )))
        assert attempts and all(item.grade_status == "needs_review" for item in attempts)
        for attempt in attempts:
            attempt.grade_status = "teacher_confirmed"
            attempt.is_correct = False
        db.flush()
        assert purge_expired_recognition_images(db) == 1
        assert not path.exists()
    finally:
        path.unlink(missing_ok=True)


def test_second_active_recognition_run_is_rejected(client, db):
    _, paper = _published_paper(client, db)
    first = client.post(
        f"/api/v1/paper-instances/{paper['id']}/recognition-runs",
        data={
            "instance_code": paper["instance_code"],
            "idempotency_key": "m6-active-first-" + uuid4().hex,
        },
        files={"file": ("answers.jpg", _jpeg(), "image/jpeg")},
    )
    assert first.status_code == 201, first.text
    stored = db.get(RecognitionRun, first.json()["id"])
    try:
        second = client.post(
            f"/api/v1/paper-instances/{paper['id']}/recognition-runs",
            data={
                "instance_code": paper["instance_code"],
                "idempotency_key": "m6-active-second-" + uuid4().hex,
            },
            files={"file": ("other.jpg", _jpeg(), "image/jpeg")},
        )
        assert second.status_code == 409, second.text
    finally:
        (get_settings().recognition_upload_dir / stored.image_storage_key).unlink(
            missing_ok=True
        )


def test_ready_run_uses_absolute_review_retention_ceiling(client, db):
    _, paper = _published_paper(client, db)
    upload = client.post(
        f"/api/v1/paper-instances/{paper['id']}/recognition-runs",
        data={
            "instance_code": paper["instance_code"],
            "idempotency_key": "m6-ready-expiry-" + uuid4().hex,
        },
        files={"file": ("answers.jpg", _jpeg(), "image/jpeg")},
    )
    assert upload.status_code == 201, upload.text
    run_id = upload.json()["id"]
    assert client.post(f"/api/v1/recognition-runs/{run_id}/extract").status_code == 200
    stored = db.get(RecognitionRun, run_id)
    path = get_settings().recognition_upload_dir / stored.image_storage_key
    stored.retention_until = datetime.now(timezone.utc) - timedelta(days=1)
    stored.created_at = datetime.now(timezone.utc) - timedelta(
        days=get_settings().recognition_unreviewed_max_retention_days + 1
    )
    db.flush()
    try:
        assert purge_expired_recognition_images(db) == 1
        db.refresh(stored)
        assert stored.status == "discarded"
        assert stored.error_code == "review_expired"
        assert not path.exists()
    finally:
        path.unlink(missing_ok=True)


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
