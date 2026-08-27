from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.db.session import engine


HEAD = "c3e5a7b9d1f2"
LATEST = "e6a8c0d2f4b7"
PARENT = "b2d4f6a8c0e1"


def current_revision() -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_m4_03_migration_round_trip() -> None:
    config = Config("alembic.ini")
    try:
        assert current_revision() == LATEST
        command.downgrade(config, HEAD)
        assert current_revision() == HEAD
        inspector = inspect(engine)
        columns_at_head = {
            column["name"] for column in inspector.get_columns("test_runs")
        }
        assert "authorization_revision_id" in columns_at_head
        assert any(
            foreign_key["constrained_columns"] == ["authorization_revision_id"]
            and foreign_key["referred_table"] == "authorization_revisions"
            and foreign_key["referred_columns"] == ["id"]
            and foreign_key["options"] == {"ondelete": "RESTRICT"}
            for foreign_key in inspector.get_foreign_keys("test_runs")
        )
        assert "ix_test_runs_authorization_revision_id" in {
            index["name"] for index in inspector.get_indexes("test_runs")
        }

        command.downgrade(config, PARENT)
        assert current_revision() == PARENT
        inspector = inspect(engine)
        assert {
            column["name"] for column in inspector.get_columns("test_runs")
        } == columns_at_head - {"authorization_revision_id"}
        assert "authorization_revisions" in inspector.get_table_names()

        command.upgrade(config, HEAD)
        assert current_revision() == HEAD
    finally:
        command.upgrade(config, "head")
