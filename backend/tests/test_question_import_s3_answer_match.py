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
from app.services.question_import import DOCX_CONVERSION_VERSION, ImportProblem, _path_for, add_candidate, create_batch
from app.services.question_import_s2 import COMBINED_PROMPT_VERSION, candidate_from_question, parse_model_output, plan_units
from app.services.question_import_s3 import reconcile_source_answers, source_answer_identity
from app.services.question_import_s3_review import list_review_issues
import app.services.question_import_s3 as s3
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
    batch.checkpoint = {"s2": {"prompt_version": COMBINED_PROMPT_VERSION,
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


def test_high_risk_answer_has_review_issue_when_budget_prevents_model_call(db, tmp_path, monkeypatch):
    batch, items, answer = _batch(db, tmp_path, monkeypatch, repeated=True, budget=0.2,
                                  excerpt="求丙与丁两个有理数的积")
    answer = {**answer, "question_excerpt": ""}
    checkpoint = dict(batch.checkpoint)
    s2 = dict(checkpoint["s2"])
    s2["units"] = [{**s2["units"][0], "source_answers": [answer]}]
    batch.checkpoint = {**checkpoint, "s2": s2}
    db.commit()
    monkeypatch.setattr(matcher, "SessionLocal", _session_factory(db))
    assert matcher.process_answer_matching_one()
    db.refresh(batch)
    assert batch.checkpoint["s3"]["answer_model_matching"]["status"] == "pending_budget"
    issue = next(row for row in list_review_issues(db, batch.id)
                 if row["kind"] == "answer_match_warning")
    assert issue["current_item_id"] == str(items[1].id)
    assert "尚未完成核查" in issue["model_reason"]


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


def test_docx_cached_pdf_supplies_page_clues(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "question_import_dir", tmp_path / "imports")
    batch_id, file_id = uuid4(), uuid4()
    batch = SimpleNamespace(id=batch_id, files=[{
        "role": "questions", "file_id": str(file_id),
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}])
    original = _path_for(batch_id, file_id)
    original.parent.mkdir(parents=True, exist_ok=True)
    converted = original.with_name(original.name + f".{DOCX_CONVERSION_VERSION}.converted.pdf")
    converted.write_bytes(b"cached conversion")
    paths = []

    def reader(path):
        paths.append(path)
        return SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "习题 A1.1\n第一题")])

    monkeypatch.setattr(s3, "PdfReader", reader)
    monkeypatch.setattr(matcher, "PdfReader", reader)
    assert s3._source_sections(batch, "questions") == {1: "a1.1"}
    assert matcher._page_headings(batch, "questions") == {1: "习题 A1.1"}
    assert paths == [str(converted), str(converted)]


def test_combined_page_section_is_model_clue_not_unique_rule_anchor(monkeypatch):
    answer = {"source_file_id": "source", "page_start": 1, "original_number": "1",
              "question_excerpt": "", "text": "42"}
    first = SimpleNamespace(id=uuid4(), revision=1, payload={
        "source": {"original_number": "1", "page_start": 1},
        "content": {"question_text": "求甲的值", "options": []}})
    second = SimpleNamespace(id=uuid4(), revision=1, payload={
        "source": {"original_number": "1", "page_start": 2},
        "content": {"question_text": "求乙的值", "options": []}})
    batch = SimpleNamespace(source_mode="combined", checkpoint={"s3": {
        "source_answer_reconciliation": {"unassigned_answers": []}}})
    monkeypatch.setattr(matcher, "_answers", lambda _: [answer])
    monkeypatch.setattr(matcher, "linked_source_answer_item", lambda *_: first)
    monkeypatch.setattr(matcher, "_replaceable_source_answer", lambda _: True)
    monkeypatch.setattr(matcher, "_source_sections", lambda *_: {1: "第一讲", 2: "第二讲"})
    monkeypatch.setattr(matcher, "_page_headings", lambda *_: {})

    groups, skipped = matcher._groups(batch, [first, second])
    assert skipped == []
    assert len(groups) == 1
    assert groups[0]["context"]["answers"][0]["section"] == "第一讲"
    assert [row["section"] for row in groups[0]["context"]["questions"]] == ["第一讲", "第二讲"]


