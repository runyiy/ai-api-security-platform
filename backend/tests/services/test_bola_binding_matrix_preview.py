"""Bounded composition contracts over existing reviewed slots and access previews."""
import ast
from dataclasses import FrozenInstanceError, asdict, fields, replace
from datetime import datetime, timedelta, timezone
import importlib
import inspect
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import delete, event
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    Endpoint, EndpointResourceBinding as Binding, Resource, ResourceAccessAssertion as Assertion,
    Target, TestIdentity as Identity, TestRun as StoredRun,
)
from app.db.session import SessionLocal, engine
from app.generators.bola_matrix import BOLAMatrixAccessFact, BOLAMatrixPlanningError
from app.main import app
from app.services.bola_binding_selection import BOLAReviewedBindingSelection, BOLABindingSelectionError
from app.services.bola_matrix_preview import BOLAMatrixPreview, BOLAMatrixPreviewError
from app.services.resource_access_resolution import ResourceAccessResolutionError
from tests.api.test_resource_access_resolution import NOW, make_pair, cleanup, add_assertion
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401
from tests.services.test_bola_binding_selection import add_binding, change, no_session_work
from tests.services.test_bola_matrix_preview import snapshot


@pytest.fixture
def composer():
    return importlib.import_module("app.services.bola_binding_matrix_preview")


@pytest.fixture
def selected(composer):
    ids, other = make_pair(), make_pair()
    try:
        with SessionLocal() as db:
            endpoint = Endpoint(target_id=ids["target"], path="/projects/{project_id}/tasks/{task_id}",
                                method="GET", parameters=[{"in": "query", "name": "project_id",
                                "schema": {"$ref": "https://secret.example.test"}, "example": "example-secret"}],
                                request_body={"secret": "body-secret"}, security=[{"secret": []}])
            anonymous = Identity(target_id=ids["target"], name="owner-admin", role="user",
                                 auth_type="anonymous", credentials={"token": "credential-secret"}, is_active=True)
            peer = Identity(target_id=ids["target"], name="owner", role="user", auth_type="bearer", is_active=True)
            resource = Resource(target_id=ids["target"], resource_type="unrelated_type", external_id="resource-secret",
                                owner_identity_id=ids["identity"])
            db.add_all([endpoint, anonymous, peer, resource])
            db.commit()
            ids.update(endpoint=endpoint.id, anonymous=anonymous.id, peer=peer.id, second_resource=resource.id,
                       other_resource=other["resource"], other_identity=other["identity"])
        ids["bindings"] = [add_binding(ids["endpoint"], location=location, selector=selector)
                           for location, selector in (("path", "project_id"), ("path", "task_id"), ("query", "project_id"))]
        yield ids
    finally:
        with SessionLocal() as db:
            if "endpoint" in ids:
                db.execute(delete(Binding).where(Binding.endpoint_id == ids["endpoint"]))
                db.commit()
        cleanup([ids["target"], other["target"]])


def assignments(composer, selected):
    return tuple(composer.BOLAResourceSlotAssignment(b, r) for b, r in zip(selected["bindings"],
                 (selected["resource"], selected["second_resource"], selected["resource"]), strict=True))


def invoke(composer, db, selected, **overrides):
    return composer.preview_bola_binding_matrix(db, **{
        "endpoint_id": selected["endpoint"], "assignments": assignments(composer, selected),
        "test_identity_ids": (selected["anonymous"], selected["identity"], selected["peer"]),
        "evaluation_time": NOW, **overrides,
    })


def fail(*args, **kwargs):
    pytest.fail("multi-binding preview crossed a forbidden boundary")


def assert_code(composer, code, action):
    with pytest.raises((composer.BOLABindingMatrixPreviewError, BOLAMatrixPlanningError,
                        BOLABindingSelectionError, BOLAMatrixPreviewError, ResourceAccessResolutionError)) as error:
        action()
    assert error.value.code == str(error.value) == code
    assert error.value.args == (code,)
    return error.value


def empty_preview(endpoint_id, resource_id, identities, at):
    return BOLAMatrixPreview(endpoint_id, resource_id, at, tuple(BOLAMatrixAccessFact(
        endpoint_id, resource_id, i, "bearer", "insufficient", "unspecified", "unspecified", ())
        for i in identities), ())


