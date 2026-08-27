from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db.models import AuthorizationProfile, AuthorizationRevision, Target
from app.db.session import SessionLocal
from app.main import app


client = TestClient(app)


def test_revision_history_lifecycle_and_exact_target_binding() -> None:
    profile_ids: list[int] = []
    target_ids: list[int] = []
    try:
        profiles = []
        for suffix in ("a", "b"):
            response = client.post("/api/authorization-profiles", json={
                "name": f"profile-{suffix}-{uuid4()}",
                "program_name": f"program-{suffix}",
                "authorization_type": "self_owned",
                "automation_allowed": True,
                "allow_get": True,
            })
            assert response.status_code == 201
            profiles.append(response.json())
        profile_ids = [item["id"] for item in profiles]

        revisions = []
        for profile in profiles:
            response = client.post(f"/api/authorization-profiles/{profile['id']}/revisions")
            assert response.status_code == 201
            assert response.json()["lifecycle_state"] == "draft"
            revisions.append(response.json())

        activate = client.post(
            f"/api/authorization-profiles/{profile_ids[0]}/revisions/{revisions[0]['id']}/activate"
        )
        assert activate.status_code == 200
        assert activate.json()["lifecycle_state"] == "active"

        listing = client.get(f"/api/authorization-profiles/{profile_ids[0]}/revisions")
        assert listing.status_code == 200
        assert [item["id"] for item in listing.json()] == [revisions[0]["id"]]

        target_response = client.post("/api/targets", json={
            "name": f"target-{uuid4()}",
            "base_url": "https://example.test",
            "environment": "test",
        })
        target = target_response.json()
        target_ids = [target["id"]]
        assert target["authorization_revision_id"] is None

        no_profile = client.patch(
            f"/api/targets/{target['id']}/authorization-revision",
            json={"authorization_revision_id": revisions[0]["id"]},
        )
        assert no_profile.status_code == 409
        assert client.patch(
            f"/api/targets/{target['id']}/authorization-profile",
            json={"authorization_profile_id": profile_ids[0]},
        ).status_code == 200

        draft = client.patch(
            f"/api/targets/{target['id']}/authorization-revision",
            json={"authorization_revision_id": revisions[1]["id"]},
        )
        assert draft.status_code == 409
        bound = client.patch(
            f"/api/targets/{target['id']}/authorization-revision",
            json={"authorization_revision_id": revisions[0]["id"]},
        )
        assert bound.status_code == 200
        assert bound.json()["authorization_revision_id"] == revisions[0]["id"]

        inconsistent = client.patch(
            f"/api/targets/{target['id']}/authorization-profile",
            json={"authorization_profile_id": profile_ids[1]},
        )
        assert inconsistent.status_code == 409
        unbound = client.patch(
            f"/api/targets/{target['id']}/authorization-revision",
            json={"authorization_revision_id": None},
        )
        assert unbound.status_code == 200
        assert unbound.json()["authorization_revision_id"] is None
    finally:
        with SessionLocal() as db:
            db.execute(delete(Target).where(Target.id.in_(target_ids)))
            db.execute(delete(AuthorizationRevision).where(
                AuthorizationRevision.authorization_profile_id.in_(profile_ids)
            ))
            db.execute(delete(AuthorizationProfile).where(AuthorizationProfile.id.in_(profile_ids)))
            db.commit()
