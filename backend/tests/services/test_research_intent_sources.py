from copy import deepcopy
from datetime import timedelta
import pytest
from app.schemas import research_intent as s
from app.schemas.research_subject import SubjectError
from app.services import research_intent as service,research_observation as observation,research_subject as subject
from tests.research_intent_fixtures import (intent_graph,subject_pair,two_intake_targets,qualified_future,
    call,approve,conversion,NOW,REF)  # noqa: F401
from tests.research_subject_fixtures import proposal
from tests.research_observation_fixtures import preparation,observation as source_input
from app.schemas.research_observation import canonical
from tests.research_intake_fixtures import snapshot


def attach_source(g,seconds=60):
    p=preparation(g);p.update(preparation_ref='preparation_2',path_templates=['/folders/{project_id}'],retention_seconds=seconds)
    call(observation.prepare,g,p)
    raw=source_input();raw['batch_ref']='batch_2';raw['preparation_ref']='preparation_2';raw['entries'][0]['path_template']='/folders/{project_id}';raw['entries'][0]['entry_ref']='entry_2'
    oid=call(observation.accept,g,'preparation_2',canonical(raw))['observation_id']
    p=proposal(g);p['sources']=[dict(observation_id=oid,source_entry_index=0)]
    call(subject.record,g,1,dict(expected_version=1,correction_reference=REF,proposal=p),correction=True)
    p=deepcopy(g['manifest_input']);p['expected_version']=1;p['actions'][0]['subject']['version']=2
    g['manifest']=call(service.record_manifest,g,1,p)['reference'];approve(g)
    return oid


@pytest.mark.parametrize('change',['hold','delete','quarantine','revoke','close'])
def test_exact_source_lifecycle_invalidates_work(intent_graph,qualified_future,change):
    g=intent_graph;oid=attach_source(g);result=call(service.convert,g,1,conversion(g))
    if change in {'hold','delete','quarantine'}:
        p={'review':REF}
        if change=='hold':p.update(reason='synthetic_review',until=(NOW+timedelta(days=1)).isoformat())
        call(observation.lifecycle,g,oid,change,p)
    elif change=='revoke':call(observation.revoke_preparation,g,'preparation_2',{'review':REF})
    else:
        from app.services.research_context import close_context
        call(close_context,g,dict(expected_version=1,closure_reference=REF))
    before=snapshot()
    with pytest.raises(Exception):call(service.read,g,'intent',result['reference'])
    assert snapshot()==before


@pytest.mark.parametrize('offset,allowed',[(-1,True),(0,False),(1,False)])
def test_source_expiry_during_final_audit_is_not_dropped(intent_graph,qualified_future,monkeypatch,offset,allowed):
    g=intent_graph;attach_source(g,1);before=snapshot();clock=[NOW];real=service._audit
    def audit(*args):
        result=real(*args)
        if args[2]=='intent':clock[0]=NOW+timedelta(seconds=1,microseconds=offset)
        return result
    monkeypatch.setattr(service,'_audit',audit)
    if allowed:call(service.convert,g,1,conversion(g),now=lambda:clock[0])
    else:
        with pytest.raises(s.IntentError,match='expired'):call(service.convert,g,1,conversion(g),now=lambda:clock[0])
        assert snapshot()==before


def hold_source(g, oid, at=NOW+timedelta(seconds=1)):
    return call(observation.lifecycle,g,oid,'hold',dict(review=REF,reason='synthetic_review',
        until=(NOW+timedelta(seconds=20)).isoformat()),now=at)


def recover_source(g, oid, recovery):
    if recovery=='release':
        call(observation.lifecycle,g,oid,'release',dict(review=REF),now=NOW+timedelta(seconds=3))
        return NOW+timedelta(seconds=4)
    return NOW+timedelta(seconds=20)  # Exact half-open hold expiry, no maintenance/read.


def fresh_manifest(g, at):
    p=deepcopy(g['manifest_input']);p['expected_version']=g['manifest']['version']
    p['actions'][0]['subject']['version']=2
    g['manifest']=call(service.record_manifest,g,1,p,now=at)['reference']


