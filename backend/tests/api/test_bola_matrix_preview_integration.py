"""Real PostgreSQL and in-process HTTP acceptance of the M14 planning boundary."""
from dataclasses import asdict
from datetime import timedelta, timezone
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    Endpoint, EndpointResourceBinding as Binding, Resource, ResourceAccessAssertion as Assertion,
    Target, TestIdentity as Identity, TestRun as StoredRun,
)
from app.db.session import SessionLocal, engine, get_db
from app.main import app
from tests.api.test_bola_matrix_preview import URL, client, error, FAILED, INVALID
from tests.api.test_resource_access_resolution import NOW, add_assertion
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401
from tests.services.test_bola_binding_matrix_preview import composer, selected  # noqa: F401
from tests.services.test_bola_binding_selection import add_binding, change
from tests.services.test_bola_matrix_preview import snapshot


def payload(ids, **changes):
    return {
        "endpoint_id": ids["endpoint"],
        "assignments": [
            {"binding_id": ids["bindings"][1], "resource_id": ids["second_resource"]},
            {"binding_id": ids["bindings"][2], "resource_id": ids["resource"]},
            {"binding_id": ids["bindings"][0], "resource_id": ids["resource"]},
        ],
        "test_identity_ids": [ids["peer"], ids["anonymous"], ids["identity"]],
        "evaluation_time": NOW.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat(),
        **changes,
    }


def assert_allowlists(value):
    assert set(value) == {"endpoint_id", "evaluation_time", "test_identity_ids", "slots"}
    for slot in value["slots"]:
        assert set(slot) == {"binding", "preview"}
        assert set(slot["binding"]) == {"endpoint_id", "binding_id", "location", "selector", "review_state"}
        preview = slot["preview"]
        assert set(preview) == {"endpoint_id", "resource_id", "evaluation_time", "facts", "candidates"}
        for fact in preview["facts"]:
            assert set(fact) == {"endpoint_id", "resource_id", "test_identity_id", "identity_auth_type",
                                 "resolution_state", "relationship", "expected_access", "supporting_assertion_ids"}
        for candidate in preview["candidates"]:
            assert set(candidate) == {"endpoint_id", "resource_id", "test_identity_id", "subject_kind",
                                      "candidate_kind", "relationship", "expected_access", "supporting_assertion_ids"}


@pytest.mark.parametrize("network_mode", ["private_local", "external_public_authorized"])
def test_nested_mixed_http_preserves_independent_access_and_provenance(selected, composer, monkeypatch, network_mode):
    import app.api.routes.bola_matrix as route
    ids = selected
    change(Target, ids["target"], network_mode=network_mode)
    supports = [add_assertion(dict(resource=r, identity=i), relationship=relationship, expected_access=access)
                for r, i, relationship, access in (
                    (ids["resource"], ids["identity"], "owner", "denied"),
                    (ids["resource"], ids["anonymous"], "unspecified", "allowed"),
                    (ids["second_resource"], ids["identity"], "non_owner", "allowed"),
                    (ids["second_resource"], ids["peer"], "shared", "denied"),
                    (ids["second_resource"], ids["anonymous"], "unspecified", "denied"),
                )]
    produced = []
    delegate = route.preview_bola_binding_matrix
    def once(db, **kwargs):
        value = delegate(db, **kwargs)
        produced.append((value, asdict(value)))
        return value
    call = Mock(side_effect=once)
    monkeypatch.setattr(route, "preview_bola_binding_matrix", call)
    lower_preview = Mock(wraps=composer.preview_bola_matrix)
    lower_binding = Mock(wraps=composer.select_bola_binding)
    monkeypatch.setattr(composer, "preview_bola_matrix", lower_preview)
    monkeypatch.setattr(composer, "select_bola_binding", lower_binding)
    before = snapshot()
    response = client.post(URL, json=payload(ids))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    value = response.json()
    assert_allowlists(value)
    assert value["test_identity_ids"] == [ids["peer"], ids["anonymous"], ids["identity"]]
    assert [s["binding"]["location"] for s in value["slots"]] == ["path", "query", "path"]
    assert [s["binding"]["selector"] for s in value["slots"]] == ["task_id", "project_id", "project_id"]
    first, second, third = [s["preview"] for s in value["slots"]]
    assert second == third
    assert [(c["relationship"], c["expected_access"], c["candidate_kind"], c["supporting_assertion_ids"])
            for c in first["candidates"]] == [
        ("shared", "denied", "shared_access", [supports[3]]),
        ("unspecified", "denied", "anonymous_access", [supports[4]]),
        ("non_owner", "allowed", "cross_subject_access", [supports[2]]),
    ]
    assert [(c["relationship"], c["expected_access"], c["candidate_kind"], c["supporting_assertion_ids"])
            for c in second["candidates"]] == [
        ("unspecified", "allowed", "anonymous_access", [supports[1]]),
        ("owner", "denied", "owner_access", [supports[0]]),
    ]
    assert second["facts"][0]["resolution_state"] == "insufficient"
    assert len(second["facts"]) == 3
    call.assert_called_once()
    assert lower_binding.call_count == 3 and lower_preview.call_count == 2
    assert produced[0][0].slots[1].preview is produced[0][0].slots[2].preview
    assert asdict(produced[0][0]) == produced[0][1]
    assert snapshot() == before
    for forbidden in ("secret", "external_id", "credentials", "https://", "response_body",
                      "expected_status", "authorized", "executable"):
        assert forbidden not in response.text


