"""Short claim/finish transactions; adapter invocation has no open DB session."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
import time
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import and_, or_, select, text

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import AssessmentCall, AssessmentTask
from app.services.assessment import _paper_item, binding_error, now
from app.services.assessment_protocol import EquivalenceAdapter, FakeEquivalenceAdapter, parse_response
from app.services.equivalence_bailian import BailianEquivalenceAdapter, ProviderResult, configuration, reservation


@dataclass(frozen=True)
class Claim:
    task_id: UUID
    call_id: UUID
    token: UUID
    request: dict
    timeout_seconds: int
    provider: str = "fake"
    config: dict | None = None


def budget_state(db, *, lock=False):
    if lock:
        # One DB-wide gate for all real equivalence calls, across workers and
        # students. Reservations commit before HTTP; changing a Key doesn't reset it.
        db.execute(text("SELECT pg_advisory_xact_lock(60062026)"))
    db.flush()
    rows = db.execute(select(AssessmentCall, AssessmentTask).join(
        AssessmentTask, AssessmentCall.task_id == AssessmentTask.id,
    ).where(AssessmentTask.provider == "bailian", AssessmentTask.kind == "equivalence")
      .execution_options(populate_existing=True)).all()
    cost, reserved, unknown = Decimal(0), Decimal(0), 0
    for call, task in rows:
        usage = call.usage
        if usage.get("usage_status") == "known" and usage.get("cost_cny") is not None:
            cost += Decimal(str(usage["cost_cny"]))
        elif (usage.get("usage_status") == "reserved" and call.status == "running"
              and task.status == "running" and task.lease_until and task.lease_until > now()):
            reserved += Decimal(str(usage["reserved_cost_cny"]))
        else:
            unknown += 1
    settings = get_settings()
    return {"scope": "database_cumulative_equivalence", "calls": len(rows), "unknown_calls": unknown,
            "estimated_cost_cny": float(cost), "reserved_cost_cny": float(reserved),
            "max_calls": settings.stage6_equivalence_max_calls,
            "max_budget_cny": settings.stage6_equivalence_max_budget_cny}


def claim_one(session_factory=SessionLocal) -> Claim | None:
    if not get_settings().stage6_enabled or get_settings().app_env not in {"development", "test"}:
        return None
    current = now()
    with session_factory.begin() as db:
        eligible = or_(
            AssessmentTask.status == "queued",
            and_(AssessmentTask.status == "running", AssessmentTask.lease_until <= current),
        )
        candidate = db.scalar(select(AssessmentTask).where(eligible).order_by(AssessmentTask.created_at, AssessmentTask.id).limit(1))
        if candidate is None:
            return None
        # Same paper -> task lock order as all other writers. Revalidate before
        # paying for a call, not just when the response eventually returns.
        _paper_item(db, UUID(candidate.snapshot["paper_instance_item_id"]), active=False)
        task = db.scalar(select(AssessmentTask).where(AssessmentTask.id == candidate.id, eligible)
                         .with_for_update(skip_locked=True).execution_options(populate_existing=True))
        if task is None:
            return None
        if task.status == "running":
            old = db.scalar(select(AssessmentCall).where(AssessmentCall.lease_token == task.lease_token))
            if old:
                old.status, old.error_code, old.completed_at = "abandoned", "LEASE_EXPIRED", current
        stale = binding_error(db, task) if task.provider != "fake" else None
        error, reserve, config = stale, None, None
        if not error and task.attempt_count >= task.max_attempts:
            error = "BUDGET_EXHAUSTED"
        if not error and task.provider != "fake":
            try:
                config = configuration()
                frozen = task.action_history[0].get("provider_config")
                if task.provider != "bailian" or config != frozen:
                    error = "CONFIG_CHANGED"
                else:
                    reserve = reservation(task.request_payload, config)
                    budget = budget_state(db, lock=True)
                    if budget["unknown_calls"]:
                        error = "MODEL_USAGE_UNKNOWN"
                    elif (budget["calls"] >= budget["max_calls"]
                          or sum(Decimal(str(x)) for x in (budget["estimated_cost_cny"], budget["reserved_cost_cny"], reserve["reserved_cost_cny"]))
                          > Decimal(str(budget["max_budget_cny"]))):
                        error = "BUDGET_EXHAUSTED"
            except (ValueError, KeyError, IndexError):
                error = "CONFIG_INCOMPLETE"
        if error:
            task.status = "failed"
            if stale:
                task.status = "stale"
            task.error_code = error
            task.lease_token, task.lease_until, task.completed_at = None, None, current
            return None
        task.status, task.error_code, task.completed_at = "running", None, None
        task.attempt_count += 1
        task.lease_token = uuid4()
        task.lease_until = current + timedelta(seconds=task.lease_seconds)
        call = AssessmentCall(
            task_id=task.id, call_no=task.attempt_count, lease_token=task.lease_token,
            status="running", started_at=current,
            usage=({"usage_status": "reserved", "cost_cny": None, "synthetic": False,
                    "provider_config": config, **reserve} if reserve else
                   {"usage_status": "known", "cost_cny": 0, "input_tokens": 0, "output_tokens": 0, "synthetic": True}),
        )
        db.add(call)
        db.flush()
        return Claim(task.id, call.id, task.lease_token, task.request_payload, task.timeout_seconds, task.provider, config)


def finish(claim: Claim, raw: str | None, parsed: dict | None, error: str | None, session_factory=SessionLocal, *, usage=None):
    with session_factory.begin() as db:
        initial = db.get(AssessmentTask, claim.task_id)
        _paper_item(db, UUID(initial.snapshot["paper_instance_item_id"]), active=False)
        task = db.scalar(select(AssessmentTask).where(AssessmentTask.id == claim.task_id).with_for_update().execution_options(populate_existing=True))
        call = db.get(AssessmentCall, claim.call_id)
        if call.status in {"succeeded", "failed", "late"}:
            return  # A repeated finish must not rewrite the first received output.
        if usage is not None:
            call.usage = {**call.usage, **usage}
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
    raw, parsed, error, usage = None, None, None, None
    started = time.monotonic()
    try:
        # Injected adapters are for offline tests. A real task must never silently
        # fall back to fake on a configuration or transport error.
        transport = adapter or (BailianEquivalenceAdapter(claim.config) if claim.provider == "bailian" else FakeEquivalenceAdapter())
        output = transport.invoke(claim.request, timeout_seconds=claim.timeout_seconds)
        if isinstance(output, ProviderResult):
            raw, content, usage, error = output.raw, output.content, output.usage, output.error
        else:
            raw = content = output
            if claim.provider != "fake":
                usage, error = {"usage_status": "unknown", "cost_cny": None}, "MODEL_USAGE_UNKNOWN"
        if time.monotonic() - started > claim.timeout_seconds:
            error = "MODEL_TIMEOUT"
        elif error:
            pass
        elif not isinstance(content, str) or len(content) > 64000:
            error = "MODEL_SCHEMA_INVALID"
            raw = raw[:64000] if isinstance(raw, str) else repr(type(raw))
        else:
            try:
                parsed = parse_response(content, claim.request)
            except (ValueError, TypeError, ValidationError):
                error = "MODEL_SCHEMA_INVALID"
    except TimeoutError:
        error = "MODEL_TIMEOUT"
    except Exception:
        # Exception strings can contain provider credentials/input; don't log them.
        error = "MODEL_PROVIDER_FAILED"
    if claim.provider == "bailian" and usage is None:
        usage = {"usage_status": "unknown", "cost_cny": None, "synthetic": False}
    finish(claim, raw, parsed, error, session_factory, usage=usage)
    return True
