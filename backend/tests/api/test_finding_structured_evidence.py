from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from threading import Barrier

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql.dml import Insert
from sqlalchemy.orm import Session, sessionmaker

from app.analyzers.bola import analyze_bola_run
from app.db.models import Finding, Resource, TestCase as StoredCase, TestRun as StoredRun
from app.db.session import SessionLocal, engine, get_db
from app.main import app
from app.services.finding_analysis import FindingAnalysisService
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401


client = TestClient(app)
FACTS = {
    "evidence_type": "bola_resource_identifier_pair",
    "rule_id": "bola_resource_identifier_presence",
    "rule_version": "1",
    "reason_code": "baseline_and_probe_contain_target_resource_identifier",
    "baseline_status_code": 200,
    "probe_status_code": 200,
    "baseline_resource_identifier_present": True,
    "probe_resource_identifier_present": True,
}


def analyze(ids):
    return client.post(f"/api/test-runs/{ids['probe']}/analyze", json={
        "baseline_test_run_id": ids["baseline"],
    })


def evidence_rows(ids):
    with engine.connect() as db:
        return list(db.execute(text("""
            SELECT * FROM finding_evidence_records
            WHERE finding_id IN (SELECT id FROM findings WHERE target_id = :target)
            ORDER BY id
        """), ids).mappings())


def old_finding(ids):
    with SessionLocal() as db:
        finding = Finding(target_id=ids["target"], endpoint_id=ids["endpoint"],
                          test_run_id=ids["probe"], baseline_test_run_id=ids["baseline"],
                          category="BOLA", severity="high", confidence=0.99,
                          status="confirmed", review_notes="keep human review",
                          title="M13-01 finding", description="original description")
        db.add(finding)
        db.commit()
        return finding.id


def test_exact_bounded_record_and_read(evidence_pair):
    ids = evidence_pair
    with SessionLocal() as db:
        db.get(StoredRun, ids["baseline"]).response_status = 201
        db.get(StoredRun, ids["probe"]).response_status = 202
        db.commit()
    before = datetime.now(timezone.utc)
    response = analyze(ids)
    after = datetime.now(timezone.utc)
    assert response.status_code == 200
    finding = response.json()["finding"]
    rows = evidence_rows(ids)
    assert len(rows) == 1
    row = dict(rows[0])
    assert row == {
        **FACTS, "baseline_status_code": 201, "probe_status_code": 202,
        "id": row["id"], "finding_id": finding["id"],
        "probe_test_run_id": finding["test_run_id"],
        "baseline_test_run_id": finding["baseline_test_run_id"],
        "created_at": row["created_at"],
    }
    assert row["probe_test_run_id"] == ids["probe"]
    assert row["baseline_test_run_id"] == ids["baseline"]
    assert before <= row["created_at"] <= after
    result = client.get(f"/api/findings/{finding['id']}/evidence")
    assert result.status_code == 200
    body = result.json()
    assert set(body) == set(row)
    assert datetime.fromisoformat(body.pop("created_at")) == row.pop("created_at")
    assert body == row
    for marker in ("private-resource-marker", "response-secret", "request-secret",
                   "Authorization", "headers", "request_data", "response_body"):
        assert marker not in result.text
        assert marker not in str(rows)
    assert response.json()["reason"] not in str(rows)


def test_missing_exact_read_and_no_listing(evidence_pair):
    response = client.get("/api/findings/999999999/evidence")
    assert response.status_code == 404
    assert response.json()["detail"] == "Finding not found."
    finding_id = old_finding(evidence_pair)
    response = client.get(f"/api/findings/{finding_id}/evidence")
    assert response.status_code == 404
    assert response.json()["detail"] == "finding_structured_evidence_not_found"
    paths = {path for path in app.openapi()["paths"] if "evidence" in path}
    assert paths == {"/api/findings/{finding_id}/evidence",
                     "/api/findings/{finding_id}/evidence/excerpts",
                     "/api/findings/{finding_id}/evidence/fingerprints",
                     "/api/findings/{finding_id}/evidence/similarity"}
    for path in ("/api/evidence", "/api/findings/evidence", "/api/finding-evidence-records"):
        assert client.get(path).status_code == 404


