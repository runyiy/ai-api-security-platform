import json
from unittest.mock import Mock

import pytest

from app.executors.rate_limit import InMemoryRateLimiter
from app.policies.scope_policy import ScopePolicyEngine
from app.scanners import openapi as openapi_scanner
from app.scanners.openapi import (
    MAX_OPENAPI_ENDPOINTS,
    MAX_OPENAPI_NESTING_DEPTH,
    MAX_OPENAPI_OPERATION_ID_LENGTH,
    MAX_OPENAPI_PARAMETERS_PER_ENDPOINT,
    MAX_OPENAPI_PATH_LENGTH,
    MAX_OPENAPI_PATHS,
    MAX_OPENAPI_STRUCTURE_NODES,
    OpenAPIParseError,
    OpenAPIScanner,
    parse_openapi_schema,
    validate_openapi_structure,
)
from tests.network_gateway_fakes import HandlerNetworkGateway
from tests.scanners.test_openapi import build_revision, build_scope, build_target


def nested_document(max_depth: int) -> dict:
    value: object = 0
    for _ in range(max_depth - 2):
        value = [value]
    return {"paths": {}, "padding": value}


def endpoint_document(count: int) -> dict:
    paths = {}
    remaining = count
    index = 0
    while remaining:
        methods = {}
        for method in ("get", "post", "patch", "delete"):
            if remaining == 0:
                break
            methods[method] = {}
            remaining -= 1
        paths[f"/{index}"] = methods
        index += 1
    return {"paths": paths}


def merged_parameter_document(extra: bool) -> dict:
    path_parameters = [
        {"name": f"p{index}", "in": "query"}
        for index in range(MAX_OPENAPI_PARAMETERS_PER_ENDPOINT)
    ]
    operation_parameters = [{"name": "p0", "in": "query", "required": True}]
    if extra:
        operation_parameters.append({"name": "extra", "in": "query"})
    return {"paths": {"/items": {
        "parameters": path_parameters,
        "get": {"parameters": operation_parameters},
    }}}


def assert_code(code: str, operation) -> None:
    with pytest.raises(OpenAPIParseError) as raised:
        operation()
    assert raised.value.code == code
    assert str(raised.value) == code


def test_reviewed_constants_are_exact_and_not_scanner_configuration() -> None:
    assert (
        MAX_OPENAPI_NESTING_DEPTH,
        MAX_OPENAPI_STRUCTURE_NODES,
        MAX_OPENAPI_PATHS,
        MAX_OPENAPI_ENDPOINTS,
        MAX_OPENAPI_PARAMETERS_PER_ENDPOINT,
        MAX_OPENAPI_PATH_LENGTH,
        MAX_OPENAPI_OPERATION_ID_LENGTH,
    ) == (32, 20_000, 2_000, 4_000, 128, 500, 255)
    assert OpenAPIScanner.__init__.__annotations__ == {
        "policy_engine": ScopePolicyEngine,
        "rate_limiter": openapi_scanner.RateLimiter,
        "network_gateway": openapi_scanner.NetworkGateway,
        "return": None,
    }


def test_depth_32_passes_and_33_fails() -> None:
    validate_openapi_structure(nested_document(32))
    assert_code(
        "openapi_document_too_deep",
        lambda: validate_openapi_structure(nested_document(33)),
    )


def test_node_20000_passes_and_20001_fails() -> None:
    validate_openapi_structure({
        "paths": {}, "padding": [0] * (MAX_OPENAPI_STRUCTURE_NODES - 3)
    })
    assert_code(
        "openapi_document_too_complex",
        lambda: validate_openapi_structure({
            "paths": {}, "padding": [0] * (MAX_OPENAPI_STRUCTURE_NODES - 2)
        }),
    )


def test_paths_2000_pass_and_2001_fail() -> None:
    assert parse_openapi_schema({
        "paths": {f"/{index}": {} for index in range(MAX_OPENAPI_PATHS)}
    }) == []
    assert_code(
        "openapi_too_many_paths",
        lambda: parse_openapi_schema({
            "paths": {f"/{index}": {} for index in range(MAX_OPENAPI_PATHS + 1)}
        }),
    )


def test_endpoints_4000_pass_and_4001_fail() -> None:
    assert len(parse_openapi_schema(endpoint_document(MAX_OPENAPI_ENDPOINTS))) == 4000
    assert_code(
        "openapi_too_many_endpoints",
        lambda: parse_openapi_schema(endpoint_document(MAX_OPENAPI_ENDPOINTS + 1)),
    )


