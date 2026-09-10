"""Exact read-only preview composition with real PostgreSQL M12 resolution."""
import ast
from dataclasses import FrozenInstanceError, asdict, fields, replace
from datetime import datetime, timedelta, timezone, tzinfo
import importlib
import inspect
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import Base
from app.db.models import (Endpoint, Resource, ResourceAccessAssertion, Target,
                           TestIdentity as Identity, TestRun as StoredRun)
from app.db.session import SessionLocal, engine
from app.generators.bola_matrix import BOLAMatrixAccessFact, BOLAMatrixCandidate, BOLAMatrixPlanningError
from app.main import app
from app.services.resource_access_resolution import ResourceAccessResolution, ResourceAccessResolutionError
from tests.api.test_resource_access_resolution import NOW, make_pair, cleanup, add_assertion, make_observed_candidate


@pytest.fixture
def preview():
    return importlib.import_module("app.services.bola_matrix_preview")


@pytest.fixture
def selected(preview):
    ids = make_pair()
    other = make_pair()
    try:
        with SessionLocal() as db:
            endpoint = Endpoint(target_id=ids["target"], path="/metadata/only", method="DELETE",
                parameters=[], request_body={"secret": "endpoint-secret"}, security=[{"secret": []}])
            anonymous = Identity(target_id=ids["target"], name="owner-named-anonymous", role="owner",
                auth_type="anonymous", credentials={"secret": "credential-secret"}, is_active=True)
            decoy = Identity(target_id=ids["target"], name="unselected", auth_type="bearer", is_active=False)
            db.add_all([endpoint, anonymous, decoy])
            db.commit()
            ids.update(endpoint=endpoint.id, anonymous=anonymous.id, decoy=decoy.id,
                       other_identity=other["identity"], other_resource=other["resource"])
        yield ids
    finally:
        cleanup([ids["target"], other["target"]])


def call(preview, db, ids, **overrides):
    return preview.preview_bola_matrix(db, **{
        "endpoint_id": ids["endpoint"], "resource_id": ids["resource"],
        "test_identity_ids": [ids["identity"]], "evaluation_time": NOW, **overrides,
    })


def snapshot():
    with engine.connect() as db:
        return {table.name: list(db.execute(select(table).order_by(*table.primary_key.columns)).mappings())
                for table in Base.metadata.sorted_tables}


def fail(*args, **kwargs):
    pytest.fail("preview crossed a forbidden boundary")


def assert_code(preview, code, action):
    with pytest.raises((preview.BOLAMatrixPreviewError, BOLAMatrixPlanningError,
                        ResourceAccessResolutionError)) as error:
        action()
    assert error.value.code == str(error.value) == code
    return error.value


def no_reads(preview, monkeypatch, db):
    for name in ("execute", "scalar", "scalars", "get", "flush", "commit", "rollback", "close",
                 "expunge", "expunge_all", "expire", "expire_all", "begin", "begin_nested"):
        monkeypatch.setattr(db, name, fail)
    monkeypatch.setattr(preview, "resolve_resource_access", fail)
    monkeypatch.setattr(preview, "plan_bola_matrix", fail)


def test_signature_head_and_no_new_route_or_forbidden_dependencies(preview):
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["5f83bac2e714"]
    parameters = inspect.signature(preview.preview_bola_matrix).parameters
    assert list(parameters) == ["db", "endpoint_id", "resource_id", "test_identity_ids", "evaluation_time"]
    assert all(p.default is inspect.Parameter.empty for p in parameters.values())
    assert {path: set(operations) for path, operations in app.openapi()["paths"].items()
            if "matrix" in path} == {"/api/bola-matrix/preview": {"post"}}
    tree = ast.parse(inspect.getsource(preview))
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert modules <= {"collections.abc", "dataclasses", "datetime", "typing", "sqlalchemy", "sqlalchemy.orm",
                       "app.db.models.endpoint", "app.db.models.resource", "app.db.models.test_identity",
                       "app.generators.bola_matrix", "app.services.resource_access_resolution"}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"ResourceAccessAssertion", "EndpointResourceBinding", "owner_identity_id", "external_id",
                        "credentials", "credential_bindings", "role", "request_body", "response_body", "security",
                        "now", "utcnow", "flush", "commit", "rollback", "close", "begin", "begin_nested"}


