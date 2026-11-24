"""Offline evidence/annotation tests; no provider calls and no business writes."""
from copy import deepcopy
import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import VisionPageCall, VisionPageTask
from app.services.question_catalog_sync import protected_fingerprints
from app.services.stage6_evaluation import evaluate, literal_answer
from app.services.stage6_evaluation_export import export_evidence, label_template
from scripts.evaluate_stage6 import main
from tests.test_stage6_s5 import context, base_context, submit, audit_request, complete
from tests.test_stage6_s3_transcription import prepared as prepare_vision


@pytest.fixture
def vision_prepared(base_context, monkeypatch, request):
    return prepare_vision.__wrapped__(base_context, monkeypatch, request)


def sample(kind="equivalence", **kwargs):
    return {"sample_id": "sample-" + uuid4().hex, "kind": kind, "paper_key": "paper-1",
        "provider": "bailian", "model": "offline-record-fixture", "prompt_version": "fixture-v2",
        "policy_version": "fixture-policy", "protocol": f"stage6-{'vision' if kind == 'vision' else 'equivalence'}-request-v2",
        "task_status": "needs_review" if kind == "vision" else "succeeded", "verified_real_result": True,
        "calls": [{"call_id": uuid4().hex, "status": "succeeded", "late": False, "synthetic": False,
                   "usage_status": "known", "cost_cny": .01, "error_code": None, "seconds": 1}],
        **({"card_key": "card-1", "page_no": 1, "total_pages": 1, "layout_version": "answer-card-v3",
            "image_sha256": "a" * 64, "identity_status": "verified", "units": [
                {"unit_key": "question.sub_2.blank_3", "order_no": 3, "subquestion_no": 2, "blank_no": 3,
                 "output": {"recognition_status": "readable", "answer": "-1/2"}}]} if kind == "vision" else {
            "reference_answer": "较小", "student_answer": "小的", "answer_context": {"question_text": "【本空】"},
            "output": {"verdict": "equivalent", "reason_code": "contextual_match", "explanation": "离线夹具"}}), **kwargs}


def bundle(*samples):
    evidence = {"schema_version": "stage6-eval-evidence-v1", "samples": list(samples)}
    return evidence, label_template(evidence, "test-dev-v1")


def annotate(labels, *, verdict="equivalent", answer="-1/2", status="readable"):
    for label in labels["samples"]:
        label.update(annotation_status="labelled", reviewer_alias="reviewer-01", reviewed_at="2026-11-20T12:00:00+08:00")
        if label["units"]:
            label["identity_correct"] = True
            for unit in label["units"]:
                unit.update(annotation_status="labelled", recognition_status=status, answer=answer)
        else:
            label.update(gold_verdict=verdict, gold_reason="人工根据冻结题干核对")


def metrics(report):
    return report["groups"][0]["metrics"]


def test_unlabelled_outputs_do_not_become_gold_or_perfect_accuracy():
    evidence, labels = bundle(sample(), sample("vision"))
    report = evaluate(evidence, labels)
    assert report["acceptance_status"] == "not_evaluated"
    assert report["automatic_adoption_error_rate"] is None
    for group in report["groups"]:
        rates = [value for value in group["metrics"].values() if isinstance(value, dict)]
        assert rates and all(r["denominator"] == 0 and r["rate"] is None for r in rates)
    assert all(s["gold_verdict"] is None and s["reviewer_alias"] is None for s in labels["samples"])


@pytest.mark.parametrize("actual,gold,agree,coverage,false_accept,false_reject", [
    ("equivalent", "equivalent", 1, 1, 0, 0), ("not_equivalent", "equivalent", 0, 1, 0, 1),
    ("equivalent", "not_equivalent", 0, 1, 1, 0), ("uncertain", "equivalent", None, 0, 0, 0),
])
def test_contextual_agreement_and_coverage_have_distinct_denominators(actual, gold, agree, coverage, false_accept, false_reject):
    evidence, labels = bundle(sample(output={"verdict": actual}))
    annotate(labels, verdict=gold)
    result = metrics(evaluate(evidence, labels))
    assert result["determinate_agreement"]["rate"] == agree
    assert result["determinate_coverage"]["rate"] == coverage
    assert result["false_accepts"] == false_accept and result["false_rejects"] == false_reject


def test_failed_model_stays_in_gold_coverage_and_vision_readable_denominator():
    evidence, labels = bundle(sample(verified_real_result=False, task_status="failed", output=None),
                              sample("vision", verified_real_result=False, task_status="failed", units=[{
                                  "unit_key": "u", "output": None}]))
    annotate(labels)
    report = evaluate(evidence, labels)
    by_kind = {g["version"]["kind"]: g["metrics"] for g in report["groups"]}
    assert by_kind["equivalence"]["determinate_coverage"] == {"numerator": 0, "denominator": 1, "rate": 0}
    assert by_kind["vision"]["transcription_accuracy"] == {"numerator": 0, "denominator": 1, "rate": 0}
    assert by_kind["vision"]["readable_miss_rate"]["rate"] == 1


