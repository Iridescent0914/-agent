"""Claim/finish transactions surround an adapter invocation without a DB session."""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
import hashlib
import time
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import VisionPageCall, VisionPageTask
from app.services.assessment import AssessmentProblem, now
from app.services.vision_pages import _context, binding_error, configuration
from app.services.vision_page_protocol import (
    FakePageVisionAdapter, MAX_RESPONSE_CHARS, identity_check, parse_page_response,
)
from app.services.vision_bailian import BailianPageVisionAdapter, VisionProviderResult, reservation
from app.services.vision_budget import budget_state


@dataclass(frozen=True)
class Claim:
    task_id: UUID
    call_id: UUID
    token: UUID
    request: dict
    image: bytes
    image_sha256: str
    timeout_seconds: int
    provider: str = "fake"
    config: dict | None = None


def _lock_context(db, task):
    return _context(db, UUID(task.snapshot["paper_instance_id"]), UUID(task.snapshot["artifact_id"]), lock=True)


def claim_one(session_factory=SessionLocal):
    settings = get_settings()
    if not settings.stage6_enabled or settings.app_env not in {"development", "test"}:
        return None
    try:
        configuration()
    except AssessmentProblem:
        return None  # Unsupported provider pauses the queue, never falls back.
    current = now()
    with session_factory.begin() as db:
        eligible = or_(VisionPageTask.status == "queued",
                       and_(VisionPageTask.status == "running", VisionPageTask.lease_until <= current))
        candidate = db.scalar(select(VisionPageTask).where(eligible)
                              .order_by(VisionPageTask.created_at, VisionPageTask.id).limit(1))
        if candidate is None:
            return None
        paper, artifact, collection = _lock_context(db, candidate)
        task = db.scalar(select(VisionPageTask).where(VisionPageTask.id == candidate.id, eligible)
                         .with_for_update(skip_locked=True).execution_options(populate_existing=True))
        if task is None:
            return None
        if task.status == "running":
            old = db.scalar(select(VisionPageCall).where(VisionPageCall.lease_token == task.lease_token))
            if old and old.status == "running":
                old.status, old.error_code, old.completed_at = "abandoned", "LEASE_EXPIRED", current
        error = binding_error(db, task, paper, artifact, collection)
        if not error and task.attempt_count >= task.max_attempts:
            error = "MAX_ATTEMPTS_REACHED"
        image = None
        if not error:
            from app.models import AnswerPageImage
            row = db.get(AnswerPageImage, task.image_id)
            image = row.image_data
            if image is None or hashlib.sha256(image).hexdigest() != task.snapshot["image_sha256"]:
                error = "IMAGE_INTEGRITY_ERROR"
        config, reserve = None, None
        if not error and task.provider == "bailian":
            config = task.snapshot.get("provider_config")
            try:
                reserve = reservation(task.request_payload, config)
                budget = budget_state(db, lock=True)
                if budget["unknown_calls"]:
                    error = "MODEL_USAGE_UNKNOWN"
                elif (budget["calls"] >= budget["max_calls"] or
                      sum(Decimal(str(v)) for v in (budget["estimated_cost_cny"], budget["reserved_cost_cny"], reserve["reserved_cost_cny"]))
                      > Decimal(str(budget["max_budget_cny"]))):
                    error = "VISION_BUDGET_EXHAUSTED"
            except ValueError:
                error = "VISION_INPUT_TOO_LARGE"
        if error:
            task.status = "stale" if error in {"INPUT_STALE", "PAGE_INPUT_CHANGED", "ARTIFACT_CHANGED", "CONFIG_CHANGED"} else "failed"
            task.error_code, task.completed_at = error, current
            task.lease_token, task.lease_until = None, None
            task.revision_no += 1
            return None
        task.status, task.error_code, task.completed_at = "running", None, None
        task.attempt_count += 1
        task.revision_no += 1
        task.lease_token = uuid4()
        task.lease_until = current + timedelta(seconds=task.lease_seconds)
        call = VisionPageCall(task_id=task.id, call_no=task.attempt_count, lease_token=task.lease_token,
            status="running", started_at=current,
            usage=({"usage_status": "reserved", "synthetic": False, "cost_cny": None,
                    "provider_config": config, **reserve} if reserve else
                   {"usage_status": "known", "synthetic": True, "cost_cny": 0,
                    "input_tokens": 0, "output_tokens": 0}))
        db.add(call); db.flush()
        return Claim(task.id, call.id, task.lease_token, task.request_payload, image,
                     task.snapshot["image_sha256"], task.timeout_seconds, task.provider, config)


