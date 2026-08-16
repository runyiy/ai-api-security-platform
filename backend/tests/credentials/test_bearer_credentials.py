import base64
from collections.abc import Iterator
import threading
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select

from app.core.config import Settings, settings
from app.credentials.bearer import (
    BearerCredentialError,
    BearerCredentialService,
)
from app.credentials.stored_secret import StoredSecretCipher, StoredSecretProvider
from app.db.models.credential_binding import CredentialBinding
from app.db.models.credential_secret_version import CredentialSecretVersion
from app.db.models.target import Target
from app.db.models.test_identity import TestIdentity
from app.db.session import SessionLocal


TOKEN = "synthetic-current-bearer-token"
OLDER_TOKEN = "synthetic-older-bearer-token"
KEY = base64.urlsafe_b64encode(b"m" * 32).decode("ascii")
WRONG_KEY = base64.urlsafe_b64encode(b"w" * 32).decode("ascii")


def provider(encoded_key: str = KEY) -> StoredSecretProvider:
    settings = Settings(
        database_url="postgresql://unused.test/database",
        credential_encryption_key=SecretStr(encoded_key),
        credential_encryption_key_version="test-key-v1",
    )
    return StoredSecretProvider(StoredSecretCipher.from_settings(settings))


@pytest.fixture
def bearer_identity() -> Iterator[tuple[int, int]]:
    with SessionLocal() as db:
        target = Target(
            name=f"bearer-service-{uuid4()}",
            base_url="https://example.test",
            environment="test",
            is_enabled=True,
        )
        db.add(target)
        db.flush()
        identity = TestIdentity(
            target_id=target.id,
            name=f"bearer-{uuid4()}",
            role="user",
            auth_type="bearer",
            credentials={"access_token": "legacy-plaintext-token"},
            is_active=True,
        )
        db.add(identity)
        db.commit()
        target_id = target.id
        identity_id = identity.id

    try:
        yield target_id, identity_id
    finally:
        with SessionLocal() as db:
            binding_ids = select(CredentialBinding.id).where(
                CredentialBinding.test_identity_id == identity_id
            )
            db.execute(
                delete(CredentialSecretVersion).where(
                    CredentialSecretVersion.credential_binding_id.in_(binding_ids)
                )
            )
            db.execute(
                delete(CredentialBinding).where(
                    CredentialBinding.test_identity_id == identity_id
                )
            )
            db.execute(delete(Target).where(Target.id == target_id))
            db.commit()


def test_update_creates_binding_and_resolves_highest_version_id(
    bearer_identity: tuple[int, int],
) -> None:
    _, identity_id = bearer_identity

    with SessionLocal() as db:
        service = BearerCredentialService(db=db, provider=provider())
        service.update(identity_id=identity_id, token=SecretStr(OLDER_TOKEN))
        db.commit()

    with SessionLocal() as db:
        service = BearerCredentialService(db=db, provider=provider())
        service.update(identity_id=identity_id, token=SecretStr(TOKEN))
        db.commit()

    with SessionLocal() as db:
        identity = db.get(TestIdentity, identity_id)
        assert identity is not None
        versions = list(
            db.scalars(
                select(CredentialSecretVersion)
                .join(CredentialBinding)
                .where(CredentialBinding.test_identity_id == identity_id)
                .order_by(CredentialSecretVersion.id)
            ).all()
        )
        assert len(versions) == 2
        assert versions[0].id < versions[1].id
        assert identity.credentials is None
        assert (
            BearerCredentialService(db=db, provider=provider())
            .resolve(identity)
            .get_secret_value()
            == TOKEN
        )


def test_resolve_never_falls_back_to_legacy_plaintext(
    bearer_identity: tuple[int, int],
) -> None:
    _, identity_id = bearer_identity

    with SessionLocal() as db:
        identity = db.get(TestIdentity, identity_id)
        assert identity is not None
        with pytest.raises(BearerCredentialError) as raised:
            BearerCredentialService(db=db, provider=provider()).resolve(identity)

        message = str(raised.value)
        assert message == "Bearer credential is unavailable."
        assert "legacy-plaintext-token" not in message


def test_resolve_requires_exactly_one_active_matching_binding(
    bearer_identity: tuple[int, int],
) -> None:
    _, identity_id = bearer_identity

    with SessionLocal() as db:
        identity = db.get(TestIdentity, identity_id)
        assert identity is not None
        db.add_all(
            [
                CredentialBinding(
                    test_identity_id=identity_id,
                    auth_type="bearer",
                    source_type="stored_secret",
                    is_active=True,
                ),
                CredentialBinding(
                    test_identity_id=identity_id,
                    auth_type="bearer",
                    source_type="stored_secret",
                    is_active=True,
                ),
            ]
        )
        db.commit()

        with pytest.raises(BearerCredentialError, match="unavailable"):
            BearerCredentialService(db=db, provider=provider()).resolve(identity)


def test_inactive_or_nonmatching_binding_is_not_selected(
    bearer_identity: tuple[int, int],
) -> None:
    _, identity_id = bearer_identity

    with SessionLocal() as db:
        identity = db.get(TestIdentity, identity_id)
        assert identity is not None
        db.add_all(
            [
                CredentialBinding(
                    test_identity_id=identity_id,
                    auth_type="bearer",
                    source_type="stored_secret",
                    is_active=False,
                ),
                CredentialBinding(
                    test_identity_id=identity_id,
                    auth_type="bearer",
                    source_type="external_reference",
                    is_active=True,
                ),
            ]
        )
        db.commit()

        with pytest.raises(BearerCredentialError, match="unavailable"):
            BearerCredentialService(db=db, provider=provider()).resolve(identity)