def test_uncertain_gold_counts_overconfidence_separately():
    evidence, labels = bundle(sample())
    annotate(labels, verdict="uncertain")
    result = metrics(evaluate(evidence, labels))
    assert result["overconfident_outputs"] == 1
    assert result["determinate_coverage"]["rate"] is None


@pytest.mark.parametrize("answer,expected", [(" − 1 / 2 ", 1), ("0.5", 0), ("1/2", 0), ("-1/2²", 0)])
def test_transcription_preserves_math_instead_of_testing_equivalence(answer, expected):
    evidence, labels = bundle(sample("vision"))
    annotate(labels, answer=answer)
    assert metrics(evaluate(evidence, labels))["transcription_accuracy"]["rate"] == expected
    assert literal_answer("x²") != literal_answer("x2")


def test_identity_unknown_prevents_complete_page_even_with_correct_text():
    evidence, labels = bundle(sample("vision", identity_status="unconfirmed"))
    annotate(labels)
    result = metrics(evaluate(evidence, labels))
    assert result["transcription_accuracy"]["rate"] == 1
    assert result["complete_page_rate"]["rate"] == result["complete_card_rate"]["rate"] == 0


@pytest.mark.parametrize("gold_status,actual_status,blank_content,content_blank", [
    ("blank", "readable", 1, 0), ("readable", "blank", 0, 1), ("blank", "blank", 0, 0),
])
def test_blank_confusion_is_separate_from_transcription_accuracy(gold_status, actual_status, blank_content, content_blank):
    original = sample("vision")
    original["units"][0]["output"] = {"recognition_status": actual_status, "answer": "1" if actual_status == "readable" else None}
    evidence, labels = bundle(original)
    annotate(labels, status=gold_status, answer="1" if gold_status == "readable" else None)
    result = metrics(evaluate(evidence, labels))
    assert result["blank_to_content"] == blank_content and result["content_to_blank"] == content_blank


def test_partial_card_or_duplicate_attempts_cannot_become_complete_card():
    evidence, labels = bundle(sample("vision", total_pages=2))
    annotate(labels)
    result = metrics(evaluate(evidence, labels))
    assert result["incomplete_card_selection"] == 1 and result["complete_card_rate"]["rate"] == 0
    evidence, labels = bundle(sample("vision"), sample("vision"))
    annotate(labels)
    result = metrics(evaluate(evidence, labels))
    assert result["ambiguous_card_selection"] == 1 and result["complete_card_rate"]["rate"] is None


def test_complete_card_requires_every_page_to_be_independently_annotated():
    evidence, labels = bundle(sample("vision", total_pages=2),
                              sample("vision", page_no=2, total_pages=2, image_sha256="b" * 64))
    annotate(labels)
    report = evaluate(evidence, labels)
    assert report["counts"]["images"] == 2
    assert metrics(report)["complete_card_rate"] == {"numerator": 1, "denominator": 1, "rate": 1}
    labels["samples"][1]["units"][0]["annotation_status"] = "pending"
    result = metrics(evaluate(evidence, labels))
    assert result["unlabelled_cards"] == 1 and result["complete_card_rate"]["rate"] is None


@pytest.mark.parametrize("change,reason", [({"provider": "fake"}, "synthetic_or_unsupported_provider"),
    ({"protocol": "stage6-equivalence-request-v1"}, "legacy_protocol"),
    ({"task_status": "running", "verified_real_result": False}, "unfinished")])
def test_fake_legacy_and_unfinished_records_are_counted_outside_quality(change, reason):
    evidence, labels = bundle(sample(**change))
    report = evaluate(evidence, labels)
    assert report["counts"]["excluded"][reason] == 1 and report["groups"] == []


def test_hash_missing_units_duplicate_labels_and_cross_split_sources_are_rejected():
    evidence, labels = bundle(sample("vision"), sample("vision"))
    for mutate in (lambda x: x.update(evidence_sha256="0" * 64),
                   lambda x: x["samples"][0].update(units=[]),
                   lambda x: x["samples"].append(deepcopy(x["samples"][0])),
                   lambda x: x["samples"][1].update(split="acceptance", source_group_id="different")):
        broken = deepcopy(labels); mutate(broken)
        with pytest.raises(ValueError):
            evaluate(evidence, broken)


def test_disputed_gold_is_not_a_definite_verdict_and_unknown_cost_is_not_zero():
    original = sample()
    original["calls"][0].update(usage_status="unknown", cost_cny=None, late=True)
    evidence, labels = bundle(original)
    labels["samples"][0]["annotation_status"] = "disputed"
    report = evaluate(evidence, labels)
    assert metrics(report)["disputed_samples"] == 1
    assert metrics(report)["determinate_agreement"]["rate"] is None
    assert report["calls"]["unknown_cost"] == report["calls"]["late"] == 1


