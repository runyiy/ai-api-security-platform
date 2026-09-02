import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.models import AssetEnrollmentDecision, Target
from app.db.session import SessionLocal
from app.services.approved_enrollment_target import (
    canonical_enrollment_origin,
    create_target_from_approved_enrollment,
)
from tests.api.test_approved_enrollment_target import cleanup, make_provenance


def test_canonical_enrollment_origin_omits_only_default_ports() -> None:
    assert canonical_enrollment_origin(
        scheme="http", hostname="api.example.test", port=None
    ) == "http://api.example.test"
    assert canonical_enrollment_origin(
        scheme="http", hostname="api.example.test", port=80
    ) == "http://api.example.test"
    assert canonical_enrollment_origin(
        scheme="https", hostname="api.example.test", port=443
    ) == "https://api.example.test"
    assert canonical_enrollment_origin(
        scheme="https", hostname="api.example.test", port=8443
    ) == "https://api.example.test:8443"


def test_target_persistence_failure_rolls_back_atomically(monkeypatch) -> None:
    ids = make_provenance()
    original_commit = SessionLocal.class_.commit
    try:
        def fail_commit(session):
            if any(isinstance(item, Target) for item in session.new):
                raise IntegrityError("commit", {}, RuntimeError("forced"))
            original_commit(session)

        monkeypatch.setattr(SessionLocal.class_, "commit", fail_commit)
        with SessionLocal() as db:
            with pytest.raises(IntegrityError):
                create_target_from_approved_enrollment(
                    db,
                    profile_id=ids[0], revision_id=ids[1],
                    evaluation_id=ids[2], validation_id=ids[3],
                    decision_id=ids[4], name="Atomic target",
                    environment="test", scheme="https", port=None,
                    network_mode="private_local",
                )
            db.rollback()
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Target).where(
                Target.asset_enrollment_decision_id == ids[4]
            )) == 0
            assert db.get(AssetEnrollmentDecision, ids[4]) is not None
    finally:
        monkeypatch.setattr(SessionLocal.class_, "commit", original_commit)
        cleanup([ids])
