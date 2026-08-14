from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.scope import Scope
from app.db.models.target import Target
from app.executors.rate_limit import InMemoryRateLimiter
from app.policies.scope_policy import PolicyDecision, ScopePolicyEngine
from app.scanners.openapi import (
    MAX_OPENAPI_RESPONSE_BYTES,
    OpenAPIPolicyDenied,
    OpenAPIScanError,
    OpenAPIScanner,
    parse_openapi_schema,
)


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
    )


def build_target() -> Target:
    return Target(
        id=1,
        authorization_profile_id=100,
        name="Example",
        base_url="https://example.test",
        environment="test",
        is_enabled=True,
    )


def build_profile() -> AuthorizationProfile:
    return AuthorizationProfile(
        id=100,
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


def test_scanner_wraps_schema_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanner = build_scanner()
    monkeypatch.setattr(
        scanner,
        "_fetch_schema",
        lambda url: {
            "paths": [],
        },
    )

    with pytest.raises(
        OpenAPIScanError,
        match="schema structure is invalid",
    ) as exc_info:
        scanner.scan(
            target=build_target(),
            authorization_profile=build_profile(),
            scopes=[build_scope()],
        )

    assert isinstance(
        exc_info.value.__cause__,
        ValueError,
    )


def test_fetch_schema_disables_environment_proxy() -> None:
    scanner = build_scanner()
    response = MagicMock()
    response.__enter__.return_value = response
    response.iter_bytes.return_value = [
        b'{"paths": {}}',
    ]
    client = MagicMock()
    client.__enter__.return_value = client
    client.stream.return_value = response

    with patch(
        "app.scanners.openapi.httpx.Client",
        return_value=client,
    ) as client_class:
        schema = scanner._fetch_schema(
            "https://example.test/openapi.json"
        )

    assert schema == {
        "paths": {},
    }
    assert (
        client_class.call_args.kwargs[
            "trust_env"
        ]
        is False
    )
    assert client_class.call_args.kwargs["follow_redirects"] is False
    assert isinstance(client_class.call_args.kwargs["timeout"], httpx.Timeout)


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
        matched_scope_id=1,
    )


def denied_decision(code: str) -> PolicyDecision:
    return PolicyDecision(
        allowed=False,
        code=code,
        reason="Request denied for test.",
    )


def test_scan_orders_policy_rate_limit_policy_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    policy_engine = SequencedPolicyEngine(
        events,
        [allowed_decision(), allowed_decision()],
    )
    rate_limiter = RecordingRateLimiter(events)
    scanner = OpenAPIScanner(policy_engine, rate_limiter)
    monkeypatch.setattr(
        scanner,
        "_fetch_schema",
        lambda url: events.append("network") or {"paths": {}},
    )

    scanner.scan(
        target=build_target(),
        authorization_profile=build_profile(),
        scopes=[build_scope()],
    )

    assert events == ["policy", "rate-limit", "policy", "network"]
    assert policy_engine.evaluation_count == 2
    assert rate_limiter.calls == [("target:1", 1000.0)]


def test_first_policy_denial_skips_limiter_and_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    scanner = OpenAPIScanner(
        SequencedPolicyEngine(events, [denied_decision("no_matching_scope")]),
        RecordingRateLimiter(events),
    )
    monkeypatch.setattr(
        scanner,
        "_fetch_schema",
        lambda url: events.append("network") or {"paths": {}},
    )

    with pytest.raises(OpenAPIPolicyDenied) as exc_info:
        scanner.scan(
            target=build_target(),
            authorization_profile=build_profile(),
            scopes=[],
        )

    assert exc_info.value.decision.code == "no_matching_scope"
    assert events == ["policy"]


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
    )
    monkeypatch.setattr(
        scanner,
        "_fetch_schema",
        lambda url: events.append("network") or {"paths": {}},
    )

    with pytest.raises(OpenAPIPolicyDenied) as exc_info:
        scanner.scan(
            target=build_target(),
            authorization_profile=build_profile(),
            scopes=[build_scope()],
        )

    assert exc_info.value.decision.code == "authorization_expired"
    assert events == ["policy", "rate-limit", "policy"]


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

    def fetch_schema(url: str) -> dict[str, object]:
        nonlocal network_called
        network_called = True
        return {"paths": {}}

    monkeypatch.setattr(scanner, "_fetch_schema", fetch_schema)
    profile = build_profile()
    profile.max_requests_per_second = invalid_rate

    with pytest.raises(OpenAPIPolicyDenied) as exc_info:
        scanner.scan(
            target=build_target(),
            authorization_profile=profile,
            scopes=[build_scope()],
        )

    assert (
        exc_info.value.decision.code
        == "invalid_authorization_rate_limit"
    )
    assert network_called is False


def test_fetch_schema_rejects_timeout() -> None:
    scanner = build_scanner()

    with patch(
        "app.scanners.openapi.httpx.Client",
        side_effect=httpx.ReadTimeout("timed out"),
    ):
        with pytest.raises(OpenAPIScanError, match="Unable to fetch"):
            scanner._fetch_schema("https://example.test/openapi.json")


def test_fetch_schema_enforces_response_size_cap() -> None:
    scanner = build_scanner()
    response = MagicMock()
    response.__enter__.return_value = response
    response.iter_bytes.return_value = [
        b"x" * (MAX_OPENAPI_RESPONSE_BYTES + 1),
    ]
    client = MagicMock()
    client.__enter__.return_value = client
    client.stream.return_value = response

    with patch(
        "app.scanners.openapi.httpx.Client",
        return_value=client,
    ):
        with pytest.raises(OpenAPIScanError, match="size limit"):
            scanner._fetch_schema("https://example.test/openapi.json")
