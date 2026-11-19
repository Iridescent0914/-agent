"""Shared catalog updates use isolated transactions; no business DB or model calls."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import KnowledgePoint, Question
from app.services import question_catalog_sync as service
from app.services.seed_importer import ImportStats, _import_questions, _preserve_reviewed_answer, _question_values
from tests.test_stage6_s1 import context


def catalog(question, **patch):
    before = service.state(question)
    after = {**deepcopy(before), **patch}
    fields = [f for f in before if not service.same(f, before[f], after[f])]
    reviews = {question.original_qid: {"source_sha256": service.source_digest(before),
        "before": {f: before[f] for f in fields}, "after": {f: after[f] for f in fields}}}
    return {question.original_qid: after}, reviews, "test-shared-review"


def test_shared_catalog_has_77_ready_groups_and_two_explicit_reclassifications():
    desired, reviews, _ = service.load_catalog()
    assert len(desired) == 79 and len(reviews) == 4
    assert sum(v["grading_readiness"] == "machine_ready" for v in desired.values()) == 77
    for qid in ("q.math.g6.rational.school.070", "q.math.g6.expr.school.032"):
        assert desired[qid]["question_type"] == "solution"
        assert desired[qid]["grading_readiness"] == "raw_answer_only"
    expr = desired["q.math.g6.expr.school.013"]
    assert [v["answer"] for v in expr["correct_answer"]["items"]] == ["A", "B", "B", "D"]
    assert "pool_source_conflict" in expr["import_issues"]  # Audit evidence is not silently erased.


def test_stale_shared_review_is_rejected_when_source_question_changes(tmp_path):
    for relative in ("data/interim/rational_all.json", "data/interim/expr_all.json",
                     "data/config/question_catalog_reviews.json"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((service.ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8")
    source_path = tmp_path / "data/interim/expr_all.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    question = next(q for q in source if q["original_qid"] == "q.math.g6.expr.school.013")
    question["question_text"] += "\n题干已重新修订"
    source_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="来源基线已变化"):
        service.load_catalog(tmp_path)


def test_preview_apply_backup_revisions_idempotence_and_history_protection(context, tmp_path):
    db, _, _, _, question = context
    original = service.state(question)
    version = question.review_revision
    data = catalog(question, grading_readiness="raw_answer_only")
    business = service.protected_fingerprints(db)
    preview = service.synchronize(db, catalog=data, backup_dir=tmp_path)
    assert not preview["applied"] and preview["changes"] and not list(tmp_path.iterdir())
    assert service.state(question) == original
    applied = service.synchronize(db, catalog=data, apply=True, backup_dir=tmp_path)
    assert applied["applied"] and applied["business_unchanged"]
    snapshot = json.loads(Path(applied["backup_path"]).read_text(encoding="utf-8"))
    assert snapshot[0]["state"] == original
    assert question.review_revision == version + 1
    assert question.review_history[-1]["type"] == "question_catalog_synced"
    assert service.protected_fingerprints(db) == business
    repeated = service.synchronize(db, catalog=data, apply=True, backup_dir=tmp_path)
    assert not repeated["changes"] and not repeated["applied"]
    assert question.review_revision == version + 1 and len(list(tmp_path.iterdir())) == 1


def test_unknown_teacher_changes_block_the_entire_plan_without_backups(context, tmp_path):
    db, _, _, _, question = context
    data = catalog(question, grading_readiness="raw_answer_only")
    question.correct_answer = {"kind": "group", "schema_version": "answer-units-v1", "items": [{"item_id": "teacher", "answer": "OTHER"}]}
    question.review_history = [{"type": "promoted"}]
    db.flush()
    before = service.state(question)
    result = service.synchronize(db, catalog=data, apply=True, backup_dir=tmp_path)
    assert not result["applied"] and result["conflicts"][0]["reason"] == "divergent_teacher_review"
    assert service.state(question) == before and not list(tmp_path.iterdir())


def test_known_shared_before_state_can_be_updated_after_prior_teacher_review(context, tmp_path):
    db, _, _, _, question = context
    data = catalog(question, grading_readiness="raw_answer_only")
    question.review_history = [{"type": "promoted"}]
    db.flush()
    result = service.synchronize(db, catalog=data, apply=True, backup_dir=tmp_path)
    assert result["applied"] and question.review_history[0]["type"] == "promoted"


def test_one_conflict_prevents_other_valid_updates(context, tmp_path):
    db, _, _, _, question = context
    values = {c.name: deepcopy(getattr(question, c.name)) for c in Question.__table__.columns
              if c.name not in {"id", "original_qid", "updated_at"}}
    other = Question(original_qid="sync-" + uuid4().hex, **values)
    db.add(other); db.flush()
    desired, reviews, version = catalog(question, grading_readiness="raw_answer_only")
    more, _, _ = catalog(other, grading_readiness="raw_answer_only")
    desired.update(more)
    question.review_history = [{"type": "draft_saved"}]
    question.answer_normalization_rule = {"teacher_changed": True}; db.flush()
    result = service.synchronize(db, catalog=(desired, reviews, version), apply=True, backup_dir=tmp_path)
    assert result["conflicts"] and not result["applied"] and len(result["changes"]) == 1
    assert other.grading_readiness == "machine_ready" and not list(tmp_path.iterdir())


def test_archived_or_missing_question_is_not_recreated_or_unarchived(context, tmp_path):
    db, _, _, _, question = context
    data = catalog(question, grading_readiness="raw_answer_only")
    from app.services.assessment import now
    question.archived_at = now(); db.flush()
    result = service.synchronize(db, catalog=data, apply=True, backup_dir=tmp_path)
    assert result["conflicts"][0]["reason"] == "archived_question" and not result["applied"]
    desired, reviews, version = data
    desired["missing-question"] = deepcopy(next(iter(desired.values())))
    result = service.synchronize(db, catalog=(desired, reviews, version), apply=True, backup_dir=tmp_path)
    assert {c["reason"] for c in result["conflicts"]} == {"archived_question", "missing_question_run_seed_first"}


def test_seed_refresh_preserves_reviewed_type_and_stem():
    for event in ("content_updated", "question_catalog_synced"):
        q = type("ReviewedQuestion", (), {"review_history": [{"type": event}]})()
        values = {"question_type": "fill_blank", "question_text": "source", "correct_answer": "source",
            "grading_readiness": "machine_ready", "assets": [], "content_fingerprint": "old", "chapter_code": "source"}
        _preserve_reviewed_answer(q, values)
        assert values == {}


def test_asset_relationship_metadata_is_not_an_answer_or_content_conflict():
    assert service.same("assets", [{"path": "a.svg"}], [{"path": "a.svg", "relationship_id": "r1"}])
    assert not service.same("assets", [{"path": "a.svg"}], [{"path": "b.svg"}])


def test_collaborator_73_ready_becomes_77_and_seed_refresh_keeps_shared_reviews(context, tmp_path):
    db, _, _, _, unrelated = context
    from app.services.knowledge_catalog import formal_kp
    source = [r for chapter in ("rational", "expr")
        for r in json.loads((service.ROOT / f"data/interim/{chapter}_all.json").read_text(encoding="utf-8"))
        if r["question_type"] in service.PROFILE_QUESTION_TYPES]
    desired, reviews, version = service.load_catalog()
    suffix = ".sync-test-" + uuid4().hex
    renamed = {qid: qid + suffix for qid in desired}
    test_catalog = ({renamed[qid]: value for qid, value in desired.items()},
                    {renamed[qid]: value for qid, value in reviews.items()}, version)
    for record in source:
        record["original_qid"] = renamed[record["original_qid"]]
    kp_by_code = {}
    for record in source:
        code = record["main_knowledge_point"]["id"]
        if code in kp_by_code:
            continue
        kp = db.scalar(select(KnowledgePoint).where(KnowledgePoint.code == code))
        if kp is None:
            point = formal_kp(code)
            kp = KnowledgePoint(code=code, name=point["name"], chapter_code=point["chapter_code"],
                chapter_name=point["chapter_name"], section=point["section"])
            db.add(kp); db.flush()
        kp_by_code[code] = kp
    by_qid = {}
    for record in source:
        kp = kp_by_code[record["main_knowledge_point"]["id"]]
        q = Question(original_qid=record["original_qid"], **_question_values(record, kp))
        db.add(q); by_qid[q.original_qid] = q
    # Old governance-only history must not block importing newer source fixes.
    for q in list(by_qid.values())[:3]:
        assert q.grading_readiness == "machine_ready"
        q.grading_readiness = "raw_answer_only"
        q.review_history = [{"type": "pool_governance_applied"}]
    db.flush()
    assert sum(q.grading_readiness == "machine_ready" for q in by_qid.values()) == 73
    business = service.protected_fingerprints(db)
    result = service.synchronize(db, apply=True, backup_dir=tmp_path, catalog=test_catalog)
    assert result["applied"] and result["conflicts"] == []
    assert sum(q.grading_readiness == "machine_ready" for q in by_qid.values()) == 77
    assert by_qid[renamed["q.math.g6.rational.school.070"]].question_type == "solution"
    assert by_qid[renamed["q.math.g6.expr.school.032"]].question_type == "solution"
    _import_questions(db, ImportStats(), source, kp_by_code)
    assert by_qid[renamed["q.math.g6.rational.school.070"]].question_type == "solution"
    assert by_qid[renamed["q.math.g6.expr.school.032"]].question_type == "solution"
    assert by_qid[renamed["q.math.g6.expr.school.013"]].grading_readiness == "machine_ready"
    assert sum(q.grading_readiness == "machine_ready" for q in by_qid.values()) == 77
    assert service.protected_fingerprints(db) == business
