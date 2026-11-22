from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import engine
from app.models import Question, QuestionImportBatch, QuestionImportItem
from app.services.question_fingerprint_v2 import (
    VERSION, catalog_content_key, catalog_image_hashes, content_key, fingerprint,
)
from app.services.question_import_s4_exact import exact_catalog_matches
from app.services.question_import_s4_near import _differences, catalog_near_matches


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


def test_math_aware_fingerprint_keeps_critical_differences():
    def value(stem, options=None, images=None):
        return fingerprint(content_key(stem, options, images or []))

    assert value("4、1.2×10⁸ 米") == value("１．２×１０^８ 米")
    assert value("1.2×10⁸ 米") != value("1.2×108 米")
    assert value("xⁿ") == value("x^n")
    assert value("10⁻⁸") == value("10^-8")
    assert value("①类") != value("1类")
    assert value("-2℃") == value("−2°C")
    assert value("-2℃") != value("+2℃")
    assert value("2米") != value("2千米")
    assert value("选出正确项", [{"key": "A", "text": "1"}, {"key": "B", "text": "2"}]) != value(
        "选出正确项", [{"key": "A", "text": "2"}, {"key": "B", "text": "1"}])
    assert value("看图计算", images=["a" * 64]) != value("看图计算", images=["b" * 64])
    assert content_key("如图计算", [], []) is None
    assert content_key("[ASSET:gone.png] 计算", [], []) is None
    assert content_key("计算 1+2", [], [], missing_image=True) is None
    assert value("H₂O") != value("H2O")
    assert value("4、计算 1+2") == value("5、计算 1+2")
    assert value("选择", [{"key": "A", "text": "1. 数值"}]) != value(
        "选择", [{"key": "A", "text": "2. 数值"}])


def test_catalog_image_requires_real_bytes(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "teacher_asset_dir", tmp_path)
    path = "assets/teacher/" + uuid.uuid4().hex + ".png"
    (tmp_path / path.removeprefix("assets/teacher/")).write_bytes(b"first image")
    first = catalog_image_hashes([{"path": path}], has_image=True)
    assert first is not None and len(first) == 1
    (tmp_path / path.removeprefix("assets/teacher/")).write_bytes(b"changed image")
    assert catalog_image_hashes([{"path": path}], has_image=True) != first
    assert catalog_image_hashes([{"path": "assets/teacher/" + uuid.uuid4().hex + ".png"}]) is None


