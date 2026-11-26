"""Label a frozen whole-paper model output; never writes answers or grades."""
from copy import deepcopy
from uuid import UUID

from sqlalchemy import func, select

from app.models import AnswerPageImage, PaperRecognitionReview, PaperRecognitionReviewRevision
from app.services import answer_pages, answer_transcription, vision_pages
from app.services.assessment import AssessmentProblem, enabled, now
from app.services.assessment_protocol import input_hash


def _problem(code, message, status=409):
    raise AssessmentProblem(code, message, status)


def _snapshot(paper, artifact, collection, head, evidence=None, units=None):
    source = evidence if evidence is not None else head.evidence
    original = units if units is not None else head.units
    # Whitelist original transcription only: no teacher edits, reference answers or provider settings.
    return {
        "schema_version": "stage6-paper-recognition-review-v1",
        "paper_instance_id": str(paper.id), "artifact_id": str(artifact.id),
        "collection_id": str(collection.id), "transcription_id": str(head.id),
        "manifest_sha256": source["manifest_sha256"], "inventory_sha256": source["inventory_sha256"],
        "pages": [{k: p[k] for k in (
            "page_no", "task_id", "task_revision", "generation", "binding_sha256", "image_id",
            "image_sha256", "result_sha256", "synthetic", "provider", "model",
        )} for p in source["pages"]],
        "units": [{k: u[k] for k in (
            "slot_key", "paper_instance_item_id", "item_id", "order_no", "subquestion_no",
            "blank_no", "page_no", "original_status", "original_answer",
        )} for u in original],
    }


def _target(db, paper, artifact, collection):
    head = answer_transcription._head(db, collection)
    if head is None:
        return None, None, "请先合并整卷提取结果，再记录本次识别核对结论", False
    snapshot = _snapshot(paper, artifact, collection, head)
    stale_message = None
    if not vision_pages._active(db, paper):
        stale_message = "试卷或学生已失效，仅可查看历史核对记录"
    elif not head.submission_id:
        try:
            evidence, units = answer_transcription._capture(db, paper, artifact, collection)
            if input_hash(_snapshot(paper, artifact, collection, head, evidence, units)) != input_hash(snapshot):
                stale_message = "照片或提取结果已变化，请重新合并后核对新版本"
        except (AssessmentProblem, ValueError):
            stale_message = "当前照片或逐页提取结果不完整，请完成提取并重新合并"
    images = [db.get(AnswerPageImage, UUID(p["image_id"])) for p in snapshot["pages"]]
    available = bool(images) and all(
        image is not None and image.collection_id == collection.id and answer_pages._available(image, now())
        for image in images
    )
    return head, snapshot, stale_message, available


def _review(db, collection, digest):
    return db.scalar(select(PaperRecognitionReview).where(
        PaperRecognitionReview.collection_id == collection.id,
        PaperRecognitionReview.recognition_sha256 == digest,
    )) if digest else None


def view(db, paper_id, artifact_id):
    enabled()
    paper, artifact, collection = vision_pages._context(db, paper_id, artifact_id)
    head, snapshot, stale_message, images_available = _target(db, paper, artifact, collection)
    digest = input_hash(snapshot) if snapshot else None
    review = _review(db, collection, digest)
    history = db.execute(select(PaperRecognitionReview, PaperRecognitionReviewRevision).join(
        PaperRecognitionReviewRevision, PaperRecognitionReviewRevision.review_id == PaperRecognitionReview.id,
    ).where(PaperRecognitionReview.collection_id == collection.id).order_by(
        PaperRecognitionReviewRevision.created_at.desc(), PaperRecognitionReviewRevision.id.desc(),
    ).limit(100)).all()
    return {
        "recognition_sha256": digest, "revision_no": review.revision_no if review else 0,
        "transcription_revision_no": head.revision_no if head else None,
        "target_scope": "submitted" if head and head.submission_id else "merged",
        "can_record": bool(snapshot and not stale_message), "stale": bool(stale_message and snapshot),
        "message": stale_message, "original_images_available": images_available,
        "synthetic": any(p["synthetic"] for p in snapshot["pages"]) if snapshot else None,
        "page_count": len(snapshot["pages"]) if snapshot else 0,
        "pages": [{k: p[k] for k in ("page_no", "generation", "provider", "model")}
                  for p in snapshot["pages"]] if snapshot else [],
        "current": deepcopy(review.current) if review else {"review_status": "pending", "is_error": None},
        "history": [{"recognition_sha256": r.recognition_sha256, "revision_no": h.revision_no,
            "is_current_target": r.recognition_sha256 == digest and not stale_message,
            "synthetic": any(p["synthetic"] for p in r.evidence["pages"]),
            "created_at": h.created_at, **h.data} for r, h in history],
    }


def save(db, paper_id, artifact_id, request):
    enabled()
    # Same paper -> collection lock order as retakes, merge and task creation.
    paper, artifact, collection = vision_pages._context(db, paper_id, artifact_id, lock=True)
    head, snapshot, stale_message, images_available = _target(db, paper, artifact, collection)
    if snapshot is None or stale_message:
        _problem("RECOGNITION_REVIEW_STALE", stale_message or "请先合并整卷提取结果")
    digest = input_hash(snapshot)
    if digest != request.expected_recognition_sha256:
        _problem("RECOGNITION_REVIEW_STALE", "识别版本已变化，请刷新后重新核对")
    review = _review(db, collection, digest)
    if request.expected_revision_no != (review.revision_no if review else 0):
        _problem("REVISION_CONFLICT", "整卷识别核对记录已被修改，请刷新后再保存")
    if request.review_status in {"correct", "incorrect"}:
        if not request.all_pages_reviewed:
            _problem("FULL_REVIEW_REQUIRED", "请明确确认已对照全部答题页照片核对模型原始抄录", 422)
        if not images_available:
            _problem("ORIGINAL_IMAGES_UNAVAILABLE", "原照片已不可用，不能新增确定结论；可记录无法判断或未核对", 422)
    current = {"review_status": request.review_status,
        "is_error": {"correct": False, "incorrect": True}.get(request.review_status),
        "all_pages_reviewed": request.all_pages_reviewed,
        "reviewer": request.reviewer, "reason": request.reason}
    if review and review.current == current:
        return {**view(db, paper_id, artifact_id), "unchanged": True}
    if db.scalar(select(func.count()).select_from(PaperRecognitionReviewRevision).join(
        PaperRecognitionReview, PaperRecognitionReview.id == PaperRecognitionReviewRevision.review_id,
    ).where(PaperRecognitionReview.collection_id == collection.id)) >= 1000:
        _problem("REVIEW_LIMIT", "整卷核对记录已达上限，请联系维护人员处理")
    if review is None:
        review = PaperRecognitionReview(collection_id=collection.id, recognition_sha256=digest,
            revision_no=0, evidence=snapshot, current={})
        db.add(review)
    review.revision_no += 1
    review.current = current
    db.flush()
    db.add(PaperRecognitionReviewRevision(review_id=review.id, revision_no=review.revision_no, data=current))
    db.flush()
    return {**view(db, paper_id, artifact_id), "unchanged": False}
