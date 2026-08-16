from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.credentials.stored_secret import (
    StoredSecretCipher,
    StoredSecretError,
    StoredSecretProvider,
)
from app.db.models.credential_binding import CredentialBinding
from app.db.models.credential_secret_version import CredentialSecretVersion
from app.db.models.test_identity import TestIdentity


class BearerCredentialError(RuntimeError):
    """Sanitized failure at the bearer credential boundary."""


class BearerIdentityNotFoundError(BearerCredentialError):
    pass


class BearerIdentityTypeError(BearerCredentialError):
    pass


def build_stored_secret_provider() -> StoredSecretProvider:
    return StoredSecretProvider(StoredSecretCipher.from_settings(settings))


class BearerCredentialService:
    def __init__(
        self,
        *,
        db: Session,
        provider: StoredSecretProvider | None = None,
    ) -> None:
        self.db = db
        self._provider = provider

    def provision(
        self,
        *,
        identity: TestIdentity,
        token: SecretStr,
    ) -> CredentialBinding:
        if identity.id is None or identity.auth_type != "bearer":
            raise BearerIdentityTypeError(
                "Bearer credential operation is not valid for this identity."
            )

        binding = CredentialBinding(
            test_identity_id=identity.id,
            auth_type="bearer",
            source_type="stored_secret",
            is_active=True,
        )
        self.db.add(binding)
        self.db.flush()

        try:
            self._get_provider().store_secret(self.db, binding, token)
        except StoredSecretError:
            raise BearerCredentialError(
                "Bearer credential operation failed."
            ) from None

        return binding

    def update(
        self,
        *,
        identity_id: int,
        token: SecretStr,
    ) -> TestIdentity:
        identity = self.db.scalar(
            select(TestIdentity)
            .where(TestIdentity.id == identity_id)
            .with_for_update()
        )
        if identity is None:
            raise BearerIdentityNotFoundError(
                "Test identity not found."
            )
        if identity.auth_type != "bearer":
            raise BearerIdentityTypeError(
                "Only bearer identities can receive a bearer token."
            )

        bindings = self._matching_bindings(
            identity_id=identity.id,
            for_update=True,
        )
        if len(bindings) > 1:
            raise BearerCredentialError(
                "Bearer credential operation failed."
            )

        if bindings:
            binding = bindings[0]
        else:
            binding = CredentialBinding(
                test_identity_id=identity.id,
                auth_type="bearer",
                source_type="stored_secret",
                is_active=True,
            )
            self.db.add(binding)
            self.db.flush()

        try:
            self._get_provider().store_secret(self.db, binding, token)
        except StoredSecretError:
            raise BearerCredentialError(
                "Bearer credential operation failed."
            ) from None

        identity.credentials = None
        return identity

    def resolve(self, identity: TestIdentity) -> SecretStr:
        if (
            identity.id is None
            or not identity.is_active
            or identity.auth_type != "bearer"
        ):
            raise BearerCredentialError(
                "Bearer credential is unavailable."
            )

        bindings = self._matching_bindings(identity_id=identity.id)
        if len(bindings) != 1:
            raise BearerCredentialError(
                "Bearer credential is unavailable."
            )

        version = self.db.scalar(
            select(CredentialSecretVersion)
            .where(
                CredentialSecretVersion.credential_binding_id
                == bindings[0].id
            )
            .order_by(CredentialSecretVersion.id.desc())
            .limit(1)
        )
        if version is None:
            raise BearerCredentialError(
                "Bearer credential is unavailable."
            )

        try:
            return self._get_provider().load_secret(version)
        except StoredSecretError:
            raise BearerCredentialError(
                "Bearer credential is unavailable."
            ) from None

    def _matching_bindings(
        self,
        *,
        identity_id: int,
        for_update: bool = False,
    ) -> list[CredentialBinding]:
        statement = (
            select(CredentialBinding)
            .where(
                CredentialBinding.test_identity_id == identity_id,
                CredentialBinding.auth_type == "bearer",
                CredentialBinding.source_type == "stored_secret",
                CredentialBinding.is_active.is_(True),
            )
            .order_by(CredentialBinding.id)
            .limit(2)
        )
        if for_update:
            statement = statement.with_for_update()
        return list(self.db.scalars(statement).all())

    def _get_provider(self) -> StoredSecretProvider:
        if self._provider is None:
            self._provider = build_stored_secret_provider()
        return self._provider
