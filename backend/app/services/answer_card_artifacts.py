"""Persist PDF bytes and their page geometry in one transaction, without LLMs."""
import hashlib
from uuid import uuid4

import pymupdf as fitz

from sqlalchemy import func, select

from app.models import AnswerCardArtifact, PaperInstance, Student
from app.services.assessment import AssessmentProblem, enabled
from app.services.assessment_protocol import input_hash
from app.services.answer_card_renderer import LAYOUT_VERSION, RENDERER_VERSION, render_answer_card
from app.services.print_view import build_print_view
from app.services.vision_preparation import build_inventory


def create_artifact(db, paper_id, expected_version_no):
    enabled()
    # Serializing on paper also orders this operation against withdrawal and
    # publishing a replacement; there is no network/render subprocess here.
    paper = db.scalar(select(PaperInstance).where(PaperInstance.id == paper_id)
                      .with_for_update().execution_options(populate_existing=True))
    if paper is None:
        raise AssessmentProblem("NOT_FOUND", "试卷不存在", 404)
    student = db.get(Student, paper.student_id)
    if (paper.status != "published" or paper.invalidated_at is not None
            or student is None or student.archived_at is not None):
        raise AssessmentProblem("INPUT_STALE", "试卷或学生已失效，不能生成新的答题卡")
    if paper.version_no != expected_version_no:
        raise AssessmentProblem("REVISION_CONFLICT", "试卷版本已变更，请刷新后生成")
    try:
        view = build_print_view(db, paper.id)
        inventory = build_inventory(db, paper.id)
    except ValueError as exc:
        raise AssessmentProblem("PRINT_STRUCTURE_INVALID", "试卷作答空位结构无效，请核对参考答案", 422) from exc
    source = {"inventory_sha256": inventory.inventory_sha256, "assignment_title": view.assignment_title,
              "student_code": view.student_code, "assignment_type": view.assignment_type,
              "layout_version": LAYOUT_VERSION, "renderer_version": RENDERER_VERSION}
    source_sha = input_hash(source)
    dedup_key = input_hash({"paper_id": str(paper.id), "source_sha256": source_sha})
    existing = db.scalar(select(AnswerCardArtifact).where(AnswerCardArtifact.dedup_key == dedup_key))
    if existing:
        return existing, True
    revision = int(db.scalar(select(func.max(AnswerCardArtifact.revision_no)).where(
        AnswerCardArtifact.paper_instance_id == paper.id)) or 0) + 1
    artifact_id = uuid4()
    code = artifact_id.hex[:16].upper()
    try:
        pdf, manifest = render_answer_card(
            inventory, assignment_title=view.assignment_title, student_code=view.student_code,
            assignment_type=view.assignment_type, artifact_id=artifact_id, artifact_code=code,
            revision_no=revision, source_sha256=source_sha,
        )
    except ValueError as exc:
        raise AssessmentProblem("PRINT_RENDER_FAILED", str(exc), 422) from exc
    artifact = AnswerCardArtifact(
        id=artifact_id, paper_instance_id=paper.id, revision_no=revision, artifact_code=code,
        dedup_key=dedup_key, source_sha256=source_sha, pdf_sha256=hashlib.sha256(pdf).hexdigest(),
        manifest_sha256=input_hash(manifest), layout_version=LAYOUT_VERSION,
        renderer_version=RENDERER_VERSION, total_pages=manifest["total_pages"],
        manifest=manifest, pdf_data=pdf,
    )
    db.add(artifact)
    db.flush()
    return artifact, False


def get_artifact(db, paper_id, artifact_id):
    enabled()
    artifact = db.scalar(select(AnswerCardArtifact).where(
        AnswerCardArtifact.id == artifact_id, AnswerCardArtifact.paper_instance_id == paper_id))
    if artifact is None:
        raise AssessmentProblem("NOT_FOUND", "该试卷的答题卡产物不存在", 404)
    if input_hash(artifact.manifest) != artifact.manifest_sha256:
        raise AssessmentProblem("ARTIFACT_INTEGRITY_ERROR", "答题卡页清单校验失败，请联系维护人员")
    return artifact


def artifact_view(artifact, *, reused=False):
    prefix = f"/api/v1/internal/stage6/paper-instances/{artifact.paper_instance_id}/answer-card-artifacts/{artifact.id}"
    return {"id": artifact.id, "paper_instance_id": artifact.paper_instance_id,
            "artifact_code": artifact.artifact_code, "revision_no": artifact.revision_no,
            "layout_version": artifact.layout_version, "renderer_version": artifact.renderer_version,
            "source_sha256": artifact.source_sha256, "pdf_sha256": artifact.pdf_sha256,
            "manifest_sha256": artifact.manifest_sha256, "total_pages": artifact.total_pages,
            "created_at": artifact.created_at, "pdf_url": prefix + "/pdf", "manifest_url": prefix + "/manifest",
            "reused": reused}


def artifact_pdf(artifact):
    data = artifact.pdf_data
    if hashlib.sha256(data).hexdigest() != artifact.pdf_sha256:
        raise AssessmentProblem("ARTIFACT_INTEGRITY_ERROR", "答题卡PDF校验失败，请联系维护人员")
    return data


def artifact_page_svg(artifact, page_no):
    if type(page_no) is not int or not 1 <= page_no <= artifact.total_pages:
        raise AssessmentProblem("NOT_FOUND", "答题卡页面不存在", 404)
    with fitz.open(stream=artifact_pdf(artifact), filetype="pdf") as document:
        if len(document) != artifact.total_pages:
            raise AssessmentProblem("ARTIFACT_INTEGRITY_ERROR", "答题卡页数校验失败")
        # Glyph paths preserve exact geometry without requiring browser fonts.
        return document[page_no - 1].get_svg_image(text_as_path=True).encode("utf-8")
