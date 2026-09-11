from sqlalchemy import select
from app.db.session import SessionLocal
from app.db.models import TestRun, Finding
from app.services import research_verification as verify
from tests.research_verification_fixtures import (verification_graph,intent_graph,subject_pair,two_intake_targets,
    original_targets,owned_server,subject_encryption,bootstrap,plan_approve,send,call,REF)


def test_real_health_baseline_probe_and_exact_pair(verification_graph):
    g=verification_graph
    health,receipt,business=bootstrap(g)
    plan_approve(g,business,'baseline');plan_approve(g,business,'probe')
    baseline=send(g,business,'baseline');probe=send(g,business,'probe')
    evidence=call(verify.verify_pair,g,dict(intent=business['reference'],baseline=baseline['reference'],probe=probe['reference']))
    assert evidence['body']['outcome']=='suspected_violation'
    assert evidence['finding_confirmed'] is False
    assert g['server']['requests']==[('/folders/80814',True),('/folders/7001',False),('/folders/7001',True)]
    with SessionLocal() as db:
        for view in (receipt,baseline,probe):
            run=db.get(TestRun,view['body']['run_id'])
            assert run.response_body is None
            assert 'fixture_actor' not in str(run.request_data)
        assert db.scalar(select(Finding.id).where(Finding.test_run_id==probe['body']['run_id'])) is None
    assert send(g,business,'probe')['reference']==probe['reference']
    assert len(g['server']['requests'])==3

from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock
import pytest
from pydantic import SecretStr
from sqlalchemy import func,update
from app.schemas import research_intent as c
from app.schemas.research_subject import SubjectError
from app.services import research_intent as intent,research_observation as observation,research_subject as subject
from app.services.research_verification_dispatch import Dispatch,context_permit
from app.db.session import engine
from app.db.models.research_verification import VerificationWitness,VerificationAttempt,VerificationPair,VerificationContract,VerificationAudit
from app.db.models.execution_plan_progress import ExecutionPlanProgress
from app.db.models import Scope,TestIdentity
from app.credentials.bearer import BearerCredentialService
from app.executors.http import ExecutionBlockedError
from tests.research_verification_fixtures import confirm,conversion,NOW,proposal,approve
from tests.research_intake_fixtures import snapshot


def prepared_health(g):
    confirm(g);value=call(intent.convert,g,1,conversion(g,purpose='health_probe'))
    plan_approve(g,value,'health');return value


def sourced_manifest(g):
    from tests.research_observation_fixtures import preparation,observation as raw
    from app.schemas.research_observation import canonical
    p=preparation(g);p.update(preparation_ref='preparation_2',path_templates=['/folders/{project_id}'])
    call(observation.prepare,g,p)
    value=raw(port=g['server']['port']);value.update(batch_ref='batch_2',preparation_ref='preparation_2')
    value['entries'][0].update(path_template='/folders/{project_id}',entry_ref='entry_2')
    oid=call(observation.accept,g,'preparation_2',canonical(value))['observation_id']
    p=proposal(g);p['sources']=[dict(observation_id=oid,source_entry_index=0)]
    call(subject.record,g,1,dict(expected_version=1,correction_reference=REF,proposal=p),correction=True)
    p=deepcopy(g['manifest_input']);p['expected_version']=g['manifest']['version'];p['actions'][0]['subject']['version']=2
    g['manifest_input']=p;g['manifest']=call(intent.record_manifest,g,1,p)['reference'];approve(g)
    return oid


def mutate(g,kind,value,oid=None):
    if kind=='credential':
        with SessionLocal() as db:
            BearerCredentialService(db=db).update(identity_id=g['bearer'],token=SecretStr(g['server']['token']));db.commit()
    elif kind in ('identity','scope'):
        with SessionLocal() as db:
            if kind=='identity':db.get(TestIdentity,g['bearer']).is_active=False
            else:db.add(Scope(target_id=g['target'],hostname='127.0.0.1',path_pattern='/other/*',allowed_methods=['GET']))
            db.commit()
    elif kind=='source':
        call(observation.lifecycle,g,oid,'hold',dict(reason='synthetic_review',until=(NOW+timedelta(seconds=20)).isoformat(),review=REF))
        call(observation.lifecycle,g,oid,'release',dict(review=REF))
    elif kind=='approval':
        member=value['body']['link']['members'][0]
        call(verify.approve,g,dict(intent=value['reference'],plan_id=member['plan_id'],expected_sequence=1,decision='revoked',evidence=REF))
    elif kind=='budget':call(intent.decide_budget,g,dict(manifest=g['manifest'],expected_sequence=1,decision='revoked',evidence=REF))
    elif kind=='cancellation':
        from app.services.execution_plan_cancellation import ExecutionPlanCancellationService
        ExecutionPlanCancellationService(bind=engine).request_cancel(value['body']['link']['members'][0]['plan_id'])
    elif kind=='facts':
        from tests.research_subject_fixtures import assertion
        assertion(g,access='denied')
    else:raise AssertionError(kind)


