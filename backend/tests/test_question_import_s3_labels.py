from __future__ import annotations

from types import SimpleNamespace
from io import BytesIO
import json

import httpx
import pytest
from pypdf import PdfWriter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import engine
from app.models import QuestionImportItem
from app.services.knowledge_catalog import formal_kps_by_code
from app.services.question_import import ImportProblem, add_candidate, create_batch, update_candidate
from app.services import question_import as import_service
from app.services import question_import_s3_labels as labels_service
from app.services.question_import_s3_labels import (
    QwenLabelProvider, _allowed_kps, _groups, _split_failed_group, process_labels_one, start_labels,
    validate_suggestions,
)


ALLOWED = {"R1": {"name": "有理数", "section": "有理数", "chapter_name": "有理数",
                  "grade": "六年级", "available_for_import": True},
           "R2": {"name": "有理数的运算", "section": "有理数的运算", "chapter_name": "有理数",
                  "grade": "六年级", "available_for_import": True}}


def _item(number: int):
    return SimpleNamespace(id=f"item-{number}", revision=1,
                           payload={"content": {"question_text": f"计算 {number}+1", "options": [],
                                                "asset_ids": []}, "extraction": {"subquestions": []}})


def _suggestion(item_id: str, **changes):
    return {"item_id": item_id, "question_type": "calculation", "difficulty": 1,
            "main_kp_code": "R2", "alternative_kp_codes": ["R1"],
            "uncertainty": None, **changes}


def test_label_groups_keep_every_candidate_once():
    groups = _groups([_item(index) for index in range(19)])
    assert [len(group) for group in groups] == [12, 7]
    assert [entry["item_id"] for group in groups for entry in group] == [f"item-{i}" for i in range(19)]


def test_formal_kps_without_explicit_availability_flag_remain_available():
    catalog = formal_kps_by_code()

    class CatalogSession:
        def scalars(self, _statement):
            return iter(catalog)

    allowed = _allowed_kps(CatalogSession())
    assert set(allowed) == set(catalog)
    assert all(point["core_competencies"] for point in allowed.values())


def test_explicit_retry_splits_invalid_group_and_keeps_paid_audit():
    entries = _groups([_item(index) for index in range(12)])[0]
    state = {"error_code": "S3_LABEL_OUTPUT_INVALID", "groups": [
        {"index": 0, "status": "pending", "items": entries,
         "response_id": "paid-invalid", "usage": {"prompt_tokens": 20}}]}
    assert _split_failed_group(state) is True
    assert [(row["index"], row["status"], len(row["items"])) for row in state["groups"]] == [
        (0, "superseded", 12), (1, "pending", 6), (2, "pending", 6)]
    assert state["groups"][0]["response_id"] == "paid-invalid"
    assert _split_failed_group(state) is True
    assert [(row["index"], len(row["items"])) for row in state["groups"][-2:]] == [(3, 3), (4, 3)]


def test_label_output_requires_exact_ids_and_whitelisted_codes():
    group = _groups([_item(1), _item(2)])[0]
    rows = [_suggestion("item-2"), _suggestion("item-1")]
    assert len(validate_suggestions({"suggestions": rows}, group, ALLOWED)) == 2
    for invalid in [rows[:1], [rows[0], rows[0]],
                    [_suggestion("item-1", main_kp_code="made-up"), rows[1]],
                    [_suggestion("item-1", difficulty=6), rows[1]]]:
        with pytest.raises(ImportProblem) as exc:
            validate_suggestions({"suggestions": invalid}, group, ALLOWED)
        assert exc.value.code == "S3_LABEL_OUTPUT_INVALID"


def test_uncertain_kp_must_explain_missing_suggestion():
    group = _groups([_item(1)])[0]
    with pytest.raises(ImportProblem):
        validate_suggestions({"suggestions": [_suggestion("item-1", main_kp_code=None,
                                                          alternative_kp_codes=[], uncertainty=None)]},
                             group, ALLOWED)
    result = validate_suggestions({"suggestions": [_suggestion("item-1", main_kp_code=None,
                                                               alternative_kp_codes=[],
                                                               uncertainty="题图缺失，无法判断")]} , group, ALLOWED)
    assert result[0]["main_kp_code"] is None


