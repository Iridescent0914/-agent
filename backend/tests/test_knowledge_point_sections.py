"""The seed trees, importer, and DB correction must agree."""

from __future__ import annotations

import json
import runpy
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal, get_db
from app.main import app
from app.models import KnowledgePoint, Question
from app.services.html_kp_sections import sections_for_tree
from app.services.seed_importer import _question_values, _validate_inputs


CODE_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = CODE_ROOT / "backend/alembic/versions/0026_backfill_pilot_kp_sections.py"


def test_pilot_kp_sections_match_migration_and_questions() -> None:
    migration_values = runpy.run_path(str(MIGRATION))["SECTIONS_BY_CODE"]
    expected = {}
    question_count = 0
    for stem in ("rational", "expr"):
        tree = json.loads(
            (CODE_ROOT / f"data/seed/{stem}_knowledge_tree.json").read_text(encoding="utf-8")
        )
        questions = json.loads(
            (CODE_ROOT / f"data/interim/{stem}_all.json").read_text(encoding="utf-8")
        )
        canonical = sections_for_tree(tree)
        for kp in tree["knowledge_points"]:
            assert kp["section"] == canonical[kp["id"]]
            expected[kp["id"]] = canonical[kp["id"]]
        for question in questions:
            assert question["section"] == expected[question["main_knowledge_point"]["id"]]
        question_count += len(questions)
    assert len(expected) == 21
    assert migration_values == expected
    assert question_count == 180


def test_seed_import_uses_html_section_when_question_section_drifts() -> None:
    tree = json.loads(
        (CODE_ROOT / "data/seed/rational_knowledge_tree.json").read_text(encoding="utf-8")
    )
    question = json.loads(
        (CODE_ROOT / "data/seed/rational_questions.json").read_text(encoding="utf-8")
    )[0]
    broken = deepcopy(question)
    broken["section"] = tree["sections"][1]["name"]
    _validate_inputs([broken], tree, require_machine_ready=False)
    kp = SimpleNamespace(id=1, section=sections_for_tree(tree)[question["main_knowledge_point"]["id"]])
    assert _question_values(broken, kp)["section"] == kp.section


def test_seed_import_rejects_tree_section_drift_from_html() -> None:
    tree = json.loads(
        (CODE_ROOT / "data/seed/rational_knowledge_tree.json").read_text(encoding="utf-8")
    )
    question = json.loads(
        (CODE_ROOT / "data/seed/rational_questions.json").read_text(encoding="utf-8")
    )[0]
    tree["knowledge_points"][0]["section"] = tree["sections"][1]["name"]
    with pytest.raises(ValueError, match="differs from the HTML graph"):
        _validate_inputs([question], tree, require_machine_ready=False)


def test_migration_replaces_conflicting_database_section(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = runpy.run_path(str(MIGRATION))
    code, canonical = next(iter(migration["SECTIONS_BY_CODE"].items()))

    class FakeConnection:
        def __init__(self) -> None:
            self.updated: list[dict[str, str]] = []

        def execute(self, statement, parameters):
            if str(statement).startswith("SELECT"):
                return SimpleNamespace(all=lambda: [(code, "错误节名")])
            self.updated.append(parameters)

    connection = FakeConnection()
    monkeypatch.setattr(migration["op"], "get_bind", lambda: connection)
    migration["upgrade"]()
    assert connection.updated == [{"section": canonical, "code": code}]


def test_question_catalog_uses_formal_kp_sections() -> None:
    with SessionLocal() as session:
        loaded = session.execute(
            select(KnowledgePoint.code, KnowledgePoint.chapter_code, KnowledgePoint.section)
            .where(KnowledgePoint.chapter_code.in_(("math.g6.rational", "math.g6.expr")))
        ).all()
    if not loaded:
        pytest.skip("pilot knowledge points are not seeded in this database")

    response = TestClient(app).get("/api/v1/questions/catalog")
    assert response.status_code == 200
    chapters = {chapter["code"]: chapter for chapter in response.json()}
    for code, chapter_code, section in loaded:
        if chapter_code not in chapters:
            continue  # The catalog only exposes chapters with active questions.
        chapter = chapters[chapter_code]
        point = next(point for point in chapter["knowledge_points"] if point["code"] == code)
        assert point["section"] == section
        assert section in chapter["sections"]


def test_question_section_cannot_override_formal_catalog_section() -> None:
    with SessionLocal() as session:
        pair = session.execute(
            select(Question, KnowledgePoint)
            .join(KnowledgePoint, Question.main_knowledge_point_id == KnowledgePoint.id)
            .where(
                KnowledgePoint.chapter_code.in_(("math.g6.rational", "math.g6.expr")),
                KnowledgePoint.section.is_not(None),
                Question.archived_at.is_(None),
            )
            .limit(1)
        ).first()
        if pair is None:
            pytest.skip("pilot questions are not seeded in this database")
        question, point = pair
        question.section = "临时错误节"
        session.flush()
        app.dependency_overrides[get_db] = lambda: session
        try:
            response = TestClient(app).get("/api/v1/questions/catalog")
            assert response.status_code == 200
            chapter = next(
                chapter for chapter in response.json()
                if chapter["code"] == point.chapter_code
            )
            catalog_point = next(
                item for item in chapter["knowledge_points"]
                if item["code"] == point.code
            )
            assert catalog_point["section"] == point.section
            assert "临时错误节" not in chapter["sections"]
        finally:
            app.dependency_overrides.pop(get_db, None)
            session.rollback()
