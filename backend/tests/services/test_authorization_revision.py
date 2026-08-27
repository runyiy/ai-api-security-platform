from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.db.models import AuthorizationProfile, AuthorizationRevision
from app.db.session import SessionLocal
from app.services.authorization_revision import (
    InvalidRevisionTransitionError,
    SNAPSHOT_FIELDS,
    create_revision,
    transition_revision,
)


def make_profile(db, *, allow_get: bool = True) -> AuthorizationProfile:
    profile = AuthorizationProfile(
        name=f"revision-service-{uuid4()}",
        program_name="Program",
        authorization_type="self_owned",
        authorization_reference="reference",
        automation_allowed=True,
        max_requests_per_second=2.0,
        allow_get=allow_get,
        allow_post=False,
        allow_patch=False,
        allow_put=False,
        allow_delete=False,
        require_human_execution_approval=False,
        notes="snapshot",
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def cleanup(profile_ids: list[int]) -> None:
    with SessionLocal() as db:
        db.execute(delete(AuthorizationRevision).where(
            AuthorizationRevision.authorization_profile_id.in_(profile_ids)
        ))
        db.execute(delete(AuthorizationProfile).where(AuthorizationProfile.id.in_(profile_ids)))
        db.commit()


def test_creation_snapshots_profile_and_numbers_per_profile() -> None:
    ids = []
    try:
        with SessionLocal() as db:
            first = make_profile(db)
            second = make_profile(db, allow_get=False)
            ids = [first.id, second.id]
            first_id, second_id = first.id, second.id

        with SessionLocal() as db:
            one = create_revision(db, first_id)
            assert one.revision_number == 1
            assert one.lifecycle_state == "draft"
            assert one.allow_get is True
        with SessionLocal() as db:
            profile = db.get(AuthorizationProfile, first_id)
            profile.allow_get = False
            profile.notes = "changed"
            db.commit()
        with SessionLocal() as db:
            two = create_revision(db, first_id)
            assert two.revision_number == 2
            assert two.allow_get is False
            other = create_revision(db, second_id)
            assert other.revision_number == 1
        with SessionLocal() as db:
            original = db.scalar(select(AuthorizationRevision).where(
                AuthorizationRevision.authorization_profile_id == first_id,
                AuthorizationRevision.revision_number == 1,
            ))
            assert original.allow_get is True
            assert original.notes == "snapshot"
    finally:
        cleanup(ids)


def test_lifecycle_supersedes_and_terminal_states_fail_closed() -> None:
    ids = []
    try:
        with SessionLocal() as db:
            profile = make_profile(db)
            ids = [profile.id]
            profile_id = profile.id
        with SessionLocal() as db:
            first = create_revision(db, profile_id)
            first_id = first.id
        with SessionLocal() as db:
            second = create_revision(db, profile_id)
            second_id = second.id
        with SessionLocal() as db:
            assert transition_revision(db, profile_id, first_id, "active").lifecycle_state == "active"
        with SessionLocal() as db:
            transition_revision(db, profile_id, second_id, "active")
        with SessionLocal() as db:
            assert db.get(AuthorizationRevision, first_id).lifecycle_state == "superseded"
            assert db.get(AuthorizationRevision, second_id).lifecycle_state == "active"
        with SessionLocal() as db:
            with pytest.raises(InvalidRevisionTransitionError):
                transition_revision(db, profile_id, first_id, "active")
    finally:
        cleanup(ids)


def test_database_prevents_two_active_revisions_for_one_profile() -> None:
    ids = []
    try:
        with SessionLocal() as db:
            profile = make_profile(db)
            ids = [profile.id]
            profile_id = profile.id
        with SessionLocal() as db:
            first = create_revision(db, profile_id)
            first_id = first.id
        with SessionLocal() as db:
            second = create_revision(db, profile_id)
            second_id = second.id
        with SessionLocal() as db:
            db.get(AuthorizationRevision, first_id).lifecycle_state = "active"
            db.get(AuthorizationRevision, second_id).lifecycle_state = "active"
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
    finally:
        cleanup(ids)


def test_revoke_transitions_are_terminal_and_preserve_snapshots() -> None:
    ids = []
    try:
        with SessionLocal() as db:
            profile = make_profile(db)
            ids = [profile.id]
            profile_id = profile.id

        with SessionLocal() as db:
            draft = create_revision(db, profile_id)
            draft_id = draft.id
            draft_snapshot = {
                field: getattr(draft, field)
                for field in SNAPSHOT_FIELDS
            }
        with SessionLocal() as db:
            revoked_draft = transition_revision(
                db,
                profile_id,
                draft_id,
                "revoked",
            )
            assert revoked_draft.lifecycle_state == "revoked"
            assert {
                field: getattr(revoked_draft, field)
                for field in draft_snapshot
            } == draft_snapshot
        for destination in ("active", "revoked"):
            with SessionLocal() as db:
                with pytest.raises(InvalidRevisionTransitionError):
                    transition_revision(
                        db,
                        profile_id,
                        draft_id,
                        destination,
                    )

        with SessionLocal() as db:
            active = create_revision(db, profile_id)
            active_id = active.id
            active_snapshot = {
                field: getattr(active, field)
                for field in SNAPSHOT_FIELDS
            }
        with SessionLocal() as db:
            transition_revision(db, profile_id, active_id, "active")
        with SessionLocal() as db:
            revoked_active = transition_revision(
                db,
                profile_id,
                active_id,
                "revoked",
            )
            assert revoked_active.lifecycle_state == "revoked"
            assert {
                field: getattr(revoked_active, field)
                for field in active_snapshot
            } == active_snapshot

        with SessionLocal() as db:
            old_active_id = create_revision(db, profile_id).id
        with SessionLocal() as db:
            transition_revision(db, profile_id, old_active_id, "active")
        with SessionLocal() as db:
            replacement_id = create_revision(db, profile_id).id
        with SessionLocal() as db:
            transition_revision(db, profile_id, replacement_id, "active")
        with SessionLocal() as db:
            assert (
                db.get(AuthorizationRevision, old_active_id).lifecycle_state
                == "superseded"
            )
            with pytest.raises(InvalidRevisionTransitionError):
                transition_revision(
                    db,
                    profile_id,
                    old_active_id,
                    "revoked",
                )
    finally:
        cleanup(ids)
