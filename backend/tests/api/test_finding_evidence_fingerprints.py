from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql.dml import Insert
from sqlalchemy.orm import Session, sessionmaker

from app.analyzers.bola import analyze_bola_run
from app.db.models import (
    Finding, FindingEvidenceRecord, FindingEvidenceExcerpt, FindingEvidenceFingerprint,
    Resource, TestCase as StoredCase, TestRun as StoredRun,
)
from app.db.session import SessionLocal, engine
from app.main import app
from app.services.finding_analysis import FindingAnalysisError, FindingAnalysisService
from tests.api.test_finding_structured_evidence import analyze, client, evidence_rows, old_finding
from tests.api.test_finding_evidence_excerpts import excerpt_rows, structured_only
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401


FIELDS = {"algorithm", "fingerprint_version", "baseline_digest", "probe_digest",
          "baseline_body_bytes", "probe_body_bytes"}


def fingerprint_rows(ids):
    with engine.connect() as db:
        return list(db.execute(select(FindingEvidenceFingerprint.__table__).where(
            FindingEvidenceFingerprint.finding_evidence_record_id.in_(select(FindingEvidenceRecord.id).where(
                FindingEvidenceRecord.finding_id.in_(select(Finding.id).where(Finding.target_id == ids["target"])))))
        ).mappings())


def snapshot():
    with engine.connect() as db:
        return {model.__tablename__: list(db.execute(select(model.__table__).order_by(model.id)).mappings())
                for model in (Finding, FindingEvidenceRecord, FindingEvidenceExcerpt, FindingEvidenceFingerprint, StoredRun)}


def typed_result(ids):
    with SessionLocal() as db:
        return analyze_bola_run(test_case=db.get(StoredCase, ids["probe_case"]),
            resource=db.get(Resource, ids["resource"]), cross_owner_run=db.get(StoredRun, ids["probe"]),
            owner_baseline_run=db.get(StoredRun, ids["baseline"]))


def old_evidence(ids):
    finding_id, evidence_id = structured_only(ids)
    with SessionLocal() as db:
        db.add(FindingEvidenceExcerpt(finding_evidence_record_id=evidence_id,
            extractor_id="bola_matched_identifier_field", extractor_version="1",
            baseline_excerpt='{"id":"[MATCHED_RESOURCE_IDENTIFIER]"}',
            probe_excerpt='{"id":"[MATCHED_RESOURCE_IDENTIFIER]"}'))
        db.commit()
    return finding_id, evidence_id


def test_atomic_exact_pair_and_allowlisted_read(evidence_pair):
    ids = evidence_pair
    baseline = '{"id":"private-resource-marker","secret":"private-token","name":"界😀"}'
    probe = ' {"name":"界😀","secret":"private-token","id":"private-resource-marker"} '
    with SessionLocal() as db:
        db.get(StoredRun, ids["baseline"]).response_body = baseline
        db.get(StoredRun, ids["probe"]).response_body = probe
        db.commit()
    before = datetime.now(timezone.utc)
    response = analyze(ids)
    after = datetime.now(timezone.utc)
    assert response.status_code == 200
    assert response.json()["outcome"] == "potential_bola"
    assert response.json()["confidence"] == .99  # JSON equality is independent of raw digest equality.
    row, = fingerprint_rows(ids)
    evidence, = evidence_rows(ids)
    excerpt, = excerpt_rows(ids)
    assert evidence["finding_id"] == response.json()["finding"]["id"]
    assert evidence["baseline_test_run_id"] == ids["baseline"]
    assert evidence["probe_test_run_id"] == ids["probe"]
    assert row == {
        "id": row["id"], "finding_evidence_record_id": evidence["id"],
        "algorithm": "sha256", "fingerprint_version": "1",
        "baseline_digest": hashlib.sha256(baseline.encode("utf-8")).hexdigest(),
        "probe_digest": hashlib.sha256(probe.encode("utf-8")).hexdigest(),
        "baseline_body_bytes": len(baseline.encode("utf-8")),
        "probe_body_bytes": len(probe.encode("utf-8")), "created_at": row["created_at"],
    }
    assert row["baseline_body_bytes"] > len(baseline)
    assert row["baseline_digest"] != row["probe_digest"]
    assert excerpt["finding_evidence_record_id"] == evidence["id"]
    assert before <= row["created_at"] <= after
    read = client.get(f"/api/findings/{evidence['finding_id']}/evidence/fingerprints")
    assert read.status_code == 200
    body = read.json()
    assert set(body) == set(row)
    assert datetime.fromisoformat(body.pop("created_at")) == row["created_at"]
    assert body == {k: v for k, v in row.items() if k != "created_at"}
    for marker in ("private-resource-marker", "private-token", "request-secret", "界", "😀",
                   "response_body", "request_data", "Authorization", "headers", "credentials", "excerpt"):
        assert marker not in read.text
        assert marker not in str(row)


