from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    AssessmentCall, AssessmentInput, AssessmentTask, Attempt, ModelQualityFeedback,
    ModelQualityFeedbackRevision, PaperInstance, PaperInstanceItem, Question,
    RecognitionItem, RecognitionRun,
    Student,
    Submission,
)
from app.schemas.assessment import (
    CreateAssessmentTask, EquivalenceGold, ContextualEquivalenceRequest, QualityFeedbackIn,
    RecognitionGold,
)
from app.services.assessment_protocol import POLICY_VERSION, canonical_json, input_hash
from app.services.print_view import answer_subquestions, print_source_module
from app.services.submission_workflow import _grading_reference, _initial_grading_reference
from app.services.equivalence_candidates import classify_unit
from app.services.equivalence_bailian import configuration
from app.services.answer_context import build_context, ContextUnavailable


class AssessmentProblem(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409, *, task_id=None, retryable=False):
        super().__init__(message)
        self.code, self.status_code = code, status_code
        self.task_id, self.retryable = task_id, retryable


def now():
    return datetime.now(timezone.utc)


def enabled():
    if not get_settings().stage6_enabled or get_settings().app_env not in {"development", "test"}:
        raise AssessmentProblem("MODEL_DISABLED", "阶段六内部建议功能尚未启用", 403)


def _paper_item(db, item_id, *, active=True):
    item = db.get(PaperInstanceItem, item_id)
    if item is None:
        raise AssessmentProblem("NOT_FOUND", "试卷题项不存在", 404)
    # All writers in this service serialize on paper before input/task/feedback.
    paper = db.scalar(select(PaperInstance).where(PaperInstance.id == item.paper_instance_id).with_for_update().execution_options(populate_existing=True))
    if active and (paper.status != "published" or paper.invalidated_at is not None):
        raise AssessmentProblem("INPUT_STALE", "试卷未发布或已经失效")
    student = db.get(Student, paper.student_id)
    if active and (student is None or student.archived_at is not None):
        raise AssessmentProblem("INPUT_STALE", "学生已停用或归属不可用")
    return paper, item


def _units(answer, question_type):
    rows = answer_subquestions(answer, question_type)
    return {blank.item_id: (row.subquestion_no, blank.blank_no) for row in rows for blank in row.blanks}


def _leaf(answer, item_id):
    if isinstance(answer, dict) and answer.get("kind") == "group":
        parts = answer.get("items")
        if not isinstance(parts, list):
            raise AssessmentProblem("REFERENCE_STRUCTURE_CHANGED", "作答单元结构无效")
        matched = [p for p in parts if isinstance(p, dict) and p.get("item_id") == item_id]
        if len(matched) != 1:
            raise AssessmentProblem("REFERENCE_STRUCTURE_CHANGED", "作答单元缺失或重复")
        return matched[0].get("answer")
    if item_id != "answer":
        raise AssessmentProblem("SLOT_SET_MISMATCH", "作答单元不属于本题")
    return answer


def _text(value):
    if type(value) not in (str, int, float, bool) or not str(value).strip():
        raise AssessmentProblem("INVALID_ANSWER", "仅支持非空的单个答案文本", 422)
    result = str(value)
    if len(result) > 16000:
        raise AssessmentProblem("INVALID_ANSWER", "答案文本超出长度限制", 422)
    return result


