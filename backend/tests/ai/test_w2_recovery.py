"""Restoration and omission are checked against independent durable evidence."""
from contextlib import contextmanager
from hashlib import sha256
import os

import pytest
from sqlalchemy import select,text,update
from app.db.session import SessionLocal,engine
from app.ai.proposals.adapter import Usage
from app.ai.w2 import schema as t
from app.ai.w2.fake_authority import Journal,EndpointWitness,FakeAuthority
from app.ai.w2.records import PortError,make
from tests.ai.test_w2_accounting import accepted
from tests.ai.test_w2_execution import balances
from tests.ai.w2_fixtures import (w2,rule_pair,knowledge_pair,subject_pair,two_intake_targets,zero_capabilities,KNOWN)  # noqa: F401


@contextmanager
def restarted(w2):
    old=w2.authority
    old.journal.close();old.witness.close()
    journal=Journal(old.journal.path)
    witness=EndpointWitness(old.witness.journal.path)
    authority=FakeAuthority(w2.scope.deployment_ref,journal,witness,w2.clock)
    w2.writer.authority=w2.recovery.authority=authority
    try:
        assert authority.epoch!=old.epoch and authority.state=='RECOVERY_REQUIRED'
        yield authority
    finally:
        journal.close();witness.close()


@pytest.mark.parametrize('omission',['result','accounting','both','core_and_balances'])
def test_x7_omitted_ledgers_recovered_from_parent_core_and_endpoint(w2,omission):
    rt=accepted(w2,Usage())
    if omission=='result':
        rt.reconcile()
    elif omission=='core_and_balances':
        # Explicitly simulate a verified older snapshot of only this owned TEST
        # database. Parent journal/core files and endpoint witness are untouched.
        with engine.begin() as db:
            db.execute(text('TRUNCATE research_ai_call_core CASCADE'))
            db.execute(update(t.balance).values(held_tokens=0,held_microusd=0,calls=0,wall_ns=0))
    with restarted(w2) as authority:
        assert authority.coverage() and authority.witness.accepted[rt.key.fingerprint()]==1
        if omission=='core_and_balances':
            with pytest.raises(PortError):w2.reopen()
            ctx=w2.recovery.restore_liability(w2.read(),rt.key,'restore_1','restored_owner',w2.deadline())
        else:
            ctx=w2.recovery.recover_call(w2.read(),rt.key,'recover_1',1,'recovery_owner',w2.deadline())
        assert all(b['held_tokens']==5120 and b['held_microusd']==22528 for b in balances(rt.key))
        event=authority.late_usage(rt.key,KNOWN)
        settled=w2.store.reconcile_v1(ctx,rt.key,[event.event_id],authority)
        assert settled.state=='KNOWN' and settled.settled_tokens==2432 and settled.settled_microusd==8704
        assert all(b['held_tokens']==b['held_microusd']==0 for b in balances(rt.key))
        assert authority.state=='CLOSED'
        with pytest.raises(PortError):authority.consume_write_v1(rt.permit,rt.prepared.body)
        assert authority.witness.accepted[rt.key.fingerprint()]==1
        with SessionLocal() as db:assert len(list(db.execute(select(t.completion))))==0


@pytest.mark.parametrize('drop',['observer_callback','journal_tail','all_independent_evidence'])
def test_t10_t11_coverage_gap_never_authorizes_refund_or_reopen(w2,drop):
    w2.reopen();rt=w2.runtime()
    rt.admit(rt.receipt,rt.prepared.core.w1_binding_digest,30)
    w2.authority.script_final(rt.key,Usage())
    rt.begin_send();rt.end_send()
    before=w2.journal.path.read_bytes()
    rt.consume(rt.prepared.body,3)
    accepted_event=next(e for e in w2.authority.events(rt.key) if e.kind=='WRITE_ACCEPTED')
    if drop=='observer_callback':
        del w2.authority.observations[accepted_event.event_id]
        assert not w2.authority.coverage()
        rt.close();result=rt.reconcile()
        assert result.state=='UNKNOWN' and result.held_tokens==5120
        with pytest.raises(PortError):w2.reopen()
    elif drop=='journal_tail':
        w2.journal.close()
        # Restore only the earlier observer journal; the separately retained
        # witness still has the accepted event and later high-water mark.
        w2.journal.path.write_bytes(before)
        with restarted(w2) as authority:
            assert not authority.coverage()
            assert authority.witness.accepted[rt.key.fingerprint()]==1
            with pytest.raises(PortError):w2.reopen()
    else:
        w2.journal.close();w2.witness.close()
        assert not w2.authority.coverage()
        with pytest.raises(PortError):w2.reopen()
    assert all(b['held_tokens']==5120 and b['held_microusd']==22528 for b in balances(rt.key))


