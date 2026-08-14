from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.routes import openapi as openapi_routes
from app.policies.scope_policy import ScopePolicyEngine
from app.scanners.openapi import (
    OpenAPIScanner,
    ParsedEndpoint,
)
from app.schemas.openapi import OpenAPIImportRequest
from tests.scanners.test_openapi import (
    build_profile,
    build_scope,
    build_target,
)


def test_import_returns_502_for_malformed_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanner = OpenAPIScanner(
        ScopePolicyEngine(
            platform_allowed_hosts={
                "example.test",
            }
        )
    )
    monkeypatch.setattr(
        scanner,
        "_fetch_schema",
        lambda url: {
            "paths": [],
        },
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
    db.get.side_effect = [build_target(), build_profile()]
    db.scalars.return_value = scalar_result

    with pytest.raises(HTTPException) as exc_info:
        openapi_routes.import_openapi(
            payload=OpenAPIImportRequest(
                target_id=1
            ),
            db=db,
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == (
        "OpenAPI schema structure is invalid"
    )


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
        return build_profile()

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
    db.in_transaction.side_effect = (
        lambda: transaction_active["value"]
    )

    class OrderingScanner:
        def scan(self, *, target, authorization_profile, scopes):
            events.append("scanner-call")
            assert db.in_transaction() is False
            assert authorization_profile.id == 100
            return (
                "https://example.test/openapi.json",
                [
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
        payload=OpenAPIImportRequest(target_id=1),
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
    db.add.assert_not_called()