def mock_services(composer, monkeypatch):
    selector = Mock(side_effect=lambda db, *, endpoint_id, binding_id: BOLAReviewedBindingSelection(
        endpoint_id, binding_id, "query", f"slot{binding_id}", "confirmed"))
    preview = Mock(side_effect=lambda db, *, endpoint_id, resource_id, test_identity_ids, evaluation_time:
                   empty_preview(endpoint_id, resource_id, test_identity_ids, evaluation_time))
    monkeypatch.setattr(composer, "select_bola_binding", selector)
    monkeypatch.setattr(composer, "preview_bola_matrix", preview)
    return selector, preview


def structural_call(composer, db, **overrides):
    return composer.preview_bola_binding_matrix(db, **{
        "endpoint_id": 1, "assignments": [composer.BOLAResourceSlotAssignment(2, 3)],
        "test_identity_ids": [4], "evaluation_time": NOW, **overrides,
    })


def prohibit_work(composer, guard, db):
    no_session_work(guard, db)
    guard.setattr(composer, "select_bola_binding", fail)
    guard.setattr(composer, "preview_bola_matrix", fail)


def test_signature_frozen_input_and_isolated_composition(composer):
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["f0b2d4e6a8c0"]
    signature = inspect.signature(composer.preview_bola_binding_matrix)
    assert list(signature.parameters) == ["db", "endpoint_id", "assignments", "test_identity_ids", "evaluation_time"]
    assert all(p.default is inspect.Parameter.empty for p in signature.parameters.values())
    assert {path: set(operations) for path, operations in app.openapi()["paths"].items()
            if "matrix" in path} == {"/api/bola-matrix/preview": {"post"}}
    source = composer.BOLAResourceSlotAssignment(1, 2)
    assert asdict(source) == {"binding_id": 1, "resource_id": 2}
    for field in fields(source):
        with pytest.raises(FrozenInstanceError): setattr(source, field.name, 3)
    assert composer.MAX_BOLA_MATRIX_ASSIGNMENTS == 32
    assert composer.MAX_BOLA_MATRIX_FACTS == 512
    tree = ast.parse(inspect.getsource(composer))
    imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imports |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imports <= {"collections.abc", "dataclasses", "datetime", "typing", "sqlalchemy.orm",
                       "app.generators.bola_matrix", "app.services.bola_binding_selection", "app.services.bola_matrix_preview"}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"select", "execute", "scalar", "scalars", "query", "Resource", "Endpoint", "TestIdentity",
        "resolve_resource_access", "plan_bola_matrix", "validate_resource_binding_selector", "owner_identity_id",
        "external_id", "credentials", "role", "request_body", "response_body", "security", "HTTPException",
        "flush", "commit", "rollback", "close", "begin", "begin_nested", "now", "utcnow"}


@pytest.mark.parametrize("invalid", [True, False, "1", 1.0, None, 0, -1])
@pytest.mark.parametrize("field", ["endpoint_id", "binding_id", "resource_id", "identity"])
def test_strict_ids_before_dependencies_or_sql(composer, monkeypatch, invalid, field):
    with Session() as db:
        with monkeypatch.context() as guard:
            prohibit_work(composer, guard, db)
            if field in ("binding_id", "resource_id"):
                # Constructor validation and service revalidation use the same stable code.
                assert_code(composer, "bola_binding_matrix_invalid_input", lambda:
                    composer.BOLAResourceSlotAssignment(**{"binding_id": 1, "resource_id": 2, field: invalid}))
                broken = composer.BOLAResourceSlotAssignment(1, 2)
                object.__setattr__(broken, field, invalid)
                values = {"assignments": [broken]}
            else:
                values = {"test_identity_ids": [invalid]} if field == "identity" else {field: invalid}
            code = "bola_matrix_invalid_access_fact" if field == "identity" else "bola_binding_matrix_invalid_input"
            assert_code(composer, code, lambda: structural_call(composer, db, **values))


@pytest.mark.parametrize("value", [None, True, 1, "", "secret", b"123", {}, [], [None], [{"binding_id": 1, "resource_id": 2}], [(1, 2)]])
def test_invalid_assignments_and_empty_before_work(composer, monkeypatch, value):
    with Session() as db:
        with monkeypatch.context() as guard:
            prohibit_work(composer, guard, db)
            assert_code(composer, "bola_binding_matrix_invalid_input", lambda: structural_call(composer, db, assignments=value))


