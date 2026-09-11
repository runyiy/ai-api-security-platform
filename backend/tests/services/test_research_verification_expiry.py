"""Observed deadlines cannot be undone by a later UTC correction.

All execution uses the owned synthetic server and real dispatcher/M8. Monotonic
samples continue increasing while separate requests receive corrected UTC.
"""
from copy import deepcopy
from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.db.session import SessionLocal
from app.db.models.research_verification import VerificationClockFault
from app.services import research_intent as intent, research_verification as verify
from app.services import research_verification_clock as vc
from app.schemas import research_intent as c
from app.api.routes import research_intents as intent_route, research_verification as route
from tests.research_verification_fixtures import (verification_graph, intent_graph, subject_pair,
    two_intake_targets, original_targets, owned_server, subject_encryption, bootstrap,
    plan_approve, send, call, confirm, conversion, approve, NOW, REF)
from tests.research_intake_fixtures import snapshot


def prepare(g, seconds):
    if seconds == 300:
        confirm(g)
        value=call(intent.convert,g,1,conversion(g,purpose='health_probe'))
        role='health';history=None;healthy=None
    else:
        healthy,history,value=bootstrap(g)
        role='baseline' if seconds==120 else 'probe'
        if seconds==30:
            plan_approve(g,value,'baseline')
            history=send(g,value,'baseline')
    member=plan_approve(g,value,role)
    return value,role,member,history,healthy


def faults(g):
    with SessionLocal() as db:
        return set(db.scalars(select(VerificationClockFault.digest).where(VerificationClockFault.context_id==g['ctx'])))


def unchanged_except_fence(before):
    after=snapshot()
    assert {k:v for k,v in after.items() if k!='research_verification_clock_faults'} == {
        k:v for k,v in before.items() if k!='research_verification_clock_faults'}


@pytest.fixture
def clocks(monkeypatch):
    wall=[NOW];samples=[]
    real=vc.monotonic_ns
    def monotonic():
        value=real();samples.append(value);return value
    monkeypatch.setattr(vc,'monotonic_ns',monotonic)
    monkeypatch.setattr(vc,'utcnow',lambda:wall[0])
    yield wall
    assert len(samples)>1 and samples[-1]>samples[0]
    assert all(a<=b for a,b in zip(samples,samples[1:]))


def endpoint(g, suffix):
    return f"/api/research-projects/{g['project']}/contexts/{g['ctx']}/{suffix}"


@pytest.mark.parametrize('seconds',[120,30,300])
@pytest.mark.parametrize('entry',['service_read','api_read','api_execute'])
def test_observed_expiry_survives_rollback_and_fresh_corrected_request(verification_graph,monkeypatch,clocks,seconds,entry):
    g=verification_graph;value,role,member,history,healthy=prepare(g,seconds)
    monkeypatch.setattr(route,'executor',g['executor'])
    before=snapshot();requests=list(g['server']['requests'])
    clocks[0]=NOW+timedelta(seconds=seconds)
    with TestClient(app) as api:
        if entry=='service_read':
            # Includes _exact's check before the former late binding in W1 read.
            with SessionLocal() as db:
                with pytest.raises(c.IntentError,match='expired'):
                    intent.read(db,g['project'],g['ctx'],'intent',value['reference'],now=vc.Clock())
                db.rollback()
        else:
            suffix='intents/read/intent' if entry=='api_read' else 'verification/execute'
            payload=value['reference'] if entry=='api_read' else dict(intent=value['reference'],plan_id=member['plan_id'])
            response=api.post(endpoint(g,suffix),json=payload)
            assert response.status_code==409 and 'expired' in response.json()['code']
        unchanged_except_fence(before)
        assert value['reference']['digest'] in faults(g)
        clocks[0]=NOW+timedelta(seconds=1)
        # Each call opens a new Session and Clock. The pending exact plan cannot
        # make the first (or dependent probe) GET after UTC correction.
        response=api.post(endpoint(g,'verification/execute'),json=dict(intent=value['reference'],plan_id=member['plan_id']))
        assert response.status_code==409 and response.json()['code']=='verification_clock_invalidated'
        with pytest.raises(c.IntentError,match='clock_invalidated'):
            call(intent.read,g,'intent',value['reference'],now=vc.Clock())
        if history:
            result=api.post(endpoint(g,'verification/history/execution'),json=history['reference'])
            assert result.status_code==200 and result.json()['body']==history['body']
            assert result.json()['reusable'] is False and 'eligibility_until' not in result.json()
    assert g['server']['requests']==requests
    if seconds==30:
        # Expiring the pair must not fence its still-valid health dependency.
        assert healthy['reference']['digest'] not in faults(g)
        assert call(intent.read,g,'intent',healthy['reference'],now=vc.Clock())['reference']==healthy['reference']


@pytest.mark.parametrize('seconds',[120,30,300])
@pytest.mark.parametrize('entry',['intent_response','approval_response'])
def test_final_response_expiry_is_durable(verification_graph,monkeypatch,clocks,seconds,entry):
    g=verification_graph;value,role,member,_,healthy=prepare(g,seconds)
    monkeypatch.setattr(route,'executor',g['executor'])
    before=snapshot();requests=list(g['server']['requests'])
    selected=intent_route if entry=='intent_response' else route
    original=selected.Response
    def delayed_encoding(*args,**kwargs):
        response=original(*args,**kwargs)
        clocks[0]=NOW+timedelta(seconds=seconds)
        return response
    monkeypatch.setattr(selected,'Response',delayed_encoding)
    with TestClient(app) as api:
        suffix='intents/read/intent' if entry=='intent_response' else 'verification/plan-decisions'
        payload=value['reference'] if entry=='intent_response' else dict(intent=value['reference'],plan_id=member['plan_id'],
            expected_sequence=1,decision='approved',evidence=REF)
        response=api.post(endpoint(g,suffix),json=payload)
        assert response.status_code==409 and 'expired' in response.json()['code']
        unchanged_except_fence(before)
        assert value['reference']['digest'] in faults(g)
        monkeypatch.setattr(selected,'Response',original)
        clocks[0]=NOW+timedelta(seconds=1)
        response=api.post(endpoint(g,'verification/execute'),json=dict(intent=value['reference'],plan_id=member['plan_id']))
        assert response.status_code==409 and response.json()['code']=='verification_clock_invalidated'
    assert g['server']['requests']==requests
    if seconds==30:assert healthy['reference']['digest'] not in faults(g)


