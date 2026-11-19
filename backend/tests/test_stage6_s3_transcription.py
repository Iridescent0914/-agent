"""All images/results are offline fixtures; never invokes any provider."""
from copy import deepcopy
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.database import get_db
from app.main import app
from app.models import (
    AnswerTranscription, AnswerTranscriptionRevision, Assignment, Attempt, AttemptRevision,
    PaperInstance, PaperInstanceItem, Student, Submission, VisionPageTask,
)
from app.schemas.answer_transcription import MergeTranscription, SaveTranscription, ConfirmTranscription
from app.schemas.answer_pages import CreatePageCollection
from app.schemas.vision_pages import CreatePageVisionTasks, PageVisionAction
from app.services import answer_pages, answer_transcription as service, vision_pages
from app.services.answer_card_artifacts import create_artifact
from app.services.assessment import AssessmentProblem, now
from app.services.assessment_protocol import input_hash
from app.services.vision_page_protocol import identity_check
from tests.test_stage6_s1 import context, protected_counts
from tests.test_stage6_s3_pages import mutation, picture
from tests.test_stage6_s3_vision_pages import response_for


@pytest.fixture
def prepared(context, monkeypatch, request):
    db, factory, original, old_item, question = context
    # Config fixture authorizes no HTTP; directly persist a parsed provider result.
    monkeypatch.setattr(vision_pages, "configuration", lambda: {
        "provider": "bailian", "model": "offline-test", "prompt_version": "fixture", "policy_version": "fixture", "synthetic": False})
    assignment = Assignment(title="核对录入fixture", chapter_code="s1")
    db.add(assignment); db.flush()
    paper = PaperInstance(assignment_id=assignment.id, student_id=original.student_id,
        version_no=1, status="draft", instance_code="S3E-" + uuid4().hex)
    db.add(paper); db.flush()
    mode = getattr(request, "param", "group")
    reference, rule = deepcopy(old_item.answer_snapshot), deepcopy(old_item.answer_rule_snapshot)
    if mode == "many":
        reference["items"] = [{"item_id": f"sub_{n}.blank_1", "subquestion_no": n, "blank_no": 1,
            "answer": str(n), "answer_match_type": "numeric"} for n in range(1, 19)]
    elif mode == "scalar":
        reference, rule = "30", {"answer_match_type": "numeric", "answer_normalization_rule": {}}
    question.correct_answer, question.answer_match_type = deepcopy(reference), rule["answer_match_type"]
    from app.models import KnowledgePoint
    point = db.get(KnowledgePoint, question.main_knowledge_point_id)
    from app.services import knowledge_catalog
    catalog = knowledge_catalog.formal_kps_by_code()
    monkeypatch.setattr(knowledge_catalog, "formal_kps_by_code", lambda: {
        **catalog, point.code: {**next(iter(catalog.values())), "code": point.code}})
    item = PaperInstanceItem(paper_instance_id=paper.id, question_id=question.id, order_no=3,
        question_snapshot={"question_type": "fill_blank", "question_text": "fixture", "difficulty": 2,
            "original_qid": question.original_qid, "core_literacy": knowledge_catalog.formal_literacy_dimensions()[0]},
        answer_snapshot=reference, answer_rule_snapshot=rule,
        knowledge_point_snapshot={"id": point.code, "name": point.name}, selection_audit={"source_module": "history"})
    db.add(item); db.flush()
    paper.status, paper.published_at = "published", now()
    artifact, _ = create_artifact(db, paper.id, 1)
    collection = answer_pages.create_collection(db, paper.id, artifact.id, CreatePageCollection(
        artifact_code=artifact.artifact_code, instance_code=paper.instance_code, expected_version_no=1))
    for n in range(1, artifact.total_pages + 1):
        collection = answer_pages.upload_image(db, paper.id, artifact.id, n, mutation(collection["revision_no"]), picture((n * 20, 20, 20)))
    vision_pages.create_tasks(db, paper.id, artifact.id, CreatePageVisionTasks(
        expected_collection_revision=collection["revision_no"], expected_input_sha256=collection["effective_input_sha256"],
        reviewer="lin", reason="离线结果fixture", external_images_reviewed=True))
    for task in db.scalars(select(VisionPageTask).where(VisionPageTask.collection_id == collection["id"])):
        parsed = response_for(task)
        for answer, slot in zip(parsed["answers"], task.snapshot["slots"]):
            answer["answer"] = ("30" if mode == "scalar" else str(slot["subquestion_no"]) if mode == "many" else
                {"sub_1.blank_1": "x+x", "sub_2.blank_1": "PRIVATE_OTHER_ANSWER"}[slot["item_id"]])
        task.status = "needs_review"
        task.result = {**parsed, "identity_check": identity_check(parsed, task.snapshot["expected_identity"]), "synthetic": False}
    db.commit()
    return db, factory, paper, item, question, artifact, collection


