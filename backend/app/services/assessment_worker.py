"""Short claim/finish transactions; adapter invocation has no open DB session."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import time
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import and_, or_, select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import AssessmentCall, AssessmentTask
from app.services.assessment import _paper_item, binding_error, now
from app.services.assessment_protocol import EquivalenceAdapter, FakeEquivalenceAdapter, parse_response


@dataclass(frozen=True)
class Claim:
    task_id: UUID
    call_id: UUID
    token: UUID
    request: dict
    timeout_seconds: int


def claim_one(session_factory=SessionLocal) -> Claim | None:
    if not get_settings().stage6_enabled or get_settings().app_env not in {"development", "test"}:
        return None
    current = now()
    with session_factory.begin() as db:
        task = db.scalar(select(AssessmentTask).where(or_(
            AssessmentTask.status == "queued",
            and_(AssessmentTask.status == "running", AssessmentTask.lease_until <= current),
        )).order_by(AssessmentTask.created_at, AssessmentTask.id).with_for_update(skip_locked=True).limit(1))
        if task is None:
            return None
        if task.status == "running":
            old = db.scalar(select(AssessmentCall).where(AssessmentCall.lease_token == task.lease_token))
            if old:
                old.status, old.error_code, old.completed_at = "abandoned", "LEASE_EXPIRED", current
        if task.attempt_count >= task.max_attempts or task.provider != "fake":
            task.status = "failed"
            task.error_code = "BUDGET_EXHAUSTED" if task.provider == "fake" else "CONFIG_INCOMPLETE"
            task.lease_token, task.lease_until, task.completed_at = None, None, current
            return None
        task.status, task.error_code, task.completed_at = "running", None, None
        task.attempt_count += 1
        task.lease_token = uuid4()
        task.lease_until = current + timedelta(seconds=task.lease_seconds)
        call = AssessmentCall(
            task_id=task.id, call_no=task.attempt_count, lease_token=task.lease_token,
            status="running", started_at=current,
            usage={"usage_status": "known", "cost_cny": 0, "input_tokens": 0, "output_tokens": 0, "synthetic": True},
        )
        db.add(call)
        db.flush()
        return Claim(task.id, call.id, task.lease_token, task.request_payload, task.timeout_seconds)


def finish(claim: Claim, raw: str | None, parsed: dict | None, error: str | None, session_factory=SessionLocal):
    with session_factory.begin() as db:
        initial = db.get(AssessmentTask, claim.task_id)
        _paper_item(db, UUID(initial.snapshot["paper_instance_item_id"]), active=False)
        task = db.scalar(select(AssessmentTask).where(AssessmentTask.id == claim.task_id).with_for_update().execution_options(populate_existing=True))
        call = db.get(AssessmentCall, claim.call_id)
        if call.status in {"succeeded", "failed", "late"}:
            return  # A repeated finish must not rewrite the first received output.
        # Keep late output attached to its original call, never to the new lease.
        call.raw_response, call.parsed_response = raw, parsed
        call.completed_at = now()
        if task.status != "running" or task.lease_token != claim.token or task.lease_until <= now():
            call.late_response = True
            call.status, call.error_code = "late", error or call.error_code or "LEASE_EXPIRED"
            return
        stale = binding_error(db, task)
        call.status, call.error_code = ("failed", error) if error else ("succeeded", None)
        task.lease_token, task.lease_until, task.completed_at = None, None, now()
        if stale:
            task.status, task.error_code = "stale", stale
        elif error:
            task.status, task.error_code = "failed", error
        else:
            task.status, task.result, task.error_code = "succeeded", parsed, None


def process_one(session_factory=SessionLocal, adapter: EquivalenceAdapter | None = None) -> bool:
    claim = claim_one(session_factory)
    if claim is None:
        return False
    raw, parsed, error = None, None, None
    started = time.monotonic()
    try:
        # No transaction/session spans this boundary. Real transports in S2 must
        # also enforce the passed deadline at the HTTP client itself.
        raw = (adapter or FakeEquivalenceAdapter()).invoke(claim.request, timeout_seconds=claim.timeout_seconds)
        if time.monotonic() - started > claim.timeout_seconds:
            error = "MODEL_TIMEOUT"
        elif not isinstance(raw, str) or len(raw) > 64000:
            error = "MODEL_SCHEMA_INVALID"
            raw = raw[:64000] if isinstance(raw, str) else repr(type(raw))
        else:
            try:
                parsed = parse_response(raw, claim.request)
            except (ValueError, TypeError, ValidationError):
                error = "MODEL_SCHEMA_INVALID"
    except TimeoutError:
        error = "MODEL_TIMEOUT"
    except Exception:
        # Exception strings can contain provider credentials/input; don't log them.
        error = "MODEL_PROVIDER_FAILED"
    finish(claim, raw, parsed, error, session_factory)
    return True