def test_http_conflict_insufficient_and_unspecified_facts_are_not_dropped(selected):
    a = add_assertion(selected, expected_access="denied", confidence=0)
    b = add_assertion(selected, expected_access="allowed", confidence=100)
    add_assertion(dict(selected, resource=selected["second_resource"]), expected_access="unspecified")
    response = client.post(URL, json=payload(selected))
    assert response.status_code == 200
    previews = [s["preview"] for s in response.json()["slots"]]
    assert all(len(p["facts"]) == 3 and p["candidates"] == [] for p in previews)
    assert previews[1]["facts"][2]["resolution_state"] == "conflict"
    assert previews[1]["facts"][2]["supporting_assertion_ids"] == [a, b]
    assert previews[0]["facts"][0]["resolution_state"] == "insufficient"
    assert previews[0]["facts"][2]["expected_access"] == "unspecified"
    assert_allowlists(response.json())


@pytest.mark.parametrize("case,status,code", [
    ("endpoint", 404, "endpoint_not_found"), ("binding", 404, "bola_binding_not_found"),
    ("resource", 404, "resource_not_found"), ("identity", 404, "test_identity_not_found"),
    ("candidate", 409, "bola_binding_not_confirmed"), ("rejected", 409, "bola_binding_not_confirmed"),
    ("body", 409, "bola_binding_location_unsupported"),
    ("binding_endpoint", 409, "bola_binding_endpoint_mismatch"),
    ("selector", 409, "bola_binding_selector_invalid"),
    ("metadata", 409, "bola_binding_endpoint_metadata_invalid"),
    ("ambiguous", 409, "bola_binding_selector_ambiguous"),
    ("undeclared", 409, "bola_binding_selector_not_declared"),
    ("parameter_limit", 409, "bola_binding_parameter_limit_exceeded"),
    ("resource_target", 409, "bola_matrix_endpoint_resource_target_mismatch"),
    ("identity_target", 409, "resource_identity_target_mismatch"),
    ("inactive", 409, "bola_matrix_identity_inactive"),
    ("duplicate_slot", 409, "bola_binding_matrix_duplicate_slot"),
])
def test_real_missing_and_conflicting_metadata_fails_closed(selected, case, status, code):
    ids, request = selected, payload(selected)
    late = ids["bindings"][2]
    restore_binding = None
    if case == "endpoint": request["endpoint_id"] = 2147483647
    if case == "binding": request["assignments"][-1]["binding_id"] = 2147483647
    if case == "resource": request["assignments"][1]["resource_id"] = 2147483647
    if case == "identity": request["test_identity_ids"][-1] = 2147483647
    if case in ("candidate", "rejected"): change(Binding, late, review_state=case)
    if case == "body": change(Binding, late, location="body", selector="/id")
    if case == "selector": change(Binding, late, selector="api_key=synthetic-secret")
    if case == "metadata": change(Endpoint, ids["endpoint"], parameters={})
    if case == "ambiguous": change(Endpoint, ids["endpoint"], parameters=[{"in": "query", "name": "project_id"}] * 2)
    if case == "undeclared": change(Endpoint, ids["endpoint"], parameters=[])
    if case == "parameter_limit": change(Endpoint, ids["endpoint"], parameters=[{"in": "query", "name": "project_id"}] * 257)
    if case == "resource_target": request["assignments"][1]["resource_id"] = ids["other_resource"]
    if case == "identity_target": request["test_identity_ids"][-1] = ids["other_identity"]
    if case == "inactive": change(Identity, ids["identity"], is_active=False)
    if case == "duplicate_slot":
        alternate = add_binding(ids["endpoint"], selector="project_id", provenance="openapi_inferred")
        request["assignments"][0]["binding_id"] = alternate
    if case == "binding_endpoint":
        with SessionLocal() as db:
            endpoint = Endpoint(target_id=ids["target"], path="/other/{project_id}", method="GET", parameters=[])
            db.add(endpoint)
            db.flush()
            db.get(Binding, late).endpoint_id = endpoint.id
            db.commit()
        restore_binding = late
    before = snapshot()
    try:
        error(client.post(URL, json=request), status, code)
        assert snapshot() == before
    finally:
        if restore_binding is not None: change(Binding, restore_binding, endpoint_id=ids["endpoint"])


