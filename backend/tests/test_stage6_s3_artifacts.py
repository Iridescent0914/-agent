from copy import deepcopy
import hashlib
from uuid import uuid4

from fastapi.testclient import TestClient
import pymupdf as fitz
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.database import get_db
from app.main import app
from app.models import AnswerCardArtifact, Assignment
from app.services.assessment import AssessmentProblem
from app.services.assessment_protocol import input_hash
from app.services.answer_card_artifacts import artifact_page_svg, artifact_pdf, artifact_view, create_artifact, get_artifact
from app.services.answer_card_renderer import render_answer_card
from app.services.vision_preparation import inventory_from_snapshots
from tests.test_stage6_s1 import context, protected_counts
from tests.test_stage6_s3_preparation import snapshots


def render_fixture(*, many=False, assignment_type="practice"):
    paper, items = snapshots()
    if many:
        items[1].answer_snapshot["items"] = [
            {"item_id": f"sub_{i}.blank_{j}", "answer": "PRIVATE_ANSWER"}
            for i in range(1, 23) for j in range(1, 5)
        ]
    inventory = inventory_from_snapshots(paper, items)
    before = inventory.model_dump()
    pdf, manifest = render_answer_card(
        inventory, assignment_title="多页识别答题卡检查", student_code="S001", assignment_type=assignment_type,
        artifact_id=uuid4(), artifact_code="A123456789ABCDEF", revision_no=1, source_sha256=input_hash("fixture"),
    )
    assert before == inventory.model_dump()
    return inventory, pdf, manifest


def test_pdf_manifest_has_exact_disjoint_coverage_and_visible_identity_on_every_page():
    inventory, pdf, manifest = render_fixture(many=True)
    assert manifest["layout_version"] == "answer-card-v3"
    assert manifest["total_pages"] > 1
    actual = [slot for page in manifest["pages"] for slot in page["slots"]]
    assert len(actual) == len({s["slot_key"] for s in actual}) == len(inventory.slots)
    assert {s["slot_key"] for s in actual} == {s.slot_key for s in inventory.slots}
    assert len({p["page_token"] for p in manifest["pages"]}) == manifest["total_pages"]
    assert len({s["slot_token"] for s in actual}) == len(actual)
    with fitz.open(stream=pdf, filetype="pdf") as document:
        assert len(document) == manifest["total_pages"]
        for page, spec in zip(document, manifest["pages"], strict=True):
            page_text = page.get_text()
            assert "PRIVATE_" not in page_text
            assert "RAT-7K3M-9Q2H" in page_text and "S001" in page_text
            assert "A123456789ABCDEF" in page_text and "v1" in page_text
            assert f"{spec['page_no']} / {len(document)}" in page_text
            assert "answer-card-v3" in page_text
            boxes = []
            for slot in spec["slots"]:
                box = fitz.Rect(slot["writing_bbox_pt"])
                assert page.rect.contains(box) and box.height >= 40 and box.width >= 120
                assert all((box & other).is_empty for other in boxes)
                boxes.append(box)
        all_text = "".join(page.get_text() for page in document)
        assert all_text.index("必做题") < all_text.index("个性化") < all_text.index("历史回顾")
        assert "选项" not in all_text and "答案" not in all_text


def test_empty_modules_and_manual_writing_area_are_not_dropped():
    paper, items = snapshots()
    items = items[1:2]
    items[0].selection_audit = {"source_module": "required"}
    items[0].answer_snapshot["items"][0]["answer_match_type"] = "manual"
    inventory = inventory_from_snapshots(paper, items)
    pdf, manifest = render_answer_card(inventory, assignment_title="人工题", student_code="S001", assignment_type="practice",
        artifact_id=uuid4(), artifact_code="B123456789ABCDEF", revision_no=1, source_sha256=input_hash("fixture"))
    slots = [s for p in manifest["pages"] for s in p["slots"]]
    assert len(slots) == 2
    manual = next(s for s in slots if s["blocker"] == "MANUAL_SLOT")
    assert fitz.Rect(manual["writing_bbox_pt"]).height > 80
    with fitz.open(stream=pdf, filetype="pdf") as document:
        text_value = "".join(p.get_text() for p in document)
        assert "本次无个性化" in text_value and "本次无历史回顾" in text_value