@pytest.mark.parametrize("value", [None, True, 1, "", "123", b"123"])
def test_invalid_identity_iterables(composer, monkeypatch, value):
    with Session() as db:
        with monkeypatch.context() as guard:
            prohibit_work(composer, guard, db)
            assert_code(composer, "bola_matrix_invalid_access_fact", lambda: structural_call(composer, db, test_identity_ids=value))


@pytest.mark.parametrize(("value", "code"), [(None, "bola_matrix_evaluation_time_invalid"),
    ("2030-06-01", "bola_matrix_evaluation_time_invalid"), (True, "bola_matrix_evaluation_time_invalid"),
    (NOW.date(), "bola_matrix_evaluation_time_invalid"), (NOW.replace(tzinfo=None), "evaluation_time_timezone_required")])
def test_required_aware_time_before_work(composer, monkeypatch, value, code):
    with Session() as db:
        with monkeypatch.context() as guard:
            prohibit_work(composer, guard, db)
            assert_code(composer, code, lambda: structural_call(composer, db, evaluation_time=value))
            with pytest.raises(TypeError):
                composer.preview_bola_binding_matrix(db, endpoint_id=1, assignments=[], test_identity_ids=[])


@pytest.mark.parametrize(("kind", "limit", "code"), [("assignments", 33, "bola_binding_matrix_assignment_limit_exceeded"),
                                                       ("test_identity_ids", 513, "bola_matrix_fact_limit_exceeded")])
def test_bounded_consumption_never_reads_one_more(composer, monkeypatch, kind, limit, code):
    consumed = []
    def source():
        for index in range(1, limit + 2):
            assert index <= limit
            consumed.append(index)
            yield composer.BOLAResourceSlotAssignment(index, 1) if kind == "assignments" else index
    with Session() as db:
        with monkeypatch.context() as guard:
            prohibit_work(composer, guard, db)
            assert_code(composer, code, lambda: structural_call(composer, db, **{kind: source()}))
    assert len(consumed) == limit


@pytest.mark.parametrize("kind", ["binding", "identity"])
def test_duplicate_ids_fail_before_any_work_even_if_skipped(composer, monkeypatch, kind):
    values = {"assignments": [composer.BOLAResourceSlotAssignment(1, 2)] * 2} if kind == "binding" else {"test_identity_ids": [7, 7]}
    code = "bola_binding_matrix_duplicate_binding" if kind == "binding" else "bola_matrix_duplicate_access_fact"
    with Session() as db:
        with monkeypatch.context() as guard:
            prohibit_work(composer, guard, db)
            assert_code(composer, code, lambda: structural_call(composer, db, **values))


@pytest.mark.parametrize(("slots", "identities"), [(32, 16), (2, 256), (1, 512), (32, 0)])
def test_maximum_requested_cells_accepted_with_skipped_facts_and_repeated_resource(composer, monkeypatch, slots, identities):
    selector, preview = mock_services(composer, monkeypatch)
    with Session() as db:
        with monkeypatch.context() as guard:
            no_session_work(guard, db)
            result = structural_call(composer, db, assignments=[composer.BOLAResourceSlotAssignment(i, 3) for i in range(1, slots + 1)],
                                     test_identity_ids=range(1, identities + 1))
    assert len(result.slots) == selector.call_count == slots
    assert preview.call_count == 1
    assert all(len(slot.preview.facts) == identities and slot.preview.candidates == () for slot in result.slots)
    assert all(slot.preview is result.slots[0].preview for slot in result.slots)


@pytest.mark.parametrize(("slots", "identities"), [(2, 257), (32, 17)])
def test_aggregate_bound_cannot_be_deduplicated_or_skipped(composer, monkeypatch, slots, identities):
    with Session() as db:
        with monkeypatch.context() as guard:
            prohibit_work(composer, guard, db)
            assert_code(composer, "bola_matrix_fact_limit_exceeded", lambda: structural_call(composer, db,
                assignments=[composer.BOLAResourceSlotAssignment(i, 3) for i in range(1, slots + 1)],
                test_identity_ids=range(1, identities + 1)))


