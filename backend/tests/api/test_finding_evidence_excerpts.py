from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from threading import Barrier

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql.dml import Insert
from sqlalchemy.orm import Session, sessionmaker

from app.analyzers.bola import analyze_bola_run
from app.db.models import (
    Finding, FindingEvidenceExcerpt, FindingEvidenceRecord, Resource,
    TestCase as StoredCase, TestRun as StoredRun,
)
from app.db.session import SessionLocal, engine
from app.main import app
from app.services.finding_analysis import FindingAnalysisError, FindingAnalysisService
from tests.api.test_finding_structured_evidence import (
    FACTS, analyze, client, evidence_rows, old_finding,
)
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401


EXCERPTS = dict(extractor_id="bola_matched_identifier_field", extractor_version="1",
                baseline_excerpt='{"id":"[MATCHED_RESOURCE_IDENTIFIER]"}',
                probe_excerpt='{"project_id":"[MATCHED_RESOURCE_IDENTIFIER]"}')

FALLBACK = '{"[MATCHED_RESOURCE_IDENTIFIER_FIELD]":"[MATCHED_RESOURCE_IDENTIFIER]"}'


def set_fallback_key(ids, resource_type='"' * 100):
    with SessionLocal() as db:
        db.get(Resource, ids["resource"]).resource_type = resource_type
        db.get(StoredRun, ids["baseline"]).response_body = json.dumps({"data": [{
            f"{resource_type}_id": "private-resource-marker", "id": "private-resource-marker",
            "secret": "response-secret", "email": "private@example.test", "business": "private-business",
        }]})
        db.get(StoredRun, ids["probe"]).response_body = '{"id":"private-resource-marker"}'
        db.commit()


def excerpt_rows(ids):
    with engine.connect() as db:
        return list(db.execute(select(FindingEvidenceExcerpt.__table__).where(
            FindingEvidenceExcerpt.finding_evidence_record_id.in_(
                select(FindingEvidenceRecord.id).where(FindingEvidenceRecord.finding_id.in_(
                    select(Finding.id).where(Finding.target_id == ids["target"])))))
        ).mappings())


def structured_only(ids):
    finding_id = old_finding(ids)
    with SessionLocal() as db:
        record = FindingEvidenceRecord(**FACTS, finding_id=finding_id,
            probe_test_run_id=ids["probe"], baseline_test_run_id=ids["baseline"])
        db.add(record)
        db.commit()
        return finding_id, record.id


def set_probe_key(ids):
    with SessionLocal() as db:
        db.get(StoredRun, ids["probe"]).response_body = json.dumps({
            "parent": [{"project_id": "private-resource-marker", "email": "private@example.test",
                        "Authorization": "Bearer secret-token", "cookie": "session=secret-session",
                        "password": "secret-password", "api_key": "secret-key",
                        "credentials": {"token": "secret-token"}, "business": "secret-business"}],
        })
        db.commit()


def snapshot(ids):
    with engine.connect() as db:
        return {model.__tablename__: list(db.execute(select(model.__table__)).mappings())
                for model in (Finding, FindingEvidenceRecord, FindingEvidenceExcerpt, StoredRun)}


def test_exact_pair_atomic_creation_and_allowlisted_read(evidence_pair):
    ids = evidence_pair
    set_probe_key(ids)
    before = datetime.now(timezone.utc)
    response = analyze(ids)
    after = datetime.now(timezone.utc)
    assert response.status_code == 200
    finding = response.json()["finding"]
    record, = evidence_rows(ids)
    row, = excerpt_rows(ids)
    assert row == {**EXCERPTS, "id": row["id"], "finding_evidence_record_id": record["id"],
                   "created_at": row["created_at"]}
    assert record["finding_id"] == finding["id"]
    assert (record["probe_test_run_id"], record["baseline_test_run_id"]) == (ids["probe"], ids["baseline"])
    assert before <= row["created_at"] <= after
    read = client.get(f"/api/findings/{finding['id']}/evidence/excerpts")
    assert read.status_code == 200
    body = read.json()
    assert set(body) == set(row)
    assert datetime.fromisoformat(body.pop("created_at")) == row["created_at"]
    assert body == {k: v for k, v in row.items() if k != "created_at"}
    for marker in ("private-resource-marker", "private@example.test", "secret-", "response-secret",
                   "request-secret", "Authorization", "credentials", "request_data", "response_body"):
        assert marker not in read.text
        assert marker not in str(row)


