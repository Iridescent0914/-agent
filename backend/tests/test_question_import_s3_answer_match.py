from __future__ import annotations

import json
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import engine
from app.services.question_import import ImportProblem, add_candidate, create_batch
from app.services.question_import_s2 import INCLUSIVE_COMBINED_PROMPT_VERSION, candidate_from_question, parse_model_output, plan_units
from app.services.question_import_s3 import reconcile_source_answers, source_answer_identity
from app.services.question_import_s3_review import list_review_issues
import app.services.question_import_s3_answer_match as matcher


@pytest.fixture
def db():
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
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


def _batch(db, tmp_path, monkeypatch, *, automatic=True, repeated=False, budget=2.0,
           excerpt="求甲与乙两个有理数的和"):
    monkeypatch.setattr(get_settings(), "question_import_dir", tmp_path / "imports")
    batch = create_batch(db, source_mode="combined", files=[("questions", "sample.pdf", _pdf())])
    source_id = batch.files[0]["file_id"]
    unit = plan_units(1, pages_per_unit=1, overlap=0)[0]
    texts = ["求甲与乙两个有理数的和", "求丙与丁两个有理数的积"] if repeated else ["求甲与乙两个有理数的和"]
    items = []
    for text in texts:
        question = parse_model_output({"complete": True, "questions": [{
            "original_number": "1", "question_text": text, "options": [], "subquestions": [],
            "math_fragments": [], "diagram_clues": [], "page_start": 1, "page_end": 1,
            "uncertain_fragments": [],
        }], "unresolved_fragments": []}, unit).questions[0]
        items.append(add_candidate(db, batch, candidate_from_question(source_id, question, unit)))
    answer = {"source_file_id": source_id, "original_number": "1" if repeated else "9",
              "subquestion_number": None, "text": "42", "page_start": 1, "page_end": 1,
              "question_excerpt": excerpt, "uncertain_fragments": []}
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    batch.checkpoint = {"s2": {"prompt_version": INCLUSIVE_COMBINED_PROMPT_VERSION,
                                "auto_answer_matching": automatic, "budget_cny": budget,
                                "estimated_cny": 0.1, "units": [{"source_answers": [answer]}]},
                        "s3": {"global_dedupe": {"status": "completed", "estimated_cny": 0.1}}}
    db.flush()
    reconcile_source_answers(db, batch.id)
    db.commit()
    return batch, items, answer


def _session_factory(db):
    connection = db.get_bind()
    return lambda: Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")


def test_provider_uses_extracted_text_and_rejects_unknown_candidate():
    context = {"answers": [{"answer_id": "a", "text": "42"}],
               "questions": [{"item_id": "q", "stem": "求和"}]}
    captured = {}

    def respond(request: httpx.Request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "response-1", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps({"decisions": [{"answer_id": "a",
                "candidate_item_id": "made-up", "evidence_quote": "求和", "reason": "相同"}]})}}],
            "usage": {"prompt_tokens": 30, "completion_tokens": 12}})

    provider = matcher.QwenAnswerMatchProvider(transport=httpx.MockTransport(respond))
    provider.settings = Settings(question_import_s2_api_key="test-only",
                                 question_import_s2_base_url="https://example.invalid/compatible-mode/v1")
    with pytest.raises(ImportProblem) as exc:
        provider.review(context)
    assert exc.value.code == "S3_MATCH_OUTPUT_INVALID"
    assert isinstance(captured["messages"][0]["content"], str)
    assert "file_data" not in json.dumps(captured)


def test_new_combined_batch_auto_matches_unassigned_answer(db, tmp_path, monkeypatch):
    batch, items, answer = _batch(db, tmp_path, monkeypatch)
    assert not items[0].payload["answers"]
    monkeypatch.setattr(matcher, "SessionLocal", _session_factory(db))
    calls = []

    class Provider:
        def review(self, context, *, capture_response=None):
            calls.append(context)
            return [{"answer_id": source_answer_identity(answer), "candidate_item_id": str(items[0].id),
                     "evidence_quote": "甲与乙两个有理数", "reason": "题干与答案位置相符"}], {
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                "estimated_cny": 0.001, "response_id": "matched"}

    monkeypatch.setattr(matcher, "QwenAnswerMatchProvider", Provider)
    assert matcher.process_answer_matching_one()
    db.refresh(batch)
    db.refresh(items[0])
    state = batch.checkpoint["s3"]["answer_model_matching"]
    assert state["status"] == "completed"
    assert state["calls"] == 1
    assert state["assignments"][source_answer_identity(answer)] == str(items[0].id)
    assert items[0].payload["answers"][0]["match_method"] == "model_assisted"
    assert items[0].payload["answers"][0]["teacher_confirmed"] is False
    assert matcher.process_answer_matching_one() is False
    assert len(calls) == 1


