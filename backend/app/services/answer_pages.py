"""Collect photos against a frozen printed artifact. No model or grading calls."""
from datetime import timedelta
import hashlib
import warnings

from PIL import Image
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import AnswerPageCollection, AnswerPageEvent, AnswerPageImage, PaperInstance, Student
from app.services.answer_card_artifacts import get_artifact
from app.services.assessment import AssessmentProblem, now
from app.services.assessment_protocol import input_hash
from app.services.recognition_workflow import normalize_image

MAX_IMAGES = 500
MAX_STORED_BYTES = 128 * 1024 * 1024
MAX_EVENTS = 2000


def _scope(db, paper_id, artifact_id, *, write=False):
    # Same lock order as printing/paper changes: paper, then collection.
    statement = select(PaperInstance).where(PaperInstance.id == paper_id)
    if write:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    paper = db.scalar(statement)
    artifact = get_artifact(db, paper_id, artifact_id)
    student = db.get(Student, paper.student_id)
    active = (paper.status == "published" and paper.invalidated_at is None
              and student is not None and student.archived_at is None
              and paper.version_no == artifact.manifest["version_no"])
    if write and not active:
        raise AssessmentProblem("INPUT_STALE", "试卷或学生已失效，只能查看照片历史")
    return paper, artifact, active


def _collection(db, artifact, *, write=False):
    statement = select(AnswerPageCollection).where(AnswerPageCollection.artifact_id == artifact.id)
    if write:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return db.scalar(statement)


def create_collection(db, paper_id, artifact_id, request):
    paper, artifact, _ = _scope(db, paper_id, artifact_id, write=True)
    if (request.artifact_code.upper() != artifact.artifact_code
            or request.instance_code.upper() != paper.instance_code.upper()
            or request.expected_version_no != paper.version_no):
        raise AssessmentProblem("PAGE_IDENTITY_MISMATCH", "纸面短码、版本或产物编号不符，请核对实际打印的答题页")
    collection = _collection(db, artifact, write=True)
    if collection is None:
        collection = AnswerPageCollection(artifact_id=artifact.id, revision_no=0, effective_pages={})
        db.add(collection)
        db.flush()
    return collection_view(db, paper_id, artifact_id)


def _available(image, current):
    # Do not fetch deferred bytes just to render the list. Retention applies even
    # before the next scheduled purge physically clears database bytes.
    return image.purged_at is None and image.retention_until > current


def collection_view(db, paper_id, artifact_id):
    _, artifact, active = _scope(db, paper_id, artifact_id)
    collection = _collection(db, artifact)
    if collection is None:
        return None
    current = now()
    images = db.scalars(select(AnswerPageImage).where(AnswerPageImage.collection_id == collection.id)
                        .order_by(AnswerPageImage.created_at, AnswerPageImage.id)).all()
    image_map = {str(image.id): image for image in images}
    events = db.scalars(select(AnswerPageEvent).where(AnswerPageEvent.collection_id == collection.id)
                        .order_by(AnswerPageEvent.revision_no.desc())).all()
    prefix = f"/api/v1/internal/stage6/paper-instances/{paper_id}/answer-card-artifacts/{artifact_id}/page-collection"
    pages, missing = [], []
    for number in range(1, artifact.total_pages + 1):
        selected = collection.effective_pages.get(str(number))
        image = image_map.get(selected)
        available = image is not None and _available(image, current)
        if not available:
            missing.append(number)
        pages.append({"page_no": number, "effective_image_id": selected,
                      "status": "uploaded" if available else "expired" if image else "missing"})
    fingerprint = input_hash({"artifact_id": str(artifact.id), "manifest_sha256": artifact.manifest_sha256,
                              "collection_revision": collection.revision_no,
                              "pages": [{**p, "sha256": image_map[p["effective_image_id"]].image_sha256
                                         if p["effective_image_id"] in image_map else None} for p in pages]})
    return {"id": str(collection.id), "artifact_id": str(artifact.id), "artifact_code": artifact.artifact_code,
            "revision_no": collection.revision_no, "total_pages": artifact.total_pages,
            "status": "collected" if not missing else "collecting", "missing_page_nos": missing,
            "collection_ready": not missing and active, "is_active": active,
            "identity_status": "manually_assigned_not_model_verified", "recognition_status": "not_started",
            "can_submit": False, "effective_input_sha256": fingerprint, "pages": pages,
            "images": [{"id": str(i.id), "image_sha256": i.image_sha256, "width": i.width, "height": i.height,
                        "byte_size": i.byte_size, "created_at": i.created_at, "retention_until": i.retention_until,
                        "available": _available(i, current),
                        "image_url": f"{prefix}/images/{i.id}" if _available(i, current) else None} for i in images],
            "history": [{"revision_no": e.revision_no, "page_no": e.page_no, "action": e.action,
                         "before_image_id": str(e.before_image_id) if e.before_image_id else None,
                         "after_image_id": str(e.after_image_id) if e.after_image_id else None,
                         "reviewer": e.reviewer, "reason": e.reason, "created_at": e.created_at} for e in events]}


