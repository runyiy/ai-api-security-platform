from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.session import engine


REVISION = "f0b2d4e6a8c1"
PARENT = "e9a1c3f5b7d9"


def current_revision() -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_asset_hostname_rule_migration_schema_restrict_and_round_trip() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_revision(REVISION).down_revision == PARENT
    assert scripts.get_heads() == ["d0f2a4c6e8b1"]
    profile_id = revision_id = rule_id = None
    try:
        command.downgrade(config, PARENT)
        parent_tables = set(inspect(engine).get_table_names())
        assert "asset_hostname_rules" not in parent_tables
        command.upgrade(config, REVISION)
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) - parent_tables == {
            "asset_hostname_rules"
        }
        assert parent_tables - set(inspector.get_table_names()) == set()
        columns = {column["name"]: column for column in inspector.get_columns(
            "asset_hostname_rules"
        )}
        assert set(columns) == {
            "id",
            "authorization_revision_id",
            "rule_type",
            "hostname_pattern",
            "created_at",
        }
        assert columns["rule_type"]["type"].length == 10
        assert columns["hostname_pattern"]["type"].length == 255
        assert columns["created_at"]["nullable"] is False
        assert columns["created_at"]["default"] is not None
        assert {item["name"] for item in inspector.get_check_constraints(
            "asset_hostname_rules"
        )} == {"ck_asset_hostname_rules_rule_type"}
        assert {item["name"] for item in inspector.get_unique_constraints(
            "asset_hostname_rules"
        )} == {"uq_asset_hostname_rules_revision_type_pattern"}
        foreign_key = inspector.get_foreign_keys("asset_hostname_rules")[0]
        assert foreign_key["constrained_columns"] == ["authorization_revision_id"]
        assert foreign_key["referred_table"] == "authorization_revisions"
        assert foreign_key["referred_columns"] == ["id"]
        assert foreign_key["options"] == {"ondelete": "RESTRICT"}
        with engine.begin() as db:
            assert db.scalar(text("SELECT count(*) FROM asset_hostname_rules")) == 0
            profile_id = db.scalar(text("""
                INSERT INTO authorization_profiles
                    (name, program_name, authorization_type,
                     max_requests_per_second)
                VALUES ('m10 migration', 'm10', 'self_owned', 1.0)
                RETURNING id
            """))
            revision_id = db.scalar(text("""
                INSERT INTO authorization_revisions
                    (authorization_profile_id, revision_number, lifecycle_state,
                     name, program_name, authorization_type,
                     max_requests_per_second)
                VALUES (:profile_id, 1, 'draft', 'm10', 'm10', 'self_owned', 1.0)
                RETURNING id
            """), {"profile_id": profile_id})
            rule_id = db.scalar(text("""
                INSERT INTO asset_hostname_rules
                    (authorization_revision_id, rule_type, hostname_pattern)
                VALUES (:revision_id, 'include', '*.example.test')
                RETURNING id
            """), {"revision_id": revision_id})
        with pytest.raises(IntegrityError):
            with engine.begin() as db:
                db.execute(text(
                    "DELETE FROM authorization_revisions WHERE id = :id"
                ), {"id": revision_id})
        with pytest.raises(IntegrityError):
            with engine.begin() as db:
                db.execute(text("""
                    INSERT INTO asset_hostname_rules
                        (authorization_revision_id, rule_type, hostname_pattern)
                    VALUES (:revision_id, 'include', '*.example.test')
                """), {"revision_id": revision_id})
        with pytest.raises(IntegrityError):
            with engine.begin() as db:
                db.execute(text("""
                    INSERT INTO asset_hostname_rules
                        (authorization_revision_id, rule_type, hostname_pattern)
                    VALUES (:revision_id, 'invalid', '*.other.test')
                """), {"revision_id": revision_id})
        command.downgrade(config, PARENT)
        assert "asset_hostname_rules" not in inspect(engine).get_table_names()
    finally:
        command.upgrade(config, "head")
        with engine.begin() as db:
            if rule_id is not None:
                db.execute(text("DELETE FROM asset_hostname_rules WHERE id = :id"), {
                    "id": rule_id
                })
            if revision_id is not None:
                db.execute(text(
                    "DELETE FROM authorization_revisions WHERE id = :id"
                ), {"id": revision_id})
            if profile_id is not None:
                db.execute(text(
                    "DELETE FROM authorization_profiles WHERE id = :id"
                ), {"id": profile_id})
