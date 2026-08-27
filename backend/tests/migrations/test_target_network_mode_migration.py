from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from app.db.session import engine


HEAD = "f7b9d1e3a5c8"
LATEST = "a1c3e5f7b9d2"
PARENT = "e6a8c0d2f4b7"


def current_revision() -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_m6_01_migration_round_trip_backfills_and_preserves_schema() -> None:
    config = Config("alembic.ini")
    target_name = f"m6-network-mode-{uuid4()}"
    target_id: int | None = None
    try:
        assert current_revision() == LATEST
        command.downgrade(config, HEAD)
        assert current_revision() == HEAD
        columns_at_head = {
            column["name"] for column in inspect(engine).get_columns("targets")
        }
        assert "network_mode" in columns_at_head

        command.downgrade(config, PARENT)
        assert current_revision() == PARENT
        columns_at_parent = {
            column["name"] for column in inspect(engine).get_columns("targets")
        }
        assert columns_at_head - columns_at_parent == {"network_mode"}

        with engine.begin() as connection:
            target_id = connection.execute(
                text(
                    "INSERT INTO targets "
                    "(name, base_url, environment, is_enabled) "
                    "VALUES (:name, :base_url, :environment, true) "
                    "RETURNING id"
                ),
                {
                    "name": target_name,
                    "base_url": "http://127.0.0.1",
                    "environment": "test",
                },
            ).scalar_one()

        command.upgrade(config, HEAD)
        assert current_revision() == HEAD
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT network_mode FROM targets WHERE id = :id"),
                {"id": target_id},
            ).scalar_one() == "private_local"

        command.downgrade(config, PARENT)
        assert current_revision() == PARENT
        assert {
            column["name"] for column in inspect(engine).get_columns("targets")
        } == columns_at_parent
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM targets WHERE id = :id"), {"id": target_id}
            )
            target_id = None
        command.upgrade(config, HEAD)
        assert current_revision() == HEAD
    finally:
        command.upgrade(config, "head")
        if target_id is not None:
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM targets WHERE id = :id"), {"id": target_id}
                )