@pytest.mark.parametrize('kind',['credential','identity','scope','source','approval','budget','cancellation','facts'])
@pytest.mark.parametrize('stage',['rate','connected'])
def test_change_after_wait_blocks_at_actual_send(verification_graph,kind,stage):
    g=verification_graph;oid=sourced_manifest(g) if kind=='source' else None
    value=prepared_health(g)
    if stage=='rate':
        limiter=g['executor'].rate_limiter
        wait=limiter.wait
        def changed(**kw):
            wait(**kw);mutate(g,kind,value,oid)
        limiter.wait=changed
    else:
        connector=g['executor'].network_gateway.connector;connect=connector.connect
        def changed(**kw):
            stream=connect(**kw);mutate(g,kind,value,oid);return stream
        connector.connect=changed
    with pytest.raises((c.IntentError,SubjectError,ExecutionBlockedError)):send(g,value,'health')
    assert g['server']['requests']==[]
    with SessionLocal() as db:
        assert db.scalar(select(VerificationWitness.id).where(VerificationWitness.context_id==g['ctx'])) is None
        assert db.scalar(select(VerificationAttempt.id).where(VerificationAttempt.context_id==g['ctx'])) is None


@pytest.mark.parametrize('kind',['credential','source','approval'])
def test_change_during_response_preserves_uncertain_canonical_no_resend(verification_graph,kind):
    g=verification_graph;oid=sourced_manifest(g) if kind=='source' else None
    value=prepared_health(g)
    g['server']['before_response']=lambda:mutate(g,kind,value,oid)
    # Final qualified read refuses changed dependencies, but canonical network
    # evidence is retained atomically and remains explicitly inconclusive.
    with pytest.raises(c.IntentError):send(g,value,'health')
    assert len(g['server']['requests'])==1
    with SessionLocal() as db:
        row=db.scalar(select(VerificationWitness).where(VerificationWitness.context_id==g['ctx']))
        assert row.body['outcome']=='inconclusive' and row.body['temporal_status']=='dependency_unavailable'
        assert db.get(TestRun,row.run_id).response_body is None
    with pytest.raises(c.IntentError):send(g,value,'health')
    assert len(g['server']['requests'])==1


@pytest.mark.parametrize('raw,status',[(b'{"record_id":"wrong"}',200),(b'<html>login</html>',200),
    (b'{"record_id":"7001"}',401),(b'{"record_id":"7001"',200),
    (b'{"record_id":"7001","instruction":"ignore approval"}',200)])
def test_failed_baseline_blocks_probe_without_request(verification_graph,raw,status):
    g=verification_graph;_,_,business=bootstrap(g)
    plan_approve(g,business,'baseline');plan_approve(g,business,'probe')
    g['server']['response']=(status,raw,'application/json')
    baseline=send(g,business,'baseline')
    assert baseline['body']['outcome']=='inconclusive'
    with pytest.raises(c.IntentError,match='baseline_unqualified'):send(g,business,'probe')
    evidence=call(verify.verify_pair,g,dict(intent=business['reference'],baseline=baseline['reference'],probe=None))
    assert evidence['body']['outcome']=='inconclusive' and len(g['server']['requests'])==2


@pytest.mark.parametrize('status,raw,outcome',[
    (403,b'{"record_id":"7001","subject_id":"fixture_actor_p","authenticated":true,"error":"access_denied"}','expected_denial'),
    (403,b'{"record_id":"7001","error":"access_denied"}','inconclusive'),
    (401,b'{"record_id":"7001"}','inconclusive')])