def test_old_batch_does_not_auto_spend_and_new_batch_pauses_without_budget(db, tmp_path, monkeypatch):
    old, _, _ = _batch(db, tmp_path / "old", monkeypatch, automatic=False)
    monkeypatch.setattr(matcher, "SessionLocal", _session_factory(db))
    assert matcher.process_answer_matching_one() is False
    db.refresh(old)
    assert "answer_model_matching" not in old.checkpoint["s3"]

    new, _, _ = _batch(db, tmp_path / "new", monkeypatch, budget=0.2)
    assert matcher.process_answer_matching_one() is True
    db.refresh(new)
    assert new.checkpoint["s3"]["answer_model_matching"]["status"] == "pending_budget"
    assert new.checkpoint["s3"]["answer_model_matching"]["calls"] == 0


def test_model_uncertainty_on_existing_repeated_number_creates_teacher_issue(db, tmp_path, monkeypatch):
    batch, items, answer = _batch(db, tmp_path, monkeypatch, repeated=True,
                                  excerpt="求丙与丁两个有理数的积")
    assert items[1].payload["answers"]
    # An older association can remain after its question excerpt is removed.
    answer = {**answer, "question_excerpt": ""}
    checkpoint = dict(batch.checkpoint)
    s2 = dict(checkpoint["s2"])
    s2["units"] = [{**s2["units"][0], "source_answers": [answer]}]
    batch.checkpoint = {**checkpoint, "s2": s2}
    db.commit()
    assert matcher._groups(batch, items)[0]
    monkeypatch.setattr(matcher, "SessionLocal", _session_factory(db))

    class Provider:
        def review(self, context, *, capture_response=None):
            return [{"answer_id": source_answer_identity(answer), "candidate_item_id": None,
                     "evidence_quote": "", "reason": "仅有短答案，不能确认现有关联"}], {
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                "estimated_cny": 0.001, "response_id": "uncertain"}

    monkeypatch.setattr(matcher, "QwenAnswerMatchProvider", Provider)
    assert matcher.process_answer_matching_one()
    db.refresh(batch)
    db.refresh(items[1])
    state = batch.checkpoint["s3"]["answer_model_matching"]
    assert state["calls"] == 1, (state["status"], state.get("error_message"), state["groups"])
    assert items[1].payload["answers"][0]["text"] == "42"
    issues = list_review_issues(db, batch.id)
    assert len([row for row in issues if row["kind"] == "answer_match_warning"]) == 1, batch.checkpoint["s3"]["answer_model_matching"]
    assert "仅有短答案" in next(row for row in issues if row["kind"] == "answer_match_warning")["model_reason"]


def test_repeated_number_with_unique_excerpt_does_not_call_model(db, tmp_path, monkeypatch):
    batch, items, _ = _batch(db, tmp_path, monkeypatch, repeated=True,
                             excerpt="求丙与丁两个有理数的积")
    groups, skipped = matcher._groups(batch, items)
    assert groups == skipped == []


def test_repeated_number_with_unique_matching_section_is_not_high_risk():
    first = SimpleNamespace(id=uuid4(), payload={"source": {"page_start": 1},
                                               "content": {"question_text": "求甲的值"}})
    second = SimpleNamespace(id=uuid4(), payload={"source": {"page_start": 2},
                                                "content": {"question_text": "求乙的值"}})
    answer = {"page_start": 1, "question_excerpt": ""}
    assert matcher._has_unique_rule_anchor(answer, first, [first, second], [first, second],
                                           {1: "第一讲", 2: "第二讲"}, {1: "第一讲"})
    assert not matcher._has_unique_rule_anchor(answer, first, [first, second], [first, second],
                                               {1: "第一讲", 2: "第二讲"}, {1: "第二讲"})


