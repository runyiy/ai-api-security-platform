import pytest
from pydantic import SecretStr

from app.auth.context import (
    AuthenticationContextError,
    apply_authentication_context,
    build_authentication_context,
)
from app.db.models.test_identity import (
    TestIdentity,
)


def build_bearer_identity() -> TestIdentity:
    return TestIdentity(
        id=1,
        target_id=1,
        name="User A",
        role="user",
        auth_type="bearer",
        credentials={
            "access_token":
                "dev-user-a-token"
        },
        is_active=True,
    )


def build_anonymous_identity() -> TestIdentity:
    return TestIdentity(
        id=2,
        target_id=1,
        name="Anonymous",
        role=None,
        auth_type="anonymous",
        credentials=None,
        is_active=True,
    )


def test_builds_bearer_context() -> None:
    identity = build_bearer_identity()

    context = (
        build_authentication_context(
            identity,
            bearer_token=SecretStr(
                "resolved-dev-user-a-token"
            ),
        )
    )

    assert (
        context.headers[
            "Authorization"
        ]
        == "Bearer resolved-dev-user-a-token"
    )


def test_anonymous_has_no_auth_headers() -> None:
    identity = (
        build_anonymous_identity()
    )

    context = (
        build_authentication_context(
            identity
        )
    )

    assert context.headers == {}


def test_denies_inactive_identity() -> None:
    identity = build_bearer_identity()
    identity.is_active = False

    with pytest.raises(
        AuthenticationContextError
    ):
        build_authentication_context(
            identity
        )


def test_bearer_requires_token() -> None:
    identity = build_bearer_identity()

    with pytest.raises(
        AuthenticationContextError
    ):
        build_authentication_context(
            identity,
            bearer_token=None,
        )


def test_bearer_never_uses_legacy_plaintext_credentials() -> None:
    identity = build_bearer_identity()

    with pytest.raises(AuthenticationContextError) as raised:
        build_authentication_context(identity)

    assert "dev-user-a-token" not in str(raised.value)


def test_rejects_manual_authorization_header() -> None:
    identity = build_bearer_identity()

    context = (
        build_authentication_context(
            identity,
            bearer_token=SecretStr(
                "resolved-dev-user-a-token"
            ),
        )
    )

    with pytest.raises(
        AuthenticationContextError
    ):
        apply_authentication_context(
            request_headers={
                "Authorization":
                    "Bearer fake-admin-token"
            },
            context=context,
        )


def test_applies_identity_authentication() -> None:
    identity = build_bearer_identity()

    context = (
        build_authentication_context(
            identity,
            bearer_token=SecretStr(
                "resolved-dev-user-a-token"
            ),
        )
    )

    headers = (
        apply_authentication_context(
            request_headers={
                "Accept":
                    "application/json",
            },
            context=context,
        )
    )

    assert (
        headers["Accept"]
        == "application/json"
    )

    assert (
        headers["Authorization"]
        == "Bearer resolved-dev-user-a-token"
    )


def test_context_repr_does_not_contain_token() -> None:
    identity = build_bearer_identity()

    context = (
        build_authentication_context(
            identity,
            bearer_token=SecretStr(
                "resolved-dev-user-a-token"
            ),
        )
    )

    result = repr(context)

    assert (
        "resolved-dev-user-a-token"
        not in result
    )
