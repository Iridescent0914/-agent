from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
from pathlib import Path
import re
from typing import Any
import unicodedata
from uuid import UUID, uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    Attempt, AttemptRevision, PaperInstance, Question, RecognitionItem,
    RecognitionRun, Student, Submission,
)
from app.schemas.recognition import RecognitionReviewRequest, RecognitionRunOut
from app.schemas.submissions import ManualAnswerIn, SubmissionDetailOut
from app.services.grading import grade_answer
from app.services.student_profile import load_context, persist_mastery
from app.services.submission_workflow import (
    GradingConflict, GradingValidation, _attempt_state, _grading_reference,
    _initial_grading_reference, _items, _reference_details, _validate_answer,
    submission_view,
)
from app.services.vision_service import (
    PROMPT_VERSION, VisionService, VisionServiceError, get_vision_service,
    parse_vision_output,
)


def normalize_image(data: bytes) -> tuple[bytes, str, int, int]:
    settings = get_settings()
    if not data:
        raise GradingValidation("图片不能为空")
    if len(data) > settings.recognition_max_upload_bytes:
        raise GradingValidation("图片超过大小限制")
    try:
        with Image.open(BytesIO(data)) as source:
            source.verify()
        with Image.open(BytesIO(data)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise GradingValidation("只支持 JPEG、PNG、WebP")
            image = ImageOps.exif_transpose(source)
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > settings.recognition_max_pixels:
                raise GradingValidation("图片像素超过限制")
            output = BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=92, optimize=True)
            return output.getvalue(), "image/jpeg", width, height
    except (UnidentifiedImageError, OSError) as exc:
        raise GradingValidation("图片无法读取") from exc


def _run_out(db: Session, run: RecognitionRun) -> RecognitionRunOut:
    rows = list(db.scalars(
        select(RecognitionItem).where(RecognitionItem.recognition_run_id == run.id)
        .order_by(RecognitionItem.order_no)
    ))
    return RecognitionRunOut(
        id=run.id,
        paper_instance_id=run.paper_instance_id,
        student_id=run.student_id,
        submission_id=run.submission_id,
        status=run.status,
        provider=run.provider,
        model=run.model,
        prompt_version=run.prompt_version,
        original_filename=run.original_filename,
        content_type=run.content_type,
        byte_size=run.byte_size,
        pixel_width=run.pixel_width,
        pixel_height=run.pixel_height,
        error_code=run.error_code,
        error_message=run.error_message,
        created_at=run.created_at,
        completed_at=run.completed_at,
        items=[{
            "id": row.id,
            "paper_instance_item_id": row.paper_instance_item_id,
            "order_no": row.order_no,
            "recognition_status": row.recognition_status,
            "recognized_answer": row.recognized_answer,
            "confidence": float(row.confidence) if row.confidence is not None else None,
        } for row in rows],
        image_url=f"/api/v1/recognition-runs/{run.id}/image",
    )


def get_run(db: Session, run_id: UUID) -> RecognitionRunOut:
    run = db.get(RecognitionRun, run_id)
    if run is None:
        raise LookupError("recognition run not found")
    return _run_out(db, run)


def image_path(db: Session, run_id: UUID) -> tuple[Path, str]:
    run = db.get(RecognitionRun, run_id)
    if run is None:
        raise LookupError("recognition run not found")
    path = get_settings().recognition_upload_dir / run.image_storage_key
    if not path.is_file():
        raise LookupError("recognition image not found")
    return path, run.content_type