def test_reanalysis_appends_once_and_preserves_review(evidence_pair):
    ids = evidence_pair
    finding_id = old_finding(ids)
    assert evidence_rows(ids) == []
    first = analyze(ids)
    assert first.status_code == 200
    original = evidence_rows(ids)
    assert len(original) == 1
    assert first.json()["finding"]["id"] == finding_id
    for _ in range(2):
        response = analyze(ids)
        assert response.status_code == 200
        assert response.json()["finding"]["status"] == "confirmed"
        assert response.json()["finding"]["review_notes"] == "keep human review"
        assert evidence_rows(ids) == original
    path = f"/api/findings/{finding_id}/evidence"
    assert client.patch(path, json={"rule_version": "2"}).status_code == 405
    assert client.delete(path).status_code == 405
    assert evidence_rows(ids) == original


@pytest.mark.parametrize(("status", "outcome"), [(403, "pass"), (500, "inconclusive")])
def test_non_potential_results_do_not_persist_evidence(evidence_pair, status, outcome):
    ids = evidence_pair
    with SessionLocal() as db:
        db.get(StoredRun, ids["probe"]).response_status = status
        db.commit()
    response = analyze(ids)
    assert response.status_code == 200
    assert response.json()["outcome"] == outcome
    assert response.json()["finding"] is None
    assert evidence_rows(ids) == []


def test_large_body_does_not_change_evidence_shape_or_size(evidence_pair):
    ids = evidence_pair
    first = analyze(ids)
    assert first.status_code == 200
    original = evidence_rows(ids)
    assert len(original) == 1
    with engine.connect() as db:
        size_before = db.scalar(text("SELECT pg_column_size(e) FROM finding_evidence_records e WHERE id = :id"), original[0])
    with SessionLocal() as db:
        body = json.dumps({"id": "private-resource-marker", "secret": "sensitive-padding" * 100000})
        for run_id in (ids["baseline"], ids["probe"]):
            db.get(StoredRun, run_id).response_body = body
        db.commit()
    # M13-04 detects changed source bytes; immutable M13-02 rows stay intact.
    changed = analyze(ids)
    assert changed.status_code == 409
    assert changed.json()["detail"] == "finding_evidence_fingerprint_conflict"
    assert evidence_rows(ids) == original
    with engine.connect() as db:
        assert db.scalar(text("SELECT pg_column_size(e) FROM finding_evidence_records e WHERE id = :id"), original[0]) == size_before
    assert "sensitive-padding" not in client.get(f"/api/findings/{first.json()['finding']['id']}/evidence").text

    # A fresh Finding from the large body must also have the same storage size.
    with SessionLocal() as db:
        probe = StoredRun(test_case_id=ids["probe_case"], request_data={},
                          response_status=200, response_body=body)
        db.add(probe)
        db.commit()
        large_ids = {**ids, "probe": probe.id}
    created = analyze(large_ids)
    assert created.status_code == 200
    finding_id = created.json()["finding"]["id"]
    large_row = next(row for row in evidence_rows(ids) if row["finding_id"] == finding_id)
    assert set(large_row) == set(original[0])
    assert {key: large_row[key] for key in FACTS} == FACTS
    with engine.connect() as db:
        assert db.scalar(text("SELECT pg_column_size(e) FROM finding_evidence_records e WHERE id = :id"), large_row) == size_before
    output = client.get(f"/api/findings/{finding_id}/evidence")
    assert output.status_code == 200
    assert "sensitive-padding" not in output.text
    assert "private-resource-marker" not in output.text