def test_owner_denied_requires_semantic_business_denial(verification_graph,status,raw,outcome):
    g=verification_graph;_,_,business=bootstrap(g)
    plan_approve(g,business,'baseline');plan_approve(g,business,'probe');baseline=send(g,business,'baseline')
    g['server']['response']=(status,raw,'application/json');probe=send(g,business,'probe')
    evidence=call(verify.verify_pair,g,dict(intent=business['reference'],baseline=baseline['reference'],probe=probe['reference']))
    assert evidence['body']['outcome']==outcome and evidence['finding_confirmed'] is False


@pytest.mark.parametrize('bad',['missing','digest','foreign','health_as_baseline','reverse'])
def test_exact_references_no_substitution(verification_graph,bad):
    g=verification_graph;_,health,business=bootstrap(g)
    plan_approve(g,business,'baseline');baseline=send(g,business,'baseline')
    ref=deepcopy(baseline['reference'])
    if bad=='missing':ref['id']+=100000
    elif bad=='digest':ref['digest']='f'*64
    elif bad=='foreign':ref=health['reference'];ref={**ref,'id':ref['id']+99999}
    elif bad=='health_as_baseline':ref=health['reference']
    before=snapshot()
    with pytest.raises(c.IntentError):call(verify.verify_pair,g,dict(intent=business['reference'],baseline=ref,probe=baseline['reference'] if bad=='reverse' else None))
    assert snapshot()==before


@pytest.mark.parametrize('offset,ok',[(-1,True),(0,False),(1,False)])
@pytest.mark.parametrize('stage',['service','encode'])
def test_pair_final_boundary_rolls_back(verification_graph,monkeypatch,offset,ok,stage):
    g=verification_graph;_,_,business=bootstrap(g)
    plan_approve(g,business,'baseline');plan_approve(g,business,'probe')
    baseline=send(g,business,'baseline');probe=send(g,business,'probe');before=snapshot();clock=[NOW]
    if stage=='service':
        real=verify.audit
        def changed(db,ctx,code,source):
            result=real(db,ctx,code,source)
            if code=='pair':clock[0]=NOW+timedelta(seconds=30,microseconds=offset)
            return result
        monkeypatch.setattr(verify,'audit',changed)
    else:
        real=verify.output
        def changed(value):
            result=real(value)
            if value['kind']=='pair':clock[0]=NOW+timedelta(seconds=30,microseconds=offset)
            return result
        monkeypatch.setattr(verify,'output',changed)
    with SessionLocal() as db:
        p=dict(intent=business['reference'],baseline=baseline['reference'],probe=probe['reference'])
        if ok:verify.verify_pair(db,g['project'],g['ctx'],p,now=lambda:clock[0])
        else:
            with pytest.raises(c.IntentError,match='expired'):verify.verify_pair(db,g['project'],g['ctx'],p,now=lambda:clock[0])
        db.commit()
    if not ok:
        from tests.services.test_research_verification_expiry import unchanged_except_fence, faults
        unchanged_except_fence(before)
        assert faults(g)=={business['reference']['digest']}


@pytest.mark.parametrize('stage',['dispatch_audit','witness_audit','response_encode'])
def test_atomic_failure_and_m8_honest_no_resend(verification_graph,monkeypatch,stage):
    g=verification_graph;value=prepared_health(g);real=verify.audit
    def failed(db,ctx,code,clock):
        if code=={'dispatch_audit':'dispatch','witness_audit':'witness','response_encode':'never'}[stage]:raise RuntimeError('owned audit fault')
        return real(db,ctx,code,clock)
    monkeypatch.setattr(verify,'audit',failed)
    def encode(*args):raise RuntimeError('owned serialization fault')
    with pytest.raises((RuntimeError,c.IntentError,ExecutionBlockedError)):
        send(g,value,'health',**({'encode':encode} if stage=='response_encode' else {}))
    count=0 if stage=='dispatch_audit' else 1
    assert len(g['server']['requests'])==count
    with SessionLocal() as db:
        witness=db.scalar(select(VerificationWitness).where(VerificationWitness.context_id==g['ctx']))
        assert (witness is not None)==(stage=='response_encode')
        if stage!='response_encode':
            progress=db.get(ExecutionPlanProgress,value['body']['link']['members'][0]['plan_id'])
            assert progress.phase=='network_started'
            assert db.scalar(select(TestRun.id).where(TestRun.execution_plan_id==progress.execution_plan_id)) is None
    if stage=='response_encode':send(g,value,'health')
    else:
        with pytest.raises(ExecutionBlockedError,match='in_doubt'):send(g,value,'health')
        with SessionLocal() as db:
            assert db.get(ExecutionPlanProgress,value['body']['link']['members'][0]['plan_id']).phase=='network_started'
    assert len(g['server']['requests'])==count


