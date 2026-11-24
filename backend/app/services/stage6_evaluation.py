"""Pure, offline diagnostics of frozen evidence. No thresholds or grade writes."""
from collections import Counter, defaultdict
from decimal import Decimal
import re
import statistics

from app.schemas.stage6_evaluation import EvaluationLabels
from app.services.assessment_protocol import input_hash

NORMALIZATION_VERSION = "literal-whitespace-minus-v1"
DONE = {"labelled", "resolved"}


def literal_answer(value):
    # Do not simplify math, change case, apply NFKC (which flattens exponents),
    # turn fractions into decimals, or use the equivalence model as OCR gold.
    return re.sub(r"\s+", "", value.replace("−", "-"))


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "rate": numerator / denominator if denominator else None}


def _scope(sample):
    if sample["provider"] != "bailian":
        return "synthetic_or_unsupported_provider"
    expected = "stage6-vision-request-v2" if sample["kind"] == "vision" else "stage6-equivalence-request-v2"
    if sample["protocol"] != expected:
        return "legacy_protocol"
    if not sample["verified_real_result"] and sample["task_status"] in {"queued", "running"}:
        return "unfinished"
    if any(c["synthetic"] is True for c in sample["calls"]):
        return "synthetic_call"
    return None


def _vision(sample, label):
    gold = {u.unit_key: u for u in label.units}
    if set(gold) != {u["unit_key"] for u in sample["units"]}:
        raise ValueError("人工空位清单与冻结证据不一致")
    counts, details = Counter(), []
    correct_page = sample["verified_real_result"] and sample["identity_status"] == "verified" and label.identity_correct is True
    all_labelled = label.annotation_status in DONE and label.identity_correct is not None
    for unit in sample["units"]:
        truth = gold[unit["unit_key"]]
        counts["total_units"] += 1
        if truth.annotation_status not in DONE:
            counts[truth.annotation_status + "_units"] += 1
            all_labelled = False
            continue
        counts["labelled_units"] += 1
        actual = unit["output"] if sample["verified_real_result"] else None
        status_ok = bool(actual and label.identity_correct is True
                         and actual["recognition_status"] == truth.recognition_status)
        readable = truth.recognition_status == "readable"
        text_ok = bool(status_ok and (not readable or literal_answer(actual["answer"]) == literal_answer(truth.answer)))
        counts["state_correct"] += status_ok
        counts["readable_units"] += readable
        counts["transcribed_correct"] += readable and text_ok
        counts["missed_readable"] += readable and (not actual or actual["recognition_status"] != "readable")
        counts["blank_to_content"] += bool(truth.recognition_status == "blank" and actual and actual["recognition_status"] == "readable")
        counts["content_to_blank"] += bool(readable and actual and actual["recognition_status"] == "blank")
        correct_page &= text_ok and truth.recognition_status in {"readable", "blank"}
        details.append({"unit_key": unit["unit_key"], "status_matches": status_ok,
                        "transcription_matches": text_ok})
    return counts, {"sample_id": sample["sample_id"], "all_labelled": all_labelled,
        "complete_page": bool(all_labelled and correct_page), "units": details}


def _equivalence(sample, label):
    counts = Counter(total_samples=1)
    if label.annotation_status not in DONE:
        counts[label.annotation_status + "_samples"] += 1
        return counts
    if label.gold_verdict is None or not (label.gold_reason or "").strip():
        raise ValueError("已标注答案判断须填写人工结论及依据")
    counts["labelled_samples"] += 1
    gold = label.gold_verdict
    output = sample["output"] if sample["verified_real_result"] else None
    actual = output["verdict"] if output else None
    determinate = actual in {"equivalent", "not_equivalent"}
    counts["uncertain_outputs"] += actual == "uncertain"
    if gold != "uncertain":
        counts["gold_determinate"] += 1
        counts["determinate_outputs"] += determinate
        counts["determinate_agreements"] += determinate and gold == actual
        counts["false_accepts"] += gold == "not_equivalent" and actual == "equivalent"
        counts["false_rejects"] += gold == "equivalent" and actual == "not_equivalent"
    else:
        counts["gold_uncertain"] += 1
        counts["overconfident_outputs"] += determinate
        counts["appropriate_uncertain"] += actual == "uncertain"
    return counts


def _metrics(kind, counts, rows):
    values = dict(counts)
    if kind == "vision":
        values.update(transcription_accuracy=ratio(counts["transcribed_correct"], counts["readable_units"]),
            state_accuracy=ratio(counts["state_correct"], counts["labelled_units"]),
            readable_miss_rate=ratio(counts["missed_readable"], counts["readable_units"]),
            complete_page_rate=ratio(sum(r["complete_page"] for r in rows), sum(r["all_labelled"] for r in rows)))
    else:
        values.update(determinate_agreement=ratio(counts["determinate_agreements"], counts["determinate_outputs"]),
            determinate_coverage=ratio(counts["determinate_outputs"], counts["gold_determinate"]))
    return values