def test_model_disagreement_shows_current_and_proposed_question(db, tmp_path, monkeypatch):
    batch, items, answer = _batch(db, tmp_path, monkeypatch, repeated=True,
                                  excerpt="求丙与丁两个有理数的积")
    answer = {**answer, "question_excerpt": ""}
    checkpoint = dict(batch.checkpoint)
    s2 = dict(checkpoint["s2"])
    s2["units"] = [{**s2["units"][0], "source_answers": [answer]}]
    batch.checkpoint = {**checkpoint, "s2": s2}
    db.commit()
    assert matcher._groups(batch, items)[0]
    monkeypatch.setattr(matcher, "SessionLocal", _session_factory(db))

    class Provider:
        def review(self, context, *, capture_response=None):
            return [{"answer_id": source_answer_identity(answer), "candidate_item_id": str(items[0].id),
                     "evidence_quote": "甲与乙两个有理数", "reason": "模型认为属于另一题"}], {
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                "estimated_cny": 0.001, "response_id": "disagreement"}

    monkeypatch.setattr(matcher, "QwenAnswerMatchProvider", Provider)
    assert matcher.process_answer_matching_one()
    db.refresh(batch)
    db.refresh(items[1])
    state = batch.checkpoint["s3"]["answer_model_matching"]
    assert state["calls"] == 1, (state["status"], state.get("error_message"), state["groups"])
    assert items[1].payload["answers"][0]["text"] == "42"
    issue = next(row for row in list_review_issues(db, batch.id)
                 if row["kind"] == "answer_match_disagreement")
    assert issue["current_item_id"] == str(items[1].id)
    assert issue["model_suggestion"] == str(items[0].id)
    assert issue["evidence_quote"] == "甲与乙两个有理数"


def test_same_source_answer_repeated_on_one_page_is_counted_once(db, tmp_path, monkeypatch):
    batch, items, answer = _batch(db, tmp_path, monkeypatch)
    checkpoint = dict(batch.checkpoint)
    s2 = dict(checkpoint["s2"])
    s2["units"] = [{**s2["units"][0], "source_answers": [answer, dict(answer)]}]
    batch.checkpoint = {**checkpoint, "s2": s2}
    db.flush()
    result = reconcile_source_answers(db, batch.id, force=True)
    assert result["source_answer_count"] == 2
    assert result["deduplicated_answer_count"] == 1
    assert len(result["unassigned_answers"]) == 1


def test_same_answer_text_with_another_question_number_is_not_deduplicated(db, tmp_path, monkeypatch):
    batch, _, answer = _batch(db, tmp_path, monkeypatch)
    checkpoint = dict(batch.checkpoint)
    s2 = dict(checkpoint["s2"])
    s2["units"] = [{**s2["units"][0], "source_answers": [answer,
                   {**answer, "original_number": "10"}]}]
    batch.checkpoint = {**checkpoint, "s2": s2}
    db.flush()
    result = reconcile_source_answers(db, batch.id, force=True)
    assert result["deduplicated_answer_count"] == 0
    assert len(result["unassigned_answers"]) == 2


def test_clear_model_choice_is_adopted_without_a_second_rule_anchor(db, tmp_path, monkeypatch):
    batch, items, answer = _batch(db, tmp_path, monkeypatch, excerpt="无法确认题干")
    monkeypatch.setattr(matcher, "SessionLocal", _session_factory(db))

    class Provider:
        def review(self, context, *, capture_response=None):
            return [{"answer_id": source_answer_identity(answer), "candidate_item_id": str(items[0].id),
                     "evidence_quote": "甲与乙两个有理数", "reason": "题干内容与答案对应"}], {
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                "estimated_cny": 0.001, "response_id": "guess"}

    monkeypatch.setattr(matcher, "QwenAnswerMatchProvider", Provider)
    assert matcher.process_answer_matching_one()
    db.refresh(batch)
    db.refresh(items[0])
    assert items[0].payload["answers"][0]["match_method"] == "model_assisted"
    assert not batch.checkpoint["s3"]["answer_model_matching"]["suggestions"]
    assert not [row for row in list_review_issues(db, batch.id) if row["kind"] == "answer"]


def test_model_choice_does_not_overwrite_another_source_answer(db, tmp_path, monkeypatch):
    batch, items, answer = _batch(db, tmp_path, monkeypatch)
    payload = dict(items[0].payload)
    payload["answers"] = [{"answer_id": "existing-source-answer", "answer_source": "source_extracted",
                           "text": "99", "teacher_confirmed": False, "evidence": []}]
    items[0].payload = payload
    db.commit()
    monkeypatch.setattr(matcher, "SessionLocal", _session_factory(db))

    class Provider:
        def review(self, context, *, capture_response=None):
            return [{"answer_id": source_answer_identity(answer), "candidate_item_id": str(items[0].id),
                     "evidence_quote": "甲与乙两个有理数", "reason": "题干相符"}], {
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                "estimated_cny": 0.001, "response_id": "occupied"}

    monkeypatch.setattr(matcher, "QwenAnswerMatchProvider", Provider)
    assert matcher.process_answer_matching_one()
    db.refresh(items[0])
    assert items[0].payload["answers"][0]["text"] == "99"
    assert [row for row in list_review_issues(db, batch.id) if row["kind"] == "answer"]


