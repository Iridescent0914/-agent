"""All image HTTP intercepted; no real credentials, requests or business DB."""
from copy import deepcopy
from datetime import timedelta
import base64
import hashlib
from io import BytesIO
import json

import httpx
from PIL import Image
from pydantic import SecretStr
import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import VisionPageCall, VisionPageTask
from app.services import vision_pages as service, vision_page_worker as worker, answer_pages
from app.services.assessment import AssessmentProblem, now
from app.services.assessment_protocol import canonical_json
from app.services.vision_bailian import (BailianPageVisionAdapter, configuration, payload_for,
    prepare_image, reservation, estimated_cost, VisionProviderResult)
from app.services.vision_budget import budget_state
from tests.test_stage6_s1 import context, protected_counts
from tests.test_stage6_s3_vision_pages import prepared, request, action, first_task, response_for
from tests.test_stage6_s3_pages import mutation, picture


@pytest.fixture
def real_context(context, monkeypatch):
    settings = get_settings()
    for name, value in {
        "stage6_vision_provider": "bailian", "stage6_vision_api_key": SecretStr("sk-offline-vision-only"),
        "stage6_vision_base_url": "https://ws-test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "stage6_vision_model": "qwen3.8-flash", "stage6_vision_max_calls": 30,
        "stage6_vision_max_budget_cny": 2, "stage6_vision_max_pixels": 4194304,
        "stage6_vision_max_output_tokens": 8192,
    }.items():
        monkeypatch.setattr(settings, name, value)
    return context


def create(context, *, many=False):
    db, factory, paper, artifact, view = prepared(context, many=many)
    req = request(view).model_copy(update={"external_images_reviewed": True})
    result = service.create_tasks(db, paper.id, artifact.id, req)
    db.commit()
    return db, factory, paper, artifact, view, result


def fresh(db, task):
    db.expire_all()
    return db.get(VisionPageTask, task.id)


def adapter(handler):
    return BailianPageVisionAdapter(configuration(), client=httpx.Client(transport=httpx.MockTransport(handler)))


def completion(req, task, *, content=None, **changes):
    body = {"usage": {"prompt_tokens": 6000, "completion_tokens": 2000},
        "choices": [{"finish_reason": "stop", "message": {"content": content if content is not None else canonical_json(response_for(task)),
                      "reasoning_content": "PRIVATE_REASONING"}}], "extra_echo": "PRIVATE_PROVIDER_ECHO"}
    body.update(changes)
    return body


def test_real_transport_image_whitelist_result_usage_and_no_grading(real_context):
    db, factory, paper, artifact, view, created = create(real_context)
    before = protected_counts(db); task = first_task(db)
    def handle(req):
        assert req.headers["Authorization"] == "Bearer sk-offline-vision-only"
        assert str(req.url) == configuration()["base_url"] + "/chat/completions"
        data = json.loads(req.content)
        assert not data["enable_thinking"] and not data["stream"] and not data["vl_high_resolution_images"]
        assert data["response_format"]["json_schema"]["strict"]
        content = data["messages"][1]["content"]
        wire = json.loads(content[0]["text"])
        assert wire == task.request_payload
        assert "PRIVATE_" not in content[0]["text"]
        assert paper.instance_code not in req.content.decode() and artifact.artifact_code not in req.content.decode()
        assert "expected_identity" not in req.content.decode()
        with Image.open(BytesIO(base64.b64decode(content[1]["image_url"]["url"].split(",")[1]))) as photo:
            assert photo.format == "JPEG" and photo.width * photo.height <= 4194304
        assert content[1]["max_pixels"] == 4194304
        return httpx.Response(200, json=completion(req, task))
    assert worker.process_one(factory, adapter(handle))
    task = fresh(db, task)
    assert task.status == "needs_review" and task.result["identity_check"]["status"] == "verified"
    assert not task.result["synthetic"] and not task.result["can_submit"]
    call = db.scalar(select(VisionPageCall))
    assert call.usage["cost_cny"] == .0102 and call.usage["usage_status"] == "known"
    assert call.usage["reserved_cost_cny"] > call.usage["cost_cny"]
    assert call.usage["transmitted_image_sha256"]
    assert not any(s in str(task.snapshot) + str(call.usage) + call.raw_response for s in ("sk-offline", "PRIVATE_REASONING", "PRIVATE_PROVIDER_ECHO"))
    assert protected_counts(db) == before
    result = service.create_tasks(db, paper.id, artifact.id, request(view).model_copy(update={"external_images_reviewed": True}))
    assert result["created_count"] == 0 and budget_state(db)["calls"] == 1
    assert result["configuration"]["real_provider_available"] and not result["current_tasks"][0]["synthetic"]


