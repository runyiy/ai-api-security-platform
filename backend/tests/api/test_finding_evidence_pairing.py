from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql.dml import Insert
from sqlalchemy.orm import Session, sessionmaker

from app.analyzers.bola import analyze_bola_run
from app.core.config import settings
from app.db.base import Base
from app.db.models import Finding, Resource, Target, TestCase as StoredCase, TestRun as StoredRun
from app.db.session import SessionLocal, engine, get_db
from app.main import app
from app.services.finding_analysis import FindingAnalysisError, FindingAnalysisService
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401


client = TestClient(app)


def analyze(ids, baseline=None):
    return client.post(f"/api/test-runs/{ids['probe']}/analyze", json={
        "baseline_test_run_id": ids["baseline"] if baseline is None else baseline,
    })


def findings(ids):
    with SessionLocal() as db:
        return list(db.execute(select(Finding.__table__).where(
            Finding.test_run_id == ids["probe"])).mappings())


@pytest.mark.parametrize("payload", [None, {}, {"baseline_test_run_id": None}, *[
    {"baseline_test_run_id": value} for value in (0, -1, True, False, 1.0, "1")
]])
def test_strict_required_baseline(evidence_pair, payload):
    response = client.post(f"/api/test-runs/{evidence_pair['probe']}/analyze", json=payload)
    assert response.status_code == 422
    assert findings(evidence_pair) == []


@pytest.mark.parametrize("field", [
    "test_run_id", "probe_test_run_id", "target_id", "endpoint_id", "resource_id",
    "category", "severity", "confidence", "status", "title", "description",
    "response_body", "request_data", "evidence", "credentials", "execution_plan_id",
])
def test_body_forbids_overrides(evidence_pair, field):
    response = client.post(f"/api/test-runs/{evidence_pair['probe']}/analyze", json={
        "baseline_test_run_id": evidence_pair["baseline"], field: "forbidden-secret",
    })
    assert response.status_code == 422
    assert findings(evidence_pair) == []


def test_missing_probe_and_explicit_baseline(evidence_pair):
    ids = evidence_pair
    missing_probe = analyze({**ids, "probe": 999999999})
    assert missing_probe.status_code == 404
    assert missing_probe.json()["detail"] == "TestRun not found."
    missing_baseline = analyze(ids, 999999999)
    assert missing_baseline.status_code == 404
    assert missing_baseline.json()["detail"] == "finding_baseline_test_run_not_found"
    assert findings(ids) == []


@pytest.mark.parametrize("invalid", ["same_run", "type", "endpoint", "resource", "actor"])
def test_invalid_pair_never_falls_back(evidence_pair, invalid):
    ids = evidence_pair
    with SessionLocal() as db:
        # Keep the valid owner baseline available; give the decoy a separate invalid case.
        case = StoredCase(endpoint_id=ids["endpoint"], resource_id=ids["resource"],
                          actor_identity_id=ids["actor"], test_type="invalid_baseline",
                          ownership_relation="owner", expected_statuses=[200], status="completed")
        if invalid == "type":
            case.actor_identity_id = ids["owner"]
        if invalid in {"endpoint", "resource", "actor"}:
            case.test_type = "owner_baseline"
        if invalid == "endpoint":
            case.endpoint_id = ids["other_endpoint"]
            case.actor_identity_id = ids["owner"]
        if invalid == "resource":
            case.resource_id = ids["other_resource"]
            case.actor_identity_id = ids["owner"]
        db.add(case)
        db.flush()
        db.get(StoredRun, ids["decoy"]).test_case_id = case.id
        db.commit()
    response = analyze(ids, ids["probe"] if invalid == "same_run" else ids["decoy"])
    assert response.status_code == 409
    assert response.json()["detail"] == "finding_evidence_pair_invalid"
    assert findings(ids) == []


