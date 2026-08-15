import base64
import binascii
from dataclasses import dataclass
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr


CURRENT_FORMAT_VERSION = 1
NONCE_BYTES = 12


class SecretCipherError(ValueError):
    pass


class SecretConfigurationError(SecretCipherError):
    pass


class SecretDecryptionError(SecretCipherError):
    pass


@dataclass(frozen=True)
class EncryptedSecretEnvelope:
    encrypted_payload: str
    format_version: int
    key_version: str


class SecretCipher:
    def __init__(
        self,
        *,
        encryption_key: SecretStr | None,
        key_version: str = "1",
    ) -> None:
        if encryption_key is None:
            raise SecretConfigurationError(
                "Credential secret encryption key is not configured."
            )

        try:
            encoded_key_version = key_version.encode("ascii")
        except UnicodeEncodeError as exc:
            raise SecretConfigurationError(
                "Credential secret key version is invalid."
            ) from exc

        if (
            not key_version.strip()
            or key_version != key_version.strip()
            or len(encoded_key_version) > 50
        ):
            raise SecretConfigurationError(
                "Credential secret key version is invalid."
            )

        try:
            key = base64.b64decode(
                encryption_key.get_secret_value(),
                altchars=b"-_",
                validate=True,
            )
        except (ValueError, binascii.Error) as exc:
            raise SecretConfigurationError(
                "Credential secret encryption key is invalid."
            ) from exc

        if len(key) != 32:
            raise SecretConfigurationError(
                "Credential secret encryption key is invalid."
            )

        self._cipher = AESGCM(key)
        self._key_version = key_version

    def encrypt(self, plaintext: str) -> EncryptedSecretEnvelope:
        nonce = os.urandom(NONCE_BYTES)
        encrypted = self._cipher.encrypt(
            nonce,
            plaintext.encode("utf-8"),
            self._associated_data(
                CURRENT_FORMAT_VERSION,
                self._key_version,
            ),
        )
        payload = base64.urlsafe_b64encode(
            nonce + encrypted
        ).decode("ascii")

        return EncryptedSecretEnvelope(
            encrypted_payload=payload,
            format_version=CURRENT_FORMAT_VERSION,
            key_version=self._key_version,
        )

    def decrypt(self, envelope: EncryptedSecretEnvelope) -> str:
        try:
            if envelope.format_version != CURRENT_FORMAT_VERSION:
                raise ValueError

            if envelope.key_version != self._key_version:
                raise ValueError

            combined = base64.b64decode(
                envelope.encrypted_payload,
                altchars=b"-_",
                validate=True,
            )

            if len(combined) <= NONCE_BYTES:
                raise ValueError

            nonce = combined[:NONCE_BYTES]
            encrypted = combined[NONCE_BYTES:]
            plaintext = self._cipher.decrypt(
                nonce,
                encrypted,
                self._associated_data(
                    envelope.format_version,
                    envelope.key_version,
                ),
            )
            return plaintext.decode("utf-8")
        except (
            InvalidTag,
            UnicodeDecodeError,
            ValueError,
            binascii.Error,
        ) as exc:
            raise SecretDecryptionError(
                "Credential secret decryption failed."
            ) from exc

    @staticmethod
    def _associated_data(
        format_version: int,
        key_version: str,
    ) -> bytes:
        return (
            f"credential-secret:v{format_version}:"
            f"key:{key_version}"
        ).encode("ascii")
