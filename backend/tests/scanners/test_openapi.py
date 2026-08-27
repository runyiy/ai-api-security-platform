from datetime import datetime, timezone
import httpx
import pytest

from app.db.models.authorization_revision import AuthorizationRevision
from app.db.models.scope import Scope
from app.db.models.target import Target
from app.executors.rate_limit import InMemoryRateLimiter
from app.policies.scope_policy import PolicyDecision, ScopePolicyEngine
from app.scanners.openapi import (
    MAX_OPENAPI_RESPONSE_BYTES,
    OpenAPIExecutionBlocked,
    OpenAPIPolicyDenied,
    OpenAPIScanError,
    OpenAPIScanner,
    parse_openapi_schema,
)
from tests.network_gateway_fakes import StaticJSONNetworkGateway


def test_parses_endpoint() -> None:
    schema = {
        "openapi": "3.1.0",
        "paths": {
            "/api/projects/{project_id}": {
                "get": {
                    "operationId": "get_project",
                    "parameters": [
                        {
                            "name": "project_id",
                            "in": "path",
                            "required": True,
                        }
                    ],
                }
            }
        },
    }

    endpoints = parse_openapi_schema(
        schema
    )

    assert len(endpoints) == 1

    endpoint = endpoints[0]

    assert (
        endpoint.path
        == "/api/projects/{project_id}"
    )

    assert endpoint.method == "GET"

    assert (
        endpoint.operation_id
        == "get_project"
    )

    assert len(endpoint.parameters) == 1


def test_ignores_unsupported_methods() -> None:
    schema = {
        "paths": {
            "/api/projects": {
                "get": {},
                "options": {},
                "head": {},
            }
        }
    }

    endpoints = parse_openapi_schema(
        schema
    )

    assert len(endpoints) == 1

    assert endpoints[0].method == "GET"


def test_parses_methods_in_deterministic_order() -> None:
    schema = {
        "paths": {
            "/first": {
                "delete": {},
                "patch": {},
                "post": {},
                "get": {},
            },
            "/second": {
                "post": {},
                "get": {},
            },
        }
    }

    endpoints = parse_openapi_schema(schema)

    assert [
        (endpoint.path, endpoint.method)
        for endpoint in endpoints
    ] == [
        ("/first", "GET"),
        ("/first", "POST"),
        ("/first", "PATCH"),
        ("/first", "DELETE"),
        ("/second", "GET"),
        ("/second", "POST"),
    ]


def test_inherits_root_security() -> None:
    schema = {
        "security": [
            {
                "BearerAuth": [],
            }
        ],
        "paths": {
            "/api/projects": {
                "get": {},
            }
        },
    }

    endpoints = parse_openapi_schema(
        schema
    )

    assert (
        endpoints[0].requires_auth
        is True
    )


def test_operation_can_disable_security() -> None:
    schema = {
        "security": [
            {
                "BearerAuth": [],
            }
        ],
        "paths": {
            "/health": {
                "get": {
                    "security": [],
                }
            }
        },
    }

    endpoints = parse_openapi_schema(
        schema
    )

    assert (
        endpoints[0].requires_auth
        is False
    )


def test_operation_parameter_overrides_path_parameter() -> None:
    schema = {
        "paths": {
            "/projects/{project_id}": {
                "parameters": [
                    {
                        "name": "project_id",
                        "in": "path",
                        "required": True,
                        "description": "path version",
                    }
                ],
                "get": {
                    "parameters": [
                        {
                            "name": "project_id",
                            "in": "path",
                            "required": True,
                            "description": (
                                "operation version"
                            ),
                        }
                    ]
                },
            }
        }
    }

    endpoints = parse_openapi_schema(
        schema
    )

    parameter = (
        endpoints[0].parameters[0]
    )

    assert (
        parameter["description"]
        == "operation version"
    )


def build_scanner() -> OpenAPIScanner:
    return OpenAPIScanner(
        ScopePolicyEngine(
            platform_allowed_hosts={
                "example.test",
            }
        ),
        InMemoryRateLimiter(requests_per_second=1000.0),
        StaticJSONNetworkGateway(),
    )


def build_target() -> Target:
    return Target(
        id=1,
        authorization_profile_id=100,
        authorization_revision_id=200,
        name="Example",
        base_url="https://example.test",
        environment="test",
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
        require_human_execution_approval=False,
    )


def build_scope() -> Scope:
    return Scope(
        id=1,
        target_id=1,
        hostname="example.test",
        path_pattern="/openapi.json",
        allowed_methods=["GET"],
        is_active=True,
    )


