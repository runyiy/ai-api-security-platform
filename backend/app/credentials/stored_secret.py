import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.credential_binding import CredentialBinding
from app.db.models.credential_secret_version import CredentialSecretVersion


ENVELOPE_VERSION = 1
NONCE_SIZE_BYTES = 12
KEY_SIZE_BYTES = 32


class StoredSecretError(RuntimeError):
    """Base error that never includes secret material."""


class StoredSecretConfigurationError(StoredSecretError):
    """Raised when stored-secret configuration is unavailable or invalid."""


class StoredSecretEncryptionError(StoredSecretError):
    """Raised when a secret cannot be encrypted safely."""


class StoredSecretDecryptionError(StoredSecretError):
    """Raised when an encrypted secret cannot be authenticated or decoded."""


class StoredSecretBindingError(StoredSecretError):
    """Raised when a binding cannot use PostgreSQL stored secrets."""


def _decode_base64(value: str) -> bytes:
    return base64.b64decode(
        value.encode("ascii"),
        altchars=b"-_",
        validate=True,
    )


def _encode_base64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


class StoredSecretCipher:
    def __init__(self, key: bytes, key_version: str) -> None:
        if len(key) != KEY_SIZE_BYTES or not key_version.strip():
            raise StoredSecretConfigurationError(
                "Credential encryption key is invalid."
            )

        self._aead = AESGCM(key)
        self.key_version = key_version

    @classmethod
    def from_settings(cls, settings: Settings) -> "StoredSecretCipher":
        configured_key = settings.credential_encryption_key
        if configured_key is None:
            raise StoredSecretConfigurationError(
                "Credential encryption key is not configured."
            )

        try:
            key = _decode_base64(configured_key.get_secret_value())
        except (ValueError, UnicodeEncodeError, binascii.Error):
            raise StoredSecretConfigurationError(
                "Credential encryption key is invalid."
            ) from None

        return cls(key, settings.credential_encryption_key_version)

    def encrypt(
        self,
        plaintext: SecretStr,
        *,
        credential_binding_id: int,
    ) -> str:
        nonce = os.urandom(NONCE_SIZE_BYTES)
        associated_data = self._associated_data(credential_binding_id)

        try:
            ciphertext = self._aead.encrypt(
                nonce,
                plaintext.get_secret_value().encode("utf-8"),
                associated_data,
            )
        except Exception:
            raise StoredSecretEncryptionError(
                "Stored secret encryption failed."
            ) from None

        return ".".join(
            (
                f"v{ENVELOPE_VERSION}",
                _encode_base64(nonce),
                _encode_base64(ciphertext),
            )
        )

    def decrypt(
        self,
        envelope: str,
        *,
        credential_binding_id: int,
    ) -> SecretStr:
        try:
            version, encoded_nonce, encoded_ciphertext = envelope.split(".")
            if version != f"v{ENVELOPE_VERSION}":
                raise ValueError
            nonce = _decode_base64(encoded_nonce)
            if len(nonce) != NONCE_SIZE_BYTES:
                raise ValueError
            ciphertext = _decode_base64(encoded_ciphertext)
            plaintext = self._aead.decrypt(
                nonce,
                ciphertext,
                self._associated_data(credential_binding_id),
            ).decode("utf-8")
        except (
            InvalidTag,
            ValueError,
            UnicodeDecodeError,
            UnicodeEncodeError,
            binascii.Error,
        ):
            raise StoredSecretDecryptionError(
                "Stored secret decryption failed."
            ) from None

        return SecretStr(plaintext)

    def _associated_data(self, credential_binding_id: int) -> bytes:
        if credential_binding_id <= 0:
            raise StoredSecretConfigurationError(
                "Credential binding must be persisted before encryption."
            )
        return (
            f"credential-secret:v{ENVELOPE_VERSION}:"
            f"binding:{credential_binding_id}:key:{self.key_version}"
        ).encode("utf-8")


class StoredSecretProvider:
    def __init__(self, cipher: StoredSecretCipher) -> None:
        self._cipher = cipher

    def store_secret(
        self,
        db: Session,
        binding: CredentialBinding,
        plaintext: SecretStr,
    ) -> CredentialSecretVersion:
        self._validate_binding(binding)
        assert binding.id is not None

        version = CredentialSecretVersion(
            credential_binding=binding,
            encrypted_envelope=self._cipher.encrypt(
                plaintext,
                credential_binding_id=binding.id,
            ),
            envelope_version=ENVELOPE_VERSION,
            key_version=self._cipher.key_version,
        )
        db.add(version)
        db.flush()
        return version

    def load_secret(self, version: CredentialSecretVersion) -> SecretStr:
        self._validate_binding(version.credential_binding)
        if (
            version.envelope_version != ENVELOPE_VERSION
            or version.key_version != self._cipher.key_version
        ):
            raise StoredSecretDecryptionError(
                "Stored secret decryption failed."
            )

        return self._cipher.decrypt(
            version.encrypted_envelope,
            credential_binding_id=version.credential_binding_id,
        )

    @staticmethod
    def _validate_binding(binding: CredentialBinding) -> None:
        if binding.source_type != "stored_secret":
            raise StoredSecretBindingError(
                "Credential binding does not use stored_secret."
            )
        if binding.id is None:
            raise StoredSecretBindingError(
                "Credential binding must be persisted before storing secrets."
            )
