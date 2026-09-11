from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.schemas import research_intent as s
from app.services import research_intent as service
from app.api.routes import research_intents as route
from tests.research_intent_fixtures import (intent_graph,subject_pair,two_intake_targets,qualified_future,
    call,approve,conversion,NOW,REF)  # noqa: F401
from tests.research_intake_fixtures import snapshot


def url(g,path):return f"/api/research-projects/{g['project']}/contexts/{g['ctx']}/intents/{path}"


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(service,'_time',lambda clock=None:NOW)
    with TestClient(app) as client:yield client


def test_distinct_local_commands_and_closed_w2(api,intent_graph):
    g=intent_graph
    response=api.post(url(g,'budget-decisions'),json=dict(manifest=g['manifest'],expected_sequence=0,decision='approved',evidence=REF))
    assert response.status_code==200 and response.json()['body']['decision']=='approved'
    before=snapshot();response=api.post(url(g,'versions/1'),json=conversion(g))
    assert response.status_code==409 and response.json()['code']=='intent_w2_evidence_unavailable'
    assert response.headers['cache-control']=='no-store' and snapshot()==before


def test_future_qualified_conversion_api(api,intent_graph,qualified_future):
    g=intent_graph;approve(g)
    response=api.post(url(g,'versions/1'),json=conversion(g))
    assert response.status_code==200 and response.json()['execution_authorized'] is False
    assert len(response.json()['body']['link']['members'])==2
    assert response.json()['execution_status']=='requires_exact_dispatch'


@pytest.mark.parametrize('field,value',[('passed',True),('health',[]),('synthetic_test_only',True),('purpose','execute'),('expected_version',False),('knowledge',{'digest':'a'*64})])
def test_no_runtime_fixture_or_claim_bypass(api,intent_graph,field,value):
    g=intent_graph;p=conversion(g);p[field]=value;before=snapshot()
    response=api.post(url(g,'versions/1'),json=p)
    assert response.status_code==422 and snapshot()==before


@pytest.mark.parametrize('raw,status',[(b'{}',422),(b'{"manifest":{},"manifest":{}}',422),(b'"'+b'a'*32767+b'"',413),(b'\xff',422),(b'\xef\xbb\xbf{}',422)])
def test_bad_transport_is_atomic(api,intent_graph,raw,status):
    before=snapshot();response=api.post(url(intent_graph,'versions/1'),content=raw,headers={'content-type':'application/json'})
    assert response.status_code==status and len(response.content)<256 and snapshot()==before


@pytest.mark.parametrize('change',['project','context','missing','digest'])
def test_foreign_or_missing_refs_are_unavailable(api,intent_graph,subject_pair,change):
    g=intent_graph;p=deepcopy(g['manifest']);u=url(g,'read/manifest')
    if change=='project':u=u.replace('/research-projects/1/','/research-projects/2/')
    if change=='context':u=u.replace(f"/contexts/{g['ctx']}/",f"/contexts/{subject_pair[1]['ctx']}/")
    if change=='missing':p['number']=1000
    if change=='digest':p['digest']='0'*64
    before=snapshot();response=api.post(u,json=p)
    assert response.status_code==409 and snapshot()==before
    assert response.json()['code']=='intent_unavailable'


@pytest.mark.parametrize('offset,ok',[(-1,True),(0,False),(1,False)])
def test_api_complete_encoding_temporal_boundary(api,intent_graph,qualified_future,monkeypatch,offset,ok):
    g=intent_graph;approve(g);before=snapshot();clock=[NOW]
    monkeypatch.setattr(service,'_time',lambda _=None:clock[0])
    real=s.output;calls=[]
    def encode(value):
        raw=real(value);calls.append(1)
        if len(calls)==2:clock[0]=NOW+timedelta(seconds=60,microseconds=offset)
        return raw
    monkeypatch.setattr(s,'output',encode)
    response=api.post(url(g,'versions/1'),json=conversion(g))
    assert response.status_code==(200 if ok else 409)
    if not ok:assert snapshot()==before and b'link' not in response.content


def test_api_serialization_failure_rolls_back(api,intent_graph,qualified_future,monkeypatch):
    g=intent_graph;approve(g);before=snapshot()
    monkeypatch.setattr(route,'encoded',Mock(side_effect=RuntimeError('do not expose this text')))
    response=api.post(url(g,'versions/1'),json=conversion(g))
    assert response.status_code==500 and b'expose' not in response.content and snapshot()==before


def test_api_clock_rollback_after_service_boundary_rejects(api,intent_graph,qualified_future,monkeypatch):
    g=intent_graph;approve(g);before=snapshot();raw=[NOW]
    monkeypatch.setattr(service,'_time',lambda _=None:raw[0])
    real=s.output;calls=[]
    def encode(value):
        result=real(value);calls.append(1)
        raw[0]=NOW+timedelta(seconds=2 if len(calls)==1 else 1)
        return result
    monkeypatch.setattr(s,'output',encode)
    response=api.post(url(g,'versions/1'),json=conversion(g))
    assert response.status_code==409 and response.json()['code']=='intent_clock_invalid'
    assert snapshot()==before


@pytest.mark.parametrize('kind',['manifest','budget','intent'])
def test_recovered_source_cannot_reuse_old_records_through_api(api,intent_graph,monkeypatch,kind,request):
    from tests.services.test_research_intent_sources import attach_source,hold_source,recover_source
    g=intent_graph;oid=attach_source(g);ref=g['manifest']
    if kind=='intent':
        request.getfixturevalue('qualified_future')
        ref=call(service.convert,g,1,conversion(g))['reference']
    hold_source(g,oid)
    at=recover_source(g,oid,'release')
    monkeypatch.setattr(service,'_time',lambda _=None:at)
    before=snapshot()
    path='budget-decisions' if kind=='budget' else 'read/'+kind
    payload=dict(manifest=ref,expected_sequence=1,decision='approved',evidence=REF) if kind=='budget' else ref
    response=api.post(url(g,path),json=payload)
    assert response.status_code==409 and response.json()['code']=='intent_dependency_changed'
    assert response.headers['cache-control']=='no-store' and snapshot()==before
