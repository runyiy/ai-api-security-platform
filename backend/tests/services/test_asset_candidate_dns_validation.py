from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    AssetCandidateDNSAddress,
    AssetCandidateDNSCNAMEHop,
    AssetCandidateDNSValidation,
    AssetCandidateEvaluation,
    AuthorizationProfile,
    AuthorizationRevision,
)
from app.db.session import SessionLocal
from app.services.asset_candidate_dns_validation import (
    create_asset_candidate_dns_validation,
)


class FakeResolver:
    def lookup_cname(self, hostname: str) -> str | None:
        return "edge.example.test" if hostname == "api.example.test" else None

    def resolve_addresses(self, hostname: str):
        return ("8.8.8.8", "2606:4700:4700::1111")


def make_included_evaluation() -> tuple[int, int, int]:
    with SessionLocal() as db:
        profile = AuthorizationProfile(
            name=f"dns-rollback-{uuid4()}",
            program_name="Synthetic DNS rollback",
            authorization_type="self_owned",
            max_requests_per_second=1.0,
        )
        db.add(profile)
        db.flush()
        revision = AuthorizationRevision(
            authorization_profile_id=profile.id,
            revision_number=1,
            lifecycle_state="active",
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
        db.commit()
        return profile.id, revision.id, evaluation.id


def cleanup(profile_id: int, revision_id: int, evaluation_id: int) -> None:
    with SessionLocal() as db:
        validation_ids = list(db.scalars(select(AssetCandidateDNSValidation.id).where(
            AssetCandidateDNSValidation.asset_candidate_evaluation_id == evaluation_id
        )))
        db.execute(delete(AssetCandidateDNSAddress).where(
            AssetCandidateDNSAddress.dns_validation_id.in_(validation_ids)
        ))
        db.execute(delete(AssetCandidateDNSCNAMEHop).where(
            AssetCandidateDNSCNAMEHop.dns_validation_id.in_(validation_ids)
        ))
        db.execute(delete(AssetCandidateDNSValidation).where(
            AssetCandidateDNSValidation.id.in_(validation_ids)
        ))
        db.execute(delete(AssetCandidateEvaluation).where(
            AssetCandidateEvaluation.id == evaluation_id
        ))
        db.execute(delete(AuthorizationRevision).where(
            AuthorizationRevision.id == revision_id
        ))
        db.execute(delete(AuthorizationProfile).where(
            AuthorizationProfile.id == profile_id
        ))
        db.commit()


def test_parent_and_all_children_roll_back_atomically_on_commit_failure(
    monkeypatch,
) -> None:
    profile_id, revision_id, evaluation_id = make_included_evaluation()
    original_commit = SessionLocal.class_.commit
    try:
        def fail_commit(session) -> None:
            if any(isinstance(item, (
                AssetCandidateDNSValidation,
                AssetCandidateDNSCNAMEHop,
                AssetCandidateDNSAddress,
            )) for item in session.new):
                raise IntegrityError("commit", {}, RuntimeError("forced"))
            original_commit(session)

        monkeypatch.setattr(SessionLocal.class_, "commit", fail_commit)
        with pytest.raises(IntegrityError):
            create_asset_candidate_dns_validation(
                profile_id=profile_id,
                revision_id=revision_id,
                evaluation_id=evaluation_id,
                resolver=FakeResolver(),
            )
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                AssetCandidateDNSValidation
            ).where(
                AssetCandidateDNSValidation.asset_candidate_evaluation_id
                == evaluation_id
            )) == 0
            assert db.scalar(select(func.count()).select_from(
                AssetCandidateDNSCNAMEHop
            )) == 0
            assert db.scalar(select(func.count()).select_from(
                AssetCandidateDNSAddress
            )) == 0
    finally:
        monkeypatch.setattr(SessionLocal.class_, "commit", original_commit)
        cleanup(profile_id, revision_id, evaluation_id)
