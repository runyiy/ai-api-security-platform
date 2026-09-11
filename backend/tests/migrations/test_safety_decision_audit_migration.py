from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.db.session import engine
from tests.research_intake_fixtures import VERIFICATION_TABLES, INTENT_TABLES


HEAD = "e6a8c0d2f4b7"
LATEST = "6a94cbd3f825"
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
            *INTENT_TABLES, *VERIFICATION_TABLES,
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
                "asset_enrollment_decisions",
                "endpoint_resource_bindings",
                "resource_access_assertions",
                "finding_evidence_records",
                "finding_evidence_excerpts",
                "finding_evidence_fingerprints",
                "finding_evidence_similarities",
                "finding_evidence_retention_bindings",
                "research_contexts",
                "research_observation_controls",
                "research_observation_events",
                "research_observation_preparations",
                "research_observation_records",
                "research_observation_payloads",
                "research_subject_versions",
                "research_knowledge_versions",
                "research_knowledge_events",
                "research_knowledge_audit",
                "research_rule_validations",
                "research_rule_feedback",
                "research_rule_feedback_reviews",
                "research_context_versions",
                "research_target_associations",
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
