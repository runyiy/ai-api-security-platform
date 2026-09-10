"""Exact reviewed slot selection using synthetic persisted PostgreSQL metadata."""
import ast
from dataclasses import FrozenInstanceError, asdict, fields
import importlib
import inspect
import traceback
from unittest.mock import Mock

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    Endpoint, EndpointResourceBinding as Binding, Resource, Target,
    TestIdentity as Identity, TestRun as StoredRun,
)
from app.db.session import SessionLocal, engine
from app.main import app
from app.schemas.endpoint_resource_binding import validate_resource_binding_selector
from tests.api.test_endpoint_resource_bindings import make_endpoint, cleanup
from tests.finding_evidence_fixtures import evidence_pair  # noqa: F401
from tests.services.test_bola_matrix_preview import snapshot


@pytest.fixture
def selection():
    return importlib.import_module("app.services.bola_binding_selection")


def add_binding(endpoint_id, **overrides):
    with SessionLocal() as db:
        binding = Binding(**{ "endpoint_id": endpoint_id, "location": "path", "selector": "order_id",
                              "review_state": "confirmed", "confidence": 0, "provenance": "operator_supplied",
                              **overrides})
        db.add(binding)
        db.commit()
        return binding.id


@pytest.fixture
def selected(selection):
    target, endpoint = make_endpoint()
    other_target, other_endpoint = make_endpoint()
    try:
        yield dict(target=target, endpoint=endpoint, binding=add_binding(endpoint),
                   query=add_binding(endpoint, location="query", selector="account_id"),
                   other_endpoint=other_endpoint, other_binding=add_binding(other_endpoint))
    finally:
        cleanup([target, other_target])


def call(selection, db, selected, **overrides):
    return selection.select_bola_binding(db, **{
        "endpoint_id": selected["endpoint"], "binding_id": selected["binding"], **overrides,
    })


def change(model, row_id, **values):
    with SessionLocal() as db:
        row = db.get(model, row_id)
        for key, value in values.items():
            setattr(row, key, value)
        db.commit()


def fail(*args, **kwargs):
    pytest.fail("binding selection crossed a forbidden boundary")


def assert_code(selection, code, action):
    with pytest.raises(selection.BOLABindingSelectionError) as error:
        action()
    assert error.value.code == str(error.value) == code
    assert error.value.args == (code,)
    return error.value


def no_session_work(guard, db):
    for name in ("execute", "scalar", "scalars", "get", "add", "add_all", "delete", "flush", "commit",
                 "rollback", "close", "expunge", "expunge_all", "expire", "expire_all", "begin", "begin_nested"):
        guard.setattr(db, name, fail)


def test_signature_head_and_isolated_dependencies(selection):
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["e9a1c3d5f7b8"]
    signature = inspect.signature(selection.select_bola_binding)
    assert list(signature.parameters) == ["db", "endpoint_id", "binding_id"]
    assert all(p.default is inspect.Parameter.empty for p in signature.parameters.values())
    assert all(signature.parameters[n].kind is inspect.Parameter.KEYWORD_ONLY for n in ("endpoint_id", "binding_id"))
    assert not any("binding-selection" in route for route in app.openapi()["paths"])
    assert selection.MAX_BOLA_BINDING_PARAMETERS == 256
    assert selection.validate_resource_binding_selector is validate_resource_binding_selector
    tree = ast.parse(inspect.getsource(selection))
    imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imports |= {alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names}
    assert imports <= {"dataclasses", "typing", "sqlalchemy", "sqlalchemy.orm",
                       "app.db.models.endpoint", "app.db.models.endpoint_resource_binding",
                       "app.schemas.endpoint_resource_binding"}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"Resource", "TestIdentity", "ResourceAccessAssertion", "Target", "Scope",
                        "AuthorizationRevision", "request_body", "security", "confidence", "provenance",
                        "owner_identity_id", "credentials", "external_id", "response_body", "HTTPException",
                        "flush", "commit", "rollback", "close", "begin", "begin_nested", "with_for_update"}


