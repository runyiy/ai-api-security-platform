from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.db.session import engine


HEAD_REVISION = "d6f4a3b2c1e0"
PARENT_REVISION = "c4b8219e6d72"


def current_revision() -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_credential_binding_migration_round_trip() -> None:
    alembic_config = Config("alembic.ini")

    try:
        assert current_revision() == HEAD_REVISION
        tables_at_head = set(inspect(engine).get_table_names())
        assert "credential_bindings" in tables_at_head

        command.downgrade(alembic_config, PARENT_REVISION)

        assert current_revision() == PARENT_REVISION
        tables_after_downgrade = set(inspect(engine).get_table_names())
        assert tables_at_head - tables_after_downgrade == {
            "credential_bindings"
        }
        assert tables_after_downgrade - tables_at_head == set()

        command.upgrade(alembic_config, "head")

        assert current_revision() == HEAD_REVISION
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == tables_at_head
        assert {
            column["name"]
            for column in inspector.get_columns("credential_bindings")
        } == {
            "id",
            "test_identity_id",
            "auth_type",
            "source_type",
            "is_active",
            "created_at",
            "updated_at",
        }
        assert inspector.get_indexes("credential_bindings") == [
            {
                "name": "ix_credential_bindings_test_identity_id",
                "unique": False,
                "column_names": ["test_identity_id"],
                "include_columns": [],
                "dialect_options": {
                    "postgresql_include": [],
                },
            }
        ]
        assert inspector.get_foreign_keys("credential_bindings") == [
            {
                "name": (
                    "fk_credential_bindings_test_identity_id_"
                    "test_identities"
                ),
                "constrained_columns": ["test_identity_id"],
                "referred_schema": None,
                "referred_table": "test_identities",
                "referred_columns": ["id"],
                "options": {"ondelete": "RESTRICT"},
                "comment": None,
            }
        ]
    finally:
        command.upgrade(alembic_config, "head")
