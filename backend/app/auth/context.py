from dataclasses import (
    dataclass,
    field,
)

from app.db.models.test_identity import (
    TestIdentity,
)


class AuthenticationContextError(
    ValueError
):
    pass


@dataclass(frozen=True)
class AuthenticationContext:
    identity_id: int
    identity_name: str
    auth_type: str

    headers: dict[str, str] = field(
        repr=False
    )


def build_authentication_context(
    identity: TestIdentity,
) -> AuthenticationContext:
    if not identity.is_active:
        raise AuthenticationContextError(
            "test identity is inactive"
        )

    if identity.auth_type == "anonymous":
        return AuthenticationContext(
            identity_id=identity.id,
            identity_name=identity.name,
            auth_type=identity.auth_type,
            headers={},
        )

    if identity.auth_type == "bearer":
        credentials = (
            identity.credentials
            or {}
        )

        token = credentials.get(
            "access_token"
        )

        if not isinstance(token, str):
            raise AuthenticationContextError(
                "bearer identity has no "
                "valid access token"
            )

        if not token.strip():
            raise AuthenticationContextError(
                "bearer access token is empty"
            )

        if "\r" in token or "\n" in token:
            raise AuthenticationContextError(
                "bearer access token contains "
                "invalid newline characters"
            )

        return AuthenticationContext(
            identity_id=identity.id,
            identity_name=identity.name,
            auth_type=identity.auth_type,
            headers={
                "Authorization":
                    f"Bearer {token}"
            },
        )

    raise AuthenticationContextError(
        "unsupported authentication type: "
        f"{identity.auth_type!r}"
    )

def apply_authentication_context(
    *,
    request_headers: dict[str, str] | None,
    context: AuthenticationContext,
) -> dict[str, str]:
    headers = dict(
        request_headers
        or {}
    )

    for name in headers:
        if (
            name.strip().lower()
            == "authorization"
        ):
            raise AuthenticationContextError(
                "request headers must not "
                "set Authorization directly"
            )

    headers.update(
        context.headers
    )

    return headers