from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import DateTime, inspect, text

from app.db.session import engine


REVISION = "a2c4e6f8b0d2"
PARENT = "f1b3d5e7a9c1"


def current_revision():
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_postgres_network_control_schema_and_round_trip() -> None:
    config = Config("alembic.ini")
    try:
        command.downgrade(config, PARENT)
        tables = inspect(engine).get_table_names()
        assert "network_global_control" not in tables
        assert "network_disabled_targets" not in tables
        command.upgrade(config, REVISION)
        assert current_revision() == REVISION
        inspector = inspect(engine)
        global_columns = {
            column["name"]: column
            for column in inspector.get_columns("network_global_control")
        }
        assert set(global_columns) == {
            "id", "global_enabled", "maximum_concurrency", "updated_at"
        }
        assert global_columns["updated_at"]["type"].timezone is True
        assert inspector.get_pk_constraint("network_global_control")[
            "constrained_columns"
        ] == ["id"]
        disabled_columns = {
            column["name"]: column
            for column in inspector.get_columns("network_disabled_targets")
        }
        assert set(disabled_columns) == {"target_id", "disabled_at"}
        assert isinstance(disabled_columns["disabled_at"]["type"], DateTime)
        assert disabled_columns["disabled_at"]["type"].timezone is True
        assert inspector.get_pk_constraint("network_disabled_targets")[
            "constrained_columns"
        ] == ["target_id"]
        foreign_key = inspector.get_foreign_keys("network_disabled_targets")[0]
        assert foreign_key["referred_table"] == "targets"
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id, global_enabled, maximum_concurrency "
                    "FROM network_global_control"
                )
            ).all()
        assert rows == [(1, True, 4)]
    finally:
        command.upgrade(config, "head")
