from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.db.session import engine


REVISION = "c5e7a9b1d3f6"
PARENT = "b3d5f7a9c1e4"
TABLE = "execution_plan_claims"


def current_revision() -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_m8_02_migration_round_trip() -> None:
    config = Config("alembic.ini")
    try:
        command.upgrade(config, REVISION)
        assert current_revision() == REVISION
        inspector = inspect(engine)
        assert TABLE in inspector.get_table_names()
        assert inspector.get_foreign_keys(TABLE)[0]["options"] == {
            "ondelete": "RESTRICT"
        }
        command.downgrade(config, PARENT)
        assert current_revision() == PARENT
        assert TABLE not in inspect(engine).get_table_names()
        command.upgrade(config, REVISION)
        assert current_revision() == REVISION
    finally:
        command.upgrade(config, "head")
