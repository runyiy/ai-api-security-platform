import hashlib
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.routes import openapi as openapi_routes
from app.executors.rate_limit import InMemoryRateLimiter
from app.policies.scope_policy import ScopePolicyEngine
from app.scanners.openapi import (
    OpenAPIScanner,
    OpenAPIScanResult,
    ParsedEndpoint,
)
from app.schemas.openapi import OpenAPIImportRequest
from tests.scanners.test_openapi import (
    build_revision,
    build_scope,
    build_target,
)
from tests.network_gateway_fakes import StaticJSONNetworkGateway


def test_import_returns_502_for_malformed_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanner = OpenAPIScanner(
        ScopePolicyEngine(
            platform_allowed_hosts={
                "example.test",
            }
        ),
        InMemoryRateLimiter(requests_per_second=1000.0),
        StaticJSONNetworkGateway(),
    )
    monkeypatch.setattr(
        scanner,
        "_fetch_schema",
        lambda **kwargs: (
            hashlib.sha256(b'{"paths":[]}').hexdigest(), 12, "identity",
            hashlib.sha256(b'{"paths":[]}').hexdigest(), 12, {"paths": []},
        ),
    )
    monkeypatch.setattr(
        openapi_routes,
        "scanner",
        scanner,
    )

    scalar_result = Mock()
    scalar_result.all.return_value = [
        build_scope(),
    ]
    db = Mock(spec=Session)
    db.get.side_effect = [build_target(), build_revision()]
    db.get_bind.return_value = Mock()
    db.scalars.return_value = scalar_result
    monkeypatch.setattr(
        openapi_routes,
        "build_execution_authorization_refresh",
        lambda bind, target_id: (
            lambda: (build_target(), build_revision(), [build_scope()])
        ),
    )
    monkeypatch.setattr(
        openapi_routes,
        "build_policy_decision_observer",
        lambda *args, **kwargs: (lambda decision: None),
    )

    with pytest.raises(HTTPException) as exc_info:
        openapi_routes.import_openapi(
            payload=OpenAPIImportRequest(
                target_id=1,
                source_url="https://example.test/openapi.json",
            ),
            db=db,
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == (
        "OpenAPI schema structure is invalid"
    )
    db.add.assert_not_called()
    db.scalar.assert_not_called()
    db.rollback.assert_not_called()
    db.commit.assert_called_once()


def test_import_ends_read_transaction_before_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    transaction_active = {
        "value": False,
    }

    scalar_result = Mock()
    scalar_result.all.return_value = [
        build_scope(),
    ]
    db = Mock(spec=Session)

    def get_object(model, object_id):
        transaction_active["value"] = True
        events.append("db-read")
        if object_id == 1:
            return build_target()
        return build_revision()

    def read_scopes(statement):
        transaction_active["value"] = True
        events.append("db-read")
        return scalar_result

    def commit() -> None:
        transaction_active["value"] = False
        events.append("commit")

    def insert_endpoint(statement):
        transaction_active["value"] = True
        events.append("db-write-query")
        return 1

    db.get.side_effect = get_object
    db.scalars.side_effect = read_scopes
    db.scalar.side_effect = insert_endpoint
    db.commit.side_effect = commit
    db.flush.side_effect = lambda: setattr(db.add.call_args.args[0], "id", 123)
    db.in_transaction.side_effect = (
        lambda: transaction_active["value"]
    )

    db.get_bind.return_value = Mock()

    class OrderingScanner:
        def scan(
            self,
            *,
            target,
            authorization_revision,
            scopes,
            source_url,
            refresh_authorization,
            policy_decision_observer,
        ):
            events.append("scanner-call")
            assert db.in_transaction() is False
            assert authorization_revision.id == 200
            body = b'{"paths":{"/projects":{"get":{}}}}'
            return OpenAPIScanResult(
                source_url=source_url,
                document_sha256=hashlib.sha256(body).hexdigest(),
                document_size_bytes=len(body),
                content_encoding="identity",
                decoded_document_sha256=hashlib.sha256(body).hexdigest(),
                decoded_document_size_bytes=len(body),
                endpoints=[
                    ParsedEndpoint(
                        path="/projects",
                        method="GET",
                        operation_id="list_projects",
                        requires_auth=True,
                        parameters=[],
                        request_body=None,
                        security=None,
                    )
                ],
            )

    monkeypatch.setattr(
        openapi_routes,
        "scanner",
        OrderingScanner(),
    )

    result = openapi_routes.import_openapi(
        payload=OpenAPIImportRequest(
            target_id=1,
            source_url="https://example.test/openapi.json",
        ),
        db=db,
    )

    assert events == [
        "db-read",
        "db-read",
        "db-read",
        "commit",
        "scanner-call",
        "db-write-query",
        "commit",
    ]
    assert result.created == 1
    assert result.updated == 0
    assert result.unchanged == 0
    db.add.assert_called_once()
    db.expunge_all.assert_called_once()
