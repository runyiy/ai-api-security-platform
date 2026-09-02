from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.db.session import engine


HEAD = "e6a8c0d2f4b7"
LATEST = "b4d6f8a0c2e5"
PARENT = "d5f7a9c1e3b5"


def current_revision() -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_m5_03_migration_round_trip_preserves_preexisting_schema() -> None:
    config = Config("alembic.ini")
    try:
        assert current_revision() == LATEST
        inspector = inspect(engine)
        tables_before = set(inspector.get_table_names())
        later_tables = {
            "execution_plan_approval_records",
            "rate_reservation_states",
            "execution_plan_claims",
            "execution_plan_progress",
            "execution_plan_cancellations",
            "network_global_control",
            "network_disabled_targets",
                "openapi_import_records",
                "asset_hostname_rules",
                "asset_candidate_evaluations",
                "asset_candidate_dns_validations",
                "asset_candidate_dns_cname_hops",
                "asset_candidate_dns_addresses",
            }
        preexisting = tables_before - {"safety_decision_records", *later_tables}
        assert "safety_decision_records" in tables_before
        assert {
            foreign_key["referred_table"]
            for foreign_key in inspector.get_foreign_keys(
                "safety_decision_records"
            )
        } == {
            "targets",
            "authorization_revisions",
            "execution_plans",
            "plan_actions",
            "test_cases",
            "test_runs",
        }

        command.downgrade(config, PARENT)
        assert current_revision() == PARENT
        tables_without_audit = set(inspect(engine).get_table_names())
        assert "safety_decision_records" not in tables_without_audit
        assert preexisting == tables_without_audit

        command.upgrade(config, HEAD)
        assert current_revision() == HEAD
        assert set(inspect(engine).get_table_names()) == tables_before - later_tables
    finally:
        command.upgrade(config, "head")