def evaluate(evidence, raw_labels):
    labels = EvaluationLabels.model_validate(raw_labels)
    if evidence.get("schema_version") != "stage6-eval-evidence-v1" or labels.evidence_sha256 != input_hash(evidence):
        raise ValueError("证据版本或哈希不一致，请使用同一次导出的标注模板")
    samples = {s["sample_id"]: s for s in evidence["samples"]}
    if len(samples) != len(evidence["samples"]) or set(samples) != {s.sample_id for s in labels.samples}:
        raise ValueError("证据与人工样本清单不一致；排除样本请使用include=false")
    groups, splits, exclusions, cards = {}, {}, Counter(), defaultdict(list)
    selected, details = [], []
    for label in labels.samples:
        sample = samples[label.sample_id]
        if not label.include:
            exclusions["not_selected"] += 1
            continue
        # Enforce source separation even if one side has no successful output.
        keys = [("source", label.source_group_id), ("paper", sample["paper_key"])]
        if label.answerer_group_id:
            keys.append(("answerer", label.answerer_group_id))
        if sample.get("image_sha256"):
            keys.append(("image", sample["image_sha256"]))
        if sample["kind"] == "equivalence":
            keys.append(("answer_input", input_hash({k: sample.get(k) for k in (
                "reference_answer", "student_answer", "answer_context")})))
        for key in keys:
            if key in splits and splits[key] != label.split:
                raise ValueError("开发与验收存在同源样本，请按来源组重新划分")
            splits[key] = label.split
        scope = _scope(sample)
        if scope:
            exclusions[scope] += 1
            continue
        if sample["kind"] == "equivalence" and label.units:
            raise ValueError("答案判断样本不应填写视觉空位标注")
        selected.append((sample, label))
        version = {k: sample.get(k) for k in ("kind", "provider", "model", "prompt_version", "policy_version", "protocol", "layout_version")}
        version["split"] = label.split
        key = input_hash(version)
        group = groups.setdefault(key, {"version": version, "counts": Counter(), "pages": [], "sample_count": 0})
        group["sample_count"] += 1
        group["counts"]["no_verified_result_count"] += not sample["verified_real_result"]
        group["counts"]["not_called_count"] += not bool(sample["calls"])
        if sample["kind"] == "vision":
            counts, row = _vision(sample, label)
            group["pages"].append(row)
            cards[(key, sample["card_key"])].append((sample, row))
            details.append(row)
        else:
            counts = _equivalence(sample, label)
            details.append({"sample_id": sample["sample_id"], "annotation_status": label.annotation_status})
        group["counts"].update(counts)
    for (key, _), pages in cards.items():
        counts = groups[key]["counts"]
        expected = {p[0]["total_pages"] for p in pages}
        numbers = [p[0]["page_no"] for p in pages]
        if len(expected) != 1 or len(set(numbers)) != len(numbers):
            counts["ambiguous_card_selection"] += 1
            continue
        complete_selection = set(numbers) == set(range(1, next(iter(expected)) + 1))
        if not complete_selection:
            counts["incomplete_card_selection"] += 1
        if not all(p[1]["all_labelled"] for p in pages):
            counts["unlabelled_cards"] += 1
            continue
        counts["labelled_cards"] += 1
        counts["complete_cards"] += complete_selection and all(p[1]["complete_page"] for p in pages)
    reports = []
    for group in groups.values():
        metrics = _metrics(group["version"]["kind"], group["counts"], group["pages"])
        if group["version"]["kind"] == "vision":
            metrics["complete_card_rate"] = ratio(group["counts"]["complete_cards"], group["counts"]["labelled_cards"])
        reports.append({"version": group["version"], "sample_count": group["sample_count"], "metrics": metrics})
    # Include retries, failures and late responses in recorded cost/latency.
    calls = {c["call_id"]: c for label in labels.samples if label.include
             for c in samples[label.sample_id]["calls"]
             if samples[label.sample_id]["provider"] == "bailian" and c["synthetic"] is not True}
    known = [c for c in calls.values() if c["usage_status"] == "known" and c["cost_cny"] is not None]
    seconds = [c["seconds"] for c in calls.values() if c["seconds"] is not None]
    return {"schema_version": "stage6-eval-report-v1", "dataset_version": labels.dataset_version,
        "evidence_sha256": labels.evidence_sha256, "normalization_version": NORMALIZATION_VERSION,
        "acceptance_status": "not_evaluated", "automatic_adoption_error_rate": None,
        "counts": {"exported_tasks": len(samples), "included_tasks": sum(s.include for s in labels.samples),
            "metric_tasks": len(selected), "source_groups": len({l.source_group_id for _, l in selected}),
            "answerer_groups": len({l.answerer_group_id for _, l in selected if l.answerer_group_id}),
            "images": len({s["image_sha256"] for s, _ in selected if s.get("image_sha256")}),
            "papers": len({s["paper_key"] for s, _ in selected}), "excluded": dict(exclusions)},
        "calls": {"scope": "included_real_provider_tasks", "count": len(calls),
            "failed": sum(c["status"] == "failed" for c in calls.values()),
            "late": sum(c["late"] for c in calls.values()),
            "unknown_cost": len(calls) - len(known),
            "known_estimated_cost_cny": float(sum((Decimal(str(c["cost_cny"])) for c in known), Decimal(0))),
            "median_seconds": statistics.median(seconds) if seconds else None},
        "groups": reports, "samples": details}
