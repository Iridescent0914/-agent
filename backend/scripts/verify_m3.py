from __future__ import annotations

import json

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models import Assignment, PaperInstance, PaperInstanceItem


def main() -> None:
    with SessionLocal() as db:
        assignments = list(db.scalars(select(Assignment).where(Assignment.assignment_type == "practice")))
        assignment_ids = [assignment.id for assignment in assignments]
        papers = list(db.scalars(
            select(PaperInstance).where(PaperInstance.assignment_id.in_(assignment_ids))
        )) if assignment_ids else []
        paper_ids = [paper.id for paper in papers]
        items = list(db.scalars(
            select(PaperInstanceItem).where(PaperInstanceItem.paper_instance_id.in_(paper_ids))
        )) if paper_ids else []
        by_paper: dict = {}
        for item in items:
            by_paper.setdefault(item.paper_instance_id, []).append(item)

        duplicate_versions = int(db.scalar(
            select(func.count()).select_from(
                select(
                    PaperInstance.assignment_id, PaperInstance.student_id, PaperInstance.version_no
                ).group_by(
                    PaperInstance.assignment_id, PaperInstance.student_id, PaperInstance.version_no
                ).having(func.count(PaperInstance.id) > 1).subquery()
            )
        ) or 0)
        codes = [paper.instance_code for paper in papers if paper.instance_code]
        errors = []
        non_contiguous = 0
        duplicate_questions = 0
        incomplete = 0
        missing_selection = 0
        invalid_supersedes = 0
        for paper in papers:
            current = sorted(by_paper.get(paper.id, []), key=lambda item: item.order_no)
            if [item.order_no for item in current] != list(range(1, len(current) + 1)):
                non_contiguous += 1
            if len({item.question_id for item in current}) != len(current):
                duplicate_questions += 1
            incomplete += sum(
                not item.question_snapshot or not item.answer_rule_snapshot or
                not (item.exam_point_snapshot or {}).get("main") for item in current
            )
            missing_selection += sum(item.selection_audit is None for item in current)
            if paper.supersedes_instance_id:
                parent = db.get(PaperInstance, paper.supersedes_instance_id)
                if parent is None or parent.student_id != paper.student_id or parent.assignment_id != paper.assignment_id:
                    invalid_supersedes += 1
        published_identity = sum(
            paper.status == "published" and (not paper.instance_code or paper.published_at is None)
            for paper in papers
        )
        missing_recommendation = sum(paper.recommendation_audit is None for paper in papers)
        if len(codes) != len(set(codes)):
            errors.append("duplicate instance codes")
        for name, value in {
            "duplicate versions": duplicate_versions,
            "non-contiguous orders": non_contiguous,
            "duplicate paper questions": duplicate_questions,
            "incomplete snapshots": incomplete,
            "missing recommendation audits": missing_recommendation,
            "missing selection audits": missing_selection,
            "invalid supersedes links": invalid_supersedes,
            "published identity errors": published_identity,
        }.items():
            if value:
                errors.append(f"{name}: {value}")

        output = {
            "m3_assignments": len(assignments),
            "m3_paper_instances": len(papers),
            "draft_instances": sum(paper.status == "draft" for paper in papers),
            "published_instances": sum(paper.status == "published" for paper in papers),
            "unique_instance_codes": len(set(codes)),
            "student_assignment_version_duplicates": duplicate_versions,
            "duplicate_questions_within_paper": duplicate_questions,
            "non_contiguous_orders": non_contiguous,
            "incomplete_snapshots": incomplete,
            "missing_recommendation_audits": missing_recommendation,
            "missing_selection_audits": missing_selection,
            "invalid_supersedes_links": invalid_supersedes,
            "published_without_code_or_time": published_identity,
            "validation_errors": len(errors),
            "errors": errors,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if errors:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
