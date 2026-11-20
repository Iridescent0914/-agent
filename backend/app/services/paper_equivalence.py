"""Whole-paper scheduling; only explicit teacher adoption writes grading evidence."""
from collections import defaultdict
from uuid import UUID

from sqlalchemy import select

from app.models import AssessmentCall, AssessmentTask, Attempt, ModelQualityFeedback, PaperInstance, Student, Submission
from app.schemas.assessment import CreateAssessmentTask
from app.services import assessment, submission_workflow as grading
from app.services.assessment_protocol import POLICY_VERSION, canonical_json, input_hash, parse_response


def _problem(code, message, status=409):
    raise assessment.AssessmentProblem(code, message, status)


def _context(db, paper_id, *, writing=False):
    assessment.enabled()
    initial = db.get(PaperInstance, paper_id)
    if initial is None:
        _problem("NOT_FOUND", "试卷不存在", 404)
    # Same order as grading writers: student, then paper, then attempts/tasks.
    if writing:
        db.scalar(select(Student).where(Student.id == initial.student_id).with_for_update())
    paper = db.scalar(select(PaperInstance).where(PaperInstance.id == paper_id)
                      .with_for_update().execution_options(populate_existing=True))
    student = db.get(Student, paper.student_id, populate_existing=True)
    if (paper.status != "published" or paper.invalidated_at is not None
            or student is None or student.archived_at is not None):
        _problem("INPUT_STALE", "试卷或学生已失效，不能发起或采用整卷建议")
    submission = db.scalar(select(Submission).where(Submission.paper_instance_id == paper.id))
    if submission is None:
        _problem("SUBMISSION_REQUIRED", "请先核对并录入整卷答案，再进行等价判断", 422)
    attempts = {a.paper_instance_item_id: a for a in db.scalars(select(Attempt).where(
        Attempt.submission_id == submission.id).order_by(Attempt.id).with_for_update()
        .execution_options(populate_existing=True))}
    return paper, submission, attempts


def _result_blocker(db, task):
    def blocked(code, message):
        return {"code": code, "message": message}

    if task.provider != "bailian":
        return blocked("synthetic_result", "模拟结果不能用于正式评分")
    if task.status in {"queued", "running"}:
        return blocked("task_pending", "等价判断尚未完成，请等待或刷新整卷")
    if task.status != "succeeded":
        return blocked("task_unavailable", "任务未成功完成，请查看本题调用记录")
    if not task.result:
        return blocked("result_missing", "尚无可采用的判断结果，请查看本题调用记录")
    try:
        parsed = parse_response(canonical_json(task.result), task.request_payload)
    except (ValueError, TypeError, KeyError):
        return blocked("result_invalid", "判断结果格式校验未通过，请查看本题调用记录")
    if parsed["verdict"] == "uncertain":
        if parsed["reason_code"] == "reference_issue":
            return blocked("reference_issue", "模型提示标答与题干可能存在矛盾，请核对参考答案并人工复核本题")
        if parsed["reason_code"] == "context_incomplete":
            return blocked("context_incomplete", "模型提示题干、题图或公式信息不完整，请人工复核本题")
        return blocked("model_uncertain", "模型返回“无法确定”，请使用本题原有人工复核")
    feedback = db.scalar(select(ModelQualityFeedback).where(ModelQualityFeedback.task_id == task.id))
    if feedback and feedback.current.get("review_status") in {"incorrect", "uncertain"}:
        return blocked("quality_review_blocked", "该建议的质量核对已标记为错误或无法确定，请人工复核本题")
    # A fake result or late response cannot become grading evidence.
    verified = any(c.status == "succeeded" and not c.late_response
               and c.usage.get("synthetic") is False and c.parsed_response == task.result
               for c in db.scalars(select(AssessmentCall).where(AssessmentCall.task_id == task.id)))
    if not verified:
        return blocked("result_unverified", "建议尚未通过调用核验，请刷新或查看本题调用记录")
    return None


def _real_result(db, task):
    return _result_blocker(db, task) is None


def preview(db, paper_id):
    paper, submission, attempts = _context(db, paper_id)
    tasks = list(db.scalars(select(AssessmentTask).where(
        AssessmentTask.snapshot["paper_instance_id"].astext == str(paper.id))
        .order_by(AssessmentTask.created_at.desc(), AssessmentTask.id.desc())))
    by_unit = defaultdict(list)
    for task in tasks:
        by_unit[(task.snapshot["paper_instance_item_id"], task.snapshot["item_id"])].append(task)
    units, bindings = [], []
    for item in grading._items(db, paper.id):
        attempt = attempts.get(item.id)
        if attempt is None:
            _problem("SUBMISSION_INCOMPLETE", "整卷作答记录不完整，请检查录入结果")
        reference, rule, source = grading._grading_reference(item, attempt)
        bindings.append({"item": str(item.id), "attempt": str(attempt.id), "revision": attempt.revision_no,
            "answer": attempt.student_answer, "reference": reference, "rule": rule, "source": source,
            "details": attempt.grading_details, "status": attempt.grade_status, "correct": attempt.is_correct,
            "published_question_text": item.question_snapshot.get("question_text"),
            "published_assets": item.question_snapshot.get("assets") or [],
            "published_answer": item.answer_snapshot, "question_type": item.question_snapshot.get("question_type")})
        try:
            coordinates = assessment._units(item.answer_snapshot, item.question_snapshot.get("question_type"))
        except (ValueError, TypeError, KeyError):
            _problem("REFERENCE_STRUCTURE_CHANGED", "试卷空位结构无效，请人工核对")
        previous = (attempt.grading_details or {}).get("equivalence_review", {})
        adopted = set(previous.get("item_ids", [])) if previous.get("applied_revision_no") == attempt.revision_no else set()
        for item_id, (sub, blank) in coordinates.items():
            request = CreateAssessmentTask(paper_instance_item_id=item.id, item_id=item_id,
                                           expected_attempt_revision=attempt.revision_no)
            try:
                candidate = assessment.create_task(db, request, preview_only=True)
            except assessment.AssessmentProblem as exc:
                candidate = {"eligible": False, "reason_code": exc.code, "message": str(exc), "input_revision": 0}
            if item_id in adopted:
                candidate.update(eligible=False, reason_code="teacher_adopted", message="本空建议已经教师采用")
            current = next((t for t in by_unit[(str(item.id), item_id)]
                            if t.status not in {"stale"} and assessment.binding_error(db, t) is None), None)
            blocker = _result_blocker(db, current) if current else None
            summary = None if current is None else {"id": str(current.id), "status": current.status,
                "synthetic": current.provider == "fake", "result": current.result,
                "error_code": current.error_code, "adoptable": bool(candidate["eligible"] and blocker is None),
                "adoption_blocker": blocker}
            units.append({**candidate, "paper_instance_item_id": str(item.id), "item_id": item_id,
                "order_no": item.order_no, "subquestion_no": sub, "blank_no": blank,
                "attempt_revision_no": attempt.revision_no, "task": summary})
    digest = input_hash({"paper": str(paper.id), "version": paper.version_no, "policy": POLICY_VERSION,
                         "submission": str(submission.id), "attempts": bindings,
                         "routing": [(u["paper_instance_item_id"], u["item_id"], u["reason_code"]) for u in units]})
    return {"paper_instance_id": str(paper.id), "input_sha256": digest, "units": units,
        "candidate_count": sum(u["eligible"] for u in units),
        "new_task_count": sum(u["eligible"] and u["task"] is None for u in units)}


