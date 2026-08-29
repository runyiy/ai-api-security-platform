from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.db.session import engine


LATEST_REVISION = "c4e6a8b0d2f4"
HEAD_REVISION = "b2d4f6a8c0e1"
PARENT_REVISION = "f8c6d5e4b3a2"


def current_revision() -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_m4_02_migration_round_trip_preserves_m4_01_schema() -> None:
    alembic_config = Config("alembic.ini")

    try:
        assert current_revision() == LATEST_REVISION
        command.downgrade(alembic_config, HEAD_REVISION)
        assert current_revision() == HEAD_REVISION
        inspector = inspect(engine)
        tables_at_head = set(inspector.get_table_names())
        revision_columns_at_head = {
            column["name"]
            for column in inspector.get_columns("authorization_revisions")
        }
        target_columns_at_head = {
            column["name"] for column in inspector.get_columns("targets")
        }
        assert "authorization_revision_id" in target_columns_at_head
        assert any(
            foreign_key["constrained_columns"] == ["authorization_revision_id"]
            and foreign_key["referred_table"] == "authorization_revisions"
            and foreign_key["referred_columns"] == ["id"]
            and foreign_key["options"] == {"ondelete": "RESTRICT"}
            for foreign_key in inspector.get_foreign_keys("targets")
        )
        assert "ix_targets_authorization_revision_id" in {
            index["name"] for index in inspector.get_indexes("targets")
        }
        active_index = next(
            index
            for index in inspector.get_indexes("authorization_revisions")
            if index["name"]
            == "uq_authorization_revisions_one_active_per_profile"
        )
        assert active_index["unique"] is True
        assert active_index["column_names"] == ["authorization_profile_id"]
        assert "lifecycle_state" in str(
            active_index["dialect_options"]["postgresql_where"]
        )
        assert "active" in str(
            active_index["dialect_options"]["postgresql_where"]
        )

        command.downgrade(alembic_config, PARENT_REVISION)

        assert current_revision() == PARENT_REVISION
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == tables_at_head
        assert {
            column["name"]
            for column in inspector.get_columns("authorization_revisions")
        } == revision_columns_at_head
        assert {
            column["name"] for column in inspector.get_columns("targets")
        } == target_columns_at_head - {"authorization_revision_id"}
        assert "ix_targets_authorization_revision_id" not in {
            index["name"] for index in inspector.get_indexes("targets")
        }
        assert "uq_authorization_revisions_one_active_per_profile" not in {
            index["name"]
            for index in inspector.get_indexes("authorization_revisions")
        }

        command.upgrade(alembic_config, HEAD_REVISION)
        assert current_revision() == HEAD_REVISION
    finally:
        command.upgrade(alembic_config, "head")