def test_real_nested_path_mixed_query_independent_access_and_exact_calls(composer, selected, monkeypatch):
    ids = selected
    supports = [add_assertion(dict(resource=resource, identity=identity), relationship=relationship, expected_access=access)
                for resource, identity, relationship, access in (
                    (ids["resource"], ids["identity"], "owner", "denied"),
                    (ids["resource"], ids["anonymous"], "unspecified", "allowed"),
                    (ids["second_resource"], ids["identity"], "non_owner", "allowed"),
                    (ids["second_resource"], ids["peer"], "shared", "denied"),
                    (ids["second_resource"], ids["anonymous"], "unspecified", "denied"))]
    calls, bindings, previews = [], [], []
    select_binding, preview_matrix = composer.select_bola_binding, composer.preview_bola_matrix
    def select_spy(db, **kwargs):
        assert db is caller_session and db.autoflush is False
        calls.append(("binding", kwargs))
        value = select_binding(db, **kwargs)
        bindings.append(value)
        return value
    def preview_spy(db, **kwargs):
        assert db is caller_session and db.autoflush is False
        calls.append(("preview", kwargs))
        value = preview_matrix(db, **kwargs)
        previews.append(value)
        return value
    monkeypatch.setattr(composer, "select_bola_binding", select_spy)
    monkeypatch.setattr(composer, "preview_bola_matrix", preview_spy)
    at = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
    requested = (ids["peer"], ids["anonymous"], ids["identity"])
    proposed = assignments(composer, ids)
    # Assignment order differs from binding IDs and Resource first occurrences.
    proposed = (proposed[1], proposed[2], proposed[0])
    with SessionLocal() as db:
        caller_session = db
        result = invoke(composer, db, ids, assignments=iter(proposed), test_identity_ids=iter(requested), evaluation_time=at)
    assert [kind for kind, _ in calls] == ["binding"] * 3 + ["preview"] * 2
    assert [kwargs for kind, kwargs in calls if kind == "binding"] == [dict(endpoint_id=ids["endpoint"], binding_id=a.binding_id) for a in proposed]
    assert [kwargs["resource_id"] for kind, kwargs in calls if kind == "preview"] == [ids["second_resource"], ids["resource"]]
    for kind, kwargs in calls:
        if kind == "preview":
            assert kwargs == dict(endpoint_id=ids["endpoint"], resource_id=kwargs["resource_id"], test_identity_ids=requested, evaluation_time=at)
            assert kwargs["evaluation_time"] is at
    assert result.endpoint_id == ids["endpoint"] and result.evaluation_time is at
    assert result.test_identity_ids == requested
    assert [s.binding for s in result.slots] == bindings
    assert all(s.binding is b for s, b in zip(result.slots, bindings, strict=True))
    assert result.slots[0].preview is previews[0]
    assert result.slots[1].preview is result.slots[2].preview is previews[1]
    assert [(c.candidate_kind, c.expected_access, c.supporting_assertion_ids) for c in previews[0].candidates] == [
        ("shared_access", "denied", (supports[3],)), ("anonymous_access", "denied", (supports[4],)),
        ("cross_subject_access", "allowed", (supports[2],))]
    assert [(c.candidate_kind, c.expected_access, c.supporting_assertion_ids) for c in previews[1].candidates] == [
        ("anonymous_access", "allowed", (supports[1],)), ("owner_access", "denied", (supports[0],))]
    assert previews[1].facts[0].resolution_state == "insufficient"  # Same role, owner-like name and legacy owner grant nothing.
    assert {f.name for f in fields(result)} == {"endpoint_id", "evaluation_time", "test_identity_ids", "slots"}
    assert isinstance(result.slots, tuple) and isinstance(result.test_identity_ids, tuple)
    assert {f.name for f in fields(result.slots[0])} == {"binding", "preview"}
    for value in (result, result.slots[0], result.slots[0].binding, result.slots[0].preview):
        for field in fields(value):
            with pytest.raises(FrozenInstanceError): setattr(value, field.name, None)
    with pytest.raises(TypeError): result.slots[0] = result.slots[1]
    for forbidden in ("secret", "parent", "authorized", "executable", "expected_status", "https://", "external_id"):
        assert forbidden not in repr(asdict(result))