def test_merged_parameters_128_pass_and_129_fail_after_override() -> None:
    endpoints = parse_openapi_schema(merged_parameter_document(extra=False))
    assert len(endpoints[0].parameters) == 128
    assert endpoints[0].parameters[0]["required"] is True
    assert_code(
        "openapi_too_many_parameters",
        lambda: parse_openapi_schema(merged_parameter_document(extra=True)),
    )


def test_path_500_passes_and_501_fails() -> None:
    assert len(parse_openapi_schema({
        "paths": {"/" + "p" * 499: {"get": {}}}
    })[0].path) == 500
    assert_code(
        "openapi_path_too_long",
        lambda: parse_openapi_schema({
            "paths": {"/" + "p" * 500: {"get": {}}}
        }),
    )


def test_operation_id_255_passes_and_256_fails() -> None:
    assert len(parse_openapi_schema({
        "paths": {"/items": {"get": {"operationId": "o" * 255}}}
    })[0].operation_id) == 255
    assert_code(
        "openapi_operation_id_too_long",
        lambda: parse_openapi_schema({
            "paths": {"/items": {"get": {"operationId": "o" * 256}}}
        }),
    )


@pytest.mark.parametrize("value", ["#/components/schemas/X", "https://secret/ref.json"])
def test_nested_local_and_remote_refs_fail_closed(value: str) -> None:
    document = {"paths": {}, "components": {"schemas": {"X": {"$ref": value}}}}
    assert_code(
        "openapi_references_not_supported",
        lambda: validate_openapi_structure(document),
    )


def test_ref_makes_exactly_one_source_fetch_and_no_reference_fetch() -> None:
    body = json.dumps({
        "paths": {},
        "components": {"schemas": {"X": {"$ref": "https://secret/ref.json"}}},
    }).encode()
    gateway = HandlerNetworkGateway(
        lambda request: Mock(status_code=200, content=body)
    )
    scanner = OpenAPIScanner(
        ScopePolicyEngine(platform_allowed_hosts={"example.test"}),
        InMemoryRateLimiter(requests_per_second=1000.0),
        gateway,
    )
    target = build_target()
    scope = build_scope()
    assert_code(
        "openapi_references_not_supported",
        lambda: scanner.scan(
            target=target,
            authorization_revision=build_revision(),
            scopes=[scope],
            source_url="https://example.test/openapi.json",
            refresh_authorization=lambda: (target, build_revision(), [scope]),
            policy_decision_observer=lambda decision: None,
        ),
    )
    assert gateway.calls == 1


def test_decoder_recursion_error_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        openapi_scanner.json,
        "loads",
        lambda value: (_ for _ in ()).throw(RecursionError("attacker fragment")),
    )
    scanner = OpenAPIScanner(
        ScopePolicyEngine(platform_allowed_hosts={"example.test"}),
        InMemoryRateLimiter(requests_per_second=1000.0),
        HandlerNetworkGateway(
            lambda request: Mock(status_code=200, content=b'{"paths":{}}')
        ),
    )
    target = build_target()
    scope = build_scope()
    assert_code(
        "openapi_document_too_deep",
        lambda: scanner.scan(
            target=target,
            authorization_revision=build_revision(),
            scopes=[scope],
            source_url="https://example.test/openapi.json",
            refresh_authorization=lambda: (target, build_revision(), [scope]),
            policy_decision_observer=lambda decision: None,
        ),
    )


def test_mutating_methods_are_imported_as_metadata_but_never_executed() -> None:
    body = json.dumps({
        "paths": {"/items": {
            "post": {}, "patch": {}, "delete": {},
        }}
    }).encode()

    class RecordingGateway(HandlerNetworkGateway):
        def __init__(self) -> None:
            super().__init__(lambda request: Mock(status_code=200, content=body))
            self.methods: list[str] = []

        def request(self, **kwargs):
            self.methods.append(kwargs["method"])
            return super().request(**kwargs)

    gateway = RecordingGateway()
    scanner = OpenAPIScanner(
        ScopePolicyEngine(platform_allowed_hosts={"example.test"}),
        InMemoryRateLimiter(requests_per_second=1000.0),
        gateway,
    )
    target = build_target()
    scope = build_scope()
    result = scanner.scan(
        target=target,
        authorization_revision=build_revision(),
        scopes=[scope],
        source_url="https://example.test/openapi.json",
        refresh_authorization=lambda: (target, build_revision(), [scope]),
        policy_decision_observer=lambda decision: None,
    )

    assert [endpoint.method for endpoint in result.endpoints] == [
        "POST", "PATCH", "DELETE"
    ]
    assert gateway.methods == ["GET"]
