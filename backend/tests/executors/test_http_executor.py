import httpx
import pytest

from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.scope import Scope
from app.db.models.target import Target
from app.executors.http import (
    ExecutionBlockedError,
    PolicyEnforcedHTTPExecutor,
)
from app.executors.rate_limit import (
    InMemoryRateLimiter,
)
from app.policies.scope_policy import (
    PolicyDecision,
    ScopePolicyEngine,
)


def build_target() -> Target:
    return Target(
        id=1,
        authorization_profile_id=100,
        name="Local Lab",
        base_url="http://localhost:8001",
        environment="development",
        is_enabled=True,
    )


def build_profile() -> AuthorizationProfile:
    return AuthorizationProfile(
        id=100,
        name="Local authorization",
        program_name="Self-controlled lab",
        authorization_type="self_owned",
        automation_allowed=True,
        allow_get=True,
        require_human_execution_approval=False,
    )


def build_scope() -> Scope:
    return Scope(
        id=1,
        target_id=1,
        hostname="localhost",
        path_pattern="/api/projects/*",
        allowed_methods=[
            "GET",
            "PATCH",
            "DELETE",
        ],
        is_active=True,
    )


def build_executor(
    handler,
) -> PolicyEnforcedHTTPExecutor:
    transport = httpx.MockTransport(
        handler
    )

    return PolicyEnforcedHTTPExecutor(
        policy_engine=ScopePolicyEngine(
            {
                "localhost",
                "127.0.0.1",
                "::1",
            }
        ),
        rate_limiter=InMemoryRateLimiter(
            requests_per_second=1000.0
        ),
        transport=transport,
    )


def test_executes_allowed_get() -> None:
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": 2001,
                "name": "Secret Project",
            },
        )

    executor = build_executor(
        handler
    )

    result = executor.execute(
        target=build_target(),
        authorization_profile=build_profile(),
        scopes=[
            build_scope(),
        ],
        method="GET",
        url=(
            "http://localhost:8001"
            "/api/projects/2001"
        ),
        headers={},
    )

    assert result.status_code == 200

    assert b"2001" in result.body


def test_denies_request_outside_scope() -> None:
    called = False

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        nonlocal called
        called = True

        return httpx.Response(
            200
        )

    executor = build_executor(
        handler
    )

    with pytest.raises(
        ExecutionBlockedError
    ):
        executor.execute(
            target=build_target(),
            authorization_profile=build_profile(),
            scopes=[
                build_scope(),
            ],
            method="GET",
            url=(
                "http://localhost:8001"
                "/admin/users"
            ),
            headers={},
        )

    assert called is False


def test_blocks_delete_before_network() -> None:
    called = False

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        nonlocal called
        called = True

        return httpx.Response(
            204
        )

    executor = build_executor(
        handler
    )

    with pytest.raises(
        ExecutionBlockedError
    ) as exc_info:
        executor.execute(
            target=build_target(),
            authorization_profile=build_profile(),
            scopes=[
                build_scope(),
            ],
            method="DELETE",
            url=(
                "http://localhost:8001"
                "/api/projects/2001"
            ),
            headers={},
        )

    assert (
        exc_info.value.code
        == "automatic_method_blocked"
    )

    assert called is False


def test_does_not_follow_redirect() -> None:
    calls = 0

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        nonlocal calls
        calls += 1

        return httpx.Response(
            302,
            headers={
                "Location":
                    "http://example.com/private"
            },
        )

    executor = build_executor(
        handler
    )

    result = executor.execute(
        target=build_target(),
        authorization_profile=build_profile(),
        scopes=[
            build_scope(),
        ],
        method="GET",
        url=(
            "http://localhost:8001"
            "/api/projects/2001"
        ),
        headers={},
    )

    assert result.status_code == 302
    assert calls == 1


def test_revalidates_policy_after_rate_limit_wait() -> None:
    events: list[str] = []
    transport_called = False

    class SequencedPolicyEngine:
        def __init__(self) -> None:
            self.evaluation_count = 0

        def evaluate(self, **kwargs) -> PolicyDecision:
            events.append("policy")
            self.evaluation_count += 1

            if self.evaluation_count == 1:
                return PolicyDecision(
                    allowed=True,
                    code="allowed_by_scope",
                    reason="Request matches an active scope.",
                    matched_scope_id=1,
                )

            return PolicyDecision(
                allowed=False,
                code="authorization_expired",
                reason="Authorization has expired.",
            )

    class RecordingRateLimiter:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def wait(self, *, key: str) -> None:
            events.append("rate-limit")
            self.keys.append(key)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal transport_called
        transport_called = True
        events.append("network")
        return httpx.Response(200)

    policy_engine = SequencedPolicyEngine()
    rate_limiter = RecordingRateLimiter()
    executor = PolicyEnforcedHTTPExecutor(
        policy_engine=policy_engine,
        rate_limiter=rate_limiter,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ExecutionBlockedError) as exc_info:
        executor.execute(
            target=build_target(),
            authorization_profile=build_profile(),
            scopes=[build_scope()],
            method="GET",
            url="http://localhost:8001/api/projects/2001",
            headers={},
        )

    assert policy_engine.evaluation_count == 2
    assert rate_limiter.keys == ["target:1"]
    assert events == ["policy", "rate-limit", "policy"]
    assert exc_info.value.code == "authorization_expired"
    assert transport_called is False
