from copy import deepcopy
from datetime import timedelta
import pytest
from app.schemas import research_intent as s
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
