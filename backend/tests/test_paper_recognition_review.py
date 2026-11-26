"""Offline whole-paper human labels must never act as answer/grade mutations."""
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.database import get_db
from app.main import app
from app.models import AnswerPageImage, AnswerTranscription, PaperRecognitionReview, PaperRecognitionReviewRevision, VisionPageTask
from app.schemas.paper_recognition_review import SavePaperRecognitionReview
from app.schemas.vision_pages import PageVisionAction
from app.services import answer_transcription, paper_recognition_review as service, vision_pages
from app.services.assessment import AssessmentProblem, now
from app.services.assessment_protocol import input_hash
from tests.test_stage6_s1 import context, protected_counts
from tests.test_stage6_s3_pages import mutation, picture
from tests.test_stage6_s3_transcription import prepared, merged, save_request, confirm_request
from tests.test_stage6_s3_vision_pages import response_for


def read(values):
    return service.view(values[0], values[2].id, values[5].id)


def request(view, **kwargs):
    return SavePaperRecognitionReview(**{
        "expected_revision_no": view["revision_no"], "expected_recognition_sha256": view["recognition_sha256"],
        "review_status": "correct", "all_pages_reviewed": True, "reviewer": "lin-test", "reason": "逐页对照原图",
        **kwargs,
    })


def save(values, view, **kwargs):
    return service.save(values[0], values[2].id, values[5].id, request(view, **kwargs))


def protection(db):
    return (protected_counts(db), input_hash([
        {"revision": h.revision_no, "input": h.input_sha256, "units": h.units, "evidence": h.evidence,
         "confirmed": h.confirmed_page_nos, "submission": str(h.submission_id)}
        for h in db.scalars(select(AnswerTranscription).order_by(AnswerTranscription.id))
    ]), input_hash([t.result for t in db.scalars(select(VisionPageTask).order_by(VisionPageTask.id))]))


def test_unreviewed_is_unknown_and_gets_never_create_quality_rows(prepared):
    db = prepared[0]
    assert not read(prepared)["can_record"] and read(prepared)["recognition_sha256"] is None
    merged(prepared); before = protection(db)
    view = read(prepared)
    assert view["current"] == {"review_status": "pending", "is_error": None}
    assert view["revision_no"] == 0 and view["can_record"] and not view["synthetic"]
    assert db.scalar(select(func.count()).select_from(PaperRecognitionReview)) == 0
    assert protection(db) == before


@pytest.mark.parametrize("status,is_error", [("pending", None), ("correct", False), ("incorrect", True), ("uncertain", None)])
def test_four_explicit_states_persist_without_changing_answers_grades_or_model_results(prepared, status, is_error):
    db = prepared[0]; merged(prepared); before = protection(db)
    result = save(prepared, read(prepared), review_status=status)
    assert result["current"]["is_error"] is is_error
    assert result["current"]["review_status"] == status and result["revision_no"] == 1
    assert result["history"][0]["reviewer"] == "lin-test"
    assert protection(db) == before


def test_edits_and_answer_key_changes_do_not_become_quality_labels_or_change_recognition_version(prepared):
    db, _, paper, _, question, artifact, _ = prepared
    draft = merged(prepared); initial = read(prepared)
    changed = save_request(draft); changed.units[0].answer = "2x"
    answer_transcription.save(db, paper.id, artifact.id, changed)
    assert read(prepared)["current"]["is_error"] is None
    assert read(prepared)["recognition_sha256"] == initial["recognition_sha256"]
    result = save(prepared, initial, review_status="incorrect")
    evidence = db.scalar(select(PaperRecognitionReview)).evidence
    assert evidence["units"][0]["original_answer"] == "x+x" and "answer" not in evidence["units"][0]
    assert set(evidence) == {"schema_version", "paper_instance_id", "artifact_id", "collection_id", "transcription_id",
                             "manifest_sha256", "inventory_sha256", "pages", "units"}
    assert "references" not in evidence and "provider_config" not in str(evidence)
    question.correct_answer = {**question.correct_answer, "items": [{**u, "answer": "changed"} for u in question.correct_answer["items"]]}
    db.flush()
    assert read(prepared)["recognition_sha256"] == result["recognition_sha256"]
    assert read(prepared)["current"]["review_status"] == "incorrect"


