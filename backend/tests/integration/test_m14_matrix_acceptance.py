"""Offline M14 acceptance: real PostgreSQL -> real HTTP pipeline, no Target IO."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import delete, event
from sqlalchemy.orm import Session

from app.api.routes import bola_matrix as route
from app.core.config import settings
from app.db.models import (
    Endpoint, EndpointResourceBinding as Binding, Resource,
    ResourceAccessAssertion as Assertion, Target, TestIdentity as Identity, TestRun as StoredRun,
)
from app.db.session import SessionLocal, engine
from app.main import app
from app.services import bola_binding_matrix_preview as composer, bola_matrix_preview as resource_preview
from tests.api.test_bola_matrix_preview import error
from tests.api.test_bola_matrix_preview_integration import assert_allowlists
from tests.api.test_finding_structured_evidence import analyze
from tests.api.test_resource_access_resolution import add_assertion
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401
from tests.services.test_bola_binding_selection import add_binding, change
from tests.services.test_bola_matrix_preview import snapshot
from tests.services.test_plan_execution_integration import approved_plan  # noqa: F401


URL = "/api/bola-matrix/preview"
AT = datetime(2030, 6, 1, 12, tzinfo=timezone.utc)
ORDER = ("shared", "admin", "anonymous", "cross", "owner")
POSITIONS = (("project_query", "project"), ("task_path", "task"), ("project_path", "project"))
client = TestClient(app)


@pytest.fixture
def matrix(approved_plan, evidence_pair):
    """Own only synthetic rows; established parent fixtures own their cleanup."""
    _, target_id, _, _ = approved_plan
    with SessionLocal() as db:
        # M13 source rows are synthetic; align their times without invoking execution.
        for key, offset in (("baseline", -1), ("probe", 0), ("decoy", 1)):
            db.get(StoredRun, evidence_pair[key]).executed_at = AT + timedelta(days=offset)
        endpoint = Endpoint(
            target_id=target_id, path="/projects/{project_id}/tasks/{task_id}", method="GET",
            parameters=[{"in": "query", "name": "project_id"}], requires_auth=True,
        )
        actors = {
            name: Identity(target_id=target_id, name="admin/owner" if name == "admin" else name,
                           role="admin" if name == "admin" else "member",
                           auth_type="anonymous" if name == "anonymous" else "bearer",
                           credentials={"token": "matrix-credential"}, is_active=True)
            for name in ("owner", "cross", "shared", "admin", "anonymous")
        }
        db.add_all([endpoint, *actors.values()])
        db.flush()
        resources = {
            name: Resource(target_id=target_id, resource_type=name, external_id=f"matrix-private-{name}",
                           owner_identity_id=actors["admin"].id)
            for name in ("project", "task")
        }
        bindings = {
            key: Binding(endpoint_id=endpoint.id, location=location, selector=selector,
                         review_state="confirmed", confidence=0, provenance="operator_supplied")
            for key, location, selector in (
                ("project_path", "path", "project_id"), ("task_path", "path", "task_id"),
                ("project_query", "query", "project_id"),
            )
        }
        db.add_all([*resources.values(), *bindings.values()])
        db.commit()
        ids = {
            "target": target_id, "endpoint": endpoint.id,
            "actors": {k: v.id for k, v in actors.items()},
            "resources": {k: v.id for k, v in resources.items()},
            "bindings": {k: v.id for k, v in bindings.items()},
            "other_resource": evidence_pair["resource"],
        }
    try:
        assert analyze(evidence_pair).status_code == 200
        # Nonempty verified M12 evidence on a distinct pair must not leak into the matrix.
        add_assertion({"resource": evidence_pair["resource"], "identity": evidence_pair["owner"]},
                      asserted_at=AT - timedelta(days=1))
        for resource in ("project", "task"):
            for state in ("candidate", "rejected"):
                assertion(ids, resource, "admin", verification_state=state, confidence=100)
        yield ids
    finally:
        with SessionLocal() as db:
            db.execute(delete(Assertion).where(Assertion.resource_id.in_([
                *ids["resources"].values(), evidence_pair["resource"],
            ])))
            db.execute(delete(Binding).where(Binding.endpoint_id == ids["endpoint"]))
            db.commit()
        # approved_plan removes its own Target graph; evidence_pair removes its own evidence graph.


def assertion(ids, resource, actor, **values):
    return add_assertion(
        {"resource": ids["resources"][resource], "identity": ids["actors"][actor]},
        **{"asserted_at": AT - timedelta(days=1), **values},
    )


def request_body(ids, *, at=AT):
    return {
        "endpoint_id": ids["endpoint"],
        "assignments": [{"binding_id": ids["bindings"][position], "resource_id": ids["resources"][resource]}
                        for position, resource in POSITIONS],
        "test_identity_ids": [ids["actors"][name] for name in ORDER],
        "evaluation_time": at.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat(),
    }


def preview(request, monkeypatch, *, no_sql=False):
    """Guard and snapshot only the actual preview, never fixture setup/cleanup."""
    before, config_before = snapshot(), settings.model_dump()
    for table in (
        "test_cases", "execution_plans", "plan_actions", "test_runs", "resource_access_assertions",
        "finding_evidence_records", "finding_evidence_excerpts", "finding_evidence_fingerprints",
        "finding_evidence_similarities", "finding_evidence_retention_bindings",
        "authorization_profiles", "authorization_revisions", "scopes",
    ):
        assert before[table], f"Acceptance snapshot must include nonempty {table}."

    def prohibited(*args, **kwargs):
        pytest.fail("Offline matrix acceptance crossed a prohibited boundary.")

    def audit(conn, statement, multiparams, params, execution_options):
        assert not no_sql, "Rejected request performed SQL."
        assert statement.is_select and statement._for_update_arg is None

    send = httpx.Client.send
    def local_client_only(self, *args, **kwargs):
        assert self is client, "Outbound HTTP client used."
        return send(self, *args, **kwargs)

    with monkeypatch.context() as guard:
        for name in ("add", "add_all", "delete", "flush", "commit", "rollback", "begin", "begin_nested"):
            guard.setattr(Session, name, prohibited)
        for model, name in (
            (Resource, "owner_identity_id"), (Resource, "external_id"), (Identity, "credentials"),
            (Identity, "credential_bindings"), (Identity, "name"), (Identity, "role"),
            (Endpoint, "request_body"), (Endpoint, "security"), (StoredRun, "response_body"),
        ):
            guard.setattr(model, name, property(prohibited))
        for path in (
            "app.auth.context.build_authentication_context",
            "app.credentials.bearer.BearerCredentialService.resolve",
            "app.credentials.bearer.BearerCredentialService.resolve_binding",
            "app.domain.ownership.determine_ownership_relation",
            "app.generators.bola.determine_ownership_relation",
            "app.generators.bola.detect_resource_binding",
            "app.generators.bola.generate_bola_test_cases",
            "app.executors.http.PolicyEnforcedHTTPExecutor.execute",
            "app.services.test_execution.TestExecutionService.execute",
            "app.services.plan_execution.PlanExecutionService.execute",
            "app.network_safety.gateway.NetworkGateway.request",
            "app.scanners.openapi.OpenAPIScanner.scan",
            "app.services.openapi_binding_candidates.infer_openapi_binding_candidates",
            "app.services.openapi_body_binding_candidates.infer_openapi_body_binding_candidates",
            "app.services.ai_analysis.AIAnalysisService.analyze_finding",
            "app.ai.mock_provider.MockAIProvider.analyze",
            "httpx.AsyncClient.send", "httpcore.ConnectionPool.stream",
            "dns.resolver.resolve", "dns.resolver.Resolver.resolve", "socket.getaddrinfo",
            "socket.gethostbyname", "socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex",
        ):
            guard.setattr(path, prohibited)
        guard.setattr(httpx.Client, "send", local_client_only)
        event.listen(engine, "before_execute", audit)
        try:
            response = client.post(URL, json=request)
        finally:
            event.remove(engine, "before_execute", audit)
    assert snapshot() == before
    assert settings.model_dump() == config_before
    assert response.headers["cache-control"] == "no-store"
    if response.status_code == 200:
        assert_allowlists(response.json())
    for forbidden in ("matrix-credential", "matrix-private", "Authorization", "response-secret",
                      "expected_status", "executable", "authorized", "https://"):
        assert forbidden not in response.text
    return response


@pytest.mark.parametrize("network_mode", ["private_local", "external_public_authorized"])
def test_independent_truth_nested_positions_and_real_pipeline(matrix, monkeypatch, network_mode):
    ids = matrix
    change(Target, ids["target"], network_mode=network_mode)
    # actor, relationship, project access, task access, exact candidate kind
    cases = (
        ("owner", "owner", "allowed", "denied", "owner_access"),
        ("cross", "non_owner", "denied", "allowed", "cross_subject_access"),
        ("shared", "shared", "allowed", "denied", "shared_access"),
        ("anonymous", "unspecified", "allowed", "denied", "anonymous_access"),
        ("admin", "unspecified", "unspecified", "unspecified", None),
    )
    supports = {}
    for actor, relationship, project_access, task_access, kind in cases:
        if kind is not None:
            for resource, access in (("project", project_access), ("task", task_access)):
                supports[resource, actor] = [assertion(ids, resource, actor,
                    relationship=relationship, expected_access=access)]
    returned = []
    real_composer = route.preview_bola_binding_matrix
    def compose(*args, **kwargs):
        result = real_composer(*args, **kwargs)
        returned.append((result, asdict(result)))
        return result
    compose_spy = Mock(side_effect=compose)
    monkeypatch.setattr(route, "preview_bola_binding_matrix", compose_spy)
    spies = []
    for module, name in (
        (composer, "select_bola_binding"), (composer, "preview_bola_matrix"),
        (resource_preview, "resolve_resource_access"), (resource_preview, "plan_bola_matrix"),
    ):
        spy = Mock(wraps=getattr(module, name))
        monkeypatch.setattr(module, name, spy)
        spies.append(spy)
    response = preview(request_body(ids), monkeypatch)
    assert response.status_code == 200
    result = response.json()
    assert result["endpoint_id"] == ids["endpoint"]
    assert datetime.fromisoformat(result["evaluation_time"]) == AT
    assert result["test_identity_ids"] == [ids["actors"][actor] for actor in ORDER]
    by_actor = {row[0]: row for row in cases}
    for slot, (position, resource) in zip(result["slots"], POSITIONS, strict=True):
        location, selector = {
            "project_query": ("query", "project_id"), "task_path": ("path", "task_id"),
            "project_path": ("path", "project_id"),
        }[position]
        assert slot["binding"] == {
            "endpoint_id": ids["endpoint"], "binding_id": ids["bindings"][position],
            "location": location, "selector": selector, "review_state": "confirmed",
        }
        expected_facts, expected_candidates = [], []
        for actor in ORDER:
            _, relationship, project_access, task_access, kind = by_actor[actor]
            access = project_access if resource == "project" else task_access
            common = dict(endpoint_id=ids["endpoint"], resource_id=ids["resources"][resource],
                          test_identity_id=ids["actors"][actor], relationship=relationship,
                          expected_access=access, supporting_assertion_ids=supports.get((resource, actor), []))
            expected_facts.append(dict(common,
                identity_auth_type="anonymous" if actor == "anonymous" else "bearer",
                resolution_state="insufficient" if actor == "admin" else "resolved"))
            if kind is not None:
                expected_candidates.append(dict(common, candidate_kind=kind,
                    subject_kind="anonymous" if actor == "anonymous" else "authenticated"))
        assert slot["preview"] == {
            "endpoint_id": ids["endpoint"], "resource_id": ids["resources"][resource],
            "evaluation_time": result["evaluation_time"], "facts": expected_facts,
            "candidates": expected_candidates,
        }
    compose_spy.assert_called_once()
    assert [spy.call_count for spy in spies] == [3, 2, 10, 2]
    assert [c.kwargs["resource_id"] for c in spies[1].call_args_list] == list(ids["resources"].values())
    assert returned[0][0].slots[0].preview is returned[0][0].slots[2].preview
    assert asdict(returned[0][0]) == returned[0][1]
    assert all(call.args[-1] == AT for call in spies[2].call_args_list)
    # Snapshot includes the unchanged network mode and authorization configuration.
    # The exact public execution rejection is also covered by the existing M8 regression.


def test_history_complementary_dimensions_and_uncertainty(matrix, monkeypatch):
    ids = matrix
    relation = assertion(ids, "project", "owner", relationship="owner", expected_access="unspecified",
                         valid_from=AT, valid_until=AT + timedelta(seconds=2), confidence=0)
    access = assertion(ids, "project", "owner", relationship="unspecified", expected_access="allowed",
                       valid_from=AT, valid_until=AT + timedelta(seconds=2), confidence=0)
    future = assertion(ids, "project", "owner", relationship="unspecified", expected_access="denied",
                       asserted_at=AT + timedelta(seconds=1), valid_from=AT - timedelta(days=10), confidence=100)
    unspecified = assertion(ids, "project", "cross", relationship="non_owner", expected_access="unspecified")
    anonymous = assertion(ids, "task", "anonymous", relationship="unspecified", expected_access="allowed",
                          asserted_at=AT, valid_from=AT - timedelta(days=1),
                          valid_until=AT + timedelta(seconds=1))
    for state in ("candidate", "rejected"):
        assertion(ids, "project", "owner", relationship="shared", expected_access="denied",
                  verification_state=state, confidence=100)
    insufficient = ("insufficient", "unspecified", "unspecified", [])
    # Explicit independent oracles at asserted_at, valid_from and exclusive valid_until.
    timeline = (
        (-1, insufficient, insufficient, False),
        (0, ("resolved", "owner", "allowed", [relation, access]),
            ("resolved", "unspecified", "allowed", [anonymous]), True),
        (1_000_000, ("conflict", "owner", "unspecified", [relation, access, future]), insufficient, False),
        (2_000_000, ("resolved", "unspecified", "denied", [future]), insufficient, False),
    )
    for microseconds, owner_fact, anonymous_fact, has_candidates in timeline:
        at = AT + timedelta(microseconds=microseconds)
        response = preview(request_body(ids, at=at), monkeypatch)
        assert response.status_code == 200
        for slot, (_, resource) in zip(response.json()["slots"], POSITIONS, strict=True):
            facts = slot["preview"]["facts"]
            assert [f["test_identity_id"] for f in facts] == [ids["actors"][actor] for actor in ORDER]
            expected = {
                ("project", "owner"): owner_fact,
                ("project", "cross"): ("resolved", "non_owner", "unspecified", [unspecified]),
                ("task", "anonymous"): anonymous_fact,
            }
            for actor, fact in zip(ORDER, facts, strict=True):
                assert (fact["resolution_state"], fact["relationship"], fact["expected_access"],
                        fact["supporting_assertion_ids"]) == expected.get((resource, actor), insufficient)
            expected_candidates = []
            if has_candidates:
                actor, kind, relationship, support = (
                    ("owner", "owner_access", "owner", [relation, access]) if resource == "project"
                    else ("anonymous", "anonymous_access", "unspecified", [anonymous])
                )
                expected_candidates = [dict(
                    endpoint_id=ids["endpoint"], resource_id=ids["resources"][resource],
                    test_identity_id=ids["actors"][actor], candidate_kind=kind, relationship=relationship,
                    expected_access="allowed", supporting_assertion_ids=support,
                    subject_kind="authenticated" if actor == "owner" else "anonymous",
                )]
            assert slot["preview"]["candidates"] == expected_candidates
            assert datetime.fromisoformat(slot["preview"]["evaluation_time"]) == at


@pytest.mark.parametrize("mutation,status,code,resource_calls", [
    ("binding_rejected", 409, "bola_binding_not_confirmed", 0),
    ("identity_inactive", 409, "bola_matrix_identity_inactive", 1),
    ("missing_resource", 404, "resource_not_found", 2),
    ("wrong_target", 409, "bola_matrix_endpoint_resource_target_mismatch", 2),
    ("duplicate_slot", 409, "bola_binding_matrix_duplicate_slot", 0),
])
def test_fresh_metadata_and_late_failures_are_request_wide(matrix, monkeypatch, mutation, status, code, resource_calls):
    ids = matrix
    assertion(ids, "project", "owner")
    request = request_body(ids)
    initial = preview(request, monkeypatch)
    assert initial.status_code == 200
    assert initial.json()["slots"][0]["preview"]["candidates"][0]["supporting_assertion_ids"]
    if mutation == "binding_rejected":
        change(Binding, ids["bindings"]["project_path"], review_state="rejected")
    elif mutation == "identity_inactive":
        change(Identity, ids["actors"]["owner"], is_active=False)
    elif mutation == "missing_resource":
        request["assignments"][1]["resource_id"] = 2147483647
    elif mutation == "wrong_target":
        request["assignments"][1]["resource_id"] = ids["other_resource"]
    else:
        request["assignments"][-1]["binding_id"] = add_binding(
            ids["endpoint"], location="query", selector="project_id", provenance="openapi_inferred",
        )
    calls = Mock(wraps=composer.preview_bola_matrix)
    monkeypatch.setattr(composer, "preview_bola_matrix", calls)
    error(preview(request, monkeypatch), status, code)
    assert calls.call_count == resource_calls


def test_late_257_assertions_return_no_partial_matrix(matrix, monkeypatch):
    ids = matrix
    assertion(ids, "project", "owner")
    with SessionLocal() as db:
        db.add_all([Assertion(
            resource_id=ids["resources"]["task"], test_identity_id=ids["actors"]["owner"],
            relationship="owner", expected_access="denied", provenance="human_verified",
            confidence=0, verification_state="verified", asserted_at=AT - timedelta(days=1),
        ) for _ in range(257)])
        db.commit()
    calls = Mock(wraps=composer.preview_bola_matrix)
    planned = Mock(wraps=resource_preview.plan_bola_matrix)
    monkeypatch.setattr(composer, "preview_bola_matrix", calls)
    monkeypatch.setattr(resource_preview, "plan_bola_matrix", planned)
    error(preview(request_body(ids), monkeypatch), 409, "resource_access_resolution_limit_exceeded")
    assert calls.call_count == 2
    planned.assert_called_once()  # First Resource finished; none of it may be returned.


def test_exact_512_cells_and_sanitized_rejections_before_sql(matrix, monkeypatch):
    ids = matrix
    with SessionLocal() as db:
        actors = [Identity(target_id=ids["target"], name=f"matrix-unasserted-{i}",
                           role="member", auth_type="bearer", is_active=True) for i in range(256)]
        db.add_all(actors)
        db.commit()
        identities = [actor.id for actor in actors]
    request = request_body(ids)
    request["assignments"] = [request["assignments"][2], request["assignments"][0]]
    request["test_identity_ids"] = identities
    calls = Mock(wraps=composer.preview_bola_matrix)
    composed = Mock(wraps=route.preview_bola_binding_matrix)
    monkeypatch.setattr(composer, "preview_bola_matrix", calls)
    monkeypatch.setattr(route, "preview_bola_binding_matrix", composed)
    response = preview(request, monkeypatch)
    assert response.status_code == 200
    slots = response.json()["slots"]
    assert len(slots) == 2 and sum(len(s["preview"]["facts"]) for s in slots) == 512
    for slot in slots:
        assert slot["preview"]["resource_id"] == ids["resources"]["project"]
        assert [f["test_identity_id"] for f in slot["preview"]["facts"]] == identities
        assert all((f["resolution_state"], f["relationship"], f["expected_access"], f["supporting_assertion_ids"])
                   == ("insufficient", "unspecified", "unspecified", []) for f in slot["preview"]["facts"])
        assert slot["preview"]["candidates"] == []
    calls.assert_called_once()
    composed.assert_called_once()
    calls.reset_mock()
    composed.reset_mock()
    request["test_identity_ids"].append(ids["actors"]["admin"])
    error(preview(request, monkeypatch, no_sql=True), 422, "bola_matrix_preview_invalid_request")
    request["test_identity_ids"].pop()
    request["Authorization=Bearer matrix-credential"] = {"credentials": "matrix-credential"}
    error(preview(request, monkeypatch, no_sql=True), 422, "bola_matrix_preview_invalid_request")
    calls.assert_not_called()
    composed.assert_not_called()
