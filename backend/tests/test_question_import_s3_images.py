from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from uuid import UUID

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import engine, get_db
from app.main import app
from app.services.question_import import ImportProblem, add_candidate, create_batch, purge_expired_import_files, update_candidate
from app.services.question_import_s3_images import (
    crop_page, extract_images, image_bytes, list_images, render_page, set_candidate_image,
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


def _pdf() -> bytes:
    image = Image.new("RGB", (40, 30), "red")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    document = fitz.open()
    page = document.new_page(width=300, height=300)
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
    payload = fake_candidate(batch.id)
    payload["source"].update({"file_id": batch.files[0]["file_id"], "page_start": 1, "page_end": 1})
    payload["extraction"] = {"diagram_clues": ["数轴"]}
    candidate = add_candidate(db, batch, payload)
    return batch, candidate


def test_embedded_image_and_vector_crop_are_private_and_reviewable(db, private_dir):
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
    assert render_page(db, batch.id, 1).startswith(b"\x89PNG")
    assert extract_images(db, batch.id)["assets"] == first["assets"]

    crop = crop_page(db, batch.id, 1, [0.45, 0.45, 0.85, 0.72])
    assert crop["kind"] == "crop"
    assert crop["page"] == 1
    assert crop["bbox"] == [0.45, 0.45, 0.85, 0.72]
    linked = set_candidate_image(db, batch.id, candidate.id, UUID(crop["asset_id"]),
                                 expected_revision=1, linked=True)
    assert linked.payload["content"]["asset_ids"] == [crop["asset_id"]]
    assert linked.revision == 2
    with pytest.raises(ImportProblem) as stale:
        set_candidate_image(db, batch.id, candidate.id, UUID(crop["asset_id"]),
                            expected_revision=1, linked=False)
    assert stale.value.code == "REVISION_CONFLICT"
    with pytest.raises(ImportProblem) as forged:
        update_candidate(db, batch.id, candidate.id, expected_revision=2,
                         changes={"content": {**linked.payload["content"], "asset_ids": ["forged"]}})
    assert forged.value.code == "INVALID_CANDIDATE"

    batch.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert purge_expired_import_files(db) == 1
    assert not list(private_dir.rglob("*"))


def test_crop_only_saves_selected_region_before_optional_embedded_scan(db, private_dir):
    batch, _ = _batch_with_candidate(db)
    crop = crop_page(db, batch.id, 1, [0.45, 0.45, 0.85, 0.72])
    first = list_images(db, batch.id)
    assert first["embedded_scanned"] is False
    assert [asset["asset_id"] for asset in first["assets"]] == [crop["asset_id"]]
    assert list(private_dir.rglob("s3-image-*.png")) and len(list(private_dir.rglob("s3-image-*.png"))) == 1

    scanned = extract_images(db, batch.id)
    assert scanned["embedded_scanned"] is True
    assert {asset["kind"] for asset in scanned["assets"]} == {"crop", "embedded"}
    assert len(scanned["assets"]) == 2
    assert extract_images(db, batch.id)["assets"] == scanned["assets"]


def test_image_endpoints_reject_invalid_box_and_other_page(db, private_dir):
    batch, candidate = _batch_with_candidate(db)
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        base = f"/api/v1/question-imports/{batch.id}/s3"
        images = client.post(base + "/images").json()
        assert len(images["assets"]) == 1
        assert client.get(base + "/pages/1/preview").headers["cache-control"] == "private, no-store"
        assert client.get(base + "/pages/2/preview").status_code == 404
        assert client.post(base + "/pages/1/crop", json={"box": [0.8, 0.3, 0.2, 0.5]}).status_code == 422
        asset_id = images["assets"][0]["asset_id"]
        assert client.get(base + f"/images/{asset_id}/content").headers["content-type"] == "image/png"
        attached = client.post(base + f"/images/{asset_id}/items/{candidate.id}",
                               json={"expected_revision": 1, "linked": True})
        assert attached.status_code == 200, attached.text
        assert attached.json()["payload"]["content"]["asset_ids"] == [asset_id]
        assert client.get("/assets/question_imports/" + asset_id).status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)
