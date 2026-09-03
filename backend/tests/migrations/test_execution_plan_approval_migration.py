from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.db.session import engine


REVISION = "a1c3e5f7b9d2"
LATEST = "f3b5d7e9a1c2"
PARENT = "f7b9d1e3a5c8"
TABLE = "execution_plan_approval_records"


def current_revision() -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_m7_01_migration_round_trip() -> None:
    config = Config("alembic.ini")
    try:
        assert current_revision() == LATEST
        command.downgrade(config, REVISION)
        assert current_revision() == REVISION
        inspector = inspect(engine)
        tables_at_revision = set(inspector.get_table_names())
        assert TABLE in tables_at_revision
        foreign_keys = inspector.get_foreign_keys(TABLE)
        assert len(foreign_keys) == 1
        assert foreign_keys[0]["referred_table"] == "execution_plans"
        assert foreign_keys[0]["options"] == {"ondelete": "RESTRICT"}

        command.downgrade(config, PARENT)
        assert current_revision() == PARENT
        assert TABLE not in inspect(engine).get_table_names()

        command.upgrade(config, REVISION)
        assert current_revision() == REVISION
        assert set(inspect(engine).get_table_names()) == tables_at_revision
    finally:
        command.upgrade(config, "head")