@pytest.mark.parametrize("field", ["endpoint_id", "resource_id", "test_identity_ids"])
@pytest.mark.parametrize("invalid", [True, False, "1", 1.0, 0, -1, None])
def test_strict_ids_fail_before_sql(preview, monkeypatch, field, invalid):
    with Session() as db:
        with monkeypatch.context() as guard:
            no_reads(preview, guard, db)
            payload = invalid if field != "test_identity_ids" else [1, invalid]
            assert_code(preview, "bola_matrix_invalid_access_fact", lambda: call(preview, db,
                dict(endpoint=1, resource=2, identity=3), **{field: payload}))


@pytest.mark.parametrize("invalid", [None, 1, True, "", "123", b"123"])
def test_invalid_identity_iterable_fails_before_sql(preview, monkeypatch, invalid):
    with Session() as db:
        with monkeypatch.context() as guard:
            no_reads(preview, guard, db)
            assert_code(preview, "bola_matrix_invalid_access_fact", lambda: call(preview, db,
                dict(endpoint=1, resource=2, identity=3), test_identity_ids=invalid))


class NaiveZone(tzinfo):
    def utcoffset(self, dt):
        return None


@pytest.mark.parametrize(("value", "code"), [
    (None, "bola_matrix_evaluation_time_invalid"), ("2030-06-01T12:00:00Z", "bola_matrix_evaluation_time_invalid"),
    (1, "bola_matrix_evaluation_time_invalid"), (True, "bola_matrix_evaluation_time_invalid"),
    (NOW.date(), "bola_matrix_evaluation_time_invalid"),
    (NOW.replace(tzinfo=None), "evaluation_time_timezone_required"),
    (NOW.replace(tzinfo=NaiveZone()), "evaluation_time_timezone_required"),
])
def test_explicit_aware_time_validated_before_sql(preview, monkeypatch, value, code):
    with Session() as db:
        with monkeypatch.context() as guard:
            no_reads(preview, guard, db)
            assert_code(preview, code, lambda: call(preview, db, dict(endpoint=1, resource=2, identity=3), evaluation_time=value))
            with pytest.raises(TypeError):
                preview.preview_bola_matrix(db, endpoint_id=1, resource_id=2, test_identity_ids=[])


def test_513th_item_and_duplicate_fail_before_any_sql_or_resolution(preview, monkeypatch):
    consumed = []
    def bounded_source():
        for i in range(1, 515):
            assert i <= 513
            consumed.append(i)
            yield i
    with Session() as db:
        with monkeypatch.context() as guard:
            no_reads(preview, guard, db)
            assert_code(preview, "bola_matrix_fact_limit_exceeded", lambda: call(preview, db,
                dict(endpoint=1, resource=2, identity=3), test_identity_ids=bounded_source()))
            assert consumed == list(range(1, 514))
            for identities in ([3, 3], [1, 2, 1], [*range(1, 512), 1]):
                assert_code(preview, "bola_matrix_duplicate_access_fact", lambda: call(preview, db,
                    dict(endpoint=1, resource=2, identity=3), test_identity_ids=iter(identities)))


def test_empty_validates_exact_pair_without_resolution(preview, selected, monkeypatch):
    monkeypatch.setattr(preview, "resolve_resource_access", fail)
    planner = Mock(wraps=preview.plan_bola_matrix)
    monkeypatch.setattr(preview, "plan_bola_matrix", planner)
    with SessionLocal() as db:
        result = call(preview, db, selected, test_identity_ids=[])
        assert result.facts == result.candidates == ()
        planner.assert_called_once_with(facts=())
        assert_code(preview, "endpoint_not_found", lambda: call(preview, db, selected,
            endpoint_id=999999999, test_identity_ids=[]))
        assert_code(preview, "resource_not_found", lambda: call(preview, db, selected,
            resource_id=999999999, test_identity_ids=[]))
        assert_code(preview, "bola_matrix_endpoint_resource_target_mismatch", lambda: call(preview, db, selected,
            resource_id=selected["other_resource"], test_identity_ids=[]))


