from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.db.models import (
    AssetCandidateEvaluation,
    AssetHostnameRule,
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
from app.core.config import settings
from app.main import app


client = TestClient(app)


def create_revision(*, activate: bool = False) -> tuple[int, int]:
    profile = client.post("/api/authorization-profiles", json={
        "name": f"candidate-evaluations-{uuid4()}",
        "program_name": "Synthetic candidate program",
        "authorization_type": "self_owned",
    }).json()
    revision = client.post(
        f"/api/authorization-profiles/{profile['id']}/revisions"
    ).json()
    if activate:
        client.post(
            f"/api/authorization-profiles/{profile['id']}/revisions/"
            f"{revision['id']}/activate"
        )
    return profile["id"], revision["id"]


def cleanup(profile_ids: list[int]) -> None:
    if not profile_ids:
        return
    with SessionLocal() as db:
        revision_ids = list(db.scalars(select(AuthorizationRevision.id).where(
            AuthorizationRevision.authorization_profile_id.in_(profile_ids)
        )))
        db.execute(delete(AssetCandidateEvaluation).where(
            AssetCandidateEvaluation.authorization_revision_id.in_(revision_ids)
        ))
        db.execute(delete(AssetHostnameRule).where(
            AssetHostnameRule.authorization_revision_id.in_(revision_ids)
        ))
        db.execute(delete(AuthorizationRevision).where(
            AuthorizationRevision.id.in_(revision_ids)
        ))
        db.execute(delete(AuthorizationProfile).where(
            AuthorizationProfile.id.in_(profile_ids)
        ))
        db.commit()


def test_exact_matcher_provenance_normalization_append_and_bounded_api() -> None:
    profile_ids: list[int] = []
    try:
        profile_id, revision_id = create_revision()
        profile_ids = [profile_id]
        rules_url = (
            f"/api/authorization-profiles/{profile_id}/revisions/{revision_id}/"
            "asset-hostname-rules"
        )
        broad = client.post(rules_url, json={
            "rule_type": "include", "hostname_pattern": "*.test.example"
        }).json()
        specific = client.post(rules_url, json={
            "rule_type": "include", "hostname_pattern": "*.BÜCHER.test.example."
        }).json()
        excluded = client.post(rules_url, json={
            "rule_type": "exclude", "hostname_pattern": "blocked.bücher.test.example"
        }).json()
        assert client.post(
            f"/api/authorization-profiles/{profile_id}/revisions/{revision_id}/activate"
        ).status_code == 200
        url = (
            f"/api/authorization-profiles/{profile_id}/revisions/{revision_id}/"
            "asset-candidate-evaluations"
        )

        included = client.post(url, json={"hostname": "API.BÜCHER.TEST.EXAMPLE."})
        assert included.status_code == 201
        included_body = included.json()
        assert included_body["normalized_hostname"] == "api.xn--bcher-kva.test.example"
        assert included_body["decision_code"] == "asset_candidate_included"
        assert included_body["matched_include_rule_id"] == specific["id"]
        assert included_body["matched_include_rule_id"] != broad["id"]
        assert included_body["matched_exclude_rule_id"] is None
        assert included_body["source_type"] == "operator_supplied"

        denied = client.post(url, json={
            "hostname": "blocked.bücher.test.example"
        }).json()
        assert denied["decision_code"] == "asset_candidate_excluded"
        assert denied["matched_include_rule_id"] == specific["id"]
        assert denied["matched_exclude_rule_id"] == excluded["id"]

        absent = client.post(url, json={"hostname": "elsewhere.example"}).json()
        assert absent["decision_code"] == "asset_candidate_not_included"
        assert absent["matched_include_rule_id"] is None
        assert absent["matched_exclude_rule_id"] is None

        repeated = client.post(url, json={"hostname": "API.BÜCHER.TEST.EXAMPLE."})
        assert repeated.status_code == 201
        assert repeated.json()["id"] != included_body["id"]
        listed = client.get(url)
        assert listed.status_code == 200
        assert len(listed.json()) == 4
        assert client.get(f"{url}/{included_body['id']}").json() == included_body
        assert set(included_body) == {
            "id", "authorization_revision_id", "normalized_hostname",
            "decision_code", "matched_include_rule_id",
            "matched_exclude_rule_id", "source_type", "created_at",
        }
    finally:
        cleanup(profile_ids)


def test_invalid_injected_inactive_and_cross_revision_inputs_create_zero_rows() -> None:
    profile_ids: list[int] = []
    try:
        profile_id, revision_id = create_revision()
        other_profile, other_revision = create_revision(activate=True)
        profile_ids = [profile_id, other_profile]
        url = (
            f"/api/authorization-profiles/{profile_id}/revisions/{revision_id}/"
            "asset-candidate-evaluations"
        )
        assert client.post(url, json={"hostname": "api.example.test"}).status_code == 409
        cross = (
            f"/api/authorization-profiles/{profile_id}/revisions/{other_revision}/"
            "asset-candidate-evaluations"
        )
        assert client.post(cross, json={"hostname": "api.example.test"}).status_code == 404
        assert client.get(cross).status_code == 404
        active_url = (
            f"/api/authorization-profiles/{other_profile}/revisions/{other_revision}/"
            "asset-candidate-evaluations"
        )
        for hostname in ("127.0.0.1", "https://api.example.test", "bad host"):
            response = client.post(active_url, json={"hostname": hostname})
            assert response.status_code == 422
            assert response.json()["detail"] == "Asset candidate hostname is invalid."
        forbidden = {
            "decision_code": "asset_candidate_included",
            "matched_include_rule_id": 1,
            "matched_exclude_rule_id": 2,
            "source_type": "crawler",
            "target_id": 1,
            "scope_id": 1,
            "network_mode": "external_public_authorized",
            "metadata": {},
        }
        for field, value in forbidden.items():
            assert client.post(active_url, json={
                "hostname": "api.example.test", field: value
            }).status_code == 422
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                AssetCandidateEvaluation
            )) == 0
    finally:
        cleanup(profile_ids)


