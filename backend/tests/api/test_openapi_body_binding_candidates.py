from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, func, select

from app.api.routes import openapi as openapi_routes
from app.core.config import settings
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
from app.db.session import SessionLocal, get_db
from app.main import app
from app.scanners.openapi import OpenAPIScanResult, ParsedEndpoint
from app.schemas.openapi import OpenAPIImportRequest


client = TestClient(app)


def request_body(schema: object) -> dict:
    return {"content": {"application/json": {"schema": schema}}}


def make_endpoint(
    schema: object,
    *,
    parameters: list | None = None,
    network_mode: str = "private_local",
) -> tuple[int, int]:
    with SessionLocal() as db:
        target = Target(
            name=f"body-binding-{uuid4()}",
            base_url=f"https://{uuid4()}.example.test",
            environment="test",
            network_mode=network_mode,
        )
        db.add(target)
        db.flush()
        endpoint = Endpoint(
            target_id=target.id,
            path="/orders/{order_id}",
            method="POST",
            operation_id="create_order",
            requires_auth=True,
            parameters=parameters or [],
            request_body=request_body(schema),
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


def infer_body(endpoint_id: int, body: dict | None = None):
    return client.post(
        f"/api/endpoints/{endpoint_id}/resource-bindings/"
        "infer-openapi-body-candidates",
        json={} if body is None else body,
    )


def infer_parameters(endpoint_id: int):
    return client.post(
        f"/api/endpoints/{endpoint_id}/resource-bindings/"
        "infer-openapi-candidates",
        json={},
    )


def test_no_migration_exact_endpoint_empty_body_and_m11_02_unchanged() -> None:
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == [
        "a4c6e8b0d2f3"
    ]
    assert infer_body(999_999_999).status_code == 404
    for field in (
        "selector", "provenance", "confidence", "review_state", "target_id",
        "scope_id", "credential_binding_id", "execution_plan_id",
    ):
        assert infer_body(999_999_999, {field: "forbidden"}).status_code == 422

    target_ids = []
    try:
        target_id, endpoint_id = make_endpoint(
            {"type": "object", "properties": {"id": {"type": "string"}}},
            parameters=[
                {"name": "order_id", "in": "path"},
                {"name": "account_id", "in": "query"},
            ],
        )
        target_ids = [target_id]
        assert infer_parameters(endpoint_id).json()["created_count"] == 2
        assert infer_body(endpoint_id).json()["created_count"] == 1
        with SessionLocal() as db:
            rows = list(db.scalars(select(EndpointResourceBinding).where(
                EndpointResourceBinding.endpoint_id == endpoint_id
            ).order_by(EndpointResourceBinding.id)))
        assert [(row.location, row.confidence) for row in rows] == [
            ("path", 60), ("query", 40), ("body", 30)
        ]
    finally:
        cleanup(target_ids)


def test_nested_leaf_signals_metadata_omission_and_pointer_escaping() -> None:
    target_ids = []
    schema = {
        "type": "object",
        "properties": {
            "order": {
                "type": "object",
                "properties": {
                    "customer": {
                        "properties": {
                            "id": {
                                "type": "string", "example": "never-store-me"
                            },
                            "account_id": {
                                "type": "string", "default": "never-store-me"
                            },
                            "userId": {
                                "type": "string", "enum": ["never-store-me"]
                            },
                            "opaque": {
                                "type": "string", "format": "uuid",
                                "description": "never-store-me",
                            },
                            "name": {"type": "string"},
                            "status": {"type": "string"},
                            "description": {"type": "string"},
                            "page": {"type": "integer"},
                            "sort": {"type": "string"},
                            "customer/account_id": {"type": "string"},
                            "customer~account_id": {"type": "string"},
                        },
                    },
                    "order_id": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    },
                },
            },
        },
    }
    try:
        target_id, endpoint_id = make_endpoint(schema)
        target_ids = [target_id]
        with SessionLocal() as db:
            endpoint = db.get(Endpoint, endpoint_id)
            metadata = (
                deepcopy(endpoint.parameters), deepcopy(endpoint.request_body)
            )
        response = infer_body(endpoint_id)
        assert response.status_code == 200
        assert response.json()["eligible_count"] == 6
        assert response.json()["created_count"] == 6
        with SessionLocal() as db:
            endpoint = db.get(Endpoint, endpoint_id)
            rows = list(db.scalars(select(EndpointResourceBinding).where(
                EndpointResourceBinding.endpoint_id == endpoint_id
            ).order_by(EndpointResourceBinding.id)))
            assert (endpoint.parameters, endpoint.request_body) == metadata
        assert [row.selector for row in rows] == [
            "/order/customer/account_id",
            "/order/customer/customer~0account_id",
            "/order/customer/customer~1account_id",
            "/order/customer/id",
            "/order/customer/opaque",
            "/order/customer/userId",
        ]
        assert all(
            (row.location, row.provenance, row.review_state, row.confidence)
            == ("body", "openapi_inferred", "candidate", 30)
            for row in rows
        )
        serialized = response.text + " ".join(row.selector for row in rows)
        assert "never-store-me" not in serialized
    finally:
        cleanup(target_ids)