def test_empty_identities_still_validate_every_binding_and_resource(selected, monkeypatch):
    def prohibited(*args, **kwargs):
        pytest.fail("Empty identities invoked the resolver.")
    monkeypatch.setattr("app.services.bola_matrix_preview.resolve_resource_access", prohibited)
    request = payload(selected, test_identity_ids=[])
    response = client.post(URL, json=request)
    assert response.status_code == 200
    assert len(response.json()["slots"]) == 3
    assert all(s["preview"]["facts"] == s["preview"]["candidates"] == [] for s in response.json()["slots"])
    request["assignments"][-1]["resource_id"] = 2147483647
    error(client.post(URL, json=request), 404, "resource_not_found")
    request = payload(selected, test_identity_ids=[])
    change(Binding, selected["bindings"][-1], review_state="rejected")
    error(client.post(URL, json=request), 409, "bola_binding_not_confirmed")


def test_full_256_provenance_preserved_then_late_257_fails(selected, composer, monkeypatch):
    request = payload(selected)
    request["assignments"] = request["assignments"][1:] + request["assignments"][:1]
    with SessionLocal() as db:
        assertions = [Assertion(resource_id=selected["second_resource"], test_identity_id=selected["identity"],
            relationship="owner", expected_access="denied", provenance="human_verified", confidence=0,
            verification_state="verified", asserted_at=NOW) for _ in range(256)]
        db.add_all(assertions)
        db.commit()
        supports = [a.id for a in assertions]
    response = client.post(URL, json=request)
    assert response.status_code == 200
    preview = response.json()["slots"][-1]["preview"]
    assert preview["facts"][-1]["supporting_assertion_ids"] == supports
    assert preview["candidates"][0]["supporting_assertion_ids"] == supports
    add_assertion(dict(selected, resource=selected["second_resource"]), expected_access="denied")
    calls = Mock(wraps=composer.preview_bola_matrix)
    monkeypatch.setattr(composer, "preview_bola_matrix", calls)
    before = snapshot()
    error(client.post(URL, json=request), 409, "resource_access_resolution_limit_exceeded")
    assert calls.call_count == 2
    assert snapshot() == before


