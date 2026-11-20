"""Explicit source + shared-review synchronization; never overwrites divergent reviews."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.models import Question
from app.services.answer_units import is_canonical_answer_structure
from app.services.question_import import content_fingerprint
from app.services.question_pool_quality import pool_answer_issues
from app.services.profile_evidence import PROFILE_QUESTION_TYPES

ROOT = Path(__file__).resolve().parents[3]
SYNC_FIELDS = ("question_text", "question_type", "options", "correct_answer", "reference_answer_raw",
    "answer_match_type", "answer_normalization_rule", "grading_readiness", "import_issues", "assets")
SYSTEM_EVENTS = {"answer_structure_normalized", "pool_governance_applied", "question_catalog_synced"}
PROTECTED_TABLES = ("paper_instances", "paper_instance_items", "submissions", "attempts", "attempt_revisions",
    "student_masteries", "student_literacy_scores", "student_knowledge_statuses")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str).encode()).hexdigest()


def source_digest(record):
    return digest({f: record.get(f) for f in ("question_text", "options", "reference_answer_raw")})


def same(field, left, right):
    # Teacher content editing serializes assets as paths; relationship metadata
    # does not change the referenced image or justify rewriting a review.
    if field == "assets":
        return [a.get("path") for a in left or []] == [a.get("path") for a in right or []]
    return left == right


def state(question):
    return {f: deepcopy(getattr(question, f)) for f in SYNC_FIELDS}


def load_catalog(root=ROOT):
    records = {r["original_qid"]: r for chapter in ("rational", "expr")
        for r in json.loads((root / f"data/interim/{chapter}_all.json").read_text(encoding="utf-8"))}
    bundle = json.loads((root / "data/config/question_catalog_reviews.json").read_text(encoding="utf-8"))
    if bundle.get("schema_version") != "question-catalog-reviews-v1":
        raise ValueError("共享审核配置版本无效")
    reviews = bundle["questions"]
    if set(reviews) - set(records):
        raise ValueError("共享审核题号不在来源目录中")
    desired = {}
    for qid, record in records.items():
        if record["question_type"] not in PROFILE_QUESTION_TYPES and qid not in reviews:
            continue
        value = {f: deepcopy(record.get(f)) for f in SYNC_FIELDS}
        review = reviews.get(qid)
        if review:
            before, after = review["before"], review["after"]
            if (source_digest(record) != review["source_sha256"] or set(before) != set(after)
                    or not set(after) <= set(SYNC_FIELDS)
                    or any(not same(f, value[f], before[f]) for f in before)):
                raise ValueError(f"共享审核的来源基线已变化，请重新核对：{qid}")
            value.update(deepcopy(after))
        if value["grading_readiness"] == "machine_ready":
            if (value["question_type"] not in PROFILE_QUESTION_TYPES
                    or not is_canonical_answer_structure(value["correct_answer"])
                    or pool_answer_issues(value["correct_answer"], value["answer_match_type"], value["answer_normalization_rule"])):
                raise ValueError(f"共享题目不符合现有入池规则：{qid}")
        desired[qid] = value
    return desired, reviews, bundle["version"]


def protected_fingerprints(db):
    result = {}
    for name in PROTECTED_TABLES:
        table = Question.metadata.tables[name]
        rows = [dict(r) for r in db.execute(select(table).order_by(*table.primary_key.columns)).mappings()]
        result[name] = {"count": len(rows), "sha256": digest(rows)}
    return result


def plan(db, desired, reviews):
    rows = {q.original_qid: q for q in db.scalars(select(Question).where(
        Question.original_qid.in_(desired)).order_by(Question.original_qid).with_for_update())}
    changes, conflicts, updates = [], [], []
    for qid, after in sorted(desired.items()):
        q = rows.get(qid)
        if q is None:
            conflicts.append({"qid": qid, "reason": "missing_question_run_seed_first"})
            continue
        if q.archived_at is not None:
            conflicts.append({"qid": qid, "reason": "archived_question"})
            continue
        before = state(q)
        fields = [f for f in SYNC_FIELDS if not same(f, before[f], after[f])]
        if not fields:
            continue
        human = any(e.get("type") not in SYSTEM_EVENTS for e in q.review_history or [])
        if human:
            review = reviews.get(qid)
            known = bool(review) and source_digest(before) == review["source_sha256"] and all(
                same(f, before[f], after[f]) or (f in review["before"] and same(f, before[f], review["before"][f]))
                for f in SYNC_FIELDS)
            if not known:
                conflicts.append({"qid": qid, "reason": "divergent_teacher_review", "changed_fields": fields})
                continue
        changes.append({"qid": qid, "before_type": q.question_type, "after_type": after["question_type"],
            "before_readiness": q.grading_readiness, "after_readiness": after["grading_readiness"],
            "changed_fields": fields})
        updates.append((q, before, after, fields))
    all_rows = list(db.scalars(select(Question)))
    return {"scope_count": len(desired), "target_pool_count": sum(v["grading_readiness"] == "machine_ready" for v in desired.values()),
        "database_total": len(all_rows),
        "database_active": sum(q.archived_at is None for q in all_rows),
        "database_pool_count": sum(q.archived_at is None and q.grading_readiness == "machine_ready" for q in all_rows),
        "scoped_current_pool_count": sum(q.archived_at is None and q.grading_readiness == "machine_ready"
                                         for q in rows.values()),
        "outside_scope_ready_ids": [q.original_qid for q in all_rows if q.original_qid not in desired
                                    and q.archived_at is None and q.grading_readiness == "machine_ready"],
        "changes": changes, "conflicts": conflicts}, updates


def synchronize(db, *, apply=False, backup_dir=None, catalog=None):
    desired, reviews, version = catalog or load_catalog()
    report, updates = plan(db, desired, reviews)
    report.update(version=version, applied=False)
    if not apply or report["conflicts"] or not updates:
        return report
    before_business = protected_fingerprints(db)
    backup_dir = backup_dir or ROOT / "data/uploads/question_catalog_sync"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"before-{uuid4().hex}.json"
    snapshot = [{"qid": q.original_qid, "id": str(q.id), "state": before,
        "review_revision": q.review_revision, "review_history": q.review_history,
        "reviewed_by": q.reviewed_by, "reviewed_at": q.reviewed_at,
        "content_fingerprint": q.content_fingerprint, "has_image": q.has_image,
        "updated_at": q.updated_at} for q, before, _, _ in updates]
    backup.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    at = datetime.now(timezone.utc)
    for q, before, after, fields in updates:
        revision = q.review_revision
        for f in fields:
            setattr(q, f, deepcopy(after[f]))
        q.content_fingerprint = content_fingerprint(q.question_text, q.options, q.assets)
        q.has_image = bool(q.assets)
        q.review_revision += 1
        q.reviewed_by, q.reviewed_at = version, at
        q.review_history = [*(q.review_history or []), {"event_id": str(uuid4()), "type": "question_catalog_synced",
            "at": at.isoformat(), "reviewer": version, "before_revision": revision,
            "after_revision": q.review_revision, "before": before, "after": state(q)}]
    db.flush()
    if protected_fingerprints(db) != before_business:
        raise ValueError("历史试卷、作答或画像发生变化，同步事务必须回滚")
    report.update(applied=True, backup_path=str(backup), business_unchanged=True,
        scoped_current_pool_count=sum(q.archived_at is None and q.grading_readiness == "machine_ready"
            for q in db.scalars(select(Question).where(Question.original_qid.in_(desired)))),
        database_pool_count=sum(q.archived_at is None and q.grading_readiness == "machine_ready"
                                for q in db.scalars(select(Question))))
    return report