def test_oversized_answer_matching_covers_all_candidates_and_splits_answer_text(monkeypatch):
    answers = [{"source_file_id": "source", "page_start": index + 1,
                "original_number": "9", "question_excerpt": "", "text": "答案" * 100}
               for index in range(3)]
    items = [SimpleNamespace(id=uuid4(), revision=1, payload={
        "source": {"original_number": "1", "page_start": 1},
        "content": {"question_text": f"求第 {index} 题", "options": []}})
             for index in range(81)]
    batch = SimpleNamespace(source_mode="combined", checkpoint={"s3": {
        "source_answer_reconciliation": {"unassigned_answers": answers}}})
    monkeypatch.setattr(matcher, "_answers", lambda _: answers)
    monkeypatch.setattr(matcher, "linked_source_answer_item", lambda *_: None)
    monkeypatch.setattr(matcher, "_source_sections", lambda *_: {})
    monkeypatch.setattr(matcher, "_page_headings", lambda *_: {})
    groups, skipped = matcher._groups(batch, items)
    assert skipped == []
    assert {row["item_id"] for group in groups for row in group["context"]["questions"]} == {
        str(item.id) for item in items}
    for answer in answers:
        shards = [group for group in groups
                  if group.get("bundle_id") == source_answer_identity(answer)]
        assert len(shards) == 2
        assert {row["item_id"] for group in shards for row in group["context"]["questions"]} == {
            str(item.id) for item in items}
        assert all(len(group["context"]["questions"]) <= 80 for group in shards)

    two_items = items[:2]
    monkeypatch.setattr(matcher, "MAX_GROUP_ANSWERS", 1)
    one = matcher._groups(batch, two_items)[0][0]["context"]
    monkeypatch.setattr(matcher, "MAX_GROUP_ANSWERS", 10)
    single_size = len(json.dumps(one, ensure_ascii=False))
    double_size = len(json.dumps({"answers": [one["answers"][0], one["answers"][0]],
                                  "questions": one["questions"]}, ensure_ascii=False))
    monkeypatch.setattr(matcher, "MAX_GROUP_CHARS", (single_size + double_size) // 2)
    groups, skipped = matcher._groups(batch, two_items)
    assert skipped == []
    assert len(groups) == len(answers)
    assert all(len(group["context"]["answers"]) == 1 for group in groups)
    assert all(len(group["context"]["questions"]) == 2 for group in groups)


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


def test_candidate_shards_wait_for_all_results_before_assigning(db, tmp_path, monkeypatch):
    batch, items, answer = _batch(db, tmp_path, monkeypatch, excerpt="无法确认题干")
    other = add_candidate(db, batch, {"content": {"question_text": "求丙与丁两个有理数的积",
                                                  "options": [], "asset_ids": []},
                                      "source": {"original_number": "1", "page_start": 1}})
    db.commit()
    monkeypatch.setattr(matcher, "MAX_GROUP_CANDIDATES", 1)
    monkeypatch.setattr(matcher, "SessionLocal", _session_factory(db))
    calls = []

    class Provider:
        def review(self, context, *, capture_response=None):
            calls.append(context)
            question = context["questions"][0]
            return [{"answer_id": source_answer_identity(answer),
                     "candidate_item_id": question["item_id"],
                     "evidence_quote": "甲与乙两个有理数" if question["item_id"] == str(items[0].id)
                                       else "丙与丁两个有理数",
                     "reason": "题干相符"}], {"usage": {"prompt_tokens": 100, "completion_tokens": 20},
                                         "estimated_cny": 0.001, "response_id": str(len(calls))}

    monkeypatch.setattr(matcher, "QwenAnswerMatchProvider", Provider)
    assert matcher.process_answer_matching_one()
    db.refresh(batch)
    db.refresh(items[0])
    assert batch.checkpoint["s3"]["answer_model_matching"]["status"] == "queued"
    assert not items[0].payload["answers"]
    assert matcher.process_answer_matching_one()
    db.refresh(batch)
    db.refresh(items[0])
    db.refresh(other)
    state = batch.checkpoint["s3"]["answer_model_matching"]
    assert state["status"] == "completed" and state["calls"] == 2
    assert not state["assignments"]
    assert not items[0].payload.get("answers") and not other.payload.get("answers")
    assert "冲突建议" in state["suggestions"][0]["reason"]
    issue = next(row for row in list_review_issues(db, batch.id) if row["kind"] == "answer")
    assert "冲突建议" in issue["model_reason"]


def test_candidate_shards_accept_one_clear_unmatched_suggestion(db, tmp_path, monkeypatch):
    batch, items, answer = _batch(db, tmp_path, monkeypatch, excerpt="无法确认题干")
    add_candidate(db, batch, {"content": {"question_text": "求丙与丁两个有理数的积",
                                         "options": [], "asset_ids": []},
                              "source": {"original_number": "1", "page_start": 1}})
    db.commit()
    monkeypatch.setattr(matcher, "MAX_GROUP_CANDIDATES", 1)
    monkeypatch.setattr(matcher, "SessionLocal", _session_factory(db))

    class Provider:
        def review(self, context, *, capture_response=None):
            proposed = str(items[0].id) if context["questions"][0]["item_id"] == str(items[0].id) else None
            return [{"answer_id": source_answer_identity(answer), "candidate_item_id": proposed,
                     "evidence_quote": "甲与乙两个有理数" if proposed else "",
                     "reason": "题干相符" if proposed else "本片段没有对应题"}], {
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                "estimated_cny": 0.001, "response_id": "shard"}

    monkeypatch.setattr(matcher, "QwenAnswerMatchProvider", Provider)
    assert matcher.process_answer_matching_one()
    db.refresh(items[0])
    assert not items[0].payload.get("answers")
    assert matcher.process_answer_matching_one()
    db.refresh(batch)
    db.refresh(items[0])
    assert batch.checkpoint["s3"]["answer_model_matching"]["status"] == "completed"
    assert items[0].payload["answers"][0]["match_method"] == "model_assisted"


def test_candidate_shards_show_high_risk_disagreement_without_moving_answer(db, tmp_path, monkeypatch):
    batch, items, answer = _batch(db, tmp_path, monkeypatch, repeated=True,
                                  excerpt="求丙与丁两个有理数的积")
    assert items[1].payload.get("answers")
    other = add_candidate(db, batch, {"content": {"question_text": "求戊与己两个有理数的差",
                                                  "options": [], "asset_ids": []},
                                      "source": {"original_number": "1", "page_start": 1},
                                      "teacher_decision": "pending"})
    answer = {**answer, "question_excerpt": ""}
    checkpoint = dict(batch.checkpoint)
    s2 = dict(checkpoint["s2"])
    s2["units"] = [{**s2["units"][0], "source_answers": [answer]}]
    batch.checkpoint = {**checkpoint, "s2": s2}
    db.commit()
    monkeypatch.setattr(matcher, "MAX_GROUP_CANDIDATES", 2)
    monkeypatch.setattr(matcher, "SessionLocal", _session_factory(db))

    class Provider:
        def review(self, context, *, capture_response=None):
            ids = {row["item_id"] for row in context["questions"]}
            proposed = str(other.id) if str(other.id) in ids else str(items[1].id)
            return [{"answer_id": source_answer_identity(answer), "candidate_item_id": proposed,
                     "evidence_quote": "戊与己两个有理数" if proposed == str(other.id)
                                       else "丙与丁两个有理数",
                     "reason": "题干对应"}], {"usage": {"prompt_tokens": 100, "completion_tokens": 20},
                                          "estimated_cny": 0.001, "response_id": "shard"}

    monkeypatch.setattr(matcher, "QwenAnswerMatchProvider", Provider)
    assert matcher.process_answer_matching_one()
    assert matcher.process_answer_matching_one()
    db.refresh(batch)
    db.refresh(items[1])
    db.refresh(other)
    assert items[1].payload["answers"][0]["text"] == "42"
    assert not other.payload.get("answers")
    assert batch.checkpoint["s3"]["answer_model_matching"]["review_warnings"], batch.checkpoint["s3"]["answer_model_matching"]
    issue = next(row for row in list_review_issues(db, batch.id)
                 if row["kind"] == "answer_match_disagreement")
    assert issue["current_item_id"] == str(items[1].id)
    assert issue["model_suggestion"] == str(other.id)


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