def test_revisions_reopen_and_idempotent_unchanged_save_preserve_history(prepared):
    merged(prepared)
    first = save(prepared, read(prepared), review_status="incorrect", reason="分数抄录有误")
    unchanged = save(prepared, first, review_status="incorrect", reason="分数抄录有误")
    assert unchanged["unchanged"] and unchanged["revision_no"] == 1
    amended = save(prepared, unchanged, review_status="uncertain", all_pages_reviewed=False, reason="原笔迹存在歧义")
    reopened = save(prepared, amended, review_status="pending", all_pages_reviewed=False, reason="重新安排人工核对")
    assert reopened["revision_no"] == 3 and reopened["current"]["is_error"] is None
    assert {h["review_status"] for h in reopened["history"]} == {"incorrect", "uncertain", "pending"}
    with pytest.raises(AssessmentProblem) as error:
        save(prepared, first, reason="旧页面并发修改")
    assert error.value.code == "REVISION_CONFLICT"


def test_new_extraction_does_not_inherit_old_label_and_old_save_is_rejected(prepared):
    db, _, paper, _, _, artifact, _ = prepared
    draft = merged(prepared); old = save(prepared, read(prepared))
    task = db.scalar(select(VisionPageTask))
    vision_pages.task_action(db, paper.id, artifact.id, task.id, "rerun", PageVisionAction(
        expected_revision_no=task.revision_no, reviewer="lin", reason="离线重新提取"))
    stale = read(prepared)
    assert stale["stale"] and not stale["can_record"] and not stale["history"][0]["is_current_target"]
    with pytest.raises(AssessmentProblem): save(prepared, old)
    replacement = db.scalar(select(VisionPageTask).where(VisionPageTask.rerun_of == task.id))
    replacement.status = "needs_review"
    replacement.result = {**response_for(replacement), "synthetic": False}
    db.flush()
    from app.schemas.answer_transcription import MergeTranscription
    current_collection = answer_transcription.answer_pages.collection_view(db, paper.id, artifact.id)
    answer_transcription.merge(db, paper.id, artifact.id, MergeTranscription(
        expected_revision_no=draft["revision_no"], expected_collection_revision=current_collection["revision_no"],
        expected_input_sha256=current_collection["effective_input_sha256"], reviewer="lin", reason="合并新提取结果"))
    fresh = read(prepared)
    assert fresh["recognition_sha256"] != old["recognition_sha256"]
    assert fresh["current"]["review_status"] == "pending" and fresh["current"]["is_error"] is None
    assert fresh["revision_no"] == 0 and len(fresh["history"]) == 1
    with pytest.raises(AssessmentProblem): save(prepared, old)


def test_definite_labels_require_explicit_full_page_check(prepared):
    merged(prepared)
    for status in ("correct", "incorrect"):
        with pytest.raises(AssessmentProblem) as error:
            save(prepared, read(prepared), review_status=status, all_pages_reviewed=False)
        assert error.value.code == "FULL_REVIEW_REQUIRED"
    assert read(prepared)["revision_no"] == 0


def test_retake_keeps_old_label_as_history_and_blocks_old_page_save(prepared):
    db, _, paper, _, _, artifact, collection = prepared
    merged(prepared); previous = save(prepared, read(prepared))
    answer_transcription.answer_pages.upload_image(db, paper.id, artifact.id, 1,
        mutation(collection["revision_no"]), picture((100, 90, 80)))
    result = read(prepared)
    assert result["stale"] and not result["can_record"]
    assert result["history"][0]["review_status"] == "correct"
    assert not result["history"][0]["is_current_target"]
    with pytest.raises(AssessmentProblem): save(prepared, previous)


