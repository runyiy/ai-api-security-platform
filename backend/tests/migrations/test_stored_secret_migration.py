from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.db.session import engine


HEAD_REVISION = "f3b5d7e9a1c2"
STORED_SECRET_REVISION = "e7a5b4c3d2f1"
PARENT_REVISION = "d6f4a3b2c1e0"


def current_revision() -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_stored_secret_migration_round_trip() -> None:
    alembic_config = Config("alembic.ini")

    try:
        assert current_revision() == HEAD_REVISION
        command.downgrade(alembic_config, STORED_SECRET_REVISION)

        assert current_revision() == STORED_SECRET_REVISION
        tables_at_head = set(inspect(engine).get_table_names())
        assert "credential_secret_versions" in tables_at_head

        command.downgrade(alembic_config, PARENT_REVISION)

        assert current_revision() == PARENT_REVISION
        tables_after_downgrade = set(inspect(engine).get_table_names())
        assert tables_at_head - tables_after_downgrade == {
            "credential_secret_versions"
        }
        assert tables_after_downgrade - tables_at_head == set()

        command.upgrade(alembic_config, STORED_SECRET_REVISION)

        assert current_revision() == STORED_SECRET_REVISION
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == tables_at_head
        assert {
            column["name"]
            for column in inspector.get_columns("credential_secret_versions")
        } == {
            "id",
            "credential_binding_id",
            "encrypted_envelope",
            "envelope_version",
            "key_version",
            "created_at",
        }
        assert inspector.get_indexes("credential_secret_versions") == [
            {
                "name": "ix_credential_secret_versions_credential_binding_id",
                "unique": False,
                "column_names": ["credential_binding_id"],
                "include_columns": [],
                "dialect_options": {"postgresql_include": []},
            }
        ]
        assert inspector.get_foreign_keys(
            "credential_secret_versions"
        ) == [
            {
                "name": "fk_secret_versions_credential_binding_id",
                "constrained_columns": ["credential_binding_id"],
                "referred_schema": None,
                "referred_table": "credential_bindings",
                "referred_columns": ["id"],
                "options": {"ondelete": "RESTRICT"},
                "comment": None,
            }
        ]
    finally:
        command.upgrade(alembic_config, "head")
