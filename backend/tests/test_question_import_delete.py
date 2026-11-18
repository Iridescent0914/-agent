from __future__ import annotations

from io import BytesIO

import pytest
from fastapi.routing import APIRoute
from pypdf import PdfWriter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routes.question_imports import router
from app.core.config import get_settings
from app.core.database import engine
from app.models import QuestionImportBatch, QuestionImportItem
from app.services.question_import import (
    ImportProblem, add_candidate, cancel_batch, create_batch, delete_cancelled_batch,
)


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


def _pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_delete_cancelled_batch_removes_record_items_and_every_staged_file(db, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "question_import_dir", tmp_path / "imports")
    batch = create_batch(db, source_mode="separate", files=[
        ("questions", "questions.pdf", _pdf()), ("answers", "answers.pdf", _pdf())])
    source_id = next(file["file_id"] for file in batch.files if file["role"] == "questions")
    add_candidate(db, batch, {
        "source": {"file_id": source_id, "page_start": 1, "page_end": 1, "original_number": "1"},
        "content": {"question_text": "计算 1+1", "options": []},
        "answers": [{"text": "2", "answer_source": "source_extracted"}],
        "tags": {"question_type": "calculation", "difficulty": 1},
    })
    batch.checkpoint = {"s2": {"units": [{"source_answers": [{"text": "2"}]}]},
                        "s3": {"labels": {"status": "completed"}}}
    folder = get_settings().question_import_dir / str(batch.id)
    (folder / "model.unit0.json").write_text('{"answer":"2"}', encoding="utf-8")
    (folder / "s3-image.png").write_bytes(b"image")
    (folder / "nested").mkdir()
    (folder / "nested" / "response.json").write_text("{}", encoding="utf-8")
    cancel_batch(db, batch.id)

    delete_cancelled_batch(db, batch.id)

    assert db.get(QuestionImportBatch, batch.id) is None
    assert db.scalar(select(func.count(QuestionImportItem.id)).where(
        QuestionImportItem.batch_id == batch.id)) == 0
    assert not folder.exists()


def test_delete_batch_rejects_non_cancelled_status(db, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "question_import_dir", tmp_path / "imports")
    batch = create_batch(db, source_mode="questions_only", files=[
        ("questions", "questions.pdf", _pdf())])
    folder = get_settings().question_import_dir / str(batch.id)

    with pytest.raises(ImportProblem) as error:
        delete_cancelled_batch(db, batch.id)

    assert error.value.code == "COMMIT_CONFLICT"
    assert db.get(QuestionImportBatch, batch.id) is not None
    assert folder.is_dir()
    assert any(route.path.endswith("/{batch_id}") and "DELETE" in (route.methods or set())
               for route in router.routes if isinstance(route, APIRoute))
