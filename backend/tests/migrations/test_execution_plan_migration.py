from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.db.session import engine


HEAD = "d5f7a9c1e3b5"
LATEST = "c5e7a9b1d3f6"
PARENT = "c3e5a7b9d1f2"


def current_revision() -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_m5_01_migration_round_trip() -> None:
    config = Config("alembic.ini")
    try:
        assert current_revision() == LATEST
        inspector = inspect(engine)
        pre_m5_tables = set(inspector.get_table_names()) - {
            "execution_plans",
            "plan_actions",
            "safety_decision_records",
            "execution_plan_approval_records",
            "rate_reservation_states",
            "execution_plan_claims",
        }
        assert {"execution_plans", "plan_actions"}.issubset(
            inspector.get_table_names()
        )
        plan_fks = inspector.get_foreign_keys("execution_plans")
        assert {fk["referred_table"] for fk in plan_fks} == {
            "targets",
            "authorization_revisions",
            "test_identities",
            "credential_bindings",
        }
        assert all(fk["options"] == {"ondelete": "RESTRICT"} for fk in plan_fks)
        action_fks = inspector.get_foreign_keys("plan_actions")
        assert {fk["referred_table"] for fk in action_fks} == {
            "execution_plans",
            "test_cases",
            "resources",
        }
        assert all(fk["options"] == {"ondelete": "RESTRICT"} for fk in action_fks)

        command.downgrade(config, PARENT)
        assert current_revision() == PARENT
        inspector = inspect(engine)
        assert "execution_plans" not in inspector.get_table_names()
        assert "plan_actions" not in inspector.get_table_names()
        assert pre_m5_tables.issubset(inspector.get_table_names())
        assert "authorization_revisions" in inspector.get_table_names()
        assert "authorization_revision_id" in {
            column["name"] for column in inspector.get_columns("test_runs")
        }

        command.upgrade(config, "head")
        assert current_revision() == LATEST
    finally:
        command.upgrade(config, "head")
