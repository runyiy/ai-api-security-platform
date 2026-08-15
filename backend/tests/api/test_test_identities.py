from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db.models.target import Target
from app.db.models.test_identity import TestIdentity as StoredIdentity
from app.db.session import SessionLocal
from app.main import app


client = TestClient(app)


def test_existing_bearer_identity_crud_remains_unchanged() -> None:
    token = "local-identity-fixture-token"
    replacement = "local-identity-replacement-token"

    with SessionLocal() as db:
        target = Target(
            name=f"identity-compatibility-{uuid4()}",
            base_url="https://example.test",
            environment="test",
            is_enabled=True,
        )
        db.add(target)
        db.commit()
        target_id = target.id

    try:
        created = client.post(
            "/api/test-identities",
            json={
                "target_id": target_id,
                "name": "Compatibility User",
                "auth_type": "bearer",
                "access_token": token,
            },
        )

        assert created.status_code == 201
        assert "credentials" not in created.json()
        assert "access_token" not in created.json()
        identity_id = created.json()["id"]

        listed = client.get(
            f"/api/targets/{target_id}/test-identities"
        )

        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [identity_id]
        assert "credentials" not in listed.json()[0]

        updated = client.put(
            f"/api/test-identities/{identity_id}/token",
            json={"access_token": replacement},
        )

        assert updated.status_code == 200
        assert "credentials" not in updated.json()

        with SessionLocal() as db:
            stored = db.get(StoredIdentity, identity_id)
            assert stored is not None
            assert stored.credentials == {"access_token": replacement}
    finally:
        with SessionLocal() as db:
            db.execute(delete(Target).where(Target.id == target_id))
            db.commit()
