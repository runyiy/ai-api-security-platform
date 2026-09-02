from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    AssetCandidateEvaluation,
    AuthorizationProfile,
    AuthorizationRevision,
)
from app.db.session import SessionLocal
from app.services.asset_candidate_evaluation import (
    AssetCandidateEvaluationInactiveError,
    create_asset_candidate_evaluation,
)
from app.services.authorization_revision import create_revision, lock_profile


def make_active_revision() -> tuple[int, int]:
    with SessionLocal() as db:
        profile = AuthorizationProfile(
            name=f"candidate-service-{uuid4()}",
            program_name="Synthetic candidate service",
            authorization_type="self_owned",
            max_requests_per_second=1.0,
        )
        db.add(profile)
        db.commit()
        profile_id = profile.id
        revision = create_revision(db, profile_id)
        revision.lifecycle_state = "active"
        db.commit()
        return profile_id, revision.id


def cleanup(profile_id: int) -> None:
    with SessionLocal() as db:
        revision_ids = list(db.scalars(select(AuthorizationRevision.id).where(
            AuthorizationRevision.authorization_profile_id == profile_id
        )))
        db.execute(delete(AssetCandidateEvaluation).where(
            AssetCandidateEvaluation.authorization_revision_id.in_(revision_ids)
        ))
        db.execute(delete(AuthorizationRevision).where(
            AuthorizationRevision.id.in_(revision_ids)
        ))
        db.execute(delete(AuthorizationProfile).where(
            AuthorizationProfile.id == profile_id
        ))
        db.commit()


def test_lifecycle_transition_serializes_before_candidate_persistence() -> None:
    profile_id, revision_id = make_active_revision()
    started = Event()
    try:
        with SessionLocal() as transition_db:
            assert lock_profile(transition_db, profile_id) is not None
            revision = transition_db.scalar(
                select(AuthorizationRevision)
                .where(AuthorizationRevision.id == revision_id)
                .with_for_update()
            )

            def evaluate() -> str:
                started.set()
                with SessionLocal() as evaluation_db:
                    with pytest.raises(AssetCandidateEvaluationInactiveError):
                        create_asset_candidate_evaluation(
                            evaluation_db,
                            profile_id=profile_id,
                            revision_id=revision_id,
                            hostname="api.example.test",
                        )
                    evaluation_db.rollback()
                    return "denied"

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(evaluate)
                assert started.wait(timeout=2)
                revision.lifecycle_state = "revoked"
                transition_db.commit()
                assert future.result(timeout=5) == "denied"

        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                AssetCandidateEvaluation
            ).where(
                AssetCandidateEvaluation.authorization_revision_id == revision_id
            )) == 0
    finally:
        cleanup(profile_id)


def test_persistence_failure_commits_no_partial_evaluation(monkeypatch) -> None:
    profile_id, revision_id = make_active_revision()
    try:
        with SessionLocal() as db:
            def fail_commit() -> None:
                raise IntegrityError("insert", {}, RuntimeError("forced"))

            monkeypatch.setattr(db, "commit", fail_commit)
            with pytest.raises(IntegrityError):
                create_asset_candidate_evaluation(
                    db,
                    profile_id=profile_id,
                    revision_id=revision_id,
                    hostname="api.example.test",
                )
            db.rollback()
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                AssetCandidateEvaluation
            ).where(
                AssetCandidateEvaluation.authorization_revision_id == revision_id
            )) == 0
    finally:
        cleanup(profile_id)
