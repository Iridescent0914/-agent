"""SELECT-only export. No photos, current grades, credentials or teacher gold."""
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select

from app.models import AssessmentCall, AssessmentTask, PaperInstance, VisionPageCall, VisionPageTask
from app.services.assessment_protocol import canonical_json, input_hash, parse_response
from app.services.vision_page_protocol import identity_check, parse_page_response


def _alias(prefix, value):
    return prefix + "-" + input_hash(str(value))[:20]


def _audit(task, calls, parsed, kind):
    rows, verified = [], False
    for call in calls:
        usage = call.usage or {}
        observed = call.parsed_response
        # Page task results contain local flags in addition to the wire response.
        if kind == "vision" and observed:
            observed = {k: observed.get(k) for k in (
                "schema_version", "request_id", "page_token", "observed_identity", "answers")}
        verified |= bool(parsed and call.status == "succeeded" and not call.late_response
                         and usage.get("synthetic") is False and observed == parsed)
        rows.append({"call_id": str(call.id), "status": call.status, "late": call.late_response,
            "synthetic": usage.get("synthetic"), "usage_status": usage.get("usage_status"),
            "cost_cny": usage.get("cost_cny"), "error_code": call.error_code,
            "seconds": max(0, (call.completed_at - call.started_at).total_seconds()) if call.completed_at else None})
    return rows, verified and task.provider == "bailian"


def export_evidence(db, paper_ids):
    """Freeze all attempts, including failed/repeated tasks, without re-extraction."""
    ids = set(paper_ids)
    found = set(db.scalars(select(PaperInstance.id).where(PaperInstance.id.in_(ids))))
    if not ids or ids != found:
        raise ValueError("试卷不存在或未指定试卷")
    samples = []
    for kind, model, call_model in (("vision", VisionPageTask, VisionPageCall),
                                    ("equivalence", AssessmentTask, AssessmentCall)):
        tasks = list(db.scalars(select(model).where(
            model.snapshot["paper_instance_id"].astext.in_([str(i) for i in ids]))
            .order_by(model.created_at, model.id)))
        calls_by_task = defaultdict(list)
        if tasks:
            for call in db.scalars(select(call_model).where(call_model.task_id.in_([t.id for t in tasks]))
                                   .order_by(call_model.call_no)):
                calls_by_task[call.task_id].append(call)
        for task in tasks:
            snapshot, request = task.snapshot, task.request_payload
            parsed, protocol_error = None, None
            try:
                if task.result:
                    if kind == "vision":
                        wire = {k: task.result[k] for k in (
                            "schema_version", "request_id", "page_token", "observed_identity", "answers")}
                        parsed = parse_page_response(canonical_json(wire), request)
                    else:
                        parsed = parse_response(canonical_json(task.result), request)
            except (ValueError, KeyError, TypeError):
                protocol_error = "MODEL_SCHEMA_INVALID"
            calls, verified = _audit(task, calls_by_task[task.id], parsed, kind)
            sample = {"sample_id": kind + "-" + str(task.id), "kind": kind, "task_id": str(task.id),
                "paper_key": _alias("paper", snapshot["paper_instance_id"]),
                "provider": task.provider, "model": task.model, "prompt_version": task.prompt_version,
                "policy_version": task.policy_version, "protocol": request.get("schema_version"),
                "task_status": task.status, "error_code": protocol_error or task.error_code,
                "task_result_sha256": input_hash(task.result), "request_sha256": input_hash(request),
                "verified_real_result": verified, "calls": calls}
            if kind == "vision":
                by_token = {a["slot_token"]: a for a in parsed["answers"]} if parsed else {}
                sample.update(card_key=_alias("card", snapshot["artifact_id"]), page_no=task.page_no,
                    total_pages=snapshot["expected_identity"]["total_pages"],
                    layout_version=snapshot["expected_identity"]["layout_version"],
                    image_sha256=snapshot["image_sha256"],
                    identity_status=identity_check(parsed, snapshot["expected_identity"])["status"] if parsed else None,
                    units=[{"unit_key": slot["slot_key"], **{k: slot[k] for k in (
                        "order_no", "subquestion_no", "blank_no")},
                        "output": {k: by_token[slot["slot_token"]][k] for k in ("recognition_status", "answer")}
                        if slot["slot_token"] in by_token else None} for slot in snapshot["slots"]])
            else:
                # Only the exact request the model saw, never current answers or grading.
                sample.update(order_no=snapshot["order_no"], item_id=snapshot["item_id"],
                    reference_answer=request["reference_answer"], student_answer=request["student_answer"],
                    answer_context=request.get("answer_context"),
                    output={k: parsed[k] for k in ("verdict", "reason_code", "explanation")} if parsed else None)
            samples.append(sample)
    return {"schema_version": "stage6-eval-evidence-v1", "exported_at": datetime.now(timezone.utc).isoformat(),
            "selection": "all_existing_tasks_for_explicit_papers", "samples": samples}


def label_template(evidence, dataset_version):
    return {"schema_version": "stage6-eval-labels-v1", "evidence_sha256": input_hash(evidence),
        "dataset_version": dataset_version, "samples": [{
            "sample_id": s["sample_id"], "include": True, "source_group_id": s["paper_key"],
            "answerer_group_id": None, "split": "development", "capture_tags": [],
            "reviewer_alias": None, "reviewed_at": None, "annotation_status": "pending",
            "gold_verdict": None, "gold_reason": None, "identity_correct": None,
            "units": [{"unit_key": u["unit_key"], "annotation_status": "pending",
                       "recognition_status": None, "answer": None} for u in s.get("units", [])],
        } for s in evidence["samples"]]}
