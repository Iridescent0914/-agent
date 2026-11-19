"""Merge frozen current pages; teacher confirmation is the sole grading bridge.

No provider calls. Model results remain untouched; all edits have revisions.
Lock order follows grading: student (on commit), paper, collection, questions.
"""
from copy import deepcopy
from uuid import UUID

from sqlalchemy import select

from app.models import (
    AnswerTranscription, AnswerTranscriptionRevision, PaperInstance, Question,
    Student, Submission,
)
from app.schemas.submissions import ManualAnswerIn, ManualSubmissionRequest
from app.services import answer_pages, submission_workflow, vision_pages
from app.services.assessment import AssessmentProblem, enabled
from app.services.assessment_protocol import canonical_json, input_hash
from app.services.vision_page_protocol import identity_check, parse_page_response
from app.services.vision_preparation import build_inventory


def _problem(code, message, status=409):
    raise AssessmentProblem(code, message, status)


def _head(db, collection, *, lock=False):
    query = select(AnswerTranscription).where(AnswerTranscription.collection_id == collection.id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return db.scalar(query)


def _capture(db, paper, artifact, collection, *, lock=False):
    view = answer_pages.collection_view(db, paper.id, artifact.id)
    if not view["collection_ready"]:
        _problem("COLLECTION_INCOMPLETE", "照片缺页、过期或试卷失效，请先补齐有效答题页")
    inventory = build_inventory(db, paper.id)
    specs = {s.slot_key: s.model_dump() for s in inventory.slots}
    bindings = vision_pages.page_bindings(db, artifact, collection)
    pages, originals, seen = [], [], set()
    for number in range(1, artifact.total_pages + 1):
        binding = bindings[number]
        task = vision_pages._latest(db, collection.id, binding["sha256"])
        if task is None or task.status != "needs_review" or not task.result:
            _problem("PAGE_RESULT_INCOMPLETE", f"答题页 {number} 尚无可用提取结果，请先完成逐页任务")
        error = vision_pages.binding_error(db, task, paper, artifact, collection, bindings)
        if error:
            _problem(error, "提取结果已过期，请刷新照片及逐页任务")
        try:
            parsed = parse_page_response(canonical_json({k: task.result[k] for k in (
                "schema_version", "request_id", "page_token", "observed_identity", "answers")}), task.request_payload)
        except (ValueError, KeyError, TypeError):
            _problem("MODEL_SCHEMA_INVALID", "提取结果结构无效，请重新提取")
        identity = identity_check(parsed, task.snapshot["expected_identity"])
        if identity["status"] == "mismatch":
            _problem("PAGE_IDENTITY_MISMATCH", f"答题页 {number} 的纸面编号不一致，请检查照片归属")
        by_token = {a["slot_token"]: a for a in parsed["answers"]}
        for slot in task.snapshot["slots"]:
            key = slot["slot_key"]
            if key in seen or key not in specs or any(slot.get(k) != v for k, v in specs.get(key, {}).items()):
                _problem("SLOT_SET_MISMATCH", "冻结页空位与当前试卷不一致，不能录入")
            seen.add(key)
            raw = by_token[slot["slot_token"]]
            originals.append({**specs[key], "page_no": number,
                "original_status": raw["recognition_status"], "original_answer": raw["answer"],
                "confidence": raw["confidence"], "recognition_status": raw["recognition_status"],
                "answer": raw["answer"]})
        pages.append({"page_no": number, "task_id": str(task.id), "task_revision": task.revision_no,
            "generation": task.generation, "binding_sha256": task.binding_sha256,
            "image_id": str(task.image_id), "image_sha256": task.snapshot["image_sha256"],
            "result_sha256": input_hash(task.result), "identity_check": identity,
            "synthetic": task.provider == "fake" or task.result.get("synthetic") is not False,
            "provider": task.provider, "model": task.model})
    if seen != set(specs) or not seen:
        _problem("SLOT_SET_MISMATCH", "整卷逐空结果不完整，不能将缺失空位当作留白")
    items = submission_workflow._items(db, paper.id)
    query = select(Question).where(Question.id.in_({i.question_id for i in items})).order_by(Question.id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    questions = {q.id: q for q in db.scalars(query)}
    references = {str(i.id): input_hash(submission_workflow._initial_grading_reference(i, questions.get(i.question_id))) for i in items}
    evidence = {"collection_revision": collection.revision_no,
        "collection_input_sha256": view["effective_input_sha256"], "artifact_id": str(artifact.id),
        "manifest_sha256": artifact.manifest_sha256, "inventory_sha256": inventory.inventory_sha256,
        "references": references, "pages": pages}
    return evidence, sorted(originals, key=lambda u: (u["order_no"], u["subquestion_no"], u["blank_no"]))


def _record(db, head, action, request):
    db.flush()
    db.add(AnswerTranscriptionRevision(transcription_id=head.id, revision_no=head.revision_no,
        action=action, state={"input_sha256": head.input_sha256, "evidence": deepcopy(head.evidence),
            "units": deepcopy(head.units), "confirmed_page_nos": list(head.confirmed_page_nos),
            "submission_id": str(head.submission_id) if head.submission_id else None},
        reviewer=request.reviewer, reason=request.reason))
    db.flush()


def _check_revision(head, request):
    if request.expected_revision_no != (head.revision_no if head else 0):
        _problem("REVISION_CONFLICT", "核对清单已被修改，请刷新后再操作")
    if head and head.revision_no >= 1000:
        _problem("TRANSCRIPTION_REVISION_LIMIT", "核对清单修订已达上限，请联系维护人员处理")


def _current(db, paper, artifact, collection, head, request):
    _check_revision(head, request)
    if head is None:
        _problem("TRANSCRIPTION_MISSING", "请先合并整卷提取结果")
    if head.submission_id:
        _problem("ALREADY_SUBMITTED", "本清单已录入，请到批改页修正现有作答")
    evidence, _ = _capture(db, paper, artifact, collection, lock=True)
    if input_hash(evidence) != head.input_sha256 or request.expected_input_sha256 != head.input_sha256:
        _problem("TRANSCRIPTION_STALE", "照片、提取任务或评分标准已改变，请重新合并并核对")


def _blockers(head):
    errors = []
    if any(p["synthetic"] for p in head.evidence["pages"]):
        errors.append("模拟提取结果仅用于开发检查，不能录入正式批改")
    if any(p["page_no"] not in head.confirmed_page_nos for p in head.evidence["pages"]):
        errors.append("请逐页对照照片确认纸面编号和全部答案")
    if any(u["recognition_status"] not in {"readable", "blank"} for u in head.units):
        errors.append("仍有缺失、无法辨认或歧义空位，请修正或明确标记留白")
    return errors


def view(db, paper_id, artifact_id):
    enabled()
    paper, artifact, collection = vision_pages._context(db, paper_id, artifact_id)
    head = _head(db, collection)
    if head is None:
        return {"id": None, "revision_no": 0, "can_submit": False, "units": [], "history": []}
    stale, message = False, None
    if not head.submission_id:
        try:
            evidence, _ = _capture(db, paper, artifact, collection)
            stale = input_hash(evidence) != head.input_sha256
            if stale:
                message = "照片、提取任务或评分标准已改变，请重新合并并核对"
        except (AssessmentProblem, ValueError) as exc:
            stale, message = True, str(exc)
    existing = db.scalar(select(Submission.id).where(Submission.paper_instance_id == paper.id))
    history = list(db.scalars(select(AnswerTranscriptionRevision).where(
        AnswerTranscriptionRevision.transcription_id == head.id).order_by(AnswerTranscriptionRevision.revision_no.desc()).limit(100)))
    base = f"/internal/stage6/paper-instances/{paper.id}/answer-card-artifacts/{artifact.id}/page-collection"
    available = {i["id"]: i for i in answer_pages.collection_view(db, paper_id, artifact_id)["images"]}
    pages = [{**p, "image_url": f"{base}/images/{p['image_id']}" if available.get(p["image_id"], {}).get("available") else None}
             for p in head.evidence["pages"]]
    blockers = _blockers(head)
    return {"id": str(head.id), "revision_no": head.revision_no, "input_sha256": head.input_sha256,
        "collection_revision": head.evidence["collection_revision"], "stale": stale, "stale_message": message,
        "submission_id": str(head.submission_id) if head.submission_id else None,
        "existing_submission_id": str(existing) if existing else None,
        "can_submit": not stale and not existing and not blockers,
        "blockers": blockers, "pages": pages, "units": head.units,
        "confirmed_page_nos": head.confirmed_page_nos,
        "history": [{"revision_no": h.revision_no, "action": h.action, "reviewer": h.reviewer,
                     "reason": h.reason, "created_at": h.created_at} for h in history]}


def merge(db, paper_id, artifact_id, request):
    enabled()
    paper, artifact, collection = vision_pages._context(db, paper_id, artifact_id, lock=True)
    head = _head(db, collection, lock=True)
    _check_revision(head, request)
    if head and head.submission_id:
        _problem("ALREADY_SUBMITTED", "本清单已录入，请到批改页修正")
    evidence, units = _capture(db, paper, artifact, collection, lock=True)
    if (request.expected_collection_revision != collection.revision_no or
            request.expected_input_sha256 != evidence["collection_input_sha256"]):
        _problem("REVISION_CONFLICT", "照片集合已改变，请先刷新照片集合")
    digest = input_hash(evidence)
    if head and head.input_sha256 == digest:
        return view(db, paper_id, artifact_id)  # Reload never erases saved teacher edits.
    if head is None:
        head = AnswerTranscription(collection_id=collection.id, revision_no=0)
        db.add(head)
    head.revision_no += 1
    head.evidence, head.input_sha256, head.units, head.confirmed_page_nos = evidence, digest, units, []
    _record(db, head, "merge", request)
    return view(db, paper_id, artifact_id)


def save(db, paper_id, artifact_id, request):
    enabled()
    paper, artifact, collection = vision_pages._context(db, paper_id, artifact_id, lock=True)
    head = _head(db, collection, lock=True)
    _current(db, paper, artifact, collection, head, request)
    incoming = {u.slot_key: u for u in request.units}
    if len(incoming) != len(request.units) or set(incoming) != {u["slot_key"] for u in head.units}:
        _problem("SLOT_SET_MISMATCH", "保存必须完整覆盖原清单，不能增删或重复空位", 422)
    pages = request.confirmed_page_nos
    if (any(type(n) is not int for n in pages) or len(set(pages)) != len(pages)
            or not set(pages) <= {p["page_no"] for p in head.evidence["pages"]}):
        _problem("PAGE_SET_MISMATCH", "核对页码无效", 422)
    units = [{**unit, "recognition_status": incoming[unit["slot_key"]].recognition_status,
              "answer": incoming[unit["slot_key"]].answer} for unit in head.units]
    for unit in units:
        if (unit["answer_format"] == "choice_letter" and unit["recognition_status"] == "readable"
                and unit["answer"] not in unit["allowed_option_labels"]):
            _problem("CHOICE_LABEL_INVALID", "选择题请录入允许的选项字母", 422)
    head.units, head.confirmed_page_nos = units, sorted(pages)
    head.revision_no += 1
    _record(db, head, "save", request)
    return view(db, paper_id, artifact_id)


def confirm(db, paper_id, artifact_id, request):
    enabled()
    initial = db.get(PaperInstance, paper_id)
    if initial is None:
        _problem("NOT_FOUND", "试卷不存在", 404)
    db.scalar(select(Student).where(Student.id == initial.student_id).with_for_update())
    paper, artifact, collection = vision_pages._context(db, paper_id, artifact_id, lock=True)
    head = _head(db, collection, lock=True)
    # A network retry of the exact committed revision returns the same submission.
    if head and head.submission_id:
        if request.expected_revision_no != head.revision_no - 1 or request.expected_input_sha256 != head.input_sha256:
            _problem("REVISION_CONFLICT", "确认版本不一致，请查看现有批改")
        return submission_workflow.submission_view(db, paper_id, replayed=True)
    _current(db, paper, artifact, collection, head, request)
    blockers = _blockers(head)
    if blockers:
        _problem("TRANSCRIPTION_NOT_CONFIRMED", "；".join(blockers), 422)
    if db.scalar(select(Submission.id).where(Submission.paper_instance_id == paper.id)):
        _problem("ALREADY_SUBMITTED", "试卷已有作答，请在批改页修正，不能覆盖")
    grouped = {}
    for unit in head.units:
        grouped.setdefault(unit["paper_instance_item_id"], []).append(unit)
    answers = []
    for item in submission_workflow._items(db, paper.id):
        units = grouped[str(item.id)]
        blank = all(u["recognition_status"] == "blank" for u in units)
        if blank:
            answer = None
        elif isinstance(item.answer_snapshot, dict) and item.answer_snapshot.get("kind") == "group":
            answer = {"kind": "group", "schema_version": "answer-units-v1", "items": [
                {"item_id": u["item_id"], "answer": u["answer"]} for u in units]}
        else:
            if len(units) != 1:
                _problem("ANSWER_STRUCTURE_CHANGED", "当前题目结构不能自动组装，请检查标准答案")
            answer = units[0]["answer"]
        answers.append(ManualAnswerIn(paper_instance_item_id=item.id, order_no=item.order_no,
            response_status="blank" if blank else "answered", answer=answer))
    provenance = {"kind": "stage6_transcription", "transcription_id": str(head.id),
        "revision_no": head.revision_no, "input_sha256": head.input_sha256,
        "collection_id": str(collection.id), "artifact_id": str(artifact.id),
        "reviewer": request.reviewer, "reason": request.reason}
    try:
        output = submission_workflow.create_confirmed_image_submission(db, paper.id, ManualSubmissionRequest(
            student_id=paper.student_id, idempotency_key=f"s3e-{head.id}-{head.revision_no}", answers=answers), provenance)
    except (submission_workflow.GradingValidation, submission_workflow.GradingConflict) as exc:
        _problem("GRADING_CONFLICT", str(exc), 422)
    head.submission_id = output.submission_id
    head.revision_no += 1
    _record(db, head, "submit", request)
    return submission_workflow.submission_view(db, paper.id)


def submission_provenance(db, submission):
    """Images bound to the submitted evidence, never silently use retakes."""
    if submission is None:
        return None
    head = db.scalar(select(AnswerTranscription).where(AnswerTranscription.submission_id == submission.id))
    if head is None:
        return None
    from app.models import AnswerPageImage
    from app.services.assessment import now
    pages = []
    for page in head.evidence["pages"]:
        image = db.get(AnswerPageImage, UUID(page["image_id"]))
        base = f"/internal/stage6/paper-instances/{submission.paper_instance_id}/answer-card-artifacts/{head.evidence['artifact_id']}/page-collection"
        pages.append({"page_no": page["page_no"], "provider": page["provider"], "model": page["model"],
            "image_url": f"{base}/images/{image.id}" if image and answer_pages._available(image, now()) else None})
    return {"id": str(head.id), "revision_no": head.revision_no, "pages": pages}