def test_exact_404s_no_backfill_on_read_and_no_global_listing(evidence_pair):
    missing = client.get("/api/findings/999999999/evidence/fingerprints")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Finding not found."
    ids = evidence_pair
    finding_id = old_finding(ids)
    path = f"/api/findings/{finding_id}/evidence/fingerprints"
    missing = client.get(path)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "finding_structured_evidence_not_found"
    # Use the existing M13-02 facts without executing analysis.
    from tests.api.test_finding_structured_evidence import FACTS
    with SessionLocal() as db:
        db.add(FindingEvidenceRecord(**FACTS, finding_id=finding_id,
            baseline_test_run_id=ids["baseline"], probe_test_run_id=ids["probe"]))
        db.commit()
    before = snapshot()
    missing = client.get(path)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "finding_evidence_fingerprint_not_found"
    assert snapshot() == before
    assert {p for p in app.openapi()["paths"] if "fingerprint" in p} == {
        "/api/findings/{finding_id}/evidence/fingerprints"}
    for p in ("/api/fingerprints", "/api/evidence/fingerprints", "/api/finding-evidence-fingerprints"):
        assert client.get(p).status_code == 404
    for method in (client.post, client.patch, client.delete):
        assert method(path).status_code == 405


def test_explicit_same_pair_appends_once_preserving_old_evidence_and_review(evidence_pair):
    ids = evidence_pair
    finding_id, evidence_id = old_evidence(ids)
    before = snapshot()
    assert fingerprint_rows(ids) == []
    assert analyze({**ids, "baseline": ids["decoy"]}).status_code == 409
    assert snapshot() == before
    for _ in range(3):
        response = analyze(ids)
        assert response.status_code == 200
        assert response.json()["finding"]["id"] == finding_id
        assert response.json()["finding"]["status"] == "confirmed"
        assert response.json()["finding"]["review_notes"] == "keep human review"
        if _ == 0:
            original = fingerprint_rows(ids)
        assert fingerprint_rows(ids) == original
        assert len(original) == 1
        assert original[0]["finding_evidence_record_id"] == evidence_id
        after = snapshot()
        for table in ("finding_evidence_records", "finding_evidence_excerpts", "test_runs"):
            assert after[table] == before[table]


@pytest.mark.parametrize(("status", "body", "outcome"), [(403, None, "pass"), (500, None, "inconclusive"),
    (200, "not JSON", "inconclusive"), (200, '{"ok":true}', "inconclusive")])
@pytest.mark.parametrize("existing", [False, True])
def test_non_potential_never_persists_fingerprint(evidence_pair, status, body, outcome, existing):
    ids = evidence_pair
    if existing:
        old_evidence(ids)
    with SessionLocal() as db:
        run = db.get(StoredRun, ids["probe"])
        run.response_status, run.response_body = status, body
        db.commit()
    before = snapshot()
    response = analyze(ids)
    assert response.status_code == 200
    assert response.json()["outcome"] == outcome
    assert snapshot() == before
    assert fingerprint_rows(ids) == []


@pytest.mark.parametrize("field", sorted(FIELDS))
def test_material_fingerprint_conflict_is_non_mutating(evidence_pair, monkeypatch, field):
    ids = evidence_pair
    old_evidence(ids)
    assert analyze(ids).status_code == 200
    result = typed_result(ids)
    fingerprint = result.fingerprint_evidence
    if field in ("algorithm", "fingerprint_version"):
        # DB constraints prohibit invalid stored metadata; test a differing typed producer.
        changed = replace(fingerprint, **{field: "other"})
        monkeypatch.setattr("app.services.finding_analysis.analyze_bola_run",
                            lambda **kwargs: replace(result, fingerprint_evidence=changed))
    else:
        with SessionLocal() as db:
            row = db.get(FindingEvidenceFingerprint, fingerprint_rows(ids)[0]["id"])
            setattr(row, field, "0" * 64 if field.endswith("digest") else getattr(row, field) + 1)
            db.commit()
    before = snapshot()
    response = analyze(ids)
    assert response.status_code == 409
    assert response.json()["detail"] == "finding_evidence_fingerprint_conflict"
    with SessionLocal() as db:
        with pytest.raises(FindingAnalysisError, match="^finding_evidence_fingerprint_conflict$"):
            FindingAnalysisService(db=db).analyze_test_run(test_run_id=ids["probe"], baseline_test_run_id=ids["baseline"])
        db.commit()
    assert snapshot() == before


@pytest.mark.parametrize("side", ["baseline", "probe"])
@pytest.mark.parametrize("changed", [' {"id":"private-resource-marker","secret":"response-secret"}',
    '{"project_id":"private-resource-marker"}', '{"id":"other"}', 'not JSON', None])
def test_changed_source_body_conflicts_even_when_rule_no_longer_succeeds(evidence_pair, side, changed):
    ids = evidence_pair
    assert analyze(ids).status_code == 200
    with SessionLocal() as db:
        db.get(StoredRun, ids[side]).response_body = changed
        db.commit()
    before = snapshot()
    response = analyze(ids)
    assert response.status_code == 409
    assert response.json()["detail"] == "finding_evidence_fingerprint_conflict"
    assert snapshot() == before


