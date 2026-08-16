import base64

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.credentials.stored_secret import (
    StoredSecretCipher,
    StoredSecretConfigurationError,
    StoredSecretDecryptionError,
)


PLAINTEXT = "synthetic-bearer-secret"
RAW_KEY = b"a" * 32
OTHER_RAW_KEY = b"b" * 32
ENCODED_KEY = base64.urlsafe_b64encode(RAW_KEY).decode("ascii")
OTHER_ENCODED_KEY = base64.urlsafe_b64encode(OTHER_RAW_KEY).decode("ascii")


def build_settings(key: str | None = ENCODED_KEY) -> Settings:
    return Settings(
        database_url="postgresql://unused.test/database",
        credential_encryption_key=(SecretStr(key) if key is not None else None),
        credential_encryption_key_version="test-key-v1",
    )


def test_secret_round_trips_through_versioned_envelope() -> None:
    cipher = StoredSecretCipher.from_settings(build_settings())

    envelope = cipher.encrypt(
        SecretStr(PLAINTEXT),
        credential_binding_id=41,
    )

    assert envelope.startswith("v1.")
    assert PLAINTEXT not in envelope
    assert cipher.decrypt(
        envelope,
        credential_binding_id=41,
    ).get_secret_value() == PLAINTEXT


def test_identical_plaintext_produces_different_envelopes() -> None:
    cipher = StoredSecretCipher.from_settings(build_settings())

    first = cipher.encrypt(SecretStr(PLAINTEXT), credential_binding_id=41)
    second = cipher.encrypt(SecretStr(PLAINTEXT), credential_binding_id=41)

    assert first != second


def test_tampered_envelope_fails_closed_without_secret_leak() -> None:
    cipher = StoredSecretCipher.from_settings(build_settings())
    envelope = cipher.encrypt(SecretStr(PLAINTEXT), credential_binding_id=41)
    replacement = "A" if envelope[-1] != "A" else "B"
    tampered = f"{envelope[:-1]}{replacement}"

    with pytest.raises(StoredSecretDecryptionError) as raised:
        cipher.decrypt(tampered, credential_binding_id=41)

    message = str(raised.value)
    assert message == "Stored secret decryption failed."
    assert PLAINTEXT not in message
    assert ENCODED_KEY not in message
    assert RAW_KEY.decode("ascii") not in message
    assert raised.value.__cause__ is None


def test_wrong_key_fails_closed_without_key_leak() -> None:
    cipher = StoredSecretCipher.from_settings(build_settings())
    wrong_cipher = StoredSecretCipher.from_settings(
        build_settings(OTHER_ENCODED_KEY)
    )
    envelope = cipher.encrypt(SecretStr(PLAINTEXT), credential_binding_id=41)

    with pytest.raises(StoredSecretDecryptionError) as raised:
        wrong_cipher.decrypt(envelope, credential_binding_id=41)

    message = str(raised.value)
    assert message == "Stored secret decryption failed."
    assert PLAINTEXT not in message
    assert ENCODED_KEY not in message
    assert OTHER_ENCODED_KEY not in message
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    ("configured_key", "expected_message"),
    [
        (None, "Credential encryption key is not configured."),
        ("not-base64", "Credential encryption key is invalid."),
        (
            base64.urlsafe_b64encode(b"too-short").decode("ascii"),
            "Credential encryption key is invalid.",
        ),
    ],
)
def test_missing_or_invalid_key_fails_closed_when_cipher_is_created(
    configured_key: str | None,
    expected_message: str,
) -> None:
    settings = build_settings(configured_key)

    with pytest.raises(StoredSecretConfigurationError) as raised:
        StoredSecretCipher.from_settings(settings)

    message = str(raised.value)
    assert message == expected_message
    assert "not-base64" not in message
    assert "too-short" not in message
    assert raised.value.__cause__ is None


def test_settings_and_cipher_repr_do_not_expose_raw_key() -> None:
    settings = build_settings()
    cipher = StoredSecretCipher.from_settings(settings)

    assert ENCODED_KEY not in repr(settings)
    assert RAW_KEY.decode("ascii") not in repr(settings)
    assert ENCODED_KEY not in repr(cipher)
    assert RAW_KEY.decode("ascii") not in repr(cipher)


def test_envelope_is_bound_to_credential_binding() -> None:
    cipher = StoredSecretCipher.from_settings(build_settings())
    envelope = cipher.encrypt(SecretStr(PLAINTEXT), credential_binding_id=41)

    with pytest.raises(StoredSecretDecryptionError):
        cipher.decrypt(envelope, credential_binding_id=42)