def _option_label(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    label = unicodedata.normalize("NFKC", value).strip().upper()
    return label if len(label) == 1 and "A" <= label <= "Z" else None


def _structured_option_labels(options: Any) -> set[str]:
    labels: set[str] = set()
    if isinstance(options, dict):
        for key, value in options.items():
            label = _option_label(key)
            if label:
                labels.add(label)
            if isinstance(value, dict):
                for field in ("label", "key", "option", "id"):
                    label = _option_label(value.get(field))
                    if label:
                        labels.add(label)
    elif isinstance(options, list):
        for index, value in enumerate(options):
            found = False
            if isinstance(value, dict):
                for field in ("label", "key", "option", "id"):
                    label = _option_label(value.get(field))
                    if label:
                        labels.add(label)
                        found = True
            elif isinstance(value, str):
                match = re.match(r"^\s*[（(]?\s*([A-Z])\s*[）).．、:：]", unicodedata.normalize("NFKC", value))
                if match:
                    labels.add(match.group(1))
                    found = True
            if not found and index < 26:
                # A plain ordered list still defines A, B, C... even when its
                # option values do not duplicate labels.
                labels.add(chr(ord("A") + index))
    return labels


def _question_text_option_labels(question_snapshot: Any) -> set[str]:
    if not isinstance(question_snapshot, dict):
        return set()
    text = unicodedata.normalize(
        "NFKC", str(question_snapshot.get("question_text") or "")
    )
    pattern = r"(?:^|[\s;；。])(?:[（(]\s*)?([A-Z])\s*(?:[）)]|[.．、:：])"
    return set(re.findall(pattern, text, flags=re.MULTILINE))


def _expected_vision_item(item) -> dict[str, Any]:
    answer = item.answer_snapshot
    blank_ids = ["answer"]
    if isinstance(answer, dict) and answer.get("kind") == "group":
        blank_ids = [
            str(part["item_id"])
            for part in answer.get("items", [])
            if isinstance(part, dict) and part.get("item_id") is not None
        ] or ["answer"]
    question_type = item.question_snapshot.get("question_type")
    result = {
        "order_no": item.order_no,
        "question_type": question_type,
        "blank_ids": blank_ids,
        "answer_format": "choice_letter" if question_type == "choice" else "math_or_text",
    }
    if question_type == "choice":
        labels = _structured_option_labels(item.options_snapshot)
        if not labels:
            labels = _question_text_option_labels(item.question_snapshot)
        result["allowed_option_labels"] = sorted(labels)
    return result


def create_run(
    db: Session,
    paper_id: UUID,
    *,
    instance_code: str,
    idempotency_key: str,
    filename: str | None,
    data: bytes,
) -> RecognitionRunOut:
    if len(idempotency_key.strip()) < 8:
        raise GradingValidation("idempotency_key 至少 8 个字符")
    paper = db.scalar(
        select(PaperInstance).where(PaperInstance.id == paper_id).with_for_update()
    )
    if paper is None:
        raise LookupError("paper instance not found")
    confirmed_code = instance_code.strip().upper()
    if paper.status != "published" or paper.instance_code != confirmed_code:
        raise GradingValidation("纸面实例码与所选试卷不匹配")
    if paper.invalidated_at is not None and paper.invalidation_type != "superseded":
        raise GradingValidation("该试卷版本不能接收新提交")
    image, content_type, width, height = normalize_image(data)
    digest = hashlib.sha256(image).hexdigest()
    fingerprint = hashlib.sha256(
        f"{paper.id}:{confirmed_code}:{digest}".encode("utf-8")
    ).hexdigest()
    existing = db.scalar(select(RecognitionRun).where(
        RecognitionRun.idempotency_key == idempotency_key.strip()
    ).with_for_update())
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise GradingConflict("idempotency key was reused with a different image")
        return _run_out(db, existing)

    settings = get_settings()
    settings.recognition_upload_dir.mkdir(parents=True, exist_ok=True)
    storage_key = f"{uuid4().hex}.jpg"
    final_path = settings.recognition_upload_dir / storage_key
    temporary_path = settings.recognition_upload_dir / f".{storage_key}.tmp"
    temporary_path.write_bytes(image)
    temporary_path.replace(final_path)
    try:
        run = RecognitionRun(
            paper_instance_id=paper.id,
            student_id=paper.student_id,
            idempotency_key=idempotency_key.strip(),
            request_fingerprint=fingerprint,
            status="uploaded",
            provider=settings.vision_provider,
            model=settings.vision_model,
            prompt_version=PROMPT_VERSION,
            image_storage_key=storage_key,
            image_sha256=digest,
            original_filename=(filename or "")[:255] or None,
            content_type=content_type,
            byte_size=len(image),
            pixel_width=width,
            pixel_height=height,
            retention_until=datetime.now(timezone.utc) + timedelta(
                days=settings.recognition_retention_days
            ),
        )
        db.add(run)
        db.flush()
    except Exception:
        final_path.unlink(missing_ok=True)
        raise
    return _run_out(db, run)


def extract_run(
    db: Session, run_id: UUID, *, service: VisionService | None = None
) -> RecognitionRunOut:
    # Do not keep a row lock while a remote VLM request is in flight. A second
    # extractor may duplicate the provider call, but the lock below ensures
    # that only one result can change the run and its item set.
    run = db.get(RecognitionRun, run_id)
    if run is None:
        raise LookupError("recognition run not found")
    if run.status not in {"uploaded", "failed"}:
        raise GradingConflict("recognition run cannot be extracted in its current state")
    items = _items(db, run.paper_instance_id)
    started_at = datetime.now(timezone.utc)
    payload: dict[str, Any] | None = None
    try:
        try:
            image_bytes = (
                get_settings().recognition_upload_dir / run.image_storage_key
            ).read_bytes()
        except OSError as exc:
            raise VisionServiceError("image_unreadable", "recognition image is unavailable") from exc
        payload = (service or get_vision_service()).extract_answers(
            image_bytes=image_bytes,
            content_type=run.content_type,
            expected_items=[_expected_vision_item(item) for item in items],
        )
        expected_specs = [_expected_vision_item(item) for item in items]
        values = parse_vision_output(payload, expected_specs)
        db.expire(run)
        run = db.scalar(
            select(RecognitionRun).where(RecognitionRun.id == run_id)
            .with_for_update().execution_options(populate_existing=True)
        )
        if run is None:
            raise LookupError("recognition run not found")
        if run.status not in {"uploaded", "failed"}:
            raise GradingConflict("recognition run was changed while extraction was running")
        run.status = "processing"
        run.started_at = started_at
        for old in list(db.scalars(select(RecognitionItem).where(
            RecognitionItem.recognition_run_id == run.id
        ))):
            db.delete(old)
        for item in items:
            value = values[item.order_no]
            db.add(RecognitionItem(
                recognition_run_id=run.id,
                paper_instance_item_id=item.id,
                order_no=item.order_no,
                recognition_status=value["recognition_status"],
                recognized_answer=value["answer"],
                confidence=value["confidence"],
                provider_payload=value,
            ))
        run.raw_response = payload
        run.status = "ready_for_review"
        run.error_code = None
        run.error_message = None
    except VisionServiceError as exc:
        db.expire(run)
        run = db.scalar(
            select(RecognitionRun).where(RecognitionRun.id == run_id)
            .with_for_update().execution_options(populate_existing=True)
        )
        if run is None:
            raise LookupError("recognition run not found") from exc
        if run.status not in {"uploaded", "failed"}:
            raise GradingConflict("recognition run was changed while extraction was running") from exc
        run.started_at = started_at
        run.status = "failed"
        if payload is not None:
            run.raw_response = payload
        run.error_code = exc.code
        run.error_message = str(exc)[:1000]
    run.completed_at = datetime.now(timezone.utc)
    db.flush()
    return _run_out(db, run)


def commit_run(db: Session, run_id: UUID) -> SubmissionDetailOut:
    run = db.scalar(
        select(RecognitionRun).where(RecognitionRun.id == run_id).with_for_update()
    )
    if run is None:
        raise LookupError("recognition run not found")
    if run.status == "committed" and run.submission_id is not None:
        return submission_view(db, run.paper_instance_id, replayed=True)
    if run.status != "ready_for_review":
        raise GradingConflict("recognition run is not ready for commit")
    student = db.scalar(select(Student).where(Student.id == run.student_id).with_for_update())
    paper = db.scalar(select(PaperInstance).where(
        PaperInstance.id == run.paper_instance_id
    ).with_for_update())
    if student is None or student.archived_at is not None or paper is None:
        raise GradingValidation("paper ownership is unavailable")
    if paper.status != "published":
        raise GradingValidation("only a published paper can receive an image submission")
    if paper.invalidated_at is not None and paper.invalidation_type != "superseded":
        raise GradingValidation("the paper version no longer accepts submissions")
    existing = db.scalar(select(Submission).where(
        Submission.paper_instance_id == paper.id
    ).with_for_update())
    if existing is not None:
        raise GradingConflict("paper already has a submission")
    paper_items = _items(db, paper.id)
    recognized = {row.paper_instance_item_id: row for row in db.scalars(
        select(RecognitionItem).where(RecognitionItem.recognition_run_id == run.id)
    )}
    if set(recognized) != {item.id for item in paper_items}:
        raise GradingValidation("recognition items do not cover the published paper")

    question_ids = sorted({item.question_id for item in paper_items}, key=str)
    questions = {question.id: question for question in db.scalars(
        select(Question).where(Question.id.in_(question_ids)).with_for_update()
    )}
    now = datetime.now(timezone.utc)
    submission = Submission(
        student_id=paper.student_id,
        paper_instance_id=paper.id,
        source="image",
        idempotency_key=f"image-run-{run.id}",
        request_fingerprint=run.request_fingerprint,
        attachments=[{
            "recognition_run_id": str(run.id),
            "sha256": run.image_sha256,
            "content_type": run.content_type,
        }],
        submitted_at=now,
    )
    db.add(submission)
    db.flush()
    for item in paper_items:
        recognized_item = recognized[item.id]
        answer = recognized_item.recognized_answer if recognized_item.recognition_status == "readable" else None
        reference_answer, reference_rule, reference_source = _initial_grading_reference(
            item, questions.get(item.question_id)
        )
        details: dict[str, Any] = {
            "reason_code": "awaiting_image_review",
            "recognition_run_id": str(run.id),
            "recognized_answer": deepcopy(recognized_item.recognized_answer),
            "recognition_status": recognized_item.recognition_status,
            "reference_source": reference_source,
        }
        if reference_source == "question_revision":
            question = questions[item.question_id]
            details = _reference_details(details, question, reference_rule, now, regraded=False)
        attempt = Attempt(
            submission_id=submission.id,
            student_id=paper.student_id,
            paper_instance_item_id=item.id,
            question_id=item.question_id,
            student_answer=answer,
            normalized_answer=None,
            grading_details=details,
            grade_status="needs_review",
            is_correct=None,
            ocr_confidence=recognized_item.confidence,
            recognition_status=recognized_item.recognition_status,
            manual_override=False,
            revision_no=1,
            answered_at=now,
            reviewed_at=None,
        )
        db.add(attempt)
        db.flush()
        db.add(AttemptRevision(
            attempt_id=attempt.id,
            revision_no=1,
            before_state=None,
            after_state=_attempt_state(attempt),
            operator_type="vision",
            operator_id=f"{run.provider}:{run.model}"[:80],
            reason="image_recognition_pending_teacher_review",
            created_at=now,
        ))
    run.status = "committed"
    run.submission_id = submission.id
    run.committed_at = now
    db.flush()
    return submission_view(db, paper.id)


def review_recognition(
    db: Session, submission_id: UUID, request: RecognitionReviewRequest
) -> SubmissionDetailOut:
    submission = db.scalar(select(Submission).where(
        Submission.id == submission_id
    ).with_for_update())
    if submission is None:
        raise LookupError("submission not found")
    if submission.source != "image":
        raise GradingValidation("submission is not image-based")
    student = db.scalar(select(Student).where(
        Student.id == submission.student_id
    ).with_for_update())
    if student is None or student.archived_at is not None:
        raise GradingValidation("student is unavailable")
    requested = {item.attempt_id: item for item in request.items}
    attempts = list(db.scalars(select(Attempt).where(
        Attempt.id.in_(sorted(requested, key=str))
    ).order_by(Attempt.id).with_for_update()))
    if {attempt.id for attempt in attempts} != set(requested):
        raise GradingValidation("review contains an unknown attempt")
    if any(attempt.submission_id != submission.id for attempt in attempts):
        raise GradingValidation("all attempts must belong to the image submission")
    paper_items = {item.id: item for item in _items(db, submission.paper_instance_id)}
    now = datetime.now(timezone.utc)
    for attempt in attempts:
        incoming_request = requested[attempt.id]
        if attempt.revision_no != incoming_request.expected_revision_no:
            raise GradingConflict("attempt was changed; refresh before reviewing")
        if attempt.grade_status != "needs_review":
            raise GradingConflict("confirmed image evidence cannot be overwritten")
        if incoming_request.action == "accept" and attempt.recognition_status in {
            "missing", "illegible", "ambiguous"
        }:
            raise GradingValidation("uncertain recognition must be corrected explicitly")
        item = paper_items.get(attempt.paper_instance_item_id)
        if item is None:
            raise GradingValidation("attempt item is unavailable")
        incoming = ManualAnswerIn(
            paper_instance_item_id=item.id,
            order_no=item.order_no,
            response_status=incoming_request.response_status,
            answer=incoming_request.answer,
        )
        reference_answer, reference_rule, reference_source = _grading_reference(item, attempt)
        _validate_answer(item, incoming, (reference_answer, reference_rule))
        result = grade_answer(
            incoming.answer,
            reference_answer,
            reference_rule,
            response_status=incoming.response_status,
        )
        before = _attempt_state(attempt)
        attempt.student_answer = incoming.answer
        attempt.normalized_answer = result.normalized_answer
        preserved = {
            key: deepcopy((attempt.grading_details or {})[key])
            for key in (
                "reference_source", "reference_answer_snapshot",
                "reference_answer_rule_snapshot", "reference_question_revision",
                "reference_applied_at", "recognition_run_id", "recognized_answer",
                "recognition_status",
            )
            if key in (attempt.grading_details or {})
        }
        attempt.grading_details = {**result.details(), **preserved}
        attempt.grade_status = (
            "needs_review" if result.grade_status == "needs_review" else "teacher_confirmed"
        )
        attempt.is_correct = None if result.grade_status == "needs_review" else result.is_correct
        attempt.manual_override = incoming_request.action == "correct"
        attempt.revision_no += 1
        attempt.reviewed_at = now
        db.add(AttemptRevision(
            attempt_id=attempt.id,
            revision_no=attempt.revision_no,
            before_state=before,
            after_state=_attempt_state(attempt),
            operator_type="teacher",
            operator_id=request.actor,
            reason=request.reason,
            created_at=now,
        ))
    db.flush()
    mastery_stats = persist_mastery(db, load_context(db, submission.student_id))
    return submission_view(
        db, submission.paper_instance_id, mastery_stats=mastery_stats
    )


def discard_run(db: Session, run_id: UUID) -> None:
    run = db.scalar(select(RecognitionRun).where(
        RecognitionRun.id == run_id
    ).with_for_update())
    if run is None:
        raise LookupError("recognition run not found")
    if run.status == "committed":
        raise GradingConflict("committed recognition cannot be discarded")
    run.status = "discarded"
    path = get_settings().recognition_upload_dir / run.image_storage_key
    path.unlink(missing_ok=True)
    db.flush()
