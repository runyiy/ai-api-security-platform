from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db.models.authorization_profile import AuthorizationProfile
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
            == "authorization_profile_missing"
        )
    finally:
        delete_policy_rows(target_id=target_id)


def test_policy_check_uses_loaded_authorization_profile() -> None:
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
        target = Target(
            name=f"policy-target-{uuid4()}",
            base_url="http://localhost:8001",
            environment="test",
            is_enabled=True,
            authorization_profile_id=profile.id,
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

    try:
        payload = {
            "target_id": target_id,
            "url": "http://localhost:8001/projects/1",
            "method": "GET",
        }
        denied_response = client.post("/api/policy/check", json=payload)

        assert denied_response.status_code == 200
        assert denied_response.json()["allowed"] is False
        assert denied_response.json()["code"] == "automation_not_allowed"

        with SessionLocal() as db:
            stored_profile = db.get(AuthorizationProfile, profile_id)
            assert stored_profile is not None
            stored_profile.automation_allowed = True
            db.commit()

        allowed_response = client.post("/api/policy/check", json=payload)

        assert allowed_response.status_code == 200
        assert allowed_response.json()["allowed"] is True
        assert allowed_response.json()["code"] == "allowed_by_scope"
        assert allowed_response.json()["matched_scope_id"] is not None
    finally:
        delete_policy_rows(target_id=target_id, profile_id=profile_id)