def test_older_exact_pair_ignores_newer_decoy_and_legacy_owner(evidence_pair, monkeypatch):
    ids = evidence_pair
    with SessionLocal() as db:
        db.get(Resource, ids["resource"]).owner_identity_id = ids["actor"]
        older = db.get(StoredRun, ids["baseline"])
        newer = db.get(StoredRun, ids["decoy"])
        assert older.id < newer.id and older.executed_at < newer.executed_at
        assert (older.response_status, newer.response_status) == (200, 403)
        db.commit()

    def exact_analyzer(**kwargs):
        assert kwargs["cross_owner_run"].id == ids["probe"]
        assert kwargs["owner_baseline_run"].id == ids["baseline"]
        assert kwargs["test_case"].id == ids["probe_case"]
        assert kwargs["resource"].id == ids["resource"]
        return analyze_bola_run(**kwargs)

    monkeypatch.setattr("app.services.finding_analysis.analyze_bola_run", exact_analyzer)
    # Fail on even an attempted ownership read, including inside the analyzer.
    monkeypatch.setattr(Resource, "owner_identity_id", property(
        lambda self: pytest.fail("analysis consulted legacy ownership")
    ))
    response = analyze(ids)
    assert response.status_code == 200
    assert response.json()["outcome"] == "potential_bola"
    finding = response.json()["finding"]
    assert finding["test_run_id"] == ids["probe"]
    assert finding["baseline_test_run_id"] == ids["baseline"]
    assert finding["confidence"] == 0.99
    stored = findings(ids)
    assert len(stored) == 1
    assert stored[0]["test_run_id"] == ids["probe"]
    assert stored[0]["baseline_test_run_id"] == ids["baseline"]
    for marker in ("private-resource-marker", "response-secret", "request-secret"):
        assert marker not in str(stored)
        assert marker not in response.text


@pytest.mark.parametrize(("probe_status", "baseline_status", "expected"), [
    (403, 200, "pass"), (500, 200, "inconclusive"), (200, 403, "inconclusive"),
])
def test_pass_and_inconclusive_create_no_finding(evidence_pair, probe_status, baseline_status, expected):
    ids = evidence_pair
    with SessionLocal() as db:
        db.get(StoredRun, ids["probe"]).response_status = probe_status
        db.get(StoredRun, ids["baseline"]).response_status = baseline_status
        db.commit()
    response = analyze(ids)
    assert response.status_code == 200
    assert response.json()["outcome"] == expected
    assert response.json()["finding"] is None
    assert findings(ids) == []


def test_non_bola_probe_remains_inconclusive(evidence_pair):
    response = analyze({**evidence_pair, "probe": evidence_pair["baseline"]}, 999999999)
    assert response.status_code == 200
    assert response.json()["outcome"] == "inconclusive"
    assert response.json()["finding"] is None
    assert findings(evidence_pair) == []


@pytest.mark.parametrize("legacy", [False, True])
def test_existing_different_or_legacy_binding_fails_closed(evidence_pair, legacy):
    ids = evidence_pair
    if legacy:
        with SessionLocal() as db:
            db.add(Finding(target_id=ids["target"], endpoint_id=ids["endpoint"],
                           test_run_id=ids["probe"], category="BOLA", severity="high",
                           confidence=0.9, status="confirmed", title="legacy title",
                           description="legacy description", review_notes="keep review"))
            db.commit()
        response = client.get(f"/api/targets/{ids['target']}/findings")
        assert response.status_code == 200
        assert response.json()[0]["baseline_test_run_id"] is None
    else:
        assert analyze(ids).status_code == 200
    before = findings(ids)
    # The decoy would be INCONCLUSIVE: conflicting provenance must still fail closed.
    response = analyze(ids, ids["baseline"] if legacy else ids["decoy"])
    assert response.status_code == 409
    assert response.json()["detail"] == "finding_evidence_pair_conflict"
    assert findings(ids) == before


@pytest.mark.parametrize("terminal", ["confirmed", "false_positive"])
def test_same_pair_retry_preserves_finding_review(evidence_pair, terminal):
    ids = evidence_pair
    first = analyze(ids).json()["finding"]
    assert analyze(ids).json()["finding"]["id"] == first["id"]
    for state in ("reviewing", terminal):
        response = client.patch(f"/api/findings/{first['id']}/review", json={
            "status": state, "review_notes": "human review",
        })
        assert response.status_code == 200
        assert response.json()["baseline_test_run_id"] == ids["baseline"]
    assert client.patch(f"/api/findings/{first['id']}/review", json={
        "status": "reviewing",
    }).status_code == 409
    retry = analyze(ids).json()["finding"]
    assert retry["id"] == first["id"]
    assert retry["status"] == terminal
    assert retry["review_notes"] == "human review"
    assert len(findings(ids)) == 1


