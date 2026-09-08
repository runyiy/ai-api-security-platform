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
    Finding, FindingEvidenceRecord, FindingEvidenceExcerpt, FindingEvidenceFingerprint, FindingEvidenceSimilarity,
    Resource, TestCase as StoredCase, TestRun as StoredRun,
)
from app.db.session import SessionLocal, engine
from app.main import app
from app.services.finding_analysis import FindingAnalysisError, FindingAnalysisService
from tests.api.test_finding_structured_evidence import analyze, client, evidence_rows, old_finding
from tests.api.test_finding_evidence_excerpts import excerpt_rows
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401


from tests.api.test_finding_evidence_fingerprints import fingerprint_rows, old_evidence, typed_result

FIELDS = {"comparator_id", "comparator_version", "exact_digest_match", "length_similarity_bps"}


def similarity_rows(ids):
    fingerprint_ids = [row["id"] for row in fingerprint_rows(ids)]
    with engine.connect() as db:
        return list(db.execute(select(FindingEvidenceSimilarity.__table__).where(
            FindingEvidenceSimilarity.finding_evidence_fingerprint_id.in_(fingerprint_ids))).mappings())


def snapshot():
    with engine.connect() as db:
        return {model.__tablename__: list(db.execute(select(model.__table__).order_by(model.id)).mappings())
                for model in (Finding, FindingEvidenceRecord, FindingEvidenceExcerpt,
                              FindingEvidenceFingerprint, FindingEvidenceSimilarity, StoredRun)}


def old_fingerprint(ids):
    finding_id, evidence_id = old_evidence(ids)
    with SessionLocal() as db:
        row = FindingEvidenceFingerprint(finding_evidence_record_id=evidence_id,
            **asdict(typed_result(ids).fingerprint_evidence))
        db.add(row)
        db.commit()
        return finding_id, row.id


@pytest.mark.parametrize("probe", [
    '{"id":"private-resource-marker","secret":"response-secret"}',
    ' {"id":"private-resource-marker","secret":"response-secret"} ',
    '{"id":"private-resource-marker","padding":"' + 'x' * 100000 + '"}',
], ids=["equal", "different_digest", "low_ratio"])
def test_atomic_exact_linkage_and_allowlisted_read(evidence_pair, probe):
    ids = evidence_pair
    with SessionLocal() as db:
        db.get(StoredRun, ids["baseline"]).response_body = '{"id":"private-resource-marker","secret":"response-secret"}'
        db.get(StoredRun, ids["probe"]).response_body = probe
        db.commit()
    expected = typed_result(ids)
    before = datetime.now(timezone.utc)
    response = analyze(ids)
    after = datetime.now(timezone.utc)
    assert response.status_code == 200
    assert response.json()["outcome"] == "potential_bola"
    assert response.json()["confidence"] == expected.confidence
    assert response.json()["severity"] == expected.severity
    row, = similarity_rows(ids)
    fingerprint, = fingerprint_rows(ids)
    evidence, = evidence_rows(ids)
    excerpt, = excerpt_rows(ids)
    assert evidence["finding_id"] == response.json()["finding"]["id"]
    assert evidence["baseline_test_run_id"] == ids["baseline"]
    assert evidence["probe_test_run_id"] == ids["probe"]
    assert fingerprint["finding_evidence_record_id"] == excerpt["finding_evidence_record_id"] == evidence["id"]
    assert {k: fingerprint[k] for k in asdict(expected.fingerprint_evidence)} == asdict(expected.fingerprint_evidence)
    assert {k: excerpt[k] for k in asdict(expected.excerpt_evidence)} == asdict(expected.excerpt_evidence)
    assert row == {"id": row["id"], "finding_evidence_fingerprint_id": fingerprint["id"],
                   **asdict(expected.similarity_evidence), "created_at": row["created_at"]}
    assert before <= row["created_at"] <= after
    read = client.get(f"/api/findings/{evidence['finding_id']}/evidence/similarity")
    assert read.status_code == 200
    body = read.json()
    assert set(body) == set(row) == {"id", "finding_evidence_fingerprint_id", *FIELDS, "created_at"}
    assert datetime.fromisoformat(body.pop("created_at")) == row["created_at"]
    assert body == {k: v for k, v in row.items() if k != "created_at"}
    assert type(body["length_similarity_bps"]) is int
    for marker in ("private-resource-marker", "response-secret", "request-secret", "padding",
                   "response_body", "request_data", "Authorization", "headers", "credentials", "excerpt",
                   fingerprint["baseline_digest"], fingerprint["probe_digest"]):
        assert marker not in read.text
        assert marker not in str(row)


