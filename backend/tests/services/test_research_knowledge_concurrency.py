from concurrent.futures import ThreadPoolExecutor
from threading import Event
from datetime import timedelta
import pytest
from sqlalchemy import text, event, select, delete
from app.db.session import SessionLocal, engine
from app.services import research_knowledge as service, research_observation as observation
from app.services.research_context import close_context, create_context
from app.schemas.research_knowledge import KnowledgeError
from app.db.models.research_observation import ObservationRecord, ObservationPayload
from tests.research_knowledge_fixtures import knowledge_pair, subject_pair, two_intake_targets, content, published, query, retrieve, source, call, NOW, REF, zero_capabilities  # noqa: F401
from tests.research_intake_fixtures import intake
from tests.services.test_research_context_isolation import wait_for_blocker


@pytest.mark.parametrize('action',['withdraw','hold','delete','close'])
@pytest.mark.parametrize('read_first',[True,False])
def test_real_transactions_serialize_consumption_and_lifecycle(knowledge_pair,action,read_first):
    g,_=knowledge_pair;ref=published(g,content(sources=[source(g)]))
    ready,release,waiting=Event(),Event(),Event();pids={}
    def worker(is_read,leader):
        with SessionLocal() as db:
            pids[leader]=db.scalar(text('SELECT pg_backend_pid()'))
            if not leader:waiting.set()
            try:
                if is_read:result=service.retrieve(db,1,g['ctx'],query(),now=NOW)
                elif action=='withdraw':result=service.decide(db,1,g['ctx'],{'reference':ref,'expected_sequence':3,'action':'withdraw','review':REF,'valid_from':None,'valid_until':None},now=NOW)
                elif action=='close':result=close_context(db,1,g['ctx'],{'expected_version':1,'closure_reference':REF},now=NOW)
                else:
                    p={'review':REF}
                    if action=='hold':p.update(reason='synthetic_review',until=(NOW+timedelta(days=1)).isoformat())
                    result=observation.lifecycle(db,1,g['ctx'],g['observation'],action,p,now=NOW)
            except KnowledgeError as exc:result=exc.code
            if leader:ready.set();assert release.wait(10)
            db.commit();return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(worker,read_first,True);assert ready.wait(10)
        b=pool.submit(worker,not read_first,False);assert waiting.wait(10)
        try:wait_for_blocker(pids[False],pids[True])
        finally:release.set()
        first,second=a.result(15),b.result(15)
    if read_first:assert first['matches']
    elif action=='close':assert second=='knowledge_unavailable'
    else:assert second['matches']==[]
    if action!='close':assert retrieve(g)['matches']==[]


def test_close_transfer_never_reads_new_owners_metadata(knowledge_pair):
    g,_=knowledge_pair;ref=published(g)
    call(close_context,g['ctx'],{'expected_version':1,'closure_reference':REF})
    with SessionLocal() as db:
        moved=create_context(db,{'project_number':3,'intake':intake(g)},now=NOW)['context_id'];db.commit()
    # The existing owning fixture removes associations/contexts for its Targets.
    statements=[]
    def capture(conn,cursor,statement,*args):statements.append(statement.lower())
    event.listen(engine,'before_cursor_execute',capture)
    try:
        for q in (query(),query(selected=[ref])):
            with pytest.raises(KnowledgeError):retrieve(g,q)
    finally:event.remove(engine,'before_cursor_execute',capture)
    assert not any('from '+table in sql for sql in statements for table in ('targets','test_identities','credential_bindings','authorization_revisions','scopes'))


@pytest.mark.parametrize('kind',['publication','source'])
def test_expiration_during_audit_rolls_back_instead_of_stale_result(knowledge_pair,monkeypatch,kind):
    from tests.research_intake_fixtures import snapshot
    g,_=knowledge_pair
    # A moving service clock is injectable only in tests, never in the API body.
    end=NOW+timedelta(seconds=1)
    if kind=='source':
        with SessionLocal() as db:
            row=db.get(ObservationRecord,g['observation']);row.expires_at=end;db.commit()
    published(g,content(sources=[source(g)]),until=end if kind=='publication' else NOW+timedelta(days=10))
    clock=[NOW];original=observation._time;old_audit=service._audit
    monkeypatch.setattr(observation,'_time',lambda now: clock[0])
    def audit(*args,**kwargs):
        result=old_audit(*args,**kwargs);clock[0]=end;return result
    monkeypatch.setattr(service,'_audit',audit)
    before=snapshot()
    with pytest.raises(KnowledgeError):retrieve(g)
    assert snapshot()==before


def test_hold_release_and_final_purge_preserve_knowledge_history(knowledge_pair):
    g,_=knowledge_pair;published(g,content(sources=[source(g)]))
    call(observation.lifecycle,g['ctx'],g['observation'],'hold',{'review':REF,'reason':'synthetic_review','until':(NOW+timedelta(days=1)).isoformat()})
    assert retrieve(g)['matches']==[]
    call(observation.lifecycle,g['ctx'],g['observation'],'release',{'review':REF},now=NOW+timedelta(hours=1))
    assert retrieve(g,now=NOW+timedelta(hours=1))['matches']
    call(observation.lifecycle,g['ctx'],g['observation'],'delete',{'review':REF},now=NOW+timedelta(hours=2))
    call(observation.maintain,g['ctx'],{'action':'reconcile','review':REF},now=NOW+timedelta(days=91))
    with SessionLocal() as db:
        assert db.get(ObservationRecord,g['observation']) is None
        from app.db.models.research_knowledge import KnowledgeVersion, KnowledgeEvent
        assert db.scalar(select(KnowledgeVersion.id)) is not None and db.scalar(select(KnowledgeEvent.id)) is not None


@pytest.mark.parametrize('read_first',[True,False])
def test_new_conflicting_assertion_cannot_race_retrieval(knowledge_pair,read_first):
    from app.db.models.resource_access_assertion import ResourceAccessAssertion
    g,_=knowledge_pair;published(g)
    ready,release,waiting=Event(),Event(),Event();pids={}
    def worker(is_read,leader):
        with SessionLocal() as db:
            pids[leader]=db.scalar(text('SELECT pg_backend_pid()'))
            if not leader:waiting.set()
            if is_read:result=service.retrieve(db,1,g['ctx'],query(),now=NOW)
            else:
                db.add(ResourceAccessAssertion(resource_id=g['resource'],test_identity_id=g['anonymous'],relationship='non_owner',expected_access='denied',provenance='target_fixture',confidence=80,verification_state='verified',asserted_at=NOW-timedelta(seconds=1)))
                db.flush();result=None
            if leader:ready.set();assert release.wait(10)
            db.commit();return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(worker,read_first,True);assert ready.wait(10)
        b=pool.submit(worker,not read_first,False);assert waiting.wait(10)
        try:wait_for_blocker(pids[False],pids[True])
        finally:release.set()
        first,second=a.result(15),b.result(15)
    if read_first:assert first['matches']
    else:assert second['matches']==[] and 'facts_conflict' in second['missing_inputs']
    assert retrieve(g)['matches']==[]
