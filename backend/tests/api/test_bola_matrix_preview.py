"""HTTP transport contracts for the offline M14 composer; synthetic data only."""
import asyncio
import ast
from dataclasses import asdict, replace
from datetime import datetime, timezone
import importlib
import inspect
import json
from unittest.mock import Mock

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.generators.bola_matrix import BOLAMatrixAccessFact, BOLAMatrixCandidate, BOLAMatrixPlanningError
from app.main import app
from app.services.bola_binding_matrix_preview import (
    BOLAAssignedSlotPreview, BOLABindingMatrixPreviewError,
    BOLAMultiBindingPreview, BOLAResourceSlotAssignment,
)
from app.services.bola_binding_selection import BOLABindingSelectionError, BOLAReviewedBindingSelection
from app.services.bola_matrix_preview import BOLAMatrixPreview, BOLAMatrixPreviewError
from app.services.resource_access_resolution import ResourceAccessResolutionError


URL = "/api/bola-matrix/preview"
INVALID = "bola_matrix_preview_invalid_request"
FAILED = "bola_matrix_preview_failed"
REQUEST_LIMIT = "bola_matrix_preview_request_limit_exceeded"
RESPONSE_LIMIT = "bola_matrix_preview_response_limit_exceeded"
MEDIA = "bola_matrix_preview_media_type_unsupported"
AT = datetime(2030, 6, 1, 12, tzinfo=timezone.utc)
SECRET = "synthetic-Bearer-secret-unknown-field"
client = TestClient(app)


def body(**changes):
    return {
        "endpoint_id": 1, "assignments": [{"binding_id": 2, "resource_id": 3}],
        "test_identity_ids": [4], "evaluation_time": "2030-06-01T12:00:00Z",
        **changes,
    }


def output(db, *, endpoint_id, assignments, test_identity_ids, evaluation_time):
    slots = []
    for assignment in assignments:
        facts = tuple(BOLAMatrixAccessFact(
            endpoint_id, assignment.resource_id, i, "bearer", "insufficient",
            "unspecified", "unspecified", (),
        ) for i in test_identity_ids)
        slots.append(BOLAAssignedSlotPreview(
            BOLAReviewedBindingSelection(endpoint_id, assignment.binding_id, "query",
                                         f"slot{assignment.binding_id}", "confirmed"),
            BOLAMatrixPreview(endpoint_id, assignment.resource_id, evaluation_time, facts, ()),
        ))
    return BOLAMultiBindingPreview(endpoint_id, evaluation_time, tuple(test_identity_ids), tuple(slots))


@pytest.fixture
def api():
    return importlib.import_module("app.api.routes.bola_matrix")


@pytest.fixture
def isolated(api, monkeypatch):
    calls = Mock(side_effect=output)
    monkeypatch.setattr(api, "preview_bola_binding_matrix", calls)
    sessions = []
    def dependency():
        with Session() as db:
            sessions.append(db)
            yield db
    app.dependency_overrides[get_db] = dependency
    try:
        yield calls, sessions
    finally:
        app.dependency_overrides.pop(get_db, None)


def error(response, status, code):
    assert response.status_code == status
    assert response.json() == {"detail": code}
    assert response.headers["cache-control"] == "no-store"
    assert len(response.content) < 150
    assert SECRET not in response.text


def test_only_preview_operation_and_unchanged_head():
    paths = app.openapi()["paths"]
    assert {p: set(v) for p, v in paths.items() if "matrix" in p} == {URL: {"post"}}
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == ["b5d7f9a1c3e6"]
    assert "/api/test-cases/generate/bola" in paths


@pytest.mark.parametrize("field", list(body()))
def test_every_field_required(isolated, field):
    payload = body()
    del payload[field]
    error(client.post(URL, json=payload), 422, INVALID)
    isolated[0].assert_not_called()
    assert isolated[1] == []


@pytest.mark.parametrize("field", ["endpoint_id", "binding_id", "resource_id", "identity"])
@pytest.mark.parametrize("value", [True, False, 1.0, "1", None, 0, -1, 2147483648, 10**50])
def test_strict_integer_ids_before_service_or_session(isolated, field, value):
    payload = body()
    if field in ("binding_id", "resource_id"):
        payload["assignments"][0][field] = value
    elif field == "identity":
        payload["test_identity_ids"] = [value]
    else:
        payload[field] = value
    error(client.post(URL, json=payload), 422, INVALID)
    isolated[0].assert_not_called()
    assert isolated[1] == []


