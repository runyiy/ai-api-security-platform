from datetime import datetime, timedelta, timezone
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
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
from app.db.session import SessionLocal
from app.main import app
from app.services.resource_access_resolution import (
    MAX_ASSERTIONS_SCANNED,
    ResourceAccessResolutionError,
    resolve_resource_access,
)


client = TestClient(app)
NOW = datetime(2030, 6, 1, 12, 0, tzinfo=timezone.utc)


def make_pair(*, network_mode: str = "private_local") -> dict[str, int]:
    with SessionLocal() as db:
        target = Target(
            name=f"resolution-{uuid4()}",
            base_url=f"https://{uuid4()}.example.test",
            environment="test",
            network_mode=network_mode,
        )
        db.add(target)
        db.flush()
        identity = TestIdentity(
            target_id=target.id,
            name="resolution identity",
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
            external_id="external-resolution-secret",
            owner_identity_id=identity.id,
        )
        db.add(resource)
        db.commit()
        return {
            "target": target.id,
            "identity": identity.id,
            "resource": resource.id,
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


def add_assertion(
    ids: dict[str, int],
    *,
    relationship: str = "owner",
    expected_access: str = "allowed",
    verification_state: str = "verified",
    provenance: str = "human_verified",
    confidence: int = 50,
    asserted_at: datetime = NOW - timedelta(days=1),
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
) -> int:
    with SessionLocal() as db:
        assertion = ResourceAccessAssertion(
            resource_id=ids["resource"],
            test_identity_id=ids["identity"],
            relationship=relationship,
            expected_access=expected_access,
            provenance=provenance,
            confidence=confidence,
            verification_state=verification_state,
            asserted_at=asserted_at,
            observed_at=None,
            valid_from=valid_from,
            valid_until=valid_until,
            source_test_run_id=None,
        )
        db.add(assertion)
        db.commit()
        return assertion.id


def resolve(ids: dict[str, int], evaluation_time: datetime = NOW):
    return client.get(
        f"/api/resources/{ids['resource']}/access-resolution",
        params={
            "test_identity_id": ids["identity"],
            "evaluation_time": evaluation_time.isoformat(),
        },
    )


def make_observed_candidate(ids: dict[str, int]) -> int:
    with SessionLocal() as db:
        endpoint = Endpoint(
            target_id=ids["target"], path="/orders/{id}", method="GET",
            operation_id="resolution_baseline", requires_auth=True,
            parameters=[], request_body=None, security=None,
        )
        db.add(endpoint)
        db.flush()
        case = StoredTestCase(
            endpoint_id=endpoint.id,
            actor_identity_id=ids["identity"],
            resource_id=ids["resource"],
            test_type="owner_baseline",
            ownership_relation="owner",
            expected_statuses=[200],
            status="completed",
        )
        db.add(case)
        db.flush()
        run = StoredTestRun(
            test_case_id=case.id,
            request_data={"Authorization": "Bearer never-return"},
            response_status=200,
            response_body="never-return",
            duration_ms=1,
            error_message=None,
            executed_at=NOW - timedelta(hours=1),
        )
        db.add(run)
        db.commit()
        run_id = run.id
    response = client.post(
        f"/api/test-runs/{run_id}/observed-access-assertion", json={}
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_no_migration_exact_pair_and_timezone_boundaries() -> None:
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == [
        "b6d8f0a2c4e5"
    ]
    target_ids = []
    try:
        ids = make_pair()
        other = make_pair()
        target_ids = [ids["target"], other["target"]]
        assert client.get(
            "/api/resources/999999999/access-resolution",
            params={
                "test_identity_id": ids["identity"],
                "evaluation_time": NOW.isoformat(),
            },
        ).status_code == 404
        missing_identity = dict(ids, identity=999_999_999)
        assert resolve(missing_identity).status_code == 404
        cross_target = dict(ids, identity=other["identity"])
        response = resolve(cross_target)
        assert response.status_code == 409
        assert response.json()["detail"] == "resource_identity_target_mismatch"
        assert client.get(
            f"/api/resources/{ids['resource']}/access-resolution",
            params={
                "test_identity_id": ids["identity"],
                "evaluation_time": "2026-06-01T12:00:00",
            },
        ).status_code == 422
    finally:
        cleanup(target_ids)


@pytest.mark.parametrize(("relationship", "expected_access"), (
    ("owner", "allowed"),
    ("non_owner", "allowed"),
))
def test_verified_values_resolve_without_implication(
    relationship: str, expected_access: str
) -> None:
    target_ids = []
    try:
        ids = make_pair()
        target_ids = [ids["target"]]
        assertion_id = add_assertion(
            ids, relationship=relationship, expected_access=expected_access
        )
        response = resolve(ids)
        assert response.status_code == 200
        assert response.json() == {
            "resource_id": ids["resource"],
            "test_identity_id": ids["identity"],
            "evaluation_time": NOW.isoformat().replace("+00:00", "Z"),
            "state": "resolved",
            "relationship": relationship,
            "expected_access": expected_access,
            "supporting_assertion_ids": [assertion_id],
        }
        assert "external-resolution-secret" not in response.text
        assert not {"authorized", "can_execute", "executable",
                    "allowed_to_execute"}.intersection(response.json())
    finally:
        cleanup(target_ids)


def test_independent_merge_duplicates_and_deterministic_support() -> None:
    target_ids = []
    try:
        ids = make_pair()
        target_ids = [ids["target"]]
        relationship_id = add_assertion(
            ids, relationship="owner", expected_access="unspecified"
        )
        access_id = add_assertion(
            ids, relationship="unspecified", expected_access="allowed"
        )
        duplicate_id = add_assertion(
            ids, relationship="owner", expected_access="allowed"
        )
        body = resolve(ids).json()
        assert body["state"] == "resolved"
        assert body["relationship"] == "owner"
        assert body["expected_access"] == "allowed"
        assert body["supporting_assertion_ids"] == sorted([
            relationship_id, access_id, duplicate_id
        ])
    finally:
        cleanup(target_ids)


@pytest.mark.parametrize(("first", "second", "dimension"), (
    (("owner", "allowed"), ("non_owner", "allowed"), "relationship"),
    (("owner", "allowed"), ("owner", "denied"), "expected_access"),
))
def test_conflicting_verified_values_fail_closed(
    first: tuple[str, str], second: tuple[str, str], dimension: str
) -> None:
    target_ids = []
    try:
        ids = make_pair()
        target_ids = [ids["target"]]
        low_id = add_assertion(
            ids, relationship=first[0], expected_access=first[1],
            confidence=1, provenance="target_fixture",
        )
        high_id = add_assertion(
            ids, relationship=second[0], expected_access=second[1],
            confidence=100, provenance="human_verified",
        )
        body = resolve(ids).json()
        assert body["state"] == "conflict"
        assert body[dimension] == "unspecified"
        assert body["supporting_assertion_ids"] == [low_id, high_id]
    finally:
        cleanup(target_ids)


def test_candidate_rejected_and_observed_candidate_are_ignored() -> None:
    target_ids = []
    try:
        ids = make_pair()
        target_ids = [ids["target"]]
        add_assertion(ids, verification_state="candidate", confidence=100)
        add_assertion(ids, verification_state="rejected", confidence=100)
        observed_id = make_observed_candidate(ids)
        body = resolve(ids).json()
        assert body["state"] == "insufficient"
        assert body["relationship"] == "unspecified"
        assert body["expected_access"] == "unspecified"
        assert body["supporting_assertion_ids"] == []
        assert observed_id not in body["supporting_assertion_ids"]
    finally:
        cleanup(target_ids)


def test_time_boundaries_are_asserted_aware_and_half_open() -> None:
    target_ids = []
    try:
        ids = make_pair()
        target_ids = [ids["target"]]
        current_id = add_assertion(ids)
        add_assertion(
            ids, relationship="non_owner", expected_access="denied",
            asserted_at=NOW + timedelta(seconds=1),
            valid_from=NOW - timedelta(days=5),
            valid_until=NOW + timedelta(days=5),
        )
        add_assertion(
            ids, relationship="non_owner", expected_access="denied",
            valid_from=NOW - timedelta(days=2), valid_until=NOW,
        )
        body = resolve(ids).json()
        assert body["state"] == "resolved"
        assert body["supporting_assertion_ids"] == [current_id]

        inclusive_ids = make_pair()
        target_ids.append(inclusive_ids["target"])
        inclusive_id = add_assertion(
            inclusive_ids, asserted_at=NOW - timedelta(days=1),
            valid_from=NOW, valid_until=NOW + timedelta(seconds=1),
        )
        assert resolve(inclusive_ids).json()["supporting_assertion_ids"] == [
            inclusive_id
        ]
        assert resolve(
            inclusive_ids, NOW - timedelta(microseconds=1)
        ).json()["state"] == "insufficient"
        assert resolve(
            inclusive_ids, NOW + timedelta(seconds=1)
        ).json()["state"] == "insufficient"
    finally:
        cleanup(target_ids)


def test_limit_fails_closed_and_query_is_bounded(monkeypatch) -> None:
    target_ids = []
    try:
        ids = make_pair()
        target_ids = [ids["target"]]
        with SessionLocal() as db:
            db.add_all([
                ResourceAccessAssertion(
                    resource_id=ids["resource"],
                    test_identity_id=ids["identity"],
                    relationship="owner",
                    expected_access="allowed",
                    provenance="human_verified",
                    confidence=index % 101,
                    verification_state="verified",
                    asserted_at=NOW - timedelta(days=1),
                    observed_at=None,
                    valid_from=None,
                    valid_until=None,
                    source_test_run_id=None,
                )
                for index in range(MAX_ASSERTIONS_SCANNED + 1)
            ])
            db.commit()
        response = resolve(ids)
        assert response.status_code == 409
        assert response.json()["detail"] == (
            "resource_access_resolution_limit_exceeded"
        )

        with SessionLocal() as db:
            real_scalars = db.scalars
            statements = []

            def capture(statement, *args, **kwargs):
                if "resource_access_assertions" in str(statement):
                    statements.append(statement)
                return real_scalars(statement, *args, **kwargs)

            monkeypatch.setattr(db, "scalars", capture)
            with pytest.raises(
                ResourceAccessResolutionError,
                match="resolution_limit_exceeded",
            ):
                resolve_resource_access(
                    db, ids["resource"], ids["identity"], NOW
                )
            assert len(statements) == 1
            assert statements[0]._limit_clause.value == MAX_ASSERTIONS_SCANNED + 1
    finally:
        cleanup(target_ids)


def test_resolution_is_read_only_and_has_zero_authority_or_network_effects(
    monkeypatch,
) -> None:
    target_ids = []
    tracked = (
        Target, Scope, TestIdentity, CredentialBinding, EndpointResourceBinding,
        StoredTestCase, ExecutionPlan, PlanAction, StoredTestRun,
        ResourceAccessAssertion,
    )
    try:
        ids = make_pair(network_mode="external_public_authorized")
        target_ids = [ids["target"]]
        assertion_id = add_assertion(ids, confidence=0)
        with SessionLocal() as db:
            before = {model: db.scalar(select(func.count()).select_from(model))
                      for model in tracked}
            assertion_snapshot = tuple(db.execute(select(
                ResourceAccessAssertion.relationship,
                ResourceAccessAssertion.expected_access,
                ResourceAccessAssertion.verification_state,
                ResourceAccessAssertion.confidence,
            ).where(ResourceAccessAssertion.id == assertion_id)).one())
            owner_id = db.get(Resource, ids["resource"]).owner_identity_id
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
        response = resolve(ids)
        assert response.status_code == 200
        with SessionLocal() as db:
            after = {model: db.scalar(select(func.count()).select_from(model))
                     for model in tracked}
            assert tuple(db.execute(select(
                ResourceAccessAssertion.relationship,
                ResourceAccessAssertion.expected_access,
                ResourceAccessAssertion.verification_state,
                ResourceAccessAssertion.confidence,
            ).where(ResourceAccessAssertion.id == assertion_id)).one()) == (
                assertion_snapshot
            )
            assert db.get(Resource, ids["resource"]).owner_identity_id == owner_id
            assert list(db.execute(
                select(Target.id, Target.network_mode).order_by(Target.id)
            )) == modes
        assert before == after
        assert settings.allowed_target_hosts == allowed_hosts
        assert settings.allowed_target_host_set == allowed_host_set
    finally:
        cleanup(target_ids)