def create_task(db: Session, request: CreateAssessmentTask, *, rerun_of=None, reason=None, preview_only=False):
    enabled()
    paper, item = _paper_item(db, request.paper_instance_item_id)
    question = db.get(Question, item.question_id)
    if (question is None or question.archived_at is not None
            or question.grading_readiness != "machine_ready" or question.source_review_status != "reviewed"):
        raise AssessmentProblem("REFERENCE_NOT_REVIEWED", "仅允许使用已审核且可机判的正式题目")
    question_type = item.question_snapshot.get("question_type")
    if question_type not in {"choice", "fill_blank", "true_false"}:
        raise AssessmentProblem("UNSUPPORTED_QUESTION", "当前仅支持日常短答案题型", 422)
    current_input = db.scalar(select(AssessmentInput).where(
        AssessmentInput.paper_instance_item_id == item.id, AssessmentInput.item_id == request.item_id,
    ).execution_options(populate_existing=True))
    attempt = db.scalar(select(Attempt).where(Attempt.paper_instance_item_id == item.id).with_for_update().execution_options(populate_existing=True))
    if attempt is not None:
        if request.expected_attempt_revision != attempt.revision_no:
            raise AssessmentProblem("REVISION_CONFLICT", "作答修订号已改变，请重新读取")
        if request.student_answer is not None:
            raise AssessmentProblem("INVALID_ANSWER", "已提交作答必须从服务端读取答案", 422)
        reference, rule, source = _grading_reference(item, attempt)
        student_answer = _leaf(attempt.student_answer, request.item_id)
    else:
        if request.expected_attempt_revision is not None:
            raise AssessmentProblem("REVISION_CONFLICT", "尚无对应的正式作答")
        student_answer = request.student_answer
        if current_input and current_input.snapshot.get("attempt_id") is None:
            # Preserve the reference frozen for this draft until explicit rebind in S2.
            frozen = current_input.snapshot
            reference, rule, source = frozen["reference_answer"], frozen["reference_rule"], frozen["reference_source"]
        else:
            reference, rule, source = _initial_grading_reference(item, question)
    try:
        coordinates = _units(item.answer_snapshot, question_type)
        if coordinates != _units(reference, question_type):
            raise ValueError("reference unit set changed")
    except ValueError as exc:
        raise AssessmentProblem("REFERENCE_STRUCTURE_CHANGED", "打印空位与有效标答结构不一致") from exc
    if request.item_id not in coordinates:
        raise AssessmentProblem("SLOT_SET_MISMATCH", "作答单元不属于该试卷题项", 422)
    reference_text = _text(_leaf(reference, request.item_id))
    submission = db.get(Submission, attempt.submission_id) if attempt else None
    image_input = submission is not None and submission.source == "image"
    confirmed = not image_input or bool((attempt.grading_details or {}).get("recognition_reviewed"))
    candidate = classify_unit(
        question_type=question_type, reference=reference, rule=rule, item_id=request.item_id,
        student_answer=student_answer, input_confirmed=confirmed,
        teacher_confirmed=attempt is not None and attempt.grade_status == "teacher_confirmed",
    )
    context_binding = None
    if candidate["eligible"]:
        try:
            context_binding = build_context(item, coordinates, request.item_id)
        except ContextUnavailable as exc:
            candidate.update(eligible=False, reason_code=exc.code, message=str(exc))
    if preview_only:
        return {
            **candidate, "paper_instance_item_id": item.id, "item_id": request.item_id,
            "reference_answer": reference_text, "student_answer": student_answer,
            "reference_source": source,
            "input_revision": current_input.revision_no if current_input else 0,
            "attempt_revision_no": attempt.revision_no if attempt else None,
            "subquestion_no": coordinates[request.item_id][0], "blank_no": coordinates[request.item_id][1],
            "answer_context": context_binding["answer_context"] if context_binding else None,
        }
    if not candidate["eligible"]:
        raise AssessmentProblem("NOT_ELIGIBLE", candidate["message"], 422)
    try:
        provider_config = configuration()
    except ValueError as exc:
        raise AssessmentProblem("CONFIG_INCOMPLETE", "请检查阶段六供应商、模型、北京地域接口地址和API Key配置", 422) from exc
    student_answer = _text(student_answer)
    snapshot = {
        "hash_version": "stage6-input-hash-v2",
        "paper_instance_id": str(paper.id), "version_no": paper.version_no,
        "student_id": str(paper.student_id), "paper_instance_item_id": str(item.id),
        "item_id": request.item_id, "order_no": item.order_no,
        "subquestion_no": coordinates[request.item_id][0], "blank_no": coordinates[request.item_id][1],
        "source_module": print_source_module(item.selection_audit), "layout_version": "answer-card-v2",
        "attempt_id": str(attempt.id) if attempt else None,
        "attempt_revision_no": attempt.revision_no if attempt else None,
        "student_answer": student_answer, "input_sha256": input_hash({"student_answer": student_answer}),
        "reference_source": source, "reference_answer": reference, "reference_rule": rule,
        "reference_sha256": input_hash({"answer": reference, "rule": rule, "source": source}),
        "published_structure": coordinates,
        "candidate_routing": candidate,
        "context_binding": context_binding,
    }
    # JSON roundtrip normalizes tuple coordinates before comparison with JSONB.
    snapshot = json.loads(canonical_json(snapshot))
    unchanged = current_input is not None and current_input.snapshot == snapshot
    revision = current_input.revision_no if current_input else 0
    if (not unchanged or rerun_of is not None) and request.expected_input_revision != revision:
        raise AssessmentProblem("REVISION_CONFLICT", "内部输入版本已改变，请重新读取")
    if not unchanged:
        if current_input is None:
            current_input = AssessmentInput(paper_instance_item_id=item.id, item_id=request.item_id, revision_no=1, snapshot=snapshot)
            db.add(current_input)
        else:
            current_input.revision_no += 1
            current_input.snapshot = snapshot
        db.flush()
        # Preserve all former outputs; invalidate suggestions for older input.
        for old in db.scalars(select(AssessmentTask).where(
            AssessmentTask.input_id == current_input.id,
            AssessmentTask.status.in_(("queued", "running", "succeeded", "failed")),
        ).with_for_update()):
            old.status, old.error_code = "stale", "INPUT_STALE"
            _abandon_call(db, old, "INPUT_STALE")
            old.lease_token, old.lease_until = None, None
    dedup = input_hash({
        "kind": "equivalence", "input_id": str(current_input.id), "revision": current_input.revision_no,
        "snapshot": snapshot, "configuration": provider_config,
        "policy": POLICY_VERSION, "protocol": "stage6-equivalence-request-v2",
        "rerun": str(uuid4()) if rerun_of else None,
    })
    existing = db.scalar(select(AssessmentTask).where(AssessmentTask.dedup_key == dedup))
    if existing:
        return existing, True
    settings = get_settings()
    payload = ContextualEquivalenceRequest(
        schema_version="stage6-equivalence-request-v2", request_id=str(uuid4()),
        reference_answer=reference_text, student_answer=student_answer,
        answer_context=context_binding["answer_context"],
    ).model_dump()
    task = AssessmentTask(
        input_id=current_input.id, input_revision=current_input.revision_no,
        dedup_key=dedup, snapshot=deepcopy(snapshot), request_payload=payload,
        provider=provider_config["provider"], model=provider_config["model"],
        prompt_version=provider_config["prompt_version"], policy_version=POLICY_VERSION,
        max_attempts=settings.stage6_max_attempts, timeout_seconds=settings.stage6_timeout_seconds,
        lease_seconds=settings.stage6_lease_seconds, rerun_of=rerun_of, rerun_reason=reason,
        action_history=[{"action": "rerun" if rerun_of else "create", "reason": reason, "at": now().isoformat(),
                         "provider_config": provider_config}],
    )
    db.add(task)
    db.flush()
    return task, False


