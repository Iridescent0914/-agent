from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import get_db
from app.main import app
from app.models import Question
from app.schemas.question_import_s5 import S5ItemDecisionRequest
from app.schemas.question_import_s6 import S6CommitSelection, S6PreflightRequest
from app.services.question_fingerprint_v2 import fingerprint
from app.services.question_import_s4_exact import _candidate_key
from app.services.question_import_s5_review import decide_item
from app.services.question_import_s6_preflight import preflight_commit
from tests.test_question_import_s5_preflight import _case, _second_item, db


def _selection(item, event_id):
    return S6CommitSelection(item_id=item.id, expected_revision=item.revision,
                             question_event_id=uuid.UUID(event_id))


def _codes(row):
    return {problem["code"] for problem in row["blockers"]}


def test_requires_current_s5_approval_and_exact_event(db):
    batch, item, _ = _case(db)
    missing = preflight_commit(db, batch.id, S6PreflightRequest(items=[
        _selection(item, str(uuid.uuid4()))]))
    assert _codes(missing["items"][0]) == {"REVIEW_NOT_APPROVED"}

    saved = decide_item(db, batch.id, item.id, S5ItemDecisionRequest(
        expected_revision=item.revision, decision="approved", route="library_only"))
    request = S6PreflightRequest(items=[_selection(item, saved["question_event_id"])])
    ready = preflight_commit(db, batch.id, request)
    assert ready["summary"] == {"selected": 1, "eligible": 1, "blocked": 0,
                                "library_only": 1, "direct_pool": 0}
    assert ready["items"][0]["eligible"] is True

    stale_event = preflight_commit(db, batch.id, S6PreflightRequest(items=[
        _selection(item, str(uuid.uuid4()))]))
    assert "REVIEW_CHANGED" in _codes(stale_event["items"][0])
    assert "REVISION_CONFLICT" in _codes(preflight_commit(db, batch.id,
        S6PreflightRequest(items=[S6CommitSelection(
            item_id=item.id, expected_revision=item.revision - 1,
            question_event_id=uuid.UUID(saved["question_event_id"]))]))["items"][0])


def test_new_catalog_exact_match_blocks_after_s5_approval(db):
    batch, item, _ = _case(db)
    saved = decide_item(db, batch.id, item.id, S5ItemDecisionRequest(
        expected_revision=item.revision, decision="approved", route="library_only"))
    formal = db.scalar(select(Question).where(Question.has_image.is_(False),
                                              Question.archived_at.is_(None)))
    assert formal is not None
    formal.question_text = item.payload["content"]["question_text"]
    formal.options = item.payload["content"]["options"]
    formal.exact_fingerprint_v2 = None
    db.flush()
    unindexed = preflight_commit(db, batch.id, S6PreflightRequest(items=[
        _selection(item, saved["question_event_id"])]))
    assert "UNINDEXED_CATALOG" in {warning["code"] for warning in unindexed["items"][0]["warnings"]}
    formal.exact_fingerprint_v2 = fingerprint(_candidate_key(db, batch.id, item))
    db.flush()

    result = preflight_commit(db, batch.id, S6PreflightRequest(items=[
        _selection(item, saved["question_event_id"])]))
    assert "EXACT_DUPLICATE" in _codes(result["items"][0])
    assert result["summary"]["blocked"] == 1


def test_selected_exact_duplicates_and_http_contract(db):
    batch, first, _ = _case(db)
    second = _second_item(db, batch, first)
    second.payload = {**second.payload, "content": dict(first.payload["content"])}
    db.flush()
    # S3 ordinarily removes batch duplicates. Verify S6 still catches a later edit.
    from app.services.question_import_s4_review import _candidate_signature
    from app.models import QuestionImportCatalogCheck
    check = db.scalar(select(QuestionImportCatalogCheck).where(
        QuestionImportCatalogCheck.item_id == second.id))
    check.candidate_signature = _candidate_signature(db, batch.id, second)
    db.flush()
    first_saved = decide_item(db, batch.id, first.id, S5ItemDecisionRequest(
        expected_revision=first.revision, decision="approved", route="library_only"))
    second_saved = decide_item(db, batch.id, second.id, S5ItemDecisionRequest(
        expected_revision=second.revision, decision="approved", route="library_only"))
    request = S6PreflightRequest(items=[
        _selection(first, first_saved["question_event_id"]),
        _selection(second, second_saved["question_event_id"]),
    ])
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).post(
            f"/api/v1/question-imports/{batch.id}/s6/preflight",
            json=request.model_dump(mode="json"))
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 200
    result = response.json()
    assert result["summary"]["blocked"] == 2
    assert all("EXACT_DUPLICATE_IN_SELECTION" in _codes(row) for row in result["items"])
