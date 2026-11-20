"""All requests intercepted by MockTransport; never uses a purchased API Key."""
from datetime import timedelta
import json

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema
from uuid import uuid4

from app.core.config import get_settings
from app.core.database import Base, engine, get_db
from app.main import app
from app.models import AssessmentCall, Assignment, KnowledgePoint, Student
from app.services.assessment import AssessmentProblem, create_task, now, task_action
from app.services.assessment_protocol import canonical_json, parse_response
from app.services.assessment_worker import budget_state, claim_one, finish, process_one
from app.services.equivalence_bailian import BailianEquivalenceAdapter, configuration
from tests.test_stage6_s1 import context, fresh, protected_counts, request


@pytest.fixture
def real_context(context, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "stage6_equivalence_provider", "bailian")
    monkeypatch.setattr(settings, "stage6_equivalence_api_key", SecretStr("sk-offline-test-only"))
    monkeypatch.setattr(settings, "stage6_equivalence_model", "qwen3.7-flash")
    monkeypatch.setattr(settings, "stage6_equivalence_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(settings, "stage6_equivalence_max_calls", 20)
    monkeypatch.setattr(settings, "stage6_equivalence_max_budget_cny", 1)
    return context


def completion(req, *, content=None, **changes):
    data = json.loads(req.content)
    pair = json.loads(data["messages"][1]["content"])
    response = {"schema_version": "stage6-equivalence-response-v2", "request_id": pair["request_id"],
                "verdict": "equivalent", "reason_code": "contextual_match", "explanation": "结合题干，本空答案可接受。"}
    body = {"id": "response-offline", "model": "qwen3.7-flash-2026-07-15",
            "usage": {"prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 600},
            "choices": [{"finish_reason": "stop", "message": {"content": canonical_json(response) if content is None else content}}]}
    body.update(changes)
    return body


def adapter(handler):
    return BailianEquivalenceAdapter(configuration(), client=httpx.Client(transport=httpx.MockTransport(handler)))


def create(context):
    db, _, _, item, _ = context
    task, _ = create_task(db, request(item))
    db.commit()
    return task


def test_real_transport_whitelist_and_audit_without_grading(real_context):
    db, factory, _, item, _ = real_context
    before = protected_counts(db)
    task = create(real_context)
    calls = []
    def handle(req):
        calls.append(req)
        data = json.loads(req.content)
        assert req.url.path == "/compatible-mode/v1/chat/completions"
        assert req.headers["Authorization"] == "Bearer sk-offline-test-only"
        assert data["enable_thinking"] is False and data["stream"] is False
        assert data["response_format"]["json_schema"]["strict"] is True
        assert data["max_tokens"] == 512
        pair = json.loads(data["messages"][1]["content"])
        assert set(pair) == {"schema_version", "request_id", "reference_answer", "student_answer", "answer_context"}
        assert pair["reference_answer"] == "x+x" and pair["student_answer"] == "2x"
        assert "PRIVATE_" not in req.content.decode()
        return httpx.Response(200, json=completion(req))
    assert process_one(factory, adapter(handle))
    task = fresh(db, task)
    assert task.status == "succeeded" and task.result["verdict"] == "equivalent"
    call = db.scalar(select(AssessmentCall).where(AssessmentCall.task_id == task.id))
    assert call.usage["cost_cny"] == 0.00018 and call.usage["usage_status"] == "known"
    assert call.usage["provider_config"]["model"] == "qwen3.7-flash"
    assert json.loads(call.raw_response)["id"] == "response-offline"
    assert "sk-offline" not in str(task.action_history) + str(call.usage) + call.raw_response
    assert budget_state(db)["calls"] == 1 and protected_counts(db) == before
    duplicate, reused = create_task(db, request(item))
    assert reused and duplicate.id == task.id and len(calls) == 1


@pytest.mark.parametrize("url", [
    "http://dashscope.aliyuncs.com/compatible-mode/v1", "https://evil.test/compatible-mode/v1",
    "https://dashscope.aliyuncs.com.evil.test/compatible-mode/v1", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "https://sk-secret@dashscope.aliyuncs.com/compatible-mode/v1", "https://dashscope.aliyuncs.com/compatible-mode/v1?key=secret",
])
def test_reject_wrong_region_or_credential_destination(real_context, monkeypatch, url):
    monkeypatch.setattr(get_settings(), "stage6_equivalence_base_url", url)
    with pytest.raises(ValueError):
        configuration()


def test_missing_key_is_configuration_error_and_preview_stays_read_only(real_context, monkeypatch):
    db, _, _, item, _ = real_context
    monkeypatch.setattr(get_settings(), "stage6_equivalence_api_key", SecretStr(""))
    assert create_task(db, request(item), preview_only=True)["eligible"]
    with pytest.raises(AssessmentProblem) as exc:
        create_task(db, request(item))
    assert exc.value.code == "CONFIG_INCOMPLETE"


@pytest.mark.parametrize("limit", ["calls", "amount"])
def test_budget_stops_before_request_and_survives_session_restart(real_context, monkeypatch, limit):
    db, factory, *_ = real_context
    task = create(real_context)
    if limit == "calls":
        monkeypatch.setattr(get_settings(), "stage6_equivalence_max_calls", 1)
        assert process_one(factory, adapter(lambda req: httpx.Response(200, json=completion(req))))
        task = task_action(db, task.id, "rerun", "offline budget test"); db.commit()
    else:
        monkeypatch.setattr(get_settings(), "stage6_equivalence_max_budget_cny", 0.000001)
    def never(req):
        pytest.fail("Budget denied calls must never reach transport")
    assert not process_one(factory, adapter(never))
    assert fresh(db, task).error_code == "BUDGET_EXHAUSTED"
    with factory() as restored:
        assert budget_state(restored)["calls"] == (1 if limit == "calls" else 0)


@pytest.mark.parametrize("failure", ["timeout", "usage_missing", "invalid_json", "server_error"])
def test_unknown_usage_pauses_later_real_calls(real_context, failure):
    db, factory, *_ = real_context
    task = create(real_context)
    def handle(req):
        if failure == "timeout":
            raise httpx.ReadTimeout("secret in exception is not logged", request=req)
        if failure == "server_error":
            return httpx.Response(500, text="secret in error body")
        if failure == "invalid_json":
            return httpx.Response(200, text="not json")
        body = completion(req); body.pop("usage")
        return httpx.Response(200, json=body)
    assert process_one(factory, adapter(handle))
    task = fresh(db, task)
    assert task.status == "failed" and budget_state(db)["unknown_calls"] == 1
    rerun = task_action(db, task.id, "rerun", "verify unknown usage gate"); db.commit()
    assert claim_one(factory) is None
    assert fresh(db, rerun).error_code == "MODEL_USAGE_UNKNOWN"
    assert db.scalar(select(AssessmentCall).where(AssessmentCall.task_id == task.id)).usage["cost_cny"] is None


@pytest.mark.parametrize("content", ["not json", '{}', '{"verdict":"equivalent","verdict":"uncertain"}'])
def test_bad_model_output_keeps_known_usage_and_original_response(real_context, content):
    db, factory, *_ = real_context
    task = create(real_context)
    assert process_one(factory, adapter(lambda req: httpx.Response(200, json=completion(req, content=content))))
    task = fresh(db, task)
    assert task.error_code == "MODEL_SCHEMA_INVALID" and task.result is None
    call = db.scalar(select(AssessmentCall).where(AssessmentCall.task_id == task.id))
    assert call.usage["cost_cny"] > 0 and content in json.loads(call.raw_response)["choices"][0]["message"]["content"]
    assert budget_state(db)["unknown_calls"] == 0


@pytest.mark.parametrize("status,error", [(400, "MODEL_REQUEST_REJECTED"), (401, "MODEL_AUTH_FAILED"),
    (403, "MODEL_PERMISSION_DENIED"), (404, "MODEL_NOT_FOUND"), (429, "MODEL_RATE_LIMITED")])
def test_http_errors_are_clear_and_do_not_expose_provider_error_body(real_context, status, error):
    db, factory, *_ = real_context
    task = create(real_context)
    assert process_one(factory, adapter(lambda req: httpx.Response(status, text="sk-secret")))
    assert fresh(db, task).error_code == error
    call = db.scalar(select(AssessmentCall).where(AssessmentCall.task_id == task.id))
    assert call.raw_response is None and call.usage["cost_cny"] == 0


def test_config_changes_and_stale_inputs_do_not_make_paid_calls(real_context, monkeypatch):
    db, factory, paper, *_ = real_context
    task = create(real_context)
    monkeypatch.setattr(get_settings(), "stage6_equivalence_max_output_tokens", 1024)
    assert claim_one(factory) is None
    assert fresh(db, task).error_code == "CONFIG_CHANGED"
    task = task_action(db, task.id, "rerun", "new config"); db.commit()
    paper.invalidated_at = now(); db.commit()
    assert claim_one(factory) is None
    assert fresh(db, task).status == "stale" and budget_state(db)["calls"] == 0


def test_cancelled_inflight_reservation_is_not_refunded_until_late_usage_arrives(real_context):
    db, factory, *_ = real_context
    task = create(real_context)
    claim = claim_one(factory)
    assert budget_state(db)["reserved_cost_cny"] > 0
    task_action(db, task.id, "cancel", "cancel while sending"); db.commit()
    assert budget_state(db)["unknown_calls"] == 1
    raw = canonical_json({"schema_version": "stage6-equivalence-response-v2", "request_id": claim.request["request_id"],
        "verdict": "equivalent", "reason_code": "contextual_match", "explanation": "fixture"})
    finish(claim, raw, parse_response(raw, claim.request), None, factory,
           usage={"usage_status": "known", "cost_cny": 0.001, "input_tokens": 1, "output_tokens": 1})
    assert fresh(db, task).status == "cancelled"
    state = budget_state(db)
    assert state["unknown_calls"] == 0 and state["estimated_cost_cny"] == 0.001
    call = db.get(AssessmentCall, claim.call_id)
    assert call.late_response


def test_expired_real_lease_never_blindly_resends(real_context):
    db, factory, *_ = real_context
    task = create(real_context)
    claim = claim_one(factory)
    task = fresh(db, task); task.lease_until = now() - timedelta(seconds=1); db.commit()
    assert claim_one(factory) is None
    assert fresh(db, task).error_code == "MODEL_USAGE_UNKNOWN"
    assert budget_state(db)["calls"] == 1


def test_status_only_exposes_safe_configuration(real_context):
    db, *_ = real_context
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).get("/api/v1/internal/stage6/status")
        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == "bailian" and body["real_provider_available"]
        assert body["budget"]["max_budget_cny"] == 1 and "sk-offline" not in response.text
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_inflight_reservation_limits_other_students_and_workers(real_context, monkeypatch):
    """Separate connections/papers; a slow first call still occupies budget."""
    from copy import deepcopy
    db, _, paper, item, question = real_context
    schema = "stage6_budget_" + uuid4().hex
    scoped = engine.execution_options(schema_translate_map={None: schema})
    factory = sessionmaker(bind=scoped, expire_on_commit=False)
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        Base.metadata.create_all(scoped)
        with factory.begin() as separate:
            def clone(source, **changes):
                values = {c.key: deepcopy(getattr(source, c.key)) for c in source.__table__.columns}
                values.update(changes)
                result = type(source)(**values)
                separate.add(result); separate.flush()
                return result
            clone(db.get(KnowledgePoint, question.main_knowledge_point_id))
            student = clone(db.get(Student, paper.student_id))
            clone(db.get(Assignment, paper.assignment_id))
            clone(question); clone(paper)
            first_item = clone(item)
            second_student = clone(student, id=uuid4(), student_code="budget-" + uuid4().hex[:20])
            second_paper = clone(paper, id=uuid4(), student_id=second_student.id, instance_code="budget-" + uuid4().hex)
            second_item = clone(item, id=uuid4(), paper_instance_id=second_paper.id)
            first, _ = create_task(separate, request(first_item))
            second, _ = create_task(separate, request(second_item))
        first_claim = claim_one(factory)
        assert first_claim is not None
        with factory() as separate:
            state = budget_state(separate)
        assert state["calls"] == 1 and state["reserved_cost_cny"] > 0
        monkeypatch.setattr(get_settings(), "stage6_equivalence_max_budget_cny", state["reserved_cost_cny"] * 1.5)
        assert claim_one(factory) is None
        with factory() as separate:
            denied_id = second.id if first_claim.task_id == first.id else first.id
            from app.models import AssessmentTask
            assert separate.get(AssessmentTask, denied_id).error_code == "BUDGET_EXHAUSTED"
            assert budget_state(separate)["calls"] == 1
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
