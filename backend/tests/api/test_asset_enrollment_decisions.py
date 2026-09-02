from uuid import uuid4

from fastapi.testclient import TestClient
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
from app.main import app


client = TestClient(app)


def make_hierarchy(
    *, dns_code: str = "asset_candidate_dns_public_only"
) -> tuple[int, int, int, int]:
    with SessionLocal() as db:
        profile = AuthorizationProfile(
            name=f"enrollment-{uuid4()}",
            program_name="Synthetic enrollment",
            authorization_type="self_owned",
            max_requests_per_second=1.0,
        )
        db.add(profile)
        db.flush()
        revision = AuthorizationRevision(
            authorization_profile_id=profile.id,
            revision_number=1,
            lifecycle_state="superseded",
            name=profile.name,
            program_name=profile.program_name,
            authorization_type=profile.authorization_type,
            max_requests_per_second=1.0,
        )
        db.add(revision)
        db.flush()
        evaluation = AssetCandidateEvaluation(
            authorization_revision_id=revision.id,
            normalized_hostname="api.example.test",
            decision_code="asset_candidate_included",
            source_type="operator_supplied",
        )
        db.add(evaluation)
        db.flush()
        validation = AssetCandidateDNSValidation(
            asset_candidate_evaluation_id=evaluation.id,
            authorization_revision_id=revision.id,
            decision_code=dns_code,
            normalized_hostname=evaluation.normalized_hostname,
            terminal_hostname="terminal.vendor.test",
        )
        db.add(validation)
        db.commit()
        return profile.id, revision.id, evaluation.id, validation.id


def url(ids: tuple[int, int, int, int]) -> str:
    profile_id, revision_id, evaluation_id, validation_id = ids
    return (
        f"/api/authorization-profiles/{profile_id}/revisions/{revision_id}/"
        f"asset-candidate-evaluations/{evaluation_id}/dns-validations/"
        f"{validation_id}/enrollment-decisions"
    )


def cleanup(hierarchies: list[tuple[int, int, int, int]], target_id=None) -> None:
    with SessionLocal() as db:
        if target_id is not None:
            db.execute(delete(Target).where(Target.id == target_id))
        validation_ids = [item[3] for item in hierarchies]
        evaluation_ids = [item[2] for item in hierarchies]
        revision_ids = [item[1] for item in hierarchies]
        profile_ids = [item[0] for item in hierarchies]
        db.execute(delete(AssetEnrollmentDecision).where(
            AssetEnrollmentDecision.asset_candidate_dns_validation_id.in_(
                validation_ids
            )
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


def test_explicit_approved_and_rejected_decisions_append_exact_provenance() -> None:
    hierarchies = []
    try:
        ids = make_hierarchy(dns_code="asset_candidate_dns_prohibited")
        hierarchies = [ids]
        endpoint = url(ids)
        assert client.post(endpoint).status_code == 422
        approved = client.post(endpoint, json={
            "decision": "approved",
            "reason_code": "ownership_confirmed",
            "note": "Operator reviewed retained provenance.",
        })
        assert approved.status_code == 201
        body = approved.json()
        assert body["asset_candidate_dns_validation_id"] == ids[3]
        assert body["authorization_revision_id"] == ids[1]
        assert body["normalized_hostname"] == "api.example.test"
        assert body["decision"] == "approved"
        assert "target_id" not in body and "network_mode" not in body
        rejected = client.post(endpoint, json={
            "decision": "rejected",
            "reason_code": "dns_risk",
        })
        assert rejected.status_code == 201
        assert rejected.json()["id"] != body["id"]
        assert rejected.json()["decision"] == "rejected"
        assert [item["id"] for item in client.get(endpoint).json()] == [
            body["id"], rejected.json()["id"]
        ]
        assert client.get(f"{endpoint}/{body['id']}").json() == body
    finally:
        cleanup(hierarchies)


def test_no_dns_result_implies_automatic_approval() -> None:
    hierarchies = []
    try:
        for dns_code in (
            "asset_candidate_dns_public_only",
            "asset_candidate_dns_private_local_only",
            "asset_candidate_dns_prohibited",
            "asset_candidate_dns_resolution_failed",
        ):
            ids = make_hierarchy(dns_code=dns_code)
            hierarchies.append(ids)
            assert client.get(url(ids)).json() == []
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                AssetEnrollmentDecision
            )) == 0
    finally:
        cleanup(hierarchies)


def test_hierarchy_mismatch_and_exact_get_ownership_fail_closed() -> None:
    hierarchies = []
    try:
        first = make_hierarchy()
        second = make_hierarchy()
        hierarchies = [first, second]
        first_decision = client.post(url(first), json={
            "decision": "approved"
        }).json()
        cross_paths = (
            (first[0], second[1], second[2], second[3]),
            (second[0], second[1], first[2], first[3]),
            (second[0], second[1], second[2], first[3]),
        )
        for path in cross_paths:
            assert client.post(url(path), json={
                "decision": "rejected"
            }).status_code == 404
        assert client.get(
            f"{url(second)}/{first_decision['id']}"
        ).status_code == 404
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                AssetEnrollmentDecision
            )) == 1
    finally:
        cleanup(hierarchies)