def _check_input(report, request):
    if report["input_sha256"] != request.expected_input_sha256:
        _problem("INPUT_STALE", "作答、判分或标答依据已变化，请重新检查整卷")


def schedule(db, paper_id, request):
    report = preview(db, paper_id)
    _check_input(report, request)
    created, reused = 0, 0
    # No provider calls here. Existing worker/budget/timeout rules own dispatch.
    for unit in report["units"]:
        if not unit["eligible"]:
            continue
        if unit["task"]:
            reused += 1
            continue
        task, existed = assessment.create_task(db, CreateAssessmentTask(
            paper_instance_item_id=UUID(unit["paper_instance_item_id"]), item_id=unit["item_id"],
            expected_attempt_revision=unit["attempt_revision_no"], expected_input_revision=unit["input_revision"]))
        if not existed:
            task.action_history = [*task.action_history, {"action": "paper_schedule", "at": assessment.now().isoformat(),
                "reviewer": request.reviewer, "reason": request.reason, "input_sha256": report["input_sha256"]}]
        created += not existed
        reused += existed
    db.flush()
    return {**preview(db, paper_id), "created_count": created, "reused_count": reused}


def adopt(db, paper_id, request):
    paper, _, attempts = _context(db, paper_id, writing=True)
    tasks = list(db.scalars(select(AssessmentTask).where(AssessmentTask.id.in_(request.task_ids))
                           .order_by(AssessmentTask.id).with_for_update().execution_options(populate_existing=True)))
    if len(tasks) != len(request.task_ids) or any(t.snapshot["paper_instance_id"] != str(paper.id) for t in tasks):
        _problem("TASK_SCOPE_MISMATCH", "建议记录不属于当前试卷", 422)
    receipt = input_hash({"input": request.expected_input_sha256, "tasks": sorted(str(t.id) for t in tasks),
                          "reviewer": request.reviewer, "reason": request.reason})
    if all(any(e.get("action") == "teacher_adopt" and e.get("receipt") == receipt for e in t.action_history) for t in tasks):
        return grading.submission_view(db, paper.id, replayed=True)
    report = preview(db, paper_id)
    _check_input(report, request)
    selected = {str(t.id) for t in tasks}
    selected_items = {t.snapshot["paper_instance_item_id"] for t in tasks}
    candidates = [u for u in report["units"] if u["eligible"] and u["paper_instance_item_id"] in selected_items]
    if (not candidates or any(not u["task"] or not u["task"]["adoptable"] for u in candidates)
            or {u["task"]["id"] for u in candidates} != selected):
        _problem("ADOPTION_NOT_READY", "请一次采用本题全部有效且有确定结论的真实建议；模拟、过期或无法确定的结果不能采用", 422)
    decisions = defaultdict(dict)
    for task in tasks:
        if assessment.binding_error(db, task) or not _real_result(db, task):
            _problem("INPUT_STALE", "建议已失效，请重新检查整卷")
        decisions[UUID(task.snapshot["paper_instance_item_id"])][task.snapshot["item_id"]] = {
            "task_id": str(task.id), "verdict": task.result["verdict"], "provider": task.provider,
            "model": task.model, "reference_sha256": task.snapshot["reference_sha256"],
            "context_sha256": task.snapshot["context_binding"]["context_sha256"],
            "input_sha256": task.snapshot["input_sha256"]}
    for item in grading._items(db, paper.id):
        if item.id in decisions:
            grading.record_equivalence_review(db, attempts[item.id], item, decisions[item.id], request, receipt)
    for task in tasks:
        task.action_history = [*task.action_history, {"action": "teacher_adopt", "receipt": receipt,
            "reviewer": request.reviewer, "reason": request.reason, "at": assessment.now().isoformat(),
            "attempt_revision_no": attempts[UUID(task.snapshot["paper_instance_item_id"])].revision_no}]
    db.flush()
    stats = grading.refresh_student_profiles(db, paper.student_id)["mastery"]
    return grading.submission_view(db, paper.id, mastery_stats=stats)
