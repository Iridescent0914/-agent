from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from sqlalchemy import text

from app.core.database import SessionLocal


PRESERVED_TABLES: tuple[str, ...] = (
    "alembic_version",
    "knowledge_points",
    "questions",
)

BUSINESS_TABLES: tuple[str, ...] = (
    "recognition_items",
    "recognition_runs",
    "attempt_revisions",
    "attempts",
    "submissions",
    "paper_instance_items",
    "paper_instances",
    "assignments",
    "student_knowledge_statuses",
    "student_literacy_scores",
    "student_masteries",
    "students",
    "school_classes",
)

SAFE_DATABASE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or clear all business/demo data while preserving the "
            "question catalog, knowledge points, and Alembic state."
        )
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="执行永久清理；不传时只显示数据库、目标表和数量",
    )
    parser.add_argument(
        "--expected-database",
        help="确认清理时必须精确匹配的数据库名，例如 math_agent",
    )
    return parser


def _public_tables(session: Any) -> list[str]:
    return list(session.execute(text(
        "SELECT tablename FROM pg_tables "
        "WHERE schemaname = 'public' ORDER BY tablename"
    )).scalars())


def _count(session: Any, table_name: str) -> int:
    if table_name not in {*PRESERVED_TABLES, *BUSINESS_TABLES}:
        raise ValueError(f"unapproved table name: {table_name}")
    return int(session.execute(text(f'SELECT count(*) FROM "{table_name}"')).scalar_one())


def _snapshot(session: Any) -> dict[str, Any]:
    return {
        "preserved": {table: _count(session, table) for table in PRESERVED_TABLES},
        "business": {table: _count(session, table) for table in BUSINESS_TABLES},
        "alembic_versions": list(session.execute(
            text("SELECT version_num FROM alembic_version ORDER BY version_num")
        ).scalars()),
    }


def _report(
    *, database: str, user: str, mode: str, snapshot: dict[str, Any],
    unclassified_tables: list[str],
) -> dict[str, Any]:
    return {
        "mode": mode,
        "database": database,
        "database_user": user,
        "preserved_tables": list(PRESERVED_TABLES),
        "business_tables_to_clear": list(BUSINESS_TABLES),
        "unclassified_public_tables": unclassified_tables,
        **snapshot,
    }


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if args.confirm and not args.expected_database:
        parser.error("--confirm requires --expected-database")
    if args.expected_database and not SAFE_DATABASE_NAME.fullmatch(args.expected_database):
        parser.error("--expected-database contains unsupported characters")

    try:
        with SessionLocal() as session:
            database, database_user = session.execute(text(
                "SELECT current_database(), current_user"
            )).one()
            public_tables = _public_tables(session)
            classified = {*PRESERVED_TABLES, *BUSINESS_TABLES}
            missing_tables = sorted(classified - set(public_tables))
            unclassified_tables = sorted(set(public_tables) - classified)
            if missing_tables:
                raise RuntimeError(
                    "required tables are missing; refusing to continue: "
                    + ", ".join(missing_tables)
                )

            before = _snapshot(session)
            print(json.dumps(_report(
                database=database,
                user=database_user,
                mode="preview" if not args.confirm else "preflight",
                snapshot=before,
                unclassified_tables=unclassified_tables,
            ), ensure_ascii=False, indent=2))

            if not args.confirm:
                session.rollback()
                return 0
            if database != args.expected_database:
                raise RuntimeError(
                    f"database mismatch: connected to {database!r}, "
                    f"expected {args.expected_database!r}"
                )
            if unclassified_tables:
                raise RuntimeError(
                    "unclassified public tables exist; update the cleanup allowlist first: "
                    + ", ".join(unclassified_tables)
                )

            preserved_sql = ", ".join(f'"{name}"' for name in PRESERVED_TABLES)
            business_sql = ", ".join(f'"{name}"' for name in BUSINESS_TABLES)
            session.execute(text(f"LOCK TABLE {preserved_sql} IN SHARE MODE"))
            session.execute(text(f"LOCK TABLE {business_sql} IN ACCESS EXCLUSIVE MODE"))

            # Re-read under locks so the verified pre/post state belongs to the
            # same transaction as the destructive operation.
            locked_before = _snapshot(session)
            session.execute(text(
                f"TRUNCATE TABLE {business_sql} RESTART IDENTITY"
            ))
            after = _snapshot(session)

            if after["preserved"] != locked_before["preserved"]:
                raise RuntimeError("preserved table counts changed; rolling back")
            if after["alembic_versions"] != locked_before["alembic_versions"]:
                raise RuntimeError("Alembic version changed; rolling back")
            nonempty = {
                table: count
                for table, count in after["business"].items()
                if count != 0
            }
            if nonempty:
                raise RuntimeError(
                    "business tables are not empty after cleanup; rolling back: "
                    + json.dumps(nonempty, ensure_ascii=False)
                )

            session.commit()
            print(json.dumps(_report(
                database=database,
                user=database_user,
                mode="completed",
                snapshot=after,
                unclassified_tables=[],
            ), ensure_ascii=False, indent=2))
            return 0
    except Exception as exc:
        print(f"Cleanup failed; transaction rolled back: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