def test_persisted_artifact_is_reused_and_download_bytes_never_rerender(context, monkeypatch):
    db, _, paper, item, _ = context
    before = protected_counts(db)
    artifact, reused = create_artifact(db, paper.id, 1)
    assert not reused
    original = artifact_pdf(artifact)
    assert artifact.pdf_sha256 == hashlib.sha256(original).hexdigest()
    assert artifact.manifest_sha256 == input_hash(artifact.manifest)
    db.commit()
    db.expire_all()
    from app.services import answer_card_artifacts as service
    monkeypatch.setattr(service, "render_answer_card", lambda *args, **kwargs: pytest.fail("download must reuse saved PDF"))
    existing, reused = create_artifact(db, paper.id, 1)
    assert reused and existing.id == artifact.id
    assert artifact_pdf(get_artifact(db, paper.id, artifact.id)) == original
    assert db.scalar(select(func.count()).select_from(AnswerCardArtifact)) == 1
    assert protected_counts(db) == before
    assert "PRIVATE_" not in str(existing.manifest)


def test_title_change_creates_new_revision_without_overwriting_previous_pdf(context):
    db, _, paper, _, _ = context
    first, _ = create_artifact(db, paper.id, 1)
    previous = artifact_pdf(first)
    assignment = db.get(Assignment, paper.assignment_id)
    assignment.title = "更新标题"
    db.flush()
    second, reused = create_artifact(db, paper.id, 1)
    assert not reused and second.id != first.id and second.revision_no == 2
    assert first.source_sha256 != second.source_sha256
    assert artifact_pdf(get_artifact(db, paper.id, first.id)) == previous


def test_artifacts_cannot_be_updated_or_deleted_even_via_sql(context):
    db, _, paper, _, _ = context
    artifact, _ = create_artifact(db, paper.id, 1)
    db.commit()
    for sql in ["UPDATE answer_card_artifacts SET total_pages=total_pages WHERE id=:id",
                "DELETE FROM answer_card_artifacts WHERE id=:id"]:
        with pytest.raises(DBAPIError, match="immutable"):
            with db.begin_nested():
                db.execute(text(sql), {"id": artifact.id})


def test_stale_version_withdrawal_and_cross_paper_download_are_blocked(context):
    db, _, paper, _, _ = context
    with pytest.raises(AssessmentProblem, match="版本"):
        create_artifact(db, paper.id, 2)
    artifact, _ = create_artifact(db, paper.id, 1)
    with pytest.raises(AssessmentProblem) as error:
        get_artifact(db, uuid4(), artifact.id)
    assert error.value.status_code == 404
    from app.services.assessment import now
    paper.invalidated_at = now()
    db.flush()
    with pytest.raises(AssessmentProblem, match="失效"):
        create_artifact(db, paper.id, 1)
    # Historical artifact bytes remain inspectable, without republishing.
    assert artifact_pdf(get_artifact(db, paper.id, artifact.id)).startswith(b"%PDF")


def test_corrupt_manifest_or_pdf_is_rejected_before_download(context):
    db, _, paper, _, _ = context
    artifact, _ = create_artifact(db, paper.id, 1)
    original = artifact.pdf_data
    artifact.pdf_data = b"corrupt"
    with pytest.raises(AssessmentProblem, match="PDF校验"):
        artifact_pdf(artifact)
    artifact.pdf_data = original
    # No flush: simulate a corrupted in-memory value rather than bypassing the immutable table.
    manifest = deepcopy(artifact.manifest)
    manifest["total_pages"] += 1
    artifact.manifest = manifest
    with pytest.raises(AssessmentProblem, match="页清单校验"):
        with db.no_autoflush:
            get_artifact(db, paper.id, artifact.id)
    db.rollback()


def test_api_create_list_manifest_and_download_have_no_grading_side_effects(context):
    db, _, paper, _, _ = context
    app.dependency_overrides[get_db] = lambda: db
    before = protected_counts(db)
    try:
        client = TestClient(app)  # no lifespan/background workers
        prefix = f"/api/v1/internal/stage6/paper-instances/{paper.id}/answer-card-artifacts"
        response = client.post(prefix, json={"expected_version_no": 1})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["layout_version"] == "answer-card-v3"
        assert client.post(prefix, json={"expected_version_no": 1}).json()["reused"]
        assert len(client.get(prefix).json()) == 1
        download = client.get(result["pdf_url"])
        assert download.headers["content-type"] == "application/pdf"
        assert hashlib.sha256(download.content).hexdigest() == result["pdf_sha256"]
        assert client.get(result["manifest_url"]).json()["artifact_id"] == result["id"]
        image = client.get(prefix + f"/{result['id']}/pages/1/image")
        assert image.status_code == 200 and image.headers["content-type"].startswith("image/svg+xml")
        assert b"<svg" in image.content and b"<script" not in image.content
        assert client.get(prefix + f"/{result['id']}/pages/0/image").status_code == 404
        assert client.get(prefix + f"/{result['id']}/pages/999/image").status_code == 404
        assert protected_counts(db) == before
    finally:
        app.dependency_overrides.pop(get_db, None)