@pytest.mark.parametrize("field", ["endpoint_id", "binding_id"])
@pytest.mark.parametrize("invalid", [True, False, "1", 1.0, None, 0, -1, [], {}])
def test_strict_ids_before_sql(selection, monkeypatch, field, invalid):
    with Session() as db:
        with monkeypatch.context() as guard:
            no_session_work(guard, db)
            assert_code(selection, "bola_binding_selection_invalid_input", lambda: call(selection, db,
                dict(endpoint=1, binding=2), **{field: invalid}))


@pytest.mark.parametrize(("case", "code"), [
    ("endpoint", "endpoint_not_found"), ("binding", "bola_binding_not_found"),
    ("wrong_endpoint", "bola_binding_endpoint_mismatch"),
    ("candidate", "bola_binding_not_confirmed"), ("rejected", "bola_binding_not_confirmed"),
])
def test_exact_errors_without_alternative_fallback(selection, selected, case, code):
    # A valid confirmed alternative, including the same selector, never rescues a bad selection.
    add_binding(selected["endpoint"], provenance="openapi_inferred", confidence=100)
    overrides = {}
    if case == "endpoint": overrides["endpoint_id"] = 999999999
    if case == "binding": overrides["binding_id"] = 999999999
    if case == "wrong_endpoint": overrides["binding_id"] = selected["other_binding"]
    if case in ("candidate", "rejected"):
        change(Binding, selected["binding"], review_state=case, confidence=100)
    before = snapshot()
    with SessionLocal() as db:
        assert_code(selection, code, lambda: call(selection, db, selected, **overrides))
    assert snapshot() == before


@pytest.mark.parametrize("provenance", ["operator_supplied", "openapi_inferred", "heuristic_inferred"])
@pytest.mark.parametrize("location", ["path", "query"])
def test_all_confirmed_provenances_with_zero_confidence(selection, selected, provenance, location):
    chosen = selected["binding"] if location == "path" else selected["query"]
    change(Binding, chosen, provenance=provenance)
    before = snapshot()
    with SessionLocal() as db:
        result = call(selection, db, selected, binding_id=chosen)
    assert result.binding_id == chosen and result.location == location
    assert result.review_state == "confirmed"
    assert snapshot() == before


@pytest.mark.parametrize("location", ["path", "query"])
@pytest.mark.parametrize("selector", [" bad", "1bad", "a/b", "x?value=secret", "a%2Eb", "é", "a\n", "a" * 129,
                                      "Authorization: Bearer selector-secret"])
def test_invalid_stored_selector_sanitized(selection, selected, location, selector):
    change(Binding, selected["binding"], location=location, selector=selector)
    before = snapshot()
    with SessionLocal() as db:
        error = assert_code(selection, "bola_binding_selector_invalid", lambda: call(selection, db, selected))
    assert "selector-secret" not in "".join(traceback.format_exception(error))
    assert error.__cause__ is None
    assert snapshot() == before


def test_confirmed_body_never_reads_declaration_or_evaluates_pointer(selection, selected, monkeypatch):
    change(Binding, selected["binding"], location="body", selector="/order/id")
    with SessionLocal() as db:
        with monkeypatch.context() as guard:
            for name in ("path", "parameters", "request_body", "security"):
                guard.setattr(Endpoint, name, property(fail))
            guard.setattr(selection, "validate_resource_binding_selector", fail)
            assert_code(selection, "bola_binding_location_unsupported", lambda: call(selection, db, selected))


@pytest.mark.parametrize("name", ["id", "Order_ID", "order.id", "order-id", "_id", "a" * 128])
def test_exact_path_names_and_multiple_distinct_slots(selection, selected, name):
    change(Endpoint, selected["endpoint"], path="/tenants/{tenant}/orders/{" + name + "}")
    change(Binding, selected["binding"], selector=name)
    with SessionLocal() as db:
        result = call(selection, db, selected)
    assert asdict(result) == dict(endpoint_id=selected["endpoint"], binding_id=selected["binding"],
                                 location="path", selector=name, review_state="confirmed")


@pytest.mark.parametrize("path", ["/orders", "/orders/{Order_id}", "/orders/{order}", "/orders/{order_ids}",
                                  "/orders/%7Border_id%7D"])
def test_parameters_entry_or_normalization_cannot_establish_path_slot(selection, selected, path):
    change(Endpoint, selected["endpoint"], path=path)
    with SessionLocal() as db:
        assert_code(selection, "bola_binding_selector_not_declared", lambda: call(selection, db, selected))


