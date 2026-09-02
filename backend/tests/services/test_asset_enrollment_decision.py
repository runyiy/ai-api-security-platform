from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    AssetCandidateDNSValidation,
    AssetCandidateEvaluation,
    AssetEnrollmentDecision,
    AuthorizationProfile,
    AuthorizationRevision,
)
from app.db.session import SessionLocal
from app.services.asset_enrollment_decision import create_asset_enrollment_decision
from app.services.asset_enrollment_note import AssetEnrollmentNoteAuthMaterialError


def make_hierarchy() -> tuple[int, int, int, int]:
    with SessionLocal() as db:
        profile = AuthorizationProfile(
            name=f"enrollment-rollback-{uuid4()}",
            program_name="Synthetic enrollment rollback",
            authorization_type="self_owned",
            max_requests_per_second=1.0,
        )
        db.add(profile)
        db.flush()
        revision = AuthorizationRevision(
            authorization_profile_id=profile.id,
            revision_number=1,
            lifecycle_state="revoked",
            name=profile.name,
            program_name=profile.program_name,
            authorization_type=profile.authorization_type,
            max_requests_per_second=1.0,
        )
        db.add(revision)
        db.flush()
        evaluation = AssetCandidateEvaluation(
            authorization_revision_id=revision.id,
            normalized_hostname="api.example.test",
            decision_code="asset_candidate_included",
            source_type="operator_supplied",
        )
        db.add(evaluation)
        db.flush()
        validation = AssetCandidateDNSValidation(
            asset_candidate_evaluation_id=evaluation.id,
            authorization_revision_id=revision.id,
            decision_code="asset_candidate_dns_public_only",
            normalized_hostname="api.example.test",
        )
        db.add(validation)
        db.commit()
        return profile.id, revision.id, evaluation.id, validation.id


def cleanup(ids) -> None:
    with SessionLocal() as db:
        db.execute(delete(AssetEnrollmentDecision).where(
            AssetEnrollmentDecision.asset_candidate_dns_validation_id == ids[3]
        ))
        db.execute(delete(AssetCandidateDNSValidation).where(
            AssetCandidateDNSValidation.id == ids[3]
        ))
        db.execute(delete(AssetCandidateEvaluation).where(
            AssetCandidateEvaluation.id == ids[2]
        ))
        db.execute(delete(AuthorizationRevision).where(
            AuthorizationRevision.id == ids[1]
        ))
        db.execute(delete(AuthorizationProfile).where(
            AuthorizationProfile.id == ids[0]
        ))
        db.commit()


def test_persistence_failure_rolls_back_decision_atomically(monkeypatch) -> None:
    ids = make_hierarchy()
    original_commit = SessionLocal.class_.commit
    try:
        def fail_commit(session):
            if any(isinstance(item, AssetEnrollmentDecision) for item in session.new):
                raise IntegrityError("commit", {}, RuntimeError("forced"))
            original_commit(session)

        monkeypatch.setattr(SessionLocal.class_, "commit", fail_commit)
        with SessionLocal() as db:
            with pytest.raises(IntegrityError):
                create_asset_enrollment_decision(
                    db,
                    profile_id=ids[0], revision_id=ids[1],
                    evaluation_id=ids[2], validation_id=ids[3],
                    decision="approved", reason_code=None, note=None,
                )
            db.rollback()
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                AssetEnrollmentDecision
            ).where(
                AssetEnrollmentDecision.asset_candidate_dns_validation_id
                == ids[3]
            )) == 0
    finally:
        monkeypatch.setattr(SessionLocal.class_, "commit", original_commit)
        cleanup(ids)


def test_direct_service_rejects_secret_note_and_accepts_normal_note() -> None:
    ids = make_hierarchy()
    secret_note = "Authorization: Bearer service-layer-secret"
    ordinary_note = "Operator confirmed ownership from internal inventory."
    try:
        with SessionLocal() as db:
            with pytest.raises(AssetEnrollmentNoteAuthMaterialError) as captured:
                create_asset_enrollment_decision(
                    db,
                    profile_id=ids[0], revision_id=ids[1],
                    evaluation_id=ids[2], validation_id=ids[3],
                    decision="approved", reason_code=None, note=secret_note,
                )
            assert secret_note not in str(captured.value)
            assert secret_note not in repr(captured.value)
            assert db.scalar(select(func.count()).select_from(
                AssetEnrollmentDecision
            ).where(
                AssetEnrollmentDecision.asset_candidate_dns_validation_id
                == ids[3]
            )) == 0

            accepted = create_asset_enrollment_decision(
                db,
                profile_id=ids[0], revision_id=ids[1],
                evaluation_id=ids[2], validation_id=ids[3],
                decision="approved", reason_code=None, note=ordinary_note,
            )
            assert accepted.note == ordinary_note

        with SessionLocal() as db:
            rows = list(db.scalars(select(AssetEnrollmentDecision).where(
                AssetEnrollmentDecision.asset_candidate_dns_validation_id
                == ids[3]
            )).all())
            assert len(rows) == 1
            assert rows[0].note == ordinary_note
    finally:
        cleanup(ids)