def test_exact_not_found_errors_and_no_global_listing(evidence_pair):
    missing = client.get("/api/findings/999999999/evidence/excerpts")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Finding not found."
    finding_id = old_finding(evidence_pair)
    path = f"/api/findings/{finding_id}/evidence/excerpts"
    missing = client.get(path)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "finding_structured_evidence_not_found"
    with SessionLocal() as db:
        db.add(FindingEvidenceRecord(**FACTS, finding_id=finding_id,
            probe_test_run_id=evidence_pair["probe"], baseline_test_run_id=evidence_pair["baseline"]))
        db.commit()
    missing = client.get(path)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "finding_evidence_excerpt_not_found"
    assert excerpt_rows(evidence_pair) == []
    assert {p for p in app.openapi()["paths"] if "excerpt" in p} == {
        "/api/findings/{finding_id}/evidence/excerpts"}
    for global_path in ("/api/evidence/excerpts", "/api/findings/evidence/excerpts", "/api/finding-evidence-excerpts"):
        assert client.get(global_path).status_code == 404
    for method in (client.post, client.patch, client.delete):
        assert method(path).status_code == 405


def test_explicit_same_pair_reanalysis_appends_once_and_preserves_review(evidence_pair):
    ids = evidence_pair
    finding_id, evidence_id = structured_only(ids)
    before = evidence_rows(ids)
    assert excerpt_rows(ids) == []
    assert client.get(f"/api/findings/{finding_id}/evidence").status_code == 200
    assert excerpt_rows(ids) == []
    assert analyze({**ids, "baseline": ids["decoy"]}).status_code == 409
    assert excerpt_rows(ids) == []
    assert analyze(ids).status_code == 200
    original = excerpt_rows(ids)
    assert len(original) == 1
    assert original[0]["finding_evidence_record_id"] == evidence_id
    for _ in range(2):
        response = analyze(ids)
        assert response.status_code == 200
        assert response.json()["finding"]["status"] == "confirmed"
        assert response.json()["finding"]["review_notes"] == "keep human review"
        assert excerpt_rows(ids) == original
        assert evidence_rows(ids) == before


