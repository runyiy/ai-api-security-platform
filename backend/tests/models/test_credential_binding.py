from uuid import uuid4

import pytest
from sqlalchemy import delete, inspect, select
from sqlalchemy.exc import IntegrityError

from app.auth.context import AuthenticationContextError, build_authentication_context
from app.db.base import Base
from app.db.models import CredentialBinding
from app.db.models.target import Target
from app.db.models.test_identity import TestIdentity as StoredIdentity
from app.db.session import SessionLocal, engine


def build_target() -> Target:
    return Target(
        name=f"credential-binding-target-{uuid4()}",
        base_url="https://example.test",
        environment="test",
        is_enabled=True,
    )


def build_identity(target_id: int) -> StoredIdentity:
    return StoredIdentity(
        target_id=target_id,
        name=f"credential-binding-identity-{uuid4()}",
        role="user",
        auth_type="bearer",
        credentials={"access_token": "transitional-test-token"},
        is_active=True,
    )


def test_credential_binding_is_registered_in_metadata() -> None:
    table = Base.metadata.tables["credential_bindings"]

    assert table is CredentialBinding.__table__


def test_credential_binding_metadata_round_trips_through_identity() -> None:
    target_id: int | None = None

    try:
        with SessionLocal() as db:
            target = build_target()
            db.add(target)
            db.flush()
            identity = build_identity(target.id)
            identity.credential_bindings.extend(
                [
                    CredentialBinding(
                        auth_type="bearer",
                        source_type="stored_secret",
                        is_active=True,
                    ),
                    CredentialBinding(
                        auth_type="bearer",
                        source_type="stored_secret",
                        is_active=False,
                    ),
                ]
            )
            db.add(identity)
            db.commit()
            target_id = target.id
            identity_id = identity.id

        with SessionLocal() as db:
            loaded_identity = db.get(StoredIdentity, identity_id)

            assert loaded_identity is not None
            assert len(loaded_identity.credential_bindings) == 2
            assert {
                (
                    binding.auth_type,
                    binding.source_type,
                    binding.is_active,
                )
                for binding in loaded_identity.credential_bindings
            } == {
                ("bearer", "stored_secret", True),
                ("bearer", "stored_secret", False),
            }
            assert all(
                binding.test_identity is loaded_identity
                for binding in loaded_identity.credential_bindings
            )
            assert all(
                binding.created_at is not None
                and binding.updated_at is not None
                for binding in loaded_identity.credential_bindings
            )
    finally:
        if target_id is not None:
            with SessionLocal() as db:
                db.execute(
                    delete(CredentialBinding).where(
                        CredentialBinding.test_identity_id == identity_id
                    )
                )
                db.execute(delete(Target).where(Target.id == target_id))
                db.commit()


def test_credential_binding_metadata_update_preserves_identity_authentication() -> None:
    target_id: int | None = None
    binding_id: int | None = None

    try:
        with SessionLocal() as db:
            target = build_target()
            db.add(target)
            db.flush()
            identity = build_identity(target.id)
            binding = CredentialBinding(
                test_identity=identity,
                auth_type="bearer",
                source_type="stored_secret",
                is_active=True,
            )
            db.add(binding)
            db.commit()
            target_id = target.id
            identity_id = identity.id
            binding_id = binding.id

        with SessionLocal() as db:
            binding = db.get(CredentialBinding, binding_id)
            assert binding is not None
            binding.auth_type = "bearer_v2"
            binding.source_type = "stored_secret_v2"
            binding.is_active = False
            db.commit()

        with SessionLocal() as db:
            loaded_binding = db.get(CredentialBinding, binding_id)
            loaded_identity = db.get(StoredIdentity, identity_id)

            assert loaded_binding is not None
            assert loaded_identity is not None
            assert loaded_binding.auth_type == "bearer_v2"
            assert loaded_binding.source_type == "stored_secret_v2"
            assert loaded_binding.is_active is False
            assert loaded_binding.test_identity_id == identity_id
            assert loaded_binding.test_identity is loaded_identity
            assert loaded_binding in loaded_identity.credential_bindings
            assert loaded_identity.credentials == {
                "access_token": "transitional-test-token"
            }
            with pytest.raises(AuthenticationContextError):
                build_authentication_context(loaded_identity)
    finally:
        if target_id is not None:
            with SessionLocal() as db:
                db.execute(
                    delete(CredentialBinding).where(
                        CredentialBinding.id == binding_id
                    )
                )
                db.execute(delete(Target).where(Target.id == target_id))
                db.commit()


def test_credential_binding_maps_no_secret_material() -> None:
    mapped_columns = set(CredentialBinding.__table__.columns.keys())

    assert mapped_columns == {
        "id",
        "test_identity_id",
        "auth_type",
        "source_type",
        "is_active",
        "created_at",
        "updated_at",
    }
    assert "credentials" not in mapped_columns
    assert "plaintext" not in mapped_columns
    assert "ciphertext" not in mapped_columns


def test_credential_binding_migration_created_expected_schema() -> None:
    inspector = inspect(engine)
    columns = {
        column["name"]: column
        for column in inspector.get_columns("credential_bindings")
    }
    foreign_keys = inspector.get_foreign_keys("credential_bindings")

    assert set(columns) == {
        "id",
        "test_identity_id",
        "auth_type",
        "source_type",
        "is_active",
        "created_at",
        "updated_at",
    }
    assert columns["test_identity_id"]["nullable"] is False
    assert foreign_keys == [
        {
            "name": "fk_credential_bindings_test_identity_id_test_identities",
            "constrained_columns": ["test_identity_id"],
            "referred_schema": None,
            "referred_table": "test_identities",
            "referred_columns": ["id"],
            "options": {"ondelete": "RESTRICT"},
            "comment": None,
        }
    ]


def test_referenced_identity_delete_is_rejected_without_orphaning_binding() -> None:
    target_id: int | None = None
    binding_id: int | None = None

    try:
        with SessionLocal() as db:
            target = build_target()
            db.add(target)
            db.flush()
            identity = build_identity(target.id)
            binding = CredentialBinding(
                test_identity=identity,
                auth_type="bearer",
                source_type="stored_secret",
                is_active=True,
            )
            db.add(binding)
            db.commit()
            target_id = target.id
            identity_id = identity.id
            binding_id = binding.id

        with SessionLocal() as db:
            identity = db.get(StoredIdentity, identity_id)
            assert identity is not None
            db.delete(identity)

            with pytest.raises(IntegrityError):
                db.commit()

            db.rollback()
            assert db.get(CredentialBinding, binding_id) is not None
    finally:
        if target_id is not None:
            with SessionLocal() as db:
                db.execute(
                    delete(CredentialBinding).where(
                        CredentialBinding.id == binding_id
                    )
                )
                db.execute(delete(Target).where(Target.id == target_id))
                db.commit()


def test_invalid_test_identity_id_is_rejected_by_postgresql() -> None:
    with SessionLocal() as db:
        maximum_identity_id = db.scalar(select(StoredIdentity.id).order_by(
            StoredIdentity.id.desc()
        ).limit(1))
        binding = CredentialBinding(
            test_identity_id=(maximum_identity_id or 0) + 1_000_000,
            auth_type="bearer",
            source_type="stored_secret",
            is_active=True,
        )
        db.add(binding)

        with pytest.raises(IntegrityError):
            db.commit()

        db.rollback()