@pytest.mark.parametrize("schema", (
    {"$ref": "#/components/schemas/Order"},
    {"allOf": [{"type": "object"}]},
    {"oneOf": [{"type": "object"}]},
    {"anyOf": [{"type": "object"}]},
    {"not": {"type": "object"}},
    {
        "properties": {
            "safe_id": {"type": "string"},
            "nested": {"properties": {"value": {"$ref": "#/x"}}},
        }
    },
))
def test_unsupported_reference_or_composition_fails_closed(schema) -> None:
    target_ids = []
    try:
        target_id, endpoint_id = make_endpoint(schema)
        target_ids = [target_id]
        response = infer_body(endpoint_id)
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "openapi_body_schema_unsupported_construct"
        )
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                EndpointResourceBinding
            ).where(EndpointResourceBinding.endpoint_id == endpoint_id)) == 0
    finally:
        cleanup(target_ids)


def test_arrays_malformed_and_sensitive_properties_never_become_candidates() -> None:
    target_ids = []
    schema = {
        "properties": {
            "items": {
                "type": "array",
                "items": {"properties": {"id": {"type": "string"}}},
            },
            "malformed_id": "not-a-schema",
            "Authorization: Bearer actual-secret-token": {
                "type": "string", "format": "uuid"
            },
            "x-api-key=actual-secret-token": {
                "type": "string", "format": "uuid"
            },
            "safe": {"type": "string"},
        }
    }
    try:
        target_id, endpoint_id = make_endpoint(schema)
        target_ids = [target_id]
        response = infer_body(endpoint_id)
        assert response.status_code == 200
        assert response.json()["eligible_count"] == 0
        assert "actual-secret-token" not in response.text
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                EndpointResourceBinding
            ).where(EndpointResourceBinding.endpoint_id == endpoint_id)) == 0
    finally:
        cleanup(target_ids)


def test_bare_sensitive_identifier_names_are_not_inferred() -> None:
    target_ids = []
    schema = {
        "properties": {
            "credential_id": {"type": "string"},
            "api_key_id": {"type": "string"},
            "secret_id": {"type": "string"},
            "password": {"type": "string", "format": "uuid"},
            "access_token": {"type": "string", "format": "uuid"},
            "refresh_token": {"type": "string", "format": "uuid"},
            "account_id": {"type": "string"},
            "userId": {"type": "string"},
            "order_id": {"type": "string"},
            "opaque": {"type": "string", "format": "uuid"},
        }
    }
    try:
        target_id, endpoint_id = make_endpoint(schema)
        target_ids = [target_id]
        response = infer_body(endpoint_id)
        assert response.status_code == 200
        assert response.json()["eligible_count"] == 4
        with SessionLocal() as db:
            selectors = list(db.scalars(
                select(EndpointResourceBinding.selector).where(
                    EndpointResourceBinding.endpoint_id == endpoint_id
                ).order_by(EndpointResourceBinding.selector)
            ))
        assert selectors == ["/account_id", "/opaque", "/order_id", "/userId"]
    finally:
        cleanup(target_ids)