@pytest.mark.parametrize(("status", "body", "outcome"), [
    (403, '{"id":"private-resource-marker"}', "pass"),
    (500, '{"id":"private-resource-marker"}', "inconclusive"),
    (200, 'not JSON', "inconclusive"),
])
def test_pass_inconclusive_do_not_append_even_to_old_evidence(evidence_pair, status, body, outcome):
    ids = evidence_pair
    structured_only(ids)
    with SessionLocal() as db:
        run = db.get(StoredRun, ids["probe"])
        run.response_status, run.response_body = status, body
        db.commit()
    before = snapshot(ids)
    response = analyze(ids)
    assert response.status_code == 200
    assert response.json()["outcome"] == outcome
    assert response.json()["finding"] is None
    assert snapshot(ids) == before
    assert excerpt_rows(ids) == []


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("field", list(EXCERPTS))
def test_material_conflict_fails_closed_without_any_mutation(evidence_pair, field, fallback):
    ids = evidence_pair
    set_probe_key(ids)
    if fallback:
        set_fallback_key(ids)
    finding_id, evidence_id = structured_only(ids)
    expected = {**EXCERPTS}
    if fallback:
        expected.update(baseline_excerpt=FALLBACK, probe_excerpt=EXCERPTS["baseline_excerpt"])
    with SessionLocal() as db:
        db.add(FindingEvidenceExcerpt(finding_evidence_record_id=evidence_id,
                                     **{**expected, field: "different"}))
        db.commit()
    before = snapshot(ids)
    response = analyze(ids)
    assert response.status_code == 409
    assert response.json()["detail"] == "finding_evidence_excerpt_conflict"
    assert snapshot(ids) == before
    # The service itself must also leave no pending updates if its caller catches the error.
    with SessionLocal() as db:
        with pytest.raises(FindingAnalysisError, match="^finding_evidence_excerpt_conflict$"):
            FindingAnalysisService(db=db).analyze_test_run(
                test_run_id=ids["probe"], baseline_test_run_id=ids["baseline"])
        db.commit()
    assert snapshot(ids) == before


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("after_insert", [False, True])
@pytest.mark.parametrize("failing_table", ["finding_evidence_records", "finding_evidence_excerpts"])
@pytest.mark.parametrize("existing", [False, True])
def test_persistence_failure_rolls_back_partial_state_even_if_caller_commits(evidence_pair, failing_table, existing, after_insert, fallback):
    ids = evidence_pair
    if fallback:
        set_fallback_key(ids)
    if existing:
        old_finding(ids)
    before = snapshot(ids)

    class FailingSession(Session):
        def scalar(self, statement, *args, **kwargs):
            if isinstance(statement, Insert) and statement.table.name == failing_table:
                if after_insert:
                    super().scalar(statement, *args, **kwargs)
                raise RuntimeError("synthetic persistence failure")
            return super().scalar(statement, *args, **kwargs)

    with FailingSession(engine) as db:
        with pytest.raises(RuntimeError, match="synthetic persistence failure"):
            FindingAnalysisService(db=db).analyze_test_run(
                test_run_id=ids["probe"], baseline_test_run_id=ids["baseline"])
        db.commit()
    assert snapshot(ids) == before


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_real_concurrent_identical_excerpt_inserts_converge(evidence_pair, existing, fallback):
    ids = evidence_pair
    if fallback:
        set_fallback_key(ids)
    if existing:
        structured_only(ids)
    ready = Barrier(2)
    table_name = "finding_evidence_excerpts" if existing else "findings"

    class RacingSession(Session):
        def scalar(self, statement, *args, **kwargs):
            if isinstance(statement, Insert) and statement.table.name == table_name:
                ready.wait(timeout=10)
            return super().scalar(statement, *args, **kwargs)

    sessions = sessionmaker(bind=engine, class_=RacingSession, expire_on_commit=False)

    def worker(_):
        with sessions() as db:
            return FindingAnalysisService(db=db).analyze_test_run(
                test_run_id=ids["probe"], baseline_test_run_id=ids["baseline"]).finding.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        finding_ids = list(pool.map(worker, range(2)))
    assert finding_ids[0] == finding_ids[1]
    assert len(evidence_rows(ids)) == 1
    original = excerpt_rows(ids)
    assert len(original) == 1
    assert original[0]["baseline_excerpt"] == (FALLBACK if fallback else EXCERPTS["baseline_excerpt"])
    assert analyze(ids).status_code == 200
    assert excerpt_rows(ids) == original


def test_service_copies_only_typed_excerpt_and_uses_exact_evidence_lookup(evidence_pair, monkeypatch):
    ids = evidence_pair
    finding_id, evidence_id = structured_only(ids)
    with SessionLocal() as db:
        result = analyze_bola_run(test_case=db.get(StoredCase, ids["probe_case"]),
            resource=db.get(Resource, ids["resource"]), cross_owner_run=db.get(StoredRun, ids["probe"]),
            owner_baseline_run=db.get(StoredRun, ids["baseline"]))
    selected = replace(result.excerpt_evidence, **EXCERPTS)
    monkeypatch.setattr("app.services.finding_analysis.analyze_bola_run",
                        lambda **kwargs: replace(result, excerpt_evidence=selected))
    for field in ("response_body", "request_data"):
        monkeypatch.setattr(StoredRun, field, property(lambda self: pytest.fail("service read body/data")))
    monkeypatch.setattr(Resource, "external_id", property(lambda self: pytest.fail("service read external_id")))
    monkeypatch.setattr(json, "loads", lambda *a, **k: pytest.fail("service parsed JSON"))
    lookups = []

    class ExactSession(Session):
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
                tables = statement.get_final_froms()
                if StoredRun.__table__ in tables:
                    assert self.exact_run_lookup, "alternate run lookup"
                for model, column, value in (
                    (FindingEvidenceRecord, "finding_id", finding_id),
                    (FindingEvidenceExcerpt, "finding_evidence_record_id", evidence_id),
                ):
                    if model.__table__ in tables:
                        clause = statement.whereclause
                        assert clause.left.name == column
                        assert clause.right.value == value
                        assert len(tables) == 1
                        lookups.append(model)
            return super()._execute_internal(statement, *args, **kwargs)

    with ExactSession(engine) as db:
        outcome = FindingAnalysisService(db=db).analyze_test_run(
            test_run_id=ids["probe"], baseline_test_run_id=ids["baseline"])
        assert outcome.finding.id == finding_id
    assert lookups == [FindingEvidenceRecord, FindingEvidenceExcerpt]
    row, = excerpt_rows(ids)
    assert {k: row[k] for k in asdict(selected)} == asdict(selected)