@pytest.mark.parametrize(("change", "code"), [
    ("endpoint_missing", "endpoint_not_found"), ("resource_missing", "resource_not_found"),
    ("identity_missing", "test_identity_not_found"),
    ("resource_target", "bola_matrix_endpoint_resource_target_mismatch"),
    ("identity_target", "resource_identity_target_mismatch"), ("inactive", "bola_matrix_identity_inactive"),
])
def test_exact_metadata_errors_abort_whole_preview(preview, selected, monkeypatch, change, code):
    kwargs = {}
    if change == "endpoint_missing": kwargs["endpoint_id"] = 999999999
    if change == "resource_missing": kwargs["resource_id"] = 999999999
    if change == "resource_target": kwargs["resource_id"] = selected["other_resource"]
    if change in ("identity_missing", "identity_target", "inactive"):
        identity_id = {"identity_missing": 999999999, "identity_target": selected["other_identity"],
                       "inactive": selected["decoy"]}[change]
        kwargs["test_identity_ids"] = [selected["identity"], identity_id]
    monkeypatch.setattr(preview, "resolve_resource_access", fail)
    monkeypatch.setattr(preview, "plan_bola_matrix", fail)
    before = snapshot()
    with SessionLocal() as db:
        assert_code(preview, code, lambda: call(preview, db, selected, **kwargs))
    assert snapshot() == before


def test_real_resolver_exact_pair_time_and_planner_order_once(preview, selected, monkeypatch):
    ids = selected
    supported = add_assertion(ids, expected_access="denied", confidence=0)
    anonymous_support = add_assertion(dict(ids, identity=ids["anonymous"]), relationship="unspecified", expected_access="allowed")
    resolver = Mock(wraps=preview.resolve_resource_access)
    planner = Mock(wraps=preview.plan_bola_matrix)
    monkeypatch.setattr(preview, "resolve_resource_access", resolver)
    monkeypatch.setattr(preview, "plan_bola_matrix", planner)
    at = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
    identities = [ids["anonymous"], ids["identity"]]
    with SessionLocal() as db:
        result = call(preview, db, ids, test_identity_ids=iter(identities), evaluation_time=at)
        assert resolver.call_count == 2
        signature = inspect.signature(preview.resolve_resource_access._mock_wraps)
        for invocation, identity_id in zip(resolver.call_args_list, identities, strict=True):
            bound = signature.bind(*invocation.args, **invocation.kwargs).arguments
            assert bound == dict(db=db, resource_id=ids["resource"], test_identity_id=identity_id, evaluation_time=at)
            assert bound["evaluation_time"] is at
    planner.assert_called_once_with(facts=result.facts)
    assert result.evaluation_time is at
    assert [f.test_identity_id for f in result.facts] == identities
    assert [f.identity_auth_type for f in result.facts] == ["anonymous", "bearer"]
    assert [f.supporting_assertion_ids for f in result.facts] == [(anonymous_support,), (supported,)]
    assert [(c.candidate_kind, c.expected_access) for c in result.candidates] == [("anonymous_access", "allowed"), ("owner_access", "denied")]
    assert {f.name for f in fields(result)} == {"endpoint_id", "resource_id", "evaluation_time", "facts", "candidates"}
    assert isinstance(result, preview.BOLAMatrixPreview)
    assert isinstance(result.facts, tuple) and all(isinstance(f, BOLAMatrixAccessFact) for f in result.facts)
    assert isinstance(result.candidates, tuple) and all(isinstance(c, BOLAMatrixCandidate) for c in result.candidates)
    for field in fields(result):
        with pytest.raises(FrozenInstanceError): setattr(result, field.name, None)
    with pytest.raises(FrozenInstanceError): result.facts[0].relationship = "owner"
    with pytest.raises(FrozenInstanceError): result.candidates[0].expected_access = "denied"
    with pytest.raises(TypeError): result.facts[0] = result.facts[1]
    for forbidden in ("external-resolution-secret", "credential-secret", "endpoint-secret", "https://", "expected_statuses",
                      "authorized", "executable", "response_body", "request_body", "credentials"):
        assert forbidden not in repr(asdict(result))


