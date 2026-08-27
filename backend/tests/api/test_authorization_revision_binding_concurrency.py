from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db.models import AuthorizationProfile, AuthorizationRevision, Target
from app.db.session import SessionLocal
from app.main import app
from app.services.authorization_revision import create_revision, transition_revision


client = TestClient(app)


def test_binding_waits_for_revision_lock_and_rejects_concurrent_revoke() -> None:
    with SessionLocal() as db:
        profile = AuthorizationProfile(
            name=f"binding-race-{uuid4()}",
            program_name="Program",
            authorization_type="self_owned",
            automation_allowed=True,
            max_requests_per_second=1.0,
            allow_get=True,
            require_human_execution_approval=False,
        )
        target = Target(
            name=f"binding-race-target-{uuid4()}",
            base_url="https://example.test",
            environment="test",
            authorization_profile=profile,
        )
        db.add(target)
        db.commit()
        profile_id = profile.id
        target_id = target.id

    with SessionLocal() as db:
        revision_id = create_revision(db, profile_id).id
    with SessionLocal() as db:
        transition_revision(db, profile_id, revision_id, "active")

    request_started = Event()

    def bind_revision():
        request_started.set()
        return client.patch(
            f"/api/targets/{target_id}/authorization-revision",
            json={"authorization_revision_id": revision_id},
        )

    try:
        with SessionLocal() as lifecycle_db:
            lifecycle_db.scalar(
                select(AuthorizationProfile)
                .where(AuthorizationProfile.id == profile_id)
                .with_for_update()
            )
            revision = lifecycle_db.scalar(
                select(AuthorizationRevision)
                .where(AuthorizationRevision.id == revision_id)
                .with_for_update()
            )
            revision.lifecycle_state = "revoked"

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(bind_revision)
                assert request_started.wait(timeout=2)
                assert not future.done()
                lifecycle_db.commit()
                response = future.result(timeout=5)

        assert response.status_code == 409
        with SessionLocal() as db:
            assert (
                db.get(AuthorizationRevision, revision_id).lifecycle_state
                == "revoked"
            )
            assert db.get(Target, target_id).authorization_revision_id is None
    finally:
        with SessionLocal() as db:
            db.execute(delete(Target).where(Target.id == target_id))
            db.execute(
                delete(AuthorizationRevision).where(
                    AuthorizationRevision.id == revision_id
                )
            )
            db.execute(
                delete(AuthorizationProfile).where(
                    AuthorizationProfile.id == profile_id
                )
            )
            db.commit()