def test_huge_body_keeps_same_tiny_database_size(evidence_pair):
    ids = evidence_pair
    assert analyze(ids).status_code == 200
    original, = excerpt_rows(ids)
    with engine.connect() as db:
        size = db.scalar(text("SELECT pg_column_size(e) FROM finding_evidence_excerpts e WHERE id = :id"), original)
    body = json.dumps({"id": "private-resource-marker", "padding": "private-business" * 100000})
    with SessionLocal() as db:
        db.get(StoredRun, ids["baseline"]).response_body = body
        db.get(StoredRun, ids["probe"]).response_body = body
        fresh = StoredRun(test_case_id=ids["probe_case"], request_data={}, response_status=200, response_body=body)
        db.add(fresh)
        db.commit()
        fresh_id = fresh.id
    assert analyze(ids).status_code == 200
    assert excerpt_rows(ids) == [original]
    assert analyze({**ids, "probe": fresh_id}).status_code == 200
    rows = excerpt_rows(ids)
    assert len(rows) == 2
    for row in rows:
        assert row["baseline_excerpt"] == row["probe_excerpt"] == EXCERPTS["baseline_excerpt"]
        assert len(row["baseline_excerpt"]) <= 192
        with engine.connect() as db:
            assert db.scalar(text("SELECT pg_column_size(e) FROM finding_evidence_excerpts e WHERE id = :id"), row) == size


@pytest.mark.parametrize("resource_type", ['"' * 100, '\\' * 100, '\x01' * 100])
@pytest.mark.parametrize("existing", [False, True])
def test_fallback_persists_normally_and_retry_preserves_evidence_and_review(evidence_pair, existing, resource_type):
    ids = evidence_pair
    set_fallback_key(ids, resource_type)
    if existing:
        structured_only(ids)
    before = snapshot(ids)
    response = analyze(ids)
    assert response.status_code == 200
    assert response.json()["outcome"] == "potential_bola"
    row, = excerpt_rows(ids)
    evidence, = evidence_rows(ids)
    assert row["finding_evidence_record_id"] == evidence["id"]
    assert row["baseline_excerpt"] == FALLBACK
    assert row["probe_excerpt"] == EXCERPTS["baseline_excerpt"]
    finding_id = response.json()["finding"]["id"]
    assert evidence["finding_id"] == finding_id
    read = client.get(f"/api/findings/{finding_id}/evidence/excerpts")
    assert read.status_code == 200
    assert read.json()["baseline_excerpt"] == FALLBACK
    for marker in ("private-resource-marker", "response-secret", "request-secret", "private@example.test", "private-business"):
        assert marker not in read.text
        assert marker not in str(row)
    for excerpt in (row["baseline_excerpt"], row["probe_excerpt"]):
        assert len(excerpt) <= 192
        assert excerpt == json.dumps(json.loads(excerpt), separators=(",", ":"))
    after = snapshot(ids)
    assert after["test_runs"] == before["test_runs"]
    if existing:
        assert after["finding_evidence_records"] == before["finding_evidence_records"]
    for _ in range(2):
        retry = analyze(ids)
        assert retry.status_code == 200
        assert retry.json()["finding"]["id"] == finding_id
        assert excerpt_rows(ids) == [row]
        assert evidence_rows(ids) == [evidence]
        if existing:
            assert retry.json()["finding"]["status"] == "confirmed"
            assert retry.json()["finding"]["review_notes"] == "keep human review"


@pytest.mark.parametrize("missing", [None, EXCERPTS])
def test_potential_without_typed_excerpt_fails_closed(evidence_pair, monkeypatch, missing):
    ids = evidence_pair
    with SessionLocal() as db:
        result = analyze_bola_run(test_case=db.get(StoredCase, ids["probe_case"]),
            resource=db.get(Resource, ids["resource"]), cross_owner_run=db.get(StoredRun, ids["probe"]),
            owner_baseline_run=db.get(StoredRun, ids["baseline"]))
    monkeypatch.setattr("app.services.finding_analysis.analyze_bola_run",
                        lambda **kwargs: replace(result, excerpt_evidence=missing))
    before = snapshot(ids)
    response = analyze(ids)
    assert response.status_code == 409
    assert response.json()["detail"] == "finding_evidence_excerpt_conflict"
    assert snapshot(ids) == before