def binding_error(db, task):
    """Recheck durable ownership and current input, never today's newest question key."""
    frozen = task.snapshot
    if task.policy_version != POLICY_VERSION or not frozen.get("candidate_routing", {}).get("eligible"):
        return "INPUT_STALE"
    paper, item = _paper_item(db, UUID(frozen["paper_instance_item_id"]), active=False)
    student = db.get(Student, paper.student_id)
    if (paper.status != "published" or paper.invalidated_at is not None
            or str(paper.id) != frozen["paper_instance_id"] or paper.version_no != frozen["version_no"]
            or str(paper.student_id) != frozen["student_id"] or student is None or student.archived_at is not None):
        return "INPUT_STALE"
    current = db.get(AssessmentInput, task.input_id, populate_existing=True)
    if current.revision_no != task.input_revision or current.snapshot != frozen:
        return "INPUT_STALE"
    attempt = db.scalar(select(Attempt).where(Attempt.paper_instance_item_id == item.id).with_for_update().execution_options(populate_existing=True))
    if (str(attempt.id) if attempt else None) != frozen["attempt_id"]:
        return "INPUT_STALE"
    if attempt:
        if attempt.revision_no != frozen["attempt_revision_no"]:
            return "INPUT_STALE"
        answer, rule, source = _grading_reference(item, attempt)
        if input_hash({"answer": answer, "rule": rule, "source": source}) != frozen["reference_sha256"]:
            return "INPUT_STALE"
        try:
            if _text(_leaf(attempt.student_answer, frozen["item_id"])) != frozen["student_answer"]:
                return "INPUT_STALE"
        except AssessmentProblem:
            return "INPUT_STALE"
        submission = db.get(Submission, attempt.submission_id)
        if (attempt.grade_status == "teacher_confirmed"
                or (submission.source == "image" and not (attempt.grading_details or {}).get("recognition_reviewed"))):
            return "INPUT_STALE"
    try:
        if json.loads(canonical_json(_units(item.answer_snapshot, item.question_snapshot.get("question_type")))) != frozen["published_structure"]:
            return "REFERENCE_STRUCTURE_CHANGED"
        context = build_context(item, _units(item.answer_snapshot, item.question_snapshot.get("question_type")), frozen["item_id"])
        if (context != frozen.get("context_binding")
                or task.request_payload.get("schema_version") != "stage6-equivalence-request-v2"
                or task.request_payload.get("answer_context") != context["answer_context"]):
            return "INPUT_STALE"
    except ValueError:
        return "REFERENCE_STRUCTURE_CHANGED"
    return None