def test_changed_gold_requires_reviewer_and_offset_timestamp():
    evidence, labels = bundle(sample("vision"))
    annotate(labels)
    labels["samples"][0]["reviewed_at"] = "2026-11-20T12:00:00"
    with pytest.raises(ValueError):
        evaluate(evidence, labels)


def test_same_image_and_same_context_cannot_leak_across_splits_under_new_names():
    for kind in ("vision", "equivalence"):
        evidence, labels = bundle(sample(kind), sample(kind, paper_key="paper-2"))
        labels["samples"][1].update(source_group_id="new-source", split="acceptance")
        with pytest.raises(ValueError, match="同源"):
            evaluate(evidence, labels)


def test_unverified_output_cannot_count_as_correct_and_model_versions_are_not_blended():
    evidence, labels = bundle(sample(verified_real_result=False), sample(model="another-model"))
    annotate(labels)
    report = evaluate(evidence, labels)
    assert len(report["groups"]) == 2
    assert report["groups"][0]["metrics"]["determinate_coverage"]["rate"] == 0
    assert report["groups"][1]["metrics"]["determinate_coverage"]["rate"] == 1


def test_cli_report_has_no_db_dependency_or_overwrite_and_hides_answer_errors(tmp_path, capsys):
    evidence, labels = bundle(sample())
    evidence_path, labels_path, output = (tmp_path / name for name in ("evidence.json", "labels.json", "report.json"))
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    labels_path.write_text(json.dumps(labels), encoding="utf-8")
    args = ["report", "--evidence", str(evidence_path), "--labels", str(labels_path), "--output", str(output)]
    assert main(args) == 0
    # A fresh offline report process must not construct a database engine.
    isolated_output = tmp_path / "isolated-report.json"
    env = {**os.environ, "DATABASE_URL": "invalid://no-database-available"}
    assert subprocess.run([sys.executable, "-m", "scripts.evaluate_stage6", *args[:-1], str(isolated_output)],
                          env=env, capture_output=True, timeout=20).returncode == 0
    before = output.read_bytes()
    assert main(args) == 2 and output.read_bytes() == before
    labels["samples"][0]["gold_verdict"] = "PRIVATE_INVALID_ANSWER"
    labels_path.write_text(json.dumps(labels), encoding="utf-8")
    assert main(args) == 2
    assert "PRIVATE_INVALID_ANSWER" not in capsys.readouterr().out


def test_export_contextual_tasks_keeps_frozen_input_and_is_select_only(context):
    from app.services import paper_equivalence
    db, _, paper, _, _ = context
    submit(context)
    paper_equivalence.schedule(db, paper.id, audit_request(paper_equivalence.preview(db, paper.id)))
    tasks = complete(db, paper)
    before = protected_fingerprints(db)
    evidence = export_evidence(db, [paper.id])
    assert protected_fingerprints(db) == before and not db.new and not db.dirty
    assert len(evidence["samples"]) == len(tasks) == 2
    assert all(s["answer_context"] and s["verified_real_result"] for s in evidence["samples"])
    serialized = json.dumps(evidence)
    assert "PRIVATE_STUDENT" not in serialized and "PRIVATE_SOLUTION" not in serialized
    assert "provider_config" not in serialized and "raw_response" not in serialized
    assert str(paper.student_id) not in serialized and paper.instance_code not in serialized
    assert not evaluate(evidence, label_template(evidence, "dev"))["groups"][0]["metrics"]["determinate_agreement"]["denominator"]


def test_export_vision_maps_shuffled_slots_and_never_copies_teacher_gold(vision_prepared):
    from app.services.assessment import now
    db, _, paper, _, _, _, _ = vision_prepared
    for task in db.scalars(select(VisionPageTask)):
        wire = deepcopy({k: task.result[k] for k in ("schema_version", "request_id", "page_token", "observed_identity", "answers")})
        wire["answers"].reverse()
        db.add(VisionPageCall(task_id=task.id, call_no=1, lease_token=uuid4(), status="succeeded",
            parsed_response=wire, usage={"synthetic": False, "usage_status": "known", "cost_cny": .01},
            started_at=now(), completed_at=now()))
    db.flush()
    # Audit comparison is protocol-object equality, including answer order: use
    # the exact saved wire order as the result while preserving frozen slot order.
    for task in db.scalars(select(VisionPageTask)):
        task.result = {**task.result, "answers": list(reversed(task.result["answers"]))}
    db.flush()
    before = protected_fingerprints(db)
    evidence = export_evidence(db, [paper.id])
    assert protected_fingerprints(db) == before and not db.new and not db.dirty
    row = evidence["samples"][0]
    assert row["verified_real_result"] and len(row["units"]) == 2
    assert [u["subquestion_no"] for u in row["units"]] == [1, 2]
    assert "observed_identity" not in json.dumps(row) and "image_data" not in row
    labels = label_template(evidence, "dev")
    assert all(u["answer"] is None and u["annotation_status"] == "pending" for u in labels["samples"][0]["units"])