def test_active_only_historical_reads_and_revision_isolation() -> None:
    profile_ids: list[int] = []
    try:
        profile_id, first_id = create_revision()
        profile_ids = [profile_id]
        first_rules = (
            f"/api/authorization-profiles/{profile_id}/revisions/{first_id}/"
            "asset-hostname-rules"
        )
        rule = client.post(first_rules, json={
            "rule_type": "include", "hostname_pattern": "*.first.test"
        }).json()
        client.post(
            f"/api/authorization-profiles/{profile_id}/revisions/{first_id}/activate"
        )
        first_url = first_rules.replace("asset-hostname-rules", "asset-candidate-evaluations")
        event = client.post(first_url, json={"hostname": "api.first.test"}).json()

        second = client.post(
            f"/api/authorization-profiles/{profile_id}/revisions"
        ).json()
        second_rules = (
            f"/api/authorization-profiles/{profile_id}/revisions/{second['id']}/"
            "asset-hostname-rules"
        )
        client.post(second_rules, json={
            "rule_type": "include", "hostname_pattern": "*.second.test"
        })
        client.post(
            f"/api/authorization-profiles/{profile_id}/revisions/{second['id']}/activate"
        )
        assert client.post(first_url, json={"hostname": "again.first.test"}).status_code == 409
        assert client.get(first_url).json() == [event]
        assert event["matched_include_rule_id"] == rule["id"]
        assert client.get(
            f"{second_rules.replace('asset-hostname-rules', 'asset-candidate-evaluations')}/"
            f"{event['id']}"
        ).status_code == 404
        second_url = second_rules.replace(
            "asset-hostname-rules", "asset-candidate-evaluations"
        )
        isolated = client.post(
            second_url, json={"hostname": "api.first.test"}
        ).json()
        assert isolated["decision_code"] == "asset_candidate_not_included"
        assert isolated["matched_include_rule_id"] is None
        client.post(
            f"/api/authorization-profiles/{profile_id}/revisions/{second['id']}/revoke"
        )
        assert client.post(second_url, json={"hostname": "api.second.test"}).status_code == 409
        assert client.get(second_url).json() == [isolated]
        assert client.get(first_url).json() == [event]
    finally:
        cleanup(profile_ids)