def test_conflicts_unspecified_and_insufficient_facts_retained(composer, selected):
    a = add_assertion(selected, expected_access="denied", confidence=0)
    b = add_assertion(selected, expected_access="allowed", confidence=100)
    add_assertion(dict(selected, resource=selected["second_resource"]), expected_access="unspecified")
    with SessionLocal() as db: result = invoke(composer, db, selected)
    assert result.slots[0].preview.facts[1].resolution_state == "conflict"
    assert result.slots[0].preview.facts[1].supporting_assertion_ids == (a, b)
    assert result.slots[1].preview.facts[1].expected_access == "unspecified"
    assert all(len(slot.preview.facts) == 3 and slot.preview.candidates == () for slot in result.slots)


def test_same_semantic_slot_different_provenance_fails_before_access(composer, selected, monkeypatch):
    alternate = add_binding(selected["endpoint"], selector="project_id", provenance="openapi_inferred", confidence=100)
    monkeypatch.setattr(composer, "preview_bola_matrix", fail)
    with SessionLocal() as db:
        assert_code(composer, "bola_binding_matrix_duplicate_slot", lambda: invoke(composer, db, selected,
            assignments=[composer.BOLAResourceSlotAssignment(b, selected["resource"]) for b in (selected["bindings"][0], alternate)]))


@pytest.mark.parametrize(("case", "code"), [
    ("missing", "bola_binding_not_found"), ("candidate", "bola_binding_not_confirmed"), ("body", "bola_binding_location_unsupported"),
    ("wrong_endpoint", "bola_binding_endpoint_mismatch"), ("declaration", "bola_binding_selector_not_declared"),
    ("query_limit", "bola_binding_parameter_limit_exceeded"), ("selector", "bola_binding_selector_invalid")])
def test_late_binding_failure_aborts_before_access_without_fallback(composer, selected, monkeypatch, case, code):
    chosen = selected["bindings"][-1]
    if case == "missing": selected["bindings"][-1] = 999999999
    if case == "candidate": change(Binding, chosen, review_state="candidate", confidence=100)
    if case == "body": change(Binding, chosen, location="body", selector="/id")
    if case == "selector": change(Binding, chosen, selector="api_key=selector-secret")
    if case == "declaration": change(Endpoint, selected["endpoint"], parameters=[])
    if case == "query_limit": change(Endpoint, selected["endpoint"], parameters=[{"name": "project_id", "in": "query"}] * 257)
    if case == "wrong_endpoint":
        with SessionLocal() as db:
            endpoint = Endpoint(target_id=selected["target"], path="/other/{project_id}", method="GET", parameters=[])
            db.add(endpoint)
            db.flush()
            db.get(Binding, chosen).endpoint_id = endpoint.id
            db.commit()
    before = snapshot()
    monkeypatch.setattr(composer, "preview_bola_matrix", fail)
    try:
        with SessionLocal() as db:
            assert_code(composer, code, lambda: invoke(composer, db, selected))
        assert snapshot() == before
    finally:
        if case == "wrong_endpoint": change(Binding, chosen, endpoint_id=selected["endpoint"])


@pytest.mark.parametrize(("case", "code"), [("resource", "resource_not_found"),
    ("resource_target", "bola_matrix_endpoint_resource_target_mismatch"), ("identity", "test_identity_not_found"),
    ("identity_target", "resource_identity_target_mismatch"), ("inactive", "bola_matrix_identity_inactive")])
def test_access_metadata_errors_propagate_without_partial_result(composer, selected, monkeypatch, case, code):
    values = {}
    if case in ("resource", "resource_target"):
        proposed = list(assignments(composer, selected))
        proposed[1] = replace(proposed[1], resource_id=999999999 if case == "resource" else selected["other_resource"])
        values["assignments"] = proposed
    if case in ("identity", "identity_target"):
        values["test_identity_ids"] = [selected["identity"], 999999999 if case == "identity" else selected["other_identity"]]
    if case == "inactive": change(Identity, selected["peer"], is_active=False)
    calls = Mock(wraps=composer.preview_bola_matrix)
    monkeypatch.setattr(composer, "preview_bola_matrix", calls)
    before = snapshot()
    with SessionLocal() as db: assert_code(composer, code, lambda: invoke(composer, db, selected, **values))
    assert calls.call_count == (2 if case in ("resource", "resource_target") else 1)
    assert snapshot() == before


