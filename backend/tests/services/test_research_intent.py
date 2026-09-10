from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock
import pytest
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from app.db.session import SessionLocal
from app.db.models import TestCase, ExecutionPlan, PlanAction, TestIdentity, Resource, Scope
from app.db.models.research_intent import IntentMapping,IntentVersion,IntentPlanMember,IntentAudit,IntentManifest
from app.db.models.credential_secret_version import CredentialSecretVersion
from app.db.models.resource_access_assertion import ResourceAccessAssertion
from app.services import research_intent as service
from app.schemas import research_intent as s
from tests.research_intent_fixtures import (intent_graph,subject_pair,two_intake_targets,qualified_future,
    future_proof,call,approve,conversion,mapping,assertion,NOW,REF)  # noqa: F401
from tests.research_intake_fixtures import snapshot


def test_budget_then_missing_w2_never_leaves_partial_plans(intent_graph):
    g=intent_graph;before=snapshot()
    with pytest.raises(s.IntentError,match='budget_unapproved'):call(service.convert,g,1,conversion(g))
    assert snapshot()==before
    approve(g);before=snapshot()
    with pytest.raises(s.IntentError,match='w2_evidence_unavailable'):call(service.convert,g,1,conversion(g))
    assert snapshot()==before


def test_exact_positive_conversion_is_two_independent_plans(intent_graph,qualified_future):
    g=intent_graph;approve(g)
    value=call(service.convert,g,1,conversion(g));core={k:v for k,v in value['body'].items() if k not in ('link','link_digest')}
    assert core['request_count']==2 and core['snapshot']['actions'][1]['actor']['facts']['relationship']=='owner'
    assert core['snapshot']['actions'][1]['actor']['facts']['expected_access']=='denied'
    assert s.digest('ra-intent/1',core)==value['reference']['digest']
    link=value['body']['link'];assert s.digest('ra-plan-link/1',link)==value['body']['link_digest']
    assert [m['role'] for m in link['members']]==['baseline','probe']
    assert len({m['plan_id'] for m in link['members']})==2
    with SessionLocal() as db:
        for member in link['members']:
            plan=db.get(ExecutionPlan,member['plan_id']);case=db.get(TestCase,member['test_case_id'])
            assert plan.action_count==1 and len(plan.actions)==1 and plan.actions[0].method=='GET'
            assert plan.authorization_revision_id==g['revision'] and plan.plan_digest==member['plan_digest']
            assert case.test_type=='ra_intent_get_v1' and case.ownership_relation=='unspecified' and case.expected_statuses==[]
    again=call(service.read,g,'intent',value['reference']);assert again['body']==value['body']
    with pytest.raises(s.IntentError,match='manifest_consumed'):call(service.convert,g,2,conversion(g))


@pytest.mark.parametrize('change',['identity','resource','scope','credential','fact','mapping','budget'])
def test_change_invalidates_without_substitution(intent_graph,qualified_future,change):
    g=intent_graph;approve(g);result=call(service.convert,g,1,conversion(g))
    with SessionLocal() as db:
        if change=='identity':db.get(TestIdentity,g['bearer']).is_active=False
        if change=='resource':db.get(Resource,g['resource']).external_id='99991'
        if change=='scope':db.get(Scope,g['scope']).is_active=False
        if change=='credential':db.add(CredentialSecretVersion(credential_binding_id=g['credential'],encrypted_envelope='STILL_NOT_A_SECRET',envelope_version=1,key_version='metadata-only'))
        db.commit()
    if change=='fact':assertion(g,access='denied')
    if change=='mapping':
        p=mapping(g);p.update(expected_version=1,decision='withdraw');call(service.confirm_mapping,g,1,p)
    if change=='budget':call(service.decide_budget,g,dict(manifest=g['manifest'],expected_sequence=1,decision='revoked',evidence=REF))
    before=snapshot()
    with pytest.raises(Exception):call(service.read,g,'intent',result['reference'])
    assert snapshot()==before