@pytest.mark.parametrize("url", ["", "http://dashscope.aliyuncs.com/compatible-mode/v1",
    "https://evil.test/compatible-mode/v1", "https://dashscope.aliyuncs.com.evil.test/compatible-mode/v1",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "https://sk-key@dashscope.aliyuncs.com/compatible-mode/v1",
    "https://dashscope.aliyuncs.com/compatible-mode/v1?key=secret", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", "https://dashscope.aliyuncs.com:bad/compatible-mode/v1"])
def test_bad_destination_and_placeholders_rejected(real_context, monkeypatch, url):
    monkeypatch.setattr(get_settings(), "stage6_vision_base_url", url)
    with pytest.raises(ValueError, match="VISION_CONFIG_INCOMPLETE"): configuration()


@pytest.mark.parametrize("field,value", [("stage6_vision_api_key", SecretStr("")), ("stage6_vision_model", "unknown-model")])
def test_missing_new_key_or_unknown_model_does_not_use_other_keys(real_context, monkeypatch, field, value):
    monkeypatch.setattr(get_settings(), field, value)
    monkeypatch.setattr(get_settings(), "vision_api_key", "old-key")
    monkeypatch.setattr(get_settings(), "stage6_equivalence_api_key", SecretStr("text-key"))
    with pytest.raises(ValueError): configuration()


def test_real_creation_requires_photo_review_and_excludes_key_from_snapshot(real_context):
    db, factory, paper, artifact, view = prepared(real_context)
    with pytest.raises(AssessmentProblem) as error:
        service.create_tasks(db, paper.id, artifact.id, request(view))
    assert error.value.code == "VISION_EXTERNAL_REVIEW_REQUIRED"
    assert db.scalar(select(func.count()).select_from(VisionPageTask)) == 0


@pytest.mark.parametrize("limit", ["calls", "amount", "inflight"])
def test_budget_reservation_stops_next_page_before_http(real_context, monkeypatch, limit):
    db, factory, paper, artifact, view, _ = create(real_context, many=True)
    if limit == "amount":
        monkeypatch.setattr(get_settings(), "stage6_vision_max_budget_cny", .000001)
        assert worker.claim_one(factory) is None
        assert budget_state(db)["calls"] == 0
    else:
        if limit == "calls": monkeypatch.setattr(get_settings(), "stage6_vision_max_calls", 1)
        claim = worker.claim_one(factory)
        assert claim is not None
        state = budget_state(db)
        if limit == "inflight": monkeypatch.setattr(get_settings(), "stage6_vision_max_budget_cny", state["reserved_cost_cny"] * 1.5)
        assert worker.claim_one(factory) is None
        assert budget_state(db)["calls"] == 1
    db.expire_all()
    assert db.scalar(select(VisionPageTask).where(VisionPageTask.status == "failed")).error_code == "VISION_BUDGET_EXHAUSTED"


@pytest.mark.parametrize("failure", ["timeout", "missing_usage", "server", "invalid_json", "oversized", "bound"])
def test_unknown_or_out_of_bound_usage_pauses_real_calls(real_context, failure):
    db, factory, paper, artifact, view, _ = create(real_context)
    task = first_task(db)
    def handle(req):
        if failure == "timeout": raise httpx.ReadTimeout("PRIVATE_SECRET", request=req)
        if failure == "server": return httpx.Response(500, text="PRIVATE_SECRET")
        if failure == "invalid_json": return httpx.Response(200, text="PRIVATE_SECRET")
        if failure == "oversized": return httpx.Response(200, content=b"x" * 1048577)
        body = completion(req, task)
        if failure == "missing_usage": body.pop("usage")
        if failure == "bound": body["usage"]["prompt_tokens"] = 100000
        return httpx.Response(200, json=body)
    assert worker.process_one(factory, adapter(handle))
    task = fresh(db, task)
    assert task.status == "failed" and budget_state(db)["unknown_calls"] == 1
    assert "PRIVATE_SECRET" not in str(service.task_detail(db, paper.id, artifact.id, task.id))
    service.task_action(db, paper.id, artifact.id, task.id, "rerun", action(task)); db.commit()
    assert worker.claim_one(factory) is None and budget_state(db)["calls"] == 1


@pytest.mark.parametrize("status,error", [(400,"MODEL_REQUEST_REJECTED"), (401,"MODEL_AUTH_FAILED"),
    (403,"MODEL_PERMISSION_DENIED"), (404,"MODEL_NOT_FOUND"), (429,"MODEL_RATE_LIMITED"), (302,"MODEL_PROVIDER_FAILED")])
def test_http_status_sanitized_and_not_assumed_free(real_context, status, error):
    db, factory, paper, artifact, view, _ = create(real_context); task = first_task(db)
    assert worker.process_one(factory, adapter(lambda req: httpx.Response(status, text="sk-offline-vision-only", headers={"location": "https://evil.test"})))
    assert fresh(db, task).error_code == error
    call = db.scalar(select(VisionPageCall))
    assert call.raw_response is None and call.usage["cost_cny"] is None
    assert budget_state(db)["unknown_calls"] == 1


@pytest.mark.parametrize("content,finish", [('{}','stop'), ('not json','stop'), (None,'length')])
def test_bad_output_keeps_known_cost_but_cannot_be_used(real_context, content, finish):
    db, factory, paper, artifact, view, _ = create(real_context); task = first_task(db)
    def handle(req):
        body = completion(req, task, content=content); body["choices"][0]["finish_reason"] = finish
        return httpx.Response(200, json=body)
    worker.process_one(factory, adapter(handle)); task = fresh(db, task)
    assert task.status == "failed" and task.result is None
    assert budget_state(db)["estimated_cost_cny"] == .0102 and budget_state(db)["unknown_calls"] == 0


def test_model_change_does_not_reuse_fake_or_previous_real_task(real_context, monkeypatch):
    monkeypatch.setattr(get_settings(), "stage6_vision_provider", "fake")
    db, factory, paper, artifact, view = prepared(real_context)
    req = request(view).model_copy(update={"external_images_reviewed": True})
    service.create_tasks(db, paper.id, artifact.id, req); db.commit()
    fake = first_task(db)
    monkeypatch.setattr(get_settings(), "stage6_vision_provider", "bailian")
    result = service.create_tasks(db, paper.id, artifact.id, req); db.commit()
    assert result["created_count"] == 1 and len(result["current_tasks"]) == 1
    assert result["current_tasks"][0]["provider"] == "bailian"
    claims = [worker.claim_one(factory), worker.claim_one(factory)]
    paid = [claim for claim in claims if claim is not None]
    assert len(paid) == 1 and paid[0].provider == "bailian"
    assert fresh(db, fake).status == "stale"  # Old fake task never becomes a paid request.
    monkeypatch.setattr(get_settings(), "stage6_vision_model", "qwen3.7-plus")
    current = service.list_tasks(db, paper.id, artifact.id)
    assert not current["current_tasks"]
    assert service.create_tasks(db, paper.id, artifact.id, req)["created_count"] == 1


def test_expired_real_lease_not_resent_and_late_usage_is_preserved(real_context):
    db, factory, paper, artifact, view, _ = create(real_context); task = first_task(db)
    claim = worker.claim_one(factory)
    task = fresh(db, task); task.lease_until = now() - timedelta(seconds=1); db.commit()
    assert worker.claim_one(factory) is None
    assert fresh(db, task).error_code == "MODEL_USAGE_UNKNOWN" and budget_state(db)["calls"] == 1
    parsed = response_for(task)
    worker.finish(claim, canonical_json(parsed), parsed, None, factory,
        usage={"usage_status": "known", "cost_cny": .0102, "synthetic": False})
    task = fresh(db, task)
    assert task.result is None and db.get(VisionPageCall, claim.call_id).late_response
    assert budget_state(db)["unknown_calls"] == 0


def test_cancel_then_late_usage_does_not_refund_or_adopt_output(real_context):
    db, factory, paper, artifact, view, _ = create(real_context); task = first_task(db)
    claim = worker.claim_one(factory)
    service.task_action(db, paper.id, artifact.id, task.id, "cancel", action(fresh(db, task))); db.commit()
    assert budget_state(db)["unknown_calls"] == 1
    parsed = response_for(task)
    worker.finish(claim, canonical_json(parsed), parsed, None, factory,
        usage={"usage_status": "known", "cost_cny": .01, "synthetic": False})
    assert fresh(db, task).status == "cancelled" and task.result is None
    assert budget_state(db)["unknown_calls"] == 0 and budget_state(db)["estimated_cost_cny"] == .01


def test_retake_and_config_change_cannot_start_old_paid_request(real_context, monkeypatch):
    db, factory, paper, artifact, view, _ = create(real_context); task = first_task(db)
    monkeypatch.setattr(get_settings(), "stage6_vision_max_pixels", 1000000)
    assert worker.claim_one(factory) is None
    assert fresh(db, task).error_code == "CONFIG_CHANGED" and budget_state(db)["calls"] == 0


def test_image_hash_pixel_bound_and_no_exif(real_context):
    output = BytesIO(); Image.new("RGB", (3000, 4000), "white").save(output, format="JPEG", comment=b"PRIVATE_COMMENT")
    photo = output.getvalue()
    small = prepare_image(photo, hashlib.sha256(photo).hexdigest(), 1000000)
    with Image.open(BytesIO(small)) as parsed:
        assert parsed.width * parsed.height <= 1000000 and not parsed.getexif() and "comment" not in parsed.info
    with pytest.raises(ValueError): prepare_image(photo, "0" * 64, 1000000)


@pytest.mark.parametrize("model,mode", [("qwen3.8-flash","json_schema"), ("qwen3.7-flash","json_schema"),
    ("qwen3.7-plus","json_schema"), ("qwen3-vl-plus","json_object")])
def test_supported_models_freeze_response_mode_and_price(real_context, monkeypatch, model, mode):
    monkeypatch.setattr(get_settings(), "stage6_vision_model", model)
    config = configuration(); assert config["response_format"] == mode
    assert "api_key" not in config and config["input_million_price_cny"]


def test_secret_echo_is_redacted_and_non_json_failure_never_retains_it(real_context):
    db, factory, paper, artifact, view, _ = create(real_context); task = first_task(db)
    worker.process_one(factory, adapter(lambda req: httpx.Response(200, json=completion(req, task, content='{"secret":"sk-offline-vision-only"}'))))
    call = db.scalar(select(VisionPageCall))
    assert "sk-offline-vision-only" not in call.raw_response and "[REDACTED]" in call.raw_response
    assert fresh(db, task).status == "failed"


def test_wire_rejects_extra_business_fields_and_large_text_before_reserving(real_context):
    db, factory, paper, artifact, view, _ = create(real_context); task = first_task(db)
    wire = deepcopy(task.request_payload)
    wire["reference_answer"] = "PRIVATE_REFERENCE"
    with pytest.raises(ValueError): payload_for(wire, configuration(), "")
    wire = deepcopy(task.request_payload)
    wire["slots"] = [{**wire["slots"][0], "slot_token": f"slot-{n}", "order_no": n + 1} for n in range(500)]
    with pytest.raises(ValueError, match="VISION_INPUT_TOO_LARGE"): reservation(wire, configuration())


def test_paid_transport_runs_without_open_database_session(real_context):
    from contextlib import contextmanager
    db, factory, paper, artifact, view, _ = create(real_context); task = first_task(db)
    opened = []
    class Factory:
        @contextmanager
        def begin(self):
            with factory.begin() as session:
                opened.append(session)
                try: yield session
                finally: opened.pop()
    def handle(req):
        assert not opened
        return httpx.Response(200, json=completion(req, task))
    assert worker.process_one(Factory(), adapter(handle))
    assert fresh(db, task).status == "needs_review"


def test_reservation_visible_to_another_connection(real_context, monkeypatch):
    """Committed reservations limit a second worker while the first HTTP waits."""
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.schema import CreateSchema, DropSchema
    from uuid import uuid4
    from app.core.database import Base, engine
    from app.models import (KnowledgePoint, Student, Assignment, Question, PaperInstance,
        PaperInstanceItem, AnswerCardArtifact, AnswerPageCollection, AnswerPageImage, AnswerPageEvent)
    db, _, paper, artifact, view, _ = create(real_context, many=True)
    collection_id = service.list_tasks(db, paper.id, artifact.id)["collection_id"]
    question = real_context[4]
    schema = "vision_budget_" + uuid4().hex
    scoped = engine.execution_options(schema_translate_map={None: schema})
    factory = sessionmaker(bind=scoped, expire_on_commit=False)
    with engine.begin() as conn: conn.execute(CreateSchema(schema))
    try:
        Base.metadata.create_all(scoped)
        with factory.begin() as session:
            def clone(source):
                values = {c.key: deepcopy(getattr(source, c.key)) for c in source.__table__.columns}
                value = type(source)(**values); session.add(value); session.flush()
            for source in (db.get(KnowledgePoint, question.main_knowledge_point_id), db.get(Student, paper.student_id),
                           db.get(Assignment, paper.assignment_id), question, paper): clone(source)
            for source in db.scalars(select(PaperInstanceItem).where(PaperInstanceItem.paper_instance_id == paper.id)): clone(source)
            clone(artifact)
            from uuid import UUID
            clone(db.get(AnswerPageCollection, UUID(collection_id)))
            for model in (AnswerPageImage, AnswerPageEvent, VisionPageTask):
                for source in db.scalars(select(model).where(model.collection_id == UUID(collection_id))): clone(source)
        first = worker.claim_one(factory)
        assert first is not None
        with factory() as session: state = budget_state(session)
        assert state["reserved_cost_cny"] > 0 and state["calls"] == 1
        monkeypatch.setattr(get_settings(), "stage6_vision_max_budget_cny", state["reserved_cost_cny"] * 1.5)
        assert worker.claim_one(factory) is None
        with factory() as session:
            assert budget_state(session)["calls"] == 1
            assert session.scalar(select(VisionPageTask).where(VisionPageTask.status == "failed")).error_code == "VISION_BUDGET_EXHAUSTED"
    finally:
        with engine.begin() as conn: conn.execute(DropSchema(schema, cascade=True))
