from datetime import timedelta
from io import BytesIO
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.database import get_db
from app.main import app
from app.models import AnswerPageCollection, AnswerPageEvent, AnswerPageImage, Assignment
from app.schemas.answer_pages import CreatePageCollection, SelectPageImage
from app.services.answer_card_artifacts import create_artifact
from app.services import answer_pages as service
from app.services.assessment import AssessmentProblem, now
from tests.test_stage6_s1 import context, protected_counts


def picture(color="white", *, fmt="PNG", exif=False):
    out = BytesIO()
    image = Image.new("RGB", (120, 180), color)
    kwargs = {}
    if exif:
        metadata = Image.Exif()
        metadata[274] = 6
        metadata[315] = "PRIVATE_AUTHOR"
        kwargs["exif"] = metadata
    image.save(out, format=fmt, **kwargs)
    return out.getvalue()


def mutation(revision=0, image_id=None):
    return SelectPageImage(expected_revision_no=revision, image_id=image_id, reviewer="lin-test", reason="核对纸面页码")


def setup(context):
    db, _, paper, _, _ = context
    artifact, _ = create_artifact(db, paper.id, 1)
    request = CreatePageCollection(artifact_code=artifact.artifact_code, instance_code=paper.instance_code, expected_version_no=1)
    result = service.create_collection(db, paper.id, artifact.id, request)
    return db, paper, artifact, result


def test_create_collection_is_scoped_idempotent_and_has_no_grading_effects(context):
    db, _, paper, _, _ = context
    before = protected_counts(db)
    db, paper, artifact, first = setup(context)
    request = CreatePageCollection(artifact_code=artifact.artifact_code, instance_code=paper.instance_code, expected_version_no=1)
    assert service.create_collection(db, paper.id, artifact.id, request)["id"] == first["id"]
    assert first["missing_page_nos"] == list(range(1, artifact.total_pages + 1))
    assert not first["collection_ready"] and not first["can_submit"]
    assert first["recognition_status"] == "not_started"
    assert db.scalar(select(func.count()).select_from(AnswerPageCollection)) == 1
    assert protected_counts(db) == before
    for change in [{"artifact_code": "OTHER"}, {"instance_code": "OTHER"}, {"expected_version_no": 2}]:
        bad = request.model_copy(update=change)
        with pytest.raises(AssessmentProblem, match="不符"):
            service.create_collection(db, paper.id, artifact.id, bad)
    with pytest.raises(AssessmentProblem) as error:
        service.collection_view(db, uuid4(), artifact.id)
    assert error.value.status_code == 404


def test_upload_replay_new_capture_and_old_duplicate_do_not_undo_latest(context):
    db, paper, artifact, initial = setup(context)
    before = protected_counts(db)
    first = service.upload_image(db, paper.id, artifact.id, 1, mutation(), picture())
    first_id = first["pages"][0]["effective_image_id"]
    duplicate = service.upload_image(db, paper.id, artifact.id, 1, mutation(), picture())
    assert duplicate["reused"] and duplicate["revision_no"] == 1
    second = service.upload_image(db, paper.id, artifact.id, 1, mutation(1), picture("red"))
    assert second["revision_no"] == 2 and second["pages"][0]["effective_image_id"] != first_id
    old = service.upload_image(db, paper.id, artifact.id, 1, mutation(2), picture())
    assert old["reused"] and old["revision_no"] == 2 and "notice" in old
    assert old["pages"] == second["pages"]
    selected = service.select_image(db, paper.id, artifact.id, 1, mutation(2, first_id))
    assert selected["revision_no"] == 3 and selected["pages"][0]["effective_image_id"] == first_id
    replay = service.select_image(db, paper.id, artifact.id, 1, mutation(2, first_id))
    assert replay["revision_no"] == 3
    assert len(selected["history"]) == 3
    assert len(selected["images"]) == 2
    assert initial["effective_input_sha256"] != first["effective_input_sha256"] != selected["effective_input_sha256"]
    assert protected_counts(db) == before


