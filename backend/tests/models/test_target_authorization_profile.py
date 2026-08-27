from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.target import Target
from app.db.session import SessionLocal


def build_target(**values: object) -> Target:
    return Target(
        name=f"target-{uuid4()}",
        base_url="https://example.test",
        environment="test",
        is_enabled=True,
        **values,
    )


def build_profile() -> AuthorizationProfile:
    return AuthorizationProfile(
        name=f"profile-{uuid4()}",
        program_name="Self-Controlled Security Program",
        authorization_type="self_owned",
    )


def test_target_network_mode_database_check_rejects_unknown_value() -> None:
    with SessionLocal() as db:
        db.add(build_target(network_mode="public"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_target_can_remain_unbound_without_implicit_authorization() -> None:
    target_id: int | None = None

    try:
        with SessionLocal() as db:
            profile_count_before = db.scalar(
                select(func.count(AuthorizationProfile.id))
            )
            target = build_target()
            db.add(target)
            db.commit()
            db.refresh(target)
            target_id = target.id

            profile_count_after = db.scalar(
                select(func.count(AuthorizationProfile.id))
            )

            assert target.authorization_profile_id is None
            assert target.authorization_profile is None
            assert profile_count_after == profile_count_before
    finally:
        if target_id is not None:
            with SessionLocal() as db:
                db.execute(delete(Target).where(Target.id == target_id))
                db.commit()


def test_target_and_profile_relationships_work_in_both_directions() -> None:
    target_id: int | None = None
    profile_id: int | None = None

    try:
        with SessionLocal() as db:
            profile = build_profile()
            target = build_target(authorization_profile=profile)
            db.add(target)
            db.commit()
            target_id = target.id
            profile_id = profile.id

        with SessionLocal() as db:
            loaded_target = db.get(Target, target_id)
            loaded_profile = db.get(AuthorizationProfile, profile_id)

            assert loaded_target is not None
            assert loaded_profile is not None
            assert loaded_target.authorization_profile_id == profile_id
            assert loaded_target.authorization_profile is loaded_profile
            assert loaded_profile.targets == [loaded_target]
    finally:
        if target_id is not None or profile_id is not None:
            with SessionLocal() as db:
                if target_id is not None:
                    db.execute(delete(Target).where(Target.id == target_id))
                if profile_id is not None:
                    db.execute(
                        delete(AuthorizationProfile).where(
                            AuthorizationProfile.id == profile_id
                        )
                    )
                db.commit()


def test_invalid_authorization_profile_id_is_rejected_by_postgresql() -> None:
    with SessionLocal() as db:
        maximum_profile_id = db.scalar(
            select(func.max(AuthorizationProfile.id))
        )
        nonexistent_profile_id = (maximum_profile_id or 0) + 1_000_000
        target = build_target(
            authorization_profile_id=nonexistent_profile_id,
        )
        db.add(target)

        with pytest.raises(IntegrityError):
            db.commit()

        db.rollback()


def test_referenced_profile_delete_is_rejected_by_postgresql() -> None:
    target_id: int | None = None
    profile_id: int | None = None

    try:
        with SessionLocal() as db:
            profile = build_profile()
            target = build_target(authorization_profile=profile)
            db.add(target)
            db.commit()
            target_id = target.id
            profile_id = profile.id

        with SessionLocal() as db:
            profile = db.get(AuthorizationProfile, profile_id)
            assert profile is not None

            db.delete(profile)

            with pytest.raises(IntegrityError):
                db.commit()

            db.rollback()
    finally:
        if target_id is not None or profile_id is not None:
            with SessionLocal() as db:
                if target_id is not None:
                    db.execute(delete(Target).where(Target.id == target_id))
                if profile_id is not None:
                    db.execute(
                        delete(AuthorizationProfile).where(
                            AuthorizationProfile.id == profile_id
                        )
                    )
                db.commit()
