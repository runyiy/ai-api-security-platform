"""M13-06 policy-only persistence, exact reads, and atomicity contract."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict, fields
from datetime import datetime, timezone
import inspect
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql.dml import Insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import Finding, FindingEvidenceRecord, Resource, Target, TestRun as StoredRun
from app.db.session import SessionLocal, engine
from app.main import app
from app.services.finding_analysis import FindingAnalysisError, FindingAnalysisService
from tests.api.test_finding_structured_evidence import FACTS, analyze, client, evidence_rows, old_finding
from tests.api.test_finding_evidence_fingerprints import old_evidence, typed_result
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401

VALUES = dict(policy_id="m13_minimized_finding_evidence", policy_version="1",
              retention_mode="explicit_management_only", automatic_deletion_enabled=False,
              raw_response_body_retained=False)
TABLE = "finding_evidence_retention_bindings"
LAYERS = ("findings", "finding_evidence_records", "finding_evidence_excerpts",
          "finding_evidence_fingerprints", "finding_evidence_similarities", TABLE)


def binding_model():
    from app.db.models import FindingEvidenceRetentionBinding
    return FindingEvidenceRetentionBinding


def retention_rows(ids):
    model = binding_model()
    evidence_ids = [row["id"] for row in evidence_rows(ids)]
    with engine.connect() as db:
        return list(db.execute(select(model.__table__).where(
            model.finding_evidence_record_id.in_(evidence_ids)).order_by(model.id)).mappings())


def snapshot():
    with engine.connect() as db:
        return {table.name: list(db.execute(select(table).order_by(*table.primary_key.columns)).mappings())
                for table in Base.metadata.sorted_tables}


def test_frozen_exact_five_field_pure_v1_policy():
    from app.domain.finding_evidence_retention import FindingEvidenceRetentionPolicy, V1_FINDING_EVIDENCE_RETENTION_POLICY
    policy = V1_FINDING_EVIDENCE_RETENTION_POLICY
    assert isinstance(policy, FindingEvidenceRetentionPolicy)
    assert {f.name for f in fields(policy)} == set(VALUES)
    assert asdict(policy) == VALUES
    assert policy.automatic_deletion_enabled is False
    assert policy.raw_response_body_retained is False
    for field in VALUES:
        with pytest.raises(FrozenInstanceError):
            setattr(policy, field, "changed")
    # The boundary has no context, body, clock, persistence, or runtime dependency.
    import ast
    import app.domain.finding_evidence_retention as module
    tree = ast.parse(inspect.getsource(module))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imports <= {"dataclasses", "typing"}
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Attribute) and node.attr in {
        "response_body", "request_data", "now", "utcnow", "time", "owner_identity_id",
        "severity", "confidence", "status", "network_mode", "credentials",
    } for node in ast.walk(tree))


def test_new_finding_exact_binding_allowlisted_read_and_retry(evidence_pair):
    ids = evidence_pair
    before = snapshot()
    start = datetime.now(timezone.utc)
    response = analyze(ids)
    end = datetime.now(timezone.utc)
    assert response.status_code == 200
    assert response.json()["outcome"] == "potential_bola"
    evidence, = evidence_rows(ids)
    row, = retention_rows(ids)
    assert evidence["finding_id"] == response.json()["finding"]["id"]
    assert (evidence["baseline_test_run_id"], evidence["probe_test_run_id"]) == (ids["baseline"], ids["probe"])
    assert row == {"id": row["id"], "finding_evidence_record_id": evidence["id"], **VALUES, "bound_at": row["bound_at"]}
    assert start <= row["bound_at"] <= end
    read = client.get(f"/api/findings/{evidence['finding_id']}/evidence/retention")
    assert read.status_code == 200
    body = read.json()
    assert set(body) == set(row)
    assert datetime.fromisoformat(body.pop("bound_at")) == row["bound_at"]
    assert body == {k: v for k, v in row.items() if k != "bound_at"}
    for marker in ("private-resource-marker", "response-secret", "request-secret", "response_body\"",
                   "request_data", "digest", "excerpt", "expires_at", "ttl", "delete_after", "purge"):
        assert marker not in read.text
    original = retention_rows(ids)
    for _ in range(3):
        assert analyze(ids).status_code == 200
        assert retention_rows(ids) == original
    after = snapshot()
    assert {k: v for k, v in after.items() if k not in LAYERS} == {k: v for k, v in before.items() if k not in LAYERS}


def test_exact_404s_and_read_never_appends(evidence_pair):
    missing = client.get("/api/findings/999999999/evidence/retention")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Finding not found."
    finding_id = old_finding(evidence_pair)
    path = f"/api/findings/{finding_id}/evidence/retention"
    for structured, detail in ((False, "finding_structured_evidence_not_found"),
                               (True, "finding_evidence_retention_binding_not_found")):
        if structured:
            with SessionLocal() as db:
                db.add(FindingEvidenceRecord(**FACTS, finding_id=finding_id,
                    baseline_test_run_id=evidence_pair["baseline"], probe_test_run_id=evidence_pair["probe"]))
                db.commit()
        before = snapshot()
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["detail"] == detail
        assert snapshot() == before


def test_no_listing_mutation_or_background_retention_path():
    paths = app.openapi()["paths"]
    assert {p for p in paths if "retention" in p} == {"/api/findings/{finding_id}/evidence/retention"}
    for path, methods in paths.items():
        if "evidence" in path or "retention" in path:
            assert set(methods) == {"get"}
    for path in ("/api/retention", "/api/evidence/retention", "/api/finding-evidence-retention-bindings"):
        assert client.get(path).status_code == 404
    for method in ("POST", "PATCH", "PUT", "DELETE", "PURGE"):
        assert client.request(method, "/api/findings/1/evidence/retention").status_code == 405
    from pathlib import Path
    import ast
    for path in (Path("app/domain/finding_evidence_retention.py"),
                 Path("app/db/models/finding_evidence_retention_binding.py")):
        tree = ast.parse(path.read_text())
        names = {node.id.lower() for node in ast.walk(tree) if isinstance(node, ast.Name)}
        names |= {node.attr.lower() for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert not names & {"delete", "purge", "ttl", "expires_at", "delete_after", "timedelta",
                            "celery", "rq", "scheduler", "backgroundtasks", "create_task", "response_body", "request_data"}


@pytest.mark.parametrize(("status", "body", "outcome"), [(403, None, "pass"), (500, None, "inconclusive"),
    (200, "not JSON", "inconclusive"), (200, '{"ok":true}', "inconclusive")])
@pytest.mark.parametrize("existing", [False, True])
def test_non_potential_appends_nothing(evidence_pair, status, body, outcome, existing):
    if existing:
        old_evidence(evidence_pair)
    with SessionLocal() as db:
        run = db.get(StoredRun, evidence_pair["probe"])
        run.response_status, run.response_body = status, body
        db.commit()
    before = snapshot()
    response = analyze(evidence_pair)
    assert response.status_code == 200
    assert response.json()["outcome"] == outcome
    assert snapshot() == before
    assert retention_rows(evidence_pair) == []


@pytest.mark.parametrize("table", LAYERS[1:])
@pytest.mark.parametrize("after_insert", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_six_layer_savepoint_rolls_back_every_failure(evidence_pair, table, after_insert, existing):
    if existing:
        old_finding(evidence_pair)
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
            FindingAnalysisService(db=db).analyze_test_run(test_run_id=evidence_pair["probe"],
                baseline_test_run_id=evidence_pair["baseline"])
        db.commit()  # Even a caller that catches and commits cannot retain partial state.
    assert snapshot() == before


@pytest.mark.parametrize("existing", [False, True])
def test_real_concurrent_analysis_converges(evidence_pair, existing):
    if existing:
        assert analyze(evidence_pair).status_code == 200
    before = retention_rows(evidence_pair)
    ready = Barrier(2)
    class RacingSession(Session):
        def scalar(self, statement, *args, **kwargs):
            if isinstance(statement, Insert) and statement.table.name == "findings":
                ready.wait(timeout=10)
            return super().scalar(statement, *args, **kwargs)
    sessions = sessionmaker(bind=engine, class_=RacingSession, expire_on_commit=False)
    def worker(_):
        with sessions() as db:
            return FindingAnalysisService(db=db).analyze_test_run(test_run_id=evidence_pair["probe"],
                baseline_test_run_id=evidence_pair["baseline"]).finding.id
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(worker, range(2)))
    assert result[0] == result[1]
    rows = retention_rows(evidence_pair)
    assert len(rows) == 1
    if existing:
        assert rows == before
    assert analyze(evidence_pair).status_code == 200
    assert retention_rows(evidence_pair) == rows


@pytest.mark.parametrize("field", sorted(VALUES))
def test_material_existing_policy_conflict_fails_closed_without_mutation(evidence_pair, monkeypatch, field):
    finding_id = old_finding(evidence_pair)
    assert analyze(evidence_pair).status_code == 200
    model = binding_model()
    # Exact DB checks make corruption impossible via normal SQL. Inject a corrupt
    # stored value at the read boundary without weakening production constraints.
    original_scalar = Session.scalar
    def corrupt_read(self, statement, *args, **kwargs):
        row = original_scalar(self, statement, *args, **kwargs)
        if isinstance(row, model):
            from sqlalchemy.orm.attributes import set_committed_value
            set_committed_value(row, field, True if isinstance(VALUES[field], bool) else "other")
        return row
    monkeypatch.setattr(Session, "scalar", corrupt_read)
    before = snapshot()
    response = analyze(evidence_pair)
    assert response.status_code == 409
    assert response.json()["detail"] == "finding_evidence_retention_policy_conflict"
    with SessionLocal() as db:
        with pytest.raises(FindingAnalysisError, match="^finding_evidence_retention_policy_conflict$"):
            FindingAnalysisService(db=db).analyze_test_run(test_run_id=evidence_pair["probe"],
                baseline_test_run_id=evidence_pair["baseline"])
        db.commit()
    assert snapshot() == before
    assert next(row for row in before["findings"] if row["id"] == finding_id)["review_notes"] == "keep human review"


@pytest.mark.parametrize("network_mode", ["private_local", "external_public_authorized"])
def test_policy_copies_constant_using_exact_evidence_without_context_or_content(evidence_pair, monkeypatch, network_mode):
    ids = evidence_pair
    finding_id, evidence_id = old_evidence(ids)
    with SessionLocal() as db:
        db.get(Target, ids["target"]).network_mode = network_mode
        finding = db.get(Finding, finding_id)
        finding.severity, finding.confidence, finding.status = "low", 0.1, "false_positive"
        db.get(Resource, ids["resource"]).owner_identity_id = ids["actor"]
        db.commit()
    result = typed_result(ids)
    monkeypatch.setattr("app.services.finding_analysis.analyze_bola_run", lambda **kwargs: result)
    def prohibited(*args, **kwargs):
        pytest.fail("retention accessed context, content or clock")
    for field in ("response_body", "request_data"):
        monkeypatch.setattr(StoredRun, field, property(prohibited))
    monkeypatch.setattr(Resource, "owner_identity_id", property(prohibited))
    monkeypatch.setattr(Target, "network_mode", property(prohibited))
    model = binding_model()
    class ExactSession(Session):
        def _execute_internal(self, statement, *args, **kwargs):
            if getattr(statement, "is_select", False) and model.__table__ in statement.get_final_froms():
                assert len(statement.get_final_froms()) == 1
                assert statement.whereclause.left.name == "finding_evidence_record_id"
                assert statement.whereclause.right.value == evidence_id
            return super()._execute_internal(statement, *args, **kwargs)
    before = snapshot()
    with ExactSession(engine) as db:
        outcome = FindingAnalysisService(db=db).analyze_test_run(test_run_id=ids["probe"], baseline_test_run_id=ids["baseline"])
        assert outcome.finding.id == finding_id
        assert outcome.finding.status == "false_positive"
        assert outcome.finding.review_notes == "keep human review"
    row, = retention_rows(ids)
    assert {k: row[k] for k in VALUES} == VALUES
    after = snapshot()
    assert {k: v for k, v in after.items() if k not in LAYERS} == {k: v for k, v in before.items() if k not in LAYERS}


def test_real_binding_insert_race_uses_database_unique_authority(evidence_pair):
    from app.domain.finding_evidence_retention import V1_FINDING_EVIDENCE_RETENTION_POLICY
    _, evidence_id = old_evidence(evidence_pair)
    ready = Barrier(2)
    class RacingSession(Session):
        def scalar(self, statement, *args, **kwargs):
            if isinstance(statement, Insert) and statement.table.name == TABLE:
                ready.wait(timeout=10)
            return super().scalar(statement, *args, **kwargs)
    def worker(_):
        with RacingSession(engine) as db:
            binding = FindingAnalysisService(db=db)._persist_retention_binding(
                evidence_id, V1_FINDING_EVIDENCE_RETENTION_POLICY)
            identifier = binding.id
            db.commit()
            return identifier
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(worker, range(2)))
    assert result[0] == result[1]
    row, = retention_rows(evidence_pair)
    assert row["id"] == result[0]


def test_database_retention_failure_rolls_back_six_layer_unit(evidence_pair):
    from sqlalchemy.exc import IntegrityError
    before = snapshot()
    class InvalidInsertSession(Session):
        def scalar(self, statement, *args, **kwargs):
            if isinstance(statement, Insert) and statement.table.name == TABLE:
                statement = statement.values(automatic_deletion_enabled=True)
            return super().scalar(statement, *args, **kwargs)
    with InvalidInsertSession(engine) as db:
        with pytest.raises(IntegrityError):
            FindingAnalysisService(db=db).analyze_test_run(test_run_id=evidence_pair["probe"],
                baseline_test_run_id=evidence_pair["baseline"])
        db.commit()
    assert snapshot() == before


def test_retention_conflict_rolls_back_new_layers_on_historical_evidence(evidence_pair, monkeypatch):
    _, evidence_id = old_evidence(evidence_pair)
    model = binding_model()
    with SessionLocal() as db:
        db.add(model(finding_evidence_record_id=evidence_id, **VALUES))
        db.commit()
    before = snapshot()
    original_scalar = Session.scalar
    def corrupt_read(self, statement, *args, **kwargs):
        row = original_scalar(self, statement, *args, **kwargs)
        if isinstance(row, model):
            from sqlalchemy.orm.attributes import set_committed_value
            set_committed_value(row, "policy_version", "other")
        return row
    monkeypatch.setattr(Session, "scalar", corrupt_read)
    with SessionLocal() as db:
        with pytest.raises(FindingAnalysisError, match="^finding_evidence_retention_policy_conflict$"):
            FindingAnalysisService(db=db).analyze_test_run(test_run_id=evidence_pair["probe"],
                baseline_test_run_id=evidence_pair["baseline"])
        db.commit()
    assert snapshot() == before