def refresh_authorization():
    return build_target(), build_revision(), [build_scope()]


def test_scanner_wraps_schema_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanner = build_scanner()
    monkeypatch.setattr(
        scanner,
        "_fetch_schema",
        lambda **kwargs: {
            "paths": [],
        },
    )

    with pytest.raises(
        OpenAPIScanError,
        match="schema structure is invalid",
    ) as exc_info:
        scanner.scan(
            target=build_target(),
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            refresh_authorization=refresh_authorization,
            policy_decision_observer=lambda decision: None,
        )

    assert isinstance(
        exc_info.value.__cause__,
        ValueError,
    )


def test_missing_refresh_fails_closed_without_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanner = build_scanner()
    network_called = False

    def fetch_schema(**kwargs):
        nonlocal network_called
        network_called = True
        return {"paths": {}}

    monkeypatch.setattr(scanner, "_fetch_schema", fetch_schema)
    with pytest.raises(OpenAPIExecutionBlocked) as exc_info:
        scanner.scan(
            target=build_target(),
            authorization_revision=build_revision(),
            scopes=[build_scope()],
        )

    assert exc_info.value.code == "authorization_refresh_missing"
    assert network_called is False


def test_missing_audit_observer_fails_closed_without_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanner = build_scanner()
    network_called = False

    def fetch_schema(**kwargs):
        nonlocal network_called
        network_called = True
        return {"paths": {}}

    monkeypatch.setattr(scanner, "_fetch_schema", fetch_schema)
    with pytest.raises(OpenAPIScanError, match="observer is unavailable"):
        scanner.scan(
            target=build_target(),
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            refresh_authorization=refresh_authorization,
        )

    assert network_called is False


def test_external_network_mode_is_audited_then_blocked_without_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    scanner = build_scanner()
    monkeypatch.setattr(
        scanner,
        "_fetch_schema",
        lambda **kwargs: events.append("network") or {"paths": {}},
    )
    target = build_target()
    target.network_mode = "external_public_authorized"

    with pytest.raises(OpenAPIExecutionBlocked) as exc_info:
        scanner.scan(
            target=target,
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            refresh_authorization=lambda: (
                target,
                build_revision(),
                [build_scope()],
            ),
            policy_decision_observer=lambda decision: events.append("audit"),
        )

    assert exc_info.value.code == "external_network_mode_not_ready"
    assert events == ["audit"]


class RecordingRateLimiter:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[tuple[str, float]] = []

    def wait(
        self,
        *,
        key: str,
        requested_requests_per_second: float,
    ) -> None:
        self.events.append("rate-limit")
        self.calls.append((key, requested_requests_per_second))


class SequencedPolicyEngine:
    def __init__(
        self,
        events: list[str],
        decisions: list[PolicyDecision],
    ) -> None:
        self.events = events
        self.decisions = iter(decisions)
        self.evaluation_count = 0

    def evaluate(self, **kwargs) -> PolicyDecision:
        self.events.append("policy")
        self.evaluation_count += 1
        return next(self.decisions)


def allowed_decision() -> PolicyDecision:
    return PolicyDecision(
        allowed=True,
        code="allowed_by_scope",
        reason="Request matches an active scope.",
        authorization_profile_id=100,
        authorization_revision_id=200,
        evaluated_at=datetime.now(timezone.utc),
        matched_scope_id=1,
    )


def denied_decision(code: str) -> PolicyDecision:
    return PolicyDecision(
        allowed=False,
        code=code,
        reason="Request denied for test.",
        authorization_profile_id=100,
        authorization_revision_id=200,
        evaluated_at=datetime.now(timezone.utc),
    )


def observe_policy(decision: PolicyDecision) -> None:
    pass


def test_scan_orders_policy_rate_limit_policy_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    policy_engine = SequencedPolicyEngine(
        events,
        [allowed_decision(), allowed_decision()],
    )
    rate_limiter = RecordingRateLimiter(events)
    scanner = OpenAPIScanner(policy_engine, rate_limiter, StaticJSONNetworkGateway())
    monkeypatch.setattr(
        scanner,
        "_fetch_schema",
        lambda **kwargs: events.append("network") or {"paths": {}},
    )

    scanner.scan(
        target=build_target(),
        authorization_revision=build_revision(),
        scopes=[build_scope()],
        refresh_authorization=lambda: (
            events.append("refresh")
            or (build_target(), build_revision(), [build_scope()])
        ),
        policy_decision_observer=lambda decision: events.append("audit"),
    )

    assert events == [
        "policy",
        "rate-limit",
        "refresh",
        "policy",
        "audit",
        "network",
    ]
    assert policy_engine.evaluation_count == 2
    assert rate_limiter.calls == [("target:1", 1000.0)]


