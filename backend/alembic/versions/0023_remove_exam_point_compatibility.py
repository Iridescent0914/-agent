"""Remove the legacy exam-point compatibility layer.

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import json
from typing import Any

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


JSON_KEY_RENAMES = {
    "main_exam_point": "main_knowledge_point",
    "related_exam_points": "related_knowledge_points",
    "new_exam_point_codes": "new_knowledge_point_codes",
    "review_exam_point_codes": "review_knowledge_point_codes",
    "max_exam_point_ratio": "max_knowledge_point_ratio",
    "exam_point_code": "knowledge_point_code",
    "exam_point_name": "knowledge_point_name",
    "exam_point_codes": "knowledge_point_codes",
    "capped_exam_point_codes": "capped_knowledge_point_codes",
    "max_per_exam_point": "max_per_knowledge_point",
    "selected_per_exam_point": "selected_per_knowledge_point",
    "exam_point_snapshot": "knowledge_point_snapshot",
}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _rename_key(key: str) -> str:
    if key in JSON_KEY_RENAMES:
        return JSON_KEY_RENAMES[key]
    return key.replace("exam_point", "knowledge_point")


def _rewrite_json(
    value: Any,
    code_map: dict[str, str],
    name_map: dict[str, str],
    *,
    parent_key: str | None = None,
) -> Any:
    if isinstance(value, list):
        rewritten = [
            _rewrite_json(item, code_map, name_map, parent_key=parent_key)
            for item in value
        ]
        if parent_key and parent_key.endswith("_codes"):
            deduplicated: list[Any] = []
            for item in rewritten:
                if item not in deduplicated:
                    deduplicated.append(item)
            return deduplicated
        return rewritten
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        source_code = value.get("knowledge_point_code", value.get("exam_point_code"))
        mapped_source_code = code_map.get(source_code, source_code) if isinstance(source_code, str) else None
        for old_key, child in value.items():
            new_key = _rename_key(str(old_key))
            new_child = _rewrite_json(child, code_map, name_map, parent_key=new_key)
            if new_key == "knowledge_point_code" and isinstance(new_child, str):
                mapped = code_map.get(new_child, new_child)
                output[new_key] = mapped
                if "knowledge_point_name" in value or "exam_point_name" in value:
                    output["knowledge_point_name"] = name_map.get(mapped)
                continue
            if new_key == "knowledge_point_name" and mapped_source_code:
                output[new_key] = name_map.get(mapped_source_code, new_child)
                continue
            if new_key in {"selected_per_knowledge_point", "counts_per_knowledge_point"} and isinstance(new_child, dict):
                merged: dict[str, Any] = defaultdict(int)
                for code, count in new_child.items():
                    mapped = code_map.get(code, code)
                    if isinstance(count, (int, float)):
                        merged[mapped] += count
                    else:
                        merged[mapped] = count
                output[new_key] = dict(merged)
                continue
            output[new_key] = new_child
        return output
    if isinstance(value, str):
        if parent_key and (parent_key.endswith("_code") or parent_key.endswith("_codes")):
            return code_map.get(value, value)
        if value == "exam_point_cap":
            return "knowledge_point_cap"
        if value == "missing_main_exam_point":
            return "missing_main_knowledge_point"
        return value.replace("考点", "知识点")
    return value


def _rewrite_json_column(
    connection: sa.engine.Connection,
    table: str,
    column: str,
    code_map: dict[str, str],
    name_map: dict[str, str],
) -> None:
    rows = connection.execute(
        sa.text(f'SELECT id::text AS id, "{column}" AS payload FROM "{table}" WHERE "{column}" IS NOT NULL')
    ).mappings()
    for row in rows:
        rewritten = _rewrite_json(row["payload"], code_map, name_map)
        if rewritten == row["payload"]:
            continue
        connection.execute(
            sa.text(
                f'UPDATE "{table}" SET "{column}" = CAST(:payload AS jsonb) '
                'WHERE id = CAST(:id AS uuid)'
            ),
            {"id": row["id"], "payload": _dump(rewritten)},
        )


def _drop_snapshot_guards() -> None:
    op.execute("DROP TRIGGER IF EXISTS paper_item_published_guard ON paper_instance_items")
    op.execute("DROP TRIGGER IF EXISTS paper_instance_published_guard ON paper_instances")


def _restore_snapshot_guards() -> None:
    op.execute(
        """
        CREATE TRIGGER paper_instance_published_guard
        BEFORE UPDATE OR DELETE ON paper_instances
        FOR EACH ROW EXECUTE FUNCTION reject_published_paper_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER paper_item_published_guard
        BEFORE INSERT OR UPDATE OR DELETE ON paper_instance_items
        FOR EACH ROW EXECUTE FUNCTION reject_published_paper_item_mutation()
        """
    )


def _create_knowledge_masteries() -> None:
    op.create_table(
        "student_masteries",
        sa.Column(
            "student_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("students.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "knowledge_point_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_points.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("mastery", sa.Numeric(5, 4), nullable=False),
        sa.Column("confidence_level", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correct_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recent_wrong_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_practiced_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("mastery BETWEEN 0 AND 1", name="ck_mastery_range"),
        sa.CheckConstraint(
            "confidence_level IN ('low', 'medium', 'high')",
            name="ck_confidence_level",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND correct_count >= 0 AND recent_wrong_count >= 0",
            name="ck_mastery_counts_nonnegative",
        ),
    )


def upgrade() -> None:
    connection = op.get_bind()
    taxonomy = connection.execute(sa.text("""
        SELECT ep.code AS old_code, kp.code AS knowledge_code, kp.name AS knowledge_name
        FROM exam_points ep
        JOIN knowledge_points kp ON kp.id = ep.knowledge_point_id
    """)).mappings().all()
    code_map = {row["old_code"]: row["knowledge_code"] for row in taxonomy}
    name_map = {row["knowledge_code"]: row["knowledge_name"] for row in taxonomy}

    # Consolidate any legacy sub-point mastery rows that now target one formal KP.
    op.execute("""
        CREATE TEMPORARY TABLE student_masteries_by_knowledge AS
        SELECT DISTINCT ON (sm.student_id, ep.knowledge_point_id)
               sm.student_id,
               ep.knowledge_point_id,
               sm.mastery,
               sm.confidence_level,
               sm.attempt_count,
               sm.correct_count,
               sm.recent_wrong_count,
               sm.last_practiced_at,
               sm.updated_at
        FROM student_masteries sm
        JOIN exam_points ep ON ep.id = sm.exam_point_id
        ORDER BY sm.student_id, ep.knowledge_point_id, sm.updated_at DESC
    """)
    op.drop_table("student_masteries")
    _create_knowledge_masteries()
    op.execute("""
        INSERT INTO student_masteries (
            student_id, knowledge_point_id, mastery, confidence_level,
            attempt_count, correct_count, recent_wrong_count,
            last_practiced_at, updated_at
        )
        SELECT student_id, knowledge_point_id, mastery, confidence_level,
               attempt_count, correct_count, recent_wrong_count,
               last_practiced_at, updated_at
        FROM student_masteries_by_knowledge
    """)

    _drop_snapshot_guards()

    # Historical and draft papers now carry the same direct formal-KP snapshot.
    op.execute("""
        UPDATE paper_instance_items pii
        SET exam_point_snapshot = jsonb_build_object('id', kp.code, 'name', kp.name)
        FROM questions q
        JOIN knowledge_points kp ON kp.id = q.main_knowledge_point_id
        WHERE q.id = pii.question_id
    """)
    op.alter_column(
        "paper_instance_items",
        "exam_point_snapshot",
        new_column_name="knowledge_point_snapshot",
        existing_type=postgresql.JSONB(),
        existing_nullable=False,
    )

    for table, column in (
        ("questions", "import_issues"),
        ("questions", "review_history"),
        ("assignments", "teaching_scope"),
        ("assignments", "generation_config"),
        ("paper_instances", "recommendation_audit"),
        ("paper_instances", "edit_history"),
        ("paper_instance_items", "selection_audit"),
    ):
        _rewrite_json_column(connection, table, column, code_map, name_map)

    _restore_snapshot_guards()

    op.drop_table("question_exam_points")
    op.drop_table("exam_points")

    # This was only the parent container for the discarded Word-derived points.
    op.execute("""
        DELETE FROM knowledge_points
        WHERE code = 'math.g6.rational.add_sub'
          AND NOT EXISTS (
              SELECT 1 FROM questions q WHERE q.main_knowledge_point_id = knowledge_points.id
          )
    """)


def downgrade() -> None:
    connection = op.get_bind()
    op.create_table(
        "exam_points",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(180), nullable=False, unique=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column(
            "knowledge_point_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_points.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("sort_order >= 0", name="ck_exam_points_sort_order"),
    )
    op.execute("""
        INSERT INTO exam_points (id, code, name, knowledge_point_id, sort_order)
        SELECT id, code, name, id, sort_order FROM knowledge_points
    """)
    op.create_table(
        "question_exam_points",
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "exam_point_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exam_points.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("relation_type", sa.String(16), nullable=False),
        sa.CheckConstraint("relation_type IN ('main', 'related')", name="ck_qep_relation_type"),
    )
    op.create_index(
        "uq_question_one_main_exam_point",
        "question_exam_points",
        ["question_id"],
        unique=True,
        postgresql_where=sa.text("relation_type = 'main'"),
    )
    op.create_index(
        "ix_qep_exam_question",
        "question_exam_points",
        ["exam_point_id", "question_id"],
    )
    op.execute("""
        INSERT INTO question_exam_points (question_id, exam_point_id, relation_type)
        SELECT id, main_knowledge_point_id, 'main' FROM questions
    """)

    op.execute("""
        CREATE TEMPORARY TABLE student_masteries_by_exam AS
        SELECT student_id, knowledge_point_id AS exam_point_id, mastery,
               confidence_level, attempt_count, correct_count,
               recent_wrong_count, last_practiced_at, updated_at
        FROM student_masteries
    """)
    op.drop_table("student_masteries")
    op.create_table(
        "student_masteries",
        sa.Column("student_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("students.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("exam_point_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("exam_points.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("mastery", sa.Numeric(5, 4), nullable=False),
        sa.Column("confidence_level", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correct_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recent_wrong_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_practiced_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("mastery BETWEEN 0 AND 1", name="ck_mastery_range"),
        sa.CheckConstraint("confidence_level IN ('low', 'medium', 'high')", name="ck_confidence_level"),
        sa.CheckConstraint("attempt_count >= 0 AND correct_count >= 0 AND recent_wrong_count >= 0", name="ck_mastery_counts_nonnegative"),
    )
    op.execute("""
        INSERT INTO student_masteries
        SELECT * FROM student_masteries_by_exam
    """)

    _drop_snapshot_guards()
    op.alter_column(
        "paper_instance_items",
        "knowledge_point_snapshot",
        new_column_name="exam_point_snapshot",
        existing_type=postgresql.JSONB(),
        existing_nullable=False,
    )
    op.execute("""
        UPDATE paper_instance_items
        SET exam_point_snapshot = jsonb_build_object(
            'main', exam_point_snapshot,
            'related', '[]'::jsonb
        )
    """)
    _restore_snapshot_guards()

