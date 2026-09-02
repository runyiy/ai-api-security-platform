from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, func, select

from app.core.config import settings
from app.db.models import (
    AssetCandidateDNSValidation,
    AssetCandidateEvaluation,
    AssetEnrollmentDecision,
    AuthorizationProfile,
    AuthorizationRevision,
    Endpoint,
    ExecutionPlan,
    OpenAPIImportRecord,
    PlanAction,
    Scope,
    Target,
    TestCase as StoredTestCase,
    TestRun as StoredTestRun,
)
from app.db.session import SessionLocal
from app.executors.http import ExecutionBlockedError, PolicyEnforcedHTTPExecutor
from app.executors.rate_limit import InMemoryRateLimiter
from app.main import app
from app.policies.scope_policy import ScopePolicyEngine


client = TestClient(app)


def make_provenance(
    *,
    dns_code: str = "asset_candidate_dns_private_local_only",
    decision: str = "approved",
    lifecycle: str = "active",
    hostname: str = "api.example.test",
) -> tuple[int, int, int, int, int]:
    with SessionLocal() as db:
        profile = AuthorizationProfile(
            name=f"target-enrollment-{uuid4()}",
            program_name="Synthetic target enrollment",
            authorization_type="self_owned",
            automation_allowed=True,
            max_requests_per_second=1.0,
            allow_get=True,
        )
        db.add(profile)
        db.flush()
        revision = AuthorizationRevision(
            authorization_profile_id=profile.id,
            revision_number=1,
            lifecycle_state=lifecycle,
            name=profile.name,
            program_name=profile.program_name,
            authorization_type=profile.authorization_type,
            automation_allowed=True,
            max_requests_per_second=1.0,
            allow_get=True,
        )
        db.add(revision)
        db.flush()
        evaluation = AssetCandidateEvaluation(
            authorization_revision_id=revision.id,
            normalized_hostname=hostname,
            decision_code="asset_candidate_included",
            source_type="operator_supplied",
        )
        db.add(evaluation)
        db.flush()
        validation = AssetCandidateDNSValidation(
            asset_candidate_evaluation_id=evaluation.id,
            authorization_revision_id=revision.id,
            decision_code=dns_code,
            normalized_hostname=hostname,
        )
        db.add(validation)
        db.flush()
        enrollment = AssetEnrollmentDecision(
            asset_candidate_dns_validation_id=validation.id,
            authorization_revision_id=revision.id,
            decision=decision,
            normalized_hostname=hostname,
        )
        db.add(enrollment)
        db.commit()
        return (
            profile.id, revision.id, evaluation.id, validation.id,
            enrollment.id,
        )


def endpoint(ids: tuple[int, int, int, int, int]) -> str:
    profile_id, revision_id, evaluation_id, validation_id, decision_id = ids
    return (
        f"/api/authorization-profiles/{profile_id}/revisions/{revision_id}/"
        f"asset-candidate-evaluations/{evaluation_id}/dns-validations/"
        f"{validation_id}/enrollment-decisions/{decision_id}/target"
    )


def payload(
    *, network_mode: str = "private_local", scheme: str = "https",
    port: int | None = None, name: str = "Enrolled API",
) -> dict:
    result = {
        "name": name,
        "environment": "test",
        "scheme": scheme,
        "network_mode": network_mode,
    }
    if port is not None:
        result["port"] = port
    return result


def cleanup(groups: list[tuple[int, int, int, int, int]]) -> None:
    with SessionLocal() as db:
        decision_ids = [item[4] for item in groups]
        validation_ids = [item[3] for item in groups]
        evaluation_ids = [item[2] for item in groups]
        revision_ids = [item[1] for item in groups]
        profile_ids = [item[0] for item in groups]
        db.execute(delete(Target).where(
            Target.asset_enrollment_decision_id.in_(decision_ids)
        ))
        db.execute(delete(AssetEnrollmentDecision).where(
            AssetEnrollmentDecision.id.in_(decision_ids)
        ))
        db.execute(delete(AssetCandidateDNSValidation).where(
            AssetCandidateDNSValidation.id.in_(validation_ids)
        ))
        db.execute(delete(AssetCandidateEvaluation).where(
            AssetCandidateEvaluation.id.in_(evaluation_ids)
        ))
        db.execute(delete(AuthorizationRevision).where(
            AuthorizationRevision.id.in_(revision_ids)
        ))
        db.execute(delete(AuthorizationProfile).where(
            AuthorizationProfile.id.in_(profile_ids)
        ))
        db.commit()


