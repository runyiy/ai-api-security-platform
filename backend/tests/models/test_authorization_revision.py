from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, inspect, select
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.db.models import AuthorizationProfile, AuthorizationRevision
from app.db.session import SessionLocal


LIFECYCLE_STATES = ("draft", "active", "superseded", "revoked")


def build_profile() -> AuthorizationProfile:
    return AuthorizationProfile(
        name=f"profile-{uuid4()}",
        program_name="Mutable Program",
        program_url="https://example.test/program",
        authorization_type="self_owned",
        authorization_reference="profile-reference",
        automation_allowed=False,
        max_requests_per_second=1.0,
        allow_get=False,
        require_human_execution_approval=True,
        notes="Mutable profile notes.",
    )


def build_revision(
    profile: AuthorizationProfile,
    *,
    revision_number: int = 1,
    lifecycle_state: str = "draft",
    **values: object,
) -> AuthorizationRevision:
    snapshot = {
        "authorization_profile": profile,
        "revision_number": revision_number,
        "lifecycle_state": lifecycle_state,
        "name": "Authorization Snapshot",
        "program_name": "Self-Controlled Security Program",
        "program_url": "https://example.test/security",
        "authorization_type": "self_owned",
        "authorization_reference": "written-authorization-001",
        "valid_from": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "valid_until": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "automation_allowed": True,
        "max_requests_per_second": 2.5,
        "allow_get": True,
        "allow_post": False,
        "allow_patch": True,
        "allow_put": False,
        "allow_delete": False,
        "require_human_execution_approval": False,
        "notes": "Immutable authorization snapshot.",
    }
    snapshot.update(values)
    return AuthorizationRevision(**snapshot)


def delete_profile_graph(profile_ids: list[int]) -> None:
    with SessionLocal() as db:
        db.execute(
            delete(AuthorizationRevision).where(
                AuthorizationRevision.authorization_profile_id.in_(profile_ids)
            )
        )
        db.execute(
            delete(AuthorizationProfile).where(
                AuthorizationProfile.id.in_(profile_ids)
            )
        )
        db.commit()


def test_authorization_revision_is_registered_and_append_oriented() -> None:
    table = Base.metadata.tables["authorization_revisions"]

    assert table is AuthorizationRevision.__table__
    assert set(table.columns.keys()) == {
        "id",
        "authorization_profile_id",
        "revision_number",
        "lifecycle_state",
        "name",
        "program_name",
        "program_url",
        "authorization_type",
        "authorization_reference",
        "valid_from",
        "valid_until",
        "automation_allowed",
        "max_requests_per_second",
        "allow_get",
        "allow_post",
        "allow_patch",
        "allow_put",
        "allow_delete",
        "require_human_execution_approval",
        "notes",
        "created_at",
    }
    assert "updated_at" not in table.columns
    assert all(column.onupdate is None for column in table.columns)


def test_snapshot_fields_round_trip_and_ignore_later_profile_mutation() -> None:
    profile_id: int | None = None
    revision_id: int | None = None

    try:
        with SessionLocal() as db:
            profile = build_profile()
            revision = build_revision(profile)
            db.add(revision)
            db.commit()
            profile_id = profile.id
            revision_id = revision.id

        with SessionLocal() as db:
            profile = db.get(AuthorizationProfile, profile_id)
            assert profile is not None
            profile.name = "Changed Profile"
            profile.program_name = "Changed Program"
            profile.automation_allowed = False
            profile.max_requests_per_second = 99.0
            profile.allow_get = False
            profile.notes = "Changed profile notes."
            db.commit()

        with SessionLocal() as db:
            revision = db.get(AuthorizationRevision, revision_id)
            profile = db.get(AuthorizationProfile, profile_id)
            assert revision is not None
            assert profile is not None
            assert revision.authorization_profile_id == profile_id
            assert revision.authorization_profile is profile
            assert profile.revisions == [revision]
            assert revision.revision_number == 1
            assert revision.lifecycle_state == "draft"
            assert revision.name == "Authorization Snapshot"
            assert revision.program_name == "Self-Controlled Security Program"
            assert revision.program_url == "https://example.test/security"
            assert revision.authorization_type == "self_owned"
            assert revision.authorization_reference == "written-authorization-001"
            assert revision.valid_from == datetime(2026, 8, 1, tzinfo=timezone.utc)
            assert revision.valid_until == datetime(2026, 9, 1, tzinfo=timezone.utc)
            assert revision.automation_allowed is True
            assert revision.max_requests_per_second == 2.5
            assert revision.allow_get is True
            assert revision.allow_post is False
            assert revision.allow_patch is True
            assert revision.allow_put is False
            assert revision.allow_delete is False
            assert revision.require_human_execution_approval is False
            assert revision.notes == "Immutable authorization snapshot."
            assert revision.created_at is not None
    finally:
        if profile_id is not None:
            delete_profile_graph([profile_id])


