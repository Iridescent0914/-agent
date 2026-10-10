"""reconcile assignment class scope from paper students

Revision ID: 0016
Revises: 0015
"""

from alembic import op


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE assignments AS assignment
        SET class_id = inferred.class_id
        FROM (
            SELECT
                paper.assignment_id,
                MIN(student.class_id::text)::uuid AS class_id
            FROM paper_instances AS paper
            JOIN students AS student ON student.id = paper.student_id
            GROUP BY paper.assignment_id
            HAVING COUNT(DISTINCT student.class_id) = 1
               AND COUNT(*) FILTER (WHERE student.class_id IS NULL) = 0
        ) AS inferred
        WHERE assignment.id = inferred.assignment_id
        """
    )


def downgrade() -> None:
    # 0015 already gives every legacy assignment a safe Demo-class fallback.
    op.execute(
        "UPDATE assignments SET class_id = ("
        "SELECT id FROM school_classes WHERE name = '六年级 Demo 班' LIMIT 1"
        ")"
    )
