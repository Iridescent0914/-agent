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
from app.main import app
from app.models import QuestionImportBatch
from app.services.question_import import (
    ImportProblem, add_candidate, create_batch, purge_expired_import_files,
    update_candidate,
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
        assert second.status_code == 201, second.text
        assert second.json()["id"] == first.json()["id"]
        batch = first.json()
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
