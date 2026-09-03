from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.core.config import settings
from app.api.routes import openapi as openapi_routes
from app.db.models import (
    CredentialBinding,
    Endpoint,
    EndpointResourceBinding,
    ExecutionPlan,
    OpenAPIImportRecord,
    PlanAction,
    Resource,
    Scope,
    Target,
    TestCase as StoredTestCase,
    TestIdentity as StoredTestIdentity,
    TestRun as StoredTestRun,
)
from app.db.session import SessionLocal
from app.main import app
from app.scanners.openapi import OpenAPIScanResult, ParsedEndpoint
from app.schemas.openapi import OpenAPIImportRequest


client = TestClient(app)


def make_endpoint(
    parameters: object,
    *,
    path: str = "/orders/{order_id}",
    network_mode: str = "private_local",
) -> tuple[int, int]:
    with SessionLocal() as db:
        target = Target(
            name=f"openapi-binding-{uuid4()}",
            base_url=f"https://{uuid4()}.example.test",
            environment="test",
            network_mode=network_mode,
        )
        db.add(target)
        db.flush()
        endpoint = Endpoint(
            target_id=target.id,
            path=path,
            method="GET",
            operation_id="get_resource",
            requires_auth=True,
            parameters=parameters,
            request_body={"must": "remain unchanged"},
            security=None,
        )
        db.add(endpoint)
        db.commit()
        return target.id, endpoint.id


def cleanup(target_ids: list[int]) -> None:
    with SessionLocal() as db:
        endpoint_ids = list(db.scalars(
            select(Endpoint.id).where(Endpoint.target_id.in_(target_ids))
        ))
        db.execute(delete(OpenAPIImportRecord).where(
            OpenAPIImportRecord.target_id.in_(target_ids)
        ))
        if endpoint_ids:
            db.execute(delete(EndpointResourceBinding).where(
                EndpointResourceBinding.endpoint_id.in_(endpoint_ids)
            ))
            db.execute(delete(Endpoint).where(Endpoint.id.in_(endpoint_ids)))
        db.execute(delete(Target).where(Target.id.in_(target_ids)))
        db.commit()


def infer(endpoint_id: int, body: dict | None = None):
    return client.post(
        f"/api/endpoints/{endpoint_id}/resource-bindings/"
        "infer-openapi-candidates",
        json={} if body is None else body,
    )


def test_no_migration_and_explicit_exact_endpoint_boundary() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    assert scripts.get_heads() == ["e2a4c6e8b0d3"]
    assert infer(999_999_999).status_code == 404
    for field in (
        "selector", "provenance", "confidence", "review_state", "target_id",
        "scope_id", "test_identity_id", "credential_binding_id",
        "test_case_id", "execution_plan_id", "plan_action_id", "test_run_id",
    ):
        assert infer(999_999_999, {field: "forbidden"}).status_code == 422


def test_ordinary_openapi_import_creates_zero_inferred_bindings(monkeypatch) -> None:
    target_ids = []
    try:
        with SessionLocal() as db:
            target = Target(
                name=f"openapi-no-auto-inference-{uuid4()}",
                base_url=f"https://{uuid4()}.example.test",
                environment="test",
                network_mode="private_local",
            )
            db.add(target)
            db.commit()
            target_ids = [target.id]

        class SyntheticScanner:
            def scan(self, **kwargs):
                return OpenAPIScanResult(
                    source_url=kwargs["source_url"],
                    document_sha256="a" * 64,
                    document_size_bytes=100,
                    content_encoding="identity",
                    decoded_document_sha256="a" * 64,
                    decoded_document_size_bytes=100,
                    endpoints=[ParsedEndpoint(
                        path="/orders/{order_id}",
                        method="GET",
                        operation_id="get_order",
                        requires_auth=False,
                        parameters=[{"name": "order_id", "in": "path"}],
                        request_body=None,
                        security=None,
                    )],
                )

        monkeypatch.setattr(openapi_routes, "scanner", SyntheticScanner())
        with SessionLocal() as db:
            result = openapi_routes.import_openapi(
                OpenAPIImportRequest(
                    target_id=target_ids[0],
                    source_url="https://example.test/openapi.json",
                ),
                db,
            )
            assert result.created == 1
        with SessionLocal() as db:
            endpoint_id = db.scalar(select(Endpoint.id).where(
                Endpoint.target_id == target_ids[0]
            ))
            assert db.scalar(select(func.count()).select_from(
                EndpointResourceBinding
            ).where(EndpointResourceBinding.endpoint_id == endpoint_id)) == 0
    finally:
        cleanup(target_ids)


