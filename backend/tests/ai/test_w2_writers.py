"""Actual lifecycle transactions, nested reads and internal commit boundaries."""
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import select,text
from app.db.session import SessionLocal,engine
from app.db.models.research_context import ResearchContext
from app.ai.w2 import schema as t
from app.ai.w2.records import PortError
from app.services import research_context,research_observation,research_subject,research_rule_validation
from tests.research_rule_fixtures import call,REF,feedback,review
from tests.ai.w2_fixtures import w2,rule_pair,knowledge_pair,subject_pair,two_intake_targets,zero_capabilities  # noqa: F401


def new_barriers(w2,before):
    values=[a for key,a in w2.authority.operations.items() if key not in before]
    assert len(values)==1
    with SessionLocal() as db:
        rows=list(db.execute(select(t.invalidation).where(t.invalidation.c.operation_id==values[0].operation_id)).mappings())
        assert len(rows)==(values[0].disposition=='COMMITTED')
        assert db.scalar(text("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND classid=73105 AND objid=2 AND objsubid=2"))==0
    assert w2.authority.state=='CLOSED' and w2.authority.coverage()
    return values[0]


@pytest.mark.parametrize('path',['observation_read','subject_read','validation_read','internal_commit'])
def test_t3_outer_reads_and_internal_commit_have_durable_barrier(w2,path):
    w2.reopen();before=set(w2.authority.operations)
    if path=='observation_read':
        result=call(research_observation.read,w2.g['ctx'],w2.g['observation'],project=w2.g['project'])
        assert result['availability']=='available'
    elif path=='subject_read':
        call(research_subject.read,w2.g['ctx'],1,project=w2.g['project'])
    elif path=='validation_read':
        call(research_rule_validation.read_validation,w2.g['ctx'],dict(
            reference={k:w2.source[k] for k in ('scope','knowledge_id','version','digest')},validation_ref=w2.proof),project=w2.g['project'])
    else:
        from app.api.routes.scopes import create_scope
        from app.schemas.scope import ScopeCreate
        with SessionLocal() as db:
            result=create_scope(ScopeCreate(target_id=w2.g['target'],hostname='localhost',
                path_pattern='/synthetic-boundary/*',allowed_methods=['GET']),db)
            assert result.id is not None
            assert db.info.get('w2_lifecycle_guard') is None
    assert new_barriers(w2,before).disposition=='COMMITTED'


def test_t3_w3_disable_nested_knowledge_writer_borrows_one_guard(w2):
    source={k:w2.source[k] for k in ('scope','knowledge_id','version','digest')}
    proposed=feedback(w2.g,source,w2.proof,proposal='disable',reason='qualification_missing')
    w2.reopen();before=set(w2.authority.operations)
    result=review(w2.g,source,proposed['feedback_id'])
    assert result['status']=='accept'
    assert new_barriers(w2,before).disposition=='COMMITTED'
    with pytest.raises(PortError):w2.prepare()


def test_t6_guard_is_retained_through_outer_commit_and_rollback(w2):
    w2.reopen();before=set(w2.authority.operations)
    with SessionLocal() as db:
        research_context.close_context(db,w2.g['project'],w2.g['ctx'],
            dict(expected_version=1,closure_reference=REF),now=w2.clock.utcnow())
        token=db.info['w2_lifecycle_guard']
        with engine.begin() as check:
            assert check.scalar(text('SELECT count(*) FROM pg_locks WHERE pid=:pid AND locktype=\'advisory\''),{'pid':token.token.backend_pid})==1
            assert check.scalar(select(ResearchContext.closed_at).where(ResearchContext.id==w2.g['ctx'])) is None
        assert token.ack.disposition=='PENDING'
        db.rollback()
    assert new_barriers(w2,before).disposition=='ROLLED_BACK'


def test_t6_late_entry_under_database_lock_rejects_before_a(w2):
    w2.reopen();before=set(w2.authority.operations)
    with SessionLocal() as db:
        db.scalar(select(ResearchContext).where(ResearchContext.id==w2.g['ctx']).with_for_update())
        with pytest.raises(PortError,match='CONTEXT_CHANGED'):
            research_observation.read(db,w2.g['project'],w2.g['ctx'],w2.g['observation'],now=w2.clock.utcnow())
        db.rollback()
    assert set(w2.authority.operations)==before and w2.authority.state=='OPEN'


