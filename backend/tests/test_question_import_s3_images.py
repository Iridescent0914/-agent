from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from uuid import UUID, uuid4

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import engine, get_db
from app.main import app
from app.services.question_import import ImportProblem, add_candidate, create_batch, purge_expired_import_files, update_candidate
from app.services.question_import_s2 import ModelQuestion
from app.services.question_import_s3_images import (
    extract_images, image_bytes, list_images, question_figure_assets, set_candidate_image,
    upload_candidate_image,
)
from app.services.question_import_worker import fake_candidate


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


def _pdf(*, pages: int = 1, image_page: int = 1) -> bytes:
    image = Image.new("RGB", (40, 30), "red")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    document = fitz.open()
    for index in range(1, pages + 1):
        page = document.new_page(width=300, height=300)
        if index == image_page:
            page.insert_image(fitz.Rect(20, 20, 100, 80), stream=buffer.getvalue())
            page.draw_line((150, 150), (240, 150), color=(0, 0, 1), width=2)
            page.draw_circle((200, 180), 20, color=(0, 0, 1))
    result = document.tobytes()
    document.close()
    return result


def _batch_with_candidate(db):
    batch = create_batch(db, source_mode="questions_only",
                         files=[("questions", "figures.pdf", _pdf())])
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    batch.checkpoint = {"s3": {"global_dedupe": {"status": "completed"}}}
    payload = fake_candidate(batch.id)
    payload["source"].update({"file_id": batch.files[0]["file_id"], "page_start": 1, "page_end": 1})
    payload["extraction"] = {"diagram_clues": ["数轴"]}
    candidate = add_candidate(db, batch, payload)
    return batch, candidate


