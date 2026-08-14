from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.db.models import AuthorizationProfile
from app.db.session import SessionLocal


def required_profile_values() -> dict[str, object]:
    return {
        "name": f"profile-{uuid4()}",
        "program_name": "Self-Controlled Security Program",
        "authorization_type": "self_owned",
    }


def test_authorization_profile_is_registered_in_metadata() -> None:
    table = Base.metadata.tables["authorization_profiles"]

    assert table is AuthorizationProfile.__table__


def test_authorization_profile_can_be_persisted_and_loaded() -> None:
    valid_from = datetime(2026, 8, 14, tzinfo=timezone.utc)
    valid_until = valid_from + timedelta(days=30)
    profile_id: int | None = None

    try:
        with SessionLocal() as db:
            profile = AuthorizationProfile(
                **required_profile_values(),
                program_url="https://example.test/security",
                authorization_reference="written-authorization-001",
                valid_from=valid_from,
                valid_until=valid_until,
                automation_allowed=True,
                max_requests_per_second=2.5,
                allow_get=True,
                allow_post=False,
                allow_patch=False,
                allow_put=False,
                allow_delete=False,
                require_human_execution_approval=False,
                notes="Authorized self-controlled test environment.",
            )
            db.add(profile)
            db.commit()
            profile_id = profile.id

        with SessionLocal() as db:
            loaded = db.get(AuthorizationProfile, profile_id)

            assert loaded is not None
            assert loaded.program_url == "https://example.test/security"
            assert loaded.authorization_reference == "written-authorization-001"
            assert loaded.valid_from == valid_from
            assert loaded.valid_until == valid_until
            assert loaded.automation_allowed is True
            assert loaded.max_requests_per_second == 2.5
            assert loaded.allow_get is True
            assert loaded.require_human_execution_approval is False
            assert loaded.created_at is not None
            assert loaded.updated_at is not None
    finally:
        if profile_id is not None:
            with SessionLocal() as db:
                db.execute(
                    delete(AuthorizationProfile).where(
                        AuthorizationProfile.id == profile_id
                    )
                )
                db.commit()


def test_authorization_profile_defaults_fail_closed() -> None:
    profile_id: int | None = None

    try:
        with SessionLocal() as db:
            profile = AuthorizationProfile(**required_profile_values())
            db.add(profile)
            db.commit()
            db.refresh(profile)
            profile_id = profile.id

            assert profile.automation_allowed is False
            assert profile.max_requests_per_second == 1.0
            assert profile.allow_get is False
            assert profile.allow_post is False
            assert profile.allow_patch is False
            assert profile.allow_put is False
            assert profile.allow_delete is False
            assert profile.require_human_execution_approval is True
    finally:
        if profile_id is not None:
            with SessionLocal() as db:
                db.execute(
                    delete(AuthorizationProfile).where(
                        AuthorizationProfile.id == profile_id
                    )
                )
                db.commit()


@pytest.mark.parametrize("rate_limit", [0.0, -1.0])
def test_non_positive_rate_limit_is_rejected_by_postgresql(
    rate_limit: float,
) -> None:
    with SessionLocal() as db:
        profile = AuthorizationProfile(
            **required_profile_values(),
            max_requests_per_second=rate_limit,
        )
        db.add(profile)

        with pytest.raises(IntegrityError):
            db.commit()

        db.rollback()


@pytest.mark.parametrize("valid_until_offset", [timedelta(0), timedelta(days=-1)])
def test_invalid_validity_window_is_rejected_by_postgresql(
    valid_until_offset: timedelta,
) -> None:
    valid_from = datetime(2026, 8, 14, tzinfo=timezone.utc)

    with SessionLocal() as db:
        profile = AuthorizationProfile(
            **required_profile_values(),
            valid_from=valid_from,
            valid_until=valid_from + valid_until_offset,
        )
        db.add(profile)

        with pytest.raises(IntegrityError):
            db.commit()

        db.rollback()