@pytest.mark.parametrize("slot_count,identity_count", [(32, 16), (1, 512)])
def test_real_maximum_cells_then_overflow_before_sql(selected, slot_count, identity_count):
    with SessionLocal() as db:
        bindings = [Binding(endpoint_id=selected["endpoint"], location="query", selector=f"slot{i}",
                    review_state="confirmed", confidence=0, provenance="operator_supplied")
                    for i in range(slot_count)]
        identities = [Identity(target_id=selected["target"], name=f"synthetic-{i}", role="user",
                      auth_type="bearer", is_active=True) for i in range(identity_count)]
        db.get(Endpoint, selected["endpoint"]).parameters = [
            {"in": "query", "name": b.selector} for b in bindings
        ]
        db.add_all(bindings + identities)
        db.commit()
        request = payload(selected,
            assignments=[{"binding_id": b.id, "resource_id": selected["resource"]} for b in bindings],
            test_identity_ids=[i.id for i in identities],
        )
    before = snapshot()
    response = client.post(URL, json=request)
    assert response.status_code == 200
    result = response.json()
    assert len(result["slots"]) == slot_count
    assert all(len(s["preview"]["facts"]) == identity_count and s["preview"]["candidates"] == []
               for s in result["slots"])
    assert sum(len(s["preview"]["facts"]) for s in result["slots"]) == 512
    request["test_identity_ids"].append(selected["identity"])
    def no_sql(*args):
        pytest.fail("Aggregate overflow performed SQL.")
    event.listen(engine, "before_execute", no_sql)
    try:
        error(client.post(URL, json=request), 422, INVALID)
    finally:
        event.remove(engine, "before_execute", no_sql)
    assert snapshot() == before


@pytest.mark.parametrize("mutation", ["binding", "assertion", "identity"])
def test_http_has_no_cross_request_cache(selected, mutation):
    request = payload(selected)
    first = client.post(URL, json=request)
    assert first.status_code == 200
    assert client.post(URL, json=request).content == first.content
    if mutation == "binding":
        change(Binding, selected["bindings"][0], review_state="rejected")
        error(client.post(URL, json=request), 409, "bola_binding_not_confirmed")
    elif mutation == "identity":
        change(Identity, selected["identity"], is_active=False)
        error(client.post(URL, json=request), 409, "bola_matrix_identity_inactive")
    else:
        add_assertion(selected, expected_access="denied")
        later = client.post(URL, json=request)
        assert later.status_code == 200
        assert later.content != first.content