@pytest.mark.parametrize(("relationship", "access", "kind"), [
    ("owner", "allowed", "owner_access"), ("owner", "denied", "owner_access"),
    ("non_owner", "allowed", "cross_subject_access"), ("non_owner", "denied", "cross_subject_access"),
    ("shared", "allowed", "shared_access"), ("shared", "denied", "shared_access"),
    ("unspecified", "allowed", None), ("owner", "unspecified", None), ("non_owner", "unspecified", None),
])
def test_real_independent_access_truth_and_unspecified_skips(preview, selected, relationship, access, kind):
    assertion_id = add_assertion(selected, relationship=relationship, expected_access=access)
    with SessionLocal() as db: result = call(preview, db, selected)
    fact, = result.facts
    assert (fact.resolution_state, fact.relationship, fact.expected_access, fact.supporting_assertion_ids) == (
        "resolved", relationship, access, (assertion_id,))
    if kind is None:
        assert result.candidates == ()
    else:
        candidate, = result.candidates
        assert (candidate.candidate_kind, candidate.expected_access) == (kind, access)


@pytest.mark.parametrize("access", ["allowed", "denied"])
def test_real_anonymous_preserves_unspecified_relationship(preview, selected, access):
    add_assertion(dict(selected, identity=selected["anonymous"]), relationship="unspecified", expected_access=access)
    with SessionLocal() as db:
        result = call(preview, db, selected, test_identity_ids=[selected["anonymous"]])
    candidate, = result.candidates
    assert (candidate.candidate_kind, candidate.subject_kind, candidate.relationship, candidate.expected_access) == (
        "anonymous_access", "anonymous", "unspecified", access)


def test_real_conflict_and_insufficient_facts_visible_without_winner_or_owner_fallback(preview, selected):
    low = add_assertion(selected, expected_access="denied", confidence=0, provenance="target_fixture")
    high = add_assertion(selected, expected_access="allowed", confidence=100)
    with SessionLocal() as db:
        result = call(preview, db, selected, test_identity_ids=[selected["identity"], selected["anonymous"]])
    assert [f.resolution_state for f in result.facts] == ["conflict", "insufficient"]
    assert result.facts[0].supporting_assertion_ids == (low, high)
    assert result.facts[1].supporting_assertion_ids == ()
    assert result.candidates == ()


def test_real_candidate_rejected_and_observed_assertions_ignored(preview, selected):
    add_assertion(selected, verification_state="candidate", confidence=100)
    add_assertion(selected, verification_state="rejected", confidence=100)
    observed = make_observed_candidate(selected)
    with SessionLocal() as db:
        before = db.get(ResourceAccessAssertion, observed).verification_state
        result = call(preview, db, selected)
    fact, = result.facts
    assert before == "candidate"
    assert (fact.resolution_state, fact.relationship, fact.expected_access, fact.supporting_assertion_ids) == (
        "insufficient", "unspecified", "unspecified", ())
    assert result.candidates == ()


@pytest.mark.parametrize(("asserted_delta", "start_delta", "end_delta", "observed_delta", "eligible"), [
    (0, None, None, None, True), (1, -10, 10, -100, False),
    (-10, 0, 10, None, True), (-10, 1, 10, None, False),
    (-10, -5, 0, None, False), (-10, -5, 1, None, True),
    (-10, None, None, 100, True), (-10, None, None, -100, True),
])
def test_real_asserted_and_half_open_validity_observed_time_is_not_validity(
        preview, selected, asserted_delta, start_delta, end_delta, observed_delta, eligible):
    def at(delta): return None if delta is None else NOW + timedelta(seconds=delta)
    assertion_id = add_assertion(selected, asserted_at=at(asserted_delta), valid_from=at(start_delta), valid_until=at(end_delta))
    with SessionLocal() as db:
        db.get(ResourceAccessAssertion, assertion_id).observed_at = at(observed_delta)
        db.commit()
    with SessionLocal() as db: result = call(preview, db, selected)
    assert result.facts[0].supporting_assertion_ids == ((assertion_id,) if eligible else ())
    assert len(result.candidates) == int(eligible)


