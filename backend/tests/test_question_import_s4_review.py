from __future__ import annotations

import uuid
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import engine
from app.models import Question, QuestionImportBatch, QuestionImportItem
from app.services.question_fingerprint_v2 import catalog_content_key, fingerprint
from app.services.question_import import CATALOG_PATH, ImportProblem, update_candidate
from app.services.question_import_s4_review import (
    decide_catalog_match, read_catalog_review, run_catalog_review,
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


def _scenario(db: Session, *, candidate_text: str, catalog_text: str):
    source = db.scalar(select(Question).where(Question.has_image.is_(False),
                                             Question.archived_at.is_(None)))
    assert source is not None
    values = {column.name: getattr(source, column.name) for column in Question.__table__.columns
              if column.name not in {"id", "original_qid", "created_at", "updated_at"}}
    question = Question(id=uuid.uuid4(), original_qid="s4-review-" + uuid.uuid4().hex, **values)
    question.question_text = catalog_text
    question.options = []
    question.assets = []
    question.has_image = False
    question.exact_fingerprint_v2 = fingerprint(catalog_content_key(catalog_text, [], []))
    db.add(question)
    batch_id = uuid.uuid4()
    db.add(QuestionImportBatch(
        id=batch_id, owner_scope="internal-demo", request_fingerprint=uuid.uuid4().hex,
        catalog_snapshot_sha256=hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest(), source_mode="questions_only",
        status="review_pending", stage="s2_completed", files=[],
        checkpoint={"s3": {"global_dedupe": {"status": "completed"}}},
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    db.flush()
    item = QuestionImportItem(batch_id=batch_id, ordinal=1, revision=1, is_fake=False,
                              payload={"content": {"question_text": candidate_text,
                                                   "options": [], "asset_ids": []},
                                       "teacher_decision": "pending", "revision": 1})
    db.add(item)
    db.flush()
    return batch_id, item, question


def test_exact_decision_is_saved_and_content_change_invalidates_it(db):
    batch_id, item, question = _scenario(db, candidate_text="计算 1+2 米，结果是多少？",
                                         catalog_text="计算 1+2 米，结果是多少？")
    review = run_catalog_review(db, batch_id)
    row = review["items"][0]
    match = next(value for value in row["matches"] if value["question_id"] == str(question.id))
    assert match["kind"] == "exact" and match["decision"] == "pending"
    assert not row["candidate_stale"] and not row["catalog_stale"]

    with pytest.raises(ImportProblem) as invalid:
        decide_catalog_match(db, batch_id, item.id, uuid.UUID(match["id"]),
                             expected_revision=item.revision, decision="keep_variant", reason="不同")
    assert invalid.value.code == "EXACT_DUPLICATE"

    saved = decide_catalog_match(db, batch_id, item.id, uuid.UUID(match["id"]),
                                 expected_revision=item.revision, decision="skip_existing", reason=None)
    assert saved["items"][0]["matches"][0]["decision"] == "skip_existing"
    assert item.payload["teacher_decision"] == "skipped_duplicate"
    with pytest.raises(ImportProblem) as bypass:
        update_candidate(db, batch_id, item.id, expected_revision=item.revision,
                         changes={"teacher_decision": "approved"})
    assert bypass.value.code == "DUPLICATE_DECISION_REQUIRED"
    again = run_catalog_review(db, batch_id)
    assert again["items"][0]["matches"][0]["decision"] == "skip_existing"

    item.payload = {**item.payload, "content": {"question_text": "计算 1+3 米，结果是多少？",
                                                 "options": [], "asset_ids": []}}
    item.revision += 1
    db.flush()
    assert read_catalog_review(db, batch_id)["items"][0]["candidate_stale"]
    with pytest.raises(ImportProblem) as stale:
        decide_catalog_match(db, batch_id, item.id,
                             uuid.UUID(again["items"][0]["matches"][0]["id"]),
                             expected_revision=item.revision, decision="pending", reason=None)
    assert stale.value.code == "DUPLICATE_DECISION_REQUIRED"
    refreshed = run_catalog_review(db, batch_id)
    assert refreshed["items"][0]["matches"][0]["decision"] == "pending"
    assert item.payload["teacher_decision"] == "pending"


def test_near_variant_needs_reason_and_formal_content_change_invalidates(db):
    batch_id, item, question = _scenario(db, candidate_text="计算 1+3 米，结果是多少？",
                                         catalog_text="计算 1+2 米，结果是多少？")
    review = run_catalog_review(db, batch_id)
    match = next(value for value in review["items"][0]["matches"]
                 if value["question_id"] == str(question.id))
    assert match["kind"] == "nearby"
    with pytest.raises(ImportProblem) as no_reason:
        decide_catalog_match(db, batch_id, item.id, uuid.UUID(match["id"]),
                             expected_revision=item.revision, decision="keep_variant", reason=" ")
    assert no_reason.value.code == "DUPLICATE_DECISION_REQUIRED"
    saved = decide_catalog_match(db, batch_id, item.id, uuid.UUID(match["id"]),
                                 expected_revision=item.revision, decision="keep_variant",
                                 reason="数字条件不同，是独立变式")
    selected = next(value for value in saved["items"][0]["matches"]
                    if value["question_id"] == str(question.id))
    assert selected["decision"] == "keep_variant"
    assert item.payload["teacher_decision"] == "pending"

    question.question_text = "计算 1+4 米，结果是多少？"
    question.exact_fingerprint_v2 = fingerprint(catalog_content_key(question.question_text, [], []))
    question.review_revision += 1
    db.flush()
    assert read_catalog_review(db, batch_id)["items"][0]["catalog_stale"]
    with pytest.raises(ImportProblem) as changed:
        decide_catalog_match(db, batch_id, item.id, uuid.UUID(match["id"]),
                             expected_revision=item.revision, decision="unrelated", reason=None)
    assert changed.value.code == "CATALOG_CHANGED"


def test_existing_near_skip_survives_tighter_display_threshold(db, monkeypatch):
    batch_id, item, question = _scenario(db, candidate_text="计算 1+3 米，结果是多少？",
                                         catalog_text="计算 1+2 米，结果是多少？")
    first = run_catalog_review(db, batch_id)
    match = next(row for row in first["items"][0]["matches"]
                 if row["question_id"] == str(question.id))
    assert match["kind"] == "nearby"
    decide_catalog_match(db, batch_id, item.id, uuid.UUID(match["id"]),
                         expected_revision=item.revision, decision="skip_existing",
                         reason="教师核对后认为属于已有题")
    monkeypatch.setattr("app.services.question_import_s4_near.NEAR_REVIEW_MIN_SIMILARITY", 0.999)

    refreshed = run_catalog_review(db, batch_id)
    retained = next(row for row in refreshed["items"][0]["matches"]
                    if row["question_id"] == str(question.id))
    assert retained["decision"] == "skip_existing"
    assert item.payload["teacher_decision"] == "skipped_duplicate"


def test_generic_candidate_patch_cannot_bypass_duplicate_evidence(db):
    batch_id, item, _ = _scenario(db, candidate_text="计算 1+2 米，结果是多少？",
                                  catalog_text="计算 1+2 米，结果是多少？")
    with pytest.raises(ImportProblem) as error:
        update_candidate(db, batch_id, item.id, expected_revision=item.revision,
                         changes={"teacher_decision": "skipped_duplicate"})
    assert error.value.code == "INVALID_CANDIDATE"


def test_answer_edit_refreshes_conflict_hint_without_invalidating_content_check(db):
    batch_id, item, question = _scenario(db, candidate_text="计算 1+2，结果是多少？",
                                         catalog_text="计算 1+2，结果是多少？")
    question.reference_answer_raw = "3"
    item.payload = {**item.payload, "answers": [{"answer_id": "a1", "text": "3"}],
                    "selected_answer_id": "a1"}
    db.flush()
    checked = run_catalog_review(db, batch_id)
    assert checked["items"][0]["matches"][0]["evidence"]["answer_text_differs"] is False

    item.payload = {**item.payload, "answers": [{"answer_id": "a1", "text": "4"}]}
    item.revision += 1
    db.flush()
    current = read_catalog_review(db, batch_id)
    assert not current["items"][0]["candidate_stale"]
    assert current["items"][0]["matches"][0]["evidence"]["answer_text_differs"] is True
