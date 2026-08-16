import base64
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.credentials.stored_secret import (
    StoredSecretBindingError,
    StoredSecretCipher,
    StoredSecretDecryptionError,
    StoredSecretProvider,
)
from app.db.models import CredentialSecretVersion
from app.db.models.credential_binding import CredentialBinding
from app.db.models.target import Target
from app.db.models.test_identity import TestIdentity as StoredIdentity
from app.db.session import SessionLocal


PLAINTEXT = "synthetic-provider-secret"
ENCODED_KEY = base64.urlsafe_b64encode(b"c" * 32).decode("ascii")


def build_provider() -> StoredSecretProvider:
    settings = Settings(
        database_url="postgresql://unused.test/database",
        credential_encryption_key=SecretStr(ENCODED_KEY),
        credential_encryption_key_version="test-key-v1",
    )
    return StoredSecretProvider(StoredSecretCipher.from_settings(settings))


def persist_binding(source_type: str = "stored_secret") -> tuple[int, int, int]:
    with SessionLocal() as db:
        target = Target(
            name=f"stored-secret-target-{uuid4()}",
            base_url="https://example.test",
            environment="test",
            is_enabled=True,
        )
        db.add(target)
        db.flush()
        identity = StoredIdentity(
            target_id=target.id,
            name=f"stored-secret-identity-{uuid4()}",
            role="user",
            auth_type="bearer",
            credentials={"access_token": "transitional-test-token"},
            is_active=True,
        )
        binding = CredentialBinding(
            test_identity=identity,
            auth_type="bearer",
            source_type=source_type,
            is_active=True,
        )
        db.add(binding)
        db.commit()
        return target.id, identity.id, binding.id


def delete_binding_graph(target_id: int, binding_id: int) -> None:
    with SessionLocal() as db:
        db.execute(
            delete(CredentialSecretVersion).where(
                CredentialSecretVersion.credential_binding_id == binding_id
            )
        )
        db.execute(
            delete(CredentialBinding).where(CredentialBinding.id == binding_id)
        )
        db.execute(delete(Target).where(Target.id == target_id))
        db.commit()


def test_store_and_load_secret_round_trip_uses_encrypted_persistence() -> None:
    provider = build_provider()
    target_id, identity_id, binding_id = persist_binding()

    try:
        with SessionLocal() as db:
            binding = db.get(CredentialBinding, binding_id)
            assert binding is not None
            version = provider.store_secret(
                db,
                binding,
                SecretStr(PLAINTEXT),
            )
            db.commit()
            version_id = version.id

        with SessionLocal() as db:
            version = db.get(CredentialSecretVersion, version_id)
            binding = db.get(CredentialBinding, binding_id)
            identity = db.get(StoredIdentity, identity_id)

            assert version is not None
            assert binding is not None
            assert identity is not None
            assert version.credential_binding_id == binding_id
            assert version.credential_binding is binding
            assert version in binding.secret_versions
            assert version.envelope_version == 1
            assert version.key_version == "test-key-v1"
            assert version.created_at is not None
            assert PLAINTEXT not in version.encrypted_envelope
            assert identity.credentials == {
                "access_token": "transitional-test-token"
            }
            assert provider.load_secret(version).get_secret_value() == PLAINTEXT
    finally:
        delete_binding_graph(target_id, binding_id)


def test_storing_same_secret_creates_distinct_versions_and_envelopes() -> None:
    provider = build_provider()
    target_id, _, binding_id = persist_binding()

    try:
        with SessionLocal() as db:
            binding = db.get(CredentialBinding, binding_id)
            assert binding is not None
            first = provider.store_secret(db, binding, SecretStr(PLAINTEXT))
            second = provider.store_secret(db, binding, SecretStr(PLAINTEXT))
            db.commit()

            assert first.id != second.id
            assert first.encrypted_envelope != second.encrypted_envelope
    finally:
        delete_binding_graph(target_id, binding_id)