@pytest.mark.parametrize("prepared", ["many"], indirect=True)
def test_multipage_label_is_one_whole_paper_record_bound_to_every_page(prepared):
    db = prepared[0]; merged(prepared)
    result = save(prepared, read(prepared), review_status="incorrect", reason="第二页负号漏抄")
    evidence = db.scalar(select(PaperRecognitionReview)).evidence
    assert result["page_count"] == prepared[5].total_pages > 1
    assert len(evidence["units"]) == 18
    assert {p["page_no"] for p in evidence["pages"]} == set(range(1, result["page_count"] + 1))
    assert len(result["history"]) == 1 and result["current"]["is_error"] is True


def test_submitted_version_remains_bound_and_expired_photos_allow_only_unknown_labels(prepared):
    db, _, paper, _, _, artifact, _ = prepared
    draft = merged(prepared)
    draft = answer_transcription.save(db, paper.id, artifact.id, save_request(draft))
    initial = read(prepared)
    answer_transcription.confirm(db, paper.id, artifact.id, confirm_request(draft))
    assert read(prepared)["recognition_sha256"] == initial["recognition_sha256"]
    assert read(prepared)["target_scope"] == "submitted"
    result = save(prepared, read(prepared)); before = protection(db)
    for image in db.scalars(select(AnswerPageImage)):
        image.retention_until = now() - timedelta(seconds=1)
    db.flush()
    assert not read(prepared)["original_images_available"] and read(prepared)["can_record"]
    with pytest.raises(AssessmentProblem) as error: save(prepared, result, reason="重新确认原结论")
    assert error.value.code == "ORIGINAL_IMAGES_UNAVAILABLE"
    unknown = save(prepared, result, review_status="uncertain", all_pages_reviewed=False, reason="原照片已不可用")
    assert unknown["current"]["is_error"] is None and protection(db) == before


def test_quality_audit_and_frozen_evidence_are_immutable_in_postgres(prepared):
    db = prepared[0]; merged(prepared); save(prepared, read(prepared))
    for statement in (
        "UPDATE paper_recognition_review_revisions SET data='{}'::jsonb",
        "DELETE FROM paper_recognition_review_revisions",
        "UPDATE paper_recognition_reviews SET evidence='{}'::jsonb",
        "DELETE FROM paper_recognition_reviews",
    ):
        with pytest.raises(DBAPIError), db.begin_nested(): db.execute(text(statement))
    assert read(prepared)["current"]["review_status"] == "correct"


def test_synthetic_results_are_always_identified_and_do_not_grade(prepared):
    db = prepared[0]
    task = db.scalar(select(VisionPageTask)); task.result = {**task.result, "synthetic": True}; db.flush()
    merged(prepared); before = protection(db)
    result = save(prepared, read(prepared))
    assert result["synthetic"] and result["history"][0]["synthetic"]
    assert protection(db) == before


def test_api_guards_scope_status_and_optimistic_revision(prepared, monkeypatch):
    db, _, paper, _, _, artifact, _ = prepared
    merged(prepared)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    path = f"/api/v1/internal/stage6/paper-instances/{paper.id}/answer-card-artifacts/{artifact.id}/page-collection/recognition-review"
    before = protection(db)
    try:
        current = client.get(path).json()
        payload = request(current).model_dump()
        response = client.put(path, json=payload)
        assert response.status_code == 200 and response.json()["current"]["is_error"] is False
        assert client.put(path, json=payload).status_code == 409
        assert client.put(path, json={**payload, "is_error": False}).status_code == 422
        assert client.get(path.replace(str(paper.id), str(uuid4()))).status_code == 404
        paper.invalidated_at = now(); paper.invalidation_type = "withdrawn"; db.flush()
        assert not client.get(path).json()["can_record"]
        assert client.put(path, json={**payload, "expected_revision_no": 1}).status_code == 409
        from app.core.config import get_settings
        monkeypatch.setattr(get_settings(), "app_env", "production")
        assert client.get(path).status_code == 403
        assert protection(db) == before
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize("patch", [{"all_pages_reviewed": "true"}, {"expected_revision_no": True},
                                   {"reviewer": " "}, {"reason": " "}, {"review_status": "accepted"}])
def test_request_rejects_coercions_empty_audit_and_acceptance_status(patch):
    with pytest.raises(ValidationError): request({"revision_no": 0, "recognition_sha256": "a" * 64}, **patch)