def test_exact_404s_reads_do_not_append_and_no_global_listing(evidence_pair):
    ids = evidence_pair
    missing = client.get("/api/findings/999999999/evidence/similarity")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Finding not found."
    finding_id = old_finding(ids)
    path = f"/api/findings/{finding_id}/evidence/similarity"
    def missing_without_mutation(detail):
        before = snapshot()
        missing = client.get(path)
        assert missing.status_code == 404
        assert missing.json()["detail"] == detail
        assert snapshot() == before
    missing_without_mutation("finding_structured_evidence_not_found")
    from tests.api.test_finding_structured_evidence import FACTS
    with SessionLocal() as db:
        evidence = FindingEvidenceRecord(**FACTS, finding_id=finding_id,
            baseline_test_run_id=ids["baseline"], probe_test_run_id=ids["probe"])
        db.add(evidence)
        db.commit()
        evidence_id = evidence.id
    missing_without_mutation("finding_evidence_fingerprint_not_found")
    with SessionLocal() as db:
        db.add(FindingEvidenceFingerprint(finding_evidence_record_id=evidence_id,
            **asdict(typed_result(ids).fingerprint_evidence)))
        db.commit()
    missing_without_mutation("finding_evidence_similarity_not_found")
    assert {p for p in app.openapi()["paths"] if "similarity" in p or "similarities" in p} == {
        "/api/findings/{finding_id}/evidence/similarity"}
    for p in ("/api/similarity", "/api/similarities", "/api/evidence/similarity", "/api/finding-evidence-similarities"):
        assert client.get(p).status_code == 404
    for method in (client.post, client.patch, client.delete):
        assert method(path).status_code == 405


def test_explicit_same_pair_appends_once_preserving_old_evidence_and_review(evidence_pair):
    ids = evidence_pair
    finding_id, fingerprint_id = old_fingerprint(ids)
    before = snapshot()
    assert similarity_rows(ids) == []
    assert analyze({**ids, "baseline": ids["decoy"]}).status_code == 409
    assert snapshot() == before
    for _ in range(3):
        response = analyze(ids)
        assert response.status_code == 200
        assert response.json()["finding"]["id"] == finding_id
        assert response.json()["finding"]["status"] == "confirmed"
        assert response.json()["finding"]["review_notes"] == "keep human review"
        if _ == 0:
            original = similarity_rows(ids)
        assert similarity_rows(ids) == original
        assert len(original) == 1
        assert original[0]["finding_evidence_fingerprint_id"] == fingerprint_id
        after = snapshot()
        for table in ("finding_evidence_records", "finding_evidence_excerpts", "finding_evidence_fingerprints", "test_runs"):
            assert after[table] == before[table]


@pytest.mark.parametrize(("status", "body", "outcome"), [(403, None, "pass"), (500, None, "inconclusive"),
    (200, "not JSON", "inconclusive"), (200, '{"ok":true}', "inconclusive")])
@pytest.mark.parametrize("existing", [False, True])
def test_non_potential_never_persists_similarity(evidence_pair, status, body, outcome, existing):
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
    assert similarity_rows(ids) == []


@pytest.mark.parametrize("field", sorted(FIELDS))
def test_material_similarity_conflict_is_non_mutating(evidence_pair, monkeypatch, field):
    ids = evidence_pair
    old_fingerprint(ids)
    assert analyze(ids).status_code == 200
    result = typed_result(ids)
    similarity = result.similarity_evidence
    if field in ("comparator_id", "comparator_version"):
        # DB constraints prohibit invalid stored metadata; test a differing typed producer.
        changed = replace(similarity, **{field: "other"})
        monkeypatch.setattr("app.services.finding_analysis.analyze_bola_run",
                            lambda **kwargs: replace(result, similarity_evidence=changed))
    else:
        with SessionLocal() as db:
            row = db.get(FindingEvidenceSimilarity, similarity_rows(ids)[0]["id"])
            setattr(row, field, not row.exact_digest_match if field == "exact_digest_match" else row.length_similarity_bps - 1)
            db.commit()
    before = snapshot()
    response = analyze(ids)
    assert response.status_code == 409
    assert response.json()["detail"] == "finding_evidence_similarity_conflict"
    with SessionLocal() as db:
        with pytest.raises(FindingAnalysisError, match="^finding_evidence_similarity_conflict$"):
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
        db.get(FindingEvidenceSimilarity, similarity_rows(ids)[0]["id"]).exact_digest_match = False
        db.commit()
    before = snapshot()
    response = analyze(ids)
    assert response.status_code == 409
    assert response.json()["detail"] == "finding_evidence_fingerprint_conflict"
    assert snapshot() == before