def merged(values):
    db, _, paper, _, _, artifact, collection = values
    return service.merge(db, paper.id, artifact.id, MergeTranscription(expected_revision_no=0,
        expected_collection_revision=collection["revision_no"], expected_input_sha256=collection["effective_input_sha256"], reviewer="lin", reason="整卷合并"))


def save_request(view, **kwargs):
    values = dict(expected_revision_no=view["revision_no"], expected_input_sha256=view["input_sha256"],
        reviewer="lin", reason="对照原图核对", confirmed_page_nos=[p["page_no"] for p in view["pages"]],
        units=[{k: u[k] for k in ("slot_key", "recognition_status", "answer")} for u in view["units"]])
    values.update(kwargs)
    return SaveTranscription(**values)


def confirm_request(view):
    return ConfirmTranscription(expected_revision_no=view["revision_no"], expected_input_sha256=view["input_sha256"],
        reviewer="lin", reason="确认抄读录入", transcription_confirmed=True)


def test_merge_save_confirm_grade_and_profile_are_atomic_idempotent_and_traceable(prepared):
    db, _, paper, item, question, artifact, collection = prepared
    before = protected_counts(db)
    view = merged(prepared)
    assert len(view["units"]) == 2 and not view["can_submit"]
    assert view["units"][1]["subquestion_no"] == 2
    assert protected_counts(db) == before
    view = service.save(db, paper.id, artifact.id, save_request(view))
    assert view["can_submit"] and protected_counts(db) == before
    request = confirm_request(view)
    output = service.confirm(db, paper.id, artifact.id, request)
    assert output.source == "image" and output.summary.correct_count == 1
    assert output.answer_transcription["pages"][0]["image_url"]
    assert output.items[0].attempt.used_in_profile
    assert output.items[0].attempt.grading_details["recognition_reviewed"] is True
    assert output.items[0].attempt.student_answer["items"][1]["item_id"] == "sub_2.blank_1"
    again = service.confirm(db, paper.id, artifact.id, request)
    assert again.replayed and again.submission_id == output.submission_id
    assert db.scalar(select(func.count()).select_from(Submission).where(Submission.paper_instance_id == paper.id)) == 1
    assert db.scalar(select(func.count()).select_from(AttemptRevision).join(Attempt).where(Attempt.submission_id == output.submission_id)) == 1
    history = service.view(db, paper.id, artifact.id)["history"]
    assert [h["action"] for h in history] == ["submit", "save", "merge"]
    source = db.get(AnswerTranscription, output.answer_transcription["id"])
    assert source.units[0]["original_answer"] == "x+x"


def test_teacher_correction_preserves_model_output_and_merge_does_not_erase_it(prepared):
    db, _, paper, _, _, artifact, collection = prepared
    view = merged(prepared)
    request = save_request(view)
    request.units[0].answer = "2x"
    view = service.save(db, paper.id, artifact.id, request)
    assert view["units"][0]["answer"] == "2x" and view["units"][0]["original_answer"] == "x+x"
    same = service.merge(db, paper.id, artifact.id, MergeTranscription(expected_revision_no=view["revision_no"],
        expected_collection_revision=collection["revision_no"], expected_input_sha256=collection["effective_input_sha256"], reviewer="lin", reason="重复合并"))
    assert same["revision_no"] == view["revision_no"] and same["units"][0]["answer"] == "2x"
    assert db.scalar(select(VisionPageTask)).result["answers"][0]["answer"] == "x+x"
    revision = db.scalar(select(AnswerTranscriptionRevision).where(AnswerTranscriptionRevision.revision_no == 1))
    assert revision.state["units"][0]["answer"] == "x+x"