@pytest.mark.parametrize('offset,allowed',[(-1,True),(0,False),(1,False)])
@pytest.mark.parametrize('stage',['audit','encode'])
def test_final_boundary_rollback_even_if_caller_commits(intent_graph,qualified_future,monkeypatch,offset,allowed,stage):
    g=intent_graph;approve(g);before=snapshot();clock=[NOW]
    if stage=='audit':
        real=service._audit
        def boundary(*args):
            result=real(*args)
            if args[2]=='intent':clock[0]=NOW+timedelta(seconds=60,microseconds=offset)
            return result
        monkeypatch.setattr(service,'_audit',boundary)
    else:
        real=s.Receipt.model_dump
        def boundary(self,*args,**kwargs):
            result=real(self,*args,**kwargs)
            if self.kind=='intent':clock[0]=NOW+timedelta(seconds=60,microseconds=offset)
            return result
        monkeypatch.setattr(s.Receipt,'model_dump',boundary)
    with SessionLocal() as db:
        if allowed:service.convert(db,g['project'],g['ctx'],1,conversion(g),now=lambda:clock[0])
        else:
            with pytest.raises(s.IntentError,match='expired'):service.convert(db,g['project'],g['ctx'],1,conversion(g),now=lambda:clock[0])
        db.commit()
    if not allowed:assert snapshot()==before


@pytest.mark.parametrize('field',['asserted_at','valid_from'])
def test_future_conflict_deadline_is_retained(intent_graph,qualified_future,monkeypatch,field):
    g=intent_graph
    # A new manifest incorporates the currently future fact into its dependency set.
    kwargs={field:NOW+timedelta(seconds=1)}
    if field=='asserted_at':
        with SessionLocal() as db:
            db.add(ResourceAccessAssertion(resource_id=g['resource'],test_identity_id=g['anonymous'],relationship='non_owner',expected_access='denied',provenance='target_fixture',confidence=80,verification_state='verified',asserted_at=kwargs[field]));db.commit()
    else:assertion(g,access='denied',**kwargs)
    p=deepcopy(g['manifest_input']);p['expected_version']=1
    g['manifest']=call(service.record_manifest,g,1,p)['reference'];approve(g)
    clock=[NOW];real=service._audit
    def audit(*args):
        result=real(*args)
        if args[2]=='intent':clock[0]=NOW+timedelta(seconds=1)
        return result
    monkeypatch.setattr(service,'_audit',audit);before=snapshot()
    with pytest.raises(s.IntentError,match='expired'):call(service.convert,g,1,conversion(g),now=lambda:clock[0])
    assert snapshot()==before


@pytest.mark.parametrize('field',['context_id','target_id','identity_id','credential_version_id','digest','send_at','valid_until'])
def test_forged_or_stale_future_envelope_rejected(intent_graph,monkeypatch,field):
    g=intent_graph;approve(g)
    def wrong(*args):
        p=future_proof(*args);r=p['health'][0]
        if field=='digest':r[field]='NOT_RUN'
        elif field=='send_at':r[field]=s.stamp(NOW+timedelta(seconds=1))
        elif field=='valid_until':r[field]=s.stamp(NOW)
        else:r[field]+=999
        return p
    monkeypatch.setattr(service,'_interpretation',wrong);before=snapshot()
    with pytest.raises(s.IntentError):call(service.convert,g,1,conversion(g))
    assert snapshot()==before


@pytest.mark.parametrize('stage',['plan2','audit','serialization'])
def test_atomic_failure_preserves_all_legacy_and_new_rows(intent_graph,qualified_future,monkeypatch,stage):
    g=intent_graph;approve(g);before=snapshot()
    if stage=='plan2':
        real=service._create_execution_plan;calls=[]
        def fail(*args,**kwargs):
            calls.append(1)
            if len(calls)==2:raise RuntimeError('controlled persistence failure')
            return real(*args,**kwargs)
        monkeypatch.setattr(service,'_create_execution_plan',fail)
    elif stage=='audit':monkeypatch.setattr(service,'_audit',Mock(side_effect=RuntimeError('audit failure')))
    else:monkeypatch.setattr(s.Receipt,'model_dump',Mock(side_effect=RuntimeError('serialization failure')))
    with SessionLocal() as db:
        with pytest.raises(RuntimeError):service.convert(db,g['project'],g['ctx'],1,conversion(g),now=NOW)
        db.commit()
    assert snapshot()==before