@pytest.mark.parametrize("mismatch", ["resource", "identity", "time", "naive", "invalid_time"])
def test_wrong_resolution_pair_or_time_fails_before_planner(preview, selected, monkeypatch, mismatch):
    real_resolver = preview.resolve_resource_access
    def wrong(db, resource_id, test_identity_id, evaluation_time):
        result = real_resolver(db, resource_id, test_identity_id, evaluation_time)
        if mismatch == "resource": return replace(result, resource_id=selected["other_resource"])
        if mismatch == "identity": return replace(result, test_identity_id=selected["anonymous"])
        if mismatch == "time": return replace(result, evaluation_time=evaluation_time + timedelta(microseconds=1))
        if mismatch == "naive": return replace(result, evaluation_time=evaluation_time.replace(tzinfo=None))
        return replace(result, evaluation_time="invalid")
    monkeypatch.setattr(preview, "resolve_resource_access", wrong)
    monkeypatch.setattr(preview, "plan_bola_matrix", fail)
    with SessionLocal() as db:
        assert_code(preview, "bola_matrix_resolution_mismatch", lambda: call(preview, db, selected))


def test_resolution_instant_checks_fold_and_accepts_equivalent_offset(preview, selected, monkeypatch):
    at = datetime(2030, 11, 3, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=0)
    resolution = ResourceAccessResolution(selected["resource"], selected["identity"], at,
                                         "insufficient", "unspecified", "unspecified", ())
    monkeypatch.setattr(preview, "resolve_resource_access", lambda *args, **kwargs: replace(
        resolution, evaluation_time=at.astimezone(timezone.utc)))
    with SessionLocal() as db:
        assert call(preview, db, selected, evaluation_time=at).evaluation_time is at
        monkeypatch.setattr(preview, "resolve_resource_access", lambda *args, **kwargs: replace(
            resolution, evaluation_time=at.replace(fold=1)))
        monkeypatch.setattr(preview, "plan_bola_matrix", fail)
        assert_code(preview, "bola_matrix_resolution_mismatch", lambda: call(preview, db, selected, evaluation_time=at))


def test_copies_opaque_support_and_all_skipped_facts_to_existing_planner(preview, selected, monkeypatch):
    supports = (90, 1, 90, 2)
    resolution = ResourceAccessResolution(selected["resource"], selected["identity"], NOW,
                                         "conflict", "owner", "unspecified", supports)
    monkeypatch.setattr(preview, "resolve_resource_access", lambda *args, **kwargs: resolution)
    planner = Mock(wraps=preview.plan_bola_matrix)
    monkeypatch.setattr(preview, "plan_bola_matrix", planner)
    with SessionLocal() as db: result = call(preview, db, selected)
    assert result.facts == (BOLAMatrixAccessFact(selected["endpoint"], selected["resource"],
        selected["identity"], "bearer", "conflict", "owner", "unspecified", supports),)
    assert result.candidates == ()
    planner.assert_called_once_with(facts=result.facts)


def test_512_selected_identities_accepted_with_real_resolution(preview, selected, monkeypatch):
    with SessionLocal() as db:
        identities = [Identity(target_id=selected["target"], name=f"bounded-{i}", auth_type="bearer", is_active=True)
                      for i in range(512)]
        db.add_all(identities)
        db.commit()
        requested = [identity.id for identity in reversed(identities)]
    resolver = Mock(wraps=preview.resolve_resource_access)
    planner = Mock(wraps=preview.plan_bola_matrix)
    monkeypatch.setattr(preview, "resolve_resource_access", resolver)
    monkeypatch.setattr(preview, "plan_bola_matrix", planner)
    with SessionLocal() as db: result = call(preview, db, selected, test_identity_ids=iter(requested))
    assert [f.test_identity_id for f in result.facts] == requested
    assert all(f.resolution_state == "insufficient" for f in result.facts)
    assert result.candidates == ()
    assert resolver.call_count == 512
    planner.assert_called_once_with(facts=result.facts)


