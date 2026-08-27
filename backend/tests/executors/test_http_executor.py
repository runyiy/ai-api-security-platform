from datetime import datetime, timezone

import httpx
import pytest

from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.scope import Scope
from app.db.models.target import Target
from app.executors.http import (
    ExecutionBlockedError,
    HTTPExecutionError,
    MAX_RESPONSE_BYTES,
    PolicyEnforcedHTTPExecutor,
)
from app.executors.rate_limit import (
    InMemoryRateLimiter,
)
from app.policies.scope_policy import (
    PolicyDecision,
    ScopePolicyEngine,
)
from tests.network_gateway_fakes import HandlerNetworkGateway


def build_target() -> Target:
    return Target(
        id=1,
        authorization_profile_id=100,
        authorization_revision_id=200,
        name="Local Lab",
        base_url="http://localhost:8001",
        environment="development",
        network_mode="private_local",
        is_enabled=True,
    )


def build_revision() -> AuthorizationRevision:
    return AuthorizationRevision(
        id=200,
        authorization_profile_id=100,
        revision_number=1,
        lifecycle_state="active",
        name="Local authorization",
        program_name="Self-controlled lab",
        authorization_type="self_owned",
        automation_allowed=True,
        max_requests_per_second=1000.0,
        allow_get=True,
        allow_post=True,
        allow_patch=True,
        allow_delete=True,
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
            "POST",
            "PATCH",
            "DELETE",
        ],
        is_active=True,
    )


def refresh_authorization():
    return build_target(), build_revision(), [build_scope()]


def observe_policy(decision: PolicyDecision) -> None:
    pass


def build_executor(
    handler,
) -> PolicyEnforcedHTTPExecutor:
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
        network_gateway=HandlerNetworkGateway(handler),
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
        authorization_revision=build_revision(),
        scopes=[
            build_scope(),
        ],
        method="GET",
        url=(
            "http://localhost:8001"
            "/api/projects/2001"
        ),
        headers={},
        refresh_authorization=refresh_authorization,
        policy_decision_observer=observe_policy,
    )

    assert result.status_code == 200

    assert b"2001" in result.body


def test_durable_audit_precedes_gateway() -> None:
    events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        events.append("gateway")
        return httpx.Response(200)

    build_executor(handler).execute(
        target=build_target(),
        authorization_revision=build_revision(),
        scopes=[build_scope()],
        method="GET",
        url="http://localhost:8001/api/projects/2001",
        headers={},
        refresh_authorization=lambda: (
            events.append("refresh")
            or (build_target(), build_revision(), [build_scope()])
        ),
        policy_decision_observer=lambda decision: events.append("audit"),
    )

    assert events == ["refresh", "audit", "gateway"]


def test_internal_exact_plan_approval_satisfies_only_human_approval_gate() -> None:
    executor = build_executor(lambda request: httpx.Response(200, content=b"ok"))
    target = build_target()
    revision = build_revision()
    revision.require_human_execution_approval = True

    result = executor.execute(
        target=target,
        authorization_revision=revision,
        scopes=[build_scope()],
        method="GET",
        url="http://localhost:8001/api/projects/2001",
        headers={},
        refresh_authorization=lambda: (target, revision, [build_scope()]),
        policy_decision_observer=observe_policy,
        human_approval_satisfied=True,
    )

    assert result.status_code == 200


def test_gateway_receives_refreshed_exact_target_id() -> None:
    executor = build_executor(lambda request: httpx.Response(200))
    refreshed_target = build_target()
    refreshed_target.id = 77
    refreshed_scope = build_scope()
    refreshed_scope.target_id = 77

    executor.execute(
        target=build_target(),
        authorization_revision=build_revision(),
        scopes=[build_scope()],
        method="GET",
        url="http://localhost:8001/api/projects/2001",
        headers={},
        refresh_authorization=lambda: (
            refreshed_target,
            build_revision(),
            [refreshed_scope],
        ),
        policy_decision_observer=observe_policy,
    )

    assert executor.network_gateway.target_ids == [77]


def test_missing_refresh_fails_closed_without_network() -> None:
    network_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal network_called
        network_called = True
        return httpx.Response(200)

    with pytest.raises(ExecutionBlockedError) as exc_info:
        build_executor(handler).execute(
            target=build_target(),
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            method="GET",
            url="http://localhost:8001/api/projects/2001",
            headers={},
            policy_decision_observer=observe_policy,
        )

    assert exc_info.value.code == "authorization_refresh_missing"
    assert network_called is False


def test_missing_audit_observer_fails_closed_without_network() -> None:
    network_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal network_called
        network_called = True
        return httpx.Response(200)

    with pytest.raises(ExecutionBlockedError) as exc_info:
        build_executor(handler).execute(
            target=build_target(),
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            method="GET",
            url="http://localhost:8001/api/projects/2001",
            headers={},
            refresh_authorization=refresh_authorization,
        )

    assert exc_info.value.code == "safety_audit_observer_missing"
    assert network_called is False