def _abandon_call(db, task, error):
    if task.lease_token:
        call = db.scalar(select(AssessmentCall).where(AssessmentCall.lease_token == task.lease_token))
        if call and call.status == "running":
            call.status, call.error_code, call.completed_at = "abandoned", error, now()


def get_task(db, task_id, *, revalidate=True):
    task = db.get(AssessmentTask, task_id)
    if task is None:
        raise AssessmentProblem("NOT_FOUND", "模型任务不存在", 404)
    if revalidate and task.status not in {"cancelled", "stale"}:
        # Acquire paper first, then refresh task under its own lock.
        _paper_item(db, UUID(task.snapshot["paper_instance_item_id"]), active=False)
        task = db.scalar(select(AssessmentTask).where(AssessmentTask.id == task_id).with_for_update().execution_options(populate_existing=True))
        error = binding_error(db, task)
        if error:
            task.status, task.error_code = "stale", error
            _abandon_call(db, task, error)
            task.lease_token, task.lease_until = None, None
    return task


def task_view(db, task):
    calls = db.scalars(select(AssessmentCall).where(AssessmentCall.task_id == task.id).order_by(AssessmentCall.call_no))
    return {
        "id": task.id, "kind": task.kind, "status": task.status,
        "input_revision": task.input_revision, "snapshot": task.snapshot,
        "provider": task.provider, "model": task.model, "prompt_version": task.prompt_version,
        "policy_version": task.policy_version, "request_payload": task.request_payload,
        "result": task.result, "error_code": task.error_code,
        "attempt_count": task.attempt_count, "max_attempts": task.max_attempts,
        "retryable": task.status == "failed" and task.attempt_count < task.max_attempts
                     and task.error_code in {"MODEL_TIMEOUT", "MODEL_PROVIDER_FAILED", "MODEL_SCHEMA_INVALID", "LEASE_EXPIRED"},
        "rerun_of": task.rerun_of, "rerun_reason": task.rerun_reason,
        "action_history": task.action_history,
        "created_at": task.created_at, "completed_at": task.completed_at,
        "adoption_status": "teacher_adopted" if any(e.get("action") == "teacher_adopt" for e in task.action_history) else "not_adopted",
        "synthetic": task.provider == "fake",
        "candidate_routing": task.snapshot.get("candidate_routing"),
        "judgment_mode": "contextual" if task.request_payload.get("schema_version") == "stage6-equivalence-request-v2" else "answer_pair_legacy",
        "calls": [{"id": c.id, "call_no": c.call_no, "status": c.status,
                   "raw_response": c.raw_response, "parsed_response": c.parsed_response,
                   "error_code": c.error_code, "usage": c.usage, "late_response": c.late_response,
                   "started_at": c.started_at, "completed_at": c.completed_at} for c in calls],
    }


