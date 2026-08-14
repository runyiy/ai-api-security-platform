from datetime import datetime, timedelta, timezone

import pytest

from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.scope import Scope
from app.db.models.target import Target
from app.policies.scope_policy import ScopePolicyEngine


NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


def build_target() -> Target:
    return Target(
        id=1,
        authorization_profile_id=100,
        name="Local Lab",
        base_url="http://localhost:8001",
        environment="development",
        is_enabled=True,
    )


def build_profile(**values: object) -> AuthorizationProfile:
    defaults = {
        "id": 100,
        "name": "Local authorization",
        "program_name": "Self-controlled lab",
        "authorization_type": "self_owned",
        "automation_allowed": True,
        "allow_get": True,
        "allow_post": True,
        "allow_patch": True,
        "allow_delete": True,
        "require_human_execution_approval": False,
    }
    defaults.update(values)
    return AuthorizationProfile(**defaults)


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
    return ScopePolicyEngine({"localhost", "127.0.0.1", "::1"})


def evaluate(
    *,
    target: Target | None = None,
    profile: AuthorizationProfile | None = None,
    scopes: list[Scope] | None = None,
    request_url: str = "http://localhost:8001/api/projects/2001",
    method: str = "GET",
):
    return build_engine().evaluate(
        target=target or build_target(),
        authorization_profile=profile or build_profile(),
        scopes=[build_scope()] if scopes is None else scopes,
        request_url=request_url,
        method=method,
        evaluation_time=NOW,
    )


def test_allows_valid_profile_and_matching_scope() -> None:
    decision = evaluate()

    assert decision.allowed is True
    assert decision.code == "allowed_by_scope"
    assert decision.matched_scope_id == 10


def test_denies_disabled_target() -> None:
    target = build_target()
    target.is_enabled = False

    decision = evaluate(target=target)

    assert decision.code == "target_disabled"


def test_denies_unbound_target() -> None:
    target = build_target()
    target.authorization_profile_id = None

    decision = evaluate(target=target)

    assert decision.code == "authorization_profile_missing"


def test_denies_missing_explicit_profile() -> None:
    decision = build_engine().evaluate(
        target=build_target(),
        authorization_profile=None,
        scopes=[build_scope()],
        request_url="http://localhost:8001/api/projects/2001",
        method="GET",
        evaluation_time=NOW,
    )

    assert decision.code == "authorization_profile_missing"


def test_denies_mismatched_profile() -> None:
    decision = evaluate(profile=build_profile(id=101))

    assert decision.code == "authorization_profile_mismatch"


def test_valid_from_is_inclusive() -> None:
    assert evaluate(profile=build_profile(valid_from=NOW)).allowed is True

    decision = evaluate(
        profile=build_profile(valid_from=NOW + timedelta(seconds=1))
    )
    assert decision.code == "authorization_not_yet_valid"


def test_valid_until_is_exclusive() -> None:
    decision = evaluate(profile=build_profile(valid_until=NOW))
    assert decision.code == "authorization_expired"

    assert evaluate(
        profile=build_profile(valid_until=NOW + timedelta(seconds=1))
    ).allowed is True


def test_requires_timezone_aware_evaluation_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        build_engine().evaluate(
            target=build_target(),
            authorization_profile=build_profile(),
            scopes=[build_scope()],
            request_url="http://localhost:8001/api/projects/2001",
            method="GET",
            evaluation_time=datetime(2026, 8, 14, 12, 0),
        )


def test_denies_when_automation_is_not_allowed() -> None:
    decision = evaluate(profile=build_profile(automation_allowed=False))

    assert decision.code == "automation_not_allowed"


def test_denies_when_human_approval_is_required() -> None:
    decision = evaluate(
        profile=build_profile(require_human_execution_approval=True)
    )

    assert decision.code == "human_approval_required"


def test_denies_when_profile_disallows_method() -> None:
    decision = evaluate(profile=build_profile(allow_get=False))

    assert decision.code == "authorization_method_not_allowed"


def test_profile_cannot_bypass_scope() -> None:
    decision = evaluate(scopes=[])

    assert decision.code == "no_matching_scope"


def test_denies_wrong_scope_method() -> None:
    decision = evaluate(method="DELETE")

    assert decision.code == "no_matching_scope"


def test_denies_unsupported_put_even_when_profile_flag_is_true() -> None:
    decision = evaluate(
        profile=build_profile(allow_put=True),
        method="PUT",
    )

    assert decision.code == "unsupported_http_method"


def test_platform_allowlist_remains_mandatory() -> None:
    decision = evaluate(
        request_url="http://localhost.evil.com/api/projects/1"
    )

    assert decision.code == "host_not_in_platform_allowlist"


def test_target_origin_equality_remains_mandatory() -> None:
    decision = evaluate(
        request_url="http://localhost:9999/api/projects/1"
    )

    assert decision.code == "target_origin_mismatch"


def test_safe_path_validation_remains_mandatory() -> None:
    decision = evaluate(
        request_url="http://localhost:8001/api/projects/../admin"
    )

    assert decision.code == "unsafe_request_path"
