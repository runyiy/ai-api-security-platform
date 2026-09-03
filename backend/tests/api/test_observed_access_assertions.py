from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
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
OBSERVED_AT = datetime(2026, 2, 3, 4, 5, 6, tzinfo=timezone.utc)


def make_source_run(
    *,
    test_type: str = "owner_baseline",
    response_status: int | None = 200,
    expected_statuses: list[int] | None = None,
    error_message: str | None = None,
    network_mode: str = "private_local",
) -> dict[str, int]:
    with SessionLocal() as db:
        target = Target(
            name=f"observed-{uuid4()}",
            base_url=f"https://{uuid4()}.example.test",
            environment="test",
            network_mode=network_mode,
        )
        db.add(target)
        db.flush()
        identity = TestIdentity(
            target_id=target.id,
            name="baseline actor",
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
            external_id="external-sensitive-resource-value",
            owner_identity_id=identity.id,
        )
        endpoint = Endpoint(
            target_id=target.id,
            path="/orders/{id}",
            method="GET",
            operation_id="get_order",
            requires_auth=True,
            parameters=[],
            request_body=None,
            security=None,
        )
        db.add_all([resource, endpoint])
        db.flush()
        test_case = StoredTestCase(
            endpoint_id=endpoint.id,
            actor_identity_id=identity.id,
            resource_id=resource.id,
            test_type=test_type,
            ownership_relation="owner",
            expected_statuses=(
                expected_statuses if expected_statuses is not None else [200]
            ),
            status="completed",
        )
        db.add(test_case)
        db.flush()
        run = StoredTestRun(
            test_case_id=test_case.id,
            request_data={"Authorization": "Bearer request-secret"},
            response_status=response_status,
            response_body="response-secret-body",
            duration_ms=1,
            error_message=error_message,
            executed_at=OBSERVED_AT,
        )
        db.add(run)
        db.commit()
        return {
            "target": target.id,
            "identity": identity.id,
            "resource": resource.id,
            "endpoint": endpoint.id,
            "case": test_case.id,
            "run": run.id,
        }


def cleanup(target_ids: list[int]) -> None:
    if not target_ids:
        return
    with SessionLocal() as db:
        resource_ids = list(db.scalars(select(Resource.id).where(
            Resource.target_id.in_(target_ids)
        )))
        endpoint_ids = list(db.scalars(select(Endpoint.id).where(
            Endpoint.target_id.in_(target_ids)
        )))
        case_ids = list(db.scalars(select(StoredTestCase.id).where(
            StoredTestCase.endpoint_id.in_(endpoint_ids)
        ))) if endpoint_ids else []
        if resource_ids:
            db.execute(delete(ResourceAccessAssertion).where(
                ResourceAccessAssertion.resource_id.in_(resource_ids)
            ))
        if case_ids:
            db.execute(delete(StoredTestRun).where(
                StoredTestRun.test_case_id.in_(case_ids)
            ))
            db.execute(delete(StoredTestCase).where(
                StoredTestCase.id.in_(case_ids)
            ))
        if endpoint_ids:
            db.execute(delete(Endpoint).where(Endpoint.id.in_(endpoint_ids)))
        if resource_ids:
            db.execute(delete(Resource).where(Resource.id.in_(resource_ids)))
        db.execute(delete(TestIdentity).where(TestIdentity.target_id.in_(target_ids)))
        db.execute(delete(Target).where(Target.id.in_(target_ids)))
        db.commit()


def derive(run_id: int, body: dict | None = None):
    return client.post(
        f"/api/test-runs/{run_id}/observed-access-assertion",
        json={} if body is None else body,
    )


@pytest.mark.parametrize(("status_code", "expected"), ((200, [200]), (204, [204])))
def test_eligible_owner_baseline_derives_exact_candidate(
    status_code: int, expected: list[int]
) -> None:
    target_ids = []
    try:
        ids = make_source_run(
            response_status=status_code, expected_statuses=expected
        )
        target_ids = [ids["target"]]
        before = datetime.now(timezone.utc)
        response = derive(ids["run"])
        after = datetime.now(timezone.utc)
        assert response.status_code == 201
        body = response.json()
        assert body["resource_id"] == ids["resource"]
        assert body["test_identity_id"] == ids["identity"]
        assert body["relationship"] == "unspecified"
        assert body["expected_access"] == "allowed"
        assert body["provenance"] == "observed_baseline"
        assert body["confidence"] == 50
        assert body["verification_state"] == "candidate"
        assert datetime.fromisoformat(body["observed_at"]) == OBSERVED_AT
        assert body["source_test_run_id"] == ids["run"]
        assert body["valid_from"] is None
        assert body["valid_until"] is None
        assert before <= datetime.fromisoformat(body["asserted_at"]) <= after
        serialized = response.text
        assert "request-secret" not in serialized
        assert "response-secret" not in serialized
        assert "external-sensitive" not in serialized
    finally:
        cleanup(target_ids)


