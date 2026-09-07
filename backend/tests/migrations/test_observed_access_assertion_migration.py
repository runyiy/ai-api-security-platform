from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.session import engine


REVISION = "a4c6e8b0d2f3"
PARENT = "f3b5d7e9a1c2"


def test_observed_access_assertion_migration_round_trip() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_revision(REVISION).down_revision == PARENT
    assert scripts.get_heads() == ["b6d8f0a2c4e5"]
    ids: dict[str, int] = {}
    try:
        command.downgrade(config, PARENT)
        before = inspect(engine)
        assert "source_test_run_id" not in {
            column["name"] for column in before.get_columns(
                "resource_access_assertions"
            )
        }
        with engine.begin() as db:
            ids["target"] = db.scalar(text("""
                INSERT INTO targets
                    (name, base_url, environment, network_mode, is_enabled)
                VALUES ('m12-02 migration', 'https://m1202.example.test',
                        'test', 'private_local', true) RETURNING id
            """))
            ids["identity"] = db.scalar(text("""
                INSERT INTO test_identities
                    (target_id, name, auth_type, is_active)
                VALUES (:target, 'm12-02 identity', 'bearer', true) RETURNING id
            """), ids)
            ids["resource"] = db.scalar(text("""
                INSERT INTO resources
                    (target_id, resource_type, external_id, owner_identity_id)
                VALUES (:target, 'order', 'm12-02-resource', :identity)
                RETURNING id
            """), ids)
            ids["endpoint"] = db.scalar(text("""
                INSERT INTO endpoints
                    (target_id, path, method, requires_auth, parameters)
                VALUES (:target, '/orders/{id}', 'GET', true, '[]'::jsonb)
                RETURNING id
            """), ids)
            ids["case"] = db.scalar(text("""
                INSERT INTO test_cases
                    (endpoint_id, actor_identity_id, resource_id, test_type,
                     ownership_relation, expected_statuses, status)
                VALUES (:endpoint, :identity, :resource, 'owner_baseline',
                        'owner', ARRAY[200], 'pending') RETURNING id
            """), ids)
            ids["run"] = db.scalar(text("""
                INSERT INTO test_runs
                    (test_case_id, request_data, response_status, response_body)
                VALUES (:case, '{}'::jsonb, 200, 'ok') RETURNING id
            """), ids)
            ids["human"] = db.scalar(text("""
                INSERT INTO resource_access_assertions
                    (resource_id, test_identity_id, relationship,
                     expected_access, provenance, confidence,
                     verification_state)
                VALUES (:resource, :identity, 'owner', 'allowed',
                        'human_verified', 88, 'verified') RETURNING id
            """), ids)

        command.upgrade(config, REVISION)
        inspector = inspect(engine)
        source = next(column for column in inspector.get_columns(
            "resource_access_assertions"
        ) if column["name"] == "source_test_run_id")
        assert source["nullable"] is True
        source_fk = next(fk for fk in inspector.get_foreign_keys(
            "resource_access_assertions"
        ) if fk["constrained_columns"] == ["source_test_run_id"])
        assert source_fk["referred_table"] == "test_runs"
        assert source_fk["options"] == {"ondelete": "RESTRICT"}
        source_index = next(index for index in inspector.get_indexes(
            "resource_access_assertions"
        ) if index["name"] == "ux_resource_access_assertions_source_test_run_id")
        assert source_index["unique"] is True
        assert "ck_resource_access_assertions_observed_source" in {
            check["name"] for check in inspector.get_check_constraints(
                "resource_access_assertions"
            )
        }
        with engine.begin() as db:
            human = db.execute(text("""
                SELECT relationship, expected_access, provenance, confidence,
                       verification_state, source_test_run_id
                FROM resource_access_assertions WHERE id = :human
            """), ids).one()
            assert tuple(human) == (
                "owner", "allowed", "human_verified", 88, "verified", None
            )
            assert db.scalar(text("""
                SELECT count(*) FROM resource_access_assertions
                WHERE resource_id = :resource
            """), ids) == 1
            db.execute(text("""
                INSERT INTO resource_access_assertions
                    (resource_id, test_identity_id, relationship,
                     expected_access, provenance, confidence,
                     verification_state, source_test_run_id)
                VALUES (:resource, :identity, 'unspecified', 'allowed',
                        'observed_baseline', 50, 'candidate', :run)
            """), ids)
        with pytest.raises(IntegrityError):
            with engine.begin() as db:
                db.execute(text("""
                    INSERT INTO resource_access_assertions
                        (resource_id, test_identity_id, relationship,
                         expected_access, provenance, confidence,
                         verification_state, source_test_run_id)
                    VALUES (:resource, :identity, 'unspecified', 'allowed',
                            'observed_baseline', 50, 'candidate', :run)
                """), ids)
        with pytest.raises(IntegrityError):
            with engine.begin() as db:
                db.execute(text("""
                    INSERT INTO resource_access_assertions
                        (resource_id, test_identity_id, relationship,
                         expected_access, provenance, confidence,
                         verification_state)
                    VALUES (:resource, :identity, 'unspecified', 'allowed',
                            'observed_baseline', 50, 'candidate')
                """), ids)
        with pytest.raises(IntegrityError):
            with engine.begin() as db:
                db.execute(text("DELETE FROM test_runs WHERE id = :run"), ids)

        command.downgrade(config, PARENT)
        after = inspect(engine)
        assert "source_test_run_id" not in {
            column["name"] for column in after.get_columns(
                "resource_access_assertions"
            )
        }
        with engine.begin() as db:
            assert db.scalar(text("""
                SELECT count(*) FROM resource_access_assertions
                WHERE resource_id = :resource
            """), ids) == 2
            db.execute(text("""
                DELETE FROM resource_access_assertions
                WHERE resource_id = :resource
                  AND provenance = 'observed_baseline'
            """), ids)
        command.upgrade(config, REVISION)
        with engine.begin() as db:
            assert db.scalar(text("""
                SELECT source_test_run_id
                FROM resource_access_assertions WHERE id = :human
            """), ids) is None
    finally:
        command.upgrade(config, "head")
        with engine.begin() as db:
            if "resource" in ids:
                db.execute(text(
                    "DELETE FROM resource_access_assertions WHERE resource_id = :resource"
                ), ids)
            if "run" in ids:
                db.execute(text("DELETE FROM test_runs WHERE id = :run"), ids)
            if "case" in ids:
                db.execute(text("DELETE FROM test_cases WHERE id = :case"), ids)
            if "endpoint" in ids:
                db.execute(text("DELETE FROM endpoints WHERE id = :endpoint"), ids)
            if "resource" in ids:
                db.execute(text("DELETE FROM resources WHERE id = :resource"), ids)
            if "identity" in ids:
                db.execute(text(
                    "DELETE FROM test_identities WHERE id = :identity"
                ), ids)
            if "target" in ids:
                db.execute(text("DELETE FROM targets WHERE id = :target"), ids)
