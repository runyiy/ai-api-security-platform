import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.routes import openapi as openapi_routes
from app.executors.rate_limit import InMemoryRateLimiter
from app.network_safety.controller import NetworkExecutionController
from app.network_safety.gateway import NetworkGateway
from app.policies.scope_policy import ScopePolicyEngine
from app.schemas.openapi import OpenAPIImportRequest
from app.scanners import openapi as openapi_scanner
from app.scanners.openapi import OpenAPIScanner, OpenAPIScanResult
from app.scanners.openapi import OpenAPIPolicyDenied
from tests.network_gateway_fakes import HandlerNetworkGateway
from tests.scanners.test_openapi import (
    build_revision, build_scope, build_target,
)


def test_source_url_is_required_without_fallback() -> None:
    with pytest.raises(ValidationError):
        OpenAPIImportRequest(target_id=1)


def test_scanner_hash_input_is_exact_anonymous_gateway_bytes() -> None:
    body = b'{"paths":{"/items":{"post":{}}}}\n'

    class Gateway(HandlerNetworkGateway):
        def __init__(self) -> None:
            super().__init__(lambda request: Mock(status_code=200, content=body))
            self.requests = []

        def request(self, **kwargs):
            self.requests.append(kwargs)
            return super().request(**kwargs)

    gateway = Gateway()
    scanner = OpenAPIScanner(
            ScopePolicyEngine(platform_allowed_hosts={"example.test"}),
            InMemoryRateLimiter(requests_per_second=1000.0),
        gateway,
    )
    target = build_target()
    source_url = "https://example.test/docs/spec.json"
    scope = build_scope()
    scope.path_pattern = "/docs/spec.json"
    result = scanner.scan(
        target=target,
        authorization_revision=build_revision(),
        scopes=[scope],
        source_url=source_url,
        refresh_authorization=lambda: (target, build_revision(), [scope]),
        policy_decision_observer=lambda decision: None,
    )
    assert result.source_url == source_url
    assert result.document_sha256 == hashlib.sha256(body).hexdigest()
    assert result.document_size_bytes == len(body)
    assert result.content_encoding == "identity"
    assert result.decoded_document_sha256 == result.document_sha256
    assert result.decoded_document_size_bytes == result.document_size_bytes
    assert [(item.path, item.method) for item in result.endpoints] == [("/items", "POST")]
    assert gateway.requests[0]["method"] == "GET"
    assert gateway.requests[0]["url"] == source_url
    assert gateway.requests[0]["headers"] == {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    }


def test_alternate_in_scope_localhost_source_uses_real_network_gateway() -> None:
    body = b'{"paths":{"/local":{"get":{}}}}\n'
    observed: list[tuple[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed.append((self.path, self.headers.get("Authorization")))
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source_url = f"http://127.0.0.1:{server.server_port}/docs/spec.json"
        target = build_target()
        target.base_url = f"http://127.0.0.1:{server.server_port}"
        scope = build_scope()
        scope.hostname = "127.0.0.1"
        scope.path_pattern = "/docs/spec.json"
        scanner = OpenAPIScanner(
            ScopePolicyEngine(platform_allowed_hosts={"127.0.0.1"}),
            InMemoryRateLimiter(requests_per_second=1000.0),
            NetworkGateway(controller=NetworkExecutionController()),
        )
        result = scanner.scan(
            target=target,
            authorization_revision=build_revision(),
            scopes=[scope],
            source_url=source_url,
            refresh_authorization=lambda: (target, build_revision(), [scope]),
            policy_decision_observer=lambda decision: None,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.source_url == source_url
    assert result.document_sha256 == hashlib.sha256(body).hexdigest()
    assert result.document_size_bytes == len(body)
    assert [(item.path, item.method) for item in result.endpoints] == [("/local", "GET")]
    assert observed == [("/docs/spec.json", None)]


@pytest.mark.parametrize(
    "source_url",
    [
        "https://other.test/openapi.json",
        "https://example.test:8443/openapi.json",
        "https://example.test/private/openapi.json",
    ],
)
def test_exact_source_cross_origin_or_out_of_scope_is_zero_network(
    source_url: str,
) -> None:
    gateway = HandlerNetworkGateway(
        lambda request: pytest.fail("policy denial must not reach the gateway")
    )
    scanner = OpenAPIScanner(
        ScopePolicyEngine(platform_allowed_hosts={"example.test", "other.test"}),
        InMemoryRateLimiter(requests_per_second=1000.0),
        gateway,
    )
    with pytest.raises(OpenAPIPolicyDenied):
        scanner.scan(
            target=build_target(),
            authorization_revision=build_revision(),
            scopes=[build_scope()],
            source_url=source_url,
            policy_decision_observer=lambda decision: None,
        )
    assert gateway.calls == 0


def test_provenance_failure_rolls_back_endpoint_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Mock(spec=Session)
    target = build_target()
    db.get.side_effect = [target, build_revision()]
    scopes = Mock()
    scopes.all.return_value = [build_scope()]
    db.scalars.return_value = scopes
    db.get_bind.return_value = Mock()
    body = json.dumps({"paths": {"/new": {"get": {}}}}).encode()
    monkeypatch.setattr(
        openapi_routes.scanner, "scan",
        lambda **kwargs: OpenAPIScanResult(
            source_url=kwargs["source_url"],
            document_sha256=hashlib.sha256(body).hexdigest(),
            document_size_bytes=len(body),
            content_encoding="identity",
            decoded_document_sha256=hashlib.sha256(body).hexdigest(),
            decoded_document_size_bytes=len(body),
            endpoints=[
            openapi_routes.ParsedEndpoint(
                path="/new", method="GET", operation_id=None,
                requires_auth=False, parameters=[], request_body=None,
                security=None,
            )
        ]),
    )
    db.scalar.return_value = 1
    db.add.side_effect = RuntimeError("provenance insert failed")
    with pytest.raises(RuntimeError):
        openapi_routes.import_openapi(
            OpenAPIImportRequest(
                target_id=1, source_url="https://example.test/openapi.json"
            ), db,
        )
    db.rollback.assert_called_once()
    db.commit.assert_called_once()  # pre-network read transaction only
    assert hashlib.sha256(body).hexdigest() not in repr(db.commit.call_args_list)


def test_provenance_is_computed_before_json_and_openapi_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b'{"paths":{"/ordered":{"get":{}}}}'
    events: list[str] = []
    real_sha256 = hashlib.sha256
    real_loads = json.loads
    real_parse = openapi_scanner.parse_openapi_schema

    monkeypatch.setattr(
        openapi_scanner.hashlib,
        "sha256",
        lambda value: events.append("sha256") or real_sha256(value),
    )
    monkeypatch.setattr(
        openapi_scanner.json,
        "loads",
        lambda value: events.append("json") or real_loads(value),
    )
    monkeypatch.setattr(
        openapi_scanner,
        "parse_openapi_schema",
        lambda schema: events.append("openapi") or real_parse(schema),
    )
    scanner = OpenAPIScanner(
        ScopePolicyEngine(platform_allowed_hosts={"example.test"}),
        InMemoryRateLimiter(requests_per_second=1000.0),
        HandlerNetworkGateway(
            lambda request: Mock(status_code=200, content=body)
        ),
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

    assert events == ["sha256", "sha256", "json", "openapi"]
    assert result.document_sha256 == real_sha256(body).hexdigest()
    assert result.document_size_bytes == len(body)
