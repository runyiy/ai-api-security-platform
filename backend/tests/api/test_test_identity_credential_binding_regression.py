import base64
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, func, select

from app.core.config import settings
from app.credentials.stored_secret import StoredSecretCipher, StoredSecretProvider
from app.api.routes.test_identities import update_bearer_token
from app.db.models.credential_binding import CredentialBinding
from app.db.models.credential_secret_version import CredentialSecretVersion
from app.db.models.target import Target
from app.db.models.test_identity import TestIdentity
from app.db.session import SessionLocal
from app.main import app
from app.schemas.test_identity import BearerTokenUpdate


client = TestClient(app)
KEY = base64.urlsafe_b64encode(b"a" * 32).decode("ascii")


@pytest.fixture
def target_id() -> Iterator[int]:
    with SessionLocal() as db:
        target = Target(
            name=f"identity-regression-{uuid4()}",
            base_url="https://example.test",
            environment="test",
            is_enabled=True,
        )
        db.add(target)
        db.commit()
        stored_target_id = target.id

    try:
        yield stored_target_id
    finally:
        with SessionLocal() as db:
            binding_ids = select(CredentialBinding.id).join(TestIdentity).where(
                TestIdentity.target_id == stored_target_id
            )
            db.execute(
                delete(CredentialSecretVersion).where(
                    CredentialSecretVersion.credential_binding_id.in_(binding_ids)
                )
            )
            db.execute(
                delete(CredentialBinding).where(
                    CredentialBinding.id.in_(binding_ids)
                )
            )
            db.execute(delete(Target).where(Target.id == stored_target_id))
            db.commit()


@pytest.fixture
def configured_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "credential_encryption_key", SecretStr(KEY))
    monkeypatch.setattr(settings, "credential_encryption_key_version", "test-v1")


def test_bearer_create_and_update_use_encrypted_binding_versions(
    target_id: int,
    configured_key: None,
) -> None:
    initial_token = "synthetic-initial-token"
    updated_token = "synthetic-updated-token"
    create_response = client.post(
        "/api/test-identities",
        json={
            "target_id": target_id,
            "name": f"bearer-identity-{uuid4()}",
            "role": "user",
            "auth_type": "bearer",
            "access_token": initial_token,
        },
    )

    assert create_response.status_code == 201
    created = create_response.json()
    assert initial_token not in create_response.text
    assert "credentials" not in created
    assert "credential_bindings" not in created

    update_response = client.put(
        f"/api/test-identities/{created['id']}/token",
        json={"access_token": updated_token},
    )
    assert update_response.status_code == 200
    assert updated_token not in update_response.text

    with SessionLocal() as db:
        identity = db.get(TestIdentity, created["id"])
        assert identity is not None
        assert identity.credentials is None
        bindings = list(
            db.scalars(
                select(CredentialBinding).where(
                    CredentialBinding.test_identity_id == identity.id,
                    CredentialBinding.auth_type == "bearer",
                    CredentialBinding.source_type == "stored_secret",
                    CredentialBinding.is_active.is_(True),
                )
            ).all()
        )
        assert len(bindings) == 1
        versions = list(
            db.scalars(
                select(CredentialSecretVersion)
                .where(
                    CredentialSecretVersion.credential_binding_id
                    == bindings[0].id
                )
                .order_by(CredentialSecretVersion.id)
            ).all()
        )
        assert len(versions) == 2
        assert initial_token not in versions[0].encrypted_envelope
        assert updated_token not in versions[1].encrypted_envelope
        provider = StoredSecretProvider(StoredSecretCipher.from_settings(settings))
        assert provider.load_secret(versions[-1]).get_secret_value() == updated_token


def test_anonymous_create_remains_binding_free(
    target_id: int,
) -> None:
    response = client.post(
        "/api/test-identities",
        json={
            "target_id": target_id,
            "name": f"anonymous-{uuid4()}",
            "auth_type": "anonymous",
        },
    )

    assert response.status_code == 201
    with SessionLocal() as db:
        identity = db.get(TestIdentity, response.json()["id"])
        assert identity is not None
        assert identity.credentials is None
        assert db.scalar(
            select(func.count(CredentialBinding.id)).where(
                CredentialBinding.test_identity_id == identity.id
            )
        ) == 0