def test_health_witness_expiry_fences_original_and_preserves_history(verification_graph,clocks):
    g=verification_graph;health,receipt,_=bootstrap(g)
    clocks[0]=NOW+timedelta(seconds=120)
    before=snapshot()
    with pytest.raises(c.IntentError,match='expired'):
        call(verify.read_execution,g,receipt['reference'],now=vc.Clock())
    unchanged_except_fence(before)
    assert faults(g)=={health['reference']['digest']}
    clocks[0]=NOW+timedelta(seconds=1)
    with pytest.raises(c.IntentError,match='clock_invalidated'):
        call(verify.read_execution,g,receipt['reference'],now=vc.Clock())
    result,_=call(verify.history,g,receipt['reference'],now=vc.Clock())
    assert result['body']==receipt['body'] and result['reusable'] is False
    assert len(g['server']['requests'])==1


@pytest.mark.parametrize('change',['project','context','digest'])
def test_unowned_or_forged_expired_reference_cannot_fence_valid_work(verification_graph,subject_pair,clocks,change):
    g=verification_graph;value,_,_,_,_=prepare(g,300)
    project,context,reference=g['project'],g['ctx'],dict(value['reference'])
    if change=='project':project=2
    elif change=='context':context=subject_pair[1]['ctx']
    else:reference['digest']='f'*64
    clocks[0]=NOW+timedelta(seconds=300)
    with SessionLocal() as db:
        with pytest.raises(Exception):
            verify.current_intent(db,project,context,reference,vc.Clock())
        db.rollback()
    assert faults(g)==set()
    clocks[0]=NOW+timedelta(seconds=1)
    assert send(g,value,'health',now=vc.Clock())['body']['outcome']=='healthy'
    assert len(g['server']['requests'])==1


def test_fresh_replacement_after_expiry_requires_new_records_and_decisions(verification_graph,clocks):
    g=verification_graph;old,_,_,_,_=prepare(g,300)
    clocks[0]=NOW+timedelta(seconds=300)
    with pytest.raises(c.IntentError,match='expired'):
        call(intent.read,g,'intent',old['reference'],now=vc.Clock())
    clocks[0]=NOW+timedelta(seconds=1)
    p=deepcopy(g['manifest_input']);p['expected_version']=0
    g['manifest']=call(intent.record_manifest,g,2,p)['reference'];approve(g)
    expectations=[dict(role=a['role'],object_key='record_id',object_value='80814' if a['role']=='health_probe' else '7001',
        identity_key='subject_id',identity_value=None if a['role']=='baseline' else 'fixture_actor_p') for a in p['actions']]
    call(verify.confirm,g,2,dict(manifest=g['manifest'],expected_version=0,decision='confirm',expectations=expectations,evidence=REF))
    fresh=call(intent.convert,g,2,conversion(g,purpose='health_probe'))
    with pytest.raises(c.IntentError,match='approval_required'):send(g,fresh,'health',now=vc.Clock())
    assert g['server']['requests']==[]
    plan_approve(g,fresh,'health')
    assert send(g,fresh,'health',now=vc.Clock())['body']['outcome']=='healthy'
    assert faults(g)=={old['reference']['digest']}
    with pytest.raises(c.IntentError,match='clock_invalidated'):send(g,old,'health',now=vc.Clock())
    assert len(g['server']['requests'])==1


def test_expiry_during_dependency_read_survives_failure_before_receipt(verification_graph,monkeypatch,clocks):
    g=verification_graph;value,_,_,_,_=prepare(g,300)
    before=snapshot();original=intent._manifest
    def after_exact(*args,**kwargs):
        clocks[0]=NOW+timedelta(seconds=300)
        return original(*args,**kwargs)
    monkeypatch.setattr(intent,'_manifest',after_exact)
    with pytest.raises(c.IntentError,match='expired'):
        call(intent.read,g,'intent',value['reference'],now=vc.Clock())
    unchanged_except_fence(before)
    assert faults(g)=={value['reference']['digest']}
    monkeypatch.setattr(intent,'_manifest',original)
    clocks[0]=NOW+timedelta(seconds=1)
    with pytest.raises(c.IntentError,match='clock_invalidated'):send(g,value,'health',now=vc.Clock())
    assert g['server']['requests']==[]


def test_committed_conversion_retains_exact_binding_for_final_consumption(verification_graph,clocks):
    g=verification_graph;confirm(g);clock=vc.Clock()
    value=call(intent.convert,g,1,conversion(g,purpose='health_probe'),now=clock)
    before=snapshot();clocks[0]=NOW+timedelta(seconds=300)
    with pytest.raises(c.IntentError,match='expired'):intent.final_boundary(value,clock)
    unchanged_except_fence(before)
    assert faults(g)=={value['reference']['digest']}
    clocks[0]=NOW+timedelta(seconds=1)
    with pytest.raises(c.IntentError,match='clock_invalidated'):
        call(intent.read,g,'intent',value['reference'],now=vc.Clock())
    assert g['server']['requests']==[]