def test_extra_authority_secret_and_unbounded_fields_are_rejected() -> None:
    hierarchies = []
    try:
        ids = make_hierarchy()
        hierarchies = [ids]
        endpoint = url(ids)
        forbidden = {
            "target_id": 1,
            "scope_id": 1,
            "network_mode": "external_public_authorized",
            "allowed_target_hosts": "evil.test",
            "execution_plan_id": 1,
            "authorization": "redacted",
            "cookie": "redacted",
            "api_key": "redacted",
            "credentials": {},
            "resolver_error": "redacted",
            "metadata": {},
        }
        for field, value in forbidden.items():
            response = client.post(endpoint, json={
                "decision": "approved", field: value
            })
            assert response.status_code == 422
        assert client.post(endpoint, json={
            "decision": "approved", "reason_code": "execute_now"
        }).status_code == 422
        assert client.post(endpoint, json={
            "decision": "approved", "note": "x" * 501
        }).status_code == 422
        assert client.post(endpoint, json={
            "decision": "approved", "note": ""
        }).status_code == 422
        assert client.get(endpoint).json() == []
    finally:
        cleanup(hierarchies)


def test_authentication_material_in_note_is_rejected_without_echo_or_row() -> None:
    hierarchies = []
    try:
        ids = make_hierarchy()
        hierarchies = [ids]
        endpoint = url(ids)
        secret_notes = (
            "Authorization: Bearer actual-auth-token-4815",
            "Bearer standalone-token-9821",
            "Cookie: session=actual-cookie-value-1732",
            "Set-Cookie: session=actual-set-cookie-value-6149",
            "x-api-key: actual-api-key-value-7253",
            "api_key=actual-api-key-value-8364",
            "access_token=actual-access-token-9475",
            "refresh_token: actual-refresh-token-1586",
            "credentials=actual-credential-value-2697",
            "password: actual-password-value-3708",
            "secret=actual-secret-value-4819",
            "client_secret=actual-client-secret-value-5920",
            "db_password=actual-db-password-value-6031",
        )
        for note in secret_notes:
            response = client.post(endpoint, json={
                "decision": "approved", "note": note,
            })
            assert response.status_code == 422
            assert response.json() == {
                "detail": [{
                    "type": "asset_enrollment_note_auth_material",
                    "loc": ["body", "note"],
                    "msg": (
                        "Enrollment decision note contains prohibited "
                        "authentication material."
                    ),
                }]
            }
            assert note not in response.text
            with SessionLocal() as db:
                assert db.scalar(select(func.count()).select_from(
                    AssetEnrollmentDecision
                ).where(
                    AssetEnrollmentDecision.asset_candidate_dns_validation_id
                    == ids[3]
                )) == 0

        ordinary_note = "Operator confirmed ownership from internal inventory."
        accepted = client.post(endpoint, json={
            "decision": "approved", "note": ordinary_note,
        })
        assert accepted.status_code == 201
        assert accepted.json()["note"] == ordinary_note
        assert client.get(
            f"{endpoint}/{accepted.json()['id']}"
        ).json()["note"] == ordinary_note
    finally:
        cleanup(hierarchies)


def test_decision_history_is_deterministically_paginated_and_capped() -> None:
    hierarchies = []
    try:
        ids = make_hierarchy()
        hierarchies = [ids]
        with SessionLocal() as db:
            rows = [AssetEnrollmentDecision(
                asset_candidate_dns_validation_id=ids[3],
                authorization_revision_id=ids[1],
                decision="approved" if number % 2 == 0 else "rejected",
                normalized_hostname="api.example.test",
            ) for number in range(105)]
            db.add_all(rows)
            db.commit()
            row_ids = [row.id for row in rows]
        endpoint = url(ids)
        assert [item["id"] for item in client.get(endpoint).json()] == row_ids[:50]
        maximum = client.get(endpoint, params={"limit": 100}).json()
        assert [item["id"] for item in maximum] == row_ids[:100]
        assert [item["id"] for item in client.get(endpoint, params={
            "after_id": row_ids[99], "limit": 100
        }).json()] == row_ids[100:]
        assert client.get(endpoint, params={"limit": 101}).status_code == 422
    finally:
        cleanup(hierarchies)


def test_enrollment_has_zero_network_or_authority_side_effects(
    monkeypatch,
) -> None:
    hierarchies = []
    target_id = None
    tracked = (
        Target, Scope, Endpoint, OpenAPIImportRecord, ExecutionPlan, PlanAction,
        StoredTestCase, StoredTestRun,
    )
    try:
        ids = make_hierarchy()
        hierarchies = [ids]
        with SessionLocal() as db:
            target = Target(
                name=f"retained-external-{uuid4()}",
                base_url="https://retained.example.test",
                environment="test",
                network_mode="external_public_authorized",
            )
            db.add(target)
            db.commit()
            target_id = target.id
            before = {model: db.scalar(select(func.count()).select_from(model))
                      for model in tracked}
        allowed_hosts = settings.allowed_target_hosts
        allowed_host_set = settings.allowed_target_host_set

        def prohibited(*args, **kwargs):
            raise AssertionError("network path invoked")

        monkeypatch.setattr("socket.getaddrinfo", prohibited)
        monkeypatch.setattr("socket.create_connection", prohibited)
        monkeypatch.setattr(
            "app.services.asset_candidate_dns.classify_asset_candidate_dns",
            prohibited,
        )
        monkeypatch.setattr(
            "app.network_safety.gateway.NetworkGateway.request", prohibited
        )
        monkeypatch.setattr("httpcore.ConnectionPool.stream", prohibited)
        response = client.post(url(ids), json={"decision": "approved"})
        assert response.status_code == 201
        assert client.get(url(ids)).status_code == 200
        with SessionLocal() as db:
            after = {model: db.scalar(select(func.count()).select_from(model))
                     for model in tracked}
            assert db.get(Target, target_id).network_mode == (
                "external_public_authorized"
            )
        assert after == before
        assert settings.allowed_target_hosts == allowed_hosts
        assert settings.allowed_target_host_set == allowed_host_set
    finally:
        cleanup(hierarchies, target_id)