def test_t11_fsync_without_ack_is_lookup_only_then_explicit_recovery(w2):
    w2.reopen();rt=w2.runtime()
    rt.admit(rt.receipt,rt.prepared.core.w1_binding_digest,30)
    rt.begin_send();rt.end_send()
    deadline=w2.deadline()
    operation=w2.writer.mutation('LIFECYCLE','writer',sha256(b'independent mutation').hexdigest(),deadline,'lost_ack')
    def drop(stage):
        if stage=='before_ack':raise PortError('COMMIT_UNKNOWN')
    w2.authority.hook=drop
    with pytest.raises(PortError):operation.__enter__()
    w2.authority.hook=lambda _:None
    ack=w2.authority.lookup_invalidation_v1(operation.context,'lost_ack',operation.request.operation_digest)
    assert ack.disposition=='PENDING'
    with SessionLocal() as db:assert db.scalar(select(t.invalidation.c.operation_id).where(t.invalidation.c.operation_id=='lost_ack')) is None
    with pytest.raises(PortError):w2.authority.consume_write_v1(rt.permit,rt.prepared.body)
    # Exact lookup does not authorize mutation replay, and OPEN requires a new
    # explicit recovery decision with transaction and stream closure evidence.
    w2.reopen()
    assert w2.authority.state=='OPEN'
    with pytest.raises(PortError):w2.authority.consume_write_v1(rt.permit,rt.prepared.body)
    assert w2.witness.accepted[rt.key.fingerprint()]==0


def test_t11_partial_restoration_with_unattributed_settled_balance_stays_closed(w2):
    rt=accepted(w2);rt.reconcile()
    with engine.begin() as db:
        db.execute(text('TRUNCATE research_ai_call_core CASCADE'))
    with restarted(w2) as authority:
        with pytest.raises(PortError,match='CONFLICT'):
            w2.recovery.restore_liability(w2.read(),rt.key,'ambiguous_restore','restore_owner',w2.deadline())
        assert authority.state=='CLOSED'
        assert all((b['settled_tokens'],b['settled_microusd'],b['held_tokens'])==(2432,8704,0) for b in balances(rt.key))
        with SessionLocal() as db:assert db.scalar(select(t.reservation.c.key_digest)) is None
        with pytest.raises(PortError):w2.reopen()


@pytest.mark.parametrize('missing',['marker','posting','all_observer_history'])
def test_t10_reopening_compares_sql_against_independent_durable_inventory(w2,missing):
    rt=accepted(w2);rt.reconcile()
    rt.finish(rt.receipt)
    if missing in ('marker','posting'):
        with engine.begin() as db:
            db.execute(text('TRUNCATE '+('research_ai_send_marker' if missing=='marker' else 'research_ai_posting')))
        with restarted(w2) as authority:
            with pytest.raises(PortError,match='OBSERVER_UNAVAILABLE'):w2.reopen()
            assert authority.state=='RECOVERY_REQUIRED'
    else:
        w2.journal.close();w2.witness.close()
        # Independent records physically absent, with SQL call history still
        # present: an empty new journal is not a proof of zero past calls.
        w2.journal.path.unlink();w2.witness.journal.path.unlink()
        with restarted(w2) as authority:
            assert not authority.calls and authority.coverage()
            with pytest.raises(PortError,match='OBSERVER_UNAVAILABLE'):w2.reopen()
            assert authority.state=='RECOVERY_REQUIRED'
    assert all(b['state']=='PAUSED_UNKNOWN' and b['settled_tokens']==2432 for b in balances(rt.key))