def test_non_stored_secret_binding_is_rejected_without_persistence() -> None:
    provider = build_provider()
    target_id, _, binding_id = persist_binding(source_type="external_reference")

    try:
        with SessionLocal() as db:
            binding = db.get(CredentialBinding, binding_id)
            assert binding is not None

            with pytest.raises(StoredSecretBindingError) as raised:
                provider.store_secret(db, binding, SecretStr(PLAINTEXT))

            assert str(raised.value) == (
                "Credential binding does not use stored_secret."
            )
            assert PLAINTEXT not in str(raised.value)
            assert db.scalars(
                select(CredentialSecretVersion).where(
                    CredentialSecretVersion.credential_binding_id == binding_id
                )
            ).all() == []
    finally:
        delete_binding_graph(target_id, binding_id)


def test_secret_version_maps_no_plaintext_field() -> None:
    mapped_columns = set(CredentialSecretVersion.__table__.columns.keys())

    assert mapped_columns == {
        "id",
        "credential_binding_id",
        "encrypted_envelope",
        "envelope_version",
        "key_version",
        "created_at",
    }
    assert "plaintext" not in mapped_columns
    assert "secret" not in mapped_columns
    assert "credentials" not in mapped_columns


def test_invalid_credential_binding_id_is_rejected_by_postgresql() -> None:
    with SessionLocal() as db:
        maximum_binding_id = db.scalar(
            select(CredentialBinding.id)
            .order_by(CredentialBinding.id.desc())
            .limit(1)
        )
        version = CredentialSecretVersion(
            credential_binding_id=(maximum_binding_id or 0) + 1_000_000,
            encrypted_envelope="v1.synthetic.ciphertext",
            envelope_version=1,
            key_version="test-key-v1",
        )
        db.add(version)

        with pytest.raises(IntegrityError):
            db.commit()

        db.rollback()


def test_referenced_binding_delete_is_rejected_without_orphaning_version() -> None:
    provider = build_provider()
    target_id, _, binding_id = persist_binding()
    version_id: int | None = None

    try:
        with SessionLocal() as db:
            binding = db.get(CredentialBinding, binding_id)
            assert binding is not None
            version = provider.store_secret(db, binding, SecretStr(PLAINTEXT))
            db.commit()
            version_id = version.id

        with SessionLocal() as db:
            binding = db.get(CredentialBinding, binding_id)
            assert binding is not None
            db.delete(binding)

            with pytest.raises(IntegrityError):
                db.commit()

            db.rollback()
            assert db.get(CredentialSecretVersion, version_id) is not None
    finally:
        delete_binding_graph(target_id, binding_id)


def test_stored_row_and_model_repr_do_not_contain_plaintext() -> None:
    provider = build_provider()
    target_id, _, binding_id = persist_binding()

    try:
        with SessionLocal() as db:
            binding = db.get(CredentialBinding, binding_id)
            assert binding is not None
            version = provider.store_secret(db, binding, SecretStr(PLAINTEXT))
            db.commit()

            assert PLAINTEXT not in repr(version)
            assert ENCODED_KEY not in repr(version)
            assert PLAINTEXT not in repr(provider)
            assert ENCODED_KEY not in repr(provider)
    finally:
        delete_binding_graph(target_id, binding_id)


def test_version_metadata_mismatch_fails_closed() -> None:
    provider = build_provider()
    target_id, _, binding_id = persist_binding()

    try:
        with SessionLocal() as db:
            binding = db.get(CredentialBinding, binding_id)
            assert binding is not None
            version = provider.store_secret(db, binding, SecretStr(PLAINTEXT))
            db.commit()
            version.envelope_version = 999

            with pytest.raises(StoredSecretDecryptionError) as raised:
                provider.load_secret(version)

            assert str(raised.value) == "Stored secret decryption failed."
            assert PLAINTEXT not in str(raised.value)
    finally:
        delete_binding_graph(target_id, binding_id)
