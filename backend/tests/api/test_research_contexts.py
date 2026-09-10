import json
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event

from app.db.session import engine
from app.main import app
from tests.research_intake_fixtures import REF, intake, intake_target, snapshot  # noqa: F401

client = TestClient(app)
ROOT = "/api/research-projects/1/contexts"


@pytest.fixture(autouse=True)
def no_side_effect_capabilities(monkeypatch):
    blocked = Mock(side_effect=AssertionError("intake crossed capability boundary"))
    for path in (
        "app.credentials.bearer.BearerCredentialService.resolve",
        "app.credentials.bearer.BearerCredentialService.resolve_binding",
        "app.ai.mock_provider.MockAIProvider.analyze",
        "app.services.plan_execution.PlanExecutionService.execute",
        "app.network_safety.gateway.NetworkGateway.request",
        "socket.getaddrinfo",
    ):
        monkeypatch.setattr(path, blocked)
    yield
    blocked.assert_not_called()


def test_api_workflow_and_actual_readiness(intake_target):
    before = snapshot(legacy=True)
    response = client.post(ROOT, json=intake(intake_target))
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["missing_inputs"] == ["budget_unapproved", "facts_missing"]
    assert body["permissions"][0]["status"] == "referenced_current"
    url = f'{ROOT}/{body["context_id"]}'
    read = client.get(url)
    assert read.status_code == 200 and read.json()["intake"] == body["intake"]
    missing = intake(intake_target)
    missing["targets"][0].update(authorization_revision_id=None, permission_source=None)
    missing["eligibility_reference"] = None
    correction = client.post(url+"/versions", json={"expected_version": 1,
        "correction_reference": REF, "intake": missing})
    assert correction.status_code == 200
    assert correction.json()["missing_inputs"] == ["permission_missing", "data_ineligible", "budget_unapproved", "facts_missing"]
    assert client.get(url+"/versions/1").json()["intake"] == body["intake"]
    assert client.get(url.replace("projects/1", "projects/2")).json() == {"detail": "intake_context_unavailable"}
    conflict = client.post("/api/research-projects/2/contexts", json=intake(intake_target))
    assert conflict.status_code == 409 and conflict.json() == {"detail": "intake_context_unavailable"}
    close = client.post(url+"/close", json={"expected_version": 2, "closure_reference": REF})
    assert close.status_code == 200 and close.json()["state"] == "closed"
    assert close.json()["execution_preparation_allowed"] is False
    assert snapshot(legacy=True) == before


@pytest.mark.parametrize("field", ["version", "purpose", "data_eligibility", "eligibility_reference", "rules", "targets", "budget"])
def test_missing_fields_are_not_defaulted(intake_target, field):
    payload = intake(intake_target)
    del payload[field]
    before = snapshot()
    assert client.post(ROOT, json=payload).json() == {"detail": "intake_invalid_request"}
    assert snapshot() == before


@pytest.mark.parametrize("field", ["target_requests", "duration_seconds", "concurrency",
    "rate_millirequests_per_second", "model_tokens", "model_cost_microusd"])
@pytest.mark.parametrize("value", [True, -1, "1", 1.0])
def test_strict_budget_types_and_no_partial_write(intake_target, field, value):
    payload = intake(intake_target)
    payload["budget"][field] = value
    before = snapshot()
    result = client.post(ROOT, json=payload)
    assert result.status_code == 422 and result.json() == {"detail": "intake_invalid_request"}
    assert snapshot() == before


@pytest.mark.parametrize("field,maximum", [("target_requests", 100), ("duration_seconds", 1800),
    ("concurrency", 1), ("rate_millirequests_per_second", 1000), ("model_tokens", 1000000000),
    ("model_cost_microusd", 1000000000)])
def test_exact_and_over_budget_storage_bounds(intake_target, field, maximum):
    payload = intake(intake_target)
    payload["budget"][field] = maximum+1
    before = snapshot()
    assert client.post(ROOT, json=payload).status_code == 422
    assert snapshot() == before
    payload["budget"][field] = maximum
    response = client.post(ROOT, json=payload)
    assert response.status_code == 201
    assert response.json()["intake"]["budget"][field] == maximum
    assert response.json()["budget_approval"] == "unverified"
    assert response.json()["execution_authorized"] is False


@pytest.mark.parametrize("mutation", ["private", "rule_text", "source_url", "secret", "extra",
    "verified", "approved", "unit", "duplicate_target", "duplicate_rule", "zero_incoherent", "revision_list"])