def test_empty_identities_still_validate_all_resources_and_bindings(composer, selected, monkeypatch):
    monkeypatch.setattr("app.services.bola_matrix_preview.resolve_resource_access", fail)
    with SessionLocal() as db:
        result = invoke(composer, db, selected, test_identity_ids=[])
        assert len(result.slots) == 3
        assert all(slot.preview.facts == slot.preview.candidates == () for slot in result.slots)
        proposed = list(assignments(composer, selected))
        proposed[1] = replace(proposed[1], resource_id=999999999)
        assert_code(composer, "resource_not_found", lambda: invoke(composer, db, selected, assignments=proposed, test_identity_ids=[]))
        assert_code(composer, "endpoint_not_found", lambda: invoke(composer, db, selected, endpoint_id=999999999, test_identity_ids=[]))


@pytest.mark.parametrize("mismatch", ["endpoint", "binding", "review", "location", "type"])
def test_binding_dependency_mismatch_before_access(composer, monkeypatch, mismatch):
    value = BOLAReviewedBindingSelection(1, 2, "path", "id", "confirmed")
    changes = {"endpoint": {"endpoint_id": 99}, "binding": {"binding_id": 99},
               "review": {"review_state": "candidate"}, "location": {"location": "body"}}
    value = None if mismatch == "type" else replace(value, **changes[mismatch])
    monkeypatch.setattr(composer, "select_bola_binding", Mock(return_value=value))
    monkeypatch.setattr(composer, "preview_bola_matrix", fail)
    with Session() as db:
        assert_code(composer, "bola_binding_matrix_dependency_mismatch", lambda: structural_call(composer, db))


@pytest.mark.parametrize("mismatch", ["endpoint", "resource", "time", "naive", "invalid_time", "identity_order",
                                     "identity_missing", "identity_extra", "fact_resource", "type"])
def test_access_dependency_mismatch_aborts(composer, monkeypatch, mismatch):
    _, preview = mock_services(composer, monkeypatch)
    value = empty_preview(1, 3, (8, 4), NOW)
    changes = {"endpoint": {"endpoint_id": 99}, "resource": {"resource_id": 99},
        "time": {"evaluation_time": NOW + timedelta(microseconds=1)}, "naive": {"evaluation_time": NOW.replace(tzinfo=None)},
        "invalid_time": {"evaluation_time": "invalid"}, "identity_order": {"facts": value.facts[::-1]},
        "identity_missing": {"facts": value.facts[:1]}, "identity_extra": {"facts": value.facts + value.facts[:1]},
        "fact_resource": {"facts": (replace(value.facts[0], resource_id=99), value.facts[1])}}
    preview.side_effect = None
    preview.return_value = None if mismatch == "type" else replace(value, **changes[mismatch])
    with Session() as db:
        assert_code(composer, "bola_binding_matrix_dependency_mismatch", lambda: structural_call(composer, db, test_identity_ids=[8, 4]))


def test_evaluation_instant_preserves_time_and_rejects_wrong_dst_fold(composer, monkeypatch):
    _, preview = mock_services(composer, monkeypatch)
    at = datetime(2030, 11, 3, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=0)
    preview.side_effect = None
    value = empty_preview(1, 3, (4,), at.astimezone(timezone.utc))
    preview.return_value = value
    with Session() as db:
        result = structural_call(composer, db, evaluation_time=at)
        assert result.evaluation_time is at and result.slots[0].preview is value
        preview.return_value = replace(value, evaluation_time=at.replace(fold=1))
        assert_code(composer, "bola_binding_matrix_dependency_mismatch", lambda: structural_call(composer, db, evaluation_time=at))


def test_opaque_provenance_and_skipped_facts_are_not_rewritten(composer, monkeypatch):
    _, preview = mock_services(composer, monkeypatch)
    value = empty_preview(1, 3, (4,), NOW)
    value = replace(value, facts=(replace(value.facts[0], resolution_state="conflict", supporting_assertion_ids=(90, 1, 90, 2)),))
    preview.side_effect = None
    preview.return_value = value
    with Session() as db: result = structural_call(composer, db)
    assert result.slots[0].preview is value
    assert result.slots[0].preview.facts[0].supporting_assertion_ids == (90, 1, 90, 2)


