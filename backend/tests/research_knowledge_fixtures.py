"""Independent synthetic knowledge records; publication inserts are TEST ONLY."""
from datetime import timedelta
import pytest
from sqlalchemy import delete, select
from app.db.session import SessionLocal
from app.db.models.research_knowledge import KnowledgeVersion as KV, KnowledgeEvent as KE, KnowledgeAudit as KA
from app.schemas import research_knowledge as schema
from app.services import research_knowledge as service, research_subject as subject
from tests.research_subject_fixtures import subject_pair, two_intake_targets, proposal, assertion, call, NOW, REF, zero_capabilities  # noqa: F401


def content(number=1, scope='project', category='rule', sources=None):
    return {'contract':'ra-knowledge/1','scope':scope,'knowledge_id':f'knowledge-{number}','version':1,
        'category':category,'data_class':'synthetic_authored','purpose':'offline_context_explanation',
        'claim':'Sharing may allow non-owner access.','tags':['sharing','access'],
        'applicability':{'shape':'general_mechanism' if category=='mechanism' else 'single_resource_path_get_json_object',
                         'actors':['anonymous','bearer'],'requires_facts':category!='mechanism'},
        'source_refs':sources or [{'kind':'synthetic_authored','reference':REF,'observation_id':None,'source_entry_index':None,'payload_digest':None}],
        'example_refs':[REF], 'counterexample_refs':[{**REF,'fixture_id':2}], 'supersedes':None}


def record(g, c=None):
    return call(service.record,g['ctx'],{'context_version':1,'target_id':g['target'],'content':c or content(),'review':REF},project=g['project'])


def decision(g, ref, action, sequence, *, now=NOW, until=None):
    return call(service.decide,g['ctx'],{'reference':ref,'expected_sequence':sequence,'action':action,'review':REF,
        'valid_from':NOW.isoformat() if action in {'review','reuse','publish'} else None,
        'valid_until':(until or NOW+timedelta(days=10)).isoformat() if action in {'review','reuse','publish'} else None},project=g['project'],now=now)


def published(g, c=None, *, until=None):
    ref=record(g,c)['reference']
    r=decision(g,ref,'review',0,until=until)
    u=decision(g,ref,'reuse',1,until=until)
    with SessionLocal() as db:
        v=db.scalar(select(KV).where(KV.context_id==g['ctx'], KV.knowledge_id==ref['knowledge_id'],KV.version==ref['version']))
        # Ordinary runtime publish has no implementation. Only this test-owned fixture inserts
        # a clearly synthetic record; NOT_RUN is never converted to real W3 approval evidence.
        b=schema.EventBody(digest=ref['digest'],actor='synthetic_test_reviewer',evidence='synthetic_test_only',review=REF,
            context_version=1,valid_from=NOW.isoformat(),valid_until=(until or NOW+timedelta(days=10)).isoformat(),
            review_event_id=r['event_id'],reuse_event_id=u['event_id'],validation_ref='synthetic_test_only')
        db.add(KE(version_id=v.id,sequence=3,action='publish',body=b.model_dump(),recorded_at=NOW));db.commit()
    return ref


def query(**changes):
    return {'context_version':1,'subject_number':1,'subject_version':1,'purpose':'offline_context_explanation',
            'keywords':['sharing'],'tags':['access'],'top_k':8,'selected':[],**changes}


def retrieve(g, q=None, **kwargs):
    return call(service.retrieve,g['ctx'],q or query(),project=g['project'],**kwargs)


def source(g):
    from app.services import research_observation as observation
    value=call(observation.read,g['ctx'],g['observation'],project=g['project'])
    return {'kind':'observation','reference':None,'observation_id':g['observation'],'source_entry_index':0,'payload_digest':schema.digest(value['payload'])}


@pytest.fixture
def knowledge_pair(subject_pair):
    try:
        for g in subject_pair:
            a=assertion(g,'non_owner','allowed')
            from tests.research_observation_fixtures import observation, preparation
            from app.services import research_observation as observation_service
            prep=preparation(g);prep.update(preparation_ref='preparation_99',batch_refs=['batch_99'],entry_refs=['entry_99'])
            call(observation_service.prepare,g['ctx'],prep,project=g['project'])
            shape=observation(g['project'],58122+g['project'])
            shape.update(batch_ref='batch_99',preparation_ref='preparation_99');shape['entries'][0]['entry_ref']='entry_99'
            shape_id=call(observation_service.accept,g['ctx'],'preparation_99',schema.canonical(shape),project=g['project'])['observation_id']
            p=proposal(g);p['assertion_ids']=[a]
            p['sources']=[{'observation_id':shape_id,'source_entry_index':0}]
            call(subject.record,g['ctx'],1,p,project=g['project'])
        yield subject_pair
    finally:
        with SessionLocal() as db:
            ids=[g['ctx'] for g in subject_pair]
            v=select(KV.id).where(KV.context_id.in_(ids))
            db.execute(delete(KE).where(KE.version_id.in_(v)))
            db.execute(delete(KA).where(KA.context_id.in_(ids)))
            db.execute(delete(KV).where(KV.context_id.in_(ids)))
            db.commit()