@pytest.mark.parametrize("field", ["assignments", "test_identity_ids"])
@pytest.mark.parametrize("value", [None, {}, "123", 1, True])
def test_arrays_are_required(isolated, field, value):
    error(client.post(URL, json=body(**{field: value})), 422, INVALID)
    isolated[0].assert_not_called()


@pytest.mark.parametrize("value", [
    None, True, 1906545600, 1906545600.0, "1906545600", "2030-06-01",
    "2030-06-01T12:00:00", "2030-06-01 12:00:00Z", "2030-06-01T12:00Z",
    "2030-06-01T12:00:00.1234567Z", "2030-02-30T12:00:00Z",
    "0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00",
    "2030-06-01T12:00:00+24:00", "2030-06-01T12:00:00+01:60",
])
def test_time_is_explicit_rfc3339_with_representable_instant(isolated, value):
    error(client.post(URL, json=body(evaluation_time=value)), 422, INVALID)
    isolated[0].assert_not_called()


@pytest.mark.parametrize("where", ["top", "assignment", "query"])
def test_unknown_names_and_sensitive_values_are_never_reflected(isolated, where):
    payload, url = body(), URL
    if where == "query":
        url += f"?endpoint_id=99&{SECRET}={SECRET}"
    else:
        (payload if where == "top" else payload["assignments"][0])[SECRET] = {"credentials": SECRET}
    error(client.post(url, json=payload, headers={"Authorization": SECRET}), 422, INVALID)
    isolated[0].assert_not_called()


@pytest.mark.parametrize("raw", [
    b"", b"\xff", b"{", b"null", b"[]", b"true",
    b'{"endpoint_id":1,"endpoint_id":2}',
    b'{"assignments":[{"binding_id":1,"binding_id":2}]}',
    b'{"evaluation_time":"2030-06-01T12:00:00Z","evaluation_time":"2031-01-01T00:00:00Z"}',
    b'{"endpoint_id":NaN}', b'{"endpoint_id":Infinity}', b'{"endpoint_id":-Infinity}',
    b"[" * 5000 + b"0" + b"]" * 5000,
    ('{"' + SECRET + '":NaN}').encode(),
    b'{"endpoint_id":' + b"9" * 5000 + b"}",
])
def test_bad_json_is_sanitized_before_session(isolated, raw):
    error(client.post(URL, content=raw, headers={"content-type": "application/json"}), 422, INVALID)
    isolated[0].assert_not_called()
    assert isolated[1] == []


@pytest.mark.parametrize("field,value", [
    ("endpoint_id", "1"), ("binding_id", "2"), ("resource_id", "3"),
    ("evaluation_time", '"2030-06-01T12:00:00Z"'),
])
def test_duplicate_keys_in_otherwise_valid_request_never_use_last_value(isolated, field, value):
    raw = json.dumps(body()).replace(f'"{field}": {value}', f'"{field}": {value}, "{field}": {value}')
    error(client.post(URL, content=raw, headers={"content-type": "application/json"}), 422, INVALID)
    isolated[0].assert_not_called()
    assert isolated[1] == []


@pytest.mark.parametrize("at", [
    "2030-06-01t12:00:00z", "2030-06-01T07:00:00-05:00",
    "2030-11-03T01:30:00-04:00", "2030-11-03T01:30:00-05:00",
    "2030-06-01T17:30:00.123456+05:30",
])
def test_valid_explicit_times_preserve_exact_instant(isolated, at):
    response = client.post(URL, json=body(evaluation_time=at))
    assert response.status_code == 200
    expected = datetime.fromisoformat(at.upper()).astimezone(timezone.utc)
    assert datetime.fromisoformat(response.json()["evaluation_time"]).astimezone(timezone.utc) == expected
    assert datetime.fromisoformat(response.json()["slots"][0]["preview"]["evaluation_time"]).astimezone(timezone.utc) == expected