@pytest.mark.parametrize('recovery',['release','expiry'])
@pytest.mark.parametrize('intermediate_read',[False,True])
def test_recovery_requires_new_manifest_and_budget_in_production(intent_graph,recovery,intermediate_read):
    g=intent_graph;oid=attach_source(g);old=deepcopy(g['manifest'])
    hold_source(g,oid)
    if intermediate_read:
        before=snapshot()
        with pytest.raises(SubjectError):call(service.read,g,'manifest',old,now=NOW+timedelta(seconds=2))
        assert snapshot()==before
    at=recover_source(g,oid,recovery);before=snapshot()
    for fn,args in ((service.read,('manifest',old)),
                    (service.decide_budget,(dict(manifest=old,expected_sequence=1,decision='approved',evidence=REF),)),
                    (service.convert,(1,conversion(g)))):
        with pytest.raises(s.IntentError,match='intent_dependency_changed'):call(fn,g,*args,now=at)
        assert snapshot()==before
    fresh_manifest(g,at)
    assert g['manifest']!=old
    # Old human budget never transfers to the recovered source's fresh manifest.
    with pytest.raises(s.IntentError,match='intent_budget_unapproved'):
        call(service.convert,g,1,conversion(g),now=at)
    call(service.decide_budget,g,dict(manifest=g['manifest'],expected_sequence=0,decision='approved',evidence=REF),now=at)
    before=snapshot()
    with pytest.raises(s.IntentError,match='intent_w2_evidence_unavailable'):
        call(service.convert,g,1,conversion(g),now=at)
    assert snapshot()==before


@pytest.mark.parametrize('recovery',['release','expiry'])
@pytest.mark.parametrize('intermediate_read',[False,True])
def test_old_intent_never_revives_and_recovery_creates_new_plans(intent_graph,qualified_future,recovery,intermediate_read):
    g=intent_graph;oid=attach_source(g);old=call(service.convert,g,1,conversion(g))
    hold_source(g,oid)
    if intermediate_read:
        before=snapshot()
        with pytest.raises(SubjectError):call(service.read,g,'intent',old['reference'],now=NOW+timedelta(seconds=2))
        assert snapshot()==before
    at=recover_source(g,oid,recovery);before=snapshot()
    with pytest.raises(s.IntentError,match='intent_dependency_changed'):
        call(service.read,g,'intent',old['reference'],now=at)
    assert snapshot()==before
    fresh_manifest(g,at)
    call(service.decide_budget,g,dict(manifest=g['manifest'],expected_sequence=0,decision='approved',evidence=REF),now=at)
    new=call(service.convert,g,1,conversion(g,expected_version=1),now=at)
    assert new['reference']['version']==2
    assert new['body']['supersedes']==old['reference']
    assert {m['plan_id'] for m in old['body']['link']['members']}.isdisjoint(m['plan_id'] for m in new['body']['link']['members'])
    assert call(service.read,g,'intent',new['reference'],now=at)['reference']==new['reference']


@pytest.mark.parametrize('review',[None,dict(review=REF)])
def test_ordinary_observation_and_intent_reads_preserve_dependencies(intent_graph,qualified_future,review):
    g=intent_graph;oid=attach_source(g);old=call(service.convert,g,1,conversion(g))
    for seconds in (1,2):
        at=NOW+timedelta(seconds=seconds)
        assert call(observation.read,g,oid,review=review,now=at)['availability']=='available'
        assert call(service.read,g,'manifest',g['manifest'],now=at)['reference']==g['manifest']
        assert call(service.read,g,'intent',old['reference'],now=at)['reference']==old['reference']


def test_recovered_pin_survives_repeat_hold_with_identical_timestamps_and_audit_retirement(intent_graph,qualified_future):
    from sqlalchemy import delete,select
    from app.db.session import SessionLocal
    from app.db.models.research_observation import ObservationEvent,ObservationRecord
    g=intent_graph;oid=attach_source(g)
    at=NOW+timedelta(seconds=1)
    hold_source(g,oid,at)
    call(observation.lifecycle,g,oid,'release',dict(review=REF),now=at)
    fresh_manifest(g,at)
    call(service.decide_budget,g,dict(manifest=g['manifest'],expected_sequence=0,decision='approved',evidence=REF),now=at)
    result=call(service.convert,g,1,conversion(g),now=at)
    hold_source(g,oid,at)
    call(observation.lifecycle,g,oid,'release',dict(review=REF),now=at)
    with SessionLocal() as db:
        assert db.get(ObservationRecord,oid).hold_generation==2
        # Simulate eventual retirement of the bounded audit. It is not provenance.
        db.execute(delete(ObservationEvent).where(ObservationEvent.context_id==g['ctx']));db.commit()
    with pytest.raises(s.IntentError,match='intent_dependency_changed'):
        call(service.read,g,'intent',result['reference'],now=at)


