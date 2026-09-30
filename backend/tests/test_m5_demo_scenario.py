from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.database import SessionLocal
from app.services.demo_scenarios import prepare_m5_scenario, validate_m5_scenario


CONFIG_PATH = Path(__file__).resolve().parents[2] / "data" / "seed" / "m5_demo_scenarios.json"


@pytest.fixture
def db():
    with SessionLocal() as session:
        try:
            yield session
        finally:
            session.rollback()


@pytest.fixture
def config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def test_m5_start_is_deterministic_and_idempotent(db, config):
    summary, created = prepare_m5_scenario(db, config, "m5_start")
    assert created is True
    assert summary["assignment"]["summary"]["active_published_count"] == 5
    assert summary["assignment"]["summary"]["unsubmitted_count"] == 5
    assert validate_m5_scenario(db, config, "m5_start") == []

    repeated, repeated_created = prepare_m5_scenario(db, config, "m5_start")
    assert repeated_created is False
    assert repeated["assignment_id"] == summary["assignment_id"]
    assert repeated["assignment"]["summary"] == summary["assignment"]["summary"]


def test_m5_showcase_contains_all_teacher_workbench_states(db, config):
    summary, created = prepare_m5_scenario(db, config, "m5_showcase")
    assert created is True
    progress = summary["assignment"]["summary"]
    assert progress["active_published_count"] == 5
    assert progress["unsubmitted_count"] == 1
    assert progress["needs_review_count"] == 1
    assert progress["completed_count"] == 3
    assert progress["pending_attempt_count"] == 1
    assert validate_m5_scenario(db, config, "m5_showcase") == []

