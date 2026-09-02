from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.api.routes.authorization_profiles import get_asset_candidate_dns_resolver
from app.core.config import settings
from app.db.models import (
    AssetCandidateDNSAddress,
    AssetCandidateDNSCNAMEHop,
    AssetCandidateDNSValidation,
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
from app.db.session import SessionLocal, engine
from app.main import app


client = TestClient(app)


class FakeResolver:
    def __init__(self, *, cnames=None, addresses=None, callback=None, error=None):
        self.cnames = cnames or {}
        self.addresses = addresses or {}
        self.callback = callback
        self.error = error
        self.calls = 0

    def lookup_cname(self, hostname: str) -> str | None:
        self.calls += 1
        if self.callback is not None:
            callback, self.callback = self.callback, None
            callback()
        if self.error is not None:
            raise self.error
        return self.cnames.get(hostname)

    def resolve_addresses(self, hostname: str):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.addresses.get(hostname, ())


def make_evaluations() -> tuple[int, int, dict[str, int]]:
    profile = client.post("/api/authorization-profiles", json={
        "name": f"dns-validation-{uuid4()}",
        "program_name": "Synthetic DNS validation",
        "authorization_type": "self_owned",
    }).json()
    revision = client.post(
        f"/api/authorization-profiles/{profile['id']}/revisions"
    ).json()
    rules_url = (
        f"/api/authorization-profiles/{profile['id']}/revisions/"
        f"{revision['id']}/asset-hostname-rules"
    )
    client.post(rules_url, json={
        "rule_type": "include", "hostname_pattern": "*.example.test"
    })
    client.post(rules_url, json={
        "rule_type": "exclude", "hostname_pattern": "blocked.example.test"
    })
    client.post(
        f"/api/authorization-profiles/{profile['id']}/revisions/"
        f"{revision['id']}/activate"
    )
    evaluations_url = rules_url.replace(
        "asset-hostname-rules", "asset-candidate-evaluations"
    )
    evaluations = {
        name: client.post(evaluations_url, json={"hostname": hostname}).json()["id"]
        for name, hostname in {
            "included": "api.example.test",
            "excluded": "blocked.example.test",
            "not_included": "outside.other.test",
        }.items()
    }
    return profile["id"], revision["id"], evaluations


def validation_url(profile_id: int, revision_id: int, evaluation_id: int) -> str:
    return (
        f"/api/authorization-profiles/{profile_id}/revisions/{revision_id}/"
        f"asset-candidate-evaluations/{evaluation_id}/dns-validations"
    )


def cleanup(profile_ids: list[int], target_id: int | None = None) -> None:
    app.dependency_overrides.pop(get_asset_candidate_dns_resolver, None)
    with SessionLocal() as db:
        if target_id is not None:
            db.execute(delete(Target).where(Target.id == target_id))
        revision_ids = list(db.scalars(select(AuthorizationRevision.id).where(
            AuthorizationRevision.authorization_profile_id.in_(profile_ids)
        )))
        evaluation_ids = list(db.scalars(select(AssetCandidateEvaluation.id).where(
            AssetCandidateEvaluation.authorization_revision_id.in_(revision_ids)
        )))
        validation_ids = list(db.scalars(select(AssetCandidateDNSValidation.id).where(
            AssetCandidateDNSValidation.asset_candidate_evaluation_id.in_(evaluation_ids)
        )))
        db.execute(delete(AssetCandidateDNSAddress).where(
            AssetCandidateDNSAddress.dns_validation_id.in_(validation_ids)
        ))
        db.execute(delete(AssetCandidateDNSCNAMEHop).where(
            AssetCandidateDNSCNAMEHop.dns_validation_id.in_(validation_ids)
        ))
        db.execute(delete(AssetCandidateDNSValidation).where(
            AssetCandidateDNSValidation.id.in_(validation_ids)
        ))
        db.execute(delete(AssetCandidateEvaluation).where(
            AssetCandidateEvaluation.id.in_(evaluation_ids)
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


def test_exact_included_evaluation_persists_exact_dns_provenance_and_appends() -> None:
    profile_ids: list[int] = []
    try:
        profile_id, revision_id, evaluations = make_evaluations()
        profile_ids = [profile_id]
        resolver = FakeResolver(
            cnames={
                "api.example.test": "EDGE.EXAMPLE.TEST.",
                "edge.example.test": "terminal.vendor.test.",
            },
            addresses={
                "terminal.vendor.test": (
                    "2606:4700:4700::1111", "8.8.8.8"
                )
            },
        )
        app.dependency_overrides[get_asset_candidate_dns_resolver] = lambda: resolver
        url = validation_url(profile_id, revision_id, evaluations["included"])
        first = client.post(url)
        assert first.status_code == 201
        body = first.json()
        assert body["asset_candidate_evaluation_id"] == evaluations["included"]
        assert body["authorization_revision_id"] == revision_id
        assert body["decision_code"] == "asset_candidate_dns_public_only"
        assert body["normalized_hostname"] == "api.example.test"
        assert body["terminal_hostname"] == "terminal.vendor.test"
        assert body["cname_chain"] == [
            {"ordinal": 1, "hostname": "edge.example.test"},
            {"ordinal": 2, "hostname": "terminal.vendor.test"},
        ]
        assert body["addresses"] == [
            {"ordinal": 1, "address": "8.8.8.8", "category": "public"},
            {"ordinal": 2, "address": "2606:4700:4700::1111", "category": "public"},
        ]
        second = client.post(url, json={})
        assert second.status_code == 201
        assert second.json()["id"] != body["id"]
        assert [item["id"] for item in client.get(url).json()] == [
            body["id"], second.json()["id"]
        ]
        assert client.get(f"{url}/{body['id']}").json() == body
        assert client.post(url, json={"address": "127.0.0.1"}).status_code == 422
    finally:
        cleanup(profile_ids)


def test_ineligible_inactive_and_cross_ownership_fail_before_dns() -> None:
    profile_ids: list[int] = []
    try:
        profile_id, revision_id, evaluations = make_evaluations()
        other_profile, other_revision, other_evaluations = make_evaluations()
        profile_ids = [profile_id, other_profile]
        resolver = FakeResolver(addresses={"api.example.test": ("8.8.8.8",)})
        app.dependency_overrides[get_asset_candidate_dns_resolver] = lambda: resolver
        for evaluation_id in (
            evaluations["excluded"], evaluations["not_included"]
        ):
            assert client.post(validation_url(
                profile_id, revision_id, evaluation_id
            )).status_code == 409
        assert client.post(validation_url(
            profile_id, other_revision, other_evaluations["included"]
        )).status_code == 404
        assert client.post(validation_url(
            other_profile, other_revision, evaluations["included"]
        )).status_code == 404
        for lifecycle_state in ("draft", "superseded", "revoked"):
            with SessionLocal() as db:
                db.get(AuthorizationRevision, revision_id).lifecycle_state = (
                    lifecycle_state
                )
                db.commit()
            assert client.post(validation_url(
                profile_id, revision_id, evaluations["included"]
            )).status_code == 409
        assert resolver.calls == 0
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                AssetCandidateDNSValidation
            )) == 0
    finally:
        cleanup(profile_ids)


def test_no_db_connection_is_checked_out_during_dns_and_lifecycle_drift_fails() -> None:
    profile_ids: list[int] = []
    try:
        profile_id, revision_id, evaluations = make_evaluations()
        profile_ids = [profile_id]

        def revoke_during_dns() -> None:
            assert engine.pool.checkedout() == 0
            with SessionLocal() as db:
                revision = db.get(AuthorizationRevision, revision_id)
                revision.lifecycle_state = "revoked"
                db.commit()

        resolver = FakeResolver(
            addresses={"api.example.test": ("8.8.8.8",)},
            callback=revoke_during_dns,
        )
        app.dependency_overrides[get_asset_candidate_dns_resolver] = lambda: resolver
        response = client.post(validation_url(
            profile_id, revision_id, evaluations["included"]
        ))
        assert response.status_code == 409
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(
                AssetCandidateDNSValidation
            )) == 0
    finally:
        cleanup(profile_ids)