def test_immutable_core_mapping_and_role_constraints(intent_graph,qualified_future):
    g=intent_graph;approve(g);value=call(service.convert,g,1,conversion(g))
    before=snapshot()
    with SessionLocal() as db:
        for model in (IntentMapping,IntentVersion):
            with pytest.raises(Exception):
                with db.begin_nested():db.execute(update(model).where(model.context_id==g['ctx']).values(body={}))
        member=db.scalar(select(IntentPlanMember).join(IntentVersion,IntentVersion.id==IntentPlanMember.intent_id).where(IntentVersion.context_id==g['ctx']))
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(IntentPlanMember(intent_id=member.intent_id,role=member.role,plan_id=member.plan_id,action_id=member.action_id,test_case_id=member.test_case_id));db.flush()
        db.rollback()
    assert snapshot()==before


@pytest.mark.parametrize('size,ok',[(15,True),(16,False)])
def test_exact_budget_decision_limit(intent_graph,size,ok):
    g=intent_graph
    for i in range(size):call(service.decide_budget,g,dict(manifest=g['manifest'],expected_sequence=i,decision='approved' if i%2==0 else 'revoked',evidence=REF))
    before=snapshot()
    payload=dict(manifest=g['manifest'],expected_sequence=size,decision='approved',evidence=REF)
    if ok:assert call(service.decide_budget,g,payload)['body']['sequence']==16
    else:
        with pytest.raises(s.IntentError):call(service.decide_budget,g,payload)
        assert snapshot()==before


@pytest.mark.parametrize('kind',['mapping','manifest','intent','audit'])
def test_storage_limit_fail_closed(intent_graph,qualified_future,monkeypatch,kind):
    # Pin the actual count boundary through the capacity service, avoiding 1024
    # redundant fixture histories. Separate SQL constraints/serialization tests
    # cover individual record limits; no runtime config changes are introduced.
    g=intent_graph;approve(g);before=snapshot();real=service._capacity
    model={'mapping':IntentMapping,'manifest':IntentManifest,'intent':IntentVersion,'audit':IntentAudit}[kind]
    def full(db,m,context,*args):
        if m is model:raise s.IntentError('intent_storage_limit')
        return real(db,m,context,*args)
    monkeypatch.setattr(service,'_capacity',full)
    with pytest.raises(s.IntentError,match='storage_limit'):
        if kind=='mapping':call(service.confirm_mapping,g,2,mapping(g))
        elif kind=='manifest':call(service.record_manifest,g,2,g['manifest_input'])
        else:call(service.convert,g,1,conversion(g))
    assert snapshot()==before


@pytest.mark.parametrize('offset,ok',[(-1,True),(0,False),(1,False)])
def test_health_120_seconds_from_send_not_verification(intent_graph,monkeypatch,offset,ok):
    g=intent_graph;approve(g)
    def proof(*args):
        result=future_proof(*args)
        result['health'][0]['send_at']=s.stamp(NOW-timedelta(seconds=120,microseconds=offset))
        return result
    monkeypatch.setattr(service,'_interpretation',proof)
    before=snapshot()
    if ok:call(service.convert,g,1,conversion(g))
    else:
        with pytest.raises(s.IntentError,match='health_expired'):call(service.convert,g,1,conversion(g))
        assert snapshot()==before


@pytest.fixture
def health_budget_window(monkeypatch):
    # Author a longer intake before context creation; never rewrite a prior version.
    from tests import research_subject_fixtures as fixtures
    original=fixtures.intake
    def bounded_intake(ids):
        value=original(ids);value['budget']['duration_seconds']=300
        return value
    monkeypatch.setattr(fixtures,'intake',bounded_intake)