@pytest.mark.parametrize("headers", [
    {}, {"content-type": "text/plain"}, {"content-type": "application/problem+json"},
    {"content-type": "application/json; charset=latin-1"},
    {"content-type": "application/json", "content-encoding": "gzip"},
    {"content-type": "application/json", "content-encoding": "br"},
    {"content-type": "application/json", "content-encoding": "identity,gzip"},
])
def test_media_and_encoding_rejection(isolated, headers):
    error(client.post(URL, content=json.dumps(body()).encode(), headers=headers), 415, MEDIA)
    isolated[0].assert_not_called()


@pytest.mark.parametrize("content_type", [
    "application/json", "application/json; charset=utf-8",
    'Application/JSON; charset="UTF-8"',
])
def test_utf8_media_type_and_identity_encoding_accepted(isolated, content_type):
    response = client.post(URL, content=json.dumps(body()), headers={
        "content-type": content_type, "content-encoding": "identity",
    })
    assert response.status_code == 200


def asgi_request(chunks, *, headers=()):
    """Deliver exact ASGI chunks/headers without TestClient adding Content-Length."""
    received, sent = [], []
    async def run():
        remaining = iter(chunks)
        async def receive():
            chunk, more = next(remaining)
            received.append(len(chunk))
            return {"type": "http.request", "body": chunk, "more_body": more}
        async def send(message):
            sent.append(message)
        await app({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": URL, "raw_path": URL.encode(),
            "query_string": b"", "root_path": "", "headers": [
                (b"content-type", b"application/json"), *headers,
            ], "client": ("127.0.0.1", 1), "server": ("localhost", 80),
        }, receive, send)
    asyncio.run(run())
    return received, sent


@pytest.mark.parametrize("headers", [
    (), ((b"content-length", b"1"),), ((b"transfer-encoding", b"chunked"),),
    ((b"content-length", b"65537"),),
])
def test_actual_request_limit_before_parser_and_no_further_read(api, isolated, monkeypatch, headers):
    monkeypatch.setattr(api, "_parse_json", Mock(side_effect=AssertionError("parser must not run")))
    received, sent = asgi_request([
        (b" " * 65536, True), (b"x", True), (b"not-consumed", False),
    ], headers=headers)
    assert sum(received) <= 65537
    assert sent[0]["status"] == 413
    assert json.loads(sent[-1]["body"]) == {"detail": REQUEST_LIMIT}
    api._parse_json.assert_not_called()
    isolated[0].assert_not_called()
    assert isolated[1] == []


def test_exact_body_limit_missing_length_and_single_read(api, isolated, monkeypatch):
    raw = json.dumps(body()).encode()
    raw += b" " * (65536 - len(raw))
    parser = Mock(wraps=api._parse_json)
    monkeypatch.setattr(api, "_parse_json", parser)
    received, sent = asgi_request([(raw[:30000], True), (raw[30000:], False)])
    assert received == [30000, 35536]
    assert sent[0]["status"] == 200
    assert len([m for m in sent if m["type"] == "http.response.body"]) == 1
    parser.assert_called_once()
    isolated[0].assert_called_once()


@pytest.mark.parametrize("slots,identities,valid", [
    (0, 0, False), (1, 0, True), (32, 0, True), (33, 0, False),
    (1, 512, True), (1, 513, False), (2, 256, True), (2, 257, False),
    (32, 16, True), (32, 17, False),
])
def test_count_and_aggregate_bounds_with_repeated_resources_and_skipped_facts(isolated, slots, identities, valid):
    payload = body(assignments=[{"binding_id": i, "resource_id": 3} for i in range(1, slots + 1)],
                   test_identity_ids=list(range(1, identities + 1)))
    response = client.post(URL, json=payload)
    if valid:
        assert response.status_code == 200
        assert len(response.json()["slots"]) == slots
        assert all(len(s["preview"]["facts"]) == identities and s["preview"]["candidates"] == []
                   for s in response.json()["slots"])
    else:
        error(response, 422, INVALID)
        isolated[0].assert_not_called()


@pytest.mark.parametrize("field", ["assignments", "test_identity_ids"])
def test_duplicates_are_rejected_without_deduplication(isolated, field):
    payload = body()
    payload[field] *= 2
    error(client.post(URL, json=payload), 422, INVALID)
    isolated[0].assert_not_called()