@pytest.mark.parametrize("path", ["/{order_id", "/order_id}", "/{{order_id}}", "/{order_id{other}}", "/{}",
                                  "/{1id}", "/{id/name}", "/{with space}", "/{é}", "/{id\n}",
                                  "/{" + "a" * 129 + "}", "/{order_id}/{}"])
def test_malformed_path_template_fails_closed(selection, selected, path):
    change(Endpoint, selected["endpoint"], path=path)
    with SessionLocal() as db:
        assert_code(selection, "bola_binding_endpoint_metadata_invalid", lambda: call(selection, db, selected))


@pytest.mark.parametrize("path", ["/{order_id}/{order_id}", "/{order_id}/{other}/{other}"])
def test_repeated_path_names_are_ambiguous_even_for_other_slots(selection, selected, path):
    change(Endpoint, selected["endpoint"], path=path)
    with SessionLocal() as db:
        assert_code(selection, "bola_binding_selector_ambiguous", lambda: call(selection, db, selected))


def test_path_500_character_bound(selection, selected):
    change(Endpoint, selected["endpoint"], path="/" * 490 + "{order_id}")
    with SessionLocal() as db:
        assert call(selection, db, selected).selector == "order_id"


@pytest.mark.parametrize("value", [None, 1, [], "a" * 501])
def test_corrupt_path_scalar_rejected(selection, selected, monkeypatch, value):
    # These values cannot be stored in the bounded non-null PostgreSQL path column.
    with SessionLocal() as db:
        execute = db.execute
        def corrupt(statement, *args, **kwargs):
            if {c.name for c in statement.selected_columns} == {"path"}:
                return Mock(scalar_one=Mock(return_value=value))
            return execute(statement, *args, **kwargs)
        monkeypatch.setattr(db, "execute", corrupt)
        assert_code(selection, "bola_binding_endpoint_metadata_invalid", lambda: call(selection, db, selected))


@pytest.mark.parametrize("parameters", [[], [{"name": "account_id", "in": "path"}],
    [{"name": "account_id", "in": "header"}], [{"name": "account_id", "in": "cookie"}],
    [{"name": "Account_id", "in": "query"}], [{"name": "account", "in": "query"}],
    [{"name": " account_id", "in": "query"}], [{"name": "account%5Fid", "in": "query"}],
])
def test_query_requires_exact_name_and_location_not_path_text(selection, selected, parameters):
    change(Endpoint, selected["endpoint"], parameters=parameters, path="/orders?account_id=value-secret")
    with SessionLocal() as db:
        assert_code(selection, "bola_binding_selector_not_declared", lambda: call(selection, db, selected, binding_id=selected["query"]))


@pytest.mark.parametrize("parameters", [None, {}, "bad", 1, [None], [1], [[]], [{}],
    [{"$ref": "https://unresolved.example.test/secret"}], [{"name": "account_id"}],
    [{"name": "", "in": "query"}], [{"name": 1, "in": "query"}], [{"name": "id", "in": "body"}],
    [{"name": "id", "in": "Query"}], [{"name": "id", "in": []}],
    [{"name": "account_id", "in": "query"}, {"$ref": "#/late"}],
])
def test_malformed_query_declarations_including_after_match(selection, selected, parameters):
    change(Endpoint, selected["endpoint"], parameters=parameters)
    with SessionLocal() as db:
        assert_code(selection, "bola_binding_endpoint_metadata_invalid", lambda: call(selection, db, selected, binding_id=selected["query"]))


def test_query_duplicate_identical_declarations_ambiguous(selection, selected):
    change(Endpoint, selected["endpoint"], parameters=[{"name": "account_id", "in": "query"}] * 2)
    with SessionLocal() as db:
        assert_code(selection, "bola_binding_selector_ambiguous", lambda: call(selection, db, selected, binding_id=selected["query"]))


@pytest.mark.parametrize("size", [256, 257])
@pytest.mark.parametrize("match_index", [0, 255])
def test_query_bound_checked_before_match(selection, selected, size, match_index):
    parameters = [{"name": f"slot{i}", "in": "header"} for i in range(size)]
    parameters[match_index] = {"name": "account_id", "in": "query"}
    change(Endpoint, selected["endpoint"], parameters=parameters)
    with SessionLocal() as db:
        if size == 257:
            assert_code(selection, "bola_binding_parameter_limit_exceeded", lambda: call(selection, db, selected, binding_id=selected["query"]))
        else:
            assert call(selection, db, selected, binding_id=selected["query"]).selector == "account_id"


