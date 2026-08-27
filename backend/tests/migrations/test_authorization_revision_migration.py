from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.db.session import engine


HEAD_REVISION = "c3e5a7b9d1f2"
PARENT_REVISION = "e7a5b4c3d2f1"


def current_revision() -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_authorization_revision_migration_round_trip() -> None:
    alembic_config = Config("alembic.ini")

    try:
        assert current_revision() == HEAD_REVISION
        tables_at_head = set(inspect(engine).get_table_names())
        assert "authorization_revisions" in tables_at_head

        command.downgrade(alembic_config, PARENT_REVISION)

        assert current_revision() == PARENT_REVISION
        tables_after_downgrade = set(inspect(engine).get_table_names())
        assert tables_at_head - tables_after_downgrade == {
            "authorization_revisions"
        }
        assert tables_after_downgrade - tables_at_head == set()

        command.upgrade(alembic_config, "head")

        assert current_revision() == HEAD_REVISION
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == tables_at_head
        assert {
            column["name"]
            for column in inspector.get_columns("authorization_revisions")
        } == {
            "id",
            "authorization_profile_id",
            "revision_number",
            "lifecycle_state",
            "name",
            "program_name",
            "program_url",
            "authorization_type",
            "authorization_reference",
            "valid_from",
            "valid_until",
            "automation_allowed",
            "max_requests_per_second",
            "allow_get",
            "allow_post",
            "allow_patch",
            "allow_put",
            "allow_delete",
            "require_human_execution_approval",
            "notes",
            "created_at",
        }
        assert inspector.get_indexes("authorization_revisions") == [
            {
                "name": "ix_authorization_revisions_authorization_profile_id",
                "unique": False,
                "column_names": ["authorization_profile_id"],
                "include_columns": [],
                "dialect_options": {"postgresql_include": []},
            },
            {
                "name": "uq_authorization_revisions_one_active_per_profile",
                "unique": True,
                "column_names": ["authorization_profile_id"],
                "include_columns": [],
                "dialect_options": {
                    "postgresql_include": [],
                    "postgresql_where": "((lifecycle_state)::text = 'active'::text)",
                },
            },
            {
                "name": "uq_authorization_revisions_profile_revision_number",
                "unique": True,
                "column_names": [
                    "authorization_profile_id",
                    "revision_number",
                ],
                "duplicates_constraint": (
                    "uq_authorization_revisions_profile_revision_number"
                ),
                "include_columns": [],
                "dialect_options": {"postgresql_include": []},
            },
        ]
        assert inspector.get_foreign_keys("authorization_revisions") == [
            {
                "name": "fk_authorization_revisions_profile_id",
                "constrained_columns": ["authorization_profile_id"],
                "referred_schema": None,
                "referred_table": "authorization_profiles",
                "referred_columns": ["id"],
                "options": {"ondelete": "RESTRICT"},
                "comment": None,
            }
        ]
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints(
                "authorization_revisions"
            )
        } == {"uq_authorization_revisions_profile_revision_number"}
        assert {
            constraint["name"]
            for constraint in inspector.get_check_constraints(
                "authorization_revisions"
            )
        } == {
            "ck_authorization_revisions_lifecycle_state",
            "ck_authorization_revisions_max_requests_per_second_positive",
            "ck_authorization_revisions_revision_number_positive",
            "ck_authorization_revisions_validity_window",
        }
    finally:
        command.upgrade(alembic_config, "head")
