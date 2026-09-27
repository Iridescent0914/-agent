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
        invalid_lifecycle = 0
        invalid_superseded_by = 0
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
                elif paper.status == 'published' and (
                    parent.invalidated_at is None or parent.superseded_by_instance_id != paper.id
                ):
                    invalid_supersedes += 1
            lifecycle_values = (
                paper.invalidation_type, paper.invalidation_reason, paper.invalidated_by
            )
            if (paper.invalidated_at is None) != all(value is None for value in lifecycle_values):
                invalid_lifecycle += 1
            if paper.superseded_by_instance_id:
                child = db.get(PaperInstance, paper.superseded_by_instance_id)
                if child is None or child.supersedes_instance_id != paper.id or child.status != 'published':
                    invalid_superseded_by += 1
        published_identity = sum(
            paper.status == "published" and (not paper.instance_code or paper.published_at is None)
            for paper in papers
        )
        missing_recommendation = sum(paper.recommendation_audit is None for paper in papers)
        active_version_duplicates = int(db.scalar(
            select(func.count()).select_from(
                select(PaperInstance.assignment_id, PaperInstance.student_id)
                .where(
                    PaperInstance.status == 'published',
                    PaperInstance.invalidated_at.is_(None),
                )
                .group_by(PaperInstance.assignment_id, PaperInstance.student_id)
                .having(func.count(PaperInstance.id) > 1)
                .subquery()
            )
        ) or 0)
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
            'invalid lifecycle metadata': invalid_lifecycle,
            'invalid superseded-by links': invalid_superseded_by,
            'multiple active published versions': active_version_duplicates,
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
            'invalid_lifecycle_metadata': invalid_lifecycle,
            'invalid_superseded_by_links': invalid_superseded_by,
            'active_published_instances': sum(
                paper.status == 'published' and paper.invalidated_at is None for paper in papers
            ),
            'invalidated_instances': sum(paper.invalidated_at is not None for paper in papers),
            'multiple_active_published_versions': active_version_duplicates,
            "published_without_code_or_time": published_identity,
            "validation_errors": len(errors),
            "errors": errors,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if errors:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