def test_context_permit_rejects_concurrent_dispatch_without_network(verification_graph):
    g=verification_graph;value=prepared_health(g)
    with context_permit(engine,g['ctx']):
        with pytest.raises(c.IntentError,match='busy'):send(g,value,'health')
    assert g['server']['requests']==[]


@pytest.mark.parametrize('offset,ok',[(-1,True),(0,False),(1,False)])
def test_real_health_evidence_120_boundary(verification_graph,offset,ok):
    g=verification_graph;_,_,business=bootstrap(g)
    at=NOW+timedelta(seconds=120,microseconds=offset)
    if ok:assert call(intent.read,g,'intent',business['reference'],now=at)
    else:
        with pytest.raises(c.IntentError,match='expired'):call(intent.read,g,'intent',business['reference'],now=at)
    assert len(g['server']['requests'])==1


@pytest.mark.parametrize('offset,ok',[(-1,True),(0,False),(1,False)])
def test_probe_dispatch_gate_exact_30_boundary(verification_graph,offset,ok):
    g=verification_graph;_,_,business=bootstrap(g)
    plan_approve(g,business,'baseline');plan_approve(g,business,'probe');send(g,business,'baseline')
    at=NOW+timedelta(seconds=30,microseconds=offset)
    if ok:
        # A one-microsecond remaining window permits qualification; it cannot
        # promise a real socket exchange will complete inside that window.
        pid=next(m['plan_id'] for m in business['body']['link']['members'] if m['role']=='probe')
        gate=Dispatch(bind=engine,project=g['project'],context_id=g['ctx'],reference=business['reference'],plan_id=pid,executor=g['executor'],clock=at)
        with SessionLocal() as db:
            gate.validate(db);assert gate.deadline==NOW+timedelta(seconds=30)
            db.rollback()
    else:
        with pytest.raises(c.IntentError,match='expired'):send(g,business,'probe',now=at)
    assert len(g['server']['requests'])==2


def test_slow_response_and_late_health_completion_are_uncertain(verification_graph):
    import time
    g=verification_graph;value=prepared_health(g)
    g['server']['before_response']=lambda:time.sleep(5.2)
    response=send(g,value,'health')
    assert response['body']['outcome']=='inconclusive'
    assert response['body']['temporal_status']=='network_incomplete'
    assert len(g['server']['requests'])==1
    with pytest.raises(c.IntentError,match='health_unqualified'):
        call(verify.select_health,g,dict(manifest=g['manifest'],health=[dict(role='probe',evidence=response['reference'])],evidence=REF))
    assert send(g,value,'health')['reference']==response['reference']
    assert len(g['server']['requests'])==1


def test_health_expiry_during_body_read_never_persists_qualified_evidence(verification_graph):
    g=verification_graph;value=prepared_health(g);at=[NOW]
    g['server']['before_response']=lambda:at.__setitem__(0,NOW+timedelta(seconds=120))
    with pytest.raises(c.IntentError,match='clock_invalidated'):send(g,value,'health',now=lambda:at[0])
    with SessionLocal() as db:
        row=db.scalar(select(VerificationWitness).where(VerificationWitness.context_id==g['ctx']))
        assert row.body['outcome']=='inconclusive' and row.body['temporal_status']=='clock_or_deadline_invalid'
        view,end=verify.history(db,g['project'],g['ctx'],verify.ref(row),now=at[0])
        assert view['reusable'] is False and view['body']==row.body
        db.commit()
    at[0]=NOW+timedelta(seconds=1)
    with pytest.raises(c.IntentError,match='clock_invalidated'):
        send(g,value,'health',now=lambda:at[0])
    assert len(g['server']['requests'])==1