@pytest.mark.parametrize(("dns_code", "mode", "expected_url"), (
    ("asset_candidate_dns_private_local_only", "private_local",
     "https://api.example.test"),
    ("asset_candidate_dns_public_only", "external_public_authorized",
     "https://api.example.test"),
))
def test_approved_compatible_decision_creates_exact_bound_target(
    dns_code, mode, expected_url,
) -> None:
    groups = []
    try:
        ids = make_provenance(dns_code=dns_code)
        groups = [ids]
        response = client.post(endpoint(ids), json=payload(network_mode=mode))
        assert response.status_code == 201
        body = response.json()
        assert body["asset_enrollment_decision_id"] == ids[4]
        assert body["authorization_profile_id"] == ids[0]
        assert body["authorization_revision_id"] == ids[1]
        assert body["base_url"] == expected_url
        assert body["network_mode"] == mode
        with SessionLocal() as db:
            target = db.get(Target, body["id"])
            assert target.asset_enrollment_decision_id == ids[4]
            assert target.authorization_revision_id == ids[1]
    finally:
        cleanup(groups)


def test_canonical_ports_idempotency_conflict_and_duplicate_origin() -> None:
    groups = []
    unrelated_target_id = None
    try:
        ids = make_provenance()
        groups = [ids]
        request = payload(port=443)
        first = client.post(endpoint(ids), json=request)
        second = client.post(endpoint(ids), json=request)
        assert first.status_code == second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        assert first.json()["base_url"] == "https://api.example.test"
        assert client.post(endpoint(ids), json={
            **request, "name": "Materially different",
        }).json()["detail"] == "asset_enrollment_target_conflict"

        other = make_provenance(hostname="duplicate.example.test")
        groups.append(other)
        with SessionLocal() as db:
            unrelated = Target(
                name="Existing origin",
                base_url="http://duplicate.example.test:80/",
                environment="test", network_mode="private_local",
            )
            db.add(unrelated)
            db.commit()
            unrelated_target_id = unrelated.id
        duplicate = client.post(endpoint(other), json=payload(scheme="http"))
        assert duplicate.status_code == 409
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Target).where(
                Target.asset_enrollment_decision_id == other[4]
            )) == 0
            assert db.get(Target, unrelated_target_id).name == "Existing origin"
    finally:
        if unrelated_target_id is not None:
            with SessionLocal() as db:
                db.execute(delete(Target).where(Target.id == unrelated_target_id))
                db.commit()
        cleanup(groups)


@pytest.mark.parametrize(("kwargs", "request_mode", "detail"), (
    ({"decision": "rejected"}, "private_local",
     "asset_enrollment_decision_not_approved"),
    ({"dns_code": "asset_candidate_dns_prohibited"}, "private_local",
     "asset_enrollment_dns_outcome_ineligible"),
    ({"dns_code": "asset_candidate_dns_resolution_failed"}, "private_local",
     "asset_enrollment_dns_outcome_ineligible"),
    ({"dns_code": "asset_candidate_dns_invalid"}, "private_local",
     "asset_enrollment_dns_outcome_ineligible"),
    ({"dns_code": "asset_candidate_dns_private_local_only"},
     "external_public_authorized", "asset_enrollment_network_mode_mismatch"),
    ({"dns_code": "asset_candidate_dns_public_only"}, "private_local",
     "asset_enrollment_network_mode_mismatch"),
    ({"lifecycle": "revoked"}, "private_local",
     "asset_enrollment_revision_inactive"),
))
def test_ineligible_provenance_creates_zero_target(kwargs, request_mode, detail) -> None:
    groups = []
    try:
        ids = make_provenance(**kwargs)
        groups = [ids]
        response = client.post(
            endpoint(ids), json=payload(network_mode=request_mode)
        )
        assert response.status_code == 409
        assert response.json()["detail"] == detail
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Target).where(
                Target.asset_enrollment_decision_id == ids[4]
            )) == 0
    finally:
        cleanup(groups)