@pytest.mark.parametrize("table", ["finding_evidence_records", "finding_evidence_excerpts", "finding_evidence_fingerprints", "finding_evidence_similarities"])
@pytest.mark.parametrize("after_insert", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_all_five_rows_roll_back_on_persistence_failure(evidence_pair, table, after_insert, existing):
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
def test_real_concurrent_identical_similarities_converge(evidence_pair, existing):
    ids = evidence_pair
    if existing:
        old_fingerprint(ids)
    ready = Barrier(2)
    table = "finding_evidence_similarities" if existing else "findings"
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
    original = similarity_rows(ids)
    assert len(original) == len(evidence_rows(ids)) == len(excerpt_rows(ids)) == len(fingerprint_rows(ids)) == 1
    assert analyze(ids).status_code == 200
    assert similarity_rows(ids) == original


def test_service_copies_typed_similarity_without_computing_or_alternate_lookup(evidence_pair, monkeypatch):
    ids = evidence_pair
    finding_id, fingerprint_id = old_fingerprint(ids)
    evidence_id = evidence_rows(ids)[0]["id"]
    result = typed_result(ids)
    selected = replace(result.similarity_evidence, exact_digest_match=False, length_similarity_bps=1234)
    monkeypatch.setattr("app.services.finding_analysis.analyze_bola_run",
                        lambda **kwargs: replace(result, similarity_evidence=selected))
    def prohibited(*args, **kwargs):
        pytest.fail("service accessed input or computed similarity")
    for field in ("response_body", "request_data"):
        monkeypatch.setattr(StoredRun, field, property(prohibited))
    monkeypatch.setattr(Resource, "external_id", property(prohibited))
    monkeypatch.setattr(hashlib, "sha256", prohibited)
    monkeypatch.setattr(json, "loads", prohibited)
    monkeypatch.setattr(json, "dumps", prohibited)
    monkeypatch.setattr("app.analyzers.bola.compare_response_fingerprints", prohibited)
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
                    (FindingEvidenceSimilarity, "finding_evidence_fingerprint_id", fingerprint_id),
                ):
                    if model.__table__ in tables:
                        assert len(tables) == 1
                        assert statement.whereclause.left.name == column
                        assert statement.whereclause.right.value == expected
            return super()._execute_internal(statement, *args, **kwargs)
    with ExactSession(engine) as db:
        result = FindingAnalysisService(db=db).analyze_test_run(test_run_id=ids["probe"], baseline_test_run_id=ids["baseline"])
        assert result.finding.id == finding_id
    row, = similarity_rows(ids)
    assert {k: row[k] for k in FIELDS} == asdict(selected)


@pytest.mark.parametrize("invalid", [None, {}])
def test_missing_typed_similarity_fails_without_partial_state(evidence_pair, monkeypatch, invalid):
    ids = evidence_pair
    result = typed_result(ids)
    monkeypatch.setattr("app.services.finding_analysis.analyze_bola_run",
                        lambda **kwargs: replace(result, similarity_evidence=invalid))
    before = snapshot()
    response = analyze(ids)
    assert response.status_code == 409
    assert response.json()["detail"] == "finding_evidence_similarity_conflict"
    assert snapshot() == before


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize(("status", "body", "outcome"), [
    (403, '{"id":"private-resource-marker"}', "pass"),
    (200, '{"ok":true}', "inconclusive"),
])
def test_equal_digest_and_full_length_match_cannot_create_finding_or_similarity(evidence_pair, existing, status, body, outcome):
    ids = evidence_pair
    with SessionLocal() as db:
        db.get(StoredRun, ids["baseline"]).response_body = body
        probe = db.get(StoredRun, ids["probe"])
        probe.response_body, probe.response_status = body, status
        db.commit()
    if existing:
        old_fingerprint(ids)
    result = typed_result(ids)
    assert result.fingerprint_evidence.baseline_digest == result.fingerprint_evidence.probe_digest
    assert result.fingerprint_evidence.baseline_body_bytes == result.fingerprint_evidence.probe_body_bytes
    before = snapshot()
    response = analyze(ids)
    assert response.status_code == 200
    assert response.json()["outcome"] == outcome
    assert response.json()["finding"] is None
    assert response.json()["confidence"] is None
    assert response.json()["severity"] is None
    assert snapshot() == before
    assert similarity_rows(ids) == []


def test_database_similarity_failure_rolls_back_new_five_layer_unit(evidence_pair, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    ids = evidence_pair
    result = typed_result(ids)
    invalid = replace(result.similarity_evidence, length_similarity_bps=10001)
    monkeypatch.setattr("app.services.finding_analysis.analyze_bola_run",
                        lambda **kwargs: replace(result, similarity_evidence=invalid))
    before = snapshot()
    with SessionLocal() as db:
        with pytest.raises(IntegrityError):
            FindingAnalysisService(db=db).analyze_test_run(test_run_id=ids["probe"], baseline_test_run_id=ids["baseline"])
        db.commit()
    assert snapshot() == before