def test_exact_composer_call_order_offset_and_threadpool(api, isolated):
    calls, sessions = isolated
    parsed_time = "2030-06-01T17:30:00.123456+05:30"
    payload = body(endpoint_id=2147483647, assignments=[
        {"binding_id": 8, "resource_id": 2147483647}, {"binding_id": 2, "resource_id": 3},
    ], test_identity_ids=[7, 4], evaluation_time=parsed_time)
    results = []
    def once(db, **kwargs):
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        assert not db.new and not db.dirty and not db.deleted
        value = output(db, **kwargs)
        results.append((value, asdict(value)))
        return value
    calls.side_effect = once
    response = client.post(URL, json=payload)
    assert response.status_code == 200
    calls.assert_called_once_with(
        sessions[0], endpoint_id=2147483647,
        assignments=(BOLAResourceSlotAssignment(8, 2147483647), BOLAResourceSlotAssignment(2, 3)),
        test_identity_ids=(7, 4), evaluation_time=datetime.fromisoformat(parsed_time),
    )
    assert response.json()["test_identity_ids"] == [7, 4]
    assert [s["binding"]["binding_id"] for s in response.json()["slots"]] == [8, 2]
    assert datetime.fromisoformat(response.json()["evaluation_time"]) == datetime.fromisoformat(parsed_time)
    assert asdict(results[0][0]) == results[0][1]
    assert response.headers["cache-control"] == "no-store"


DOMAIN_CODES = {
    404: ["endpoint_not_found", "resource_not_found", "test_identity_not_found", "bola_binding_not_found"],
    422: ["bola_binding_matrix_invalid_input", "bola_binding_matrix_assignment_limit_exceeded",
          "bola_binding_matrix_duplicate_binding", "bola_matrix_invalid_access_fact",
          "bola_matrix_duplicate_access_fact", "bola_matrix_fact_limit_exceeded",
          "bola_matrix_evaluation_time_invalid", "evaluation_time_timezone_required",
          "bola_binding_selection_invalid_input"],
    409: ["bola_binding_matrix_duplicate_slot", "bola_binding_endpoint_mismatch", "bola_binding_not_confirmed",
          "bola_binding_location_unsupported", "bola_binding_selector_invalid",
          "bola_binding_endpoint_metadata_invalid", "bola_binding_selector_ambiguous",
          "bola_binding_selector_not_declared", "bola_binding_parameter_limit_exceeded",
          "bola_matrix_endpoint_resource_target_mismatch", "resource_identity_target_mismatch",
          "bola_matrix_identity_inactive", "resource_access_resolution_limit_exceeded"],
}


@pytest.mark.parametrize("status,code", [(s, c) for s, codes in DOMAIN_CODES.items() for c in codes])
def test_exact_domain_code_mapping(isolated, status, code):
    exc = (ResourceAccessResolutionError(code, 418) if code.startswith("resource_") else
           BOLAMatrixPlanningError(code) if code in DOMAIN_CODES[422][3:6] else
           BOLAMatrixPreviewError(code) if code.startswith("bola_matrix_") or code == "endpoint_not_found" else
           BOLABindingSelectionError(code) if code.startswith("bola_binding_") and "matrix" not in code else
           BOLABindingMatrixPreviewError(code))
    exc.args = (SECRET,)
    isolated[0].side_effect = exc
    error(client.post(URL, json=body()), status, code)
    isolated[0].assert_called_once()


@pytest.mark.parametrize("exc", [
    RuntimeError(SECRET), ValueError(SECRET), BOLABindingMatrixPreviewError(SECRET),
    BOLABindingMatrixPreviewError("bola_binding_matrix_session_not_clean"),
    BOLABindingMatrixPreviewError("bola_binding_matrix_dependency_mismatch"),
    BOLAMatrixPreviewError("bola_matrix_resolution_mismatch"),
    BOLAMatrixPreviewError("bola_matrix_preview_session_not_clean"),
    BOLABindingSelectionError("bola_binding_selection_session_not_clean"),
    ResourceAccessResolutionError(SECRET, 409),
])
def test_internal_failures_are_fixed_and_sanitized(isolated, exc):
    isolated[0].side_effect = exc
    error(client.post(URL, json=body()), 500, FAILED)


def sample_output():
    return output(None, endpoint_id=1, assignments=(BOLAResourceSlotAssignment(2, 3),),
                  test_identity_ids=(4,), evaluation_time=AT)