def test_contradictory_object_metadata_fails_closed() -> None:
    target_ids = []
    try:
        target_id, endpoint_id = make_endpoint({
            "type": "string",
            "properties": {"id": {"type": "string"}},
        })
        target_ids = [target_id]
        response = infer_body(endpoint_id)
        assert response.status_code == 409
        assert response.json()["detail"] == "openapi_body_schema_malformed"
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                EndpointResourceBinding
            ).where(EndpointResourceBinding.endpoint_id == endpoint_id)) == 0
    finally:
        cleanup(target_ids)


@pytest.mark.parametrize(("schema", "code"), (
    (
        {"properties": {f"field_{index}": {} for index in range(129)}},
        "openapi_body_schema_property_limit_exceeded",
    ),
    (
        {"properties": {f"resource_{index}_id": {"type": "string"}
                        for index in range(65)}},
        "openapi_body_binding_creation_limit_exceeded",
    ),
))
def test_property_and_candidate_bounds_fail_without_partial_rows(schema, code) -> None:
    target_ids = []
    try:
        target_id, endpoint_id = make_endpoint(schema)
        target_ids = [target_id]
        response = infer_body(endpoint_id)
        assert response.status_code == 409
        assert response.json()["detail"] == code
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                EndpointResourceBinding
            ).where(EndpointResourceBinding.endpoint_id == endpoint_id)) == 0
    finally:
        cleanup(target_ids)


def test_depth_and_node_bounds_fail_without_partial_rows() -> None:
    target_ids = []
    depth_schema: dict = {"type": "string"}
    for index in range(17):
        depth_schema = {"properties": {f"level_{index}": depth_schema}}
    node_schema = {"properties": {
        f"branch_{branch}": {"properties": {
            f"leaf_{leaf}": {"type": "string"} for leaf in range(128)
        }} for branch in range(8)
    }}
    try:
        depth_target, depth_endpoint = make_endpoint(depth_schema)
        node_target, node_endpoint = make_endpoint(node_schema)
        target_ids = [depth_target, node_target]
        depth_response = infer_body(depth_endpoint)
        node_response = infer_body(node_endpoint)
        assert depth_response.status_code == 409
        assert depth_response.json()["detail"] == (
            "openapi_body_schema_depth_limit_exceeded"
        )
        assert node_response.status_code == 409
        assert node_response.json()["detail"] == (
            "openapi_body_schema_node_limit_exceeded"
        )
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                EndpointResourceBinding
            ).where(EndpointResourceBinding.endpoint_id.in_([
                depth_endpoint, node_endpoint
            ]))) == 0
    finally:
        cleanup(target_ids)


def test_idempotency_review_preservation_operator_suppression_and_concurrency() -> None:
    target_ids = []
    schema = {"properties": {
        "account_id": {"type": "string"},
        "userId": {"type": "string"},
    }}
    try:
        target_id, endpoint_id = make_endpoint(schema)
        target_ids = [target_id]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: infer_body(endpoint_id), range(2)))
        assert all(response.status_code == 200 for response in responses)
        assert sorted(response.json()["created_count"] for response in responses) == [0, 2]
        with SessionLocal() as db:
            rows = {row.selector: row for row in db.scalars(
                select(EndpointResourceBinding).where(
                    EndpointResourceBinding.endpoint_id == endpoint_id
                )
            )}
            rows["/account_id"].review_state = "confirmed"
            rows["/account_id"].confidence = 93
            rows["/userId"].review_state = "rejected"
            rows["/userId"].confidence = 4
            ids = {key: value.id for key, value in rows.items()}
            db.commit()
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: infer_body(endpoint_id), range(2)))
        assert all(response.json()["created_count"] == 0 for response in responses)
        with SessionLocal() as db:
            rows = {row.selector: row for row in db.scalars(
                select(EndpointResourceBinding).where(
                    EndpointResourceBinding.endpoint_id == endpoint_id
                )
            )}
        assert {key: value.id for key, value in rows.items()} == ids
        assert (rows["/account_id"].review_state,
                rows["/account_id"].confidence) == ("confirmed", 93)
        assert (rows["/userId"].review_state,
                rows["/userId"].confidence) == ("rejected", 4)

        other_target, other_endpoint = make_endpoint({
            "properties": {"account_id": {"type": "string"}}
        })
        target_ids.append(other_target)
        operator = client.post(
            f"/api/endpoints/{other_endpoint}/resource-bindings",
            json={
                "location": "body", "selector": "/account_id",
                "confidence": 88, "review_state": "confirmed",
            },
        ).json()
        summary = infer_body(other_endpoint).json()
        assert summary["created_count"] == 0
        assert summary["skipped_operator_count"] == 1
        assert client.get(
            f"/api/endpoints/{other_endpoint}/resource-bindings/{operator['id']}"
        ).json() == operator
    finally:
        cleanup(target_ids)


