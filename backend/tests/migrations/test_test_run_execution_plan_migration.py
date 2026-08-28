from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.db.session import engine


REVISION = "d7f9b1c3e5a7"
PARENT = "c5e7a9b1d3f6"


def current_revision() -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_m8_03_migration_round_trip() -> None:
    config = Config("alembic.ini")
    try:
        command.downgrade(config, REVISION)
        command.upgrade(config, REVISION)
        assert current_revision() == REVISION
        inspector = inspect(engine)
        column = next(
            item
            for item in inspector.get_columns("test_runs")
            if item["name"] == "execution_plan_id"
        )
        assert column["nullable"] is True
        foreign_key = next(
            item
            for item in inspector.get_foreign_keys("test_runs")
            if item["constrained_columns"] == ["execution_plan_id"]
        )
        assert foreign_key["referred_table"] == "execution_plans"
        assert foreign_key["options"] == {"ondelete": "RESTRICT"}
        assert any(
            item["column_names"] == ["execution_plan_id"]
            for item in inspector.get_unique_constraints("test_runs")
        )
        command.downgrade(config, PARENT)
        assert current_revision() == PARENT
        assert "execution_plan_id" not in {
            item["name"] for item in inspect(engine).get_columns("test_runs")
        }
        command.upgrade(config, REVISION)
        assert current_revision() == REVISION
    finally:
        command.upgrade(config, "head")
