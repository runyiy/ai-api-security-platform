from copy import deepcopy
from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.main import app
from app.api.routes import research_verification as route
from app.services import research_verification as verify,research_intent as intent
from app.services import research_verification_clock as vc
from app.db.session import SessionLocal
from app.db.models.research_verification import VerificationWitness,VerificationAudit
from tests.research_verification_fixtures import (verification_graph,intent_graph,subject_pair,two_intake_targets,
    original_targets,owned_server,subject_encryption,confirm,conversion,call,NOW,REF)
from tests.research_intake_fixtures import snapshot


def url(g,path):return f"/api/research-projects/{g['project']}/contexts/{g['ctx']}/verification/{path}"


@pytest.fixture
def api(verification_graph,monkeypatch):
    monkeypatch.setattr(route,'executor',verification_graph['executor'])
    with TestClient(app) as client:yield client


def health(g):
    confirm(g)
    return call(intent.convert,g,1,conversion(g,purpose='health_probe'))


def test_explicit_api_actions_actual_platform_provenance(api,verification_graph):
    g=verification_graph;value=health(g)
    p=dict(intent=value['reference'],plan_id=value['body']['link']['members'][0]['plan_id'])
    before=snapshot();res=api.post(url(g,'execute'),json=p)
    assert res.status_code==409 and g['server']['requests']==[] and snapshot()==before
    assert api.post(url(g,'history/plan'),json=p).status_code==409 and snapshot()==before
    res=api.post(url(g,'plan-decisions'),json={**p,'expected_sequence':0,'decision':'approved','evidence':REF})
    assert res.status_code==200 and res.json()['execution_authorized'] is False
    res=api.post(url(g,'execute'),json=p)
    assert res.status_code==200 and res.headers['cache-control']=='no-store'
    body=res.json();assert body['body']['outcome']=='healthy'
    assert body['body']['credential_version_id']==g['secret_version'] and len(g['server']['requests'])==1
    # Historical display is explicitly never a reusable receipt, even when fresh.
    res=api.post(url(g,'history/execution'),json=body['reference'])
    assert res.status_code==200 and res.json()['qualification']=='historical_not_revalidated'
    assert res.json()['reusable'] is False and res.json()['body']==body['body']


@pytest.mark.parametrize('raw,status',[(b'{}',422),(b'{"intent":{},"intent":{}}',422),(b'\xff',422),
    (b'\xef\xbb\xbf{}',422),(b'"'+b'x'*32767+b'"',413)])
def test_transport_rejection_has_no_side_effects(api,verification_graph,raw,status):
    g=verification_graph;before=snapshot()
    res=api.post(url(g,'execute'),content=raw,headers={'content-type':'application/json'})
    assert res.status_code==status and len(res.content)<256 and snapshot()==before
    assert g['server']['requests']==[]


@pytest.mark.parametrize('extra',['passed','synthetic_test_only','health','credential_version_id','url','headers'])
def test_no_caller_proof_or_execution_override(api,verification_graph,extra):
    g=verification_graph;value=health(g);before=snapshot()
    p=dict(intent=value['reference'],plan_id=value['body']['link']['members'][0]['plan_id']);p[extra]=True
    res=api.post(url(g,'execute'),json=p)
    assert res.status_code==422 and snapshot()==before and g['server']['requests']==[]


@pytest.mark.parametrize('change',['project','context','plan','digest'])
@pytest.mark.parametrize('path',['execute','history/plan'])
def test_foreign_and_missing_exact_refs(api,verification_graph,subject_pair,change,path):
    g=verification_graph;value=health(g)
    p=dict(intent=value['reference'],plan_id=value['body']['link']['members'][0]['plan_id']);u=url(g,path)
    if change=='project':u=u.replace('/research-projects/1/','/research-projects/2/')
    elif change=='context':u=u.replace(f"/contexts/{g['ctx']}/",f"/contexts/{subject_pair[1]['ctx']}/")
    elif change=='plan':p['plan_id']+=100000
    else:p['intent']={**p['intent'],'digest':'f'*64}
    before=snapshot();res=api.post(u,json=p)
    assert res.status_code==409 and snapshot()==before and g['server']['requests']==[]