@pytest.mark.usefixtures('health_budget_window')
@pytest.mark.parametrize("duration",[60,300])
def test_explicit_health_bootstrap_is_one_closed_plan(intent_graph,qualified_future,duration):
    from app.services import research_subject as subject
    from tests.research_subject_fixtures import proposal
    g=intent_graph
    with SessionLocal() as db:
        resource=Resource(target_id=g['target'],resource_type='project',external_id='80814',owner_identity_id=g['bearer'])
        db.add(resource);db.flush();rid=resource.id;g['extra_resources']=[rid]
        db.add(ResourceAccessAssertion(resource_id=rid,test_identity_id=g['bearer'],relationship='owner',expected_access='allowed',provenance='target_fixture',confidence=80,verification_state='verified',asserted_at=NOW-timedelta(seconds=1)));db.commit()
    p=proposal(g);p.update(identity_choice='bearer',test_identity_id=g['bearer'],credential_binding_id=g['credential'],resource_id=rid,session_state='unknown',credential_update='unknown')
    call(subject.record,g,3,p)
    p=mapping(g);p['resource_id']=rid
    ref=call(service.confirm_mapping,g,2,p)['reference']
    p=deepcopy(g['manifest_input']);p.update(expected_version=1,duration_seconds=duration)
    p['actions'].append(dict(role='health_probe',subject=dict(number=3,version=1),mapping=ref))
    g['manifest']=call(service.record_manifest,g,1,p)['reference'];approve(g)
    value=call(service.convert,g,1,conversion(g,purpose='health_probe'))
    core=value['body'];assert core['protocol']=='ra-health-intent/1' and core['request_count']==1
    assert len(core['snapshot']['actions'])==1 and core['interpretation']['health']==[]
    assert core['link']['protocol']=='ra-health-link/1' and core['link']['members'][0]['role']=='health'
    assert call(service.read,g,'intent',value['reference'])['execution_status']=='w2_dependency_closed'
    assert s.timestamp(core['expires_at'])==NOW+timedelta(seconds=duration)
    assert call(service.read,g,'intent',value['reference'],now=NOW+timedelta(seconds=duration,microseconds=-1))['reference']==value['reference']
    with pytest.raises(s.IntentError,match='expired'):
        call(service.read,g,'intent',value['reference'],now=NOW+timedelta(seconds=duration))


def test_real_1024_version_capacity_includes_corrections(intent_graph):
    from sqlalchemy import insert
    g=intent_graph
    with SessionLocal() as db:
        template=db.scalar(select(IntentMapping).where(IntentMapping.context_id==g['ctx']))
        rows=[]
        for number in range(2,1024):
            body={**template.body,'number':number}
            rows.append(dict(context_id=g['ctx'],context_version=1,target_id=g['target'],number=number,version=1,
                body=body,digest=s.digest('ra-mapping/1',body),recorded_at=NOW,valid_until=template.valid_until))
        db.execute(insert(IntentMapping),rows);db.commit()
    p=mapping(g);p['expected_version']=1
    assert call(service.confirm_mapping,g,1,p)['reference']['version']==2
    before=snapshot()
    with pytest.raises(s.IntentError,match='storage_limit'):call(service.confirm_mapping,g,1024,mapping(g))
    assert snapshot()==before


def test_real_4096_audit_bound_is_atomic(intent_graph):
    from sqlalchemy import insert
    g=intent_graph
    with SessionLocal() as db:
        count=db.scalar(select(func.count()).select_from(IntentAudit).where(IntentAudit.context_id==g['ctx']))
        db.execute(insert(IntentAudit),[dict(context_id=g['ctx'],code='read',recorded_at=NOW) for _ in range(4095-count)]);db.commit()
    call(service.read,g,'mapping',g['mapping']);before=snapshot()
    with pytest.raises(s.IntentError,match='storage_limit'):call(service.read,g,'mapping',g['mapping'])
    assert snapshot()==before


@pytest.mark.parametrize('size',[256,257])
def test_bounded_future_assertion_scan(intent_graph,size):
    from sqlalchemy import insert
    g=intent_graph
    with SessionLocal() as db:
        db.execute(insert(ResourceAccessAssertion),[dict(resource_id=g['resource'],test_identity_id=g['anonymous'],relationship='non_owner',expected_access='allowed',provenance='target_fixture',confidence=80,verification_state='verified',asserted_at=NOW+timedelta(seconds=10)) for _ in range(size-1)])
        db.commit()
        context=service._locked(db,g['project'],g['ctx'])
        action=s.ManifestAction.model_validate(g['manifest_input']['actions'][0])
        _,mapping_value,_=service._mapping(db,context,action.mapping,NOW)
        if size==256:
            actor,ends=service._actor(db,context,action,mapping_value,NOW)
            assert len(actor['assertions'])==256 and min(ends)==NOW+timedelta(seconds=10)
        else:
            with pytest.raises(s.IntentError,match='fact_limit'):service._actor(db,context,action,mapping_value,NOW)
        db.rollback()