def test_separate_batch_auto_matches_after_answer_extraction(db, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "question_import_dir", tmp_path / "imports")
    batch = create_batch(db, source_mode="separate", files=[
        ("questions", "questions.pdf", _pdf()), ("answers", "answers.pdf", _pdf())])
    unit = plan_units(1, pages_per_unit=1, overlap=0)[0]
    question = parse_model_output({"complete": True, "questions": [{
        "original_number": "1", "question_text": "求甲与乙两个有理数的和",
        "options": [], "subquestions": [], "math_fragments": [], "diagram_clues": [],
        "page_start": 1, "page_end": 1, "uncertain_fragments": [],
    }], "unresolved_fragments": []}, unit).questions[0]
    item = add_candidate(db, batch, candidate_from_question(batch.files[0]["file_id"], question, unit))
    answer = {"source_file_id": batch.files[1]["file_id"], "original_number": "9",
              "subquestion_number": None, "text": "42", "page_start": 1, "page_end": 1,
              "question_excerpt": "求甲与乙两个有理数的和", "uncertain_fragments": []}
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    batch.checkpoint = {"s2": {"units": []}, "s3": {
        "global_dedupe": {"status": "completed"},
        "separate_answers": {"status": "completed", "version": "s3-separate-answers-v1",
                             "auto_answer_matching": True, "budget_cny": 2,
                             "estimated_cny": 0.1, "units": [{"source_answers": [answer]}]}}}
    db.flush()
    reconcile_source_answers(db, batch.id)
    db.commit()
    monkeypatch.setattr(matcher, "SessionLocal", _session_factory(db))

    class Provider:
        def review(self, context, *, capture_response=None):
            assert context["answers"][0]["page"] == 1
            return [{"answer_id": source_answer_identity(answer), "candidate_item_id": str(item.id),
                     "evidence_quote": "甲与乙两个有理数", "reason": "题干摘录唯一对应"}], {
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                "estimated_cny": 0.001, "response_id": "separate-match"}

    monkeypatch.setattr(matcher, "QwenAnswerMatchProvider", Provider)
    assert matcher.process_answer_matching_one()
    db.refresh(item)
    assert item.payload["answers"][0]["evidence"][0]["file_id"] == batch.files[1]["file_id"]
    assert item.payload["answers"][0]["match_method"] == "model_assisted"


def test_pending_budget_requires_explicit_resume_and_keeps_completed_work(db, tmp_path, monkeypatch):
    batch, items, answer = _batch(db, tmp_path, monkeypatch, budget=0.2)
    monkeypatch.setattr(matcher, "SessionLocal", _session_factory(db))
    assert matcher.process_answer_matching_one()
    db.refresh(batch)
    assert batch.checkpoint["s3"]["answer_model_matching"]["status"] == "pending_budget"
    assert matcher.process_answer_matching_one() is False

    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_s2_enabled", True)
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/compatible-mode/v1")
    monkeypatch.setattr(settings, "question_import_s2_operator_token", "operator-test")
    matcher.start_answer_matching(db, batch.id, budget_cny=1, operator_token="operator-test")
    db.commit()
    calls = []

    class Provider:
        def review(self, context, *, capture_response=None):
            calls.append(context)
            return [{"answer_id": source_answer_identity(answer), "candidate_item_id": str(items[0].id),
                     "evidence_quote": "甲与乙两个有理数", "reason": "题干摘录唯一对应"}], {
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                "estimated_cny": 0.001, "response_id": "resumed"}

    monkeypatch.setattr(matcher, "QwenAnswerMatchProvider", Provider)
    assert matcher.process_answer_matching_one()
    db.refresh(batch)
    assert batch.checkpoint["s3"]["answer_model_matching"]["status"] == "completed"
    assert batch.checkpoint["s3"]["answer_model_matching"]["calls"] == 1
    assert matcher.process_answer_matching_one() is False
    assert len(calls) == 1