@pytest.mark.parametrize('relationship',['shared','non_owner'])
def test_independent_allowed_probe_not_a_violation(verification_graph,relationship):
    # New Resource/facts and fresh subjects/mapping/manifest, retaining the old
    # owner+denied fixture unchanged. Labels never rewrite the access oracle.
    from app.db.models import Resource
    from app.db.models.resource_access_assertion import ResourceAccessAssertion
    from tests.research_verification_fixtures import mapping
    g=verification_graph
    with SessionLocal() as db:
        resource=Resource(target_id=g['target'],resource_type='project',external_id='7002',owner_identity_id=g['anonymous'])
        db.add(resource);db.flush();rid=resource.id;g['extra_resources'].append(rid)
        for actor,rel in ((g['anonymous'],'owner'),(g['bearer'],relationship)):
            db.add(ResourceAccessAssertion(resource_id=rid,test_identity_id=actor,relationship=rel,expected_access='allowed',
                provenance='target_fixture',verification_state='verified',confidence=80,asserted_at=NOW-timedelta(seconds=1)))
        db.commit()
    for num,bearer in ((4,False),(5,True)):
        p=proposal(g);p['resource_id']=rid
        if bearer:p.update(identity_choice='bearer',test_identity_id=g['bearer'],credential_binding_id=g['credential'],session_state='unknown',credential_update='unknown')
        call(subject.record,g,num,p)
    p=mapping(g);p['resource_id']=rid;ref=call(intent.confirm_mapping,g,3,p)['reference']
    p=deepcopy(g['manifest_input']);p['expected_version']=g['manifest']['version']
    for action,num in zip(p['actions'][:2],(4,5)):action.update(subject=dict(number=num,version=1),mapping=ref)
    g['manifest_input']=p;g['manifest']=call(intent.record_manifest,g,1,p)['reference'];approve(g)
    g['business_object']='7002'
    _,_,business=bootstrap(g);plan_approve(g,business,'baseline');plan_approve(g,business,'probe')
    baseline=send(g,business,'baseline');probe=send(g,business,'probe')
    pair=call(verify.verify_pair,g,dict(intent=business['reference'],baseline=baseline['reference'],probe=probe['reference']))
    assert pair['body']['outcome']=='allowed' and pair['finding_confirmed'] is False
    assert business['body']['snapshot']['actions'][1]['actor']['facts']['relationship']==relationship


def test_explicit_anonymous_probe_with_qualified_bearer_baseline(verification_graph):
    from app.db.models import Resource
    from app.db.models.resource_access_assertion import ResourceAccessAssertion
    from tests.research_verification_fixtures import mapping
    g=verification_graph
    with SessionLocal() as db:
        resource=Resource(target_id=g['target'],resource_type='project',external_id='7003',owner_identity_id=g['bearer'])
        db.add(resource);db.flush();rid=resource.id;g['extra_resources'].append(rid)
        for actor,rel,access in ((g['bearer'],'owner','allowed'),(g['anonymous'],'non_owner','denied')):
            db.add(ResourceAccessAssertion(resource_id=rid,test_identity_id=actor,relationship=rel,expected_access=access,
                provenance='target_fixture',verification_state='verified',confidence=80,asserted_at=NOW-timedelta(seconds=1)))
        db.commit()
    for num,bearer in ((4,True),(5,False)):
        p=proposal(g);p['resource_id']=rid
        if bearer:p.update(identity_choice='bearer',test_identity_id=g['bearer'],credential_binding_id=g['credential'],session_state='unknown',credential_update='unknown')
        call(subject.record,g,num,p)
    p=mapping(g);p['resource_id']=rid;ref=call(intent.confirm_mapping,g,3,p)['reference']
    p=deepcopy(g['manifest_input']);p['expected_version']=g['manifest']['version']
    for action,num in zip(p['actions'][:2],(4,5)):action.update(subject=dict(number=num,version=1),mapping=ref)
    p['actions'][2]['role']='health_baseline'
    g.update(business_object='7003',health_purpose='health_baseline',health_role='baseline',bearer_roles={'baseline','health_baseline'})
    g['manifest_input']=p;g['manifest']=call(intent.record_manifest,g,1,p)['reference'];approve(g)
    _,_,business=bootstrap(g);plan_approve(g,business,'baseline');plan_approve(g,business,'probe')
    baseline=send(g,business,'baseline')
    g['server']['response']=(403,b'{"record_id":"7003","error":"access_denied"}','application/json')
    probe=send(g,business,'probe')
    pair=call(verify.verify_pair,g,dict(intent=business['reference'],baseline=baseline['reference'],probe=probe['reference']))
    assert pair['body']['outcome']=='expected_denial'
    assert g['server']['requests']==[('/folders/80814',True),('/folders/7003',True),('/folders/7003',False)]
    assert probe['body']['credential_version_id'] is None


