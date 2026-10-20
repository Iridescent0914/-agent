from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Assignment,
    Attempt,
    AttemptRevision,
    KnowledgePoint,
    PaperInstance,
    PaperInstanceItem,
    Question,
    SchoolClass,
    Student,
    Submission,
)


DEMO_STUDENTS = [
    ("S001", "学生甲", "六年级 Demo 班"),
    ("S002", "学生乙", "六年级 Demo 班"),
    ("S003", "学生丙", "六年级 Demo 班"),
    ("S004", "学生丁", "六年级 Demo 班"),
    ("S005", "学生戊", "六年级 Demo 班"),
]


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
        if item.get("grading_readiness") == "machine_ready":
            if item.get("correct_answer") is None or item.get("answer_match_type") is None:
                raise ValueError(f"{item['original_qid']} lacks a structured answer")
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


def _import_students(session: Session, stats: ImportStats) -> list[Student]:
    result: list[Student] = []
    for code, name, class_name in DEMO_STUDENTS:
        school_class = session.scalar(select(SchoolClass).where(SchoolClass.name == class_name))
        if school_class is None:
            school_class = SchoolClass(name=class_name)
            session.add(school_class)
            session.flush()
        student = session.scalar(select(Student).where(Student.student_code == code))
        values = {
            "display_name": name,
            "class_id": school_class.id,
            "class_name": class_name,
            "is_demo": True,
        }
        if student is None:
            student = Student(student_code=code, **values)
            session.add(student)
            session.flush()
            stats.bump("inserted", "students")
        elif _set_fields(student, values):
            stats.bump("updated", "students")
        else:
            stats.bump("unchanged", "students")
        result.append(student)
    return result


def _knowledge_point_snapshot(session: Session, question: Question) -> dict[str, Any]:
    row = session.execute(
        select(KnowledgePoint.code, KnowledgePoint.name).where(
            KnowledgePoint.id == question.main_knowledge_point_id
        )
    ).one()
    return {"id": row.code, "name": row.name}


def _answer_for_result(answer: Any, is_correct: bool) -> Any:
    if is_correct:
        return answer
    if isinstance(answer, dict) and answer.get("kind") == "group":
        return {
            "kind": "group",
            "items": [
                {"item_id": item["item_id"], "answer": "演示错答"}
                for item in answer.get("items", [])
            ],
        }
    return "演示错答"


def _demo_correctness(student_code: str, question: Question, batch_index: int) -> bool:
    knowledge_name = ""
    # The caller places the main knowledge-point name in this transient attribute.
    if hasattr(question, "_seed_knowledge_name"):
        knowledge_name = str(question._seed_knowledge_name)
    if student_code == "S001":
        return not any(token in knowledge_name for token in ("概念", "数轴", "相反数"))
    if student_code == "S002":
        return not any(token in knowledge_name for token in ("加减", "乘除", "混合", "乘方"))
    if student_code == "S003":
        return (question.source_question_number + batch_index) % 5 != 0
    if student_code == "S004":
        return question.difficulty < 4 or question.source_question_number % 4 != 0
    # S005 shows a recent decline in calculation-related review topics.
    if batch_index == 2 and any(token in knowledge_name for token in ("加减", "乘除", "混合")):
        return False
    return (question.source_question_number + batch_index) % 6 != 0