def _write_scope(db, paper_id, artifact_id, page_no):
    _, artifact, _ = _scope(db, paper_id, artifact_id, write=True)
    if not 1 <= page_no <= artifact.total_pages:
        raise AssessmentProblem("PAGE_OUT_OF_RANGE", "答题页编号超出范围；不使用整卷PDF页码", 422)
    collection = _collection(db, artifact, write=True)
    if collection is None:
        raise AssessmentProblem("NOT_FOUND", "请先核对纸面编号并建立照片集合", 404)
    return artifact, collection


def _revision(collection, expected):
    if collection.revision_no != expected:
        raise AssessmentProblem("REVISION_CONFLICT", "照片集合已改变，请刷新后操作")
    if collection.revision_no >= MAX_EVENTS:
        raise AssessmentProblem("COLLECTION_LIMIT", "照片集合已达到修改次数上限")


def _select(db, collection, page_no, image, *, action, reviewer, reason):
    key = str(page_no)
    before = collection.effective_pages.get(key)
    after = str(image.id) if image else None
    if after and any(value == after and page != key for page, value in collection.effective_pages.items()):
        raise AssessmentProblem("DUPLICATE_PAGE_IMAGE", "同一照片已用于另一答题页；请先核对并移除错误归属")
    mapping = dict(collection.effective_pages)
    if after:
        mapping[key] = after
    else:
        mapping.pop(key, None)
    collection.effective_pages = mapping
    collection.revision_no += 1
    db.add(AnswerPageEvent(collection_id=collection.id, revision_no=collection.revision_no,
                          page_no=page_no, action=action, before_image_id=before, after_image_id=after,
                          reviewer=reviewer, reason=reason))
    db.flush()