@pytest.mark.parametrize(("test_type", "response_status", "expected", "error"), (
    ("bola_cross_owner", 200, [200], None),
    ("anonymous_access", 200, [200], None),
    ("owner_baseline", 199, [199], None),
    ("owner_baseline", 300, [300], None),
    ("owner_baseline", 201, [200], None),
    ("owner_baseline", 200, [200], "stored execution error secret"),
    ("owner_baseline", None, [200], None),
))
def test_ineligible_runs_fail_closed(
    test_type: str,
    response_status: int | None,
    expected: list[int],
    error: str | None,
) -> None:
    target_ids = []
    try:
        ids = make_source_run(
            test_type=test_type,
            response_status=response_status,
            expected_statuses=expected,
            error_message=error,
        )
        target_ids = [ids["target"]]
        response = derive(ids["run"])
        assert response.status_code == 409
        assert "stored execution error secret" not in response.text
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                ResourceAccessAssertion
            ).where(ResourceAccessAssertion.source_test_run_id == ids["run"])) == 0
    finally:
        cleanup(target_ids)


def test_missing_and_inconsistent_provenance_fail_closed() -> None:
    target_ids = []
    try:
        assert derive(999_999_999).status_code == 404
        ids = make_source_run()
        other = make_source_run()
        target_ids = [ids["target"], other["target"]]
        with SessionLocal() as db:
            endpoint = db.get(Endpoint, ids["endpoint"])
            endpoint.path = "/historically-inconsistent/{id}"
            endpoint.target_id = other["target"]
            db.commit()
        response = derive(ids["run"])
        assert response.status_code == 409
        assert response.json()["detail"] == "source_provenance_inconsistent"
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                ResourceAccessAssertion
            ).where(ResourceAccessAssertion.source_test_run_id == ids["run"])) == 0
    finally:
        cleanup(target_ids)


def test_empty_body_only_and_sensitive_fields_are_not_echoed() -> None:
    target_ids = []
    try:
        ids = make_source_run()
        target_ids = [ids["target"]]
        for field in (
            "resource_id", "test_identity_id", "relationship",
            "expected_access", "provenance", "confidence",
            "verification_state", "asserted_at", "observed_at", "valid_from",
            "valid_until", "source_test_run_id", "Authorization", "cookie",
            "api_key", "credentials", "request_data", "response_body",
            "evidence",
        ):
            response = derive(ids["run"], {field: "actual-secret-value"})
            assert response.status_code == 422
            assert "actual-secret-value" not in response.text
        assert derive(ids["run"]).status_code == 201
    finally:
        cleanup(target_ids)


def test_repeated_and_concurrent_derivation_converges_without_mutation() -> None:
    target_ids = []
    try:
        ids = make_source_run()
        target_ids = [ids["target"]]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: derive(ids["run"]), range(2)))
        assert all(response.status_code == 201 for response in responses)
        first = responses[0].json()
        assert responses[1].json() == first
        repeated = derive(ids["run"])
        assert repeated.json() == first
        with SessionLocal() as db:
            rows = list(db.scalars(select(ResourceAccessAssertion).where(
                ResourceAccessAssertion.source_test_run_id == ids["run"]
            )))
        assert len(rows) == 1
        assert rows[0].id == first["id"]
    finally:
        cleanup(target_ids)