def task_action(db, task_id, action, reason):
    enabled()
    task = get_task(db, task_id)
    if action == "cancel":
        if task.status not in {"queued", "running"}:
            raise AssessmentProblem("REVISION_CONFLICT", "只能取消尚未结束的任务")
        task.status, task.error_code = "cancelled", "CANCELLED"
        _abandon_call(db, task, "CANCELLED")
        task.lease_token, task.lease_until = None, None
        task.completed_at = now()
    elif action == "retry":
        if task.status != "failed" or task.error_code not in {"MODEL_TIMEOUT", "MODEL_PROVIDER_FAILED", "MODEL_SCHEMA_INVALID", "LEASE_EXPIRED"}:
            raise AssessmentProblem("REVISION_CONFLICT", "当前任务不可重试")
        if task.attempt_count >= task.max_attempts:
            raise AssessmentProblem("BUDGET_EXHAUSTED", "已达到本任务调用次数上限")
        task.status, task.error_code, task.completed_at = "queued", None, None
    elif action == "rerun":
        if task.status not in {"succeeded", "failed", "cancelled"} or binding_error(db, task):
            raise AssessmentProblem("REVISION_CONFLICT", "只能重跑当前有效且已结束的任务")
        attempt_revision = task.snapshot["attempt_revision_no"]
        task, _ = create_task(db, CreateAssessmentTask(
            paper_instance_item_id=UUID(task.snapshot["paper_instance_item_id"]), item_id=task.snapshot["item_id"],
            student_answer=None if attempt_revision else task.snapshot["student_answer"],
            expected_attempt_revision=attempt_revision, expected_input_revision=task.input_revision,
        ), rerun_of=task.id, reason=reason)
    if action != "rerun":
        task.action_history = [*task.action_history, {"action": action, "reason": reason, "at": now().isoformat()}]
    db.flush()
    return task


def _feedback_target(db, target_kind, target_id, item_id):
    if target_kind == "equivalence":
        task = get_task(db, target_id)
        if task.snapshot["item_id"] != item_id:
            raise AssessmentProblem("SLOT_SET_MISMATCH", "反馈单元与任务不一致", 422)
        if not task.result:
            raise AssessmentProblem("MODEL_SCHEMA_INVALID", "没有可供核对的合法模型结果")
        evidence = {"kind": "equivalence", "task_snapshot": task.snapshot, "result": task.result,
                    "provider": task.provider, "model": task.model, "synthetic": task.provider == "fake"}
        return UUID(task.snapshot["paper_instance_item_id"]), evidence, task.status == "stale"
    row = db.get(RecognitionItem, target_id)
    if row is None:
        raise AssessmentProblem("NOT_FOUND", "识别结果不存在", 404)
    # Existing recognition workflow locks run before paper; follow that order.
    run = db.scalar(select(RecognitionRun).where(RecognitionRun.id == row.recognition_run_id).with_for_update())
    if run.status not in {"ready_for_review", "committed"}:
        raise AssessmentProblem("INPUT_STALE", "识别结果尚未稳定或正在重新提取")
    row = db.scalar(select(RecognitionItem).where(RecognitionItem.id == target_id).execution_options(populate_existing=True))
    if row is None:
        raise AssessmentProblem("INPUT_STALE", "识别结果已被重新提取，请重新读取")
    _paper_item(db, row.paper_instance_item_id, active=False)
    item = db.get(PaperInstanceItem, row.paper_instance_item_id)
    # Fail closed for legacy multi-unit responses that lack per-unit evidence.
    try:
        coordinates = _units(item.answer_snapshot, item.question_snapshot.get("question_type"))
        if item_id not in coordinates:
            raise ValueError("unknown unit")
        if len(coordinates) == 1 and row.recognition_status != "readable":
            original = {"recognition_status": row.recognition_status, "answer": None}
        else:
            original = {"recognition_status": row.recognition_status, "answer": _leaf(row.recognized_answer, item_id)}
        original = RecognitionGold.model_validate(original).model_dump()
    except (ValueError, AssessmentProblem) as exc:
        raise AssessmentProblem("SLOT_SET_MISMATCH", "旧识别结果缺少该空的可靠证据，请更新识别结果后核对") from exc
    evidence = {"kind": "recognition", "run_id": str(run.id), "image_sha256": run.image_sha256,
                "result": original, "provider": run.provider, "model": run.model, "prompt_version": run.prompt_version,
                "provider_payload": row.provider_payload, "synthetic": run.provider == "fake", "legacy_run_status": run.status,
                "usage": {"usage_status": "unknown"}}
    return row.paper_instance_item_id, evidence, False


