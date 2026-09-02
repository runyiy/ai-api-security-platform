from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.session import engine


REVISION = "b4d6f8a0c2e5"
PARENT = "a2c4e6f8b0d3"
TABLES = {
    "asset_candidate_dns_validations",
    "asset_candidate_dns_cname_hops",
    "asset_candidate_dns_addresses",
}


def test_dns_validation_migration_schema_constraints_and_round_trip() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_revision(REVISION).down_revision == PARENT
    assert scripts.get_heads() == ["c6e8a0b2d4f7"]
    profile_id = revision_id = rule_id = evaluation_id = None
    try:
        command.downgrade(config, PARENT)
        parent_tables = set(inspect(engine).get_table_names())
        assert TABLES.isdisjoint(parent_tables)
        with engine.begin() as db:
            rule_count = db.scalar(text("SELECT count(*) FROM asset_hostname_rules"))
            evaluation_count = db.scalar(text(
                "SELECT count(*) FROM asset_candidate_evaluations"
            ))
        command.upgrade(config, REVISION)
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) - parent_tables == TABLES
        assert {column["name"] for column in inspector.get_columns(
            "asset_candidate_dns_validations"
        )} == {
            "id", "asset_candidate_evaluation_id", "authorization_revision_id",
            "decision_code", "normalized_hostname", "terminal_hostname", "created_at",
        }
        assert {item["name"] for item in inspector.get_check_constraints(
            "asset_candidate_dns_validations"
        )} == {"ck_asset_candidate_dns_validations_decision_code"}
        assert {item["name"] for item in inspector.get_check_constraints(
            "asset_candidate_dns_cname_hops"
        )} == {"ck_asset_candidate_dns_cname_hops_ordinal"}
        assert {item["name"] for item in inspector.get_check_constraints(
            "asset_candidate_dns_addresses"
        )} == {
            "ck_asset_candidate_dns_addresses_ordinal",
            "ck_asset_candidate_dns_addresses_category",
        }
        assert {item["name"] for item in inspector.get_unique_constraints(
            "asset_candidate_dns_cname_hops"
        )} == {"uq_asset_candidate_dns_cname_hops_validation_ordinal"}
        assert {item["name"] for item in inspector.get_unique_constraints(
            "asset_candidate_dns_addresses"
        )} == {"uq_asset_candidate_dns_addresses_validation_ordinal"}
        for table, expected in {
            "asset_candidate_dns_validations": {
                "asset_candidate_evaluations", "authorization_revisions"
            },
            "asset_candidate_dns_cname_hops": {"asset_candidate_dns_validations"},
            "asset_candidate_dns_addresses": {"asset_candidate_dns_validations"},
        }.items():
            foreign_keys = inspector.get_foreign_keys(table)
            assert {item["referred_table"] for item in foreign_keys} == expected
            assert all(item["options"] == {"ondelete": "RESTRICT"}
                       for item in foreign_keys)
        with engine.begin() as db:
            assert all(db.scalar(text(f"SELECT count(*) FROM {table}")) == 0
                       for table in TABLES)
            assert db.scalar(text("SELECT count(*) FROM asset_hostname_rules")) == rule_count
            assert db.scalar(text(
                "SELECT count(*) FROM asset_candidate_evaluations"
            )) == evaluation_count
            profile_id = db.scalar(text("""
                INSERT INTO authorization_profiles
                    (name, program_name, authorization_type, max_requests_per_second)
                VALUES ('m10-04 migration', 'm10', 'self_owned', 1.0) RETURNING id
            """))
            revision_id = db.scalar(text("""
                INSERT INTO authorization_revisions
                    (authorization_profile_id, revision_number, lifecycle_state,
                     name, program_name, authorization_type, max_requests_per_second)
                VALUES (:profile, 1, 'active', 'm10', 'm10', 'self_owned', 1.0)
                RETURNING id
            """), {"profile": profile_id})
            rule_id = db.scalar(text("""
                INSERT INTO asset_hostname_rules
                    (authorization_revision_id, rule_type, hostname_pattern)
                VALUES (:revision, 'include', '*.example.test') RETURNING id
            """), {"revision": revision_id})
            evaluation_id = db.scalar(text("""
                INSERT INTO asset_candidate_evaluations
                    (authorization_revision_id, normalized_hostname, decision_code,
                     matched_include_rule_id, source_type)
                VALUES (:revision, 'api.example.test', 'asset_candidate_included',
                        :rule, 'operator_supplied') RETURNING id
            """), {"revision": revision_id, "rule": rule_id})
            validation_id = db.scalar(text("""
                INSERT INTO asset_candidate_dns_validations
                    (asset_candidate_evaluation_id, authorization_revision_id,
                     decision_code, normalized_hostname, terminal_hostname)
                VALUES (:evaluation, :revision, 'asset_candidate_dns_public_only',
                        'api.example.test', 'edge.example.test') RETURNING id
            """), {"evaluation": evaluation_id, "revision": revision_id})
            db.execute(text("""
                INSERT INTO asset_candidate_dns_cname_hops
                    (dns_validation_id, ordinal, hostname)
                VALUES (:validation, 1, 'edge.example.test')
            """), {"validation": validation_id})
            db.execute(text("""
                INSERT INTO asset_candidate_dns_addresses
                    (dns_validation_id, ordinal, address, category)
                VALUES (:validation, 1, '8.8.8.8', 'public')
            """), {"validation": validation_id})
        for statement, identifier in (
            ("DELETE FROM asset_candidate_dns_validations WHERE id = :id", validation_id),
            ("DELETE FROM asset_candidate_evaluations WHERE id = :id", evaluation_id),
            ("DELETE FROM authorization_revisions WHERE id = :id", revision_id),
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as db:
                    db.execute(text(statement), {"id": identifier})
        for table, column, value in (
            ("asset_candidate_dns_validations", "decision_code", "invalid"),
            ("asset_candidate_dns_cname_hops", "ordinal", 0),
            ("asset_candidate_dns_addresses", "ordinal", 17),
            ("asset_candidate_dns_addresses", "category", "metadata"),
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as db:
                    if table == "asset_candidate_dns_validations":
                        db.execute(text("""
                            INSERT INTO asset_candidate_dns_validations
                                (asset_candidate_evaluation_id, authorization_revision_id,
                                 decision_code, normalized_hostname)
                            VALUES (:evaluation, :revision, :value, 'api.example.test')
                        """), {"evaluation": evaluation_id, "revision": revision_id,
                               "value": value})
                    elif table == "asset_candidate_dns_cname_hops":
                        db.execute(text("""
                            INSERT INTO asset_candidate_dns_cname_hops
                                (dns_validation_id, ordinal, hostname)
                            VALUES (:validation, :value, 'bad.example.test')
                        """), {"validation": validation_id, "value": value})
                    else:
                        db.execute(text(f"""
                            INSERT INTO asset_candidate_dns_addresses
                                (dns_validation_id, ordinal, address, category)
                            VALUES (:validation, :ordinal, '8.8.4.4', :category)
                        """), {
                            "validation": validation_id,
                            "ordinal": value if column == "ordinal" else 2,
                            "category": value if column == "category" else "public",
                        })
        command.downgrade(config, PARENT)
        assert TABLES.isdisjoint(inspect(engine).get_table_names())
    finally:
        command.upgrade(config, "head")
        with engine.begin() as db:
            if evaluation_id is not None:
                db.execute(text("""
                    DELETE FROM asset_candidate_dns_addresses WHERE dns_validation_id IN
                    (SELECT id FROM asset_candidate_dns_validations
                     WHERE asset_candidate_evaluation_id = :evaluation)
                """), {"evaluation": evaluation_id})
                db.execute(text("""
                    DELETE FROM asset_candidate_dns_cname_hops WHERE dns_validation_id IN
                    (SELECT id FROM asset_candidate_dns_validations
                     WHERE asset_candidate_evaluation_id = :evaluation)
                """), {"evaluation": evaluation_id})
                db.execute(text(
                    "DELETE FROM asset_candidate_dns_validations "
                    "WHERE asset_candidate_evaluation_id = :evaluation"
                ), {"evaluation": evaluation_id})
                db.execute(text(
                    "DELETE FROM asset_candidate_evaluations WHERE id = :evaluation"
                ), {"evaluation": evaluation_id})
            if rule_id is not None:
                db.execute(text("DELETE FROM asset_hostname_rules WHERE id = :id"), {"id": rule_id})
            if revision_id is not None:
                db.execute(text("DELETE FROM authorization_revisions WHERE id = :id"), {"id": revision_id})
            if profile_id is not None:
                db.execute(text("DELETE FROM authorization_profiles WHERE id = :id"), {"id": profile_id})
