from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.db.session import engine


REVISION = "b3d5f7a9c1e4"
PARENT = "a1c3e5f7b9d2"
TABLE = "rate_reservation_states"


def current_revision() -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_m8_01_migration_round_trip() -> None:
    config = Config("alembic.ini")
    try:
        command.upgrade(config, REVISION)
        assert current_revision() == REVISION
        assert TABLE in inspect(engine).get_table_names()
        assert {
            column["name"] for column in inspect(engine).get_columns(TABLE)
        } == {"key", "next_allowed_at"}

        command.downgrade(config, PARENT)
        assert current_revision() == PARENT
        assert TABLE not in inspect(engine).get_table_names()

        command.upgrade(config, REVISION)
        assert current_revision() == REVISION
        assert TABLE in inspect(engine).get_table_names()
    finally:
        command.upgrade(config, "head")
