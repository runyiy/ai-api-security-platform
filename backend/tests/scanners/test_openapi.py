from unittest.mock import MagicMock, patch

import pytest

from app.db.models.authorization_profile import AuthorizationProfile
from app.db.models.scope import Scope
from app.db.models.target import Target
from app.policies.scope_policy import ScopePolicyEngine
from app.scanners.openapi import (
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
        )
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