def test_hierarchy_mismatch_and_caller_authority_injection_fail_closed() -> None:
    groups = []
    try:
        first = make_provenance()
        second = make_provenance()
        groups = [first, second]
        for mixed in (
            (first[0], second[1], second[2], second[3], second[4]),
            (second[0], second[1], first[2], first[3], first[4]),
            (second[0], second[1], second[2], first[3], first[4]),
            (second[0], second[1], second[2], second[3], first[4]),
        ):
            assert client.post(endpoint(mixed), json=payload()).status_code == 404
        for field, value in {
            "hostname": "evil.test", "base_url": "https://evil.test/path?q=x",
            "path": "/x", "query": "x=1", "fragment": "x",
            "userinfo": "admin", "authorization": "redacted",
            "scope_id": 1, "allowed_target_hosts": ["evil.test"],
            "execution_plan_id": 1,
        }.items():
            assert client.post(endpoint(first), json={
                **payload(), field: value,
            }).status_code == 422
        assert client.post(endpoint(first), json={
            **payload(), "scheme": "ftp",
        }).status_code == 422
        assert client.post(endpoint(first), json={
            **payload(), "port": 65536,
        }).status_code == 422
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Target).where(
                Target.asset_enrollment_decision_id.in_([first[4], second[4]])
            )) == 0
    finally:
        cleanup(groups)


def test_lifecycle_transition_serializes_and_fails_closed() -> None:
    groups = []
    started = Event()
    try:
        ids = make_provenance()
        groups = [ids]

        def enroll():
            started.set()
            return client.post(endpoint(ids), json=payload())

        with SessionLocal() as lifecycle_db:
            lifecycle_db.scalar(select(AuthorizationProfile).where(
                AuthorizationProfile.id == ids[0]
            ).with_for_update())
            revision = lifecycle_db.scalar(select(AuthorizationRevision).where(
                AuthorizationRevision.id == ids[1]
            ).with_for_update())
            revision.lifecycle_state = "revoked"
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(enroll)
                assert started.wait(timeout=2)
                assert not future.done()
                lifecycle_db.commit()
                response = future.result(timeout=5)
        assert response.status_code == 409
        assert response.json()["detail"] == "asset_enrollment_revision_inactive"
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Target).where(
                Target.asset_enrollment_decision_id == ids[4]
            )) == 0
    finally:
        cleanup(groups)


def test_target_enrollment_has_zero_network_and_authority_side_effects(monkeypatch) -> None:
    groups = []
    tracked = (
        Scope, Endpoint, OpenAPIImportRecord, ExecutionPlan, PlanAction,
        StoredTestCase, StoredTestRun,
    )
    try:
        ids = make_provenance()
        unrelated_ids = make_provenance(
            dns_code="asset_candidate_dns_public_only",
            hostname="unrelated.example.test",
        )
        groups = [ids, unrelated_ids]
        with SessionLocal() as db:
            unrelated = Target(
                name="Unrelated existing target",
                base_url="https://unrelated.example.test",
                environment="test",
                network_mode="external_public_authorized",
                authorization_profile_id=unrelated_ids[0],
                authorization_revision_id=unrelated_ids[1],
                asset_enrollment_decision_id=unrelated_ids[4],
            )
            db.add(unrelated)
            db.commit()
            unrelated_snapshot = {
                "id": unrelated.id,
                "network_mode": unrelated.network_mode,
                "authorization_profile_id": unrelated.authorization_profile_id,
                "authorization_revision_id": unrelated.authorization_revision_id,
                "asset_enrollment_decision_id": (
                    unrelated.asset_enrollment_decision_id
                ),
            }
            before = {model: db.scalar(select(func.count()).select_from(model))
                      for model in tracked}
        allowed_hosts = settings.allowed_target_hosts
        allowed_host_set = settings.allowed_target_host_set

        def prohibited(*args, **kwargs):
            raise AssertionError("network path invoked")

        monkeypatch.setattr("socket.getaddrinfo", prohibited)
        monkeypatch.setattr("socket.create_connection", prohibited)
        monkeypatch.setattr(
            "app.network_safety.gateway.NetworkGateway.request", prohibited
        )
        monkeypatch.setattr("httpcore.ConnectionPool.stream", prohibited)
        response = client.post(endpoint(ids), json=payload())
        assert response.status_code == 201
        with SessionLocal() as db:
            after = {model: db.scalar(select(func.count()).select_from(model))
                     for model in tracked}
            unrelated = db.get(Target, unrelated_snapshot["id"])
            assert unrelated is not None
            assert {
                "id": unrelated.id,
                "network_mode": unrelated.network_mode,
                "authorization_profile_id": unrelated.authorization_profile_id,
                "authorization_revision_id": unrelated.authorization_revision_id,
                "asset_enrollment_decision_id": (
                    unrelated.asset_enrollment_decision_id
                ),
            } == unrelated_snapshot
        assert after == before
        assert settings.allowed_target_hosts == allowed_hosts
        assert settings.allowed_target_host_set == allowed_host_set
    finally:
        cleanup(groups)


