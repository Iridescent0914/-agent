from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import engine, get_db
from app.api.routes.question_imports import list_items
from app.main import app
from app.models import QuestionImportBatch
from app.services.question_import import (
    ImportProblem, active_candidate, add_candidate, create_batch, create_teacher_candidate,
    delete_teacher_candidate, purge_expired_import_files, update_candidate,
)
from app.services.question_import_worker import claim_one, fake_candidate, heartbeat


@pytest.fixture
def db():
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, expire_on_commit=False,
                          join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()


@pytest.fixture
def private_dir(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    return settings.question_import_dir


def _pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_upload_is_idempotent_and_files_stay_private(db, private_dir):
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        body = {"source_mode": "questions_only"}
        files = {"question_file": ("sample.pdf", _pdf(), "application/pdf")}
        first = client.post("/api/v1/question-imports", data=body, files=files)
        assert first.status_code == 201, first.text
        second = client.post("/api/v1/question-imports", data=body, files=files)
        assert second.status_code == 200, second.text
        assert second.json()["id"] == first.json()["id"]
        batch = first.json()
        retention = datetime.fromisoformat(batch["expires_at"]) - datetime.fromisoformat(batch["created_at"])
        assert abs(retention.total_seconds() - get_settings().question_import_retention_hours * 3600) < 5
        assert batch["files"][0]["page_count"] == 1
        assert batch["stage"] == "files_saved"
        assert len(list(private_dir.rglob("*"))) == 2  # one directory, one file
        assert client.get("/assets/question_imports/" + batch["files"][0]["file_id"]).status_code == 404
        assert len(client.get("/api/v1/question-imports").json()) >= 1
        assert client.get(f"/api/v1/question-imports/{batch['id']}/items").json() == []
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_file_guard_and_candidate_revision(db, private_dir):
    with pytest.raises(ImportProblem) as invalid:
        create_batch(db, source_mode="questions_only", files=[("questions", "bad.pdf", b"not pdf")])
    assert invalid.value.code == "INVALID_FILE"
    batch = create_batch(db, source_mode="questions_only",
                         files=[("questions", "valid.pdf", _pdf())])
    candidate = add_candidate(db, batch, fake_candidate(batch.id), fake=True)
    assert candidate.is_fake is True
    assert candidate.payload["tags"]["is_required"] is False
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    batch.checkpoint = {"s3": {"global_dedupe": {"status": "completed"}}}
    changed = update_candidate(db, batch.id, candidate.id, expected_revision=1,
                               changes={"content": {"question_text": "已修改", "options": [], "solution": None, "asset_ids": []}})
    assert changed.revision == 2
    with pytest.raises(ImportProblem) as stale:
        update_candidate(db, batch.id, candidate.id, expected_revision=1,
                         changes={"teacher_decision": "approved"})
    assert stale.value.code == "REVISION_CONFLICT"
    with pytest.raises(ImportProblem) as derived:
        update_candidate(db, batch.id, candidate.id, expected_revision=2,
                         changes={"tags": {"chapter_code": "forged"}})
    assert derived.value.code == "TAG_CONFLICT"


def test_teacher_edit_preserves_source_answer_and_checks_revision(db, private_dir):
    batch = create_batch(db, source_mode="combined",
                         files=[("questions", "teacher-edit.pdf", _pdf())])
    batch.stage = "s2_completed"
    batch.status = "review_pending"
    batch.checkpoint = {"s3": {"global_dedupe": {"status": "completed"}}}
    candidate = add_candidate(db, batch, fake_candidate(batch.id), fake=False)
    source_answer = {"answer_id": "source-1", "answer_source": "source_extracted",
                     "text": "原资料答案", "evidence": [{"page": 1}], "teacher_confirmed": False}
    candidate.payload = {**candidate.payload, "answers": [source_answer],
                         "selected_answer_id": source_answer["answer_id"],
                         "teacher_decision": "approved"}
    db.flush()

    content = {**candidate.payload["content"], "question_text": "教师修正后的题干"}
    updated = update_candidate(db, batch.id, candidate.id, expected_revision=1,
                               changes={"content": content, "teacher_answer_text": "教师修正后的答案"})
    assert updated.revision == 2
    assert updated.payload["content"]["question_text"] == "教师修正后的题干"
    assert updated.payload["answers"][0] == source_answer
    teacher_answer = updated.payload["answers"][1]
    assert teacher_answer["answer_source"] == "teacher_entered"
    assert teacher_answer["text"] == "教师修正后的答案"
    assert teacher_answer["teacher_confirmed"] is False
    assert updated.payload["selected_answer_id"] == teacher_answer["answer_id"]
    assert updated.payload["answer_match_status"] == "uncertain"
    assert updated.payload["teacher_decision"] == "pending"

    with pytest.raises(ImportProblem) as stale:
        update_candidate(db, batch.id, candidate.id, expected_revision=1,
                         changes={"teacher_answer_text": "过期修改"})
    assert stale.value.code == "REVISION_CONFLICT"


def test_teacher_add_delete_after_combined_dedupe(db, private_dir):
    batch = create_batch(db, source_mode="combined",
                         files=[("questions", "manual-combined.pdf", _pdf())])
    with pytest.raises(ImportProblem) as early:
        create_teacher_candidate(db, batch.id, question_text="新增题干", options=[])
    assert early.value.code == "COMMIT_CONFLICT"
    original = add_candidate(db, batch, fake_candidate(batch.id))
    original_answer = {"answer_id": "source-unchanged", "answer_source": "source_extracted",
                       "text": "既有资料答案", "teacher_confirmed": False}
    original.payload = {**original.payload, "answers": [original_answer]}
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    batch.checkpoint = {"s3": {"global_dedupe": {"status": "completed"},
                               "labels": {"status": "completed"},
                               "source_answer_reconciliation": {"linked_question_count": 1,
                                                                "unassigned_answers": []}}}

    created = create_teacher_candidate(db, batch.id, question_text="新增题干",
                                       options=[{"key": "A", "text": "选项一"}],
                                       answer_text="教师答案", original_number="17", source_page=1)
    assert created.payload["source"]["original_number"] == "17"
    assert created.payload["source"]["page_start"] == 1
    assert created.payload["answers"][0]["answer_source"] == "teacher_entered"
    assert created.payload["selected_answer_id"] == created.payload["answers"][0]["answer_id"]
    assert created.payload["tags"]["question_type"] == "unknown"
    assert active_candidate(created)
    assert [item.id for item in list_items(batch.id, db)] == [original.id, created.id]
    assert original.payload["answers"] == [original_answer]
    assert batch.checkpoint["s3"]["source_answer_reconciliation"]["manual_candidate_changes_pending"] is True
    with pytest.raises(ImportProblem) as stale:
        delete_teacher_candidate(db, batch.id, created.id, expected_revision=2)
    assert stale.value.code == "REVISION_CONFLICT"
    delete_teacher_candidate(db, batch.id, created.id, expected_revision=1)
    assert not active_candidate(created)
    assert created.payload["teacher_deleted_at"]
    assert [item.id for item in list_items(batch.id, db)] == [original.id]
    assert original.payload["answers"] == [original_answer]
    with pytest.raises(ImportProblem) as missing:
        update_candidate(db, batch.id, created.id, expected_revision=2,
                         changes={"teacher_answer_text": "不应再编辑"})
    assert missing.value.code == "NOT_FOUND"


def test_separate_candidate_changes_wait_for_answer_extraction(db, private_dir):
    batch = create_batch(db, source_mode="separate", files=[
        ("questions", "manual-questions.pdf", _pdf()),
        ("answers", "manual-answers.pdf", _pdf()),
    ])
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    batch.checkpoint = {"s3": {"global_dedupe": {"status": "completed"}}}
    candidate = add_candidate(db, batch, fake_candidate(batch.id))
    content = {**candidate.payload["content"], "question_text": "修正后的题干"}
    with pytest.raises(ImportProblem) as early_edit:
        update_candidate(db, batch.id, candidate.id, expected_revision=1,
                         changes={"content": content})
    assert early_edit.value.code == "COMMIT_CONFLICT"
    with pytest.raises(ImportProblem) as early_add:
        create_teacher_candidate(db, batch.id, question_text="新增题干", options=[])
    assert early_add.value.code == "COMMIT_CONFLICT"
    with pytest.raises(ImportProblem) as early_delete:
        delete_teacher_candidate(db, batch.id, candidate.id, expected_revision=1)
    assert early_delete.value.code == "COMMIT_CONFLICT"

    batch.checkpoint = {"s3": {"global_dedupe": {"status": "completed"},
                               "separate_answers": {"status": "completed"}}}
    updated = update_candidate(db, batch.id, candidate.id, expected_revision=1,
                               changes={"content": content, "teacher_answer_text": "修正后的答案"})
    assert updated.revision == 2
    created = create_teacher_candidate(db, batch.id, question_text="新增题干", options=[])
    assert active_candidate(created)
    delete_teacher_candidate(db, batch.id, candidate.id, expected_revision=2)
    assert not active_candidate(candidate)


def test_worker_lease_and_expiration(db, private_dir):
    batch = create_batch(db, source_mode="questions_only",
                         files=[("questions", "lease.pdf", _pdf())])
    claimed = claim_one(db)
    assert claimed is not None and claimed[0] == batch.id
    assert claim_one(db) is None
    heartbeat(db, *claimed)
    assert batch.lease_until is not None
    next_claim = claim_one(db, now=batch.lease_until + timedelta(seconds=1))
    assert next_claim is not None and next_claim[1] != claimed[1]
    batch.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    removed = purge_expired_import_files(db)
    assert removed == 1
    assert batch.status == "failed"
    assert batch.error_code == "FILE_EXPIRED"
    assert batch.files_purged_at is not None
    assert not list(private_dir.rglob("*"))