def test_sanitized_failure_persists_without_exception_text() -> None:
    profile_ids: list[int] = []
    try:
        profile_id, revision_id, evaluations = make_evaluations()
        profile_ids = [profile_id]
        resolver = FakeResolver(error=RuntimeError("secret resolver provider detail"))
        app.dependency_overrides[get_asset_candidate_dns_resolver] = lambda: resolver
        response = client.post(validation_url(
            profile_id, revision_id, evaluations["included"]
        ))
        assert response.status_code == 201
        assert response.json()["decision_code"] == (
            "asset_candidate_dns_resolution_failed"
        )
        assert "secret" not in response.text
        with SessionLocal() as db:
            row = db.get(AssetCandidateDNSValidation, response.json()["id"])
            assert "secret" not in repr(row.__dict__)
    finally:
        cleanup(profile_ids)


def test_prohibited_dns_result_persists_as_observation_only() -> None:
    profile_ids: list[int] = []
    try:
        profile_id, revision_id, evaluations = make_evaluations()
        profile_ids = [profile_id]
        resolver = FakeResolver(addresses={
            "api.example.test": ("169.254.169.254",)
        })
        app.dependency_overrides[get_asset_candidate_dns_resolver] = lambda: resolver
        response = client.post(validation_url(
            profile_id, revision_id, evaluations["included"]
        ))
        assert response.status_code == 201
        assert response.json()["decision_code"] == (
            "asset_candidate_dns_prohibited"
        )
        assert response.json()["addresses"] == [{
            "ordinal": 1,
            "address": "169.254.169.254",
            "category": "link_local",
        }]
    finally:
        cleanup(profile_ids)


