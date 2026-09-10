import json
from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from app.main import app
from app.db.session import engine
from app.services import research_observation as observation
from tests.research_subject_fixtures import subject_pair, two_intake_targets, proposal, assertion, NOW, REF, zero_capabilities  # noqa: F401
from tests.research_intake_fixtures import snapshot

client=TestClient(app)


@pytest.fixture
def api(subject_pair,monkeypatch):
    clock={"now":NOW}
    monkeypatch.setattr(observation,"_time",lambda value:clock["now"])
    g,b=subject_pair
    return f'/api/research-projects/1/contexts/{g["ctx"]}/subjects',g,b,clock


def post(root,p,number=1):return client.post(root+f"/{number}",json=p)


def test_synthetic_operator_example_with_existing_human_fact_review(api):
    root,g,_,_=api
    # Explicit human entry uses the existing boundary; W3 never promotes imports.
    result=client.post(f'/api/resources/{g["resource"]}/access-assertions',json={
        "test_identity_id":g["anonymous"],"relationship":"non_owner","expected_access":"allowed","confidence":100})
    assert result.status_code==201
    aid=result.json()["id"]
    p=proposal(g);p.update(relationship="non_owner",expected_access="allowed",fact_reference=REF,assertion_ids=[aid])
    before=snapshot(legacy=True)
    result=post(root,p)
    assert result.status_code==200 and result.headers["cache-control"]=="no-store"
    value=result.json()
    assert value["facts"]["expected_access"]=="allowed" and value["facts"]["supporting_assertion_ids"]==[aid]
    assert value["status"]=="NEEDS_INPUT" and value["provenance"]=="operator_proposed_unverified"
    assert snapshot(legacy=True)==before
    candidate=assertion(g,"owner","denied",state="candidate")
    from app.db.models.resource_access_assertion import ResourceAccessAssertion
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        db.get(ResourceAccessAssertion,candidate).provenance="inferred_candidate";db.commit()
    reviewed=client.post(f'/api/resources/{g["resource"]}/access-assertions/{candidate}/review',json={"decision":"verify","confidence":100})
    assert reviewed.status_code==201 and reviewed.json()["reviewed_assertion_id"]==candidate
    assert client.get(root+"/1").json()["facts"]["state"]=="conflict"
    with SessionLocal() as db:
        assert db.get(ResourceAccessAssertion,candidate).verification_state=="candidate"


@pytest.mark.parametrize("size,status",[(8192,200),(8193,413)])
def test_exact_body_byte_boundary(api,size,status):
    root,g,_,_=api
    raw=json.dumps(proposal(g)).encode();raw+=b" "*(size-len(raw))
    assert client.post(root+"/1",content=raw,headers={"content-type":"application/json"}).status_code==status


@pytest.mark.parametrize("raw",[b'{}', b'{"target_id":1,"target_id":2}', b'\xef\xbb\xbf{}',b'\xff',b'{"x":"\\ud800"}',b'{"x":NaN}',b'{"x":1.5}',b'[[[[[[[0]]]]]]]',b'{"secret":"synthetic-canary"}'])
def test_invalid_raw_input_has_bounded_sanitized_error(api,raw,caplog):
    root,_,_,_=api;before=snapshot()
    response=client.post(root+"/1",content=raw,headers={"content-type":"application/json"})
    assert response.status_code in (413,422) and len(response.content)<=256
    assert "synthetic-canary" not in response.text+caplog.text
    assert snapshot()==before


@pytest.mark.parametrize("field,value",[("target_id",True),("resource_id","1"),("binding_id",1.0),
    ("review",{"kind":"private","fixture_id":1,"version":1}),("session_state","synthetic-secret"),
    ("source_test_run_id",1),("verified",True),("access_token","synthetic-canary"),("headers",{"Authorization":"synthetic-canary"})])
def test_extra_secret_and_coerced_fields_rejected_before_persistence(api,field,value,caplog):
    root,g,_,_=api;p=proposal(g);p[field]=value;before=snapshot()
    response=post(root,p)
    assert response.status_code==422 and len(response.content)<=256
    assert "synthetic-canary" not in response.text+caplog.text
    assert snapshot()==before


@pytest.mark.parametrize("failure",["insert","response","audit"])
def test_failure_rolls_back_proposal_and_source_audit(api,monkeypatch,failure):
    from app.api.routes import research_subjects as routes
    root,g,_,_=api;p=proposal(g);p["sources"]=[{"observation_id":g["observation"],"source_entry_index":0}]
    before=snapshot()
    def fail_sql(conn,cursor,statement,*args):
        table="research_subject_versions" if failure=="insert" else "research_observation_events"
        if statement.startswith("INSERT INTO "+table):cursor.execute("SELECT 1/0")
    def fail_response(value):raise RuntimeError("synthetic-canary")
    if failure=="response":monkeypatch.setattr(routes,"encoded",fail_response)
    else:event.listen(engine,"before_cursor_execute",fail_sql)
    try:response=post(root,p)
    finally:
        if failure!="response":event.remove(engine,"before_cursor_execute",fail_sql)
    assert response.status_code==500 and len(response.content)<=256
    assert "synthetic-canary" not in response.text and snapshot()==before


def test_cross_project_and_unknown_routes_are_same(api):
    root,g,b,_=api
    p=proposal(g);before=snapshot()
    foreign=post(root,p | {"resource_id":b["resource"]})
    absent=post(root,p | {"resource_id":2147483647})
    assert foreign.status_code==absent.status_code==409 and foreign.json()==absent.json()
    assert post(root.replace('/research-projects/1/','/research-projects/2/'),p).status_code==409
    assert snapshot()==before


def test_source_expiry_exact_api_boundary_and_history(api):
    root,g,_,clock=api;p=proposal(g)
    p["sources"]=[{"observation_id":g["observation"],"source_entry_index":0}]
    assert post(root,p).status_code==200
    clock["now"]=NOW+timedelta(days=30)-timedelta(microseconds=1)
    assert client.get(root+"/1").json()["availability"]=="available"
    clock["now"]+=timedelta(microseconds=1)
    for path in ("/1","/1/versions/1"):
        result=client.get(root+path).json()
        assert result["availability"]=="unavailable" and result["proposal"] is None and result["facts"] is None