@pytest.mark.parametrize('failure',['audit','receipt'])
def test_failed_hold_rolls_back_generation_and_preserves_manifest(intent_graph,monkeypatch,failure):
    g=intent_graph;oid=attach_source(g);before=snapshot()
    def fail(*args,**kwargs):raise RuntimeError('controlled failure')
    with monkeypatch.context() as patch:
        patch.setattr(observation,'_event' if failure=='audit' else '_receipt',fail)
        with pytest.raises(RuntimeError,match='controlled failure'):hold_source(g,oid)
    assert snapshot()==before
    assert call(service.read,g,'manifest',g['manifest'])['reference']==g['manifest']


def test_unrelated_source_hold_does_not_invalidate_manifest(intent_graph):
    from sqlalchemy import select
    from app.db.session import SessionLocal
    from app.db.models.research_observation import ObservationRecord
    g=intent_graph;oid=attach_source(g)
    with SessionLocal() as db:
        other=db.scalar(select(ObservationRecord.id).where(ObservationRecord.context_id==g['ctx'],ObservationRecord.id!=oid))
    assert other is not None
    hold_source(g,other)
    at=recover_source(g,other,'release')
    assert call(service.read,g,'manifest',g['manifest'],now=at)['reference']==g['manifest']


def test_hold_generation_limit_fails_closed_without_wrapping(intent_graph):
    from app.db.session import SessionLocal
    from app.db.models.research_observation import ObservationRecord
    from app.schemas.research_observation import ObservationError
    g=intent_graph;oid=attach_source(g)
    with SessionLocal() as db:
        db.get(ObservationRecord,oid).hold_generation=2147483646;db.commit()
    hold_source(g,oid)
    call(observation.lifecycle,g,oid,'release',dict(review=REF),now=NOW+timedelta(seconds=2))
    before=snapshot()
    with pytest.raises(ObservationError,match='observation_capacity_exceeded'):
        hold_source(g,oid,NOW+timedelta(seconds=3))
    assert snapshot()==before
    assert next(r for r in before['research_observation_records'] if r['id']==oid)['hold_generation']==2147483647


def test_previous_manifest_without_lifecycle_pin_is_not_reinterpreted(intent_graph):
    from app.db.session import SessionLocal
    from app.db.models.research_intent import IntentManifest
    from sqlalchemy import select
    g=intent_graph;attach_source(g)
    with SessionLocal() as db:
        original=db.scalar(select(IntentManifest).where(IntentManifest.context_id==g['ctx'],IntentManifest.version==2))
        body=deepcopy(original.body);body['number']=2;body['version']=1
        body['command']['expected_version']=0;body['supersedes']=None
        for action in body['snapshot']['actions']:
            for source in action['actor']['sources']:source.pop('hold_generation')
        old=IntentManifest(context_id=g['ctx'],context_version=1,target_id=g['target'],number=2,version=1,
            body=body,digest=s.digest('ra-manifest/1',body),recorded_at=original.recorded_at,valid_until=original.valid_until)
        db.add(old);db.flush();ref=service._ref(old);db.commit()
    before=snapshot()
    with pytest.raises(s.IntentError,match='intent_dependency_changed'):call(service.read,g,'manifest',ref)
    assert snapshot()==before


@pytest.mark.parametrize('offset',[-1,0,1])
def test_hold_expiry_boundary_only_permits_fresh_qualification(intent_graph,offset):
    g=intent_graph;oid=attach_source(g);old=deepcopy(g['manifest'])
    hold_source(g,oid)
    at=NOW+timedelta(seconds=20,microseconds=offset)
    before=snapshot()
    with pytest.raises(SubjectError if offset<0 else s.IntentError):
        call(service.read,g,'manifest',old,now=at)
    assert snapshot()==before
    if offset<0:
        with pytest.raises(SubjectError):fresh_manifest(g,at)
        assert snapshot()==before
    else:
        fresh_manifest(g,at)
        assert call(service.read,g,'manifest',g['manifest'],now=at)['reference']==g['manifest']