@pytest.mark.parametrize('change',['network_stop','fence'])
def test_connected_wait_still_obeys_network_stop_and_fencing(verification_graph,change):
    from sqlalchemy import text
    from app.services.execution_plan_claim import ExecutionPlanClaimService
    g=verification_graph;value=prepared_health(g);pid=value['body']['link']['members'][0]['plan_id']
    gateway=g['executor'].network_gateway;connect=gateway.connector.connect
    def changed(**kwargs):
        stream=connect(**kwargs)
        if change=='network_stop':gateway.controller.disable_target(g['target'])
        else:
            with engine.begin() as db:db.execute(text("UPDATE execution_plan_claims SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE execution_plan_id=:id"),{'id':pid})
            ExecutionPlanClaimService(bind=engine).acquire(pid,'owned-successor',lease_seconds=60)
        return stream
    gateway.connector.connect=changed
    try:
        with pytest.raises(ExecutionBlockedError):send(g,value,'health')
        assert g['server']['requests']==[]
        with SessionLocal() as db:assert db.scalar(select(VerificationWitness.id).where(VerificationWitness.context_id==g['ctx'])) is None
    finally:
        if change=='network_stop':gateway.controller.enable_target(g['target'])


def test_exact_audit_capacity_rolls_back_approval(verification_graph):
    from sqlalchemy import text
    from app.db.models.execution_plan_approval_record import ExecutionPlanApprovalRecord
    g=verification_graph;value=prepared_health(g);pid=value['body']['link']['members'][0]['plan_id']
    with engine.begin() as db:
        count=db.scalar(select(func.count()).select_from(VerificationAudit).where(VerificationAudit.context_id==g['ctx']))
        db.execute(text("INSERT INTO research_verification_audit (context_id,code,recorded_at) SELECT :ctx,'read',:at FROM generate_series(1,:count)"),{'ctx':g['ctx'],'at':NOW,'count':4095-count})
    p=dict(intent=value['reference'],plan_id=pid,expected_sequence=1,decision='approved',evidence=REF)
    call(verify.approve,g,p)  # Entry 4096 is allowed.
    before=snapshot();p.update(expected_sequence=2,decision='revoked')
    with pytest.raises(c.IntentError,match='storage_limit'):call(verify.approve,g,p)
    assert snapshot()==before and g['server']['requests']==[]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ExecutionPlanApprovalRecord).where(ExecutionPlanApprovalRecord.execution_plan_id==pid))==2


from tests.research_intent_fixtures import qualified_future


def test_w1_controlled_envelope_cannot_open_real_dispatch(verification_graph,qualified_future,monkeypatch):
    g=verification_graph;value=call(intent.convert,g,1,conversion(g))
    blocked=Mock(side_effect=AssertionError('must not resolve a credential'))
    monkeypatch.setattr(BearerCredentialService,'resolve_exact',blocked)
    with pytest.raises(c.IntentError,match='w2_evidence_unavailable'):send(g,value,'baseline')
    blocked.assert_not_called();assert g['server']['requests']==[]


@pytest.mark.parametrize('role',['baseline','probe'])
def test_pair_consumption_rechecks_each_exact_approval(verification_graph,role):
    g=verification_graph;_,_,business=bootstrap(g)
    plan_approve(g,business,'baseline');plan_approve(g,business,'probe')
    baseline=send(g,business,'baseline');probe=send(g,business,'probe')
    pid=next(m['plan_id'] for m in business['body']['link']['members'] if m['role']==role)
    decision=dict(intent=business['reference'],plan_id=pid,expected_sequence=1,decision='revoked',evidence=REF)
    call(verify.approve,g,decision);before=snapshot()
    payload=dict(intent=business['reference'],baseline=baseline['reference'],probe=probe['reference'])
    with pytest.raises(c.IntentError,match='approval_required'):call(verify.verify_pair,g,payload)
    assert snapshot()==before
    call(verify.approve,g,{**decision,'expected_sequence':2,'decision':'approved'})
    pair=call(verify.verify_pair,g,payload)
    assert pair['body']['outcome']=='suspected_violation' and len(g['server']['requests'])==3