def quality_feedback(db, target_kind, target_id, item_id, request: QualityFeedbackIn | None = None):
    key = f"{target_kind}:{target_id}:{item_id}"
    previous = db.scalar(select(ModelQualityFeedback).where(ModelQualityFeedback.target_key == key))
    if previous is not None and target_kind == "recognition":
        # Its original RecognitionItem may already have been replaced. Feedback
        # always refers to the frozen evidence, never a later recognition pass.
        run = db.scalar(select(RecognitionRun).where(RecognitionRun.id == UUID(previous.evidence["run_id"])).with_for_update())
        _paper_item(db, previous.paper_instance_item_id, active=False)
        paper_item_id, evidence = previous.paper_instance_item_id, previous.evidence
        stale = db.get(RecognitionItem, target_id) is None or run is None or run.status not in {"ready_for_review", "committed"}
    else:
        paper_item_id, evidence, stale = _feedback_target(db, target_kind, target_id, item_id)
    row = db.scalar(select(ModelQualityFeedback).where(ModelQualityFeedback.target_key == key).with_for_update().execution_options(populate_existing=True))
    if request:
        enabled()
        # Stale evidence can be reviewed for its ORIGINAL input, not adopted.
        revision = row.revision_no if row else 0
        if request.expected_revision != revision:
            raise AssessmentProblem("REVISION_CONFLICT", "质量反馈已被修改，请重新读取")
        expected_gold = EquivalenceGold if target_kind == "equivalence" else RecognitionGold
        if request.gold is not None and not isinstance(request.gold, expected_gold):
            raise AssessmentProblem("INVALID_FEEDBACK", "人工正确结果类型与模型任务不一致", 422)
        if target_kind == "equivalence" and request.error_type in {"transcription_error", "slot_mismatch", "blank_confusion"}:
            raise AssessmentProblem("INVALID_FEEDBACK", "等价任务不能标记为识别错误", 422)
        if target_kind == "recognition" and request.error_type == "equivalence_error":
            raise AssessmentProblem("INVALID_FEEDBACK", "识别任务不能标记为等价错误", 422)
        result = (row.evidence if row else evidence)["result"]
        gold = request.gold.model_dump() if request.gold else None
        original_gold = {"verdict": result["verdict"]} if target_kind == "equivalence" else result
        if request.review_status == "incorrect" and gold == original_gold:
            raise AssessmentProblem("INVALID_FEEDBACK", "确认错误时人工正确结果必须与模型结果不同", 422)
        if request.review_status == "correct" and gold is not None and gold != original_gold:
            raise AssessmentProblem("INVALID_FEEDBACK", "确认正确时人工结果须与模型结果一致", 422)
        data = request.model_dump(exclude={"expected_revision"})
        data.update(is_error={"correct": False, "incorrect": True, "uncertain": None}[request.review_status],
                    reviewed_at=now().isoformat(), adoption_status="not_adopted")
        if row is None:
            row = ModelQualityFeedback(
                target_key=key, task_id=target_id if target_kind == "equivalence" else None,
                recognition_item_id=target_id if target_kind == "recognition" else None,
                paper_instance_item_id=paper_item_id, item_id=item_id,
                revision_no=1, evidence=deepcopy(evidence), current=data,
            )
            db.add(row)
        else:
            row.revision_no += 1
            row.current = data
        db.flush()
        db.add(ModelQualityFeedbackRevision(feedback_id=row.id, revision_no=row.revision_no, data=deepcopy(data)))
        db.flush()
    history = [] if row is None else list(db.scalars(select(ModelQualityFeedbackRevision).where(
        ModelQualityFeedbackRevision.feedback_id == row.id,
    ).order_by(ModelQualityFeedbackRevision.revision_no)))
    return {"target_kind": target_kind, "target_id": target_id, "paper_instance_item_id": paper_item_id,
            "item_id": item_id, "revision_no": row.revision_no if row else 0,
            "evidence": row.evidence if row else evidence, "input_stale": stale,
            "current": row.current if row else {"review_status": "unreviewed", "is_error": None, "adoption_status": "not_adopted"},
            "history": [{"revision_no": h.revision_no, **h.data} for h in history]}