def test_embedded_image_is_private_and_can_be_linked(db, private_dir):
    batch, candidate = _batch_with_candidate(db)
    first = extract_images(db, batch.id)
    assert first["page_count"] == 1
    assert len(first["assets"]) == 1
    embedded = first["assets"][0]
    assert embedded["kind"] == "embedded"
    assert embedded["page"] == 1
    assert embedded["sha256"]
    embedded_png = image_bytes(db, batch.id, UUID(embedded["asset_id"]))
    assert embedded_png.startswith(b"\x89PNG")
    with Image.open(BytesIO(embedded_png)) as rendered:
        red, green, blue = rendered.getpixel((rendered.width // 2, rendered.height // 2))
        assert red > 200 and green < 80 and blue < 80
    assert extract_images(db, batch.id)["assets"] == first["assets"]

    linked = set_candidate_image(db, batch.id, candidate.id, UUID(embedded["asset_id"]),
                                 expected_revision=1, linked=True)
    assert linked.payload["content"]["asset_ids"] == [embedded["asset_id"]]
    assert linked.revision == 2
    with pytest.raises(ImportProblem) as stale:
        set_candidate_image(db, batch.id, candidate.id, UUID(embedded["asset_id"]),
                            expected_revision=1, linked=False)
    assert stale.value.code == "REVISION_CONFLICT"
    with pytest.raises(ImportProblem) as forged:
        update_candidate(db, batch.id, candidate.id, expected_revision=2,
                         changes={"content": {**linked.payload["content"], "asset_ids": ["forged"]}})
    assert forged.value.code == "INVALID_CANDIDATE"

    batch.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert purge_expired_import_files(db) == 1
    assert not list(private_dir.rglob("*"))


def test_teacher_can_match_unassigned_image_from_later_answer_page(db, private_dir):
    batch = create_batch(db, source_mode="combined",
                         files=[("questions", "combined.pdf", _pdf(pages=2, image_page=2))])
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    batch.checkpoint = {"s3": {"global_dedupe": {"status": "completed"}}}
    payload = fake_candidate(batch.id)
    payload["source"].update({"file_id": batch.files[0]["file_id"], "page_start": 1, "page_end": 1})
    candidate = add_candidate(db, batch, payload)
    asset = extract_images(db, batch.id)["assets"][0]
    assert asset["page"] == 2
    assert list_images(db, batch.id)["assets"][0]["candidate_item_ids"] == []

    linked = set_candidate_image(db, batch.id, candidate.id, UUID(asset["asset_id"]),
                                 expected_revision=1, linked=True)
    assert linked.payload["content"]["asset_ids"] == [asset["asset_id"]]
    assert list_images(db, batch.id)["assets"][0]["candidate_item_ids"] == [str(candidate.id)]
    linked.payload = {**linked.payload, "source": {**linked.payload["source"], "file_id": str(uuid4())}}
    db.flush()
    with pytest.raises(ImportProblem) as wrong_file:
        set_candidate_image(db, batch.id, candidate.id, UUID(asset["asset_id"]),
                            expected_revision=2, linked=True)
    assert wrong_file.value.code == "IMAGE_SOURCE_CONFLICT"


def test_candidate_upload_survives_later_embedded_scan(db, private_dir):
    batch, candidate = _batch_with_candidate(db)
    image = BytesIO()
    Image.new("RGB", (32, 24), "blue").save(image, format="PNG")
    uploaded = upload_candidate_image(db, batch.id, candidate.id, image.getvalue(), expected_revision=1)
    uploaded_id = uploaded.payload["content"]["asset_ids"][0]
    first = list_images(db, batch.id)
    assert first["embedded_scanned"] is False
    assert [asset["asset_id"] for asset in first["assets"]] == [uploaded_id]
    assert list(private_dir.rglob("s3-image-*.png")) and len(list(private_dir.rglob("s3-image-*.png"))) == 1

    scanned = extract_images(db, batch.id)
    assert scanned["embedded_scanned"] is True
    assert {asset["kind"] for asset in scanned["assets"]} == {"upload", "embedded"}
    assert len(scanned["assets"]) == 2
    assert extract_images(db, batch.id)["assets"] == scanned["assets"]


def test_s2_model_reuses_embedded_image_and_crops_missing_vector_figure(db, private_dir):
    batch, _ = _batch_with_candidate(db)
    token = uuid4()
    batch.status = "parsing"
    batch.stage = "s2_processing"
    batch.worker_token = token
    db.flush()
    inventory = extract_images(db, batch.id, s2_worker_token=token)
    embedded_id = inventory["assets"][0]["asset_id"]
    question = ModelQuestion.model_validate({
        "question_text": "根据两幅图作答", "page_start": 1, "page_end": 1,
        "diagram_clues": ["红色图和蓝色图"], "figure_status": "complete",
        "figures": [{"page": 1, "asset_id": embedded_id, "bbox": None},
                    {"page": 1, "asset_id": None, "bbox": [450, 450, 850, 720]}],
    })
    ids, issues = question_figure_assets(db, batch.id, token, batch.files[0]["file_id"], 1, question)
    assert not issues
    assert ids[0] == embedded_id
    assert {asset["kind"] for asset in list_images(db, batch.id)["assets"]} == {"embedded", "model_crop"}
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    db.flush()
    assert image_bytes(db, batch.id, UUID(ids[1])).startswith(b"\x89PNG")

    batch.status = "parsing"
    batch.stage = "s2_processing"
    db.flush()
    invalid = question.model_copy(update={"figures": [question.figures[0].model_copy(update={"page": 2})]})
    assert question_figure_assets(db, batch.id, token, batch.files[0]["file_id"], 1, invalid)[0] == []


def test_s2_does_not_attach_a_scanned_page_as_one_question_figure(db, private_dir):
    image = Image.new("RGB", (300, 300), "white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    document = fitz.open()
    page = document.new_page(width=300, height=300)
    page.insert_image(page.rect, stream=buffer.getvalue())
    batch = create_batch(db, source_mode="questions_only",
                         files=[("questions", "scan.pdf", document.tobytes())])
    document.close()
    token = uuid4()
    batch.status = "parsing"
    batch.stage = "s2_processing"
    batch.worker_token = token
    db.flush()
    asset_id = extract_images(db, batch.id, s2_worker_token=token)["assets"][0]["asset_id"]
    question = ModelQuestion.model_validate({
        "question_text": "见图求解", "page_start": 1, "page_end": 1,
        "figure_status": "complete", "figures": [{"page": 1, "asset_id": asset_id}],
    })
    ids, issues = question_figure_assets(db, batch.id, token, batch.files[0]["file_id"], 1, question)
    assert ids == []
    assert any("整页扫描图" in issue for issue in issues)


def test_image_endpoints_upload_and_delete_per_candidate(db, private_dir):
    batch, candidate = _batch_with_candidate(db)
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        base = f"/api/v1/question-imports/{batch.id}/s3"
        images = client.post(base + "/images").json()
        assert len(images["assets"]) == 1
        assert client.get(base + "/pages/1/preview").status_code == 404
        assert client.post(base + "/pages/1/crop", json={"box": [0.8, 0.3, 0.2, 0.5]}).status_code == 404
        asset_id = images["assets"][0]["asset_id"]
        assert client.get(base + f"/images/{asset_id}/content").headers["content-type"] == "image/png"
        attached = client.post(base + f"/images/{asset_id}/items/{candidate.id}",
                               json={"expected_revision": 1, "linked": True})
        assert attached.status_code == 200, attached.text
        assert attached.json()["payload"]["content"]["asset_ids"] == [asset_id]
        uploaded_png = BytesIO()
        Image.new("RGB", (24, 16), "blue").save(uploaded_png, format="PNG")
        upload_url = base + f"/items/{candidate.id}/images"
        assert client.post(upload_url, data={"expected_revision": "2"},
                           files={"file": ("bad.txt", b"bad", "text/plain")}).status_code == 415
        uploaded = client.post(upload_url, data={"expected_revision": "2"},
                               files={"file": ("diagram.png", uploaded_png.getvalue(), "image/png")})
        assert uploaded.status_code == 200, uploaded.text
        uploaded_id = uploaded.json()["payload"]["content"]["asset_ids"][1]
        assert client.post(upload_url, data={"expected_revision": "2"},
                           files={"file": ("diagram.png", uploaded_png.getvalue(), "image/png")}).status_code == 409
        inventory = client.get(base + "/images").json()
        assert next(asset for asset in inventory["assets"] if asset["asset_id"] == uploaded_id)["kind"] == "upload"
        assert client.get(base + f"/images/{uploaded_id}/content").headers["content-type"] == "image/png"
        removed = client.post(base + f"/images/{uploaded_id}/items/{candidate.id}",
                              json={"expected_revision": 3, "linked": False})
        assert removed.status_code == 200, removed.text
        assert removed.json()["payload"]["content"]["asset_ids"] == [asset_id]
        assert all(asset["asset_id"] != uploaded_id for asset in client.get(base + "/images").json()["assets"])
        assert client.get("/assets/question_imports/" + asset_id).status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)