def test_bounded_history_and_exact_get_ownership_after_revoke() -> None:
    profile_ids: list[int] = []
    try:
        profile_id, revision_id, evaluations = make_evaluations()
        other_profile, other_revision, other_evaluations = make_evaluations()
        profile_ids = [profile_id, other_profile]
        with SessionLocal() as db:
            rows = [AssetCandidateDNSValidation(
                asset_candidate_evaluation_id=evaluations["included"],
                authorization_revision_id=revision_id,
                decision_code="asset_candidate_dns_public_only",
                normalized_hostname="api.example.test",
                terminal_hostname="api.example.test",
            ) for _ in range(105)]
            db.add_all(rows)
            db.commit()
            ids = [row.id for row in rows]
        url = validation_url(profile_id, revision_id, evaluations["included"])
        assert [row["id"] for row in client.get(url).json()] == ids[:50]
        maximum = client.get(url, params={"limit": 100}).json()
        assert [row["id"] for row in maximum] == ids[:100]
        assert [row["id"] for row in client.get(url, params={
            "after_id": ids[99], "limit": 100
        }).json()] == ids[100:]
        assert client.get(url, params={"limit": 101}).status_code == 422
        wrong_url = validation_url(
            other_profile, other_revision, other_evaluations["included"]
        )
        assert client.get(f"{wrong_url}/{ids[0]}").status_code == 404
        client.post(
            f"/api/authorization-profiles/{profile_id}/revisions/{revision_id}/revoke"
        )
        assert client.get(f"{url}/{ids[0]}").status_code == 200
    finally:
        cleanup(profile_ids)


def test_dns_validation_has_zero_authority_or_application_network_side_effects(
    monkeypatch,
) -> None:
    profile_ids: list[int] = []
    target_id = None
    tracked = (
        Target, Scope, Endpoint, OpenAPIImportRecord, ExecutionPlan, PlanAction,
        StoredTestCase, StoredTestRun,
    )
    try:
        profile_id, revision_id, evaluations = make_evaluations()
        profile_ids = [profile_id]
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
            raise AssertionError("application network path invoked")

        monkeypatch.setattr("socket.create_connection", prohibited)
        monkeypatch.setattr(
            "app.network_safety.gateway.NetworkGateway.request", prohibited
        )
        monkeypatch.setattr(
            "app.network_safety.gateway.DirectTCPConnector.connect", prohibited
        )
        monkeypatch.setattr("httpcore.ConnectionPool.stream", prohibited)
        resolver = FakeResolver(addresses={"api.example.test": ("10.0.0.1",)})
        app.dependency_overrides[get_asset_candidate_dns_resolver] = lambda: resolver
        response = client.post(validation_url(
            profile_id, revision_id, evaluations["included"]
        ))
        assert response.json()["decision_code"] == (
            "asset_candidate_dns_private_local_only"
        )
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
        cleanup(profile_ids, target_id)
