from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from sqlalchemy import text
from app.db.session import SessionLocal
from app.db.models import Scope
from app.services import research_intent as service
from app.schemas.research_intent import IntentError
from tests.research_intent_fixtures import (intent_graph,subject_pair,two_intake_targets,qualified_future,
    future_proof,call,approve,conversion,mapping,NOW,REF)  # noqa: F401
from tests.services.test_research_subject_concurrency import wait_for_blocker


def test_scope_writer_waits_for_conversion_then_invalidates(intent_graph,monkeypatch):
    g=intent_graph;approve(g);ready,release,waiting=Event(),Event(),Event();pids={}
    def proof(*args,**kwargs):
        ready.set();assert release.wait(10);return future_proof(*args,**kwargs)
    monkeypatch.setattr(service,'_interpretation',proof)
    def reader():
        with SessionLocal() as db:
            pids['reader']=db.scalar(text('SELECT pg_backend_pid()'))
            result=service.convert(db,g['project'],g['ctx'],1,conversion(g),now=NOW)
            db.commit();return result
    def writer():
        with SessionLocal() as db:
            pids['writer']=db.scalar(text('SELECT pg_backend_pid()'));waiting.set()
            db.get(Scope,g['scope']).is_active=False;db.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(reader);assert ready.wait(10)
        b=pool.submit(writer);assert waiting.wait(10)
        try:wait_for_blocker(pids['writer'],pids['reader'])
        finally:release.set()
        result=a.result(15);b.result(15)
    with pytest.raises(Exception):call(service.read,g,'intent',result['reference'])


def test_concurrent_exact_version_creates_only_one_mapping(intent_graph):
    g=intent_graph
    def create():
        try:return call(service.confirm_mapping,g,2,mapping(g))['reference']
        except IntentError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:create(),range(2)))
    assert sum(isinstance(v,dict) for v in results)==1
    assert 'intent_version_conflict' in results


def test_hold_release_committed_while_reader_waits_invalidates_old_intent(intent_graph,qualified_future):
    from datetime import timedelta
    from app.services import research_observation as observation
    from tests.services.test_research_intent_sources import attach_source
    g=intent_graph;oid=attach_source(g);old=call(service.convert,g,1,conversion(g))
    waiting=Event();pids={}
    def reader():
        with SessionLocal() as db:
            pids['reader']=db.scalar(text('SELECT pg_backend_pid()'));waiting.set()
            return service.read(db,g['project'],g['ctx'],'intent',old['reference'],now=NOW+timedelta(seconds=4))
    with SessionLocal() as writer:
        pid=writer.scalar(text('SELECT pg_backend_pid()'))
        observation.lifecycle(writer,g['project'],g['ctx'],oid,'hold',dict(review=REF,reason='synthetic_review',
            until=(NOW+timedelta(seconds=20)).isoformat()),now=NOW+timedelta(seconds=1))
        with ThreadPoolExecutor(max_workers=1) as pool:
            result=pool.submit(reader);assert waiting.wait(10)
            try:
                wait_for_blocker(pids['reader'],pid)
                observation.lifecycle(writer,g['project'],g['ctx'],oid,'release',dict(review=REF),now=NOW+timedelta(seconds=3))
            finally:writer.commit()
            with pytest.raises(IntentError,match='intent_dependency_changed'):result.result(15)