@pytest.mark.parametrize('offset,ok',[(-1,True),(0,False),(1,False)])
def test_recorded_baseline_narrows_intent_and_approval_views(verification_graph,monkeypatch,offset,ok):
    from app.services import research_verification_clock as vc
    g=verification_graph;_,_,business=bootstrap(g);plan_approve(g,business,'baseline')
    baseline=send(g,business,'baseline')
    at=NOW+timedelta(seconds=30,microseconds=offset)
    # Both trusted clocks stay at this exact consumption instant. A real clock
    # would spend the remaining microsecond while encoding the approval view.
    ns=baseline['body']['complete']['monotonic_ns']+30_000_000_000+offset*1000
    monkeypatch.setattr(vc,'monotonic_ns',lambda:ns)
    pid=next(m['plan_id'] for m in business['body']['link']['members'] if m['role']=='probe')
    p=dict(intent=business['reference'],plan_id=pid,expected_sequence=0,decision='approved',evidence=REF)
    before=snapshot()
    for fn,args in ((intent.read,('intent',business['reference'])),(verify.approve,(p,))):
        if ok:
            result=call(fn,g,*args,now=at)
            assert result['eligibility_until']==c.stamp(NOW+timedelta(seconds=30))
        else:
            from tests.services.test_research_verification_expiry import unchanged_except_fence, faults
            code='expired' if fn is intent.read else 'clock_invalidated'
            with pytest.raises(c.IntentError,match=code):call(fn,g,*args,now=at)
            unchanged_except_fence(before)
            assert faults(g)=={business['reference']['digest']}
    assert len(g['server']['requests'])==2


def test_old_definition_is_historical_only(verification_graph,monkeypatch):
    from app.services import research_response_semantics as semantics
    g=verification_graph;value=prepared_health(g);response=send(g,value,'health')
    monkeypatch.setattr(semantics,'DEFINITION_DIGEST','f'*64)
    with pytest.raises(c.IntentError):call(verify.read_execution,g,response['reference'])
    view,end=call(verify.history,g,response['reference'])
    assert view['body']==response['body'] and view['reusable'] is False
    assert len(g['server']['requests'])==1


def test_detected_clock_rollback_durably_fences_old_intent_before_any_send(verification_graph):
    from app.db.models.research_verification import VerificationClockFault
    g=verification_graph;value=prepared_health(g);calls=[0]
    def reversed_clock():
        calls[0]+=1
        return NOW if calls[0]<3 else NOW-timedelta(seconds=1)
    with pytest.raises(c.IntentError,match='clock'):send(g,value,'health',now=reversed_clock)
    pid=value['body']['link']['members'][0]['plan_id']
    with SessionLocal() as db:
        assert db.get(ExecutionPlanProgress,pid) is None  # Before M8/credential/network work.
        faults=list(db.scalars(select(VerificationClockFault).where(VerificationClockFault.context_id==g['ctx'])))
        assert len(faults)==1 and faults[0].digest==value['reference']['digest']
    before=snapshot()
    for action in (lambda:send(g,value,'health',now=NOW),
                   lambda:call(intent.read,g,'intent',value['reference'],now=NOW)):
        with pytest.raises(c.IntentError,match='clock_invalidated'):action()
    assert snapshot()==before and g['server']['requests']==[]
    # Recovery requires fresh manifest/contract/intent and explicit decisions;
    # the old fence is never deleted or rewritten.
    p=deepcopy(g['manifest_input']);p['expected_version']=0
    g['manifest']=call(intent.record_manifest,g,2,p)['reference'];approve(g)
    expectations=[dict(role=a['role'],object_key='record_id',object_value='80814' if a['role']=='health_probe' else '7001',
        identity_key='subject_id',identity_value=None if a['role']=='baseline' else 'fixture_actor_p') for a in p['actions']]
    call(verify.confirm,g,2,dict(manifest=g['manifest'],expected_version=0,decision='confirm',expectations=expectations,evidence=REF))
    fresh=call(intent.convert,g,2,conversion(g,purpose='health_probe'));plan_approve(g,fresh,'health')
    assert send(g,fresh,'health')['body']['outcome']=='healthy' and len(g['server']['requests'])==1