def test_lifecycle_state_defaults_to_draft() -> None:
    profile_id: int | None = None

    try:
        with SessionLocal() as db:
            profile = build_profile()
            revision = build_revision(profile)
            del revision.lifecycle_state
            db.add(revision)
            db.commit()
            profile_id = profile.id
            revision_id = revision.id

        with SessionLocal() as db:
            loaded = db.get(AuthorizationRevision, revision_id)
            assert loaded is not None
            assert loaded.lifecycle_state == "draft"
    finally:
        if profile_id is not None:
            delete_profile_graph([profile_id])


def test_revision_number_is_unique_per_profile_but_not_globally() -> None:
    profile_ids: list[int] = []

    try:
        with SessionLocal() as db:
            first_profile = build_profile()
            second_profile = build_profile()
            db.add_all(
                [
                    build_revision(first_profile, revision_number=1),
                    build_revision(first_profile, revision_number=2),
                    build_revision(second_profile, revision_number=1),
                ]
            )
            db.commit()
            profile_ids = [first_profile.id, second_profile.id]

        with SessionLocal() as db:
            revisions = list(
                db.scalars(
                    select(AuthorizationRevision).where(
                        AuthorizationRevision.authorization_profile_id
                        == profile_ids[0]
                    ).order_by(AuthorizationRevision.revision_number)
                ).all()
            )
            assert [revision.revision_number for revision in revisions] == [1, 2]

            first_profile = db.get(AuthorizationProfile, profile_ids[0])
            assert first_profile is not None
            db.add(build_revision(first_profile, revision_number=1))
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
    finally:
        if profile_ids:
            delete_profile_graph(profile_ids)


@pytest.mark.parametrize("revision_number", [0, -1])
def test_non_positive_revision_number_is_rejected(revision_number: int) -> None:
    with SessionLocal() as db:
        profile = build_profile()
        db.add(build_revision(profile, revision_number=revision_number))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


@pytest.mark.parametrize("lifecycle_state", LIFECYCLE_STATES)
def test_supported_lifecycle_states_round_trip(lifecycle_state: str) -> None:
    profile_id: int | None = None
    try:
        with SessionLocal() as db:
            profile = build_profile()
            revision = build_revision(profile, lifecycle_state=lifecycle_state)
            db.add(revision)
            db.commit()
            profile_id = profile.id
            revision_id = revision.id

        with SessionLocal() as db:
            loaded = db.get(AuthorizationRevision, revision_id)
            assert loaded is not None
            assert loaded.lifecycle_state == lifecycle_state
    finally:
        if profile_id is not None:
            delete_profile_graph([profile_id])


def test_invalid_lifecycle_state_is_rejected() -> None:
    with SessionLocal() as db:
        profile = build_profile()
        db.add(build_revision(profile, lifecycle_state="expired"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


@pytest.mark.parametrize("rate", [0.0, -1.0])
def test_non_positive_rate_is_rejected(rate: float) -> None:
    with SessionLocal() as db:
        profile = build_profile()
        db.add(build_revision(profile, max_requests_per_second=rate))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


@pytest.mark.parametrize("offset", [timedelta(0), timedelta(days=-1)])
def test_invalid_validity_window_is_rejected(offset: timedelta) -> None:
    valid_from = datetime(2026, 8, 1, tzinfo=timezone.utc)
    with SessionLocal() as db:
        profile = build_profile()
        db.add(
            build_revision(
                profile,
                valid_from=valid_from,
                valid_until=valid_from + offset,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_profile_delete_is_restricted_and_revision_survives_failed_delete() -> None:
    profile_id: int | None = None
    revision_id: int | None = None

    try:
        with SessionLocal() as db:
            profile = build_profile()
            revision = build_revision(profile)
            db.add(revision)
            db.commit()
            profile_id = profile.id
            revision_id = revision.id

        with SessionLocal() as db:
            profile = db.get(AuthorizationProfile, profile_id)
            assert profile is not None
            db.delete(profile)
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
            assert db.get(AuthorizationProfile, profile_id) is not None
            assert db.get(AuthorizationRevision, revision_id) is not None
    finally:
        if profile_id is not None:
            delete_profile_graph([profile_id])


def test_relationship_has_no_delete_orphan_or_destructive_cascade() -> None:
    relationship = inspect(AuthorizationProfile).relationships.revisions

    assert relationship.passive_deletes == "all"
    assert "delete" not in relationship.cascade
    assert "delete-orphan" not in relationship.cascade