def test_conflicting_existing_source_assertion_fails_without_mutation() -> None:
    target_ids = []
    try:
        ids = make_source_run()
        target_ids = [ids["target"]]
        with SessionLocal() as db:
            conflicting = ResourceAccessAssertion(
                resource_id=ids["resource"],
                test_identity_id=ids["identity"],
                relationship="owner",
                expected_access="allowed",
                provenance="human_verified",
                confidence=100,
                verification_state="verified",
                observed_at=None,
                valid_from=None,
                valid_until=None,
                source_test_run_id=ids["run"],
            )
            db.add(conflicting)
            db.commit()
            assertion_id = conflicting.id
        response = derive(ids["run"])
        assert response.status_code == 409
        assert response.json()["detail"] == "source_assertion_conflict"
        with SessionLocal() as db:
            assertion = db.get(ResourceAccessAssertion, assertion_id)
            assert (
                assertion.provenance,
                assertion.relationship,
                assertion.confidence,
                assertion.verification_state,
            ) == ("human_verified", "owner", 100, "verified")
    finally:
        cleanup(target_ids)


def test_human_assertions_remain_append_only_and_owner_unchanged() -> None:
    target_ids = []
    try:
        ids = make_source_run()
        target_ids = [ids["target"]]
        payload = {
            "test_identity_id": ids["identity"],
            "relationship": "owner",
            "expected_access": "allowed",
            "confidence": 100,
        }
        first = client.post(
            f"/api/resources/{ids['resource']}/access-assertions", json=payload
        ).json()
        second = client.post(
            f"/api/resources/{ids['resource']}/access-assertions", json=payload
        ).json()
        assert first["id"] != second["id"]
        assert first["source_test_run_id"] is None
        assert second["source_test_run_id"] is None
        assert derive(ids["run"]).status_code == 201
        with SessionLocal() as db:
            resource = db.get(Resource, ids["resource"])
            assert resource.owner_identity_id == ids["identity"]
            assert db.scalar(select(func.count()).select_from(
                ResourceAccessAssertion
            ).where(ResourceAccessAssertion.resource_id == ids["resource"])) == 3
    finally:
        cleanup(target_ids)


def test_persistence_failure_rolls_back(monkeypatch) -> None:
    target_ids = []
    db = None
    try:
        ids = make_source_run()
        target_ids = [ids["target"]]
        db = SessionLocal()
        real_rollback = db.rollback
        rolled_back = False

        def fail_commit():
            raise RuntimeError("synthetic observed assertion persistence failure")

        def track_rollback():
            nonlocal rolled_back
            rolled_back = True
            real_rollback()

        def override_db():
            yield db

        monkeypatch.setattr(db, "commit", fail_commit)
        monkeypatch.setattr(db, "rollback", track_rollback)
        app.dependency_overrides[get_db] = override_db
        with pytest.raises(RuntimeError, match="synthetic observed assertion"):
            derive(ids["run"])
        assert rolled_back is True
        with SessionLocal() as verification_db:
            assert verification_db.scalar(select(func.count()).select_from(
                ResourceAccessAssertion
            ).where(ResourceAccessAssertion.source_test_run_id == ids["run"])) == 0
    finally:
        app.dependency_overrides.pop(get_db, None)
        if db is not None:
            db.close()
        cleanup(target_ids)


def test_derivation_has_zero_authority_network_or_execution_side_effects(
    monkeypatch,
) -> None:
    target_ids = []
    tracked = (
        Target, Scope, TestIdentity, CredentialBinding, EndpointResourceBinding,
        StoredTestCase, ExecutionPlan, PlanAction, StoredTestRun,
    )
    try:
        ids = make_source_run(network_mode="external_public_authorized")
        target_ids = [ids["target"]]
        with SessionLocal() as db:
            before = {model: db.scalar(select(func.count()).select_from(model))
                      for model in tracked}
            owner_id = db.get(Resource, ids["resource"]).owner_identity_id
            run_snapshot = (
                db.get(StoredTestRun, ids["run"]).response_status,
                db.get(StoredTestRun, ids["run"]).executed_at,
            )
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
        response = derive(ids["run"])
        assert response.status_code == 201
        with SessionLocal() as db:
            after = {model: db.scalar(select(func.count()).select_from(model))
                     for model in tracked}
            assert db.get(Resource, ids["resource"]).owner_identity_id == owner_id
            run = db.get(StoredTestRun, ids["run"])
            assert (run.response_status, run.executed_at) == run_snapshot
            assert list(db.execute(
                select(Target.id, Target.network_mode).order_by(Target.id)
            )) == modes
        assert before == after
        assert settings.allowed_target_hosts == allowed_hosts
        assert settings.allowed_target_host_set == allowed_host_set
    finally:
        cleanup(target_ids)