def test_missing_secret_version_fails_closed(
    bearer_identity: tuple[int, int],
) -> None:
    _, identity_id = bearer_identity

    with SessionLocal() as db:
        identity = db.get(TestIdentity, identity_id)
        assert identity is not None
        db.add(
            CredentialBinding(
                test_identity_id=identity_id,
                auth_type="bearer",
                source_type="stored_secret",
                is_active=True,
            )
        )
        db.commit()

        with pytest.raises(BearerCredentialError, match="unavailable"):
            BearerCredentialService(db=db, provider=provider()).resolve(identity)


@pytest.mark.parametrize("failure", ["tampered", "wrong-key"])
def test_crypto_failure_is_sanitized(
    bearer_identity: tuple[int, int],
    failure: str,
) -> None:
    _, identity_id = bearer_identity

    with SessionLocal() as db:
        BearerCredentialService(db=db, provider=provider()).update(
            identity_id=identity_id,
            token=SecretStr(TOKEN),
        )
        db.commit()

    with SessionLocal() as db:
        identity = db.get(TestIdentity, identity_id)
        assert identity is not None
        selected_provider = provider()
        if failure == "tampered":
            version = db.scalar(
                select(CredentialSecretVersion)
                .join(CredentialBinding)
                .where(CredentialBinding.test_identity_id == identity_id)
            )
            assert version is not None
            version.encrypted_envelope += "A"
            db.commit()
        else:
            selected_provider = provider(WRONG_KEY)

        with pytest.raises(BearerCredentialError) as raised:
            BearerCredentialService(
                db=db,
                provider=selected_provider,
            ).resolve(identity)

        assert str(raised.value) == "Bearer credential is unavailable."
        assert TOKEN not in str(raised.value)
        assert KEY not in str(raised.value)
        assert WRONG_KEY not in str(raised.value)


def test_missing_runtime_key_fails_resolution_with_sanitized_error(
    bearer_identity: tuple[int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, identity_id = bearer_identity

    with SessionLocal() as db:
        BearerCredentialService(db=db, provider=provider()).update(
            identity_id=identity_id,
            token=SecretStr(TOKEN),
        )
        db.commit()

    monkeypatch.setattr(settings, "credential_encryption_key", None)
    with SessionLocal() as db:
        identity = db.get(TestIdentity, identity_id)
        assert identity is not None
        with pytest.raises(BearerCredentialError) as raised:
            BearerCredentialService(db=db).resolve(identity)

        assert str(raised.value) == "Bearer credential is unavailable."
        assert TOKEN not in str(raised.value)
        assert KEY not in str(raised.value)


def test_ambiguous_update_rolls_back_without_clearing_legacy_plaintext(
    bearer_identity: tuple[int, int],
) -> None:
    _, identity_id = bearer_identity

    with SessionLocal() as db:
        db.add_all(
            [
                CredentialBinding(
                    test_identity_id=identity_id,
                    auth_type="bearer",
                    source_type="stored_secret",
                    is_active=True,
                ),
                CredentialBinding(
                    test_identity_id=identity_id,
                    auth_type="bearer",
                    source_type="stored_secret",
                    is_active=True,
                ),
            ]
        )
        db.commit()

    with SessionLocal() as db:
        with pytest.raises(BearerCredentialError):
            BearerCredentialService(db=db, provider=provider()).update(
                identity_id=identity_id,
                token=SecretStr(TOKEN),
            )
        db.rollback()

    with SessionLocal() as db:
        identity = db.get(TestIdentity, identity_id)
        assert identity is not None
        assert identity.credentials == {"access_token": "legacy-plaintext-token"}
        assert db.scalars(
            select(CredentialSecretVersion)
            .join(CredentialBinding)
            .where(CredentialBinding.test_identity_id == identity_id)
        ).all() == []


def test_concurrent_zero_binding_updates_serialize_on_identity_lock(
    bearer_identity: tuple[int, int],
) -> None:
    _, identity_id = bearer_identity
    first_holds_lock = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_finished = threading.Event()
    errors: list[Exception] = []

    class BlockingProvider:
        def __init__(self, *, block: bool) -> None:
            self._delegate = provider()
            self._block = block

        def store_secret(self, db, binding, plaintext):
            if self._block:
                first_holds_lock.set()
                if not release_first.wait(timeout=10):
                    raise RuntimeError("test release timed out")
            return self._delegate.store_secret(db, binding, plaintext)

        def load_secret(self, version):
            return self._delegate.load_secret(version)

    def update(token: str, *, block: bool) -> None:
        try:
            with SessionLocal() as db:
                if not block:
                    second_started.set()
                BearerCredentialService(
                    db=db,
                    provider=BlockingProvider(block=block),
                ).update(identity_id=identity_id, token=SecretStr(token))
                db.commit()
        except Exception as exc:
            errors.append(exc)
        finally:
            if not block:
                second_finished.set()

    first = threading.Thread(
        target=update,
        kwargs={"token": OLDER_TOKEN, "block": True},
    )
    second = threading.Thread(
        target=update,
        kwargs={"token": TOKEN, "block": False},
    )
    first.start()
    assert first_holds_lock.wait(timeout=10)
    second.start()
    assert second_started.wait(timeout=10)
    second_was_blocked = not second_finished.wait(timeout=0.2)
    release_first.set()

    first.join(timeout=10)
    second.join(timeout=10)
    assert not first.is_alive()
    assert not second.is_alive()
    assert second_was_blocked is True
    assert errors == []

    with SessionLocal() as db:
        identity = db.get(TestIdentity, identity_id)
        assert identity is not None
        bindings = list(
            db.scalars(
                select(CredentialBinding).where(
                    CredentialBinding.test_identity_id == identity_id,
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
        assert (
            BearerCredentialService(db=db, provider=provider())
            .resolve(identity)
            .get_secret_value()
            == TOKEN
        )
