from __future__ import annotations

import uuid
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import get_db
from app.main import app
from app.models import Question, QuestionImportCommit
from app.schemas.question_import_s5 import S5ItemDecisionRequest
from app.schemas.question_import_s6 import S6CommitRequest
from app.services.question_import import ImportProblem
from app.services.question_import_s4_review import _candidate_signature
from app.services.question_import_s5_review import decide_item
from app.services.question_import_s6_commit import commit_import
from tests.test_question_import_s5_preflight import _case, _second_item, db  # noqa: F401


def _approve(db, batch, item, *, route="library_only", review_items=None):
    return decide_item(db, batch.id, item.id, S5ItemDecisionRequest(
        expected_revision=item.revision, decision="approved", route=route,
        answer_review_items=review_items,
    ))


def _request(items, operation_id=None):
    return S6CommitRequest(operation_id=operation_id or uuid.uuid4(), items=[
        {"item_id": item.id, "expected_revision": item.revision,
         "question_event_id": uuid.UUID(approval["question_event_id"])}
        for item, approval in items
    ])


def _attach_image(db, batch, item, check, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    monkeypatch.setattr(settings, "teacher_asset_dir", tmp_path / "teacher")
    asset_id = uuid.uuid4()
    buffer = BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    source = settings.question_import_dir / str(batch.id) / f"s3-image-{asset_id.hex}.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(buffer.getvalue())
    batch.checkpoint = {"s3": {"global_dedupe": {"status": "completed"},
                               "images": {"assets": [{"asset_id": str(asset_id),
                                                       "source_file_id": None}]}}}
    item.payload = {**item.payload, "content": {**item.payload["content"],
                    "asset_ids": [str(asset_id)]}}
    db.flush()
    check.candidate_signature = _candidate_signature(db, batch.id, item)
    db.flush()
    return buffer.getvalue()


def test_library_commit_creates_formal_question_and_replays_once(db):
    batch, item, _ = _case(db)
    approval = _approve(db, batch, item)
    request = _request([(item, approval)])
    first = commit_import(db, batch.id, request)
    question = db.get(Question, uuid.UUID(first["items"][0]["question_id"]))
    assert question.import_item_id == item.id
    assert question.grading_readiness == "raw_answer_only"
    assert question.correct_answer is None
    assert item.payload["imported_question_id"] == str(question.id)
    assert first["count"] == 1 and first["replayed"] is False

    replay = commit_import(db, batch.id, request)
    assert replay["items"] == first["items"] and replay["replayed"] is True
    assert db.scalar(select(QuestionImportCommit).where(
        QuestionImportCommit.batch_id == batch.id)).result == first

    conflict = _request([(item, approval)], operation_id=request.operation_id)
    conflict.items[0].expected_revision += 1
    with pytest.raises(ImportProblem) as error:
        commit_import(db, batch.id, conflict)
    assert error.value.code == "OPERATION_CONFLICT"


def test_direct_pool_commit_promotes_s5_approved_answer(db):
    batch, item, check = _case(db)
    item.payload = {**item.payload, "selected_answer_id": "a1", "answer_match_status": "matched",
                    "answers": [{"answer_id": "a1", "answer_source": "source_extracted",
                                 "text": "A", "teacher_confirmed": False}]}
    check.candidate_signature = _candidate_signature(db, batch.id, item)
    db.flush()
    approval = _approve(db, batch, item, route="direct_pool", review_items=[
        {"item_id": "answer-1", "answer": "A", "answer_match_type": "choice"},
    ])
    result = commit_import(db, batch.id, _request([(item, approval)]))
    question = db.get(Question, uuid.UUID(result["items"][0]["question_id"]))
    assert question.grading_readiness == "machine_ready"
    assert question.correct_answer["items"][0]["answer"] == "A"
    assert question.review_revision == 2
    assert [row["type"] for row in question.review_history] == ["s6_import", "promoted"]


def test_one_blocked_selection_prevents_every_question_write(db):
    batch, first, _ = _case(db)
    second = _second_item(db, batch, first)
    approval = _approve(db, batch, first)
    request = _request([(first, approval), (second, approval)])
    with pytest.raises(ImportProblem) as error:
        commit_import(db, batch.id, request)
    assert error.value.code == "S6_COMMIT_BLOCKED"
    assert not db.scalar(select(Question).where(Question.import_batch_id == batch.id))
    assert not db.scalar(select(QuestionImportCommit).where(QuestionImportCommit.batch_id == batch.id))


def test_formal_image_is_copied_out_of_temporary_import_dir(db, tmp_path, monkeypatch):
    batch, item, check = _case(db)
    image = _attach_image(db, batch, item, check, tmp_path, monkeypatch)
    approval = _approve(db, batch, item)
    result = commit_import(db, batch.id, _request([(item, approval)]))
    question = db.get(Question, uuid.UUID(result["items"][0]["question_id"]))
    target = get_settings().teacher_asset_dir / question.assets[0]["path"].split("/")[-1]
    assert question.has_image is True
    assert target.read_bytes() == image


def test_failed_pool_promotion_rolls_back_question_and_removes_new_asset(db, tmp_path, monkeypatch):
    batch, item, check = _case(db)
    _attach_image(db, batch, item, check, tmp_path, monkeypatch)
    item.payload = {**item.payload, "selected_answer_id": "a1", "answer_match_status": "matched",
                    "answers": [{"answer_id": "a1", "answer_source": "source_extracted",
                                 "text": "A", "teacher_confirmed": False}]}
    check.candidate_signature = _candidate_signature(db, batch.id, item)
    db.flush()
    approval = _approve(db, batch, item, route="direct_pool", review_items=[
        {"item_id": "answer-1", "answer": "A", "answer_match_type": "choice"},
    ])
    asset_id = uuid.UUID(item.payload["content"]["asset_ids"][0])
    target = get_settings().teacher_asset_dir / f"{uuid.uuid5(batch.id, str(asset_id)).hex}.png"

    def fail_promotion(*args, **kwargs):
        raise RuntimeError("simulated promotion failure")

    monkeypatch.setattr("app.services.question_import_s6_commit.promote_question", fail_promotion)
    with pytest.raises(RuntimeError, match="simulated promotion failure"):
        commit_import(db, batch.id, _request([(item, approval)]))
    assert not target.exists()
    assert not db.scalar(select(Question).where(
        Question.original_qid == f"bulk.{batch.id.hex}.{item.id.hex}"))


def test_commit_http_contract_and_idempotent_retry(db):
    batch, item, _ = _case(db)
    approval = _approve(db, batch, item)
    request = _request([(item, approval)])
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        url = f"/api/v1/question-imports/{batch.id}/s6/commit"
        first = client.post(url, json=request.model_dump(mode="json"))
        second = client.post(url, json=request.model_dump(mode="json"))
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert first.status_code == second.status_code == 200
    assert first.json()["count"] == 1
    assert second.json()["replayed"] is True
    assert second.json()["items"] == first.json()["items"]
