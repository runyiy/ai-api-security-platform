from datetime import datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.scope import Scope
from app.db.models.target import Target
from app.db.session import SessionLocal
from app.main import app


client = TestClient(app)


def delete_policy_rows(
    *,
    target_id: int,
    profile_id: int | None = None,
) -> None:
    with SessionLocal() as db:
        db.execute(delete(Target).where(Target.id == target_id))
        if profile_id is not None:
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


def test_policy_check_denies_unbound_target() -> None:
    with SessionLocal() as db:
        target = Target(
            name=f"policy-unbound-{uuid4()}",
            base_url="http://localhost:8001",
            environment="test",
            is_enabled=True,
        )
        db.add(target)
        db.commit()
        target_id = target.id

    try:
        response = client.post(
            "/api/policy/check",
            json={
                "target_id": target_id,
                "url": "http://localhost:8001/projects/1",
                "method": "GET",
            },
        )

        assert response.status_code == 200
        assert response.json()["allowed"] is False
        assert (
            response.json()["code"]
            == "authorization_revision_missing"
        )
        assert response.json()["authorization_profile_id"] is None
        assert response.json()["authorization_revision_id"] is None
        evaluated_at = datetime.fromisoformat(
            response.json()["evaluated_at"]
        )
        assert evaluated_at.tzinfo is not None
        assert evaluated_at.utcoffset() == timedelta(0)
    finally:
        delete_policy_rows(target_id=target_id)


def test_policy_check_uses_exact_revision_not_mutable_profile() -> None:
    with SessionLocal() as db:
        profile = AuthorizationProfile(
            name=f"policy-profile-{uuid4()}",
            program_name="Self-controlled policy test",
            authorization_type="self_owned",
            automation_allowed=False,
            allow_get=True,
            require_human_execution_approval=False,
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
            automation_allowed=True,
            max_requests_per_second=10.0,
            allow_get=True,
            require_human_execution_approval=False,
        )
        db.add(revision)
        db.flush()
        target = Target(
            name=f"policy-target-{uuid4()}",
            base_url="http://localhost:8001",
            environment="test",
            is_enabled=True,
            authorization_profile_id=profile.id,
            authorization_revision_id=revision.id,
        )
        db.add(target)
        db.flush()
        db.add(
            Scope(
                target_id=target.id,
                hostname="localhost",
                path_pattern="/projects/*",
                allowed_methods=["GET"],
                is_active=True,
            )
        )
        db.commit()
        target_id = target.id
        profile_id = profile.id
        revision_id = revision.id

    try:
        payload = {
            "target_id": target_id,
            "url": "http://localhost:8001/projects/1",
            "method": "GET",
        }
        allowed_response = client.post("/api/policy/check", json=payload)
        assert allowed_response.status_code == 200
        assert allowed_response.json()["allowed"] is True

        with SessionLocal() as db:
            stored_profile = db.get(AuthorizationProfile, profile_id)
            assert stored_profile is not None
            stored_profile.automation_allowed = False
            stored_profile.allow_get = False
            db.commit()

        allowed_response = client.post("/api/policy/check", json=payload)

        assert allowed_response.status_code == 200
        assert allowed_response.json()["allowed"] is True
        assert allowed_response.json()["code"] == "allowed_by_scope"
        assert allowed_response.json()["matched_scope_id"] is not None
        assert (
            allowed_response.json()["authorization_profile_id"]
            == profile_id
        )
        assert allowed_response.json()["authorization_revision_id"] == revision_id
        allowed_evaluated_at = datetime.fromisoformat(
            allowed_response.json()["evaluated_at"]
        )
        assert allowed_evaluated_at.tzinfo is not None
        assert allowed_evaluated_at.utcoffset() == timedelta(0)
        assert set(allowed_response.json()) == {
            "allowed",
            "code",
            "reason",
            "matched_scope_id",
            "authorization_profile_id",
            "authorization_revision_id",
            "evaluated_at",
        }
    finally:
        delete_policy_rows(target_id=target_id, profile_id=profile_id)
