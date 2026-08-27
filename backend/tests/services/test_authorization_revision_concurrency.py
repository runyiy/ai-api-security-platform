from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4

from sqlalchemy import delete, select

from app.db.models import AuthorizationProfile, AuthorizationRevision
from app.db.session import SessionLocal
from app.services.authorization_revision import create_revision, transition_revision


def test_concurrent_zero_revision_creation_serializes_on_profile_lock() -> None:
    with SessionLocal() as db:
        profile = AuthorizationProfile(
            name=f"concurrent-revision-{uuid4()}",
            program_name="Program",
            authorization_type="self_owned",
            automation_allowed=True,
            max_requests_per_second=1.0,
            allow_get=True,
            require_human_execution_approval=False,
        )
        db.add(profile)
        db.commit()
        profile_id = profile.id

    barrier = Barrier(2)

    def worker() -> int:
        with SessionLocal() as db:
            barrier.wait()
            return create_revision(db, profile_id).revision_number

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            numbers = sorted(future.result() for future in futures)
        assert numbers == [1, 2]
    finally:
        with SessionLocal() as db:
            db.execute(delete(AuthorizationRevision).where(
                AuthorizationRevision.authorization_profile_id == profile_id
            ))
            db.execute(delete(AuthorizationProfile).where(AuthorizationProfile.id == profile_id))
            db.commit()


def test_creation_waits_for_profile_update_and_takes_coherent_snapshot() -> None:
    with SessionLocal() as setup:
        profile = AuthorizationProfile(
            name=f"coherent-revision-{uuid4()}",
            program_name="Before",
            authorization_type="self_owned",
            automation_allowed=False,
            max_requests_per_second=1.0,
            allow_get=False,
            require_human_execution_approval=True,
        )
        setup.add(profile)
        setup.commit()
        profile_id = profile.id

    started = Event()

    def creator() -> tuple[str, bool]:
        with SessionLocal() as db:
            started.set()
            revision = create_revision(db, profile_id)
            return revision.program_name, revision.allow_get

    try:
        with SessionLocal() as patch_db:
            locked = patch_db.scalar(
                select(AuthorizationProfile)
                .where(AuthorizationProfile.id == profile_id)
                .with_for_update()
            )
            locked.program_name = "After"
            locked.allow_get = True
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(creator)
                assert started.wait(timeout=2)
                assert not future.done()
                patch_db.commit()
                assert future.result(timeout=5) == ("After", True)
    finally:
        with SessionLocal() as db:
            db.execute(delete(AuthorizationRevision).where(
                AuthorizationRevision.authorization_profile_id == profile_id
            ))
            db.execute(delete(AuthorizationProfile).where(AuthorizationProfile.id == profile_id))
            db.commit()


def test_concurrent_draft_activations_serialize_without_duplicate_active() -> None:
    with SessionLocal() as db:
        profile = AuthorizationProfile(
            name=f"activation-race-{uuid4()}",
            program_name="Program",
            authorization_type="self_owned",
            automation_allowed=True,
            max_requests_per_second=1.0,
            allow_get=True,
            require_human_execution_approval=False,
        )
        db.add(profile)
        db.commit()
        profile_id = profile.id

    with SessionLocal() as db:
        first_id = create_revision(db, profile_id).id
    with SessionLocal() as db:
        second_id = create_revision(db, profile_id).id

    barrier = Barrier(2)

    def activate(revision_id: int) -> int:
        with SessionLocal() as db:
            barrier.wait()
            transition_revision(
                db,
                profile_id,
                revision_id,
                "active",
            )
            return revision_id

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(activate, first_id),
                pool.submit(activate, second_id),
            ]
            assert {
                future.result(timeout=5) for future in futures
            } == {first_id, second_id}

        with SessionLocal() as db:
            states = list(
                db.scalars(
                    select(AuthorizationRevision.lifecycle_state)
                    .where(
                        AuthorizationRevision.authorization_profile_id
                        == profile_id
                    )
                    .order_by(AuthorizationRevision.id)
                ).all()
            )
            assert sorted(states) == ["active", "superseded"]
            assert states.count("active") == 1
    finally:
        with SessionLocal() as db:
            db.execute(
                delete(AuthorizationRevision).where(
                    AuthorizationRevision.authorization_profile_id
                    == profile_id
                )
            )
            db.execute(
                delete(AuthorizationProfile).where(
                    AuthorizationProfile.id == profile_id
                )
            )
            db.commit()
