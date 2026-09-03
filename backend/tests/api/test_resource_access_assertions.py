from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.models import (
    CredentialBinding,
    EndpointResourceBinding,
    ExecutionPlan,
    PlanAction,
    Resource,
    ResourceAccessAssertion,
    Scope,
    Target,
    TestCase as StoredTestCase,
    TestIdentity,
    TestRun as StoredTestRun,
)
from app.db.session import SessionLocal, get_db
from app.main import app


client = TestClient(app)


def make_target_identity_resource(
    *, network_mode: str = "private_local"
) -> tuple[int, int, int]:
    with SessionLocal() as db:
        target = Target(
            name=f"assertion-{uuid4()}",
            base_url=f"https://{uuid4()}.example.test",
            environment="test",
            network_mode=network_mode,
        )
        db.add(target)
        db.flush()
        identity = TestIdentity(
            target_id=target.id,
            name="fixture identity",
            role="user",
            auth_type="bearer",
            credentials=None,
            is_active=True,
        )
        db.add(identity)
        db.flush()
        resource = Resource(
            target_id=target.id,
            resource_type="order",
            external_id=f"external-{uuid4()}",
            owner_identity_id=identity.id,
        )
        db.add(resource)
        db.commit()
        return target.id, identity.id, resource.id


def cleanup(target_ids: list[int]) -> None:
    if not target_ids:
        return
    with SessionLocal() as db:
        resource_ids = list(db.scalars(select(Resource.id).where(
            Resource.target_id.in_(target_ids)
        )))
        if resource_ids:
            db.execute(delete(ResourceAccessAssertion).where(
                ResourceAccessAssertion.resource_id.in_(resource_ids)
            ))
            db.execute(delete(Resource).where(Resource.id.in_(resource_ids)))
        db.execute(delete(TestIdentity).where(TestIdentity.target_id.in_(target_ids)))
        db.execute(delete(Target).where(Target.id.in_(target_ids)))
        db.commit()


def post_assertion(path_resource_id: int, referenced_identity_id: int, **overrides):
    payload = {
        "test_identity_id": referenced_identity_id,
        "relationship": "owner",
        "expected_access": "allowed",
        "confidence": 100,
    }
    payload.update(overrides)
    return client.post(
        f"/api/resources/{path_resource_id}/access-assertions", json=payload
    )


@pytest.mark.parametrize(("relationship", "expected_access"), (
    ("owner", "allowed"),
    ("non_owner", "denied"),
    ("non_owner", "allowed"),
    ("unspecified", "allowed"),
))
def test_human_assertion_dimensions_are_independent(
    relationship: str, expected_access: str
) -> None:
    target_ids = []
    try:
        target_id, identity_id, resource_id = make_target_identity_resource()
        target_ids = [target_id]
        before = datetime.now(timezone.utc)
        response = post_assertion(
            resource_id, identity_id,
            relationship=relationship,
            expected_access=expected_access,
            confidence=10,
        )
        after = datetime.now(timezone.utc)
        assert response.status_code == 201
        body = response.json()
        assert body["relationship"] == relationship
        assert body["expected_access"] == expected_access
        assert body["provenance"] == "human_verified"
        assert body["verification_state"] == "verified"
        assert body["confidence"] == 10
        assert body["observed_at"] is None
        assert before <= datetime.fromisoformat(body["asserted_at"]) <= after
        assert "external_id" not in body
    finally:
        cleanup(target_ids)