def test_api_encoding_failure_keeps_network_canonical_but_rolls_back_response_audit(api,verification_graph,monkeypatch):
    g=verification_graph;value=health(g)
    p=dict(intent=value['reference'],plan_id=value['body']['link']['members'][0]['plan_id'])
    assert api.post(url(g,'plan-decisions'),json={**p,'expected_sequence':0,'decision':'approved','evidence':REF}).status_code==200
    original=route.encoded
    def fail(value,clock):
        if value['kind']=='execution':raise RuntimeError('synthetic encode failure')
        return original(value,clock)
    monkeypatch.setattr(route,'encoded',fail)
    result=api.post(url(g,'execute'),json=p)
    assert result.status_code==500 and len(g['server']['requests'])==1
    with SessionLocal() as db:
        row=db.scalar(select(VerificationWitness).where(VerificationWitness.context_id==g['ctx']))
        assert row is not None
        assert db.scalar(select(func.count()).select_from(VerificationAudit).where(VerificationAudit.context_id==g['ctx'],VerificationAudit.code=='read'))==0
    monkeypatch.setattr(route,'encoded',original)
    recovered=api.post(url(g,'history/plan'),json=p)
    assert recovered.status_code==200 and recovered.json()['reusable'] is False
    assert recovered.json()['body']['plan_id']==p['plan_id'] and 'eligibility_until' not in recovered.json()
    assert api.post(url(g,'execute'),json=p).status_code==200
    assert len(g['server']['requests'])==1


@pytest.mark.parametrize('offset,ok',[(-1,True),(0,False),(1,False)])
def test_final_response_construction_deadline_rolls_back_pair(api,verification_graph,monkeypatch,offset,ok):
    from tests.research_verification_fixtures import bootstrap,plan_approve,send
    g=verification_graph;_,_,business=bootstrap(g)
    plan_approve(g,business,'baseline');plan_approve(g,business,'probe')
    baseline=send(g,business,'baseline');probe=send(g,business,'probe');before=snapshot()
    now=[NOW];monkeypatch.setattr(vc,'utcnow',lambda:now[0]);original=route.Response
    def construction(*args,**kwargs):
        response=original(*args,**kwargs)
        now[0]=NOW+timedelta(seconds=30,microseconds=offset)
        return response
    monkeypatch.setattr(route,'Response',construction)
    res=api.post(url(g,'pairs'),json=dict(intent=business['reference'],baseline=baseline['reference'],probe=probe['reference']))
    assert res.status_code==(200 if ok else 409)
    if not ok:
        from tests.services.test_research_verification_expiry import unchanged_except_fence, faults
        unchanged_except_fence(before)
        assert faults(g)=={business['reference']['digest']}
    assert len(g['server']['requests'])==3


def test_expired_pair_history_never_restores_qualification(api,verification_graph,monkeypatch):
    from tests.research_verification_fixtures import bootstrap,plan_approve,send
    g=verification_graph;_,_,business=bootstrap(g)
    plan_approve(g,business,'baseline');plan_approve(g,business,'probe')
    baseline=send(g,business,'baseline');probe=send(g,business,'probe')
    payload=dict(intent=business['reference'],baseline=baseline['reference'],probe=probe['reference'])
    result=api.post(url(g,'pairs'),json=payload);assert result.status_code==200
    pair=result.json();monkeypatch.setattr(vc,'utcnow',lambda:NOW+timedelta(seconds=30))
    assert api.post(url(g,'pairs'),json=payload).status_code==409
    history=api.post(url(g,'history/pair'),json=pair['reference'])
    assert history.status_code==200 and history.json()['body']==pair['body']
    assert history.json()['reusable'] is False and 'eligibility_until' not in history.json()


def test_unreturned_uncertain_execution_recovered_by_exact_plan(api,verification_graph,monkeypatch):
    from tests.services.test_research_verification import mutate
    g=verification_graph;value=health(g)
    p=dict(intent=value['reference'],plan_id=value['body']['link']['members'][0]['plan_id'])
    assert api.post(url(g,'plan-decisions'),json={**p,'expected_sequence':0,'decision':'approved','evidence':REF}).status_code==200
    g['server']['before_response']=lambda:mutate(g,'approval',value)
    assert api.post(url(g,'execute'),json=p).status_code==409
    recovered=api.post(url(g,'history/plan'),json=p)
    assert recovered.status_code==200 and recovered.json()['body']['outcome']=='inconclusive'
    assert recovered.json()['body']['temporal_status']=='dependency_unavailable'
    assert recovered.json()['reusable'] is False and recovered.json()['execution_authorized'] is False
    before=snapshot()
    def fail(*args):raise RuntimeError('synthetic historical encoding failure')
    monkeypatch.setattr(verify,'history_output',fail)
    assert api.post(url(g,'history/plan'),json=p).status_code==500 and snapshot()==before
    assert len(g['server']['requests'])==1
