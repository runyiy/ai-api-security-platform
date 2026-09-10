import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.schemas.research_observation import canonical
from app.services import research_observation as service
from tests.research_observation_fixtures import (observation_context, intake_target, zero_capabilities,
    observation, preparation, call, NOW, REF)  # noqa: F401
from tests.research_intake_fixtures import snapshot

client = TestClient(app)


@pytest.fixture
def root(observation_context, monkeypatch):
    ctx, _ = observation_context
    monkeypatch.setattr(service, "_time", lambda now: NOW)
    return f"/api/research-projects/1/contexts/{ctx}/observations"


def test_api_accept_retry_read_hold_delete_history(root):
    before = snapshot(legacy=True)
    path = root+"/preparations/preparation_1/batches"
    a = client.post(path, json=observation())
    assert a.status_code == 200, a.text
    assert a.headers["cache-control"] == "no-store"
    assert client.post(path, json=observation()).json() == a.json()
    oid = a.json()["observation_id"]
    assert client.get(root+f"/{oid}").json()["payload"] == observation()
    assert client.post(root+f"/{oid}/delete", json={"review": REF}).json()["availability"] == "deleted"
    deleted = client.get(root+f"/{oid}").json()
    assert "payload" not in deleted and deleted["digest"] == a.json()["digest"]
    result = client.post(root+"/maintenance", json={"action": "reconcile", "review": REF}).json()
    assert result["purged_payloads"] == 1 and result["exports_enabled"] is False
    assert client.get(root+f"/{oid}/export").status_code == 404
    assert snapshot(legacy=True) == before


@pytest.mark.parametrize("raw", [b'{"$ref":"file:///private/secret"}', b'{"x":"canary","x":0}', b'\xff',
    b'\xef\xbb\xbf{}', b'{}'+b' '*262143, b'[[[[[[0]]]]]]', b'{"entries":[{"body":"secret"}]}'])
def test_rejections_are_atomic_and_sanitized(root, raw, caplog):
    before = snapshot()
    result = client.post(root+"/preparations/preparation_1/batches", content=raw, headers={"content-type": "application/json"})
    assert result.status_code in (413, 422)
    assert len(result.content) <= 256 and result.json()["status"] == "rejected"
    assert "canary" not in result.text and "secret" not in result.text and "private" not in result.text
    assert "canary" not in caplog.text and "secret" not in caplog.text
    assert snapshot() == before


@pytest.mark.parametrize("headers", [{"content-type": "text/plain"}, {"content-type": "application/json", "content-encoding": "gzip"}])
def test_transport_rejects_compression_and_wrong_type(root, headers):
    assert client.post(root+"/preparations/preparation_1/batches", content=b'{}', headers=headers).status_code == 415


def test_foreign_context_and_unknown_identical(root):
    for url in (root.replace("projects/1", "projects/2"), root.replace("/contexts/", "/contexts/999999")):
        response = client.post(url+"/preparations/preparation_1/batches", json=observation())
        assert response.status_code == 409 and response.json()["code"] == "observation_context_unavailable"


def test_response_encoding_failure_rolls_back_intake(root, monkeypatch):
    from app.api.routes import research_observations as route
    before = snapshot()
    def fail(*a):
        raise RuntimeError("private canary")
    monkeypatch.setattr(route, "encoded", fail)
    response = client.post(root+"/preparations/preparation_1/batches", json=observation())
    assert response.json() == {"status": "rejected", "code": "observation_failed"}
    assert snapshot() == before


def test_preparation_and_maintenance_api_rejects_private_and_unsafe_claims(root, observation_context):
    _, ids = observation_context
    v = preparation(ids)
    v.update(preparation_ref="preparation_2", data_eligibility="reviewed-minimized-private")
    before = snapshot()
    assert client.post(root+"/preparations", json=v).status_code == 422
    assert client.post(root+"/maintenance", json={"action": "reconcile", "review": REF, "backups_encrypted": True}).status_code == 422
    assert snapshot() == before


def test_openapi_documents_strict_observation_fields():
    schema = app.openapi()["paths"]["/api/research-projects/{project}/contexts/{context_id}/observations/preparations/{ref}/batches"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(observation())
    entry = schema["properties"]["entries"]["items"]
    assert entry["additionalProperties"] is False and "body" not in entry["properties"]