def _import_demo_history(
    session: Session,
    stats: ImportStats,
    students: list[Student],
    questions: list[Question],
) -> None:
    base_time = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    ordered = sorted(questions, key=lambda value: value.original_qid)
    chunks = [ordered[index : index + 10] for index in range(0, 30, 10)]

    for batch_index, chunk in enumerate(chunks):
        seed_key = f"M1-DEMO-HISTORY-{batch_index + 1}"
        assignment = session.scalar(select(Assignment).where(Assignment.seed_key == seed_key))
        if assignment is None:
            assignment = Assignment(
                seed_key=seed_key,
                title=f"有理数演示历史练习 {batch_index + 1}",
                class_id=students[0].class_id,
                chapter_code="math.g6.rational",
                teaching_scope={"source": "m1_demo_seed", "batch": batch_index + 1},
                assignment_type="historical_seed",
                created_at=base_time + timedelta(days=batch_index * 7),
            )
            session.add(assignment)
            session.flush()
            stats.bump("inserted", "assignments")
        else:
            stats.bump("unchanged", "assignments")

        for student in students:
            instance_code = f"DEMO-H{batch_index + 1}-{student.student_code}"
            existing = session.scalar(
                select(PaperInstance).where(PaperInstance.instance_code == instance_code)
            )
            if existing is not None:
                stats.bump("unchanged", "paper_instances")
                continue

            answered_at = base_time + timedelta(days=batch_index * 7, hours=int(student.student_code[-1]))
            paper = PaperInstance(
                assignment_id=assignment.id,
                student_id=student.id,
                version_no=1,
                status="draft",
                instance_code=instance_code,
                created_at=answered_at - timedelta(days=1),
            )
            session.add(paper)
            session.flush()

            item_pairs: list[tuple[PaperInstanceItem, Question, dict[str, Any]]] = []
            for order_no, question in enumerate(chunk, start=1):
                knowledge_snapshot = _knowledge_point_snapshot(session, question)
                question._seed_knowledge_name = knowledge_snapshot["name"]
                paper_item = PaperInstanceItem(
                    paper_instance_id=paper.id,
                    question_id=question.id,
                    order_no=order_no,
                    question_snapshot={
                        "original_qid": question.original_qid,
                        "question_text": question.question_text,
                        "question_type": question.question_type,
                        "difficulty": question.difficulty,
                        "grade": question.grade,
                        "chapter_code": question.chapter_code,
                        "chapter_name": question.chapter_name,
                        "section": question.section,
                        "core_literacy": question.core_literacy,
                        "is_required": question.is_required,
                        "assets": question.assets,
                    },
                    options_snapshot=question.options,
                    answer_snapshot=question.correct_answer,
                    answer_rule_snapshot={
                        "answer_match_type": question.answer_match_type,
                        "answer_normalization_rule": question.answer_normalization_rule,
                    },
                    knowledge_point_snapshot=knowledge_snapshot,
                )
                session.add(paper_item)
                session.flush()
                item_pairs.append((paper_item, question, knowledge_snapshot))

            paper.status = "published"
            paper.published_at = answered_at - timedelta(hours=2)
            session.flush()

            submission = Submission(
                student_id=student.id,
                paper_instance_id=paper.id,
                source="manual",
                idempotency_key=f"SEED-{instance_code}",
                attachments=[],
                submitted_at=answered_at,
            )
            session.add(submission)
            session.flush()

            for paper_item, question, _ in item_pairs:
                correct = _demo_correctness(student.student_code, question, batch_index)
                student_answer = _answer_for_result(question.correct_answer, correct)
                state = {
                    "student_answer": student_answer,
                    "normalized_answer": student_answer,
                    "grade_status": "teacher_confirmed",
                    "is_correct": correct,
                    "manual_override": False,
                    "revision_no": 1,
                }
                attempt = Attempt(
                    submission_id=submission.id,
                    student_id=student.id,
                    paper_instance_item_id=paper_item.id,
                    question_id=question.id,
                    student_answer=student_answer,
                    normalized_answer=student_answer,
                    grade_status="teacher_confirmed",
                    is_correct=correct,
                    manual_override=False,
                    revision_no=1,
                    answered_at=answered_at,
                    reviewed_at=answered_at,
                )
                session.add(attempt)
                session.flush()
                session.add(
                    AttemptRevision(
                        attempt_id=attempt.id,
                        revision_no=1,
                        before_state=None,
                        after_state=state,
                        operator_type="seed",
                        reason="M1 固定演示历史",
                        created_at=answered_at,
                    )
                )
                stats.bump("inserted", "attempts")
            stats.bump("inserted", "paper_instances")


def import_seed_data(
    session: Session,
    questions_path: Path,
    knowledge_tree_path: Path,
    catalog_path: Path | None = None,
    *,
    min_seed_questions: int = 30,
    max_seed_questions: int = 50,
    expected_catalog_count: int | None = None,
    include_demo_history: bool = True,
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
        catalog_questions = _import_questions(
            session, stats, catalog_data, kp_by_code
        )
        by_qid = {question.original_qid: question for question in catalog_questions}
        questions = [by_qid[item["original_qid"]] for item in seed_data]
        students = _import_students(session, stats)
        if include_demo_history:
            _import_demo_history(session, stats, students, questions)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return stats