def test_deterministic_signals_and_untrusted_metadata_filtering() -> None:
    target_ids = []
    parameters = [
        {"name": "search", "in": "query"},
        {"name": "account_id", "in": "query", "example": "never-store-me"},
        {"name": "order_id", "in": "path", "default": "never-store-me"},
        {"name": "id", "in": "query", "description": "never-store-me"},
        {"name": "userId", "in": "query"},
        {"name": "opaque", "in": "query", "schema": {"format": "uuid"}},
        {"name": "page", "in": "query"},
        {"name": "limit", "in": "query"},
        {"name": "sort", "in": "query"},
        {"name": "header_id", "in": "header"},
        {"name": "cookie_id", "in": "cookie"},
        {"name": "body_id", "in": "body"},
        {"name": "ghost_id", "in": "path"},
        {"name": "Authorization: Bearer actual-secret", "in": "query"},
        {"name": 123, "in": "query"},
        {"name": "bad_id", "in": 123},
        "malformed",
        None,
    ]
    try:
        target_id, endpoint_id = make_endpoint(parameters)
        target_ids = [target_id]
        with SessionLocal() as db:
            endpoint = db.get(Endpoint, endpoint_id)
            metadata = (
                deepcopy(endpoint.parameters), deepcopy(endpoint.request_body)
            )
        response = infer(endpoint_id)
        assert response.status_code == 200
        assert response.json() == {
            "endpoint_id": endpoint_id,
            "eligible_count": 5,
            "created_count": 5,
            "existing_inferred_count": 0,
            "skipped_operator_count": 0,
        }
        with SessionLocal() as db:
            rows = list(db.scalars(select(EndpointResourceBinding).where(
                EndpointResourceBinding.endpoint_id == endpoint_id
            ).order_by(EndpointResourceBinding.id)))
            endpoint = db.get(Endpoint, endpoint_id)
            assert (endpoint.parameters, endpoint.request_body) == metadata
        assert [
            (row.location, row.selector, row.provenance,
             row.confidence, row.review_state)
            for row in rows
        ] == [
            ("path", "order_id", "openapi_inferred", 60, "candidate"),
            ("query", "account_id", "openapi_inferred", 40, "candidate"),
            ("query", "id", "openapi_inferred", 40, "candidate"),
            ("query", "opaque", "openapi_inferred", 40, "candidate"),
            ("query", "userId", "openapi_inferred", 40, "candidate"),
        ]
        serialized = response.text + " ".join(row.selector for row in rows)
        assert "never-store-me" not in serialized
        assert "actual-secret" not in serialized
    finally:
        cleanup(target_ids)


def test_sensitive_identifier_names_are_not_inferred_for_path_or_query() -> None:
    target_ids = []
    parameters = [
        {"name": "credential_id", "in": "path"},
        {"name": "secret_id", "in": "query"},
        {"name": "apiKeyId", "in": "query"},
        {"name": "authorizationId", "in": "query"},
        {"name": "set_cookie_id", "in": "query"},
        {"name": "xapikey_id", "in": "query"},
        {"name": "accessTokenId", "in": "query"},
        {"name": "refresh-token-id", "in": "query"},
        {"name": "password_id", "in": "query"},
        {"name": "account_id", "in": "query"},
        {"name": "opaque", "in": "query", "schema": {"format": "uuid"}},
    ]
    try:
        target_id, endpoint_id = make_endpoint(
            parameters,
            path="/credentials/{credential_id}",
        )
        target_ids = [target_id]
        response = infer(endpoint_id)
        assert response.status_code == 200
        assert response.json()["eligible_count"] == 2
        with SessionLocal() as db:
            selectors = list(db.scalars(
                select(EndpointResourceBinding.selector).where(
                    EndpointResourceBinding.endpoint_id == endpoint_id
                ).order_by(EndpointResourceBinding.selector)
            ))
        assert selectors == ["account_id", "opaque"]
    finally:
        cleanup(target_ids)


def test_idempotency_review_preservation_and_operator_suppression() -> None:
    target_ids = []
    parameters = [
        {"name": "order_id", "in": "path"},
        {"name": "account_id", "in": "query"},
        {"name": "userId", "in": "query"},
    ]
    try:
        target_id, endpoint_id = make_endpoint(parameters)
        target_ids = [target_id]
        assert infer(endpoint_id).json()["created_count"] == 3
        with SessionLocal() as db:
            bindings = {
                row.selector: row for row in db.scalars(
                    select(EndpointResourceBinding).where(
                        EndpointResourceBinding.endpoint_id == endpoint_id
                    )
                )
            }
            bindings["order_id"].review_state = "confirmed"
            bindings["order_id"].confidence = 91
            bindings["account_id"].review_state = "rejected"
            bindings["account_id"].confidence = 7
            original_ids = {key: value.id for key, value in bindings.items()}
            db.commit()
        rerun = infer(endpoint_id)
        assert rerun.json()["created_count"] == 0
        assert rerun.json()["existing_inferred_count"] == 3
        with SessionLocal() as db:
            bindings = {
                row.selector: row for row in db.scalars(
                    select(EndpointResourceBinding).where(
                        EndpointResourceBinding.endpoint_id == endpoint_id
                    )
                )
            }
        assert {key: value.id for key, value in bindings.items()} == original_ids
        assert (bindings["order_id"].review_state,
                bindings["order_id"].confidence) == ("confirmed", 91)
        assert (bindings["account_id"].review_state,
                bindings["account_id"].confidence) == ("rejected", 7)
        assert (bindings["userId"].review_state,
                bindings["userId"].confidence) == ("candidate", 40)

        other_target, other_endpoint = make_endpoint([
            {"name": "account_id", "in": "query"}
        ], path="/accounts")
        target_ids.append(other_target)
        operator = client.post(
            f"/api/endpoints/{other_endpoint}/resource-bindings",
            json={
                "location": "query", "selector": "account_id",
                "confidence": 88, "review_state": "confirmed",
            },
        ).json()
        summary = infer(other_endpoint).json()
        assert summary["created_count"] == 0
        assert summary["skipped_operator_count"] == 1
        preserved = client.get(
            f"/api/endpoints/{other_endpoint}/resource-bindings/{operator['id']}"
        ).json()
        assert preserved == operator
    finally:
        cleanup(target_ids)


