from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import DateTime, inspect

from app.db.session import engine
from tests.services.test_plan_execution_integration import approved_plan


REVISION = "f1b3d5e7a9c1"
PARENT = "e9a1c3d5f7b9"
TABLE = "execution_plan_cancellations"


def current_revision() -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_cancellation_schema_and_round_trip(approved_plan) -> None:
    config = Config("alembic.ini")
    try:
        command.downgrade(config, PARENT)
        assert TABLE not in inspect(engine).get_table_names()
        command.upgrade(config, REVISION)
        assert current_revision() == REVISION
        inspector = inspect(engine)
        columns = {column["name"]: column for column in inspector.get_columns(TABLE)}
        assert set(columns) == {"execution_plan_id", "requested_at"}
        assert inspector.get_pk_constraint(TABLE)["constrained_columns"] == [
            "execution_plan_id"
        ]
        assert isinstance(columns["requested_at"]["type"], DateTime)
        assert columns["requested_at"]["type"].timezone is True
        foreign_keys = inspector.get_foreign_keys(TABLE)
        assert len(foreign_keys) == 1
        assert foreign_keys[0]["constrained_columns"] == ["execution_plan_id"]
        assert foreign_keys[0]["options"] == {"ondelete": "RESTRICT"}
    finally:
        command.upgrade(config, "head")