def test_immutable_exact_output_and_nested_query_values_ignored(selection, selected):
    metadata = {"schema": {"$ref": "https://secret.example.test/schema"}, "example": "example-secret",
                "default": "default-secret", "description": "description-secret", "security": {"token": "credential-secret"}}
    parameters = [{"name": "account_id", "in": location, **metadata} for location in ("path", "header", "query", "cookie")]
    change(Endpoint, selected["endpoint"], parameters=parameters)
    before = snapshot()
    with SessionLocal() as db:
        result = call(selection, db, selected, binding_id=selected["query"])
    assert isinstance(result, selection.BOLAReviewedBindingSelection)
    assert asdict(result) == dict(endpoint_id=selected["endpoint"], binding_id=selected["query"],
                                 location="query", selector="account_id", review_state="confirmed")
    assert len(fields(result)) == 5
    for field in fields(result):
        with pytest.raises(FrozenInstanceError):
            setattr(result, field.name, None)
    assert snapshot() == before


@pytest.mark.parametrize("location", ["path", "query"])
@pytest.mark.parametrize("mutation", ["review", "declaration"])
def test_fresh_reads_deterministic_without_cross_call_cache(selection, selected, location, mutation):
    chosen = selected["binding"] if location == "path" else selected["query"]
    def read():
        with SessionLocal() as db:
            return call(selection, db, selected, binding_id=chosen)
    assert read() == read()
    if mutation == "review":
        change(Binding, chosen, review_state="rejected")
        code = "bola_binding_not_confirmed"
    else:
        change(Endpoint, selected["endpoint"], **({"path": "/removed"} if location == "path" else {"parameters": []}))
        code = "bola_binding_selector_not_declared"
    assert_code(selection, code, read)


@pytest.mark.parametrize("pending", ["new", "dirty", "deleted"])
def test_unclean_sessions_preserve_caller_changes_before_sql(selection, selected, monkeypatch, pending):
    before = snapshot()
    with Session(engine, autoflush=True) as db:
        if pending == "new":
            obj = Endpoint(target_id=selected["target"], path="/pending", method="GET", parameters=[])
            db.add(obj)
        else:
            obj = db.get(Binding, selected["binding"])
            if pending == "dirty": obj.review_state = "rejected"
            else: db.delete(obj)
        state = (tuple(db.new), tuple(db.dirty), tuple(db.deleted), dict(obj.__dict__))
        with monkeypatch.context() as guard:
            no_session_work(guard, db)
            assert_code(selection, "bola_binding_selection_session_not_clean", lambda: call(selection, db, selected))
            assert (tuple(db.new), tuple(db.dirty), tuple(db.deleted), dict(obj.__dict__)) == state
    assert snapshot() == before


class DeclarationOnly(dict):
    def get(self, key, default=None):
        assert key in ("name", "in")
        return super().get(key, default)

    def __getitem__(self, key):
        assert key in ("name", "in")
        return super().__getitem__(key)

    items = values = __iter__ = fail


def test_only_direct_query_name_and_location_are_inspected(selection, selected, monkeypatch):
    parameters = [DeclarationOnly(name="account_id", **{"in": "query", "schema": object(), "$ref": object()})]
    with SessionLocal() as db:
        execute = db.execute
        def projected(statement, *args, **kwargs):
            if {c.name for c in statement.selected_columns} == {"parameters"}:
                return Mock(scalar_one=Mock(return_value=parameters))
            return execute(statement, *args, **kwargs)
        monkeypatch.setattr(db, "execute", projected)
        assert call(selection, db, selected, binding_id=selected["query"]).selector == "account_id"


@pytest.mark.parametrize("location", ["path", "query"])
def test_uses_existing_selector_grammar_and_only_relevant_declaration(selection, selected, monkeypatch, location):
    validator = Mock(wraps=validate_resource_binding_selector)
    chosen = selected["binding"] if location == "path" else selected["query"]
    with SessionLocal() as db:
        with monkeypatch.context() as guard:
            guard.setattr(selection, "validate_resource_binding_selector", validator)
            guard.setattr(Endpoint, "parameters" if location == "path" else "path", property(fail))
            result = call(selection, db, selected, binding_id=chosen)
    assert validator.call_args_list[0].args == (location, result.selector)
    if location == "path":
        assert validator.call_count == 2  # Selected stored name, then current template token.