def test_real_256_assertions_then_257_fails_entire_preview(preview, selected, monkeypatch):
    with SessionLocal() as db:
        rows = [ResourceAccessAssertion(resource_id=selected["resource"], test_identity_id=selected["identity"],
            relationship="owner", expected_access="denied", provenance="human_verified", confidence=i % 101,
            verification_state="verified", asserted_at=NOW) for i in range(256)]
        db.add_all(rows)
        db.commit()
        supporting = tuple(row.id for row in rows)
    with SessionLocal() as db: result = call(preview, db, selected)
    assert result.facts[0].supporting_assertion_ids == supporting
    assert result.candidates[0].expected_access == "denied"
    add_assertion(selected, expected_access="denied")
    monkeypatch.setattr(preview, "plan_bola_matrix", fail)
    before = snapshot()
    with SessionLocal() as db:
        error = assert_code(preview, "resource_access_resolution_limit_exceeded", lambda: call(preview, db, selected,
            test_identity_ids=[selected["anonymous"], selected["identity"]]))
        assert isinstance(error, ResourceAccessResolutionError)
    assert snapshot() == before


def test_other_resolver_errors_propagate_unchanged_without_partial_preview(preview, selected, monkeypatch):
    error = ResourceAccessResolutionError("resource_not_found", 404)
    resolver = Mock(side_effect=error)
    monkeypatch.setattr(preview, "resolve_resource_access", resolver)
    monkeypatch.setattr(preview, "plan_bola_matrix", fail)
    with SessionLocal() as db:
        assert assert_code(preview, error.code, lambda: call(preview, db, selected)) is error


def test_fresh_calls_are_deterministic_and_never_cache_truth_or_identity_metadata(preview, selected):
    def read():
        with SessionLocal() as db: return call(preview, db, selected)
    first = read()
    assert first == read()
    assert first.facts[0].resolution_state == "insufficient"
    add_assertion(selected, relationship="non_owner", expected_access="allowed")
    second = read()
    assert second == read() and second != first
    assert second.candidates[0].candidate_kind == "cross_subject_access"
    with SessionLocal() as db:
        db.get(Identity, selected["identity"]).auth_type = "anonymous"
        db.commit()
    assert read().candidates[0].candidate_kind == "anonymous_access"
    with SessionLocal() as db:
        db.get(Identity, selected["identity"]).is_active = False
        db.commit()
    assert_code(preview, "bola_matrix_identity_inactive", read)


@pytest.mark.parametrize("pending", ["new", "dirty", "deleted"])
def test_unclean_session_rejected_without_sql_flush_or_discard(preview, selected, monkeypatch, pending):
    before = snapshot()
    with Session(engine, autoflush=True) as db:
        if pending == "new":
            obj = Endpoint(target_id=selected["target"], path="/pending", method="GET", parameters=[])
            db.add(obj)
        else:
            obj = db.get(Endpoint, selected["endpoint"])
            if pending == "dirty": obj.path = "/caller-change"
            else: db.delete(obj)
        session_state = (tuple(db.new), tuple(db.dirty), tuple(db.deleted), dict(obj.__dict__))
        with monkeypatch.context() as guard:
            no_reads(preview, guard, db)
            assert_code(preview, "bola_matrix_preview_session_not_clean", lambda: call(preview, db, selected))
            assert (tuple(db.new), tuple(db.dirty), tuple(db.deleted), dict(obj.__dict__)) == session_state
    assert snapshot() == before


from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401