def test_pair_clock_fault_preserves_history_and_never_revives(verification_graph,monkeypatch):
    from app.db.models.research_verification import VerificationClockFault
    g=verification_graph;_,_,business=bootstrap(g);plan_approve(g,business,'baseline');plan_approve(g,business,'probe')
    baseline=send(g,business,'baseline');probe=send(g,business,'probe');before=snapshot();at=[NOW]
    real=verify.output
    def rollback_clock(value):
        raw=real(value)
        if value['kind']=='pair':at[0]=NOW-timedelta(seconds=1)
        return raw
    monkeypatch.setattr(verify,'output',rollback_clock)
    with pytest.raises(c.IntentError,match='clock'):
        call(verify.verify_pair,g,dict(intent=business['reference'],baseline=baseline['reference'],probe=probe['reference']),now=lambda:at[0])
    after=snapshot()
    assert {k:v for k,v in after.items() if k!='research_verification_clock_faults'}=={k:v for k,v in before.items() if k!='research_verification_clock_faults'}
    assert after['research_verification_clock_faults']
    with pytest.raises(c.IntentError,match='clock_invalidated'):call(intent.read,g,'intent',business['reference'],now=NOW)
    view,end=call(verify.history,g,baseline['reference'])
    assert view['body']==baseline['body'] and view['reusable'] is False and len(g['server']['requests'])==3


@pytest.mark.parametrize('stage',['before','connected'])
def test_probe_requires_current_baseline_decision_after_wait(verification_graph,stage):
    g=verification_graph;_,_,business=bootstrap(g)
    plan_approve(g,business,'baseline');plan_approve(g,business,'probe')
    send(g,business,'baseline')
    assert len(g['server']['requests'])==2
    if stage=='before':mutate(g,'approval',business)
    else:
        connector=g['executor'].network_gateway.connector;connect=connector.connect
        def changed(**kw):
            stream=connect(**kw);mutate(g,'approval',business);return stream
        connector.connect=changed
    error=c.IntentError if stage=='before' else ExecutionBlockedError
    reason='verification_approval_required' if stage=='before' else 'execution_plan_result_persistence_failed'
    with pytest.raises(error,match=reason):
        send(g,business,'probe')
    assert len(g['server']['requests'])==2
    with SessionLocal() as db:
        pid=next(m['plan_id'] for m in business['body']['link']['members'] if m['role']=='probe')
        progress=db.get(ExecutionPlanProgress,pid)
        if stage=='connected':assert progress.phase=='network_started'
        else:assert progress is None
        assert db.scalar(select(VerificationWitness.id).where(
            VerificationWitness.context_id==g['ctx'],
            VerificationWitness.body['role'].astext=='probe')) is None


def test_uncertain_socket_write_cannot_reuse_plan_or_manifest_budget(verification_graph):
    from threading import Event
    g=verification_graph;value=prepared_health(g);received=Event()
    g['server']['before_response']=received.set
    connector=g['executor'].network_gateway.connector;connect=connector.connect
    def failing_connection(**kw):
        stream=connect(**kw);write=stream.write
        def write_then_fail(buffer,timeout=None):
            write(buffer,timeout=timeout)
            assert received.wait(3), 'owned server did not receive the GET'
            raise RuntimeError('synthetic socket write failure after transmission')
        stream.write=write_then_fail
        return stream
    connector.connect=failing_connection
    with pytest.raises(ExecutionBlockedError,match='execution_plan_result_persistence_failed'):
        send(g,value,'health')
    assert len(g['server']['requests'])==1
    pid=value['body']['link']['members'][0]['plan_id']
    with SessionLocal() as db:
        assert db.get(ExecutionPlanProgress,pid).phase=='network_started'
        assert db.scalar(select(VerificationAttempt.id).where(VerificationAttempt.plan_id==pid)) is None
        assert db.scalar(select(VerificationWitness.id).where(VerificationWitness.plan_id==pid)) is None
    connector.connect=connect
    with pytest.raises(c.IntentError,match='intent_manifest_consumed'):
        call(intent.convert,g,2,conversion(g,purpose='health_probe'))
    with pytest.raises(ExecutionBlockedError,match='execution_plan_in_doubt'):
        send(g,value,'health')
    assert len(g['server']['requests'])==1
