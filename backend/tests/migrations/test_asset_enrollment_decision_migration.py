from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.session import engine


REVISION = "c6e8a0b2d4f7"
PARENT = "b4d6f8a0c2e5"
TABLE = "asset_enrollment_decisions"


def test_asset_enrollment_decision_migration_constraints_and_round_trip() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_revision(REVISION).down_revision == PARENT
    assert scripts.get_heads() == ["f3b5d7e9a1c2"]
    profile_id = revision_id = rule_id = evaluation_id = validation_id = None
    try:
        command.downgrade(config, PARENT)
        parent_tables = set(inspect(engine).get_table_names())
        assert TABLE not in parent_tables
        with engine.begin() as db:
            historical_counts = {
                table: db.scalar(text(f"SELECT count(*) FROM {table}"))
                for table in (
                    "asset_hostname_rules",
                    "asset_candidate_evaluations",
                    "asset_candidate_dns_validations",
                )
            }

        command.upgrade(config, REVISION)
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) - parent_tables == {TABLE}
        assert {column["name"] for column in inspector.get_columns(TABLE)} == {
            "id", "asset_candidate_dns_validation_id",
            "authorization_revision_id", "decision", "normalized_hostname",
            "reason_code", "note", "created_at",
        }
        columns = {column["name"]: column for column in inspector.get_columns(TABLE)}
        assert all(columns[name]["nullable"] is False for name in (
            "id", "asset_candidate_dns_validation_id", "authorization_revision_id",
            "decision", "normalized_hostname", "created_at",
        ))
        assert columns["reason_code"]["nullable"] is True
        assert columns["note"]["nullable"] is True
        assert str(columns["normalized_hostname"]["type"]) == "VARCHAR(253)"
        assert str(columns["reason_code"]["type"]) == "VARCHAR(40)"
        assert str(columns["note"]["type"]) == "VARCHAR(500)"
        assert {item["name"] for item in inspector.get_check_constraints(TABLE)} == {
            "ck_asset_enrollment_decisions_decision",
            "ck_asset_enrollment_decisions_reason_code",
        }
        foreign_keys = inspector.get_foreign_keys(TABLE)
        assert {item["referred_table"] for item in foreign_keys} == {
            "asset_candidate_dns_validations", "authorization_revisions",
        }
        assert all(item["options"] == {"ondelete": "RESTRICT"}
                   for item in foreign_keys)

        with engine.begin() as db:
            assert db.scalar(text(f"SELECT count(*) FROM {TABLE}")) == 0
            for table, count in historical_counts.items():
                assert db.scalar(text(f"SELECT count(*) FROM {table}")) == count
            profile_id = db.scalar(text("""
                INSERT INTO authorization_profiles
                    (name, program_name, authorization_type,
                     max_requests_per_second)
                VALUES ('m10-05 migration', 'm10', 'self_owned', 1.0)
                RETURNING id
            """))
            revision_id = db.scalar(text("""
                INSERT INTO authorization_revisions
                    (authorization_profile_id, revision_number, lifecycle_state,
                     name, program_name, authorization_type,
                     max_requests_per_second)
                VALUES (:profile, 1, 'superseded', 'm10', 'm10',
                        'self_owned', 1.0)
                RETURNING id
            """), {"profile": profile_id})
            rule_id = db.scalar(text("""
                INSERT INTO asset_hostname_rules
                    (authorization_revision_id, rule_type, hostname_pattern)
                VALUES (:revision, 'include', '*.example.test') RETURNING id
            """), {"revision": revision_id})
            evaluation_id = db.scalar(text("""
                INSERT INTO asset_candidate_evaluations
                    (authorization_revision_id, normalized_hostname,
                     decision_code, matched_include_rule_id, source_type)
                VALUES (:revision, 'api.example.test',
                        'asset_candidate_included', :rule,
                        'operator_supplied') RETURNING id
            """), {"revision": revision_id, "rule": rule_id})
            validation_id = db.scalar(text("""
                INSERT INTO asset_candidate_dns_validations
                    (asset_candidate_evaluation_id, authorization_revision_id,
                     decision_code, normalized_hostname)
                VALUES (:evaluation, :revision,
                        'asset_candidate_dns_public_only', 'api.example.test')
                RETURNING id
            """), {"evaluation": evaluation_id, "revision": revision_id})
            decision_id = db.scalar(text("""
                INSERT INTO asset_enrollment_decisions
                    (asset_candidate_dns_validation_id,
                     authorization_revision_id, decision,
                     normalized_hostname, reason_code, note)
                VALUES (:validation, :revision, 'approved',
                        'api.example.test', 'manual_review', 'reviewed')
                RETURNING id
            """), {"validation": validation_id, "revision": revision_id})
            row = db.execute(text("""
                SELECT decision, normalized_hostname, reason_code, note
                FROM asset_enrollment_decisions WHERE id = :id
            """), {"id": decision_id}).one()
            assert tuple(row) == (
                "approved", "api.example.test", "manual_review", "reviewed"
            )

        for statement, identifier in (
            ("DELETE FROM asset_candidate_dns_validations WHERE id = :id", validation_id),
            ("DELETE FROM authorization_revisions WHERE id = :id", revision_id),
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as db:
                    db.execute(text(statement), {"id": identifier})
        for column, value in (
            ("decision", "automatic"),
            ("reason_code", "execute_now"),
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as db:
                    db.execute(text(f"""
                        INSERT INTO asset_enrollment_decisions
                            (asset_candidate_dns_validation_id,
                             authorization_revision_id, decision,
                             normalized_hostname, reason_code)
                        VALUES (:validation, :revision, :decision,
                                'api.example.test', :reason)
                    """), {
                        "validation": validation_id,
                        "revision": revision_id,
                        "decision": value if column == "decision" else "rejected",
                        "reason": value if column == "reason_code" else None,
                    })

        command.downgrade(config, PARENT)
        assert TABLE not in inspect(engine).get_table_names()
    finally:
        command.upgrade(config, "head")
        with engine.begin() as db:
            if validation_id is not None:
                db.execute(text("""
                    DELETE FROM asset_enrollment_decisions
                    WHERE asset_candidate_dns_validation_id = :validation
                """), {"validation": validation_id})
                db.execute(text("""
                    DELETE FROM asset_candidate_dns_validations
                    WHERE id = :validation
                """), {"validation": validation_id})
            if evaluation_id is not None:
                db.execute(text("""
                    DELETE FROM asset_candidate_evaluations WHERE id = :id
                """), {"id": evaluation_id})
            if rule_id is not None:
                db.execute(text(
                    "DELETE FROM asset_hostname_rules WHERE id = :id"
                ), {"id": rule_id})
            if revision_id is not None:
                db.execute(text(
                    "DELETE FROM authorization_revisions WHERE id = :id"
                ), {"id": revision_id})
            if profile_id is not None:
                db.execute(text(
                    "DELETE FROM authorization_profiles WHERE id = :id"
                ), {"id": profile_id})