@pytest.mark.parametrize("phase", ["binding", "preview"])
def test_late_dependency_exception_propagates_same_object_no_partial_result(composer, monkeypatch, phase):
    selector, preview = mock_services(composer, monkeypatch)
    error = BOLABindingSelectionError("bola_binding_not_confirmed") if phase == "binding" else ResourceAccessResolutionError("resource_access_resolution_limit_exceeded", 409)
    target = selector if phase == "binding" else preview
    original = target.side_effect
    def late(db, **kwargs):
        if target.call_count == 2: raise error
        return original(db, **kwargs)
    target.side_effect = late
    with Session() as db:
        caught = assert_code(composer, error.code, lambda: structural_call(composer, db,
            assignments=[composer.BOLAResourceSlotAssignment(2, 3), composer.BOLAResourceSlotAssignment(5, 6)]))
    assert caught is error
    assert selector.call_count == 2 and preview.call_count == (0 if phase == "binding" else 2)


@pytest.mark.parametrize(("asserted", "start", "end", "observed", "eligible"), [
    (0, None, None, None, True), (1, -10, 10, -100, False), (-10, 0, 10, None, True),
    (-10, 1, 10, None, False), (-10, -5, 0, None, False), (-10, -5, 1, None, True),
    (-10, None, None, 100, True), (-10, None, None, -100, True),
])
def test_real_historical_eligibility_remains_per_resource(composer, selected, asserted, start, end, observed, eligible):
    def at(delta): return None if delta is None else NOW + timedelta(seconds=delta)
    pair = dict(selected, resource=selected["second_resource"])
    chosen = add_assertion(pair, expected_access="denied", asserted_at=at(asserted), valid_from=at(start), valid_until=at(end))
    change(Assertion, chosen, observed_at=at(observed))
    add_assertion(pair, verification_state="candidate", confidence=100)
    add_assertion(pair, verification_state="rejected", confidence=100)
    with SessionLocal() as db: result = invoke(composer, db, selected)
    assert result.slots[0].preview.candidates == ()
    assert result.slots[1].preview.facts[1].supporting_assertion_ids == ((chosen,) if eligible else ())
    assert len(result.slots[1].preview.candidates) == int(eligible)


def test_real_256_assertions_accepted_then_257_fails_late(composer, selected, monkeypatch):
    with SessionLocal() as db:
        rows = [Assertion(resource_id=selected["second_resource"], test_identity_id=selected["identity"],
                relationship="owner", expected_access="denied", provenance="human_verified", confidence=0,
                verification_state="verified", asserted_at=NOW) for _ in range(256)]
        db.add_all(rows)
        db.commit()
        supports = tuple(row.id for row in rows)
    with SessionLocal() as db: result = invoke(composer, db, selected)
    assert result.slots[1].preview.facts[1].supporting_assertion_ids == supports
    add_assertion(dict(selected, resource=selected["second_resource"]), expected_access="denied")
    preview = Mock(wraps=composer.preview_bola_matrix)
    monkeypatch.setattr(composer, "preview_bola_matrix", preview)
    before = snapshot()
    with SessionLocal() as db:
        assert_code(composer, "resource_access_resolution_limit_exceeded", lambda: invoke(composer, db, selected))
    assert preview.call_count == 2
    assert snapshot() == before


@pytest.mark.parametrize("mutation", ["review", "declaration", "assertion", "active"])
def test_fresh_calls_notice_changes_without_cross_call_cache(composer, selected, monkeypatch, mutation):
    selector = Mock(wraps=composer.select_bola_binding)
    preview = Mock(wraps=composer.preview_bola_matrix)
    monkeypatch.setattr(composer, "select_bola_binding", selector)
    monkeypatch.setattr(composer, "preview_bola_matrix", preview)
    def read():
        with SessionLocal() as db: return invoke(composer, db, selected)
    first, second = read(), read()
    assert first == second and first.slots[0].preview is not second.slots[0].preview
    assert selector.call_count == 6 and preview.call_count == 4
    if mutation == "review":
        change(Binding, selected["bindings"][1], review_state="rejected")
        assert_code(composer, "bola_binding_not_confirmed", read)
    elif mutation == "declaration":
        change(Endpoint, selected["endpoint"], parameters=[])
        assert_code(composer, "bola_binding_selector_not_declared", read)
    elif mutation == "active":
        change(Identity, selected["peer"], is_active=False)
        assert_code(composer, "bola_matrix_identity_inactive", read)
    else:
        add_assertion(selected, expected_access="denied")
        updated = read()
        assert updated != first and updated.slots[0].preview.candidates[0].expected_access == "denied"


