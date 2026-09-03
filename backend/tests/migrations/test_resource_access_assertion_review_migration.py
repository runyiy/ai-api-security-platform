from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.session import engine


REVISION = "b6d8f0a2c4e5"
PARENT = "a4c6e8b0d2f3"


def test_resource_access_assertion_review_migration_round_trip() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_revision(REVISION).down_revision == PARENT
    assert scripts.get_heads() == [REVISION]
    ids: dict[str, int] = {}
    try:
        command.downgrade(config, PARENT)
        assert "reviewed_assertion_id" not in {
            column["name"] for column in inspect(engine).get_columns(
                "resource_access_assertions"
            )
        }
        with engine.begin() as db:
            ids["target"] = db.scalar(text("""
                INSERT INTO targets
                    (name, base_url, environment, network_mode, is_enabled)
                VALUES ('m12-04 migration', 'https://m1204.example.test',
                        'test', 'private_local', true) RETURNING id
            """))
            ids["identity"] = db.scalar(text("""
                INSERT INTO test_identities
                    (target_id, name, auth_type, is_active)
                VALUES (:target, 'reviewer subject', 'bearer', true) RETURNING id
            """), ids)
            ids["resource"] = db.scalar(text("""
                INSERT INTO resources
                    (target_id, resource_type, external_id, owner_identity_id)
                VALUES (:target, 'order', 'migration-review', :identity)
                RETURNING id
            """), ids)
            ids["legacy"] = db.scalar(text("""
                INSERT INTO resource_access_assertions
                    (resource_id, test_identity_id, relationship,
                     expected_access, provenance, confidence,
                     verification_state)
                VALUES (:resource, :identity, 'non_owner', 'allowed',
                        'inferred_candidate', 21, 'candidate') RETURNING id
            """), ids)
        command.upgrade(config, REVISION)
        inspector = inspect(engine)
        column = next(column for column in inspector.get_columns(
            "resource_access_assertions"
        ) if column["name"] == "reviewed_assertion_id")
        assert column["nullable"] is True
        fk = next(fk for fk in inspector.get_foreign_keys(
            "resource_access_assertions"
        ) if fk["constrained_columns"] == ["reviewed_assertion_id"])
        assert fk["referred_table"] == "resource_access_assertions"
        assert fk["options"] == {"ondelete": "RESTRICT"}
        index = next(index for index in inspector.get_indexes(
            "resource_access_assertions"
        ) if index["name"] == "ux_resource_access_assertions_reviewed_assertion_id")
        assert index["unique"] is True
        assert {
            "ck_resource_access_assertions_review_not_self",
            "ck_resource_access_assertions_review_provenance",
            "ck_resource_access_assertions_review_state",
            "ck_resource_access_assertions_review_source_run",
        } <= {check["name"] for check in inspector.get_check_constraints(
            "resource_access_assertions"
        )}
        with engine.begin() as db:
            legacy = db.execute(text("""
                SELECT relationship, expected_access, provenance, confidence,
                       verification_state, reviewed_assertion_id
                FROM resource_access_assertions WHERE id = :legacy
            """), ids).one()
            assert tuple(legacy) == (
                "non_owner", "allowed", "inferred_candidate", 21,
                "candidate", None,
            )
            ids["review"] = db.scalar(text("""
                INSERT INTO resource_access_assertions
                    (resource_id, test_identity_id, relationship,
                     expected_access, provenance, confidence,
                     verification_state, reviewed_assertion_id)
                VALUES (:resource, :identity, 'non_owner', 'allowed',
                        'human_verified', 90, 'verified', :legacy) RETURNING id
            """), ids)
        for values in (
            ("inferred_candidate", "verified", None),
            ("human_verified", "candidate", None),
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as db:
                    db.execute(text("""
                        INSERT INTO resource_access_assertions
                            (resource_id, test_identity_id, relationship,
                             expected_access, provenance, confidence,
                             verification_state, reviewed_assertion_id)
                        VALUES (:resource, :identity, 'non_owner', 'allowed',
                                :provenance, 90, :state, :legacy)
                    """), {**ids, "provenance": values[0], "state": values[1]})
        with pytest.raises(IntegrityError):
            with engine.begin() as db:
                db.execute(text("""
                    INSERT INTO resource_access_assertions
                        (id, resource_id, test_identity_id, relationship,
                         expected_access, provenance, confidence,
                         verification_state, reviewed_assertion_id)
                    VALUES (900000001, :resource, :identity, 'owner', 'allowed',
                            'human_verified', 90, 'verified', 900000001)
                """), ids)
        with pytest.raises(IntegrityError):
            with engine.begin() as db:
                db.execute(text("""
                    INSERT INTO resource_access_assertions
                        (resource_id, test_identity_id, relationship,
                         expected_access, provenance, confidence,
                         verification_state, reviewed_assertion_id)
                    VALUES (:resource, :identity, 'non_owner', 'allowed',
                            'human_verified', 90, 'rejected', :legacy)
                """), ids)
        with pytest.raises(IntegrityError):
            with engine.begin() as db:
                db.execute(text(
                    "DELETE FROM resource_access_assertions WHERE id = :legacy"
                ), ids)
        command.downgrade(config, PARENT)
        assert "reviewed_assertion_id" not in {
            column["name"] for column in inspect(engine).get_columns(
                "resource_access_assertions"
            )
        }
        command.upgrade(config, REVISION)
    finally:
        command.upgrade(config, "head")
        with engine.begin() as db:
            if "resource" in ids:
                db.execute(text(
                    "DELETE FROM resource_access_assertions WHERE resource_id = :resource"
                ), ids)
                db.execute(text("DELETE FROM resources WHERE id = :resource"), ids)
            if "identity" in ids:
                db.execute(text("DELETE FROM test_identities WHERE id = :identity"), ids)
            if "target" in ids:
                db.execute(text("DELETE FROM targets WHERE id = :target"), ids)