def upload_image(db, paper_id, artifact_id, page_no, request, data):
    # Decode before taking DB row locks. Normalize EXIF orientation, remove
    # metadata, and deduplicate on actual stored JPEG bytes rather than filename.
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image_data, _, width, height = normalize_image(data)
    except (ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise AssessmentProblem("INVALID_IMAGE", "图片无法读取、格式不支持或超过大小/像素限制", 422) from exc
    if len(image_data) > get_settings().recognition_max_upload_bytes:
        raise AssessmentProblem("INVALID_IMAGE", "图片转码后超过大小限制", 413)
    _, collection = _write_scope(db, paper_id, artifact_id, page_no)
    digest = hashlib.sha256(image_data).hexdigest()
    image = db.scalar(select(AnswerPageImage).where(AnswerPageImage.collection_id == collection.id,
                                                  AnswerPageImage.image_sha256 == digest))
    current = now()
    if image and any(value == str(image.id) and key != str(page_no)
                     for key, value in collection.effective_pages.items()):
        raise AssessmentProblem("DUPLICATE_PAGE_IMAGE", "同一照片已用于另一答题页；请先核对并移除错误归属")
    if image and _available(image, current) and collection.effective_pages.get(str(page_no)) == str(image.id):
        return {**collection_view(db, paper_id, artifact_id), "reused": True}
    _revision(collection, request.expected_revision_no)
    if image and _available(image, current):
        seen_on_page = db.scalar(select(AnswerPageEvent.id).where(
            AnswerPageEvent.collection_id == collection.id, AnswerPageEvent.page_no == page_no,
            AnswerPageEvent.after_image_id == image.id).limit(1))
        if not seen_on_page:
            _select(db, collection, page_no, image, action="select", reviewer=request.reviewer, reason=request.reason)
            return {**collection_view(db, paper_id, artifact_id), "reused": True}
        # A duplicate of a superseded photo must not undo a newer capture.
        return {**collection_view(db, paper_id, artifact_id), "reused": True,
                "notice": "照片已存在，未替换当前有效页；需要恢复时请显式选择历史照片"}
    stored = int(db.scalar(select(func.coalesce(func.sum(AnswerPageImage.byte_size), 0)).where(
        AnswerPageImage.collection_id == collection.id, AnswerPageImage.purged_at.is_(None))) or 0)
    count = db.scalar(select(func.count()).select_from(AnswerPageImage).where(AnswerPageImage.collection_id == collection.id))
    extra_bytes = len(image_data) - (image.byte_size if image is not None and image.purged_at is None else 0)
    if stored + extra_bytes > MAX_STORED_BYTES or (image is None and count >= MAX_IMAGES):
        raise AssessmentProblem("COLLECTION_LIMIT", "照片集合超过保存上限，请联系维护人员")
    restored = image is not None
    if image is None:
        image = AnswerPageImage(collection_id=collection.id, image_sha256=digest, width=width,
                                height=height, byte_size=len(image_data))
        db.add(image)
    image.image_data = image_data
    image.purged_at = None
    image.retention_until = current + timedelta(days=max(1, get_settings().recognition_retention_days))
    db.flush()
    _select(db, collection, page_no, image, action="restore" if restored else "upload",
            reviewer=request.reviewer, reason=request.reason)
    return {**collection_view(db, paper_id, artifact_id), "reused": False}


def select_image(db, paper_id, artifact_id, page_no, request):
    _, collection = _write_scope(db, paper_id, artifact_id, page_no)
    image = None
    if request.image_id is not None:
        image = db.scalar(select(AnswerPageImage).where(AnswerPageImage.id == request.image_id,
                                                       AnswerPageImage.collection_id == collection.id))
        if image is None:
            raise AssessmentProblem("NOT_FOUND", "照片不属于当前产物的照片集合", 404)
        if not _available(image, now()):
            raise AssessmentProblem("IMAGE_EXPIRED", "照片已过期，请重新上传或补拍")
    selected = str(image.id) if image else None
    if collection.effective_pages.get(str(page_no)) == selected:
        return collection_view(db, paper_id, artifact_id)
    _revision(collection, request.expected_revision_no)
    _select(db, collection, page_no, image, action="select" if image else "remove",
            reviewer=request.reviewer, reason=request.reason)
    return collection_view(db, paper_id, artifact_id)


def read_image(db, paper_id, artifact_id, image_id):
    _, artifact, _ = _scope(db, paper_id, artifact_id)
    collection = _collection(db, artifact)
    image = db.scalar(select(AnswerPageImage).where(AnswerPageImage.id == image_id,
        AnswerPageImage.collection_id == collection.id)) if collection else None
    if image is None:
        raise AssessmentProblem("NOT_FOUND", "照片不存在或不属于当前试卷产物", 404)
    if not _available(image, now()):
        raise AssessmentProblem("IMAGE_EXPIRED", "照片已按保留策略过期", 410)
    data = image.image_data
    if data is None or hashlib.sha256(data).hexdigest() != image.image_sha256:
        raise AssessmentProblem("IMAGE_INTEGRITY_ERROR", "照片内容校验失败")
    return data


def purge_expired_page_images(db, *, current=None):
    current = current or now()
    # Same collection lock as selection/upload; skip collections currently edited.
    ids = select(AnswerPageImage.collection_id).where(AnswerPageImage.purged_at.is_(None),
                                                     AnswerPageImage.retention_until <= current)
    collections = db.scalars(select(AnswerPageCollection).where(AnswerPageCollection.id.in_(ids))
                             .with_for_update(skip_locked=True)).all()
    count = 0
    for collection in collections:
        images = db.scalars(select(AnswerPageImage).where(AnswerPageImage.collection_id == collection.id,
            AnswerPageImage.purged_at.is_(None), AnswerPageImage.retention_until <= current)).all()
        for image in images:
            image.image_data, image.purged_at = None, current
            count += 1
    db.flush()
    return count