@pytest.mark.parametrize("mutation", [
    "untyped", "wrong_endpoint", "wrong_binding", "wrong_resource", "wrong_time",
    "wrong_identity", "missing_slots", "extra_slots", "selector", "auth_type",
    "supports", "fact_ids", "candidate_kind", "mutable_slots",
])
def test_invalid_output_never_sends_partial_success(isolated, mutation):
    value = sample_output()
    slot, = value.slots
    fact, = slot.preview.facts
    if mutation == "untyped": value = {"credentials": SECRET}
    if mutation == "wrong_endpoint": value = replace(value, endpoint_id=9)
    if mutation == "wrong_binding": slot = replace(slot, binding=replace(slot.binding, binding_id=9))
    if mutation == "wrong_resource": slot = replace(slot, preview=replace(slot.preview, resource_id=9))
    if mutation == "wrong_time": value = replace(value, evaluation_time=AT.replace(year=2031))
    if mutation == "wrong_identity": value = replace(value, test_identity_ids=(5,))
    if mutation == "missing_slots": value = replace(value, slots=())
    if mutation == "extra_slots": value = replace(value, slots=(slot,) * 33)
    if mutation == "mutable_slots": value = replace(value, slots=[slot])
    if mutation == "selector": slot = replace(slot, binding=replace(slot.binding, selector="x" * 129))
    if mutation == "auth_type": fact = replace(fact, identity_auth_type="x" * 31)
    if mutation == "supports": fact = replace(fact, supporting_assertion_ids=(1,) * 257)
    if mutation == "fact_ids": fact = replace(fact, endpoint_id=9)
    if mutation == "candidate_kind":
        slot = replace(slot, preview=replace(slot.preview, candidates=(
            BOLAMatrixCandidate(1, 3, 4, "authenticated", SECRET, "owner", "allowed", ()),
        )))
    if mutation in ("auth_type", "supports", "fact_ids"):
        slot = replace(slot, preview=replace(slot.preview, facts=(fact,)))
    if mutation in ("wrong_binding", "wrong_resource", "selector", "auth_type", "supports", "fact_ids", "candidate_kind"):
        value = replace(value, slots=(slot,))
    isolated[0].side_effect = None
    isolated[0].return_value = value
    error(client.post(URL, json=body()), 500, FAILED)


@pytest.mark.parametrize("collection", ["facts", "candidates"])
def test_output_cell_bound_before_converting_nested_objects(isolated, collection):
    class Unreadable:
        @property
        def endpoint_id(self):
            pytest.fail("Output exceeding the requested cells was traversed.")
    value = sample_output()
    oversized = replace(value.slots[0].preview, **{collection: (Unreadable(), Unreadable())})
    value = replace(value, slots=(replace(value.slots[0], preview=oversized),))
    isolated[0].side_effect = None
    isolated[0].return_value = value
    error(client.post(URL, json=body()), 500, FAILED)


def test_complete_utf8_response_size_before_sending(api, isolated, monkeypatch):
    value = sample_output()
    fact = replace(value.slots[0].preview.facts[0], identity_auth_type="é" * 30)
    value = replace(value, slots=(replace(value.slots[0], preview=replace(value.slots[0].preview, facts=(fact,))),))
    isolated[0].side_effect = None
    isolated[0].return_value = value
    response = client.post(URL, json=body())
    assert response.status_code == 200
    size = len(response.content)
    assert size > len(response.text)
    assert api.MAX_BOLA_MATRIX_RESPONSE_BYTES == 4194304
    monkeypatch.setattr(api, "MAX_BOLA_MATRIX_RESPONSE_BYTES", size)
    assert client.post(URL, json=body()).content == response.content
    monkeypatch.setattr(api, "MAX_BOLA_MATRIX_RESPONSE_BYTES", size - 1)
    _, sent = asgi_request([(json.dumps(body()).encode(), False)])
    assert sent[0]["status"] == 500
    assert json.loads(sent[-1]["body"]) == {"detail": RESPONSE_LIMIT}
    assert len(sent) == 2


