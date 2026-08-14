from app.scanners.openapi import (
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