@pytest.mark.parametrize("conflicting", [False, True])
def test_concurrent_pairs_use_unique_constraint(evidence_pair, conflicting):
    ids = evidence_pair
    with SessionLocal() as db:
        db.get(StoredRun, ids["decoy"]).response_status = 200
        db.commit()
    ready = Barrier(2)

    class RacingSession(Session):
        def scalar(self, statement, *args, **kwargs):
            if isinstance(statement, Insert) and statement.table.name == "findings":
                ready.wait(timeout=10)
            return super().scalar(statement, *args, **kwargs)

    sessions = sessionmaker(bind=engine, class_=RacingSession, expire_on_commit=False)

    def override_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        baselines = [ids["baseline"], ids["decoy"] if conflicting else ids["baseline"]]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda baseline: analyze(ids, baseline), baselines))
    finally:
        app.dependency_overrides.pop(get_db, None)
    stored = findings(ids)
    assert len(stored) == 1
    for baseline, response in zip(baselines, responses, strict=True):
        if response.status_code == 200:
            assert response.json()["finding"]["baseline_test_run_id"] == baseline
            assert response.json()["finding"]["test_run_id"] == ids["probe"]
    if conflicting:
        assert sorted(response.status_code for response in responses) == [200, 409]
        winner = next(response.json()["finding"] for response in responses if response.status_code == 200)
        loser = next(response for response in responses if response.status_code == 409)
        assert loser.json()["detail"] == "finding_evidence_pair_conflict"
        assert stored[0]["baseline_test_run_id"] == winner["baseline_test_run_id"]
    else:
        assert [response.status_code for response in responses] == [200, 200]
        assert responses[0].json()["finding"]["id"] == responses[1].json()["finding"]["id"]
        assert stored[0]["baseline_test_run_id"] == ids["baseline"]


def test_missing_baseline_case_fails_closed(evidence_pair):
    ids = evidence_pair
    with SessionLocal() as db:
        real_get = db.get

        def get(model, identifier, *args, **kwargs):
            if model is StoredCase and identifier == ids["baseline_case"]:
                return None
            return real_get(model, identifier, *args, **kwargs)

        db.get = Mock(side_effect=get)
        with pytest.raises(FindingAnalysisError, match="^finding_evidence_pair_invalid$"):
            FindingAnalysisService(db=db).analyze_test_run(
                test_run_id=ids["probe"], baseline_test_run_id=ids["baseline"])
    assert findings(ids) == []


@pytest.mark.parametrize("network_mode", ["private_local", "external_public_authorized"])
def test_analysis_has_zero_execution_network_ai_or_authority_side_effects(evidence_pair, monkeypatch, network_mode):
    ids = evidence_pair
    with SessionLocal() as db:
        db.get(Target, ids["target"]).network_mode = network_mode
        db.commit()

    def snapshot():
        with engine.connect() as db:
            return {table.name: list(db.execute(select(table).order_by(*table.primary_key.columns)).mappings())
                    for table in Base.metadata.sorted_tables if table.name != "findings"}

    before = snapshot()
    config_before = settings.model_dump()

    def prohibited(*args, **kwargs):
        raise AssertionError("analysis crossed execution/network/AI boundary")

    for path in (
        "socket.getaddrinfo", "socket.create_connection", "socket.socket.connect",
        "socket.socket.connect_ex", "httpcore.ConnectionPool.stream",
        "app.network_safety.gateway.NetworkGateway.request",
        "app.services.test_execution.TestExecutionService.execute",
        "app.executors.http.PolicyEnforcedHTTPExecutor.execute",
        "app.generators.bola.generate_bola_test_cases",
        "app.scanners.openapi.OpenAPIScanner.scan",
        "app.services.ai_analysis.AIAnalysisService.analyze_finding",
        "app.ai.mock_provider.MockAIProvider.analyze",
    ):
        monkeypatch.setattr(path, prohibited)
    response = analyze(ids)
    assert response.status_code == 200
    assert response.json()["outcome"] == "potential_bola"
    assert snapshot() == before
    assert settings.model_dump() == config_before