def test_label_provider_sends_question_images_and_requires_complete_response(monkeypatch):
    captured = {}
    group = _groups([_item(1)])[0]

    def respond(request: httpx.Request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "labels-1", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps({"suggestions": [_suggestion("item-1")]})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50}})

    monkeypatch.setattr(labels_service, "get_settings", lambda: Settings(
        question_import_s2_api_key="test-only",
        question_import_s2_base_url="https://example.invalid/v1"))
    results, meta = QwenLabelProvider(transport=httpx.MockTransport(respond)).suggest(
        group, ALLOWED, {"item-1": [("asset-1", b"png-data")]})
    assert results[0]["main_kp_code"] == "R2"
    assert meta["estimated_cny"] is not None
    content = captured["messages"][0]["content"]
    assert content[1]["text"] == "题目 item-1 的图片 asset-1"
    assert content[2]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "必做标记" in content[0]["text"]
    assert "suggestions 必须恰好返回 1 条" in content[0]["text"]
    assert "is_required" not in json.dumps(captured["response_format"]["json_schema"]["schema"])
    assert "reason" not in json.dumps(captured["response_format"]["json_schema"]["schema"])


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


def _pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_label_stage_waits_for_dedupe_then_preserves_candidate_identity(db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    monkeypatch.setattr(settings, "question_import_s2_enabled", True)
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_operator_token", "operator-test")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/v1")
    batch = create_batch(db, source_mode="questions_only", files=[("questions", "questions.pdf", _pdf())])
    batch.owner_scope = "s3-label-test"
    monkeypatch.setattr(labels_service, "OWNER_SCOPE", "s3-label-test")
    monkeypatch.setattr(import_service, "OWNER_SCOPE", "s3-label-test")
    first = add_candidate(db, batch, {"content": {"question_text": "计算一加一", "options": [], "asset_ids": []},
                                      "tags": {"question_type": "unknown", "difficulty": None,
                                               "main_kp_code": None}, "teacher_decision": "pending"})
    second = add_candidate(db, batch, {"content": {"question_text": "计算二加二", "options": [], "asset_ids": []},
                                       "tags": {"question_type": "unknown", "difficulty": None,
                                                "main_kp_code": None}, "teacher_decision": "pending"})
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    batch.checkpoint = {"s2": {"auto_global_dedupe": True}, "s3": {}}
    with pytest.raises(ImportProblem) as pending:
        start_labels(db, batch.id, budget_cny=1, operator_token="operator-test")
    assert pending.value.code == "S3_DEDUPE_PENDING"
    batch.checkpoint = {"s2": {"auto_global_dedupe": True},
                        "s3": {"global_dedupe": {"status": "completed"}}}
    kp_code, kp = next(iter(formal_kps_by_code().items()))
    monkeypatch.setattr(labels_service, "_allowed_kps", lambda _db: {kp_code: kp})
    start_labels(db, batch.id, budget_cny=1, operator_token="operator-test")
    db.commit()
    db.refresh(batch)
    assert batch.checkpoint["s3"]["labels"]["status"] == "queued"
    connection = db.get_bind()
    monkeypatch.setattr(labels_service, "SessionLocal", lambda: Session(
        bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"))

    class FakeProvider:
        def suggest(self, group, allowed, images):
            assert len(group) == 2 and allowed[kp_code]["name"] == kp["name"]
            assert images == {str(first.id): [], str(second.id): []}
            # The provider may return results in any order.
            return ([{**_suggestion(str(second.id)), "main_kp_code": kp_code,
                      "alternative_kp_codes": []},
                     {**_suggestion(str(first.id)), "main_kp_code": kp_code,
                      "alternative_kp_codes": []}],
                    {"usage": {"prompt_tokens": 100, "completion_tokens": 50},
                     "estimated_cny": 0.01, "response_id": "labels-1"})

    monkeypatch.setattr(labels_service, "QwenLabelProvider", FakeProvider)
    assert process_labels_one() is True
    db.refresh(batch)
    db.refresh(first)
    db.refresh(second)
    assert batch.checkpoint["s3"]["labels"]["status"] == "completed"
    assert batch.checkpoint["s3"]["labels"]["calls"] == 1
    for item in (first, second):
        assert item.payload["tags"]["main_kp_code"] == kp_code
        assert item.payload["tags"]["chapter_code"] == kp["chapter_code"]
        assert item.payload["tags"]["core_literacies"] == kp["core_competencies"]
        assert item.payload["tags"]["difficulty"] == 1
        assert item.payload["label_status"] == "needs_confirmation"
        assert item.payload["teacher_decision"] == "pending"
    assert process_labels_one() is False
    assert len(list(db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch.id)))) == 2

    update_candidate(db, batch.id, first.id, expected_revision=first.revision,
                     changes={"tags": {"question_type": "true_false", "difficulty": 5,
                                       "main_kp_code": None, "is_required": True}})
    labels_before_content_edit = dict(first.payload["tags"])
    label_status_before_content_edit = first.payload["label_status"]
    content = dict(first.payload["content"])
    content["question_text"] = "计算一加二"
    update_candidate(db, batch.id, first.id, expected_revision=first.revision,
                     changes={"content": content})
    assert first.payload["tags"] == labels_before_content_edit
    assert first.payload["label_status"] == label_status_before_content_edit
    assert first.payload["label_suggestion"]["stale"] is False
    with pytest.raises(ImportProblem) as already_complete:
        start_labels(db, batch.id, budget_cny=1, operator_token="operator-test")
    assert already_complete.value.code == "COMMIT_CONFLICT"
    # Seed legacy decisions to verify label rerun invalidation. The public PATCH
    # now refuses to create either decision; S5 owns that audited action.
    first.payload = {**first.payload, "teacher_decision": "approved"}
    second.payload = {**second.payload, "teacher_decision": "rejected"}
    db.flush()
    start_labels(db, batch.id, budget_cny=1, operator_token="operator-test", rerun=True)
    assert len(batch.checkpoint["s3"]["label_group_audit"]) == 1
    assert batch.checkpoint["s3"]["labels"]["overwrite_manual_tags"] is True
    assert [entry["item_id"] for entry in batch.checkpoint["s3"]["labels"]["groups"][0]["items"]] == [
        str(first.id), str(second.id)]
    db.commit()

    class RetryProvider:
        def suggest(self, group, allowed, images):
            assert [entry["item_id"] for entry in group] == [str(first.id), str(second.id)]
            return ([{**_suggestion(str(first.id)), "main_kp_code": kp_code,
                      "alternative_kp_codes": [], "difficulty": 2,
                      "question_type": "solution"},
                     {**_suggestion(str(second.id)), "main_kp_code": None,
                      "alternative_kp_codes": [], "difficulty": 3,
                      "question_type": "solution", "uncertainty": "题干信息不足"}],
                    {"usage": {"prompt_tokens": 50, "completion_tokens": 20},
                     "estimated_cny": 0.005, "response_id": "labels-2"})

    monkeypatch.setattr(labels_service, "QwenLabelProvider", RetryProvider)
    assert process_labels_one() is True
    db.refresh(first)
    db.refresh(second)
    db.refresh(batch)
    assert first.payload["label_suggestion"]["stale"] is False
    assert first.payload["label_suggestion"]["difficulty"] == 2
    assert first.payload["tags"]["difficulty"] == 2
    assert first.payload["tags"]["main_kp_code"] == kp_code
    assert first.payload["tags"]["question_type"] == "solution"
    assert first.payload["tags"]["is_required"] is True  # The model does not suggest this field.
    assert first.payload["teacher_decision"] == "pending"
    assert first.payload["label_suggestion"]["teacher_overrides"] == []
    assert second.payload["tags"]["difficulty"] == 3
    assert second.payload["tags"]["main_kp_code"] is None
    assert second.payload["tags"]["chapter_code"] is None
    assert second.payload["tags"]["core_literacies"] == []
    assert second.payload["label_status"] == "out_of_scope"
    assert second.payload["teacher_decision"] == "rejected"
    assert batch.checkpoint["s3"]["labels"]["calls"] == 2

    start_labels(db, batch.id, budget_cny=1, operator_token="operator-test", rerun=True)
    db.commit()

    class InvalidPaidProvider:
        def __init__(self):
            self.last_response = {"id": "labels-invalid", "usage": {"prompt_tokens": 80,
                                                                       "completion_tokens": 20}}

        def suggest(self, group, allowed, images):
            raise ImportProblem("S3_LABEL_OUTPUT_INVALID", "模型返回非法标签")

    monkeypatch.setattr(labels_service, "QwenLabelProvider", InvalidPaidProvider)
    assert process_labels_one() is True
    db.refresh(batch)
    failed = batch.checkpoint["s3"]["labels"]
    assert failed["status"] == "failed" and failed["cost_unknown"] is False
    assert failed["overwrite_manual_tags"] is True
    assert failed["calls"] == 3 and failed["estimated_cny"] > 0.015
    start_labels(db, batch.id, budget_cny=1, operator_token="operator-test")
    assert batch.checkpoint["s3"]["labels"]["status"] == "queued"
    for item in (first, second):
        newer_content = dict(item.payload["content"])
        newer_content["question_text"] += "（已修改）"
        with pytest.raises(ImportProblem) as busy:
            update_candidate(db, batch.id, item.id, expected_revision=item.revision,
                             changes={"content": newer_content})
        assert busy.value.code == "COMMIT_CONFLICT"
        # Simulate a concurrent internal correction to verify stale group entries are skipped.
        item.revision += 1
        item.payload = {**item.payload, "content": newer_content, "revision": item.revision}
    db.commit()

    class NeverProvider:
        def suggest(self, group, allowed, images):
            raise AssertionError("stale candidate must not be sent to the model")

    monkeypatch.setattr(labels_service, "QwenLabelProvider", NeverProvider)
    for _ in range(4):
        assert process_labels_one() is True
        db.refresh(batch)
        if batch.checkpoint["s3"]["labels"]["status"] == "completed":
            break
    db.refresh(batch)
    finished = batch.checkpoint["s3"]["labels"]
    assert finished["status"] == "completed" and finished["calls"] == 3, finished["groups"]
    assert {item_id for group in finished["groups"] for item_id in group.get("skipped_item_ids", [])} == {
        str(first.id), str(second.id)}


