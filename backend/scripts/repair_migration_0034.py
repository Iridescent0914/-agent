"""Repair the old S4/answer-card Alembic revision collision before upgrading.

The two branches once both used revision 0034. Only the database shape can tell
which migration actually ran. This command changes the version marker solely
when the S4 column exists and the answer-card table does not.
"""

from sqlalchemy import inspect, text

from app.core.database import engine


def migration_action(version: str | None, has_s4_column: bool,
                     has_answer_card_table: bool) -> str:
    if version != "0034":
        return "unchanged"
    if has_s4_column and not has_answer_card_table:
        return "relabel_s4_to_0033"
    if not has_s4_column and not has_answer_card_table:
        raise ValueError("数据库版本为 0034，但两个 0034 迁移的对象均不存在")
    return "unchanged"


def main() -> None:
    with engine.begin() as connection:
        inspector = inspect(connection)
        if not inspector.has_table("alembic_version"):
            print("migration-0034: new database")
            return
        versions = connection.execute(text("SELECT version_num FROM alembic_version FOR UPDATE")).scalars().all()
        if len(versions) != 1:
            raise ValueError("数据库迁移版本不唯一，请人工核对")
        has_s4_column = (inspector.has_table("questions") and
                         any(column["name"] == "exact_fingerprint_v2"
                             for column in inspector.get_columns("questions")))
        action = migration_action(versions[0], has_s4_column,
                                  inspector.has_table("answer_card_artifacts"))
        if action == "relabel_s4_to_0033":
            connection.execute(text("UPDATE alembic_version SET version_num = '0033' WHERE version_num = '0034'"))
        print(f"migration-0034: {action}")


if __name__ == "__main__":
    main()