def finish(claim, raw, parsed, error, session_factory=SessionLocal, *, truncated=False, usage=None):
    with session_factory.begin() as db:
        initial = db.get(VisionPageTask, claim.task_id)
        paper, artifact, collection = _lock_context(db, initial)
        task = db.scalar(select(VisionPageTask).where(VisionPageTask.id == claim.task_id)
                         .with_for_update().execution_options(populate_existing=True))
        call = db.get(VisionPageCall, claim.call_id)
        if call.status in {"succeeded", "failed", "late"}:
            return  # First received output is authoritative for this call.
        if usage is not None:
            call.usage = {**call.usage, **usage}
        call.raw_response, call.parsed_response = raw, parsed
        call.completed_at = now()
        if truncated:
            call.usage = {**call.usage, "raw_response_truncated": True}
        if task.status != "running" or task.lease_token != claim.token or task.lease_until <= now():
            call.status, call.error_code, call.late_response = "late", error or "LEASE_EXPIRED_OR_CANCELLED", True
            return
        stale = binding_error(db, task, paper, artifact, collection)
        checked = identity_check(parsed, task.snapshot["expected_identity"]) if parsed else None
        if checked and checked["status"] == "mismatch":
            error = "PAGE_IDENTITY_MISMATCH"
        call.status, call.error_code = ("failed", error) if error else ("succeeded", None)
        task.lease_token, task.lease_until, task.completed_at = None, None, now()
        task.revision_no += 1
        if stale:
            task.status, task.error_code = "stale", stale
        elif error:
            task.status, task.error_code = "failed", error
        else:
            # Even a synthetic test adapter that returns readable values and all
            # correct IDs cannot authorize transcription confirmation or grading.
            task.status, task.error_code = "needs_review", None
            task.result = {**parsed, "identity_check": checked, "synthetic": claim.provider == "fake",
                           "requires_transcription_confirmation": True, "can_submit": False}


def process_one(session_factory=SessionLocal, adapter=None):
    claim = claim_one(session_factory)
    if claim is None:
        return False
    raw = parsed = error = usage = None
    truncated = False
    started = time.monotonic()
    try:
        transport = adapter or (BailianPageVisionAdapter(claim.config) if claim.provider == "bailian" else FakePageVisionAdapter())
        output = transport.invoke(claim.request, image=claim.image,
            image_sha256=claim.image_sha256, timeout_seconds=claim.timeout_seconds)
        if isinstance(output, VisionProviderResult):
            raw, content, usage, error = output.raw, output.content, output.usage, output.error
        else:
            raw = content = output
            if claim.provider != "fake":
                error = "MODEL_USAGE_UNKNOWN"
        if time.monotonic() - started > claim.timeout_seconds:
            error = "MODEL_TIMEOUT"
        elif error:
            pass
        elif not isinstance(content, str):
            raw, error = None, "MODEL_SCHEMA_INVALID"
        elif len(content) > MAX_RESPONSE_CHARS:
            raw, error, truncated = raw[:MAX_RESPONSE_CHARS], "MODEL_RESPONSE_TOO_LARGE", True
        else:
            try:
                parsed = parse_page_response(content, claim.request)
            except (ValueError, TypeError):
                error = "MODEL_SCHEMA_INVALID"
    except TimeoutError:
        error = "MODEL_TIMEOUT"
    except Exception:
        # Provider errors may contain image content or secrets; do not store/log
        # exception text.
        error = "MODEL_PROVIDER_FAILED"
    if claim.provider == "bailian" and usage is None:
        usage = {"usage_status": "unknown", "synthetic": False, "cost_cny": None}
    finish(claim, raw, parsed, error, session_factory, truncated=truncated, usage=usage)
    return True