@pytest.mark.parametrize("network_mode", ["private_local", "external_public_authorized"])
@pytest.mark.parametrize("outcome", ["path", "query", "rejected", "undeclared"])
def test_exact_sql_no_mutation_or_prohibited_activity(
        selection, selected, evidence_pair, monkeypatch, network_mode, outcome):
    from tests.api.test_finding_structured_evidence import analyze
    # Load the preview before patching its dependencies so it cannot retain the guards.
    importlib.import_module("app.services.bola_matrix_preview")
    assert analyze(evidence_pair).status_code == 200  # Nonempty execution, Resource, identity and M13 data.
    change(Target, selected["target"], network_mode=network_mode)
    if outcome == "rejected": change(Binding, selected["binding"], review_state="rejected")
    if outcome == "undeclared": change(Endpoint, selected["endpoint"], path="/missing")
    chosen = selected["query"] if outcome == "query" else selected["binding"]
    before = snapshot()
    settings_before = settings.model_dump()
    reads = []

    def audit(conn, statement, multiparams, params, execution_options):
        assert statement.is_select
        assert statement._for_update_arg is None
        tables = statement.get_final_froms()
        assert len(tables) == 1
        table = tables[0].name
        assert table in {"endpoints", "endpoint_resource_bindings"}
        assert statement.whereclause.left.name == "id"
        assert statement.whereclause.right.value == (selected["endpoint"] if table == "endpoints" else chosen)
        columns = {c.name for c in statement.selected_columns}
        if table == "endpoints":
            assert columns in ({"id"}, {"parameters"} if outcome == "query" else {"path"})
        else:
            assert columns == {"endpoint_id", "location", "selector", "review_state"}
        reads.append((table, columns))

    with Session(engine, autoflush=True) as db:
        db.connection()
        transaction = db.get_transaction()
        with monkeypatch.context() as guard:
            for name in ("add", "add_all", "delete", "flush", "commit", "rollback", "close", "begin", "begin_nested",
                         "expire", "expire_all", "expunge", "expunge_all"):
                guard.setattr(db, name, fail)
            for model, name in ((Resource, "owner_identity_id"), (Resource, "external_id"),
                                (Identity, "credentials"), (Identity, "credential_bindings"), (Identity, "role"),
                                (Identity, "name"), (Endpoint, "request_body"), (Endpoint, "security"),
                                (Endpoint, "method"), (Endpoint, "target_id"), (StoredRun, "response_body"),
                                (Binding, "confidence"), (Binding, "provenance")):
                guard.setattr(model, name, property(fail))
            for path in (
                "app.domain.ownership.determine_ownership_relation", "app.generators.bola.determine_ownership_relation",
                "app.generators.bola.detect_resource_binding", "app.generators.bola.generate_bola_test_cases",
                "app.services.resource_access_resolution.resolve_resource_access",
                "app.generators.bola_matrix.plan_bola_matrix", "app.services.bola_matrix_preview.preview_bola_matrix",
                "app.services.openapi_binding_candidates.infer_openapi_binding_candidates",
                "app.services.openapi_body_binding_candidates.infer_openapi_body_binding_candidates",
                "app.schemas.endpoint_resource_binding.validate_json_pointer",
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
                if outcome in ("rejected", "undeclared"):
                    code = "bola_binding_not_confirmed" if outcome == "rejected" else "bola_binding_selector_not_declared"
                    assert_code(selection, code, lambda: call(selection, db, selected, binding_id=chosen))
                else:
                    assert call(selection, db, selected, binding_id=chosen).binding_id == chosen
            finally:
                event.remove(engine, "before_execute", audit)
            assert db.get_transaction() is transaction and transaction.is_active
            assert db.autoflush is True
            assert not db.new and not db.dirty and not db.deleted
    assert len(reads) == (2 if outcome == "rejected" else 3)
    assert snapshot() == before
    assert settings.model_dump() == settings_before