def test_inference_bounds_fail_closed_without_partial_rows() -> None:
    target_ids = []
    try:
        too_many_target, too_many_endpoint = make_endpoint([
            {"name": f"value_{index}", "in": "query"}
            for index in range(129)
        ])
        target_ids.append(too_many_target)
        response = infer(too_many_endpoint)
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "openapi_binding_parameter_limit_exceeded"
        )

        creation_target, creation_endpoint = make_endpoint([
            {"name": f"resource_{index}_id", "in": "query"}
            for index in range(65)
        ])
        target_ids.append(creation_target)
        response = infer(creation_endpoint)
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "openapi_binding_creation_limit_exceeded"
        )
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                EndpointResourceBinding
            ).where(EndpointResourceBinding.endpoint_id.in_([
                too_many_endpoint, creation_endpoint
            ]))) == 0
    finally:
        cleanup(target_ids)


def test_concurrent_inference_is_idempotent_and_preserves_review() -> None:
    target_ids = []
    try:
        target_id, endpoint_id = make_endpoint([
            {"name": "order_id", "in": "path"},
            {"name": "account_id", "in": "query"},
        ])
        target_ids = [target_id]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: infer(endpoint_id), range(2)))
        assert all(response.status_code == 200 for response in responses)
        assert sorted(response.json()["created_count"] for response in responses) == [0, 2]
        with SessionLocal() as db:
            rows = list(db.scalars(select(EndpointResourceBinding).where(
                EndpointResourceBinding.endpoint_id == endpoint_id
            )))
            assert len(rows) == 2
            rows[0].review_state = "confirmed"
            rows[0].confidence = 99
            reviewed_id = rows[0].id
            db.commit()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: infer(endpoint_id), range(2)))
        assert all(response.json()["created_count"] == 0 for response in responses)
        with SessionLocal() as db:
            reviewed = db.get(EndpointResourceBinding, reviewed_id)
            assert (reviewed.review_state, reviewed.confidence) == (
                "confirmed", 99
            )
    finally:
        cleanup(target_ids)


def test_inference_has_zero_authority_network_and_execution_side_effects(
    monkeypatch,
) -> None:
    target_ids = []
    tracked = (
        Target, Scope, StoredTestIdentity, CredentialBinding, Resource,
        StoredTestCase, ExecutionPlan, PlanAction, StoredTestRun,
    )
    try:
        target_id, endpoint_id = make_endpoint([
            {"name": "order_id", "in": "path"},
            {"name": "account_id", "in": "query"},
        ], network_mode="external_public_authorized")
        target_ids = [target_id]
        with SessionLocal() as db:
            before = {
                model: db.scalar(select(func.count()).select_from(model))
                for model in tracked
            }
            network_modes = list(db.execute(
                select(Target.id, Target.network_mode).order_by(Target.id)
            ))
        allowed_hosts = settings.allowed_target_hosts
        allowed_host_set = settings.allowed_target_host_set

        def prohibited(*args, **kwargs):
            raise AssertionError("network, OpenAPI retrieval, or execution invoked")

        monkeypatch.setattr("socket.getaddrinfo", prohibited)
        monkeypatch.setattr("socket.create_connection", prohibited)
        monkeypatch.setattr(
            "app.network_safety.gateway.NetworkGateway.request", prohibited
        )
        monkeypatch.setattr("httpcore.ConnectionPool.stream", prohibited)
        response = infer(endpoint_id)
        assert response.status_code == 200
        with SessionLocal() as db:
            after = {
                model: db.scalar(select(func.count()).select_from(model))
                for model in tracked
            }
            assert list(db.execute(
                select(Target.id, Target.network_mode).order_by(Target.id)
            )) == network_modes
        assert after == before
        assert settings.allowed_target_hosts == allowed_hosts
        assert settings.allowed_target_host_set == allowed_host_set
    finally:
        cleanup(target_ids)
