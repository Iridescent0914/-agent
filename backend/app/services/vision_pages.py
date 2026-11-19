"""Durable page preparation and read-only task views; no provider invocation."""
from copy import deepcopy
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import (
    AnswerCardArtifact, AnswerPageCollection, AnswerPageEvent, AnswerPageImage,
    PaperInstance, Student, VisionPageCall, VisionPageTask,
)
from app.schemas.vision_pages import PageVisionRequest
from app.services import answer_pages
from app.services.assessment import AssessmentProblem, enabled, now
from app.services.assessment_protocol import input_hash
from app.services.vision_page_protocol import FAKE_MODEL, POLICY_VERSION, PROMPT_VERSION

MAX_TASKS = 1000
MAX_GENERATIONS = 10


def configuration():
    if get_settings().stage6_vision_provider != "fake":
        raise AssessmentProblem("VISION_PROVIDER_UNAVAILABLE", "多页视觉当前仅支持离线模拟，真实供应商尚未接入", 422)
    return {"provider": "fake", "model": FAKE_MODEL, "synthetic": True,
            "prompt_version": PROMPT_VERSION, "policy_version": POLICY_VERSION}


def _context(db, paper_id, artifact_id, *, lock=False):
    statement = select(PaperInstance).where(PaperInstance.id == paper_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    paper = db.scalar(statement)
    artifact = db.scalar(select(AnswerCardArtifact).where(AnswerCardArtifact.id == artifact_id,
                                                        AnswerCardArtifact.paper_instance_id == paper_id))
    if paper is None or artifact is None:
        raise AssessmentProblem("NOT_FOUND", "试卷或打印产物不存在", 404)
    statement = select(AnswerPageCollection).where(AnswerPageCollection.artifact_id == artifact.id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    collection = db.scalar(statement)
    if collection is None:
        raise AssessmentProblem("NOT_FOUND", "请先建立并收集答题页照片", 404)
    return paper, artifact, collection


def _active(db, paper):
    student = db.get(Student, paper.student_id)
    return (paper.status == "published" and paper.invalidated_at is None
            and student is not None and student.archived_at is None)


def page_bindings(db, artifact, collection):
    revisions = dict(db.execute(select(AnswerPageEvent.page_no, func.max(AnswerPageEvent.revision_no))
        .where(AnswerPageEvent.collection_id == collection.id).group_by(AnswerPageEvent.page_no)).all())
    images = {str(i.id): i for i in db.scalars(select(AnswerPageImage).where(
        AnswerPageImage.collection_id == collection.id)).all()}
    result = {}
    for number in range(1, artifact.total_pages + 1):
        image = images.get(collection.effective_pages.get(str(number)))
        if image is None or not answer_pages._available(image, now()):
            continue
        evidence = {"artifact_id": str(artifact.id), "manifest_sha256": artifact.manifest_sha256,
                    "page_no": number, "page_revision": revisions.get(number, 0),
                    "image_id": str(image.id), "image_sha256": image.image_sha256}
        result[number] = {"sha256": input_hash(evidence), "evidence": evidence, "image": image}
    return result


def binding_error(db, task, paper, artifact, collection, bindings=None):
    if (task.provider != "fake" or task.model != FAKE_MODEL or task.prompt_version != PROMPT_VERSION
            or task.policy_version != POLICY_VERSION):
        return "CONFIG_CHANGED"
    if not _active(db, paper) or paper.version_no != task.snapshot["expected_identity"]["version_no"]:
        return "INPUT_STALE"
    if (artifact.manifest_sha256 != task.snapshot["manifest_sha256"]
            or input_hash(artifact.manifest) != artifact.manifest_sha256):
        return "ARTIFACT_CHANGED"
    current = (bindings if bindings is not None else page_bindings(db, artifact, collection)).get(task.page_no)
    if current is None or current["sha256"] != task.binding_sha256:
        return "PAGE_INPUT_CHANGED"
    return None


def _prepare(artifact, collection, binding, view):
    number = binding["evidence"]["page_no"]
    manifest = artifact.manifest
    pages = manifest["pages"]
    if (len(pages) != artifact.total_pages or [p["page_no"] for p in pages] != list(range(1, artifact.total_pages + 1))):
        raise AssessmentProblem("MANIFEST_INVALID", "冻结页清单无效", 422)
    page = pages[number - 1]
    wire_slots = []
    for slot in page["slots"]:
        if slot.get("blocker") is not None:
            raise AssessmentProblem("VISION_STRUCTURE_BLOCKED", "存在人工评分空或缺少选项字母，暂不能启动多页提取", 422)
        wire = {key: slot[key] for key in ("slot_token", "order_no", "subquestion_no", "blank_no", "answer_format", "writing_bbox_pt")}
        if slot["answer_format"] == "choice_letter":
            wire["allowed_option_labels"] = slot["allowed_option_labels"]
        wire_slots.append(wire)
    request = {"schema_version": "stage6-vision-request-v2", "request_id": str(uuid4()),
               "page_token": page["page_token"], "page_size_pt": manifest["page_size_pt"], "slots": wire_slots}
    try:
        PageVisionRequest.model_validate(request)
    except ValueError as exc:
        raise AssessmentProblem("VISION_STRUCTURE_BLOCKED", "答题页槽位或坐标无效", 422) from exc
    snapshot = {**binding["evidence"], "collection_id": str(collection.id),
                "paper_instance_id": str(artifact.paper_instance_id),
                "collection_revision": collection.revision_no,
                "collection_input_sha256": view["effective_input_sha256"],
                "expected_identity": {"artifact_code": artifact.artifact_code,
                    "artifact_revision_no": artifact.revision_no, "instance_code": manifest["instance_code"],
                    "version_no": manifest["version_no"], "layout_version": artifact.layout_version,
                    "page_no": number, "total_pages": artifact.total_pages},
                "slots": deepcopy(page["slots"])}
    return snapshot, request


def _latest(db, collection_id, binding_hash):
    return db.scalar(select(VisionPageTask).where(VisionPageTask.collection_id == collection_id,
        VisionPageTask.binding_sha256 == binding_hash, VisionPageTask.provider == "fake",
        VisionPageTask.prompt_version == PROMPT_VERSION, VisionPageTask.policy_version == POLICY_VERSION)
        .order_by(VisionPageTask.generation.desc()).limit(1))


def _new_task(db, collection, binding, snapshot, payload, reviewer, reason, *, predecessor=None):
    count = db.scalar(select(func.count()).select_from(VisionPageTask).where(VisionPageTask.collection_id == collection.id))
    generation = predecessor.generation + 1 if predecessor else 1
    if count >= MAX_TASKS or generation > MAX_GENERATIONS:
        raise AssessmentProblem("VISION_TASK_LIMIT", "照片集合的模拟任务或重跑次数已达上限")
    config = configuration()
    task = VisionPageTask(collection_id=collection.id, image_id=UUID(snapshot["image_id"]),
        page_no=snapshot["page_no"], binding_sha256=binding, generation=generation,
        dedup_key=input_hash({"binding": binding, "generation": generation, **config}),
        snapshot=snapshot, request_payload=payload, provider=config["provider"], model=config["model"],
        prompt_version=PROMPT_VERSION, policy_version=POLICY_VERSION, status="queued", revision_no=1,
        attempt_count=0, max_attempts=get_settings().stage6_max_attempts,
        timeout_seconds=get_settings().stage6_vision_timeout_seconds,
        lease_seconds=get_settings().stage6_vision_lease_seconds,
        rerun_of=predecessor.id if predecessor else None,
        action_history=[{"action": "rerun" if predecessor else "create", "reviewer": reviewer,
                         "reason": reason, "at": now().isoformat()}])
    db.add(task); db.flush()
    return task


def create_tasks(db, paper_id, artifact_id, request):
    enabled(); configuration()
    paper, artifact, collection = _context(db, paper_id, artifact_id, lock=True)
    view = answer_pages.collection_view(db, paper_id, artifact_id)
    if (collection.revision_no != request.expected_collection_revision
            or view["effective_input_sha256"] != request.expected_input_sha256):
        raise AssessmentProblem("REVISION_CONFLICT", "照片集合已改变，请刷新后启动提取")
    if not view["collection_ready"]:
        raise AssessmentProblem("COLLECTION_INCOMPLETE", "答题页缺失、过期或试卷已失效，请先核对并补齐照片", 422)
    bindings = page_bindings(db, artifact, collection)
    # Validate every page before creating the first row.
    plans = {n: _prepare(artifact, collection, b, view) for n, b in bindings.items()}
    created = 0
    for number, binding in bindings.items():
        if _latest(db, collection.id, binding["sha256"]) is None:
            snapshot, payload = plans[number]
            _new_task(db, collection, binding["sha256"], snapshot, payload, request.reviewer, request.reason)
            created += 1
    return {**list_tasks(db, paper_id, artifact_id), "created_count": created}


def _task_summary(task, error, *, latest=True):
    return {"id": str(task.id), "page_no": task.page_no, "image_id": str(task.image_id),
            "generation": task.generation, "revision_no": task.revision_no,
            "status": task.status, "effective_status": "stale" if error else task.status,
            "is_current": error is None and latest, "binding_error": error,
            "synthetic": True, "provider": task.provider, "model": task.model,
            "attempt_count": task.attempt_count, "max_attempts": task.max_attempts,
            "error_code": task.error_code, "created_at": task.created_at, "completed_at": task.completed_at,
            "identity_check": task.result.get("identity_check") if task.result else None,
            "received_unit_count": len(task.result["answers"]) if task.result else 0,
            "can_submit": False}


def list_tasks(db, paper_id, artifact_id):
    enabled()
    paper, artifact, collection = _context(db, paper_id, artifact_id)
    bindings = page_bindings(db, artifact, collection)
    rows = db.scalars(select(VisionPageTask).where(VisionPageTask.collection_id == collection.id)
                      .order_by(VisionPageTask.created_at.desc(), VisionPageTask.generation.desc(), VisionPageTask.id.desc())).all()
    latest = {}
    for task in rows:
        if task.binding_sha256 not in latest or latest[task.binding_sha256].generation < task.generation:
            latest[task.binding_sha256] = task
    summaries = [_task_summary(t, binding_error(db, t, paper, artifact, collection, bindings),
                               latest=latest[t.binding_sha256].id == t.id) for t in rows]
    current = sorted((s for s in summaries if s["is_current"]), key=lambda s: s["page_no"])
    config_error = None
    try:
        config = configuration()
    except AssessmentProblem as exc:
        config, config_error = {"provider": get_settings().stage6_vision_provider}, exc.code
    return {"configuration": {**config, "error_code": config_error, "real_provider_available": False},
            "collection_id": str(collection.id), "collection_revision": collection.revision_no,
            "current_tasks": current, "history": summaries[:100], "can_submit": False,
            "requires_transcription_confirmation": True}


def get_task(db, paper_id, artifact_id, task_id, *, lock=False):
    paper, artifact, collection = _context(db, paper_id, artifact_id, lock=lock)
    statement = select(VisionPageTask).where(VisionPageTask.id == task_id, VisionPageTask.collection_id == collection.id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    task = db.scalar(statement)
    if task is None:
        raise AssessmentProblem("NOT_FOUND", "提取任务不属于当前试卷的照片集合", 404)
    return task, paper, artifact, collection


def task_detail(db, paper_id, artifact_id, task_id):
    enabled()
    task, paper, artifact, collection = get_task(db, paper_id, artifact_id, task_id)
    newest = _latest(db, collection.id, task.binding_sha256)
    result = _task_summary(task, binding_error(db, task, paper, artifact, collection), latest=newest is not None and newest.id == task.id)
    calls = db.scalars(select(VisionPageCall).where(VisionPageCall.task_id == task.id).order_by(VisionPageCall.call_no)).all()
    return {**result, "snapshot": task.snapshot, "request_payload": task.request_payload,
            "result": task.result, "action_history": task.action_history,
            "calls": [{"id": str(c.id), "call_no": c.call_no, "status": c.status,
                       "raw_response": c.raw_response, "parsed_response": c.parsed_response,
                       "usage": c.usage, "error_code": c.error_code, "late_response": c.late_response,
                       "started_at": c.started_at, "completed_at": c.completed_at} for c in calls]}


def task_action(db, paper_id, artifact_id, task_id, action, request):
    enabled(); configuration()
    task, paper, artifact, collection = get_task(db, paper_id, artifact_id, task_id, lock=True)
    if task.revision_no != request.expected_revision_no:
        raise AssessmentProblem("REVISION_CONFLICT", "提取任务状态已改变，请刷新后操作")
    error = binding_error(db, task, paper, artifact, collection)
    newest = _latest(db, collection.id, task.binding_sha256)
    if action != "cancel" and (error or newest is None or newest.id != task.id):
        raise AssessmentProblem("INPUT_STALE", "照片或任务版本已变化，请按当前有效照片重新建立任务")
    event = {"action": action, "reviewer": request.reviewer, "reason": request.reason, "at": now().isoformat()}
    if action == "rerun":
        if task.status in {"queued", "running"}:
            raise AssessmentProblem("TASK_BUSY", "请先等待或取消正在处理的任务")
        view = answer_pages.collection_view(db, paper_id, artifact_id)
        binding = page_bindings(db, artifact, collection)[task.page_no]
        snapshot, payload = _prepare(artifact, collection, binding, view)
        task = _new_task(db, collection, binding["sha256"], snapshot, payload, request.reviewer, request.reason, predecessor=task)
    elif action == "retry":
        if task.status not in {"failed", "cancelled"} or task.attempt_count >= task.max_attempts:
            raise AssessmentProblem("TASK_NOT_RETRYABLE", "任务不可重试或已达重试次数上限")
        task.status, task.error_code, task.completed_at = "queued", None, None
        task.lease_token, task.lease_until = None, None
        task.revision_no += 1
        task.action_history = [*task.action_history, event]
    elif action == "cancel":
        if task.status not in {"queued", "running"}:
            raise AssessmentProblem("TASK_NOT_CANCELLABLE", "仅等待或处理中的任务可取消")
        task.status, task.completed_at = "cancelled", now()
        task.lease_token, task.lease_until = None, None
        task.revision_no += 1
        task.action_history = [*task.action_history, event]
    else:
        raise AssessmentProblem("INVALID_ACTION", "不支持的任务操作", 422)
    db.flush()
    return task_detail(db, paper_id, artifact_id, task.id)