def test_untrusted_or_incoherent_content_rejected_without_echo(intake_target, mutation, caplog):
    payload = intake(intake_target)
    marker = "synthetic-secret-marker@example.invalid"
    if mutation == "private":
        payload["data_eligibility"] = "reviewed-minimized-private"
    elif mutation == "rule_text":
        payload["rules"][0]["rule"] = marker
    elif mutation == "source_url":
        payload["rules"][0]["source"] = {"url": "https://example.invalid/"+marker}
    elif mutation == "secret":
        payload["eligibility_reference"]["fixture_id"] = marker
    elif mutation == "extra":
        payload[marker] = marker
    elif mutation == "verified":
        payload["targets"][0]["verified"] = True
    elif mutation == "approved":
        payload["budget"]["approval_state"] = "approved"
    elif mutation == "unit":
        payload["budget"]["currency"] = "EUR"
    elif mutation == "duplicate_target":
        payload["targets"] *= 2
    elif mutation == "duplicate_rule":
        payload["rules"] *= 2
    elif mutation == "zero_incoherent":
        payload["budget"]["target_requests"] = 0
    elif mutation == "revision_list":
        payload["targets"][0]["authorization_revision_id"] = [intake_target["revision"]]
    before = snapshot()
    response = client.post(ROOT, json=payload)
    assert response.status_code == 422 and response.json() == {"detail": "intake_invalid_request"}
    assert marker not in response.text and marker not in caplog.text
    assert snapshot() == before


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'\xef\xbb\xbf{}', b'\xff', b'{',
    b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'{"x":"\\ud800"}', b'['*2000])
def test_malformed_transport_sanitized(intake_target, raw):
    before = snapshot()
    response = client.post(ROOT, content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and response.json() == {"detail": "intake_invalid_request"}
    assert snapshot() == before


def test_actual_byte_limit_stream_without_content_length_and_media(intake_target):
    raw = json.dumps(intake(intake_target)).encode()
    padding = b' '*(16384-len(raw))
    before = snapshot()
    response = client.post(ROOT, content=iter([raw, padding, b' ']), headers={"Content-Type": "application/json"})
    assert response.status_code == 413 and snapshot() == before
    assert client.post(ROOT, content=raw, headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post(ROOT, content=raw, headers={"Content-Type": "application/json", "Content-Encoding": "gzip"}).status_code == 415
    assert client.post(ROOT+"?project_number=2", json=intake(intake_target)).status_code == 422
    assert client.post(ROOT, content=iter([raw, padding]), headers={"Content-Type": "application/json"}).status_code == 201


def test_endpoint_rolls_back_on_late_serialization_failure(intake_target, monkeypatch, caplog):
    before = snapshot()
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic-secret-late-error")
    monkeypatch.setattr("app.api.routes.research_contexts.encoded", fail)
    response = client.post(ROOT, json=intake(intake_target))
    assert response.status_code == 500 and response.json() == {"detail": "intake_failed"}
    assert "synthetic-secret-late-error" not in caplog.text
    assert snapshot() == before


def test_no_credential_reads_or_legacy_writes_in_sql(intake_target):
    before = snapshot(legacy=True)
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())
    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = client.post(ROOT, json=intake(intake_target))
        assert result.status_code == 201
        assert client.get(f'{ROOT}/{result.json()["context_id"]}').status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    for statement in statements:
        assert not any(word in statement for word in ("credential", "test_runs", "test_identities", "execution_plans", "findings", "resource_access_assertions"))
        if statement.startswith(("insert", "update", "delete")):
            assert "research_" in statement
    assert snapshot(legacy=True) == before


def test_unknown_and_zero_budgets_are_distinct_unapproved_records(intake_target):
    payload = intake(intake_target)
    for k in ("target_requests", "duration_seconds", "concurrency", "rate_millirequests_per_second",
              "model_tokens", "model_cost_microusd"):
        payload["budget"][k] = None
    result = client.post(ROOT, json=payload).json()
    assert len(result["budget_missing_fields"]) == 7
    for k in ("target_requests", "duration_seconds", "concurrency", "rate_millirequests_per_second",
              "model_tokens", "model_cost_microusd"):
        payload["budget"][k] = 0
    payload["budget"]["approval_reference"] = REF
    response = client.post(f'{ROOT}/{result["context_id"]}/versions', json={"expected_version": 1,
        "correction_reference": REF, "intake": payload})
    assert response.status_code == 200
    assert response.json()["budget_missing_fields"] == []
    assert response.json()["budget_approval"] == "unverified"
    assert "budget_unapproved" in response.json()["missing_inputs"]


def test_generated_openapi_has_inline_request_and_typed_response():
    spec = app.openapi()
    operation = spec["paths"]["/api/research-projects/{project_number}/contexts"]["post"]
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert "$defs" not in json.dumps(request_schema) and "$ref" not in json.dumps(request_schema)
    assert request_schema["additionalProperties"] is False
    assert operation["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/ResearchContextRead")