def test_shared_allowed_probe_remains_allowed(intent_graph,qualified_future):
    from sqlalchemy import delete
    g=intent_graph
    with SessionLocal() as db:
        db.execute(delete(ResourceAccessAssertion).where(ResourceAccessAssertion.resource_id==g['resource'],ResourceAccessAssertion.test_identity_id==g['bearer']));db.commit()
    assertion(g,relationship='shared',access='allowed',identity=g['bearer'])
    p=deepcopy(g['manifest_input']);p['expected_version']=1
    g['manifest']=call(service.record_manifest,g,1,p)['reference'];approve(g)
    value=call(service.convert,g,1,conversion(g))
    actor=value['body']['snapshot']['actions'][1]['actor']
    assert actor['facts']['expected_access']=='allowed' and actor['candidate']['candidate_kind']=='shared_access'


@pytest.mark.parametrize('change',['missing','conflict','same_actor','different_mapping','unreviewed_slot','query_slot','post','unbound_revision'])
def test_mandatory_rejections_before_manifest_or_plans(intent_graph,change):
    from sqlalchemy import delete
    from app.db.models.endpoint import Endpoint
    from app.db.models.endpoint_resource_binding import EndpointResourceBinding
    from app.db.models.target import Target
    g=intent_graph;p=deepcopy(g['manifest_input']);p['expected_version']=1
    with SessionLocal() as db:
        if change=='missing':db.execute(delete(ResourceAccessAssertion).where(ResourceAccessAssertion.resource_id==g['resource']))
        if change=='unreviewed_slot':db.get(EndpointResourceBinding,g['slot']).review_state='candidate'
        if change=='query_slot':db.get(EndpointResourceBinding,g['slot']).location='query'
        if change=='post':db.get(Endpoint,g['endpoint']).method='POST'
        if change=='unbound_revision':db.get(Target,g['target']).authorization_revision_id=None
        db.commit()
    if change=='conflict':assertion(g,access='denied')
    if change=='same_actor':p['actions'][1]['subject']['number']=1
    if change=='different_mapping':p['actions'][1]['mapping']['number']=999
    before=snapshot()
    with pytest.raises(Exception):call(service.record_manifest,g,1,p)
    assert snapshot()==before


def test_conversion_never_reads_or_resolves_secret_material(intent_graph,qualified_future,monkeypatch):
    from sqlalchemy import event
    from app.db.session import engine
    from app.credentials.bearer import BearerCredentialService
    from app.credentials.stored_secret import StoredSecretProvider
    g=intent_graph;approve(g);statements=[]
    def capture(conn,cursor,statement,*args):statements.append(statement.lower())
    def forbidden(*args,**kwargs):raise AssertionError('credential access forbidden')
    monkeypatch.setattr(BearerCredentialService,'resolve',forbidden)
    monkeypatch.setattr(BearerCredentialService,'resolve_binding',forbidden)
    monkeypatch.setattr(StoredSecretProvider,'load_secret',forbidden)
    event.listen(engine,'before_cursor_execute',capture)
    try:value=call(service.convert,g,1,conversion(g))
    finally:event.remove(engine,'before_cursor_execute',capture)
    assert all('test_identities.credentials' not in q and 'encrypted_envelope' not in q for q in statements)
    assert b'NOT_A_CREDENTIAL' not in s.output(value)


def test_final_budget_payload_serialization_failure_rolls_back_caught_commit(intent_graph,monkeypatch):
    g=intent_graph;before=snapshot();real=s.output
    def encode(value):
        if value['kind']=='budget' and 'decision_id' in value['body']:
            raise RuntimeError('controlled budget encoder failure')
        return real(value)
    monkeypatch.setattr(s,'output',encode)
    with SessionLocal() as db:
        with pytest.raises(RuntimeError,match='budget encoder'):
            service.decide_budget(db,g['project'],g['ctx'],dict(manifest=g['manifest'],expected_sequence=0,decision='approved',evidence=REF),now=NOW)
        db.commit()
    assert snapshot()==before


def test_observed_clock_rollback_during_serialization_is_atomic(intent_graph,qualified_future,monkeypatch):
    g=intent_graph;approve(g);before=snapshot();raw=[NOW]
    clock=service._clock(lambda:raw[0]);real=s.output
    def encode(value):
        result=real(value)
        if value['kind']=='intent':
            raw[0]=NOW+timedelta(seconds=2);clock()
            raw[0]=NOW+timedelta(seconds=1)
        return result
    monkeypatch.setattr(s,'output',encode)
    with SessionLocal() as db:
        with pytest.raises(s.IntentError,match='clock_invalid'):
            service.convert(db,g['project'],g['ctx'],1,conversion(g),now=clock)
        db.commit()
    assert snapshot()==before