@pytest.mark.parametrize("extra", [0, 1])
def test_real_four_mib_serialized_boundary(api, isolated, monkeypatch, extra):
    schema = importlib.import_module("app.schemas.bola_matrix")
    original = schema.BOLAMatrixPreviewResponse.model_dump_json
    def padded(self, **kwargs):
        encoded = original(self, **kwargs)
        return encoded + " " * (4194304 + extra - len(encoded.encode("utf-8")))
    monkeypatch.setattr(schema.BOLAMatrixPreviewResponse, "model_dump_json", padded)
    _, sent = asgi_request([(json.dumps(body()).encode(), False)])
    assert len(sent) == 2  # No success prefix or partial streaming.
    if extra:
        assert sent[0]["status"] == 500
        assert json.loads(sent[1]["body"]) == {"detail": RESPONSE_LIMIT}
    else:
        assert sent[0]["status"] == 200
        assert len(sent[1]["body"]) == 4194304
        assert json.loads(sent[1]["body"])["endpoint_id"] == 1


def test_opaque_support_order_and_duplicate_provenance_is_preserved(isolated):
    value = sample_output()
    supports = (90, 1, 90, 2)
    fact = replace(value.slots[0].preview.facts[0], resolution_state="resolved",
                   relationship="owner", expected_access="denied", supporting_assertion_ids=supports)
    candidate = BOLAMatrixCandidate(1, 3, 4, "authenticated", "owner_access", "owner", "denied", supports)
    value = replace(value, slots=(replace(value.slots[0], preview=replace(
        value.slots[0].preview, facts=(fact,), candidates=(candidate,),
    )),))
    original = asdict(value)
    isolated[0].side_effect = None
    isolated[0].return_value = value
    response = client.post(URL, json=body())
    assert response.status_code == 200
    preview = response.json()["slots"][0]["preview"]
    assert preview["facts"][0]["supporting_assertion_ids"] == list(supports)
    assert preview["candidates"][0]["supporting_assertion_ids"] == list(supports)
    assert asdict(value) == original


def test_route_has_no_direct_sql_or_lower_layer_calls(api):
    tree = ast.parse(inspect.getsource(api))
    imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(module.startswith("app.db.models") for module in imports)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {
        "select_bola_binding", "preview_bola_matrix", "resolve_resource_access", "plan_bola_matrix",
        "select", "execute", "scalar", "scalars", "query", "flush", "commit", "rollback",
        "begin", "begin_nested", "close", "owner_identity_id", "external_id", "credentials",
        "build_authentication_context", "__dict__", "asdict",
    }
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "preview_bola_binding_matrix"]
    assert len(calls) == 1
    assert not inspect.iscoroutinefunction(api.preview_matrix)


def test_serialization_failure_is_sanitized(isolated, monkeypatch):
    schema = importlib.import_module("app.schemas.bola_matrix")
    monkeypatch.setattr(schema.BOLAMatrixPreviewResponse, "model_dump_json", Mock(side_effect=ValueError(SECRET)))
    error(client.post(URL, json=body()), 500, FAILED)


def test_openapi_resolves_all_refs_and_documents_real_contract():
    document = app.openapi()
    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value:
                ref = document
                for part in value["$ref"].removeprefix("#/").split("/"):
                    ref = ref[part.replace("~1", "/").replace("~0", "~")]
            for item in value.values(): walk(item)
        elif isinstance(value, list):
            for item in value: walk(item)
    walk(document)
    operation = document["paths"][URL]["post"]
    request = operation["requestBody"]
    assert request["required"]
    schema = request["content"]["application/json"]["schema"]
    assert set(schema["required"]) == set(body())
    assert schema["additionalProperties"] is False
    assert schema["properties"]["assignments"]["maxItems"] == 32
    assert schema["properties"]["assignments"]["items"]["additionalProperties"] is False
    assert schema["properties"]["test_identity_ids"]["maxItems"] == 512
    assert schema["properties"]["endpoint_id"]["maximum"] == 2147483647
    assert schema["properties"]["evaluation_time"]["format"] == "date-time"
    assert set(operation["responses"]) == {"200", "404", "409", "413", "415", "422", "500"}
    assert all(str(n) in json.dumps(operation) for n in (65536, 4194304, 512))
    assert "non-executable" in operation["description"]
    assert "synthetic" in json.dumps(request).lower()


def test_unrelated_validation_behavior_is_unchanged():
    response = client.get("/api/resources/not-an-integer/access-assertions")
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
