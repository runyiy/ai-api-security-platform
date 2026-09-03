from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.session import engine


REVISION = "a2c4e6f8b0d3"
PARENT = "f0b2d4e6a8c1"


def test_asset_candidate_evaluation_migration_round_trip_and_constraints() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_revision(REVISION).down_revision == PARENT
    assert scripts.get_heads() == ["f3b5d7e9a1c2"]
    profile_id = revision_id = rule_id = None
    try:
        command.downgrade(config, PARENT)
        parent_tables = set(inspect(engine).get_table_names())
        assert "asset_candidate_evaluations" not in parent_tables
        with engine.begin() as db:
            rule_count = db.scalar(text("SELECT count(*) FROM asset_hostname_rules"))
        command.upgrade(config, REVISION)
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) - parent_tables == {
            "asset_candidate_evaluations"
        }
        columns = {item["name"]: item for item in inspector.get_columns(
            "asset_candidate_evaluations"
        )}
        assert set(columns) == {
            "id", "authorization_revision_id", "normalized_hostname",
            "decision_code", "matched_include_rule_id",
            "matched_exclude_rule_id", "source_type", "created_at",
        }
        assert columns["normalized_hostname"]["type"].length == 253
        assert columns["decision_code"]["type"].length == 32
        assert columns["source_type"]["type"].length == 32
        assert columns["matched_include_rule_id"]["nullable"] is True
        assert columns["matched_exclude_rule_id"]["nullable"] is True
        assert columns["created_at"]["default"] is not None
        assert {item["name"] for item in inspector.get_check_constraints(
            "asset_candidate_evaluations"
        )} == {
            "ck_asset_candidate_evaluations_decision_code",
            "ck_asset_candidate_evaluations_source_type",
        }
        fks = {item["name"]: item for item in inspector.get_foreign_keys(
            "asset_candidate_evaluations"
        )}
        assert len(fks) == 3
        assert all(item["options"] == {"ondelete": "RESTRICT"} for item in fks.values())
        with engine.begin() as db:
            assert db.scalar(text("SELECT count(*) FROM asset_candidate_evaluations")) == 0
            assert db.scalar(text("SELECT count(*) FROM asset_hostname_rules")) == rule_count
            profile_id = db.scalar(text("""
                INSERT INTO authorization_profiles
                    (name, program_name, authorization_type, max_requests_per_second)
                VALUES ('m10-02 migration', 'm10', 'self_owned', 1.0) RETURNING id
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
            db.execute(text("""
                INSERT INTO asset_candidate_evaluations
                    (authorization_revision_id, normalized_hostname, decision_code,
                     matched_include_rule_id, source_type)
                VALUES (:revision, 'api.example.test', 'asset_candidate_included',
                        :rule, 'operator_supplied')
            """), {"revision": revision_id, "rule": rule_id})
        for statement in (
            "DELETE FROM authorization_revisions WHERE id = :id",
            "DELETE FROM asset_hostname_rules WHERE id = :id",
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as db:
                    db.execute(text(statement), {"id": revision_id if "revisions" in statement else rule_id})
        for column, value in (
            ("decision_code", "asset_candidate_invalid"),
            ("source_type", "crawler"),
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as db:
                    db.execute(text(f"""
                        INSERT INTO asset_candidate_evaluations
                            (authorization_revision_id, normalized_hostname,
                             decision_code, source_type)
                        VALUES (:revision, 'bad.example.test',
                                :decision, :source)
                    """), {
                        "revision": revision_id,
                        "decision": value if column == "decision_code" else "asset_candidate_not_included",
                        "source": value if column == "source_type" else "operator_supplied",
                    })
        command.downgrade(config, PARENT)
        assert "asset_candidate_evaluations" not in inspect(engine).get_table_names()
    finally:
        command.upgrade(config, "head")
        with engine.begin() as db:
            if revision_id is not None:
                db.execute(text(
                    "DELETE FROM asset_candidate_evaluations WHERE authorization_revision_id = :id"
                ), {"id": revision_id})
            if rule_id is not None:
                db.execute(text("DELETE FROM asset_hostname_rules WHERE id = :id"), {"id": rule_id})
            if revision_id is not None:
                db.execute(text("DELETE FROM authorization_revisions WHERE id = :id"), {"id": revision_id})
            if profile_id is not None:
                db.execute(text("DELETE FROM authorization_profiles WHERE id = :id"), {"id": profile_id})