def test_validation_confidence_time_and_prohibited_fields() -> None:
    target_ids = []
    try:
        target_id, identity_id, resource_id = make_target_identity_resource()
        target_ids = [target_id]
        assert post_assertion(
            resource_id, identity_id,
            relationship="unspecified", expected_access="unspecified",
        ).status_code == 422
        for confidence in (-1, 101, 1.5, True, "10"):
            assert post_assertion(
                resource_id, identity_id, confidence=confidence
            ).status_code == 422
        valid_from = "2026-01-01T00:00:00+00:00"
        assert post_assertion(
            resource_id, identity_id, valid_from=valid_from,
            valid_until="2026-01-02T00:00:00+00:00",
        ).status_code == 201
        invalid_windows = (
            {"valid_from": "2026-01-01T00:00:00"},
            {"valid_until": "2026-01-02T00:00:00+00:00"},
            {"valid_from": valid_from, "valid_until": valid_from},
            {
                "valid_from": "2026-01-02T00:00:00+00:00",
                "valid_until": valid_from,
            },
        )
        for window in invalid_windows:
            assert post_assertion(resource_id, identity_id, **window).status_code == 422

        prohibited = {
            "provenance": "target_fixture",
            "verification_state": "candidate",
            "resource_id": resource_id,
            "target_id": target_id,
            "asserted_at": "2026-01-01T00:00:00Z",
            "observed_at": "2026-01-01T00:00:00Z",
            "external_id": "resource-secret-value",
            "Authorization": "Bearer actual-secret-token",
            "cookie": "session=actual-secret-token",
            "api_key": "actual-secret-token",
            "credential": "actual-secret-token",
            "test_case_id": 1,
            "execution_plan_id": 1,
            "metadata": {"evidence": "actual-secret-token"},
            "note": "actual-secret-token",
        }
        for field, value in prohibited.items():
            response = post_assertion(resource_id, identity_id, **{field: value})
            assert response.status_code == 422
            assert "actual-secret-token" not in response.text
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                ResourceAccessAssertion
            ).where(ResourceAccessAssertion.resource_id == resource_id)) == 1
    finally:
        cleanup(target_ids)


def test_exact_resource_identity_target_and_cross_target_boundaries() -> None:
    target_ids = []
    try:
        target_id, identity_id, resource_id = make_target_identity_resource()
        other_target, other_identity, other_resource = make_target_identity_resource()
        target_ids = [target_id, other_target]
        assert post_assertion(999_999_999, identity_id).status_code == 404
        assert post_assertion(resource_id, 999_999_999).status_code == 404
        assert post_assertion(resource_id, other_identity).status_code == 409
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                ResourceAccessAssertion
            ).where(ResourceAccessAssertion.resource_id.in_([
                resource_id, other_resource
            ]))) == 0
    finally:
        cleanup(target_ids)


def test_append_only_bounded_history_and_exact_get_ownership() -> None:
    target_ids = []
    try:
        target_id, identity_id, resource_id = make_target_identity_resource()
        other_target, other_identity, other_resource = make_target_identity_resource()
        target_ids = [target_id, other_target]
        first = post_assertion(resource_id, identity_id).json()
        second = post_assertion(resource_id, identity_id).json()
        third = post_assertion(resource_id, identity_id).json()
        assert first["id"] < second["id"] < third["id"]
        page = client.get(
            f"/api/resources/{resource_id}/access-assertions",
            params={"after_id": first["id"], "limit": 1},
        )
        assert [item["id"] for item in page.json()] == [second["id"]]
        assert client.get(
            f"/api/resources/{resource_id}/access-assertions",
            params={"limit": 101},
        ).status_code == 422
        assert client.get(
            f"/api/resources/{resource_id}/access-assertions/{first['id']}"
        ).status_code == 200
        assert client.get(
            f"/api/resources/{other_resource}/access-assertions/{first['id']}"
        ).status_code == 404
        assert client.get(
            "/api/resources/999999999/access-assertions"
        ).status_code == 404
        assert client.get(
            f"/api/resources/{resource_id}/access-assertions/999999999"
        ).status_code == 404
        assert client.patch(
            f"/api/resources/{resource_id}/access-assertions/{first['id']}",
            json={"confidence": 1},
        ).status_code == 405
        assert client.delete(
            f"/api/resources/{resource_id}/access-assertions/{first['id']}"
        ).status_code == 405
        assert not any(
            getattr(route, "path", "").endswith("/access-assertions")
            and "{resource_id}" not in route.path
            for route in app.routes
        )
    finally:
        cleanup(target_ids)


def test_persistence_failure_rolls_back(monkeypatch) -> None:
    target_ids = []
    db = None
    try:
        target_id, identity_id, resource_id = make_target_identity_resource()
        target_ids = [target_id]
        db = SessionLocal()
        real_rollback = db.rollback
        rolled_back = False

        def fail_commit():
            raise RuntimeError("synthetic assertion persistence failure")

        def track_rollback():
            nonlocal rolled_back
            rolled_back = True
            real_rollback()

        def override_db():
            yield db

        monkeypatch.setattr(db, "commit", fail_commit)
        monkeypatch.setattr(db, "rollback", track_rollback)
        app.dependency_overrides[get_db] = override_db
        with pytest.raises(RuntimeError, match="synthetic assertion"):
            post_assertion(resource_id, identity_id)
        assert rolled_back is True
        with SessionLocal() as verification_db:
            assert verification_db.scalar(select(func.count()).select_from(
                ResourceAccessAssertion
            ).where(ResourceAccessAssertion.resource_id == resource_id)) == 0
    finally:
        app.dependency_overrides.pop(get_db, None)
        if db is not None:
            db.close()
        cleanup(target_ids)


