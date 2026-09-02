from copy import deepcopy
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, func, select

from app.core.config import settings
from app.db.models import (
    CredentialBinding,
    Endpoint,
    EndpointResourceBinding,
    ExecutionPlan,
    PlanAction,
    Scope,
    Target,
    TestCase as StoredTestCase,
    TestIdentity as StoredTestIdentity,
    TestRun as StoredTestRun,
)
from app.db.session import SessionLocal
from app.db.session import get_db
from app.main import app


client = TestClient(app)


def make_endpoint(*, path: str = "/orders/{order_id}") -> tuple[int, int]:
    with SessionLocal() as db:
        target = Target(
            name=f"binding-target-{uuid4()}",
            base_url=f"https://{uuid4()}.example.test",
            environment="test",
            network_mode="private_local",
        )
        db.add(target)
        db.flush()
        endpoint = Endpoint(
            target_id=target.id,
            path=path,
            method="GET",
            operation_id="get_order",
            requires_auth=True,
            parameters=[
                {"name": "order_id", "in": "path", "required": True},
                {"name": "account_id", "in": "query", "required": False},
            ],
            request_body={"content": {"application/json": {"schema": {}}}},
            security=[{"BearerAuth": []}],
        )
        db.add(endpoint)
        db.commit()
        return target.id, endpoint.id


def cleanup(target_ids: list[int]) -> None:
    with SessionLocal() as db:
        endpoint_ids = list(db.scalars(
            select(Endpoint.id).where(Endpoint.target_id.in_(target_ids))
        ))
        if endpoint_ids:
            db.execute(delete(EndpointResourceBinding).where(
                EndpointResourceBinding.endpoint_id.in_(endpoint_ids)
            ))
            db.execute(delete(Endpoint).where(Endpoint.id.in_(endpoint_ids)))
        db.execute(delete(Target).where(Target.id.in_(target_ids)))
        db.commit()


def post(endpoint_id: int, **overrides):
    body = {
        "location": "path",
        "selector": "order_id",
        "confidence": 50,
        "review_state": "candidate",
    }
    body.update(overrides)
    return client.post(
        f"/api/endpoints/{endpoint_id}/resource-bindings", json=body
    )


def test_path_query_body_and_multiple_bindings_are_metadata_only(monkeypatch) -> None:
    target_ids = []
    tracked = (
        Target, Scope, StoredTestIdentity, CredentialBinding, StoredTestCase,
        ExecutionPlan, PlanAction, StoredTestRun,
    )
    try:
        target_id, endpoint_id = make_endpoint()
        target_ids = [target_id]
        with SessionLocal() as db:
            endpoint = db.get(Endpoint, endpoint_id)
            metadata = (deepcopy(endpoint.parameters), deepcopy(endpoint.request_body))
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
            raise AssertionError("network or execution boundary invoked")

        monkeypatch.setattr("socket.getaddrinfo", prohibited)
        monkeypatch.setattr("socket.create_connection", prohibited)
        monkeypatch.setattr(
            "app.network_safety.gateway.NetworkGateway.request", prohibited
        )
        monkeypatch.setattr("httpcore.ConnectionPool.stream", prohibited)

        path = post(endpoint_id, confidence=100, review_state="candidate")
        query = post(
            endpoint_id, location="query", selector="account_id",
            confidence=10, review_state="confirmed",
        )
        body = post(
            endpoint_id, location="body", selector="/order/customer/id",
            confidence=0, review_state="rejected",
        )
        assert [item.status_code for item in (path, query, body)] == [201, 201, 201]
        assert path.json()["review_state"] == "candidate"
        assert query.json()["review_state"] == "confirmed"
        assert {item.json()["provenance"] for item in (path, query, body)} == {
            "operator_supplied"
        }

        with SessionLocal() as db:
            endpoint = db.get(Endpoint, endpoint_id)
            assert (endpoint.parameters, endpoint.request_body) == metadata
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


@pytest.mark.parametrize(("location", "selector"), (
    ("path", ""),
    ("path", "https://example.test/id"),
    ("path", "orders/id"),
    ("query", "id?admin=true"),
    ("query", "not declared"),
    ("body", ""),
    ("body", "/"),
    ("body", "$.order.id"),
    ("body", "/order/~2id"),
    ("body", "/order/id~"),
    ("body", "/order//id"),
))
def test_malformed_selectors_fail_without_persistence(location, selector) -> None:
    target_ids = []
    try:
        target_id, endpoint_id = make_endpoint()
        target_ids = [target_id]
        response = post(endpoint_id, location=location, selector=selector)
        assert response.status_code == 422
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                EndpointResourceBinding
            ).where(EndpointResourceBinding.endpoint_id == endpoint_id)) == 0
    finally:
        cleanup(target_ids)


