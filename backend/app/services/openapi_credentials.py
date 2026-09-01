from collections.abc import Callable

from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from app.auth.context import (
    AuthenticationContext,
    AuthenticationContextError,
    build_authentication_context,
)
from app.credentials.bearer import BearerCredentialError, BearerCredentialService
from app.db.models.credential_binding import CredentialBinding
from app.db.models.test_identity import TestIdentity


class OpenAPICredentialError(RuntimeError):
    """Sanitized failure at the OpenAPI credential boundary."""


def build_openapi_credential_refresh(
    bind: Engine,
    *,
    target_id: int,
    credential_binding_id: int,
) -> Callable[[], AuthenticationContext]:
    fresh_session = sessionmaker(
        bind=bind,
        autoflush=False,
        expire_on_commit=False,
    )

    def refresh() -> AuthenticationContext:
        try:
            with fresh_session() as db:
                binding = db.get(CredentialBinding, credential_binding_id)
                identity = (
                    db.get(TestIdentity, binding.test_identity_id)
                    if binding is not None
                    else None
                )
                if (
                    binding is None
                    or not binding.is_active
                    or binding.auth_type != "bearer"
                    or binding.source_type != "stored_secret"
                    or identity is None
                    or not identity.is_active
                    or identity.auth_type != binding.auth_type
                    or identity.target_id != target_id
                ):
                    raise OpenAPICredentialError(
                        "The selected OpenAPI credential is unavailable."
                    )
                token = BearerCredentialService(db=db).resolve_binding(
                    identity=identity,
                    credential_binding_id=credential_binding_id,
                )
                return build_authentication_context(
                    identity,
                    bearer_token=token,
                )
        except OpenAPICredentialError:
            raise
        except (BearerCredentialError, AuthenticationContextError):
            raise OpenAPICredentialError(
                "The selected OpenAPI credential is unavailable."
            ) from None

    return refresh
