from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db.models.target import Target
from app.db.models.test_identity import TestIdentity as StoredIdentity
from app.db.session import SessionLocal
from app.main import app


client = TestClient(app)


def test_test_identity_bearer_crud_remains_on_transitional_path() -> None:
    target_id: int | None = None

    try:
        with SessionLocal() as db:
            target = Target(
                name=f"identity-regression-{uuid4()}",
                base_url="https://example.test",
                environment="test",
                is_enabled=True,
            )
            db.add(target)
            db.commit()
            target_id = target.id

        create_response = client.post(
            "/api/test-identities",
            json={
                "target_id": target_id,
                "name": f"bearer-identity-{uuid4()}",
                "role": "user",
                "auth_type": "bearer",
                "access_token": "synthetic-initial-token",
            },
        )

        assert create_response.status_code == 201
        created = create_response.json()
        assert "credentials" not in created
        assert "credential_bindings" not in created

        update_response = client.put(
            f"/api/test-identities/{created['id']}/token",
            json={"access_token": "synthetic-updated-token"},
        )
        list_response = client.get(
            f"/api/targets/{target_id}/test-identities"
        )

        assert update_response.status_code == 200
        assert list_response.status_code == 200
        assert list_response.json() == [update_response.json()]
        assert "credentials" not in update_response.json()
        assert "credential_bindings" not in update_response.json()

        with SessionLocal() as db:
            identity = db.get(StoredIdentity, created["id"])

            assert identity is not None
            assert identity.credentials == {
                "access_token": "synthetic-updated-token"
            }
            assert identity.credential_bindings == []
    finally:
        if target_id is not None:
            with SessionLocal() as db:
                db.execute(delete(Target).where(Target.id == target_id))
                db.commit()