def test_persistence_failure_rolls_back_all_body_candidates(monkeypatch) -> None:
    target_ids = []
    db = None
    try:
        target_id, endpoint_id = make_endpoint({"properties": {
            "account_id": {"type": "string"},
            "userId": {"type": "string"},
        }})
        target_ids = [target_id]
        db = SessionLocal()
        real_rollback = db.rollback
        rolled_back = False

        def fail_commit():
            raise RuntimeError("synthetic body inference persistence failure")

        def track_rollback():
            nonlocal rolled_back
            rolled_back = True
            real_rollback()

        def override_db():
            yield db

        monkeypatch.setattr(db, "commit", fail_commit)
        monkeypatch.setattr(db, "rollback", track_rollback)
        app.dependency_overrides[get_db] = override_db
        with pytest.raises(RuntimeError, match="synthetic body inference"):
            infer_body(endpoint_id)
        assert rolled_back is True
        with SessionLocal() as verification_db:
            assert verification_db.scalar(select(func.count()).select_from(
                EndpointResourceBinding
            ).where(EndpointResourceBinding.endpoint_id == endpoint_id)) == 0
    finally:
        app.dependency_overrides.pop(get_db, None)
        if db is not None:
            db.close()
        cleanup(target_ids)


def test_body_inference_has_zero_authority_network_and_execution_side_effects(
    monkeypatch,
) -> None:
    target_ids = []
    tracked = (
        Target, Scope, StoredTestIdentity, CredentialBinding, Resource,
        StoredTestCase, ExecutionPlan, PlanAction, StoredTestRun,
    )
    try:
        target_id, endpoint_id = make_endpoint(
            {"properties": {"id": {"type": "string"}}},
            network_mode="external_public_authorized",
        )
        target_ids = [target_id]
        with SessionLocal() as db:
            before = {model: db.scalar(select(func.count()).select_from(model))
                      for model in tracked}
            modes = list(db.execute(
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
        assert infer_body(endpoint_id).status_code == 200
        with SessionLocal() as db:
            after = {model: db.scalar(select(func.count()).select_from(model))
                     for model in tracked}
            assert list(db.execute(
                select(Target.id, Target.network_mode).order_by(Target.id)
            )) == modes
        assert after == before
        assert settings.allowed_target_hosts == allowed_hosts
        assert settings.allowed_target_host_set == allowed_host_set
    finally:
        cleanup(target_ids)


def test_openapi_import_does_not_auto_infer_body_bindings(monkeypatch) -> None:
    target_ids = []
    try:
        with SessionLocal() as db:
            target = Target(
                name=f"body-import-no-inference-{uuid4()}",
                base_url=f"https://{uuid4()}.example.test",
                environment="test", network_mode="private_local",
            )
            db.add(target)
            db.commit()
            target_ids = [target.id]

        class SyntheticScanner:
            def scan(self, **kwargs):
                return OpenAPIScanResult(
                    source_url=kwargs["source_url"],
                    document_sha256="b" * 64,
                    document_size_bytes=100,
                    content_encoding="identity",
                    decoded_document_sha256="b" * 64,
                    decoded_document_size_bytes=100,
                    endpoints=[ParsedEndpoint(
                        path="/orders", method="POST",
                        operation_id="create_order", requires_auth=False,
                        parameters=[],
                        request_body=request_body({
                            "properties": {"id": {"type": "string"}}
                        }),
                        security=None,
                    )],
                )

        monkeypatch.setattr(openapi_routes, "scanner", SyntheticScanner())
        with SessionLocal() as db:
            result = openapi_routes.import_openapi(
                OpenAPIImportRequest(
                    target_id=target_ids[0],
                    source_url="https://example.test/openapi.json",
                ), db,
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
