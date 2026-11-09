from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.literacy_catalog import normalize_literacy_label
from app.services.answer_units import (
    AUTO_MATCH_TYPES,
    is_canonical_answer_structure,
)
from app.services.question_pool_quality import pool_answer_issues
from app.services.profile_evidence import PROFILE_QUESTION_TYPES

from app.models import KnowledgePoint, Question


@dataclass
class ImportStats:
    inserted: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    unchanged: dict[str, int] = field(default_factory=dict)

    def bump(self, bucket: str, entity: str) -> None:
        target = getattr(self, bucket)
        target[entity] = target.get(entity, 0) + 1

    def as_dict(self) -> dict[str, dict[str, int]]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "unchanged": self.unchanged,
        }


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_inputs(
    questions: list[dict[str, Any]],
    tree: dict[str, Any],
    *,
    expected_count: int | None = None,
    require_machine_ready: bool = True,
) -> None:
    if expected_count is not None and len(questions) != expected_count:
        raise ValueError(f"expected {expected_count} question groups, got {len(questions)}")
    qids = [item.get("original_qid") for item in questions]
    if None in qids or len(set(qids)) != len(qids):
        raise ValueError("Seed original_qid values must be present and unique")
    for item in questions:
        missing = [
            key
            for key in (
                "chapter",
                "grade",
                "question_type",
                "difficulty",
                "question_text",
                "main_knowledge_point",
                "core_literacy",
                "is_required",
            )
            if item.get(key) is None
        ]
        if missing:
            raise ValueError(f"{item['original_qid']} missing fields: {', '.join(missing)}")
        if item.get("question_type") not in {
            "choice", "fill_blank", "calculation", "solution", "true_false", "unknown"
        }:
            raise ValueError(f"{item['original_qid']} has unsupported question_type")
        if require_machine_ready and item.get("grading_readiness") != "machine_ready":
            raise ValueError(f"{item['original_qid']} is not machine_ready")
        if not is_canonical_answer_structure(item.get("correct_answer")):
            raise ValueError(
                f"{item['original_qid']} does not use canonical subquestion/blank answers"
            )
        if item.get("grading_readiness") == "machine_ready":
            if item.get("question_type") not in PROFILE_QUESTION_TYPES:
                raise ValueError(f"{item['original_qid']} has a question type outside the automatic pool")
            quality_issues = pool_answer_issues(item.get("correct_answer"), item.get("answer_match_type"), item.get("answer_normalization_rule", {}))
            if quality_issues:
                raise ValueError(f"{item['original_qid']} cannot enter the automatic pool: {quality_issues}")
            if item.get("correct_answer") is None or item.get("answer_match_type") is None:
                raise ValueError(f"{item['original_qid']} lacks a structured answer")
            answer_items = item["correct_answer"]["items"]
            if any(
                part.get("answer") is None
                or part.get("answer_match_type") not in AUTO_MATCH_TYPES
                for part in answer_items
            ):
                raise ValueError(
                    f"{item['original_qid']} has non-deterministic answer units"
                )
            try:
                item["core_literacy"] = normalize_literacy_label(
                    item.get("core_literacy")
                )
            except ValueError as exc:
                raise ValueError(
                    f"{item['original_qid']} has invalid core_literacy: {exc}"
                ) from exc
        if item.get("source_review_status") != "reviewed":
            raise ValueError(f"{item['original_qid']} is not source-reviewed")
    if not tree.get("knowledge_points"):
        raise ValueError("Knowledge tree has no knowledge_points")


def _set_fields(instance: object, values: dict[str, Any]) -> bool:
    changed = False
    for key, value in values.items():
        if getattr(instance, key) != value:
            setattr(instance, key, value)
            changed = True
    return changed


def _get_or_create_kp(
    session: Session,
    stats: ImportStats,
    *,
    code: str,
    name: str,
    chapter_code: str,
    chapter_name: str,
    section: str | None = None,
    sort_order: int = 0,
    mastery_level: str | None = None,
    core_competencies: list[str] | None = None,
    curriculum_basis: str | None = None,
) -> KnowledgePoint:
    instance = session.scalar(select(KnowledgePoint).where(KnowledgePoint.code == code))
    values = {
        "name": name,
        "chapter_code": chapter_code,
        "chapter_name": chapter_name,
        "section": section,
        "sort_order": sort_order,
        "mastery_level": mastery_level,
        "core_competencies": core_competencies or [],
        "curriculum_basis": curriculum_basis,
    }
    if instance is None:
        instance = KnowledgePoint(code=code, **values)
        session.add(instance)
        session.flush()
        stats.bump("inserted", "knowledge_points")
    elif _set_fields(instance, values):
        stats.bump("updated", "knowledge_points")
    else:
        stats.bump("unchanged", "knowledge_points")
    return instance


