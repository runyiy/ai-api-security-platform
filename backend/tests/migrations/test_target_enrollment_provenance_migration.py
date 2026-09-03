from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.session import engine


REVISION = "d0f2a4c6e8b1"
PARENT = "c6e8a0b2d4f7"


def test_target_enrollment_provenance_migration_round_trip() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_revision(REVISION).down_revision == PARENT
    assert scripts.get_heads() == ["b6d8f0a2c4e5"]
    profile_id = revision_id = evaluation_id = validation_id = decision_id = None
    legacy_target_id = second_target_id = None
    try:
        command.downgrade(config, PARENT)
        before = inspect(engine)
        assert "asset_enrollment_decision_id" not in {
            item["name"] for item in before.get_columns("targets")
        }
        with engine.begin() as db:
            target_count = db.scalar(text("SELECT count(*) FROM targets"))
            legacy_target_id = db.scalar(text("""
                INSERT INTO targets (name, base_url, environment, network_mode,
                                     is_enabled)
                VALUES ('m10-06 legacy', 'https://legacy.example.test', 'test',
                        'private_local', true) RETURNING id
            """))

        command.upgrade(config, REVISION)
        inspector = inspect(engine)
        columns = {item["name"]: item for item in inspector.get_columns("targets")}
        assert columns["asset_enrollment_decision_id"]["nullable"] is True
        assert {item["name"] for item in inspector.get_unique_constraints(
            "targets"
        )} >= {"uq_targets_asset_enrollment_decision_id"}
        enrollment_fk = next(item for item in inspector.get_foreign_keys("targets")
                             if item["referred_table"] == "asset_enrollment_decisions")
        assert enrollment_fk["options"] == {"ondelete": "RESTRICT"}
        with engine.begin() as db:
            assert db.scalar(text("""
                SELECT asset_enrollment_decision_id FROM targets WHERE id = :id
            """), {"id": legacy_target_id}) is None
            assert db.scalar(text("SELECT count(*) FROM targets")) == target_count + 1
            profile_id = db.scalar(text("""
                INSERT INTO authorization_profiles
                    (name, program_name, authorization_type,
                     max_requests_per_second)
                VALUES ('m10-06 migration', 'm10', 'self_owned', 1.0)
                RETURNING id
            """))
            revision_id = db.scalar(text("""
                INSERT INTO authorization_revisions
                    (authorization_profile_id, revision_number, lifecycle_state,
                     name, program_name, authorization_type,
                     max_requests_per_second)
                VALUES (:profile, 1, 'active', 'm10', 'm10', 'self_owned', 1.0)
                RETURNING id
            """), {"profile": profile_id})
            evaluation_id = db.scalar(text("""
                INSERT INTO asset_candidate_evaluations
                    (authorization_revision_id, normalized_hostname,
                     decision_code, source_type)
                VALUES (:revision, 'api.example.test',
                        'asset_candidate_included', 'operator_supplied')
                RETURNING id
            """), {"revision": revision_id})
            validation_id = db.scalar(text("""
                INSERT INTO asset_candidate_dns_validations
                    (asset_candidate_evaluation_id, authorization_revision_id,
                     decision_code, normalized_hostname)
                VALUES (:evaluation, :revision,
                        'asset_candidate_dns_private_local_only',
                        'api.example.test') RETURNING id
            """), {"evaluation": evaluation_id, "revision": revision_id})
            decision_id = db.scalar(text("""
                INSERT INTO asset_enrollment_decisions
                    (asset_candidate_dns_validation_id,
                     authorization_revision_id, decision, normalized_hostname)
                VALUES (:validation, :revision, 'approved', 'api.example.test')
                RETURNING id
            """), {"validation": validation_id, "revision": revision_id})
            db.execute(text("""
                UPDATE targets SET asset_enrollment_decision_id = :decision,
                    authorization_profile_id = :profile,
                    authorization_revision_id = :revision
                WHERE id = :target
            """), {
                "decision": decision_id, "profile": profile_id,
                "revision": revision_id, "target": legacy_target_id,
            })
            second_target_id = db.scalar(text("""
                INSERT INTO targets (name, base_url, environment, network_mode,
                                     is_enabled)
                VALUES ('m10-06 second', 'https://second.example.test', 'test',
                        'private_local', true) RETURNING id
            """))

        with pytest.raises(IntegrityError):
            with engine.begin() as db:
                db.execute(text("""
                    UPDATE targets SET asset_enrollment_decision_id = :decision
                    WHERE id = :target
                """), {"decision": decision_id, "target": second_target_id})
        with pytest.raises(IntegrityError):
            with engine.begin() as db:
                db.execute(text(
                    "DELETE FROM asset_enrollment_decisions WHERE id = :id"
                ), {"id": decision_id})

        with engine.begin() as db:
            db.execute(text("""
                UPDATE targets SET asset_enrollment_decision_id = NULL,
                    authorization_revision_id = NULL,
                    authorization_profile_id = NULL
                WHERE id = :target
            """), {"target": legacy_target_id})
        command.downgrade(config, PARENT)
        assert "asset_enrollment_decision_id" not in {
            item["name"] for item in inspect(engine).get_columns("targets")
        }
    finally:
        command.upgrade(config, "head")
        with engine.begin() as db:
            if legacy_target_id is not None:
                db.execute(text("DELETE FROM targets WHERE id = :id"),
                           {"id": legacy_target_id})
            if second_target_id is not None:
                db.execute(text("DELETE FROM targets WHERE id = :id"),
                           {"id": second_target_id})
            if decision_id is not None:
                db.execute(text(
                    "DELETE FROM asset_enrollment_decisions WHERE id = :id"
                ), {"id": decision_id})
            if validation_id is not None:
                db.execute(text(
                    "DELETE FROM asset_candidate_dns_validations WHERE id = :id"
                ), {"id": validation_id})
            if evaluation_id is not None:
                db.execute(text(
                    "DELETE FROM asset_candidate_evaluations WHERE id = :id"
                ), {"id": evaluation_id})
            if revision_id is not None:
                db.execute(text(
                    "DELETE FROM authorization_revisions WHERE id = :id"
                ), {"id": revision_id})
            if profile_id is not None:
                db.execute(text(
                    "DELETE FROM authorization_profiles WHERE id = :id"
                ), {"id": profile_id})
