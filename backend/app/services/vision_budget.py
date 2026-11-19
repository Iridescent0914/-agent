"""Database-wide cumulative real-vision gate, independent of text/import budgets."""
from decimal import Decimal

from sqlalchemy import select, text

from app.core.config import get_settings
from app.models import VisionPageCall, VisionPageTask
from app.services.assessment import now


def budget_state(db, *, lock=False):
    if lock:
        db.execute(text("SELECT pg_advisory_xact_lock(60063026)"))
    db.flush()
    rows = db.execute(select(VisionPageCall, VisionPageTask).join(
        VisionPageTask, VisionPageCall.task_id == VisionPageTask.id
    ).where(VisionPageTask.provider == "bailian").execution_options(populate_existing=True)).all()
    cost, reserved, unknown = Decimal(0), Decimal(0), 0
    for call, task in rows:
        usage = call.usage
        if usage.get("usage_bound_exceeded"):
            unknown += 1
        if usage.get("usage_status") == "known" and usage.get("cost_cny") is not None:
            cost += Decimal(str(usage["cost_cny"]))
        elif (usage.get("usage_status") == "reserved" and call.status == "running"
              and task.status == "running" and task.lease_until and task.lease_until > now()):
            reserved += Decimal(str(usage["reserved_cost_cny"]))
        else:
            unknown += 1
    settings = get_settings()
    return {"scope": "database_cumulative_page_vision", "calls": len(rows), "unknown_calls": unknown,
            "estimated_cost_cny": float(cost), "reserved_cost_cny": float(reserved),
            "max_calls": settings.stage6_vision_max_calls, "max_budget_cny": settings.stage6_vision_max_budget_cny}