def test_t11_lost_g_writer_transaction_cannot_be_declared_rolled_back(w2):
    w2.reopen();d=w2.deadline()
    operation=w2.writer.mutation('LIFECYCLE','paused_writer',sha256(b'pending real transaction').hexdigest(),d)
    operation.__enter__()
    with SessionLocal() as db:
        try:
            operation.bind(db)
            db.execute(text('UPDATE research_contexts SET closed_at=:at WHERE id=:id'),{'at':w2.clock.utcnow(),'id':w2.g['ctx']})
            with engine.begin() as check:
                assert check.scalar(text('SELECT pg_terminate_backend(:pid)'),{'pid':operation.token.backend_pid}) is True
            with pytest.raises(PortError,match='COMMIT_UNKNOWN'):w2.reopen()
            assert w2.authority.state=='CLOSED'
        finally:
            db.rollback()
            # Resolve only after independent rollback evidence exists; the G
            # socket is gone and cannot be reused for another bind.
            with pytest.raises(PortError,match='OWNER_LOST'):
                operation.__exit__(PortError,None,None)
    w2.reopen()
    assert w2.authority.state=='OPEN'


def test_t11_old_writer_cannot_bind_after_loss_and_explicit_reopen(w2):
    w2.reopen();operation=w2.writer.mutation('LIFECYCLE','stale_writer',sha256(b'unbound writer').hexdigest(),w2.deadline())
    operation.__enter__()
    with engine.begin() as check:
        assert check.scalar(text('SELECT pg_terminate_backend(:pid)'),{'pid':operation.token.backend_pid}) is True
    w2.reopen()
    with SessionLocal() as db:
        with pytest.raises(PortError,match='OWNER_LOST'):operation.bind(db)
        db.rollback()
    operation.__exit__(PortError,None,None)
    with SessionLocal() as db:
        assert db.scalar(select(t.invalidation.c.operation_id).where(t.invalidation.c.operation_id==operation.id)) is None


def test_t11_writer_paused_after_g_check_cannot_mutate_after_reopen(w2):
    from app.ai.w2.guard import WRITER_FENCE_KEY
    w2.reopen()
    operation=w2.writer.mutation('LIFECYCLE','post_check_writer',
        sha256(b'G checked before database transaction').hexdigest(),w2.deadline())
    operation.__enter__()
    checked,resume=Event(),Event()
    original=operation.token.verify

    def pause_after_real_check(deadline):
        original(deadline)
        checked.set()
        assert resume.wait(10)

    operation.token.verify=pause_after_real_check

    def stale_writer():
        with SessionLocal() as db:
            try:
                operation.bind(db)
                db.execute(text('UPDATE research_contexts SET closed_at=:at WHERE id=:id'),
                    {'at':w2.clock.utcnow(),'id':w2.g['ctx']})
                db.commit()
                return 'COMMITTED'
            except PortError as exc:
                db.rollback()
                return exc.code

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(stale_writer)
            try:
                assert checked.wait(5)
                with engine.begin() as check:
                    assert check.scalar(text('SELECT pg_terminate_backend(:pid)'),
                        {'pid':operation.token.backend_pid}) is True
                w2.reopen()
                assert w2.authority.state=='OPEN'
                assert w2.authority.operations[operation.id].disposition=='ROLLED_BACK'
                with SessionLocal() as db:
                    assert db.scalar(select(t.recovery.c.owner_generation).where(
                        t.recovery.c.key_digest==WRITER_FENCE_KEY)
                        .order_by(t.recovery.c.owner_generation.desc()).limit(1))>=operation.ack.acceptance_generation
            finally:
                resume.set()
            assert future.result(timeout=5)=='OWNER_LOST'
        with SessionLocal() as db:
            assert db.scalar(select(ResearchContext.closed_at).where(ResearchContext.id==w2.g['ctx'])) is None
            assert db.scalar(select(t.invalidation.c.operation_id).where(t.invalidation.c.operation_id==operation.id)) is None
        assert w2.authority.coverage()
    finally:
        resume.set()
        with pytest.raises(PortError,match='OWNER_LOST'):
            operation.__exit__(PortError,None,None)
