from app.db.models.scope import Scope
from app.db.models.target import Target
from app.policies.scope_policy import (
    ScopePolicyEngine,
)


def build_target() -> Target:
    return Target(
        id=1,
        name="Local Lab",
        base_url="http://localhost:8001",
        environment="development",
        is_enabled=True,
    )


def build_scope() -> Scope:
    return Scope(
        id=10,
        target_id=1,
        hostname="localhost",
        path_pattern="/api/projects/*",
        allowed_methods=["GET"],
        is_active=True,
    )


def build_engine() -> ScopePolicyEngine:
    return ScopePolicyEngine(
        {
            "localhost",
            "127.0.0.1",
            "::1",
        }
    )


def test_allows_matching_request() -> None:
    decision = build_engine().evaluate(
        target=build_target(),
        scopes=[build_scope()],
        request_url=(
            "http://localhost:8001"
            "/api/projects/2001"
        ),
        method="GET",
    )

    assert decision.allowed is True
    assert decision.code == "allowed_by_scope"
    assert decision.matched_scope_id == 10


def test_denies_wrong_method() -> None:
    decision = build_engine().evaluate(
        target=build_target(),
        scopes=[build_scope()],
        request_url=(
            "http://localhost:8001"
            "/api/projects/2001"
        ),
        method="DELETE",
    )

    assert decision.allowed is False
    assert decision.code == "no_matching_scope"


def test_denies_wrong_path() -> None:
    decision = build_engine().evaluate(
        target=build_target(),
        scopes=[build_scope()],
        request_url=(
            "http://localhost:8001"
            "/admin/users"
        ),
        method="GET",
    )

    assert decision.allowed is False


def test_denies_wrong_port() -> None:
    decision = build_engine().evaluate(
        target=build_target(),
        scopes=[build_scope()],
        request_url=(
            "http://localhost:9999"
            "/api/projects/1"
        ),
        method="GET",
    )

    assert decision.allowed is False
    assert (
        decision.code
        == "target_origin_mismatch"
    )


def test_denies_unapproved_host() -> None:
    decision = build_engine().evaluate(
        target=build_target(),
        scopes=[build_scope()],
        request_url=(
            "http://localhost.evil.com"
            "/api/projects/1"
        ),
        method="GET",
    )

    assert decision.allowed is False
    assert (
        decision.code
        == "host_not_in_platform_allowlist"
    )


def test_denies_when_no_scope_exists() -> None:
    decision = build_engine().evaluate(
        target=build_target(),
        scopes=[],
        request_url=(
            "http://localhost:8001"
            "/api/projects/1"
        ),
        method="GET",
    )

    assert decision.allowed is False
    assert decision.code == "no_matching_scope"


def test_denies_disabled_target() -> None:
    target = build_target()
    target.is_enabled = False

    decision = build_engine().evaluate(
        target=target,
        scopes=[build_scope()],
        request_url=(
            "http://localhost:8001"
            "/api/projects/1"
        ),
        method="GET",
    )

    assert decision.allowed is False
    assert decision.code == "target_disabled"


def test_denies_dot_segment_path() -> None:
    decision = build_engine().evaluate(
        target=build_target(),
        scopes=[build_scope()],
        request_url=(
            "http://localhost:8001"
            "/api/projects/../admin"
        ),
        method="GET",
    )

    assert decision.allowed is False
    assert decision.code == "unsafe_request_path"