def test_t11_old_database_cannot_erase_committed_context_invalidation(w2):
    from app.services import research_context
    from app.db.models.research_context import ResearchContext,ResearchTargetAssociation
    from tests.research_rule_fixtures import call,REF
    w2.reopen();before=set(w2.authority.operations)
    call(research_context.close_context,w2.g['ctx'],dict(expected_version=1,closure_reference=REF),project=w2.g['project'])
    committed=next(a for key,a in w2.authority.operations.items() if key not in before)
    assert committed.disposition=='COMMITTED'
    with engine.begin() as db:
        # Simulate an older snapshot of this verified TEST database. A bypass
        # is used only by its administrative fault injector, not the child or
        # the application writer path. The independent journal stays current.
        db.execute(text("SET LOCAL session_replication_role='replica'"))
        db.execute(update(ResearchContext).where(ResearchContext.id==w2.g['ctx']).values(closed_at=None,closure_reference=None))
        db.execute(update(ResearchTargetAssociation).where(ResearchTargetAssociation.context_id==w2.g['ctx']).values(released_at=None))
        db.execute(text('DELETE FROM research_ai_invalidation WHERE operation_id=:id'),{'id':committed.operation_id})
    with restarted(w2) as authority:
        with pytest.raises(PortError,match='OBSERVER_UNAVAILABLE'):w2.reopen()
        assert authority.state=='RECOVERY_REQUIRED' and not authority.witness.accepted
    with SessionLocal() as db:
        assert db.get(ResearchContext,w2.g['ctx']).closed_at is None
    assert all(b['state']=='PAUSED_UNKNOWN' for b in balances(make('ReservationKey',scope=w2.scope,
        call_ref='q_'+'1'*32,attempt=1,core_digest='a'*64)))


@pytest.mark.parametrize('w2',[{'source_window_seconds':1}],indirect=True)
@pytest.mark.parametrize('elapsed',[0.5,1.0])
def test_t11_reopen_qualifications_expire_at_final_authority_consumption(w2,monkeypatch,elapsed):
    original=w2.authority.reopen_v1
    def completed_wait(*args,**kwargs):
        w2.clock.advance(elapsed)
        return original(*args,**kwargs)
    monkeypatch.setattr(w2.authority,'reopen_v1',completed_wait)
    before=len(w2.journal.entries)
    if elapsed==0.5:
        w2.reopen()
        assert w2.authority.state=='OPEN'
    else:
        with pytest.raises(PortError,match='AUTHORITY_UNAVAILABLE'):w2.reopen()
        assert w2.authority.state=='CLOSED'
        assert not any(e['payload']['format']=='ra-w2-gate-ack/1' for e in w2.journal.entries[before:])
    assert w2.authority.coverage()


def test_t11_reopen_cannot_retag_a_generation_changed_during_qualification(w2,monkeypatch):
    original=w2.registry.model_permission
    generation=w2.authority.generation
    def intervening_invalidation(scope,deadline):
        result=original(scope,deadline)
        ctx=make('InvalidationContext',deployment_ref=scope.deployment_ref,writer_id='concurrent_closure',
            process_epoch=deadline.context.process_epoch,deadline_at=deadline.context.deadline_at,
            mono_deadline_ns=deadline.context.mono_deadline_ns,cancellation_id=deadline.context.cancellation_id)
        request=make('InvalidationRequest',operation_id='intervening_closure',
            operation_digest=sha256(b'no database mutation').hexdigest(),deployment_ref=scope.deployment_ref,
            writer_id=ctx.writer_id,action='INVALIDATE_DEPLOYMENT',reason='RECOVERY',deadline_at=ctx.deadline_at)
        ack=w2.authority.invalidate_v1(ctx,request)
        # This parent-controlled operation did no SQL and is terminal. Merely
        # checking for pending operations would therefore miss the changed gate.
        resolution=make('InvalidationResolution',operation_id=request.operation_id,
            operation_digest=request.operation_digest,deployment_ref=scope.deployment_ref,
            barrier_digest=ack.event_digest,database_outcome='ROLLED_BACK',transaction_evidence_ref=request.operation_id)
        w2.authority.resolve_invalidation_v1(ctx,resolution,('ROLLED_BACK',request.operation_id))
        return result
    monkeypatch.setattr(w2.registry,'model_permission',intervening_invalidation)
    before=len(w2.journal.entries)
    with pytest.raises(PortError,match='CONTEXT_CHANGED'):w2.reopen()
    assert w2.authority.generation==generation+1 and w2.authority.state=='CLOSED'
    assert w2.authority.operations['intervening_closure'].disposition=='ROLLED_BACK'
    assert not any(e['payload']['format']=='ra-w2-gate-ack/1' for e in w2.journal.entries[before:])
    assert w2.authority.coverage()