def test_assertions_restrict_deletion_and_leave_legacy_owner_unchanged() -> None:
    target_ids = []
    try:
        target_id, identity_id, resource_id = make_target_identity_resource()
        target_ids = [target_id]
        with SessionLocal() as db:
            asserted_identity = TestIdentity(
                target_id=target_id,
                name="asserted non-owner",
                role="user",
                auth_type="bearer",
                credentials=None,
                is_active=True,
            )
            db.add(asserted_identity)
            db.commit()
            asserted_identity_id = asserted_identity.id
        assertion = post_assertion(
            resource_id,
            asserted_identity_id,
            relationship="non_owner",
            expected_access="allowed",
        ).json()
        assert assertion["resource_id"] == resource_id
        with SessionLocal() as db:
            resource = db.get(Resource, resource_id)
            assert resource.owner_identity_id == identity_id
            with pytest.raises(IntegrityError):
                db.execute(delete(Resource).where(Resource.id == resource_id))
                db.commit()
            db.rollback()
            with pytest.raises(IntegrityError):
                db.execute(delete(TestIdentity).where(
                    TestIdentity.id == asserted_identity_id
                ))
                db.commit()
            db.rollback()
            assert db.get(Resource, resource_id).owner_identity_id == identity_id
        assert client.get(
            f"/api/resources/{resource_id}/access-assertions/{assertion['id']}"
        ).status_code == 200
    finally:
        cleanup(target_ids)


def test_legacy_resource_create_creates_no_assertion() -> None:
    target_ids = []
    try:
        target_id, identity_id, _ = make_target_identity_resource()
        target_ids = [target_id]
        response = client.post("/api/resources", json={
            "target_id": target_id,
            "resource_type": "invoice",
            "external_id": f"legacy-{uuid4()}",
            "owner_identity_id": identity_id,
        })
        assert response.status_code == 201
        assert response.json()["owner_identity_id"] == identity_id
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                ResourceAccessAssertion
            )) == 0
    finally:
        cleanup(target_ids)


def test_assertion_api_has_zero_authority_network_and_execution_side_effects(
    monkeypatch,
) -> None:
    target_ids = []
    tracked = (
        Target, Scope, TestIdentity, CredentialBinding, EndpointResourceBinding,
        StoredTestCase, ExecutionPlan, PlanAction, StoredTestRun,
    )
    try:
        target_id, identity_id, resource_id = make_target_identity_resource(
            network_mode="external_public_authorized"
        )
        target_ids = [target_id]
        with SessionLocal() as db:
            before = {model: db.scalar(select(func.count()).select_from(model))
                      for model in tracked}
            owner_id = db.get(Resource, resource_id).owner_identity_id
            modes = list(db.execute(
                select(Target.id, Target.network_mode).order_by(Target.id)
            ))
        allowed_hosts = settings.allowed_target_hosts
        allowed_host_set = settings.allowed_target_host_set

        def prohibited(*args, **kwargs):
            raise AssertionError("network or execution invoked")

        monkeypatch.setattr("socket.getaddrinfo", prohibited)
        monkeypatch.setattr("socket.create_connection", prohibited)
        monkeypatch.setattr(
            "app.network_safety.gateway.NetworkGateway.request", prohibited
        )
        monkeypatch.setattr("httpcore.ConnectionPool.stream", prohibited)
        response = post_assertion(resource_id, identity_id)
        assert response.status_code == 201
        assert client.get(
            f"/api/resources/{resource_id}/access-assertions"
        ).status_code == 200
        with SessionLocal() as db:
            after = {model: db.scalar(select(func.count()).select_from(model))
                     for model in tracked}
            assert db.get(Resource, resource_id).owner_identity_id == owner_id
            assert list(db.execute(
                select(Target.id, Target.network_mode).order_by(Target.id)
            )) == modes
        assert before == after
        assert settings.allowed_target_hosts == allowed_hosts
        assert settings.allowed_target_host_set == allowed_host_set
    finally:
        cleanup(target_ids)