def test_s4_returns_all_indexed_catalog_matches_and_revisions(db):
    source = db.scalar(select(Question).where(
        Question.exact_fingerprint_v2.is_not(None), Question.has_image.is_(False),
        Question.archived_at.is_(None), Question.reference_answer_raw != "",
    ))
    assert source is not None
    batch_id = uuid.uuid4()
    batch = QuestionImportBatch(
        id=batch_id, owner_scope="internal-demo", request_fingerprint=uuid.uuid4().hex,
        catalog_snapshot_sha256="0" * 64, source_mode="questions_only",
        status="review_pending", stage="s2_completed", files=[],
        checkpoint={"s3": {"global_dedupe": {"status": "completed"}}},
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(batch)
    db.flush()
    item = QuestionImportItem(
        batch_id=batch_id, ordinal=1, revision=3, is_fake=False,
        payload={"content": {"question_text": source.question_text, "options": source.options or [],
                             "asset_ids": []},
                 "answers": [{"answer_id": "teacher-1", "text": source.reference_answer_raw}],
                 "selected_answer_id": "teacher-1"},
    )
    db.add(item)
    db.flush()
    values = {column.name: getattr(source, column.name) for column in Question.__table__.columns
              if column.name not in {"id", "original_qid", "created_at", "updated_at"}}
    duplicate = Question(id=uuid.uuid4(), original_qid="s4-test-" + uuid.uuid4().hex, **values)
    duplicate.reference_answer_raw = "另一种答案"
    db.add(duplicate)
    db.flush()

    result = exact_catalog_matches(db, batch_id)
    assert result["version"] == VERSION
    row = result["results"][0]
    assert row["item_id"] == str(item.id) and row["revision"] == 3
    assert row["status"] == "exact_matches"
    assert {match["question_id"] for match in row["matches"]} == {str(source.id), str(duplicate.id)}
    assert next(match for match in row["matches"] if match["question_id"] == str(duplicate.id))["answer_text_differs"]
    assert row["stale_catalog_rows"] == 0

    item.payload = {"content": {"question_text": "计算 1+2", "options": [], "asset_ids": []},
                    "extraction": {"figure_status": "uncertain", "diagram_clues": ["右侧图形"]}}
    db.flush()
    assert exact_catalog_matches(db, batch_id)["results"][0]["status"] == "unverifiable"


def test_s4_near_returns_numeric_variant_without_calling_it_exact(db, monkeypatch):
    monkeypatch.setattr("app.services.question_import_s4_near.RECALL_LIMIT", 100000)
    source = db.scalar(select(Question).where(Question.has_image.is_(False),
                                             Question.archived_at.is_(None)))
    assert source is not None
    values = {column.name: getattr(source, column.name) for column in Question.__table__.columns
              if column.name not in {"id", "original_qid", "created_at", "updated_at"}}
    formal = Question(id=uuid.uuid4(), original_qid="s4-near-" + uuid.uuid4().hex, **values)
    formal.question_text = "计算 1+2 米，结果是多少？"
    formal.options = []
    formal.assets = []
    formal.has_image = False
    formal.exact_fingerprint_v2 = fingerprint(catalog_content_key(formal.question_text, [], []))
    db.add(formal)
    unrelated = Question(id=uuid.uuid4(), original_qid="s4-unrelated-" + uuid.uuid4().hex, **values)
    unrelated.question_text = "描述冬季天气与植物生长的关系。"
    unrelated.options = []
    unrelated.assets = []
    unrelated.has_image = False
    unrelated.exact_fingerprint_v2 = fingerprint(catalog_content_key(unrelated.question_text, [], []))
    db.add(unrelated)
    batch_id = uuid.uuid4()
    db.add(QuestionImportBatch(
        id=batch_id, owner_scope="internal-demo", request_fingerprint=uuid.uuid4().hex,
        catalog_snapshot_sha256="0" * 64, source_mode="questions_only",
        status="review_pending", stage="s2_completed", files=[],
        checkpoint={"s3": {"global_dedupe": {"status": "completed"}}},
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    db.flush()
    item = QuestionImportItem(batch_id=batch_id, ordinal=1, revision=1, is_fake=False,
                              payload={"content": {"question_text": "计算 1+3 米，结果是多少？",
                                                   "options": [], "asset_ids": []}})
    db.add(item)
    db.flush()

    result = catalog_near_matches(db, batch_id)
    assert result["exact_results"][0]["matches"] == []
    match = next(row for row in result["results"][0]["near_matches"]
                 if row["question_id"] == str(formal.id))
    assert all(row["question_id"] != str(unrelated.id)
               for row in result["results"][0]["near_matches"])
    assert result["near_review_min_similarity"] == 0.30
    assert "题干中的数字或指数不同" in match["differences"]
    assert match["image_relation"] == "均无题图"
    assert result["calibrated"] is False


def test_s4_near_uses_gist_index_and_flags_same_stem_with_different_images(db):
    db.execute(text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(row[0] for row in db.execute(text(
        "EXPLAIN SELECT id FROM questions WHERE archived_at IS NULL "
        "ORDER BY (question_text || ' ' || coalesce(options::text, '')) <-> '计算 1+2 米' LIMIT 6"
    )))
    assert "ix_questions_active_text_trgm" in plan

    question = Question(question_text="计算图中面积", options=[])
    candidate = {"content": {"question_text": "计算图中面积", "options": []}}
    differences, relation = _differences(
        candidate, question,
        {"images": ["a" * 64]}, {"images": ["b" * 64]},
    )
    assert relation == "题图文件不同"
    assert "题干文字相同，但题图文件不同，请人工核对是否为同一道题" in differences
    assert all("距离" not in value for value in differences)