def test_service_uses_analyzer_facts_without_reading_bodies_or_alternate_runs(evidence_pair, monkeypatch):
    ids = evidence_pair
    with SessionLocal() as db:
        result = analyze_bola_run(
            test_case=db.get(StoredCase, ids["probe_case"]),
            resource=db.get(Resource, ids["resource"]),
            cross_owner_run=db.get(StoredRun, ids["probe"]),
            owner_baseline_run=db.get(StoredRun, ids["baseline"]),
        )
    assert result.evidence is not None
    # Distinct synthetic facts prove the service copies the typed boundary rather
    # than reconstructing status codes or match flags from the persisted runs.
    selected = replace(result.evidence, baseline_status_code=206, probe_status_code=203,
                       baseline_resource_identifier_present=False,
                       probe_resource_identifier_present=False)

    def exact_analyzer(**kwargs):
        assert kwargs["cross_owner_run"].id == ids["probe"]
        assert kwargs["owner_baseline_run"].id == ids["baseline"]
        return replace(result, evidence=selected)

    monkeypatch.setattr("app.services.finding_analysis.analyze_bola_run", exact_analyzer)
    monkeypatch.setattr(StoredRun, "response_body", property(
        lambda self: pytest.fail("service read a response body")))
    monkeypatch.setattr(StoredRun, "request_data", property(
        lambda self: pytest.fail("service read request data")))

    class ExactRunSession(Session):
        exact_run_lookup = False

        def get(self, entity, ident, *args, **kwargs):
            if entity is StoredRun:
                assert ident in (ids["probe"], ids["baseline"])
                self.exact_run_lookup = True
            try:
                return super().get(entity, ident, *args, **kwargs)
            finally:
                self.exact_run_lookup = False

        def _execute_internal(self, statement, *args, **kwargs):
            if getattr(statement, "is_select", False):
                if StoredRun.__table__ in statement.get_final_froms():
                    assert self.exact_run_lookup, "alternate run selection"
            return super()._execute_internal(statement, *args, **kwargs)

    with ExactRunSession(engine) as db:
        outcome = FindingAnalysisService(db=db).analyze_test_run(
            test_run_id=ids["probe"], baseline_test_run_id=ids["baseline"])
        assert outcome.finding is not None
    row = evidence_rows(ids)[0]
    assert {key: row[key] for key in asdict(selected)} == asdict(selected)


@pytest.mark.parametrize(("field", "value"), [
    ("evidence_type", "other"), ("rule_id", "other"), ("rule_version", "2"),
    ("reason_code", "other"), ("baseline_status_code", 201), ("probe_status_code", 202),
    ("baseline_resource_identifier_present", False), ("probe_resource_identifier_present", False),
    ("baseline_test_run_id", "decoy"), ("probe_test_run_id", "decoy"),
])
def test_conflicting_evidence_is_never_overwritten(evidence_pair, field, value):
    ids = evidence_pair
    finding_id = old_finding(ids)
    from app.db.models.finding_evidence_record import FindingEvidenceRecord
    facts = {**FACTS, "finding_id": finding_id, "probe_test_run_id": ids["probe"],
             "baseline_test_run_id": ids["baseline"], field: ids[value] if value == "decoy" else value}
    with SessionLocal() as db:
        db.add(FindingEvidenceRecord(**facts))
        db.commit()
        finding_before = dict(db.execute(select(Finding.__table__).where(Finding.id == finding_id)).mappings().one())
    before = evidence_rows(ids)
    response = analyze(ids)
    assert response.status_code == 409
    assert response.json()["detail"] == "finding_structured_evidence_conflict"
    assert evidence_rows(ids) == before
    with SessionLocal() as db:
        assert dict(db.execute(select(Finding.__table__).where(Finding.id == finding_id)).mappings().one()) == finding_before


@pytest.mark.parametrize("existing", [False, True])
def test_real_concurrent_identical_evidence_converges(evidence_pair, existing):
    ids = evidence_pair
    if existing:
        old_finding(ids)
    ready = Barrier(2)
    table_name = "finding_evidence_records" if existing else "findings"

    class RacingSession(Session):
        def scalar(self, statement, *args, **kwargs):
            if isinstance(statement, Insert) and statement.table.name == table_name:
                ready.wait(timeout=10)
            return super().scalar(statement, *args, **kwargs)

    sessions = sessionmaker(bind=engine, class_=RacingSession, expire_on_commit=False)

    def override_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: analyze(ids), range(2)))
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json()["finding"]["id"] == responses[1].json()["finding"]["id"]
    original = evidence_rows(ids)
    assert len(original) == 1
    assert analyze(ids).status_code == 200
    assert evidence_rows(ids) == original


def test_evidence_insert_failure_leaves_no_orphan_finding(evidence_pair):
    class FailingSession(Session):
        def scalar(self, statement, *args, **kwargs):
            if isinstance(statement, Insert) and statement.table.name == "finding_evidence_records":
                raise RuntimeError("synthetic evidence persistence failure")
            return super().scalar(statement, *args, **kwargs)

    def override_db():
        with FailingSession(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with pytest.raises(RuntimeError, match="synthetic evidence persistence failure"):
            analyze(evidence_pair)
    finally:
        app.dependency_overrides.pop(get_db, None)
    with SessionLocal() as db:
        assert list(db.scalars(select(Finding.id).where(Finding.test_run_id == evidence_pair["probe"]))) == []
    assert evidence_rows(evidence_pair) == []