@pytest.mark.parametrize("change", ["retake", "rerun", "reference", "expired", "config", "invalidated"])
def test_changed_inputs_reject_old_save_and_confirm_without_grading(prepared, monkeypatch, change):
    db, _, paper, _, question, artifact, collection = prepared
    view = merged(prepared)
    view = service.save(db, paper.id, artifact.id, save_request(view))
    before = protected_counts(db)
    if change == "retake":
        answer_pages.upload_image(db, paper.id, artifact.id, 1, mutation(collection["revision_no"]), picture((100, 90, 80)))
    elif change == "rerun":
        task = db.scalar(select(VisionPageTask))
        vision_pages.task_action(db, paper.id, artifact.id, task.id, "rerun", PageVisionAction(
            expected_revision_no=task.revision_no, reviewer="lin", reason="重新提取"))
    elif change == "reference":
        question.correct_answer = {**question.correct_answer, "items": [{**u, "answer": "changed"} for u in question.correct_answer["items"]]}
    elif change == "config":
        monkeypatch.setattr(vision_pages, "configuration", lambda: {"provider": "fake", "model": "changed", "prompt_version": "fixture", "policy_version": "fixture"})
    elif change == "invalidated":
        paper.invalidated_at = now()
        paper.invalidation_type = "withdrawn"
    else:
        from app.models import AnswerPageImage
        from datetime import timedelta
        db.scalar(select(AnswerPageImage)).retention_until = now() - timedelta(seconds=1)
    db.flush()
    assert service.view(db, paper.id, artifact.id)["stale"]
    with pytest.raises(AssessmentProblem):
        service.save(db, paper.id, artifact.id, save_request(view))
    with pytest.raises(AssessmentProblem):
        service.confirm(db, paper.id, artifact.id, confirm_request(view))
    assert protected_counts(db) == before


def test_unresolved_blank_identity_and_synthetic_do_not_silently_become_grades(prepared):
    db, _, paper, _, _, artifact, _ = prepared
    view = merged(prepared)
    with pytest.raises(AssessmentProblem, match="逐页"):
        service.confirm(db, paper.id, artifact.id, confirm_request(view))
    request = save_request(view)
    request.units[0].recognition_status, request.units[0].answer = "illegible", None
    view = service.save(db, paper.id, artifact.id, request)
    assert not view["can_submit"]
    with pytest.raises(AssessmentProblem, match="无法辨认"):
        service.confirm(db, paper.id, artifact.id, confirm_request(view))
    request = save_request(view)
    request.units[0].recognition_status = "blank"
    view = service.save(db, paper.id, artifact.id, request)
    result = service.confirm(db, paper.id, artifact.id, confirm_request(view))
    assert result.summary.incorrect_count == 1
    assert result.items[0].attempt.student_answer["items"][0]["answer"] is None


def test_stale_revision_and_duplicate_missing_extra_slots_are_rejected(prepared):
    db, _, paper, _, _, artifact, _ = prepared
    view = merged(prepared)
    request = save_request(view)
    updated = service.save(db, paper.id, artifact.id, request)
    with pytest.raises(AssessmentProblem, match="已被修改"):
        service.save(db, paper.id, artifact.id, request)
    for values in (updated["units"][:1], updated["units"] + updated["units"][:1]):
        with pytest.raises(AssessmentProblem, match="完整覆盖"):
            service.save(db, paper.id, artifact.id, save_request(updated,
                units=[{k: u[k] for k in ("slot_key", "recognition_status", "answer")} for u in values]))


def test_synthetic_result_cannot_grade_even_after_all_teacher_checks(prepared):
    db, _, paper, _, _, artifact, _ = prepared
    task = db.scalar(select(VisionPageTask))
    task.result = {**task.result, "synthetic": True}
    db.flush()
    view = merged(prepared)
    view = service.save(db, paper.id, artifact.id, save_request(view))
    with pytest.raises(AssessmentProblem, match="模拟"):
        service.confirm(db, paper.id, artifact.id, confirm_request(view))
    assert db.scalar(select(Submission).where(Submission.paper_instance_id == paper.id)) is None


def test_history_is_immutable_in_postgres(prepared):
    db, _, _, _, _, _, _ = prepared
    view = merged(prepared)
    with pytest.raises(DBAPIError), db.begin_nested():
        db.execute(text("UPDATE answer_transcription_revisions SET reason='tampered' WHERE transcription_id=:id"), {"id": view["id"]})
    assert service.view(db, prepared[2].id, prepared[5].id)["history"][0]["reason"] == "整卷合并"