def test_concurrent_duplicate_origin_has_one_deterministic_winner() -> None:
    groups = []
    try:
        first = make_provenance(hostname="race.example.test")
        second = make_provenance(hostname="race.example.test")
        groups = [first, second]
        barrier = Barrier(2)

        def enroll(ids):
            barrier.wait(timeout=3)
            return client.post(endpoint(ids), json=payload())

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(enroll, groups))
        assert sorted(item.status_code for item in responses) == [201, 409]
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Target).where(
                Target.base_url == "https://race.example.test"
            )) == 1
    finally:
        cleanup(groups)


def test_enrollment_bound_target_cannot_switch_provenance_or_network_mode() -> None:
    groups = []
    try:
        ids = make_provenance()
        groups = [ids]
        created = client.post(endpoint(ids), json=payload()).json()
        target_id = created["id"]
        assert client.patch(
            f"/api/targets/{target_id}/network-mode",
            json={"network_mode": "external_public_authorized"},
        ).status_code == 409
        assert client.patch(
            f"/api/targets/{target_id}/authorization-profile",
            json={"authorization_profile_id": None},
        ).status_code == 409
        assert client.patch(
            f"/api/targets/{target_id}/authorization-revision",
            json={"authorization_revision_id": None},
        ).status_code == 409
        with SessionLocal() as db:
            target = db.get(Target, target_id)
            assert target.asset_enrollment_decision_id == ids[4]
            assert target.authorization_profile_id == ids[0]
            assert target.authorization_revision_id == ids[1]
            assert target.network_mode == "private_local"
    finally:
        cleanup(groups)


def test_enrolled_public_target_remains_runtime_blocked_before_gateway() -> None:
    groups = []
    try:
        ids = make_provenance(dns_code="asset_candidate_dns_public_only")
        groups = [ids]
        created = client.post(endpoint(ids), json=payload(
            network_mode="external_public_authorized"
        )).json()
        with SessionLocal() as db:
            target = db.get(Target, created["id"])
            revision = db.get(AuthorizationRevision, ids[1])
            scope = Scope(
                id=999999,
                target_id=target.id,
                hostname="api.example.test",
                path_pattern="/*",
                allowed_methods=["GET"],
                is_active=True,
            )

            class ProhibitedGateway:
                def request(self, **kwargs):
                    raise AssertionError("gateway invoked")

            executor = PolicyEnforcedHTTPExecutor(
                policy_engine=ScopePolicyEngine({"api.example.test"}),
                rate_limiter=InMemoryRateLimiter(requests_per_second=1000.0),
                network_gateway=ProhibitedGateway(),
            )
            with pytest.raises(ExecutionBlockedError) as raised:
                executor.execute(
                    target=target,
                    authorization_revision=revision,
                    scopes=[scope],
                    method="GET",
                    url="https://api.example.test/status",
                    headers={},
                    refresh_authorization=lambda: (target, revision, [scope]),
                    policy_decision_observer=lambda decision: None,
                )
            assert raised.value.code == "external_network_mode_not_ready"
    finally:
        cleanup(groups)