@pytest.mark.parametrize("pending", ["new", "dirty", "deleted"])
def test_unclean_session_rejected_without_touching_caller_changes(composer, selected, monkeypatch, pending):
    before = snapshot()
    with Session(engine, autoflush=True) as db:
        if pending == "new":
            obj = Endpoint(target_id=selected["target"], path="/pending", method="GET", parameters=[])
            db.add(obj)
        else:
            obj = db.get(Binding, selected["bindings"][0])
            if pending == "dirty": obj.review_state = "rejected"
            else: db.delete(obj)
        state = (tuple(db.new), tuple(db.dirty), tuple(db.deleted), dict(obj.__dict__))
        with monkeypatch.context() as guard:
            prohibit_work(composer, guard, db)
            assert_code(composer, "bola_binding_matrix_session_not_clean", lambda: invoke(composer, db, selected))
            assert (tuple(db.new), tuple(db.dirty), tuple(db.deleted), dict(obj.__dict__)) == state
    assert snapshot() == before


@pytest.mark.parametrize("network_mode", ["private_local", "external_public_authorized"])
@pytest.mark.parametrize("late_failure", [False, True])
def test_read_only_composition_no_direct_sql_or_prohibited_activity(
        composer, selected, evidence_pair, monkeypatch, network_mode, late_failure):
    from tests.api.test_finding_structured_evidence import analyze
    assert analyze(evidence_pair).status_code == 200
    add_assertion(selected, expected_access="denied")
    change(Target, selected["target"], network_mode=network_mode)
    proposed = list(assignments(composer, selected))
    if late_failure: proposed[1] = replace(proposed[1], resource_id=999999999)
    before, config_before = snapshot(), settings.model_dump()
    phase, reads = None, []
    def wrapped(name, delegate):
        def invoke_delegate(db, **kwargs):
            nonlocal phase
            assert db is caller and db.autoflush is False
            phase = name
            try: return delegate(db, **kwargs)
            finally: phase = None
        return invoke_delegate
    def audit(conn, statement, multiparams, params, execution_options):
        assert phase is not None, "composer performed direct SQL"
        assert statement.is_select and statement._for_update_arg is None
        tables = statement.get_final_froms()
        assert len(tables) == 1
        table = tables[0].name
        assert table in ({"endpoints", "endpoint_resource_bindings"} if phase == "binding" else
                         {"endpoints", "resources", "test_identities", "resource_access_assertions"})
        assert statement.whereclause is not None
        reads.append((phase, table))
    with Session(engine, autoflush=True) as caller:
        caller.connection()
        transaction = caller.get_transaction()
        with monkeypatch.context() as guard:
            guard.setattr(composer, "select_bola_binding", wrapped("binding", composer.select_bola_binding))
            guard.setattr(composer, "preview_bola_matrix", wrapped("preview", composer.preview_bola_matrix))
            for name in ("add", "add_all", "delete", "flush", "commit", "rollback", "close", "begin", "begin_nested",
                         "expire", "expire_all", "expunge", "expunge_all"):
                guard.setattr(caller, name, fail)
            for model, name in ((Resource, "owner_identity_id"), (Resource, "external_id"), (Identity, "credentials"),
                                (Identity, "credential_bindings"), (Identity, "role"), (Identity, "name"),
                                (Endpoint, "request_body"), (Endpoint, "security"), (StoredRun, "response_body")):
                guard.setattr(model, name, property(fail))
            for path in (
                "app.domain.ownership.determine_ownership_relation", "app.generators.bola.determine_ownership_relation",
                "app.generators.bola.detect_resource_binding", "app.generators.bola.generate_bola_test_cases",
                "app.services.openapi_binding_candidates.infer_openapi_binding_candidates",
                "app.services.openapi_body_binding_candidates.infer_openapi_body_binding_candidates",
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
                if late_failure:
                    assert_code(composer, "resource_not_found", lambda: invoke(composer, caller, selected, assignments=proposed))
                else:
                    assert len(invoke(composer, caller, selected, assignments=proposed).slots) == 3
            finally:
                event.remove(engine, "before_execute", audit)
            assert caller.get_transaction() is transaction and transaction.is_active
            assert caller.autoflush is True
            assert not caller.new and not caller.dirty and not caller.deleted
    assert any(phase == "binding" for phase, _ in reads) and any(phase == "preview" for phase, _ in reads)
    assert snapshot() == before
    assert settings.model_dump() == config_before