def _import_knowledge_tree(
    session: Session, stats: ImportStats, tree: dict[str, Any]
) -> dict[str, KnowledgePoint]:
    chapter = tree["chapter"]
    kp_by_code: dict[str, KnowledgePoint] = {}
    for kp_data in tree["knowledge_points"]:
        kp = _get_or_create_kp(
            session,
            stats,
            code=kp_data["id"],
            name=kp_data["name"],
            chapter_code=chapter["id"],
            chapter_name=chapter["name"],
            section=kp_data.get("section"),
            sort_order=kp_data.get("order_no", 0),
            mastery_level=kp_data.get("mastery_level"),
            core_competencies=kp_data.get("core_competencies", []),
            curriculum_basis=kp_data.get("curriculum_basis"),
        )
        kp_by_code[kp.code] = kp
    return kp_by_code


def _question_values(item: dict[str, Any], kp: KnowledgePoint) -> dict[str, Any]:
    return {
        "schema_version": item["schema_version"],
        "chapter_code": item["chapter"]["id"],
        "chapter_name": item["chapter"]["name"],
        "grade": item["grade"],
        "section": item["section"],
        "question_type": item["question_type"],
        "difficulty": item["difficulty"],
        "main_knowledge_point_id": kp.id,
        "question_text": item["question_text"],
        "options": item.get("options"),
        "correct_answer": item.get("correct_answer"),
        "reference_answer_raw": item.get("reference_answer_raw", ""),
        "answer_match_type": item.get("answer_match_type"),
        "answer_normalization_rule": item.get("answer_normalization_rule", {}),
        "solution": item.get("solution"),
        "wrong_analysis": item.get("wrong_analysis"),
        "estimated_score_rate": item.get("estimated_score_rate"),
        "core_literacy": item["core_literacy"],
        "is_required": item["is_required"],
        "has_image": bool(item.get("has_image")),
        "has_equation": bool(item.get("has_equation")),
        "assets": item.get("assets", []),
        "source_file": item["source_file"],
        "source_question_number": item["source_question_number"],
        "source_review_status": item["source_review_status"],
        "grading_readiness": item["grading_readiness"],
        "import_status": item.get("import_status", "parsed"),
        "import_issues": item.get("import_issues", []),
    }


def _preserve_reviewed_answer(question: Question, values: dict[str, Any]) -> None:
    if not question.review_history:
        return
    # Teacher-reviewed answers are operational data. Re-running the source
    # importer may refresh the stem/metadata, but must not undo a saved review
    # draft or a confirmed promotion.
    for field in (
        "correct_answer",
        "answer_match_type",
        "answer_normalization_rule",
        "grading_readiness",
        "import_issues",
    ):
        values.pop(field)


def _import_questions(
    session: Session,
    stats: ImportStats,
    questions: list[dict[str, Any]],
    kp_by_code: dict[str, KnowledgePoint],
) -> list[Question]:
    imported: list[Question] = []
    for item in questions:
        kp_data = item["main_knowledge_point"]
        kp = kp_by_code.get(kp_data["id"])
        if kp is None:
            kp = _get_or_create_kp(
                session,
                stats,
                code=kp_data["id"],
                name=kp_data["name"],
                chapter_code=item["chapter"]["id"],
                chapter_name=item["chapter"]["name"],
                section=item["section"],
                mastery_level=kp_data.get("mastery_level"),
                core_competencies=kp_data.get("core_competencies", []),
                curriculum_basis=kp_data.get("curriculum_basis"),
            )
            kp_by_code[kp.code] = kp

        question = session.scalar(
            select(Question).where(Question.original_qid == item["original_qid"])
        )
        values = _question_values(item, kp)
        if question is not None:
            _preserve_reviewed_answer(question, values)
        if question is None:
            question = Question(original_qid=item["original_qid"], **values)
            session.add(question)
            session.flush()
            stats.bump("inserted", "questions")
        elif _set_fields(question, values):
            stats.bump("updated", "questions")
        else:
            stats.bump("unchanged", "questions")

        imported.append(question)
    session.flush()
    return imported


def import_seed_data(
    session: Session,
    questions_path: Path,
    knowledge_tree_path: Path,
    catalog_path: Path | None = None,
    *,
    min_seed_questions: int = 30,
    max_seed_questions: int = 50,
    expected_catalog_count: int | None = None,
) -> ImportStats:
    seed_data = _load_json(questions_path)
    catalog_data = _load_json(catalog_path) if catalog_path is not None else seed_data
    tree_data = _load_json(knowledge_tree_path)
    _validate_inputs(seed_data, tree_data)
    if not min_seed_questions <= len(seed_data) <= max_seed_questions:
        raise ValueError(
            f"expected {min_seed_questions}-{max_seed_questions} seed question groups, "
            f"got {len(seed_data)}"
        )
    _validate_inputs(
        catalog_data,
        tree_data,
        expected_count=expected_catalog_count,
        require_machine_ready=False,
    )
    catalog_qids = {item["original_qid"] for item in catalog_data}
    missing_seed_qids = [
        item["original_qid"] for item in seed_data
        if item["original_qid"] not in catalog_qids
    ]
    if missing_seed_qids:
        raise ValueError(f"catalog is missing seed questions: {missing_seed_qids}")
    stats = ImportStats()
    try:
        kp_by_code = _import_knowledge_tree(session, stats, tree_data)
        _import_questions(session, stats, catalog_data, kp_by_code)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return stats