def test_failing_audit_observer_fails_closed_without_network() -> None:
    network_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal network_called
        network_called = True
        return httpx.Response(200)

    def fail_audit(decision: PolicyDecision) -> None:
        raise RuntimeError("synthetic audit failure")

    with pytest.raises(ExecutionBlockedError) as exc_info:
        build_executor(handler).execute(
            target=build_target(),
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            method="GET",
            url="http://localhost:8001/api/projects/2001",
            headers={},
            refresh_authorization=refresh_authorization,
            policy_decision_observer=fail_audit,
        )

    assert exc_info.value.code == "safety_audit_persistence_failed"
    assert network_called is False


def test_external_network_mode_is_audited_then_blocked_without_network() -> None:
    events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        events.append("network")
        return httpx.Response(200)

    target = build_target()
    target.network_mode = "external_public_authorized"
    with pytest.raises(ExecutionBlockedError) as exc_info:
        build_executor(handler).execute(
            target=target,
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            method="GET",
            url="http://localhost:8001/api/projects/2001",
            headers={},
            refresh_authorization=lambda: (
                target,
                build_revision(),
                [build_scope()],
            ),
            policy_decision_observer=lambda decision: events.append("audit"),
        )

    assert exc_info.value.code == "external_network_mode_not_ready"
    assert events == ["audit"]


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
            authorization_revision=build_revision(),
            scopes=[
                build_scope(),
            ],
            method="GET",
            url=(
                "http://localhost:8001"
                "/admin/users"
            ),
            headers={},
            refresh_authorization=refresh_authorization,
            policy_decision_observer=observe_policy,
        )

    assert called is False


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
def test_blocks_mutating_methods_before_network(method: str) -> None:
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
            authorization_revision=build_revision(),
            scopes=[
                build_scope(),
            ],
            method=method,
            url=(
                "http://localhost:8001"
                "/api/projects/2001"
            ),
            headers={},
            refresh_authorization=refresh_authorization,
            policy_decision_observer=observe_policy,
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
        authorization_revision=build_revision(),
        scopes=[
            build_scope(),
        ],
        method="GET",
        url=(
            "http://localhost:8001"
            "/api/projects/2001"
        ),
        headers={},
        refresh_authorization=refresh_authorization,
        policy_decision_observer=observe_policy,
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
                    authorization_profile_id=100,
                    authorization_revision_id=200,
                    evaluated_at=datetime.now(timezone.utc),
                    matched_scope_id=1,
                )

            return PolicyDecision(
                allowed=False,
                code="authorization_expired",
                reason="Authorization has expired.",
                authorization_profile_id=100,
                authorization_revision_id=200,
                evaluated_at=datetime.now(timezone.utc),
            )

    class RecordingRateLimiter:
        def __init__(self) -> None:
            self.keys: list[str] = []
            self.requested_rates: list[float] = []

        def wait(
            self,
            *,
            key: str,
            requested_requests_per_second: float,
        ) -> None:
            events.append("rate-limit")
            self.keys.append(key)
            self.requested_rates.append(
                requested_requests_per_second
            )

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
        network_gateway=HandlerNetworkGateway(handler),
    )

    with pytest.raises(ExecutionBlockedError) as exc_info:
        executor.execute(
            target=build_target(),
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            method="GET",
            url="http://localhost:8001/api/projects/2001",
            headers={},
            refresh_authorization=lambda: (
                events.append("refresh")
                or (build_target(), build_revision(), [build_scope()])
            ),
            policy_decision_observer=lambda decision: events.append("audit"),
        )
    assert events == ["policy", "rate-limit", "refresh", "policy", "audit"]
    assert policy_engine.evaluation_count == 2
    assert rate_limiter.keys == ["target:1"]
    assert rate_limiter.requested_rates == [1000.0]
    assert exc_info.value.code == "authorization_expired"
    assert transport_called is False


def test_timeout_behavior_remains_bounded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(HTTPExecutionError, match="network_request_failed"):
        build_executor(handler).execute(
            target=build_target(),
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            method="GET",
            url="http://localhost:8001/api/projects/2001",
                headers={},
                refresh_authorization=refresh_authorization,
                policy_decision_observer=observe_policy,
        )


def test_response_size_cap_remains_enforced() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * (MAX_RESPONSE_BYTES + 1),
        )

    with pytest.raises(HTTPExecutionError, match="response_too_large"):
        build_executor(handler).execute(
            target=build_target(),
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            method="GET",
            url="http://localhost:8001/api/projects/2001",
                headers={},
                refresh_authorization=refresh_authorization,
                policy_decision_observer=observe_policy,
        )


@pytest.mark.parametrize(
    "invalid_rate",
    [0.0, -1.0, float("nan"), float("inf")],
)
def test_invalid_profile_rate_fails_closed_before_network(
    invalid_rate: float,
) -> None:
    transport_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal transport_called
        transport_called = True
        return httpx.Response(200)

    revision = build_revision()
    revision.max_requests_per_second = invalid_rate
    executor = build_executor(handler)

    with pytest.raises(ExecutionBlockedError) as exc_info:
        executor.execute(
            target=build_target(),
            authorization_revision=revision,
            scopes=[build_scope()],
            method="GET",
            url="http://localhost:8001/api/projects/2001",
            headers={},
            refresh_authorization=refresh_authorization,
            policy_decision_observer=observe_policy,
        )

    assert exc_info.value.code == "invalid_authorization_rate_limit"
    assert transport_called is False