def test_gateway_receives_refreshed_exact_target_id() -> None:
    scanner = build_scanner()
    refreshed_target = build_target()
    refreshed_target.id = 77
    refreshed_scope = build_scope()
    refreshed_scope.target_id = 77

    scanner.scan(
        target=build_target(),
        authorization_revision=build_revision(),
        scopes=[build_scope()],
        refresh_authorization=lambda: (
            refreshed_target,
            build_revision(),
            [refreshed_scope],
        ),
        policy_decision_observer=observe_policy,
    )

    assert scanner.network_gateway.target_ids == [77]


def test_first_policy_denial_skips_limiter_and_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    scanner = OpenAPIScanner(
        SequencedPolicyEngine(events, [denied_decision("no_matching_scope")]),
        RecordingRateLimiter(events),
        StaticJSONNetworkGateway(),
    )
    monkeypatch.setattr(
        scanner,
        "_fetch_schema",
        lambda **kwargs: events.append("network") or {"paths": {}},
    )

    with pytest.raises(OpenAPIPolicyDenied) as exc_info:
        scanner.scan(
            target=build_target(),
            authorization_revision=build_revision(),
            scopes=[],
            policy_decision_observer=lambda decision: events.append("audit"),
        )

    assert exc_info.value.decision.code == "no_matching_scope"
    assert events == ["policy", "audit"]


def test_final_policy_denial_after_wait_skips_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    scanner = OpenAPIScanner(
        SequencedPolicyEngine(
            events,
            [allowed_decision(), denied_decision("authorization_expired")],
        ),
        RecordingRateLimiter(events),
        StaticJSONNetworkGateway(),
    )
    monkeypatch.setattr(
        scanner,
        "_fetch_schema",
        lambda **kwargs: events.append("network") or {"paths": {}},
    )

    with pytest.raises(OpenAPIPolicyDenied) as exc_info:
        scanner.scan(
            target=build_target(),
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            refresh_authorization=refresh_authorization,
            policy_decision_observer=lambda decision: events.append("audit"),
        )

    assert exc_info.value.decision.code == "authorization_expired"
    assert events == ["policy", "rate-limit", "policy", "audit"]


@pytest.mark.parametrize(
    "invalid_rate",
    [0.0, -1.0, float("nan"), float("inf")],
)
def test_invalid_runtime_rate_fails_closed_before_network(
    invalid_rate: float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanner = build_scanner()
    network_called = False

    def fetch_schema(**kwargs) -> dict[str, object]:
        nonlocal network_called
        network_called = True
        return {"paths": {}}

    monkeypatch.setattr(scanner, "_fetch_schema", fetch_schema)
    revision = build_revision()
    revision.max_requests_per_second = invalid_rate

    with pytest.raises(OpenAPIExecutionBlocked) as exc_info:
        scanner.scan(
            target=build_target(),
            authorization_revision=revision,
            scopes=[build_scope()],
            refresh_authorization=refresh_authorization,
        )

    assert (
        exc_info.value.code
        == "invalid_authorization_rate_limit"
    )
    assert network_called is False


def test_fetch_schema_rejects_timeout() -> None:
    gateway = StaticJSONNetworkGateway()
    gateway.handler = lambda request: (_ for _ in ()).throw(RuntimeError("secret"))
    scanner = OpenAPIScanner(
        ScopePolicyEngine({"example.test"}),
        InMemoryRateLimiter(requests_per_second=1000.0),
        gateway,
    )
    with pytest.raises(OpenAPIScanError, match="network_request_failed"):
        scanner._fetch_schema(
            target=build_target(), url="https://example.test/openapi.json"
        )


def test_fetch_schema_enforces_response_size_cap() -> None:
    gateway = StaticJSONNetworkGateway()
    gateway.handler = lambda request: httpx.Response(
        200, content=b"x" * (MAX_OPENAPI_RESPONSE_BYTES + 1)
    )
    scanner = OpenAPIScanner(
        ScopePolicyEngine({"example.test"}),
        InMemoryRateLimiter(requests_per_second=1000.0),
        gateway,
    )
    with pytest.raises(OpenAPIScanError, match="response_too_large"):
        scanner._fetch_schema(
            target=build_target(), url="https://example.test/openapi.json"
        )
