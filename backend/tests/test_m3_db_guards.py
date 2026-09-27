from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.database import SessionLocal
from app.models import PaperInstance, PaperInstanceItem


@pytest.mark.parametrize("operation", ["update", "delete", "insert"])
def test_published_paper_items_cannot_be_mutated(operation: str) -> None:
    with SessionLocal() as session:
        item = session.scalar(
            select(PaperInstanceItem)
            .join(PaperInstance, PaperInstance.id == PaperInstanceItem.paper_instance_id)
            .where(PaperInstance.status == "published")
            .limit(1)
        )
        assert item is not None
        statements = {
            "update": (
                "UPDATE paper_instance_items SET order_no = order_no WHERE id = :id",
                {"id": item.id},
            ),
            "delete": ("DELETE FROM paper_instance_items WHERE id = :id", {"id": item.id}),
            "insert": (
                """INSERT INTO paper_instance_items (
                    id, paper_instance_id, question_id, order_no, question_snapshot,
                    options_snapshot, answer_snapshot, answer_rule_snapshot,
                    exam_point_snapshot, created_at
                ) SELECT gen_random_uuid(), paper_instance_id, question_id, 9999,
                    question_snapshot, options_snapshot, answer_snapshot,
                    answer_rule_snapshot, exam_point_snapshot, now()
                  FROM paper_instance_items WHERE id = :id""",
                {"id": item.id},
            ),
        }
        statement, params = statements[operation]
        with pytest.raises(DBAPIError):
            session.execute(text(statement), params)
            session.flush()
        session.rollback()