@pytest.mark.parametrize("network_mode", ["private_local", "external_public_authorized"])
@pytest.mark.parametrize("late_failure", [False, True])
def test_http_is_read_only_with_evidence_and_zero_forbidden_activity(
        selected, evidence_pair, composer, monkeypatch, network_mode, late_failure):
    from tests.api.test_finding_structured_evidence import analyze
    assert analyze(evidence_pair).status_code == 200
    add_assertion(selected, expected_access="denied")
    change(Target, selected["target"], network_mode=network_mode)
    request = payload(selected)
    if late_failure: request["assignments"][1]["resource_id"] = 2147483647
    before, config_before = snapshot(), settings.model_dump()
    reads, closed, phase = [], [], []
    def prohibited(*args, **kwargs):
        pytest.fail("HTTP preview crossed a forbidden boundary.")
    def audited(name, delegate):
        def call(db, **kwargs):
            assert not phase
            assert db.autoflush is False
            phase.append(name)
            try: return delegate(db, **kwargs)
            finally: phase.pop()
        return call
    def sql(conn, statement, multiparams, params, execution_options):
        assert phase, "SQL outside the unchanged composer dependencies"
        assert statement.is_select and statement._for_update_arg is None
        assert statement.whereclause is not None
        tables = statement.get_final_froms()
        assert len(tables) == 1
        assert tables[0].name in {
            "endpoints", "endpoint_resource_bindings", "resources",
            "test_identities", "resource_access_assertions",
        }
        reads.append(tables[0].name)
    def request_session():
        owner = get_db()
        db = next(owner)
        close = Mock(wraps=db.close)
        try:
            with monkeypatch.context() as ownership:
                ownership.setattr(db, "close", close)
                with monkeypatch.context() as guard:
                    for name in ("add", "add_all", "delete", "flush", "commit", "rollback", "begin", "begin_nested",
                                 "expire", "expire_all", "expunge", "expunge_all"):
                        guard.setattr(db, name, prohibited)
                    yield db
                    assert not db.new and not db.dirty and not db.deleted
                    assert db.get_transaction().is_active
                close.assert_not_called()
                # Restore Session internals before the owning dependency closes.
                owner.close()
                assert close.call_count == 1
                closed.append(True)
        finally:
            owner.close()
    original_send = httpx.Client.send
    def in_process_only(self, *args, **kwargs):
        assert self is client, "Outbound HTTP client used."
        return original_send(self, *args, **kwargs)
    app.dependency_overrides[get_db] = request_session
    try:
        with monkeypatch.context() as guard:
            guard.setattr(composer, "select_bola_binding", audited("binding", composer.select_bola_binding))
            guard.setattr(composer, "preview_bola_matrix", audited("access", composer.preview_bola_matrix))
            for model, name in (
                (Resource, "owner_identity_id"), (Resource, "external_id"), (Identity, "credentials"),
                (Identity, "credential_bindings"), (Identity, "role"), (Identity, "name"),
                (Endpoint, "request_body"), (Endpoint, "security"), (StoredRun, "response_body"),
            ):
                guard.setattr(model, name, property(prohibited))
            for path in (
                "app.domain.ownership.determine_ownership_relation", "app.generators.bola.generate_bola_test_cases",
                "app.services.openapi_binding_candidates.infer_openapi_binding_candidates",
                "app.services.openapi_body_binding_candidates.infer_openapi_body_binding_candidates",
                "app.auth.context.build_authentication_context", "app.credentials.bearer.BearerCredentialService.resolve",
                "app.credentials.bearer.BearerCredentialService.resolve_binding",
                "app.network_safety.gateway.NetworkGateway.request", "app.executors.http.PolicyEnforcedHTTPExecutor.execute",
                "app.services.test_execution.TestExecutionService.execute", "app.scanners.openapi.OpenAPIScanner.scan",
                "app.services.ai_analysis.AIAnalysisService.analyze_finding", "app.ai.mock_provider.MockAIProvider.analyze",
                "httpx.AsyncClient.send", "httpcore.ConnectionPool.stream", "dns.resolver.resolve",
                "dns.resolver.Resolver.resolve", "socket.getaddrinfo", "socket.gethostbyname",
                "socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex",
            ):
                guard.setattr(path, prohibited)
            guard.setattr(httpx.Client, "send", in_process_only)
            event.listen(engine, "before_execute", sql)
            try:
                response = client.post(URL, json=request)
            finally:
                event.remove(engine, "before_execute", sql)
            if late_failure: error(response, 404, "resource_not_found")
            else: assert response.status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert closed == [True]
    assert "resources" in reads and "endpoint_resource_bindings" in reads
    assert snapshot() == before
    assert settings.model_dump() == config_before


@pytest.mark.parametrize("state", ["new", "dirty", "deleted"])
def test_unclean_request_session_is_not_flushed_or_discarded(selected, monkeypatch, state):
    before = snapshot()
    db = Session(engine, autoflush=True)
    if state == "new":
        row = Target(name="synthetic-uncommitted", base_url="https://example.test", environment="test")
        db.add(row)
    else:
        row = db.get(Target, selected["target"])
        if state == "dirty": row.name = "synthetic-uncommitted"
        else: db.delete(row)
    def dependency():
        yield db
    def prohibited(*args, **kwargs):
        pytest.fail("Unclean Session was changed or queried.")
    app.dependency_overrides[get_db] = dependency
    try:
        with monkeypatch.context() as guard:
            for name in ("execute", "flush", "commit", "rollback", "close", "expunge", "expunge_all"):
                guard.setattr(db, name, prohibited)
            error(client.post(URL, json=payload(selected)), 500, FAILED)
            assert row in getattr(db, state)
            assert db.autoflush is True
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()
    assert snapshot() == before