def test_evaluation_list_is_deterministically_paginated_and_strictly_capped() -> None:
    profile_ids: list[int] = []
    try:
        profile_id, revision_id = create_revision(activate=True)
        profile_ids = [profile_id]
        with SessionLocal() as db:
            events = [
                AssetCandidateEvaluation(
                    authorization_revision_id=revision_id,
                    normalized_hostname=f"host-{number}.example.test",
                    decision_code="asset_candidate_not_included",
                    source_type="operator_supplied",
                )
                for number in range(105)
            ]
            db.add_all(events)
            db.commit()
            event_ids = [event.id for event in events]
        url = (
            f"/api/authorization-profiles/{profile_id}/revisions/{revision_id}/"
            "asset-candidate-evaluations"
        )

        default_page = client.get(url)
        assert default_page.status_code == 200
        assert [item["id"] for item in default_page.json()] == event_ids[:50]
        maximum_page = client.get(url, params={"limit": 100})
        assert [item["id"] for item in maximum_page.json()] == event_ids[:100]
        second_page = client.get(url, params={
            "after_id": maximum_page.json()[-1]["id"], "limit": 100,
        })
        assert [item["id"] for item in second_page.json()] == event_ids[100:]
        assert client.get(url, params={"limit": 101}).status_code == 422
        assert client.get(url, params={"limit": 0}).status_code == 422
        assert client.get(url, params={"after_id": -1}).status_code == 422
    finally:
        cleanup(profile_ids)


def test_api_evaluation_has_zero_authority_execution_or_discovery_side_effects(
    monkeypatch,
) -> None:
    profile_ids: list[int] = []
    target_id: int | None = None
    tracked = (
        Target, Scope, Endpoint, OpenAPIImportRecord, ExecutionPlan, PlanAction,
        StoredTestCase, StoredTestRun,
    )
    try:
        profile_id, revision_id = create_revision(activate=True)
        profile_ids = [profile_id]
        with SessionLocal() as db:
            target = Target(
                name=f"retained-external-target-{uuid4()}",
                base_url="https://retained.example.test",
                environment="test",
                network_mode="external_public_authorized",
            )
            db.add(target)
            db.commit()
            target_id = target.id
            before = {
                model: db.scalar(select(func.count()).select_from(model))
                for model in tracked
            }
            before_network_mode = db.get(Target, target_id).network_mode
        before_allowed_hosts = settings.allowed_target_hosts
        before_allowed_host_set = settings.allowed_target_host_set

        def prohibited(*args, **kwargs):
            raise AssertionError("candidate evaluation attempted network or DNS")

        monkeypatch.setattr("socket.getaddrinfo", prohibited)
        monkeypatch.setattr("socket.create_connection", prohibited)
        monkeypatch.setattr(
            "app.network_safety.gateway.NetworkGateway.request", prohibited
        )
        monkeypatch.setattr(
            "app.network_safety.gateway.DirectTCPConnector.connect", prohibited
        )
        monkeypatch.setattr("httpcore.ConnectionPool.stream", prohibited)
        url = (
            f"/api/authorization-profiles/{profile_id}/revisions/{revision_id}/"
            "asset-candidate-evaluations"
        )
        assert client.post(url, json={"hostname": "safe.example.test"}).status_code == 201
        assert client.get(url).status_code == 200
        with SessionLocal() as db:
            after = {
                model: db.scalar(select(func.count()).select_from(model))
                for model in tracked
            }
            after_network_mode = db.get(Target, target_id).network_mode
        assert after == before
        assert before_network_mode == after_network_mode == "external_public_authorized"
        assert settings.allowed_target_hosts == before_allowed_hosts
        assert settings.allowed_target_host_set == before_allowed_host_set
    finally:
        if target_id is not None:
            with SessionLocal() as db:
                db.execute(delete(Target).where(Target.id == target_id))
                db.commit()
        cleanup(profile_ids)