@pytest.mark.parametrize("table", ["finding_evidence_records", "finding_evidence_excerpts", "finding_evidence_fingerprints"])
@pytest.mark.parametrize("after_insert", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_all_four_rows_roll_back_on_persistence_failure(evidence_pair, table, after_insert, existing):
    ids = evidence_pair
    if existing:
        old_finding(ids)
    before = snapshot()
    class FailingSession(Session):
        def scalar(self, statement, *args, **kwargs):
            if isinstance(statement, Insert) and statement.table.name == table:
                if after_insert:
                    super().scalar(statement, *args, **kwargs)
                raise RuntimeError("synthetic persistence failure")
            return super().scalar(statement, *args, **kwargs)
    with FailingSession(engine) as db:
        with pytest.raises(RuntimeError, match="synthetic persistence failure"):
            FindingAnalysisService(db=db).analyze_test_run(test_run_id=ids["probe"], baseline_test_run_id=ids["baseline"])
        db.commit()
    assert snapshot() == before


@pytest.mark.parametrize("existing", [False, True])
def test_real_concurrent_identical_fingerprints_converge(evidence_pair, existing):
    ids = evidence_pair
    if existing:
        old_evidence(ids)
    ready = Barrier(2)
    table = "finding_evidence_fingerprints" if existing else "findings"
    class RacingSession(Session):
        def scalar(self, statement, *args, **kwargs):
            if isinstance(statement, Insert) and statement.table.name == table:
                ready.wait(timeout=10)
            return super().scalar(statement, *args, **kwargs)
    sessions = sessionmaker(bind=engine, class_=RacingSession, expire_on_commit=False)
    def worker(_):
        with sessions() as db:
            return FindingAnalysisService(db=db).analyze_test_run(
                test_run_id=ids["probe"], baseline_test_run_id=ids["baseline"]).finding.id
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids_returned = list(pool.map(worker, range(2)))
    assert ids_returned[0] == ids_returned[1]
    original = fingerprint_rows(ids)
    assert len(original) == len(evidence_rows(ids)) == len(excerpt_rows(ids)) == 1
    assert analyze(ids).status_code == 200
    assert fingerprint_rows(ids) == original


def test_service_copies_typed_fingerprint_without_hashing_body_reads_or_alternate_lookup(evidence_pair, monkeypatch):
    ids = evidence_pair
    finding_id, evidence_id = old_evidence(ids)
    result = typed_result(ids)
    selected = replace(result.fingerprint_evidence, baseline_digest="c" * 64, probe_digest="d" * 64,
                       baseline_body_bytes=7, probe_body_bytes=11)
    monkeypatch.setattr("app.services.finding_analysis.analyze_bola_run",
                        lambda **kwargs: replace(result, fingerprint_evidence=selected))
    def prohibited(*args, **kwargs):
        pytest.fail("service read or hashed fingerprint input")
    for field in ("response_body", "request_data"):
        monkeypatch.setattr(StoredRun, field, property(prohibited))
    monkeypatch.setattr(Resource, "external_id", property(prohibited))
    monkeypatch.setattr(hashlib, "sha256", prohibited)
    monkeypatch.setattr("app.analyzers.bola.fingerprint_response_pair", prohibited)
    class ExactSession(Session):
        exact_run_lookup = False
        def get(self, entity, ident, *args, **kwargs):
            if entity is StoredRun:
                assert ident in (ids["baseline"], ids["probe"])
                self.exact_run_lookup = True
            try:
                return super().get(entity, ident, *args, **kwargs)
            finally:
                self.exact_run_lookup = False
        def _execute_internal(self, statement, *args, **kwargs):
            if getattr(statement, "is_select", False):
                tables = statement.get_final_froms()
                if StoredRun.__table__ in tables:
                    assert self.exact_run_lookup
                for model, column, expected in (
                    (FindingEvidenceRecord, "finding_id", finding_id),
                    (FindingEvidenceExcerpt, "finding_evidence_record_id", evidence_id),
                    (FindingEvidenceFingerprint, "finding_evidence_record_id", evidence_id),
                ):
                    if model.__table__ in tables:
                        assert len(tables) == 1
                        assert statement.whereclause.left.name == column
                        assert statement.whereclause.right.value == expected
            return super()._execute_internal(statement, *args, **kwargs)
    with ExactSession(engine) as db:
        result = FindingAnalysisService(db=db).analyze_test_run(test_run_id=ids["probe"], baseline_test_run_id=ids["baseline"])
        assert result.finding.id == finding_id
    row, = fingerprint_rows(ids)
    assert {k: row[k] for k in FIELDS} == asdict(selected)


@pytest.mark.parametrize("invalid", [None, {}])
def test_missing_typed_fingerprint_fails_without_partial_state(evidence_pair, monkeypatch, invalid):
    ids = evidence_pair
    result = typed_result(ids)
    monkeypatch.setattr("app.services.finding_analysis.analyze_bola_run",
                        lambda **kwargs: replace(result, fingerprint_evidence=invalid))
    before = snapshot()
    response = analyze(ids)
    assert response.status_code == 409
    assert response.json()["detail"] == "finding_evidence_fingerprint_conflict"
    assert snapshot() == before
