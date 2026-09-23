from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.database import SessionLocal
from app.models import PaperInstance


def test_published_paper_cannot_be_mutated() -> None:
    with SessionLocal() as session:
        paper_id = session.scalar(
            select(PaperInstance.id).where(PaperInstance.status == "published").limit(1)
        )
        assert paper_id is not None
        with pytest.raises(DBAPIError):
            session.execute(
                text("UPDATE paper_instances SET version_no = version_no + 1 WHERE id = :id"),
                {"id": paper_id},
            )
            session.flush()
        session.rollback()