@pytest.mark.parametrize("task,status", [
    ("answer_model_matching", "queued"), ("answer_model_matching", "processing"),
    ("labels", "queued"), ("labels", "processing"),
])
def test_candidate_and_manual_labels_wait_for_active_tasks(db, tmp_path, monkeypatch, task, status):
    monkeypatch.setattr(get_settings(), "question_import_dir", tmp_path / "imports")
    batch = create_batch(db, source_mode="questions_only", files=[("questions", "questions.pdf", _pdf())])
    item = add_candidate(db, batch, {"content": {"question_text": "计算一加一", "options": [], "asset_ids": []},
                                     "tags": {"question_type": "unknown", "difficulty": None,
                                              "main_kp_code": None}, "teacher_decision": "pending"})
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    batch.checkpoint = {"s3": {"global_dedupe": {"status": "completed"}, task: {"status": status}}}

    for changes in ({"tags": {"question_type": "calculation"}},
                    {"content": {**item.payload["content"], "question_text": "计算二加二"}}):
        with pytest.raises(ImportProblem) as blocked:
            update_candidate(db, batch.id, item.id, expected_revision=item.revision, changes=changes)
        assert blocked.value.code == "COMMIT_CONFLICT"
    assert item.payload["tags"]["question_type"] == "unknown"
    assert item.payload["content"]["question_text"] == "计算一加一"

    batch.checkpoint = {"s3": {"global_dedupe": {"status": "completed"}, task: {"status": "completed"}}}
    update_candidate(db, batch.id, item.id, expected_revision=item.revision,
                     changes={"tags": {"question_type": "calculation"}})
    assert item.payload["tags"]["question_type"] == "calculation"


def test_separate_labels_require_completed_answer_association(db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    monkeypatch.setattr(settings, "question_import_s2_enabled", True)
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_operator_token", "operator-test")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/v1")
    batch = create_batch(db, source_mode="separate", files=[
        ("questions", "questions.pdf", _pdf()), ("answers", "answers.pdf", _pdf())])
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    batch.checkpoint = {"s3": {"global_dedupe": {"status": "completed"}}}
    with pytest.raises(ImportProblem) as pending:
        start_labels(db, batch.id, budget_cny=1, operator_token="operator-test")
    assert pending.value.code == "S3_ANSWERS_PENDING"
    batch.checkpoint = {"s3": {"global_dedupe": {"status": "completed"},
                               "separate_answers": {"status": "completed"}}}
    with pytest.raises(ImportProblem) as unlinked:
        start_labels(db, batch.id, budget_cny=1, operator_token="operator-test")
    assert unlinked.value.code == "S3_ANSWERS_PENDING"