def test_out_of_order_pages_missing_is_not_blank_and_one_image_cannot_fill_two_pages(context):
    # Add enough units to force three pages, while keeping the published source
    # fixture and DB snapshots untouched; this test uses a render fixture artifact.
    db, _, paper, _, _ = context
    from tests.test_stage6_s3_artifacts import render_fixture
    from app.models import AnswerCardArtifact
    from app.services.assessment_protocol import input_hash
    import hashlib
    _, pdf, manifest = render_fixture(many=True)
    manifest.update(paper_instance_id=str(paper.id), instance_code=paper.instance_code, version_no=1)
    artifact = AnswerCardArtifact(id=uuid4(), paper_instance_id=paper.id, revision_no=1,
        artifact_code=uuid4().hex[:16].upper(), dedup_key=uuid4().hex, source_sha256="a" * 64,
        pdf_sha256=hashlib.sha256(pdf).hexdigest(), manifest_sha256=input_hash(manifest), layout_version="answer-card-v3",
        renderer_version="test", total_pages=manifest["total_pages"], manifest=manifest, pdf_data=pdf)
    db.add(artifact); db.flush()
    service.create_collection(db, paper.id, artifact.id, CreatePageCollection(
        artifact_code=artifact.artifact_code, instance_code=paper.instance_code, expected_version_no=1))
    uploaded = service.upload_image(db, paper.id, artifact.id, 3, mutation(), picture("blue"))
    assert 3 not in uploaded["missing_page_nos"] and 1 in uploaded["missing_page_nos"]
    with pytest.raises(AssessmentProblem, match="另一答题页"):
        service.upload_image(db, paper.id, artifact.id, 1, mutation(1), picture("blue"))
    removed = service.select_image(db, paper.id, artifact.id, 3, mutation(1))
    assert 3 in removed["missing_page_nos"]
    moved = service.upload_image(db, paper.id, artifact.id, 1, mutation(2), picture("blue"))
    assert moved["reused"] and 1 not in moved["missing_page_nos"]
    revision = moved["revision_no"]
    for number in moved["missing_page_nos"]:
        moved = service.upload_image(db, paper.id, artifact.id, number, mutation(revision), picture((number * 20, 20, 20)))
        revision = moved["revision_no"]
    assert moved["collection_ready"] and moved["status"] == "collected"
    assert not moved["can_submit"] and moved["recognition_status"] == "not_started"


def test_revision_conflict_and_foreign_images_are_rejected(context):
    db, paper, artifact, _ = setup(context)
    uploaded = service.upload_image(db, paper.id, artifact.id, 1, mutation(), picture())
    with pytest.raises(AssessmentProblem, match="已改变"):
        service.upload_image(db, paper.id, artifact.id, 1, mutation(), picture("red"))
    with pytest.raises(AssessmentProblem, match="已改变"):
        service.select_image(db, paper.id, artifact.id, 1, mutation())
    with pytest.raises(AssessmentProblem) as error:
        service.select_image(db, paper.id, artifact.id, 1, mutation(1, uuid4()))
    assert error.value.status_code == 404
    for number in [0, artifact.total_pages + 1]:
        with pytest.raises(AssessmentProblem, match="超出范围"):
            service.upload_image(db, paper.id, artifact.id, number, mutation(1), picture("red"))
    assert service.collection_view(db, paper.id, artifact.id)["pages"] == uploaded["pages"]


def test_persisted_history_survives_sessions_and_old_artifact_is_not_rebound(context):
    db, paper, artifact, _ = setup(context)
    factory = context[1]
    first = service.upload_image(db, paper.id, artifact.id, 1, mutation(), picture())
    db.commit()
    assignment = db.get(Assignment, paper.assignment_id)
    assignment.title = "新标题产生新打印产物"; db.flush()
    newer, _ = create_artifact(db, paper.id, 1)
    assert newer.id != artifact.id
    assert service.collection_view(db, paper.id, newer.id) is None
    db.commit()
    with factory() as another:
        saved = service.collection_view(another, paper.id, artifact.id)
        assert saved["id"] == first["id"] and saved["pages"] == first["pages"]
        data = service.read_image(another, paper.id, artifact.id, first["images"][0]["id"])
        assert data.startswith(b"\xff\xd8")
        with pytest.raises(AssessmentProblem):
            service.read_image(another, paper.id, newer.id, first["images"][0]["id"])


def test_expiry_blocks_read_and_completeness_before_physical_purge_then_reupload_restores(context):
    db, paper, artifact, _ = setup(context)
    first = service.upload_image(db, paper.id, artifact.id, 1, mutation(), picture())
    image = db.get(AnswerPageImage, first["images"][0]["id"])
    image.retention_until = now() - timedelta(seconds=1)
    db.flush()
    expired = service.collection_view(db, paper.id, artifact.id)
    assert expired["pages"][0]["status"] == "expired" and 1 in expired["missing_page_nos"]
    assert expired["effective_input_sha256"] != first["effective_input_sha256"]
    with pytest.raises(AssessmentProblem) as error:
        service.read_image(db, paper.id, artifact.id, image.id)
    assert error.value.status_code == 410
    assert service.purge_expired_page_images(db) == 1
    assert image.image_data is None
    assert service.purge_expired_page_images(db) == 0
    restored = service.upload_image(db, paper.id, artifact.id, 1, mutation(1), picture())
    assert restored["revision_no"] == 2 and restored["history"][0]["action"] == "restore"
    assert restored["pages"][0]["status"] == "uploaded" and len(restored["images"]) == 1