@pytest.mark.parametrize("prepared", ["many", "scalar"], indirect=True)
def test_multipage_and_scalar_answer_assembly(prepared):
    db, _, paper, _, _, artifact, _ = prepared
    view = merged(prepared)
    assert len(view["pages"]) == artifact.total_pages
    if len(view["units"]) > 1:
        assert artifact.total_pages > 1 and len(view["units"]) == 18
    view = service.save(db, paper.id, artifact.id, save_request(view))
    result = service.confirm(db, paper.id, artifact.id, confirm_request(view))
    assert result.summary.correct_count == 1
    answer = result.items[0].attempt.student_answer
    assert (len(answer["items"]) == 18) if isinstance(answer, dict) else answer == "30"


def test_profile_failure_rolls_back_submission_attempts_and_confirmation_together(prepared, monkeypatch):
    db, _, paper, _, _, artifact, _ = prepared
    view = service.save(db, paper.id, artifact.id, save_request(merged(prepared)))
    before = protected_counts(db)
    from app.services import submission_workflow
    def fail(*args, **kwargs):
        raise RuntimeError("offline profile failure")
    monkeypatch.setattr(submission_workflow, "refresh_student_profiles", fail)
    with pytest.raises(RuntimeError, match="offline profile"), db.begin_nested():
        service.confirm(db, paper.id, artifact.id, confirm_request(view))
    assert protected_counts(db) == before
    assert service.view(db, paper.id, artifact.id)["submission_id"] is None


def test_later_answer_corrections_and_regrading_preserve_confirmed_image_provenance(prepared):
    db, _, paper, _, question, artifact, _ = prepared
    view = service.save(db, paper.id, artifact.id, save_request(merged(prepared)))
    output = service.confirm(db, paper.id, artifact.id, confirm_request(view))
    from app.services.submission_workflow import review_attempt, regrade_submission
    from app.schemas.submissions import AttemptReviewRequest, SubmissionRegradeRequest
    attempt = output.items[0].attempt
    corrected = review_attempt(db, attempt.id, AttemptReviewRequest(action="correct", expected_revision_no=1,
        corrected_answer=attempt.student_answer, corrected_response_status="answered", is_correct=True, reason="再次核对"))
    assert corrected.items[0].attempt.grading_details["recognition_reviewed"] is True
    question.correct_answer = {**question.correct_answer, "items": [{**u, "answer": "changed"} for u in question.correct_answer["items"]]}
    db.flush()
    changed = regrade_submission(db, output.submission_id, SubmissionRegradeRequest(reason="标准答案更新"))
    assert changed.items[0].attempt.grading_details["answer_transcription"]["transcription_id"] == view["id"]
    assert changed.items[0].attempt.grading_details["recognition_reviewed"] is True
    assert changed.answer_transcription["id"] == view["id"]


def test_http_routes_and_legacy_write_retirement(prepared):
    db, _, paper, _, _, artifact, collection = prepared
    app.dependency_overrides[get_db] = lambda: db
    base = f"/api/v1/internal/stage6/paper-instances/{paper.id}/answer-card-artifacts/{artifact.id}/page-collection/transcription"
    try:
        client = TestClient(app)  # No lifespan: background workers cannot run.
        assert client.get(base).json()["revision_no"] == 0
        merged_view = client.post(base + "/merge", json=dict(expected_revision_no=0,
            expected_collection_revision=collection["revision_no"], expected_input_sha256=collection["effective_input_sha256"], reviewer="lin", reason="整卷合并")).json()
        saved = client.put(base, json=save_request(merged_view).model_dump()).json()
        response = client.post(base + "/confirm", json=confirm_request(saved).model_dump())
        assert response.status_code == 200 and response.json()["source"] == "image"
        for suffix in ("extract", "commit"):
            assert client.post(f"/api/v1/recognition-runs/{uuid4()}/{suffix}", json={}).status_code == 410
        assert client.post(f"/api/v1/paper-instances/{paper.id}/recognition-runs", files={"file": ("x.png", b"unused")},
            data={"instance_code": paper.instance_code, "idempotency_key": "retired-test"}).status_code == 410
        assert client.get(f"/api/v1/paper-instances/{paper.id}/recognition-runs/current").status_code == 200
    finally:
        app.dependency_overrides.clear()
