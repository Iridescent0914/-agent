"""Backfill formal section names for the first two chapter KPs.

Revision ID: 0026
Revises: 0025

The mapping follows SECTION_BY_KP in 上海市五四制初中数学知识图谱.html.
For the 21 pilot KPs, this canonical mapping replaces any conflicting
database section value. The HTML graph is the source of truth.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


SECTIONS_BY_CODE = {
    "math.g6.rational.kp.01_negative_quantity": "1.1 有理数的引入",
    "math.g6.rational.kp.02_meaning_classification": "1.1 有理数的引入",
    "math.g6.rational.kp.03_number_line": "1.1 有理数的引入",
    "math.g6.rational.kp.04_opposite_number": "1.1 有理数的引入",
    "math.g6.rational.kp.05_absolute_value": "1.1 有理数的引入",
    "math.g6.rational.kp.06_comparison": "1.1 有理数的引入",
    "math.g6.rational.kp.07_add_subtract": "1.2 有理数的加法与减法",
    "math.g6.rational.kp.08_multiply_divide": "1.3 有理数的乘法与除法",
    "math.g6.rational.kp.09_operation_laws": "1.2 有理数的加法与减法",
    "math.g6.rational.kp.10_powers": "1.4 有理数的乘方",
    "math.g6.rational.kp.11_mixed_operations": "1.5 有理数的混合运算",
    "math.g6.rational.kp.12_applications": "1.5 有理数的混合运算",
    "math.g6.rational.kp.13_negative_number_culture": "阅读材料：我国古代对负数的认识",
    "math.g6.expr.kp.01_literal_relations": "2.1 用字母表示数",
    "math.g6.expr.kp.02_expression_concept": "2.2 代数式与代数式的值",
    "math.g6.expr.kp.03_expression_quantity": "2.2 代数式与代数式的值",
    "math.g6.expr.kp.04_expression_value": "2.2 代数式与代数式的值",
    "math.g6.expr.kp.05_linear_term": "2.3 一次式",
    "math.g6.expr.kp.06_combine_like_terms": "2.3 一次式",
    "math.g6.expr.kp.07_remove_parentheses": "2.3 一次式",
    "math.g6.expr.kp.08_divisibility_patterns": "阅读材料：能被 3、5、9 整除的数的特征",
}


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT code, section FROM knowledge_points "
            "WHERE code IN :codes FOR UPDATE"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": list(SECTIONS_BY_CODE)},
    ).all()
    for code, current_section in rows:
        expected_section = SECTIONS_BY_CODE[code]
        if current_section != expected_section:
            connection.execute(
                sa.text(
                    "UPDATE knowledge_points SET section = :section WHERE code = :code"
                ),
                {"section": expected_section, "code": code},
            )


def downgrade() -> None:
    # This correction is irreversible without a record of which pre-existing
    # values were empty. Retain the validated section names on downgrade.
    pass