@pytest.mark.parametrize(("location", "selector"), (
    ("path", "account_id"),
    ("query", "order_id"),
    ("query", "missing_id"),
))
def test_path_query_binding_must_match_exact_endpoint_metadata(location, selector) -> None:
    target_ids = []
    try:
        target_id, endpoint_id = make_endpoint()
        target_ids = [target_id]
        response = post(endpoint_id, location=location, selector=selector)
        assert response.status_code == 409
        assert response.json()["detail"] == "resource_binding_selector_not_declared"
    finally:
        cleanup(target_ids)


def test_input_boundary_confidence_review_and_provenance() -> None:
    target_ids = []
    try:
        target_id, endpoint_id = make_endpoint()
        target_ids = [target_id]
        for confidence in (0, 100):
            response = post(
                endpoint_id, selector=f"/id{confidence}", location="body",
                confidence=confidence, review_state="candidate",
            )
            assert response.status_code == 201
            assert response.json()["confidence"] == confidence
        for invalid in (-1, 101, 1.5, True):
            assert post(endpoint_id, location="body", selector="/invalid",
                        confidence=invalid).status_code == 422
        missing_review = {
            "location": "body", "selector": "/missing", "confidence": 50,
        }
        assert client.post(
            f"/api/endpoints/{endpoint_id}/resource-bindings", json=missing_review
        ).status_code == 422
        for value in ("openapi_inferred", "heuristic_inferred"):
            assert post(endpoint_id, location="body", selector="/injected",
                        provenance=value).status_code == 422
        for field in (
            "target_id", "scope_id", "credential_binding_id", "authorization",
            "cookie", "api_key", "resource_value", "request_body", "evidence",
            "test_case_id", "execution_plan_id",
        ):
            assert post(endpoint_id, location="body", selector=f"/{field}",
                        **{field: "forbidden"}).status_code == 422
    finally:
        cleanup(target_ids)


def test_bounded_pagination_order_exact_ownership_and_duplicate_rollback() -> None:
    target_ids = []
    try:
        first_target, first_endpoint = make_endpoint()
        second_target, second_endpoint = make_endpoint(path="/other/{order_id}")
        target_ids = [first_target, second_target]
        created_ids = []
        for index in range(3):
            response = post(
                first_endpoint, location="body", selector=f"/item/{index}"
            )
            assert response.status_code == 201
            created_ids.append(response.json()["id"])
        page = client.get(
            f"/api/endpoints/{first_endpoint}/resource-bindings",
            params={"limit": 2, "offset": 1},
        )
        assert [item["id"] for item in page.json()] == created_ids[1:]
        assert client.get(
            f"/api/endpoints/{first_endpoint}/resource-bindings",
            params={"limit": 101},
        ).status_code == 422
        assert client.get(
            f"/api/endpoints/{second_endpoint}/resource-bindings/{created_ids[0]}"
        ).status_code == 404
        assert client.get(
            f"/api/endpoints/{first_endpoint}/resource-bindings/{created_ids[0]}"
        ).status_code == 200
        duplicate = post(first_endpoint, location="body", selector="/item/0")
        assert duplicate.status_code == 409
        assert post(
            first_endpoint, location="body", selector="/after-rollback"
        ).status_code == 201
    finally:
        cleanup(target_ids)


def test_persistence_failure_rolls_back_completely(monkeypatch) -> None:
    target_ids = []
    db = None
    try:
        target_id, endpoint_id = make_endpoint()
        target_ids = [target_id]
        db = SessionLocal()
        rolled_back = False
        real_rollback = db.rollback

        def fail_commit():
            raise RuntimeError("synthetic persistence failure")

        def track_rollback():
            nonlocal rolled_back
            rolled_back = True
            real_rollback()

        def override_db():
            yield db

        monkeypatch.setattr(db, "commit", fail_commit)
        monkeypatch.setattr(db, "rollback", track_rollback)
        app.dependency_overrides[get_db] = override_db
        with pytest.raises(RuntimeError, match="synthetic persistence failure"):
            post(endpoint_id)
        assert rolled_back is True
        with SessionLocal() as verification_db:
            assert verification_db.scalar(
                select(func.count()).select_from(EndpointResourceBinding).where(
                    EndpointResourceBinding.endpoint_id == endpoint_id
                )
            ) == 0
    finally:
        app.dependency_overrides.pop(get_db, None)
        if db is not None:
            db.close()
        cleanup(target_ids)
