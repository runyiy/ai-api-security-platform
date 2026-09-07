from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.session import engine


REVISION = "f3b5d7e9a1c2"
PARENT = "e2a4c6e8b0d3"


def test_resource_access_assertion_migration_contract_and_round_trip() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_revision(REVISION).down_revision == PARENT
    assert scripts.get_heads() == ["b6d8f0a2c4e5"]
    target_id = identity_id = resource_id = None
    try:
        command.downgrade(config, PARENT)
        before = inspect(engine)
        assert "resource_access_assertions" not in before.get_table_names()
        owner_column = next(
            item for item in before.get_columns("resources")
            if item["name"] == "owner_identity_id"
        )
        owner_fk = next(
            item for item in before.get_foreign_keys("resources")
            if item["constrained_columns"] == ["owner_identity_id"]
        )
        with engine.begin() as db:
            target_id = db.scalar(text("""
                INSERT INTO targets
                    (name, base_url, environment, network_mode, is_enabled)
                VALUES ('m12 migration', 'https://m12.example.test', 'test',
                        'private_local', true) RETURNING id
            """))
            identity_id = db.scalar(text("""
                INSERT INTO test_identities
                    (target_id, name, auth_type, is_active)
                VALUES (:target, 'm12 owner', 'bearer', true) RETURNING id
            """), {"target": target_id})
            resource_id = db.scalar(text("""
                INSERT INTO resources
                    (target_id, resource_type, external_id, owner_identity_id)
                VALUES (:target, 'order', 'historical-resource', :identity)
                RETURNING id
            """), {"target": target_id, "identity": identity_id})

        command.upgrade(config, REVISION)
        inspector = inspect(engine)
        columns = {item["name"]: item for item in inspector.get_columns(
            "resource_access_assertions"
        )}
        assert set(columns) == {
            "id", "resource_id", "test_identity_id", "relationship",
            "expected_access", "provenance", "confidence",
            "verification_state", "asserted_at", "observed_at", "valid_from",
            "valid_until",
        }
        assert columns["resource_id"]["nullable"] is False
        assert columns["test_identity_id"]["nullable"] is False
        assert columns["asserted_at"]["nullable"] is False
        assert columns["asserted_at"]["default"] is not None
        assert columns["observed_at"]["nullable"] is True
        assert {item["name"] for item in inspector.get_check_constraints(
            "resource_access_assertions"
        )} == {
            "ck_resource_access_assertions_relationship",
            "ck_resource_access_assertions_expected_access",
            "ck_resource_access_assertions_meaningful",
            "ck_resource_access_assertions_provenance",
            "ck_resource_access_assertions_confidence",
            "ck_resource_access_assertions_verification_state",
            "ck_resource_access_assertions_validity_window",
        }
        fks = {item["constrained_columns"][0]: item for item in
               inspector.get_foreign_keys("resource_access_assertions")}
        assert fks["resource_id"]["referred_table"] == "resources"
        assert fks["test_identity_id"]["referred_table"] == "test_identities"
        assert fks["resource_id"]["options"] == {"ondelete": "RESTRICT"}
        assert fks["test_identity_id"]["options"] == {"ondelete": "RESTRICT"}
        assert {item["name"] for item in inspector.get_indexes(
            "resource_access_assertions"
        )} == {
            "ix_resource_access_assertions_resource_id",
            "ix_resource_access_assertions_test_identity_id",
        }
        after_owner_column = next(
            item for item in inspector.get_columns("resources")
            if item["name"] == "owner_identity_id"
        )
        after_owner_fk = next(
            item for item in inspector.get_foreign_keys("resources")
            if item["constrained_columns"] == ["owner_identity_id"]
        )
        assert after_owner_column["nullable"] == owner_column["nullable"] is False
        assert after_owner_fk["options"] == owner_fk["options"]
        with engine.begin() as db:
            assert db.scalar(text(
                "SELECT count(*) FROM resource_access_assertions"
            )) == 0
            assert db.scalar(text(
                "SELECT owner_identity_id FROM resources WHERE id = :id"
            ), {"id": resource_id}) == identity_id

        valid = dict(resource=resource_id, identity=identity_id)
        with engine.begin() as db:
            db.execute(text("""
                INSERT INTO resource_access_assertions
                    (resource_id, test_identity_id, relationship,
                     expected_access, provenance, confidence,
                     verification_state, valid_from, valid_until)
                VALUES (:resource, :identity, 'non_owner', 'allowed',
                        'human_verified', 10, 'verified',
                        '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z')
            """), valid)
        invalid_fragments = (
            "'invalid', 'allowed', 'human_verified', 50, 'verified', NULL, NULL",
            "'owner', 'invalid', 'human_verified', 50, 'verified', NULL, NULL",
            "'owner', 'allowed', 'invalid', 50, 'verified', NULL, NULL",
            "'owner', 'allowed', 'human_verified', -1, 'verified', NULL, NULL",
            "'owner', 'allowed', 'human_verified', 101, 'verified', NULL, NULL",
            "'owner', 'allowed', 'human_verified', 50, 'invalid', NULL, NULL",
            "'unspecified', 'unspecified', 'human_verified', 50, 'verified', NULL, NULL",
            "'owner', 'allowed', 'human_verified', 50, 'verified', NULL, '2026-01-02T00:00:00Z'",
            "'owner', 'allowed', 'human_verified', 50, 'verified', "
            "'2026-01-02T00:00:00Z', '2026-01-01T00:00:00Z'",
        )
        for fragment in invalid_fragments:
            with pytest.raises(IntegrityError):
                with engine.begin() as db:
                    db.execute(text(f"""
                        INSERT INTO resource_access_assertions
                            (resource_id, test_identity_id, relationship,
                             expected_access, provenance, confidence,
                             verification_state, valid_from, valid_until)
                        VALUES (:resource, :identity, {fragment})
                    """), valid)
        with pytest.raises(IntegrityError):
            with engine.begin() as db:
                db.execute(text("DELETE FROM resources WHERE id = :id"),
                           {"id": resource_id})
        with pytest.raises(IntegrityError):
            with engine.begin() as db:
                db.execute(text("DELETE FROM test_identities WHERE id = :id"),
                           {"id": identity_id})

        command.downgrade(config, PARENT)
        downgraded = inspect(engine)
        assert "resource_access_assertions" not in downgraded.get_table_names()
        with engine.begin() as db:
            assert db.scalar(text(
                "SELECT owner_identity_id FROM resources WHERE id = :id"
            ), {"id": resource_id}) == identity_id
    finally:
        command.upgrade(config, "head")
        with engine.begin() as db:
            if resource_id is not None:
                db.execute(text(
                    "DELETE FROM resource_access_assertions WHERE resource_id = :id"
                ), {"id": resource_id})
                db.execute(text("DELETE FROM resources WHERE id = :id"),
                           {"id": resource_id})
            if identity_id is not None:
                db.execute(text("DELETE FROM test_identities WHERE id = :id"),
                           {"id": identity_id})
            if target_id is not None:
                db.execute(text("DELETE FROM targets WHERE id = :id"),
                           {"id": target_id})