def test_bearer_create_failure_rolls_back_entire_graph(
    target_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "synthetic-create-failure-token"
    identity_name = f"failed-bearer-{uuid4()}"
    monkeypatch.setattr(settings, "credential_encryption_key", None)

    response = client.post(
        "/api/test-identities",
        json={
            "target_id": target_id,
            "name": identity_name,
            "auth_type": "bearer",
            "access_token": token,
        },
    )

    assert response.status_code == 409
    assert token not in response.text
    with SessionLocal() as db:
        assert db.scalar(
            select(func.count(TestIdentity.id)).where(
                TestIdentity.target_id == target_id,
                TestIdentity.name == identity_name,
            )
        ) == 0
        assert db.scalar(select(func.count(CredentialBinding.id))) == 0
        assert db.scalar(select(func.count(CredentialSecretVersion.id))) == 0


def test_legacy_update_migrates_only_after_encrypted_store_succeeds(
    target_id: int,
    configured_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_token = "synthetic-legacy-token"
    replacement = "synthetic-replacement-token"
    with SessionLocal() as db:
        identity = TestIdentity(
            target_id=target_id,
            name=f"legacy-{uuid4()}",
            role=None,
            auth_type="bearer",
            credentials={"access_token": legacy_token},
            is_active=True,
        )
        db.add(identity)
        db.commit()
        identity_id = identity.id

    monkeypatch.setattr(settings, "credential_encryption_key", None)
    failed = client.put(
        f"/api/test-identities/{identity_id}/token",
        json={"access_token": replacement},
    )
    assert failed.status_code == 409
    assert replacement not in failed.text

    with SessionLocal() as db:
        identity = db.get(TestIdentity, identity_id)
        assert identity is not None
        assert identity.credentials == {"access_token": legacy_token}
        assert db.scalar(
            select(func.count(CredentialBinding.id)).where(
                CredentialBinding.test_identity_id == identity_id
            )
        ) == 0

    monkeypatch.setattr(settings, "credential_encryption_key", SecretStr(KEY))
    migrated = client.put(
        f"/api/test-identities/{identity_id}/token",
        json={"access_token": replacement},
    )
    assert migrated.status_code == 200
    assert replacement not in migrated.text

    with SessionLocal() as db:
        identity = db.get(TestIdentity, identity_id)
        assert identity is not None
        assert identity.credentials is None
        assert db.scalar(
            select(func.count(CredentialBinding.id)).where(
                CredentialBinding.test_identity_id == identity_id
            )
        ) == 1
        assert db.scalar(select(func.count(CredentialSecretVersion.id))) == 1


def test_ambiguous_active_bindings_fail_closed(
    target_id: int,
    configured_key: None,
) -> None:
    with SessionLocal() as db:
        identity = TestIdentity(
            target_id=target_id,
            name=f"ambiguous-{uuid4()}",
            role=None,
            auth_type="bearer",
            credentials={"access_token": "legacy-ambiguous-token"},
            is_active=True,
        )
        db.add(identity)
        db.flush()
        db.add_all(
            [
                CredentialBinding(
                    test_identity_id=identity.id,
                    auth_type="bearer",
                    source_type="stored_secret",
                    is_active=True,
                ),
                CredentialBinding(
                    test_identity_id=identity.id,
                    auth_type="bearer",
                    source_type="stored_secret",
                    is_active=True,
                ),
            ]
        )
        db.commit()
        identity_id = identity.id

    response = client.put(
        f"/api/test-identities/{identity_id}/token",
        json={"access_token": "must-not-be-stored"},
    )
    assert response.status_code == 409
    assert "must-not-be-stored" not in response.text
    with SessionLocal() as db:
        identity = db.get(TestIdentity, identity_id)
        assert identity is not None
        assert identity.credentials == {"access_token": "legacy-ambiguous-token"}
        assert db.scalar(select(func.count(CredentialSecretVersion.id))) == 0


def test_update_commit_failure_rolls_back_legacy_cleanup_and_new_graph(
    target_id: int,
    configured_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_token = "synthetic-commit-failure-legacy-token"
    with SessionLocal() as db:
        identity = TestIdentity(
            target_id=target_id,
            name=f"commit-failure-{uuid4()}",
            role=None,
            auth_type="bearer",
            credentials={"access_token": legacy_token},
            is_active=True,
        )
        db.add(identity)
        db.commit()
        identity_id = identity.id

    with SessionLocal() as db:
        def fail_commit() -> None:
            raise RuntimeError("synthetic commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="synthetic commit failure"):
            update_bearer_token(
                identity_id=identity_id,
                payload=BearerTokenUpdate(
                    access_token=SecretStr("synthetic-never-committed-token")
                ),
                db=db,
            )
        assert db.in_transaction() is False

    with SessionLocal() as db:
        identity = db.get(TestIdentity, identity_id)
        assert identity is not None
        assert identity.credentials == {"access_token": legacy_token}
        assert db.scalar(
            select(func.count(CredentialBinding.id)).where(
                CredentialBinding.test_identity_id == identity_id
            )
        ) == 0
        assert db.scalar(select(func.count(CredentialSecretVersion.id))) == 0
