import base64
from dataclasses import replace

import pytest
from pydantic import SecretStr

from app.auth.secret_cipher import (
    EncryptedSecretEnvelope,
    SecretCipher,
    SecretConfigurationError,
    SecretDecryptionError,
)
from app.core.config import Settings


PLAINTEXT = "local-fixture-token-value"
TEST_KEY = base64.urlsafe_b64encode(b"a" * 32).decode("ascii")
OTHER_TEST_KEY = base64.urlsafe_b64encode(b"b" * 32).decode("ascii")


def build_cipher(
    key: str = TEST_KEY,
    *,
    key_version: str = "test-v1",
) -> SecretCipher:
    return SecretCipher(
        encryption_key=SecretStr(key),
        key_version=key_version,
    )


def test_secret_round_trip() -> None:
    cipher = build_cipher()
    envelope = cipher.encrypt(PLAINTEXT)

    assert cipher.decrypt(envelope) == PLAINTEXT
    assert envelope.format_version == 1
    assert envelope.key_version == "test-v1"


def test_encryption_is_randomized() -> None:
    cipher = build_cipher()

    first = cipher.encrypt(PLAINTEXT)
    second = cipher.encrypt(PLAINTEXT)

    assert first.encrypted_payload != second.encrypted_payload


def test_tampered_ciphertext_fails_closed() -> None:
    cipher = build_cipher()
    envelope = cipher.encrypt(PLAINTEXT)
    payload = bytearray(
        base64.urlsafe_b64decode(envelope.encrypted_payload)
    )
    payload[-1] ^= 1
    tampered = replace(
        envelope,
        encrypted_payload=base64.urlsafe_b64encode(payload).decode("ascii"),
    )

    with pytest.raises(SecretDecryptionError) as exc_info:
        cipher.decrypt(tampered)

    assert PLAINTEXT not in str(exc_info.value)
    assert TEST_KEY not in str(exc_info.value)


def test_wrong_key_fails_closed_without_key_disclosure() -> None:
    envelope = build_cipher().encrypt(PLAINTEXT)

    with pytest.raises(SecretDecryptionError) as exc_info:
        build_cipher(OTHER_TEST_KEY).decrypt(envelope)

    message = str(exc_info.value)
    assert PLAINTEXT not in message
    assert TEST_KEY not in message
    assert OTHER_TEST_KEY not in message


@pytest.mark.parametrize(
    "invalid_key",
    ["not-base64!", base64.urlsafe_b64encode(b"short").decode("ascii")],
)
def test_invalid_key_material_fails_closed(invalid_key: str) -> None:
    with pytest.raises(SecretConfigurationError) as exc_info:
        build_cipher(invalid_key)

    assert invalid_key not in str(exc_info.value)


def test_missing_key_configuration_fails_when_cipher_is_used() -> None:
    configured = Settings(database_url="postgresql://local/test")

    assert configured.credential_secret_encryption_key is None

    with pytest.raises(SecretConfigurationError):
        SecretCipher(
            encryption_key=configured.credential_secret_encryption_key,
        )


def test_config_repr_does_not_reveal_raw_key() -> None:
    configured = Settings(
        database_url="postgresql://local/test",
        credential_secret_encryption_key=TEST_KEY,
    )

    assert TEST_KEY not in repr(configured)


def test_envelope_contains_no_plaintext() -> None:
    envelope: EncryptedSecretEnvelope = build_cipher().encrypt(PLAINTEXT)

    assert PLAINTEXT not in envelope.encrypted_payload
    assert PLAINTEXT not in repr(envelope)
