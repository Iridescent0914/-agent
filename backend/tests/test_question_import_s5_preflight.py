from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import engine, get_db
from app.main import app
from app.models import Question, QuestionImportBatch, QuestionImportCatalogCheck, QuestionImportCatalogMatch, QuestionImportItem
from app.models import QuestionImportAuditEvent
from app.schemas.question_import_s5 import (
    S5AnswerClaimRequest, S5AnswerSourceRequest, S5BulkDecisionRequest, S5IssueDecisionRequest, S5ItemDecisionRequest,
    S5PreflightRequest, S5PreflightSelection,
)
from app.services.question_import import ImportProblem, update_candidate
from app.services.question_import_s3_review import list_review_issues
from app.services.question_import_figure_review import figure_text_confirmed
from app.services.knowledge_catalog import CATALOG_PATH
from app.services.question_fingerprint_v2 import VERSION as EXACT_VERSION
from app.services.question_import_s3_labels import _allowed_kps
from app.services.question_import_s4_near import VERSION as NEAR_VERSION
from app.services.question_import_s4_review import _candidate_signature, _catalog_signature, _catalog_snapshot_signature
from app.services.question_import_s5_preflight import import_review_summary, preflight_import
from app.services.question_import_s5_review import (
    claim_source_answer, confirm_answer_source, decide_issue, decide_item, decide_items_bulk, read_item_reviews,
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


def _case(db: Session, *, fake: bool = False):
    code, kp = next(iter(_allowed_kps(db).items()))
    batch = QuestionImportBatch(
        id=uuid.uuid4(), owner_scope="internal-demo", request_fingerprint=uuid.uuid4().hex,
        catalog_snapshot_sha256=hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest(),
        source_mode="questions_only", status="review_pending", stage="s2_completed", files=[],
        checkpoint={"s3": {"global_dedupe": {"status": "completed"}}},
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    item = QuestionImportItem(
        id=uuid.uuid4(), batch_id=batch.id, ordinal=1, revision=1, is_fake=fake,
        payload={
            "content": {"question_text": "计算以下算式的结果。", "options": [
                {"key": "A", "text": "1"}, {"key": "B", "text": "2"}], "asset_ids": []},
            "tags": {"question_type": "choice", "difficulty": 2, "main_kp_code": code,
                     "is_required": False, "grade": kp["grade"],
                     "chapter_code": kp["chapter_code"], "section": kp["section"],
                     "core_literacies": kp["core_competencies"]},
            "teacher_decision": "pending", "answers": [], "selected_answer_id": None,
        },
    )
    db.add_all([batch, item])
    db.flush()
    check = QuestionImportCatalogCheck(
        batch_id=batch.id, item_id=item.id, check_no=1, candidate_revision=1,
        candidate_signature=_candidate_signature(db, batch.id, item),
        exact_version=EXACT_VERSION, near_version=NEAR_VERSION,
    )
    db.add(check)
    db.flush()
    return batch, item, check


def _request(item: QuestionImportItem, *, route: str = "library_only",
             answer_review_items=None, expected_revision=None):
    return S5PreflightRequest(items=[S5PreflightSelection(
        item_id=item.id, expected_revision=expected_revision or item.revision,
        route=route, answer_review_items=answer_review_items,
    )])


def _second_item(db: Session, batch: QuestionImportBatch, first: QuestionImportItem):
    second = QuestionImportItem(id=uuid.uuid4(), batch_id=batch.id, ordinal=2,
                                revision=1, is_fake=False, payload={**first.payload,
                                "content": {**first.payload["content"], "question_text": "另一道待审核数学题"}})
    db.add(second)
    db.flush()
    db.add(QuestionImportCatalogCheck(
        batch_id=batch.id, item_id=second.id, check_no=1, candidate_revision=1,
        candidate_signature=_candidate_signature(db, batch.id, second),
        exact_version=EXACT_VERSION, near_version=NEAR_VERSION))
    db.flush()
    return second


def _codes(result):
    return {issue["code"] for issue in result["items"][0]["blockers"]}


def test_library_only_allows_missing_answer_and_preflight_is_read_only(db):
    batch, item, check = _case(db)
    before = (item.revision, item.payload.copy(), check.check_no)
    result = preflight_import(db, batch.id, _request(item))
    assert result["summary"] == {"selected": 1, "eligible": 1, "blocked": 0,
                                  "library_only": 1, "direct_pool": 0}
    assert result["items"][0]["batch_eligible"] is True
    assert {row["code"] for row in result["items"][0]["warnings"]} == {"ANSWER_PENDING"}
    assert (item.revision, item.payload, check.check_no) == before
    summary = import_review_summary(db, batch.id)["counts"]
    assert summary["active_real"] == 1
    assert summary["ready_for_library_confirmation"] == 1
    assert summary["ready_for_pool_confirmation"] == 0


def test_direct_pool_reuses_answer_review_rules_without_confirming(db):
    batch, item, _ = _case(db)
    item.payload = {**item.payload, "selected_answer_id": "a1", "answer_match_status": "matched",
                    "answers": [{"answer_id": "a1", "answer_source": "source_file", "text": "A",
                                 "teacher_confirmed": False}]}
    db.flush()
    missing = preflight_import(db, batch.id, _request(item, route="direct_pool"))
    assert "ANSWER_UNITS_REQUIRED" in _codes(missing)
    summary = import_review_summary(db, batch.id)["counts"]
    assert summary["needs_answer_review_for_pool"] == 1
    assert summary["ready_for_pool_confirmation"] == 0
    valid_items = [{"item_id": "answer-1", "answer": "A", "answer_match_type": "choice"}]
    ready = preflight_import(db, batch.id, _request(item, route="direct_pool",
                                                  answer_review_items=valid_items))
    assert ready["items"][0]["batch_eligible"] is True
    assert ready["items"][0]["confirmation_pending"] is True
    assert "ANSWER_CONFIRMATION_REQUIRED" in {row["code"] for row in ready["items"][0]["warnings"]}
    invalid = preflight_import(db, batch.id, _request(item, route="direct_pool",
                                                    answer_review_items=[{"item_id": "answer-1", "answer": "Z",
                                                                          "answer_match_type": "choice"}]))
    assert "ANSWER_REVIEW_INVALID" in _codes(invalid)
    missing_option = preflight_import(db, batch.id, _request(item, route="direct_pool",
                                                           answer_review_items=[{"item_id": "answer-1", "answer": "C",
                                                                                 "answer_match_type": "choice"}]))
    assert "ANSWER_REVIEW_INVALID" in _codes(missing_option)
    assert "实际存在的选项字母" in missing_option["items"][0]["blockers"][0]["message"]


def test_single_choice_rejects_unneeded_answer_coordinates(db):
    batch, item, _ = _case(db)
    item.payload = {**item.payload, "selected_answer_id": "a1", "answer_match_status": "matched",
                    "answers": [{"answer_id": "a1", "text": "A", "teacher_confirmed": False}]}
    db.flush()
    result = preflight_import(db, batch.id, _request(item, route="direct_pool", answer_review_items=[
        {"item_id": "answer-1", "subquestion_no": 7, "blank_no": 3,
         "answer": "A", "answer_match_type": "choice"},
    ]))
    assert "ANSWER_REVIEW_INVALID" in _codes(result)
    assert "小题号和空号请留空" in result["items"][0]["blockers"][0]["message"]


def test_multiple_answer_items_need_explicit_correct_coordinates_for_direct_pool(db):
    batch, item, check = _case(db)
    item.payload = {**item.payload,
                    "content": {**item.payload["content"],
                                "question_text": "（1）选 A 或 B；（2）选 A 或 B。"},
                    "selected_answer_id": "a1", "answer_match_status": "matched",
                    "answers": [{"answer_id": "a1", "text": "（1）A；（2）B", "teacher_confirmed": False}]}
    db.flush()
    check.candidate_signature = _candidate_signature(db, batch.id, item)
    db.flush()
    no_numbers = [
        {"item_id": "a", "answer": "A", "answer_match_type": "choice"},
        {"item_id": "b", "answer": "B", "answer_match_type": "choice"},
    ]
    assert "ANSWER_REVIEW_INVALID" in _codes(preflight_import(
        db, batch.id, _request(item, route="direct_pool", answer_review_items=no_numbers)))
    one_answer = [{"item_id": "a", "answer": "A", "answer_match_type": "choice"}]
    assert "ANSWER_REVIEW_INVALID" in _codes(preflight_import(
        db, batch.id, _request(item, route="direct_pool", answer_review_items=one_answer)))
    assert preflight_import(db, batch.id, _request(item))["items"][0]["batch_eligible"] is True
    with pytest.raises(ImportProblem) as caught:
        decide_item(db, batch.id, item.id, S5ItemDecisionRequest(
            expected_revision=item.revision, decision="approved", route="direct_pool",
            answer_review_items=no_numbers))
    assert caught.value.code == "S5_PREFLIGHT_BLOCKED"
    assert item.revision == 1
    wrong_numbers = [
        {**no_numbers[0], "subquestion_no": 1, "blank_no": 1},
        {**no_numbers[1], "subquestion_no": 1, "blank_no": 2},
    ]
    assert "ANSWER_REVIEW_INVALID" in _codes(preflight_import(
        db, batch.id, _request(item, route="direct_pool", answer_review_items=wrong_numbers)))
    correct_numbers = [
        {**no_numbers[0], "subquestion_no": 1, "blank_no": 1},
        {**no_numbers[1], "subquestion_no": 2, "blank_no": 1},
    ]
    assert preflight_import(db, batch.id, _request(
        item, route="direct_pool", answer_review_items=correct_numbers))["items"][0]["batch_eligible"] is True


def test_detected_multiple_blanks_cannot_use_single_answer_item_for_direct_pool(db):
    batch, item, check = _case(db)
    item.payload = {**item.payload,
                    "content": {**item.payload["content"], "question_text": "填空：___，___。"},
                    "tags": {**item.payload["tags"], "question_type": "fill_blank"},
                    "selected_answer_id": "a1", "answer_match_status": "matched",
                    "answers": [{"answer_id": "a1", "text": "1，2", "teacher_confirmed": False}]}
    db.flush()
    check.candidate_signature = _candidate_signature(db, batch.id, item)
    db.flush()
    one_answer = [{"item_id": "a", "answer": "1，2", "answer_match_type": "exact"}]
    assert "ANSWER_REVIEW_INVALID" in _codes(preflight_import(
        db, batch.id, _request(item, route="direct_pool", answer_review_items=one_answer)))
    assert preflight_import(db, batch.id, _request(item))["items"][0]["batch_eligible"] is True


def test_version_stale_check_and_invalid_tags_are_independent_blockers(db):
    batch, item, check = _case(db)
    assert "REVISION_CONFLICT" in _codes(preflight_import(
        db, batch.id, _request(item, expected_revision=item.revision + 1)))
    check.near_version = "s4-near-v2"
    item.payload = {**item.payload, "tags": {**item.payload["tags"], "main_kp_code": "unknown"}}
    db.flush()
    result = preflight_import(db, batch.id, _request(item))
    assert {"TAG_OUT_OF_SCOPE", "DUPLICATE_CHECK_STALE"} <= _codes(result)


def test_fake_candidate_cannot_be_approved_by_preflight(db):
    batch, item, _ = _case(db, fake=True)
    result = preflight_import(db, batch.id, _request(item))
    assert "CANDIDATE_UNAVAILABLE" in _codes(result)
    assert import_review_summary(db, batch.id)["counts"]["active_fake"] == 1


def test_missing_key_figure_and_pending_near_match_block_confirmation(db):
    batch, item, check = _case(db)
    item.payload = {**item.payload, "extraction": {"diagram_clues": ["图形面积"]}}
    formal = db.scalar(select(Question).where(Question.has_image.is_(False),
                                              Question.archived_at.is_(None)))
    assert formal is not None
    db.add(QuestionImportCatalogMatch(
        check_id=check.id, question_id=formal.id, original_qid=formal.original_qid,
        catalog_review_revision=formal.review_revision, catalog_signature=_catalog_signature(formal),
        kind="nearby", similarity=0.5, evidence={}, decision="pending",
    ))
    db.flush()
    result = preflight_import(db, batch.id, _request(item))
    assert {"ASSET_UNASSIGNED", "DUPLICATE_DECISION_REQUIRED"} <= _codes(result)
    assert result["items"][0]["batch_eligible"] is False


def test_teacher_can_confirm_transcribed_table_and_content_edit_invalidates_it(db):
    batch, item, check = _case(db)
    item.payload = {**item.payload, "extraction": {
        "figure_status": "no_figure", "diagram_clues": ["表格：第一袋+2，第二袋0"]}}
    db.flush()
    assert "ASSET_UNASSIGNED" in _codes(preflight_import(db, batch.id, _request(item)))

    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).post(
            f"/api/v1/question-imports/{batch.id}/s5/items/{item.id}/figure-text-confirmation",
            json={"expected_revision": item.revision, "confirmed": True})
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 200
    db.refresh(item)
    checks = list(db.scalars(select(QuestionImportCatalogCheck).where(
        QuestionImportCatalogCheck.batch_id == batch.id,
        QuestionImportCatalogCheck.item_id == item.id)))
    assert max(row.check_no for row in checks) == 2  # The confirmation also reruns S4.
    assert figure_text_confirmed(item.payload)
    assert "ASSET_UNASSIGNED" not in _codes(preflight_import(db, batch.id, _request(item)))
    assert item.payload["figure_text_confirmation"]["confirmed_at"]
    assert not figure_text_confirmed({**item.payload, "content": {
        **item.payload["content"], "question_text": "外部改动后的题干"}})

    update_candidate(db, batch.id, item.id, expected_revision=item.revision,
                     changes={"content": {**item.payload["content"],
                                          "question_text": "修改后的题干"}})
    assert not figure_text_confirmed(item.payload)
    assert "ASSET_UNASSIGNED" in _codes(preflight_import(db, batch.id, _request(item)))


def test_decision_without_reason_clears_unverifiable_near_match_blocker(db):
    batch, item, check = _case(db)
    formal = db.scalar(select(Question).where(Question.has_image.is_(False),
                                              Question.archived_at.is_(None)))
    assert formal is not None
    formal.has_image = True
    formal.assets = []
    db.add(QuestionImportCatalogMatch(
        check_id=check.id, question_id=formal.id, original_qid=formal.original_qid,
        catalog_review_revision=formal.review_revision, catalog_signature=None,
        kind="nearby", similarity=0.5,
        evidence={"manual_signature": _catalog_snapshot_signature(formal)},
        decision="pending",
    ))
    db.flush()
    assert {"DUPLICATE_UNVERIFIABLE", "DUPLICATE_DECISION_REQUIRED"} <= _codes(
        preflight_import(db, batch.id, _request(item)))
    match = db.scalar(select(QuestionImportCatalogMatch).where(
        QuestionImportCatalogMatch.check_id == check.id))
    match.decision = "unrelated"
    match.reason = None
    db.flush()
    assert not {"DUPLICATE_UNVERIFIABLE", "DUPLICATE_DECISION_REQUIRED"} & _codes(
        preflight_import(db, batch.id, _request(item)))

def test_duplicate_answer_coordinates_fail_same_pool_review_rules(db):
    batch, item, _ = _case(db)
    item.payload = {**item.payload, "selected_answer_id": "a1", "answer_match_status": "matched",
                    "answers": [{"answer_id": "a1", "text": "A", "teacher_confirmed": False}]}
    db.flush()
    result = preflight_import(db, batch.id, _request(item, route="direct_pool", answer_review_items=[
        {"item_id": "a", "subquestion_no": 1, "blank_no": 1, "answer": "A", "answer_match_type": "choice"},
        {"item_id": "b", "subquestion_no": 1, "blank_no": 1, "answer": "B", "answer_match_type": "choice"},
    ]))
    assert "ANSWER_REVIEW_INVALID" in _codes(result)


def test_preflight_and_summary_http_contract(db):
    batch, item, _ = _case(db)
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.post(f"/api/v1/question-imports/{batch.id}/s5/preflight", json={
            "items": [{"item_id": str(item.id), "expected_revision": item.revision,
                       "route": "library_only"}],
        })
        assert response.status_code == 200
        assert response.json()["items"][0]["batch_eligible"] is True
        summary = client.get(f"/api/v1/question-imports/{batch.id}/s5/summary")
        assert summary.status_code == 200
        assert summary.json()["counts"]["ready_for_library_confirmation"] == 1
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_review_approval_is_version_bound_and_patch_cannot_bypass(db):
    batch, item, _ = _case(db)
    with pytest.raises(ImportProblem) as bypass:
        update_candidate(db, batch.id, item.id, expected_revision=1,
                         changes={"teacher_decision": "approved", "commit_intent": "library_only"})
    assert bypass.value.code == "INVALID_CANDIDATE"
    saved = decide_item(db, batch.id, item.id, S5ItemDecisionRequest(
        expected_revision=1, decision="approved", route="library_only"))
    assert saved["revision"] == 2
    assert read_item_reviews(db, batch.id)["items"][0]["valid"] is True
    assert read_item_reviews(db, batch.id)["items"][0]["route"] == "library_only"
    assert db.scalar(select(QuestionImportAuditEvent).where(
        QuestionImportAuditEvent.item_id == item.id,
        QuestionImportAuditEvent.kind == "question")).candidate_revision == 2
    with pytest.raises(ImportProblem) as conflict:
        decide_item(db, batch.id, item.id, S5ItemDecisionRequest(
            expected_revision=1, decision="approved", route="library_only"))
    assert conflict.value.code == "REVISION_CONFLICT"
    update_candidate(db, batch.id, item.id, expected_revision=2,
                     changes={"tags": {"difficulty": 3}})
    assert item.payload["teacher_decision"] == "pending"
    review = read_item_reviews(db, batch.id)["items"][0]
    assert review["recorded_decision"] == "approved" and review["valid"] is False


def test_direct_pool_has_separate_answer_event_and_rejection_keeps_reason(db):
    batch, item, _ = _case(db)
    item.payload = {**item.payload, "selected_answer_id": "a1", "answer_match_status": "matched",
                    "answers": [{"answer_id": "a1", "answer_source": "source_extracted", "text": "A"}]}
    db.flush()
    saved = decide_item(db, batch.id, item.id, S5ItemDecisionRequest(
        expected_revision=1, decision="approved", route="direct_pool",
        answer_review_items=[{"item_id": "answer-1", "answer": "A", "answer_match_type": "choice"}]))
    assert saved["answer_confirmed"] is True
    assert read_item_reviews(db, batch.id)["items"][0]["answer_confirmed"] is True
    assert len(list(db.scalars(select(QuestionImportAuditEvent).where(
        QuestionImportAuditEvent.item_id == item.id)))) == 2
    rejected = decide_item(db, batch.id, item.id, S5ItemDecisionRequest(
        expected_revision=2, decision="rejected", reason="题干不完整"))
    assert rejected["revision"] == 3
    review = read_item_reviews(db, batch.id)["items"][0]
    assert review["decision"] == "rejected" and review["reason"] == "题干不完整"
    assert review["answer_confirmed"] is False


def test_review_refuses_pending_nearby_even_when_called_directly(db):
    batch, item, check = _case(db)
    formal = db.scalar(select(Question).where(Question.has_image.is_(False),
                                              Question.archived_at.is_(None)))
    db.add(QuestionImportCatalogMatch(
        check_id=check.id, question_id=formal.id, original_qid=formal.original_qid,
        catalog_review_revision=formal.review_revision, catalog_signature=_catalog_signature(formal),
        kind="nearby", similarity=0.5, evidence={}, decision="pending"))
    db.flush()
    with pytest.raises(ImportProblem) as blocked:
        decide_item(db, batch.id, item.id, S5ItemDecisionRequest(
            expected_revision=1, decision="approved", route="library_only"))
    assert blocked.value.code == "S5_PREFLIGHT_BLOCKED"
    assert item.revision == 1


def test_issue_decision_is_bound_to_evidence(db):
    batch, _, _ = _case(db)
    batch.checkpoint = {**batch.checkpoint, "s2": {"units": [
        {"core_first_page": 1, "unresolved_fragments": [
            {"page": 1, "text": "无法判断的残片", "reason": "无法分类"}]}]}}
    db.flush()
    issue = list_review_issues(db, batch.id)[0]
    saved = decide_issue(db, batch.id, S5IssueDecisionRequest(
        issue_key=issue["key"], signature=issue["signature"], decision="deferred",
        reason="等待核对原件"))
    assert saved["decision"] == "deferred"
    assert list_review_issues(db, batch.id)[0]["handling"]["valid"] is True
    batch.checkpoint = {**batch.checkpoint, "s2": {"units": [
        {"core_first_page": 1, "unresolved_fragments": [
            {"page": 1, "text": "残片已变化", "reason": "无法分类"}]}]}}
    db.flush()
    assert list_review_issues(db, batch.id)[0]["handling"]["valid"] is False
    with pytest.raises(ImportProblem) as stale:
        decide_issue(db, batch.id, S5IssueDecisionRequest(
            issue_key=issue["key"], signature=issue["signature"], decision="acknowledged"))
    assert stale.value.code == "REVISION_CONFLICT"


def test_candidate_overlap_issue_blocks_until_explicitly_resolved(db):
    batch, item, _ = _case(db)
    batch.checkpoint = {**batch.checkpoint, "s2": {"units": [
        {"overlap_suspects": [{"other_item_id": str(item.id),
                               "candidate_item_id": str(item.id)}]}]}}
    db.flush()
    assert "REVIEW_ISSUE_PENDING" in _codes(preflight_import(db, batch.id, _request(item)))
    issue = list_review_issues(db, batch.id)[0]
    decide_issue(db, batch.id, S5IssueDecisionRequest(
        issue_key=issue["key"], signature=issue["signature"], decision="acknowledged",
        reason="已对照原页确认只有一道题"))
    assert preflight_import(db, batch.id, _request(item))["items"][0]["batch_eligible"] is True


def test_manual_answer_claim_uses_source_identity_and_invalidates_review(db):
    batch, item, _ = _case(db)
    file_id = str(uuid.uuid4())
    source_answer = {"source_file_id": file_id, "page_start": 1, "page_end": 1,
                     "original_number": "1", "subquestion_number": None,
                     "text": "A", "reason": "manual"}
    batch.source_mode = "combined"
    batch.files = [{"file_id": file_id, "role": "questions"}]
    batch.checkpoint = {**batch.checkpoint, "s3": {**batch.checkpoint["s3"],
                        "source_answer_reconciliation": {"unassigned_answers": [source_answer]}}}
    db.flush()
    issue = next(row for row in list_review_issues(db, batch.id) if row["kind"] == "answer")
    result = claim_source_answer(db, batch.id, S5AnswerClaimRequest(
        issue_key=issue["key"], signature=issue["signature"],
        item_id=item.id, expected_revision=1))
    assert result["revision"] == 2
    assert item.payload["selected_answer_id"] == result["answer_id"]
    assert not any(row["kind"] == "answer" for row in list_review_issues(db, batch.id))
    assert item.payload["teacher_decision"] == "pending"


def test_s5_review_http_contract(db):
    batch, item, _ = _case(db)
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.post(f"/api/v1/question-imports/{batch.id}/s5/items/{item.id}/decision",
                               json={"expected_revision": 1, "decision": "approved",
                                     "route": "library_only"})
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == 2
        reviews = client.get(f"/api/v1/question-imports/{batch.id}/s5/reviews")
        assert reviews.status_code == 200
        assert reviews.json()["items"][0]["valid"] is True
        counts = client.get(f"/api/v1/question-imports/{batch.id}/s5/summary").json()["counts"]
        assert counts["approved"] == 1
        assert counts["ready_for_library_confirmation"] == 0
        bypass = client.patch(f"/api/v1/question-imports/{batch.id}/items/{item.id}",
                              json={"expected_revision": 2, "changes": {"teacher_decision": "rejected"}})
        assert bypass.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_unassigned_image_is_inventory_only_not_review_issue(db):
    batch, _, _ = _case(db)
    asset_id = str(uuid.uuid4())
    batch.checkpoint = {**batch.checkpoint, "s3": {**batch.checkpoint["s3"],
                        "images": {"assets": [{"asset_id": asset_id, "kind": "embedded",
                                                "page": 1, "source_file_id": "source-1",
                                                "sha256": "f" * 64}]}}}
    db.flush()
    assert not any(row["kind"] == "image" for row in list_review_issues(db, batch.id))
    assert import_review_summary(db, batch.id)["counts"]["unassigned_images"] == 1


def test_uncertain_answer_source_requires_separate_confirmation(db):
    batch, item, _ = _case(db)
    item.payload = {**item.payload, "selected_answer_id": "a1", "answer_match_status": "uncertain",
                    "answers": [{"answer_id": "a1", "answer_source": "source_extracted",
                                 "text": "A", "teacher_confirmed": False}]}
    db.flush()
    review_items = [{"item_id": "answer-1", "answer": "A", "answer_match_type": "choice"}]
    assert "ANSWER_SOURCE_UNCERTAIN" in _codes(preflight_import(
        db, batch.id, _request(item, route="direct_pool", answer_review_items=review_items)))
    source = confirm_answer_source(db, batch.id, item.id, S5AnswerSourceRequest(
        expected_revision=1, selected_answer_id="a1"))
    assert source["source_confirmed"] is True and source["answer_content_confirmed"] is False
    assert preflight_import(db, batch.id, _request(item, route="direct_pool",
                                               answer_review_items=review_items))["items"][0]["batch_eligible"] is True
    assert read_item_reviews(db, batch.id)["items"][0]["decision"] == "pending"


def test_bulk_library_approval_is_one_group_and_all_items_valid(db):
    batch, first, _ = _case(db)
    second = _second_item(db, batch, first)
    result = decide_items_bulk(db, batch.id, S5BulkDecisionRequest(
        decision="approved", route="library_only", items=[
            {"item_id": first.id, "expected_revision": 1},
            {"item_id": second.id, "expected_revision": 1},
        ]))
    assert result["count"] == 2
    assert first.revision == second.revision == 2
    assert all(row["valid"] and row["route"] == "library_only"
               for row in read_item_reviews(db, batch.id)["items"])
    events = list(db.scalars(select(QuestionImportAuditEvent).where(
        QuestionImportAuditEvent.batch_id == batch.id)))
    assert len(events) == 2
    assert {row.detail["batch_action_id"] for row in events} == {result["batch_action_id"]}


def test_bulk_blocker_or_revision_conflict_writes_nothing(db):
    batch, first, _ = _case(db)
    second = _second_item(db, batch, first)
    for revision, code in [(2, "REVISION_CONFLICT"), (1, "DUPLICATE_CHECK_STALE")]:
        if code == "DUPLICATE_CHECK_STALE":
            check = db.scalar(select(QuestionImportCatalogCheck).where(
                QuestionImportCatalogCheck.item_id == second.id))
            check.near_version = "obsolete"
            db.flush()
        with pytest.raises(ImportProblem) as caught:
            decide_items_bulk(db, batch.id, S5BulkDecisionRequest(
                decision="approved", route="library_only", items=[
                    {"item_id": first.id, "expected_revision": 1},
                    {"item_id": second.id, "expected_revision": revision},
                ]))
        assert caught.value.code == "S5_BULK_BLOCKED"
        assert code in {blocker["code"] for row in caught.value.details
                        for blocker in row["blockers"]}
        assert first.revision == second.revision == 1
        assert not list(db.scalars(select(QuestionImportAuditEvent).where(
            QuestionImportAuditEvent.batch_id == batch.id)))


def test_bulk_direct_pool_records_separate_answers_and_rejection_reason(db):
    batch, first, _ = _case(db)
    second = _second_item(db, batch, first)
    for item in (first, second):
        item.payload = {**item.payload, "selected_answer_id": "a1", "answer_match_status": "matched",
                        "answers": [{"answer_id": "a1", "answer_source": "source_extracted",
                                     "text": "A", "teacher_confirmed": False}]}
        check = db.scalar(select(QuestionImportCatalogCheck).where(
            QuestionImportCatalogCheck.item_id == item.id))
        check.candidate_signature = _candidate_signature(db, batch.id, item)
    db.flush()
    result = decide_items_bulk(db, batch.id, S5BulkDecisionRequest(
        decision="approved", route="direct_pool", items=[
            {"item_id": item.id, "expected_revision": 1,
             "answer_review_items": [{"item_id": "answer-1", "answer": "A",
                                      "answer_match_type": "choice"}]}
            for item in (first, second)]))
    assert result["count"] == 2
    reviews = read_item_reviews(db, batch.id)["items"]
    assert all(row["valid"] and row["answer_confirmed"] for row in reviews)
    events = list(db.scalars(select(QuestionImportAuditEvent).where(
        QuestionImportAuditEvent.batch_id == batch.id)))
    assert len(events) == 4
    assert {event.detail.get("batch_action_id") for event in events} == {result["batch_action_id"]}

    other, candidate, _ = _case(db)
    peer = _second_item(db, other, candidate)
    rejected = decide_items_bulk(db, other.id, S5BulkDecisionRequest(
        decision="rejected", reason="题干与原卷不符", items=[
            {"item_id": candidate.id, "expected_revision": 1},
            {"item_id": peer.id, "expected_revision": 1}]))
    assert rejected["count"] == 2
    assert all(row["decision"] == "rejected" and row["reason"] == "题干与原卷不符"
               for row in read_item_reviews(db, other.id)["items"])


@pytest.mark.parametrize("source_mode", ["questions_only", "combined", "separate"])
def test_bulk_library_approval_supports_each_source_mode(db, source_mode):
    batch, item, _ = _case(db)
    batch.source_mode = source_mode
    if source_mode == "separate":
        batch.checkpoint = {**batch.checkpoint, "s3": {**batch.checkpoint["s3"],
                            "separate_answers": {"status": "completed"}}}
    db.flush()
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        path = f"/api/v1/question-imports/{batch.id}/s5"
        selection = {"item_id": str(item.id), "expected_revision": 1}
        preflight = client.post(f"{path}/preflight", json={"items": [
            {**selection, "route": "library_only"}]})
        assert preflight.status_code == 200, preflight.text
        assert preflight.json()["summary"]["eligible"] == 1
        result = client.post(f"{path}/items/bulk-decision", json={
            "decision": "approved", "route": "library_only", "items": [selection]})
        assert result.status_code == 200, result.text
        assert result.json()["count"] == 1
        reviews = client.get(f"{path}/reviews")
        assert reviews.status_code == 200
        assert reviews.json()["items"][0]["valid"] is True
        summary = client.get(f"{path}/summary")
        assert summary.status_code == 200
        assert summary.json()["counts"]["approved"] == 1
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_bulk_api_reports_per_item_blockers_and_preserves_atomicity(db):
    batch, first, _ = _case(db)
    second = _second_item(db, batch, first)
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).post(
            f"/api/v1/question-imports/{batch.id}/s5/items/bulk-decision",
            json={"decision": "approved", "route": "library_only", "items": [
                {"item_id": str(first.id), "expected_revision": 1},
                {"item_id": str(second.id), "expected_revision": 2},
            ]})
        assert response.status_code == 409, response.text
        body = response.json()
        assert body["code"] == "S5_BULK_BLOCKED"
        assert len(body["details"]) == 2
        assert body["details"][1]["blockers"][0]["code"] == "REVISION_CONFLICT"
        assert first.revision == second.revision == 1
        assert not list(db.scalars(select(QuestionImportAuditEvent).where(
            QuestionImportAuditEvent.batch_id == batch.id)))
    finally:
        app.dependency_overrides.pop(get_db, None)