@pytest.mark.parametrize("network_mode", ["private_local", "external_public_authorized"])
def test_exact_minimal_reads_no_autoflush_mutation_credentials_or_network(
        preview, selected, evidence_pair, monkeypatch, network_mode):
    from tests.api.test_finding_structured_evidence import analyze
    assert analyze(evidence_pair).status_code == 200  # Nonempty M13 tables in the snapshot.
    assertion_id = add_assertion(selected, expected_access="denied")
    make_observed_candidate(selected)  # Nonempty selected source TestCase/TestRun.
    with SessionLocal() as db:
        db.get(Target, selected["target"]).network_mode = network_mode
        db.commit()
    before = snapshot()
    config_before = settings.model_dump()
    resolver = preview.resolve_resource_access
    resolving = False
    def exact_resolver(db, resource_id, test_identity_id, evaluation_time):
        nonlocal resolving
        assert resource_id == selected["resource"]
        assert test_identity_id == selected["identity"]
        assert evaluation_time is NOW
        resolving = True
        try:
            return resolver(db, resource_id, test_identity_id, evaluation_time)
        finally:
            resolving = False
    own_reads = []
    def audit(conn, statement, multiparams, params, execution_options):
        assert statement.is_select, "preview issued a write or changed transaction settings"
        tables = statement.get_final_froms()
        assert len(tables) == 1
        table = tables[0].name
        assert table in {"endpoints", "resources", "test_identities", "resource_access_assertions"}
        if resolving:
            assert table != "endpoints"
            if table == "resource_access_assertions":
                assert statement._limit_clause.value == 257
            return
        assert table != "resource_access_assertions", "service bypassed M12 resolver"
        allowed_columns = {
            "endpoints": {"id", "target_id"}, "resources": {"id", "target_id"},
            "test_identities": {"id", "target_id", "auth_type", "is_active"},
        }
        assert {c.name for c in statement.selected_columns} <= allowed_columns[table]
        assert statement.whereclause.left.name == "id"
        assert statement.whereclause.right.value == {
            "endpoints": selected["endpoint"], "resources": selected["resource"],
            "test_identities": selected["identity"],
        }[table]
        own_reads.append(table)
    with Session(engine, autoflush=True) as db:
        db.connection()  # The caller opens and owns this read transaction.
        transaction = db.get_transaction()
        with monkeypatch.context() as guard:
            guard.setattr(preview, "resolve_resource_access", exact_resolver)
            for name in ("flush", "commit", "rollback", "close", "add", "add_all", "delete", "begin", "begin_nested",
                         "expire", "expire_all", "expunge", "expunge_all"):
                guard.setattr(db, name, fail)
            for model, name in ((Resource, "owner_identity_id"), (Resource, "external_id"),
                                (Identity, "credentials"), (Identity, "credential_bindings"), (Identity, "role"),
                                (Identity, "name"), (Endpoint, "request_body"), (Endpoint, "security"),
                                (Endpoint, "parameters"), (Endpoint, "path"), (Endpoint, "method"), (StoredRun, "response_body")):
                guard.setattr(model, name, property(fail))
            for path in (
                "app.domain.ownership.determine_ownership_relation", "app.generators.bola.determine_ownership_relation",
                "app.generators.bola.detect_resource_binding", "app.generators.bola.generate_bola_test_cases",
                "app.auth.context.build_authentication_context", "app.credentials.bearer.BearerCredentialService.resolve",
                "app.credentials.bearer.BearerCredentialService.resolve_binding",
                "app.network_safety.gateway.NetworkGateway.request", "app.executors.http.PolicyEnforcedHTTPExecutor.execute",
                "app.services.test_execution.TestExecutionService.execute", "app.scanners.openapi.OpenAPIScanner.scan",
                "app.services.ai_analysis.AIAnalysisService.analyze_finding", "app.ai.mock_provider.MockAIProvider.analyze",
                "httpx.Client.send", "httpx.AsyncClient.send", "httpcore.ConnectionPool.stream",
                "dns.resolver.resolve", "dns.resolver.Resolver.resolve", "socket.getaddrinfo", "socket.gethostbyname",
                "socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex",
            ):
                guard.setattr(path, fail)
            event.listen(engine, "before_execute", audit)
            try:
                result = call(preview, db, selected)
            finally:
                event.remove(engine, "before_execute", audit)
            assert db.get_transaction() is transaction
            assert transaction.is_active
            assert db.autoflush is True
            assert not db.new and not db.dirty and not db.deleted
    assert own_reads == ["endpoints", "resources", "test_identities"]
    assert result.candidates[0].supporting_assertion_ids == (assertion_id,)
    assert snapshot() == before
    assert settings.model_dump() == config_before