def test_invalid_images_limits_exif_and_integrity(context, monkeypatch):
    db, paper, artifact, _ = setup(context)
    for data in [b"", b"not an image", picture(fmt="GIF")]:
        with pytest.raises(AssessmentProblem) as error:
            service.upload_image(db, paper.id, artifact.id, 1, mutation(), data)
        assert error.value.status_code == 422
    result = service.upload_image(db, paper.id, artifact.id, 1, mutation(), picture(exif=True))
    image = result["images"][0]
    assert (image["width"], image["height"]) == (180, 120)
    data = service.read_image(db, paper.id, artifact.id, image["id"])
    with Image.open(BytesIO(data)) as decoded:
        assert not decoded.getexif()
    row = db.get(AnswerPageImage, image["id"])
    original = row.image_data; row.image_data = b"corrupt"
    with pytest.raises(AssessmentProblem, match="校验失败"):
        service.read_image(db, paper.id, artifact.id, image["id"])
    row.image_data = original
    monkeypatch.setattr(service, "MAX_STORED_BYTES", 1)
    with pytest.raises(AssessmentProblem, match="保存上限"):
        service.upload_image(db, paper.id, artifact.id, 1, mutation(1), picture("red"))


def test_invalidated_paper_history_readable_but_cannot_write(context):
    db, paper, artifact, _ = setup(context)
    first = service.upload_image(db, paper.id, artifact.id, 1, mutation(), picture())
    paper.invalidated_at = now(); db.flush()
    history = service.collection_view(db, paper.id, artifact.id)
    assert not history["is_active"] and not history["collection_ready"]
    assert service.read_image(db, paper.id, artifact.id, first["images"][0]["id"])
    with pytest.raises(AssessmentProblem, match="失效"):
        service.upload_image(db, paper.id, artifact.id, 1, mutation(1), picture("red"))
    with pytest.raises(AssessmentProblem, match="失效"):
        service.select_image(db, paper.id, artifact.id, 1, mutation(1))


@pytest.mark.parametrize("limit", ["pixels", "bytes", "image_count"])
def test_upload_resource_limits(context, monkeypatch, limit):
    from app.core.config import get_settings
    db, paper, artifact, _ = setup(context)
    if limit == "pixels":
        monkeypatch.setattr(get_settings(), "recognition_max_pixels", 100)
    elif limit == "bytes":
        monkeypatch.setattr(get_settings(), "recognition_max_upload_bytes", 10)
    else:
        monkeypatch.setattr(service, "MAX_IMAGES", 0)
    with pytest.raises(AssessmentProblem):
        service.upload_image(db, paper.id, artifact.id, 1, mutation(), picture())
    assert service.collection_view(db, paper.id, artifact.id)["revision_no"] == 0


def test_internal_gate_blocks_collection_and_upload_without_model_calls(context, monkeypatch):
    from app.core.config import get_settings
    db, paper, artifact, _ = setup(context)
    monkeypatch.setattr(get_settings(), "stage6_enabled", False)
    with pytest.raises(AssessmentProblem) as error:
        service.collection_view(db, paper.id, artifact.id)
    assert error.value.status_code == 403
    monkeypatch.setattr(get_settings(), "stage6_enabled", True)
    monkeypatch.setattr(get_settings(), "app_env", "production")
    with pytest.raises(AssessmentProblem) as error:
        service.collection_view(db, paper.id, artifact.id)
    assert error.value.status_code == 403


def test_event_history_immutable(context):
    db, paper, artifact, _ = setup(context)
    service.upload_image(db, paper.id, artifact.id, 1, mutation(), picture())
    db.commit()
    for sql in ["UPDATE answer_page_events SET reason=reason", "DELETE FROM answer_page_events"]:
        with pytest.raises(DBAPIError, match="immutable"):
            with db.begin_nested():
                db.execute(text(sql))


def test_api_collection_upload_selection_and_image_scope(context):
    db, _, paper, _, _ = context
    artifact, _ = create_artifact(db, paper.id, 1)
    db.flush()
    before = protected_counts(db)
    app.dependency_overrides[get_db] = lambda: db
    prefix = f"/api/v1/internal/stage6/paper-instances/{paper.id}/answer-card-artifacts/{artifact.id}/page-collection"
    try:
        client = TestClient(app)  # no lifespan workers, no model requests
        assert client.get(prefix).json() is None
        result = client.post(prefix, json={"artifact_code": artifact.artifact_code,
            "instance_code": paper.instance_code, "expected_version_no": 1})
        assert result.status_code == 200, result.text
        form = {"expected_revision_no": "0", "reviewer": "test-lin", "reason": "首次上传"}
        response = client.post(prefix + "/pages/1/images", data=form, files={"file": ("photo.png", picture(), "image/png")})
        assert response.status_code == 200, response.text
        image = response.json()["images"][0]
        assert client.get(image["image_url"]).headers["content-type"] == "image/jpeg"
        assert client.get(image["image_url"].replace(str(paper.id), str(uuid4()))).status_code == 404
        whitespace = {**form, "reviewer": "   "}
        assert client.post(prefix + "/pages/1/images", data=whitespace, files={"file": ("x.png", picture(), "image/png")}).status_code == 422
        selected = client.put(prefix + "/pages/1/effective-image", json=mutation(1).model_dump(mode="json"))
        assert selected.status_code == 200 and 1 in selected.json()["missing_page_nos"]
        assert protected_counts(db) == before
    finally:
        app.dependency_overrides.pop(get_db, None)
