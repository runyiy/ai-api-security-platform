"""Real PostgreSQL conservation, transaction contention and explicit correction."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal, ROUND_CEILING
from threading import Event

import pytest
from sqlalchemy import select, text
from app.db.session import SessionLocal
from app.ai.proposals.adapter import Usage
from app.ai.w2 import schema as t
from app.ai.w2.records import make, PortError
from tests.ai.test_w2_execution import balances
from tests.ai.w2_fixtures import (w2, rule_pair, knowledge_pair, subject_pair,
    two_intake_targets, zero_capabilities, KNOWN)  # noqa: F401


def accepted(w2, usage=KNOWN):
    w2.reopen(); rt=w2.runtime()
    rt.admit(rt.receipt,rt.prepared.core.w1_binding_digest,30)
    w2.authority.script_final(rt.key,usage)
    rt.begin_send()
    try:
        rt.consume(rt.prepared.body,3)
    finally:
        rt.end_send()
    rt.close()
    return rt


@pytest.mark.parametrize('values', [(2048,384,0,0,128), (4097,1025,0,0,1025),
    (4000,10,51,2,None), (10000,2000,0,0,128)])
def test_t4_known_usage_full_actual_and_exact_refund(w2,values):
    i,o,c,w,r=values
    usage=Usage('known',i,o,c,w,r,i+o)
    rt=accepted(w2,usage)
    settled=rt.reconcile()
    cost=int((Decimal(2)*(i-c-w)+Decimal('.2')*c+Decimal('2.5')*w+Decimal(12)*o).to_integral_value(rounding=ROUND_CEILING))
    assert (settled.settled_tokens,settled.settled_microusd)==(i+o,cost)
    assert (settled.refund_tokens,settled.refund_microusd)==(max(5120-i-o,0),max(22528-cost,0))
    assert (settled.overrun_tokens,settled.overrun_microusd)==(max(i+o-5120,0),max(cost-22528,0))
    assert all((b['settled_tokens'],b['settled_microusd'],b['held_tokens'],b['held_microusd'],b['calls'])==(i+o,cost,0,0,1)
               for b in balances(rt.key))
    assert w2.witness.accepted[rt.key.fingerprint()]==1


@pytest.mark.parametrize('marker', [False,True])
def test_x2_independent_zero_keeps_call_slot(w2,marker):
    w2.reopen();rt=w2.runtime();rt.admit(rt.receipt,rt.prepared.core.w1_binding_digest,30)
    if marker:
        rt.begin_send();rt.end_send()
    rt.close();result=rt.reconcile()
    assert result.state=='ZERO' and (result.refund_tokens,result.refund_microusd)==(5120,22528)
    assert w2.witness.accepted[rt.key.fingerprint()]==0
    assert any(e.kind=='STREAM_CLOSED' for e in w2.authority.events(rt.key))
    assert all((b['calls'],b['held_tokens'],b['settled_tokens'])==(1,0,0) for b in balances(rt.key))
    with pytest.raises(Exception):rt.begin_send()


def test_x3_unknown_retains_liability_and_prevents_new_reservation(w2):
    rt=accepted(w2,Usage())
    result=rt.reconcile()
    assert result.state=='UNKNOWN'
    assert all((b['held_tokens'],b['held_microusd'],b['state'])==(5120,22528,'PAUSED_UNKNOWN') for b in balances(rt.key))
    with pytest.raises(PortError):w2.runtime('Q4b')
    assert w2.witness.accepted[rt.key.fingerprint()]==1


def test_t5_competing_postgres_transactions_one_winner(w2):
    left,right=w2.runtime(reserve=False),w2.runtime('Q4b',reserve=False)
    committing,release=Event(),Event()
    def pause(stage):
        if stage=='before_reserve_commit':
            committing.set();assert release.wait(5)
    w2.store.hook=pause
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(left.reserve_v1)
        assert committing.wait(5)
        second=pool.submit(right.reserve_v1)
        try:
            # Independently observe the second PostgreSQL backend waiting for
            # the account row held by the first transaction.
            from time import monotonic
            until=monotonic()+3
            blocked=False
            while monotonic()<until:
                with SessionLocal() as db:
                    blocked=bool(db.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE '%research_ai_budget_balance%'")))
                if blocked:break
            assert blocked
        finally:
            release.set()
        assert first.result(timeout=5).key==left.key
        with pytest.raises(PortError):second.result(timeout=5)
    assert all((b['held_tokens'],b['held_microusd'],b['calls'])==(5120,22528,1) for b in balances(left.key))
    assert not w2.witness.accepted


@pytest.mark.parametrize('stage', ['before_reserve_commit','after_commit'])
def test_t5_reserve_rollback_or_lost_ack_exact_lookup(w2,stage):
    rt=w2.runtime(reserve=False)
    def fail(actual):
        if actual==stage:raise RuntimeError('synthetic_lost_ack')
    w2.store.hook=fail
    with pytest.raises(RuntimeError):rt.reserve_v1()
    w2.store.hook=lambda _:None
    if stage=='before_reserve_commit':
        with pytest.raises(PortError):w2.store.lookup_reservation_v1(w2.read(),rt.key,w2.deadline())
        assert all(b['held_tokens']==b['calls']==0 for b in balances(rt.key))
    else:
        receipt=w2.store.lookup_reservation_v1(w2.read(),rt.key,w2.deadline())
        assert receipt.key==rt.key
        assert all((b['held_tokens'],b['calls'])==(5120,1) for b in balances(rt.key))


def test_x5_duplicate_conflict_and_delta_correction(w2):
    rt=accepted(w2);first=rt.reconcile()
    with SessionLocal() as db: before=list(db.execute(select(t.posting)).mappings())
    assert rt.reconcile()==first
    with SessionLocal() as db: assert list(db.execute(select(t.posting)).mappings())==before
    late=w2.authority.late_usage(rt.key,Usage('known',1000,100,0,0,20,1100))
    with pytest.raises(PortError,match='CONFLICT'):
        w2.store.reconcile_v1(rt.run,rt.key,[late.event_id],w2.authority)
    assert all((b['settled_tokens'],b['held_tokens'],b['state'])==(2432,5120,'PAUSED_UNKNOWN') for b in balances(rt.key))
    context=w2.recovery.recover_call(w2.read(),rt.key,'correct_1',1,'recovery_owner',w2.deadline())
    result=w2.store.reconcile_v1(context,rt.key,[late.event_id],w2.authority,recovery_decision='correct_1')
    assert result.revision==2 and (result.settled_tokens,result.settled_microusd)==(1100,3200)
    with SessionLocal() as db:
        correction=list(db.execute(select(t.posting).where(t.posting.c.settlement_id==result.settlement_id)).mappings())
    assert {(r['dimension'],r['settled_delta']) for r in correction}=={('tokens',-1332),('microusd',-5504)}
    with pytest.raises(PortError):rt.reconcile()


def test_t9_conflicting_over_cap_receipt_increases_disputed_liability(w2):
    rt=accepted(w2);rt.reconcile()
    late=w2.authority.late_usage(rt.key,Usage('known',30000,5000,0,0,100,35000))
    with pytest.raises(PortError):w2.store.reconcile_v1(rt.run,rt.key,[late.event_id],w2.authority)
    for b in balances(rt.key):
        assert b['settled_tokens']==2432 and b['settled_tokens']+b['held_tokens']>=35000
        assert b['settled_microusd']==8704 and b['settled_microusd']+b['held_microusd']>=120000
        assert b['state']=='PAUSED_UNKNOWN'


@pytest.mark.parametrize('w2',[{'caps':caps} for caps in (
    (5119,22528,1,30_000_000_000),(5120,22527,1,30_000_000_000),(5120,22528,0,30_000_000_000),
    (5120,22528,1,29_999_999_999),(None,22528,1,30_000_000_000),(5120,None,1,30_000_000_000),
    (0,0,0,0))],indirect=True)
def test_t4_absent_zero_or_one_short_caps_deny_atomically(w2):
    with pytest.raises(PortError):w2.runtime()
    assert all(b['calls']==b['held_tokens']==b['held_microusd']==0 for b in balances(make('ReservationKey',
        scope=w2.scope,call_ref='q_'+'1'*32,attempt=1,core_digest='a'*64)))
    assert not w2.authority.calls and not w2.witness.accepted


@pytest.mark.parametrize('w2',[{'caps':(5120,22528,1,30_000_000_000)}],indirect=True)
def test_t4_exact_all_caps_can_reserve_once(w2):
    rt=w2.runtime()
    assert rt.reservation.reserved_tokens==5120
    with pytest.raises(PortError):w2.runtime('Q4b')
    assert all(b['calls']==1 and b['held_tokens']==5120 for b in balances(rt.key))


def test_x8_late_usage_requires_recovery_owner_and_cancellation_persists(w2):
    rt=accepted(w2,Usage());rt.reconcile()
    class Cancellation:
        def __init__(self):self.tasks=set()
        def set(self,task):self.tasks.add(task)
    cancelled=Cancellation()
    w2.recovery.cancel_v1(w2.read(),rt.key,'cancel_explicit',w2.deadline(),cancelled)
    context=w2.recovery.recover_call(w2.read(),rt.key,'late_1',1,'late_owner',w2.deadline())
    event=w2.authority.late_usage(rt.key,KNOWN)
    result=w2.store.reconcile_v1(context,rt.key,[event.event_id],w2.authority)
    assert result.state=='KNOWN' and result.settled_tokens==2432
    assert sorted(b['state'] for b in balances(rt.key))==['CANCELLED','PAUSED_UNKNOWN']
    assert all(b['held_tokens']==0 for b in balances(rt.key))
    with pytest.raises(PortError):rt.reconcile()
    with pytest.raises(PortError):w2.runtime('Q4b')


def test_t11_explicit_recovery_resumes_only_resolved_budget_and_distinct_question(w2):
    rt=accepted(w2,Usage());rt.reconcile()
    with pytest.raises(PortError):w2.recovery.resume_budgets(w2.read(),'premature',w2.deadline())
    ctx=w2.recovery.recover_call(w2.read(),rt.key,'owner_change',1,'new_owner',w2.deadline())
    event=w2.authority.late_usage(rt.key,KNOWN)
    w2.store.reconcile_v1(ctx,rt.key,[event.event_id],w2.authority)
    w2.recovery.resume_budgets(w2.read(),'reviewed_resume',w2.deadline())
    assert w2.authority.state=='CLOSED'
    w2.reopen();w2.clock.advance(1)
    second=w2.runtime('Q4b')
    second.admit(second.receipt,second.prepared.core.w1_binding_digest,30)
    assert second.key!=rt.key and second.run.owner_generation==1
    with pytest.raises(PortError):w2.store.finish(rt.run,rt.key,w2.authority)
    with SessionLocal() as db:
        assert db.scalar(select(t.admission.c.closed).where(t.admission.c.key_digest==second.key.fingerprint())) is False
    assert all(b['calls']==2 for b in balances(rt.key))


def test_t4_exact_full_key_fields_cannot_be_reparented(w2):
    rt=w2.runtime()
    before=balances(rt.key)
    for field in w2.scope:
        if field=='format':continue
        value=w2.scope[field]
        scope=make('Scope',**{**w2.scope.document(),field:value+1 if type(value) is int else value+'_foreign'})
        key=make('ReservationKey',**{**rt.key.document(),'scope':scope})
        with pytest.raises(PortError):w2.store.lookup_reservation_v1(w2.read(scope),key,w2.deadline(w2.read(scope)))
    assert balances(rt.key)==before and not w2.witness.accepted


def test_t9_same_observer_id_conflicting_durable_contents_pause(w2):
    rt=accepted(w2);rt.reconcile()
    original=next(e for e in w2.authority.events(rt.key) if e.kind=='FINAL_USAGE')
    event=w2.authority.late_usage(rt.key,Usage('known',1000,100,0,0,0,1100),event_id=original.event_id)
    assert w2.authority.coverage() and event.event_id==original.event_id
    with pytest.raises(PortError,match='CONFLICT'):w2.store.reconcile_v1(rt.run,rt.key,[event.event_id],w2.authority)
    assert all(b['settled_tokens']==2432 and b['held_tokens']==5120 and b['state']=='PAUSED_UNKNOWN' for b in balances(rt.key))
    with SessionLocal() as db:assert db.scalar(select(t.conflict.c.reason))=='EVENT_CONFLICT'


def test_t4_usage_overflow_retains_exact_counts_and_original_liability(w2):
    from app.ai.w2.records import MAX_N,decode
    rt=accepted(w2,Usage('known',MAX_N,0,0,0,0,MAX_N))
    with pytest.raises(PortError):rt.reconcile()
    with SessionLocal() as db:
        records=[decode('Observation',bytes(r)) for r in db.scalars(select(t.observation_copy.c.record))]
    assert any(r.kind=='FINAL_USAGE' and r.usage.input_tokens==MAX_N for r in records)
    assert all(b['settled_tokens']==0 and b['held_tokens']==5120 and b['state']=='PAUSED_UNKNOWN' for b in balances(rt.key))


def test_t9_changed_core_for_consumed_question_is_durable_conflict_without_replay(w2):
    rt=accepted(w2);rt.reconcile();rt.finish(rt.receipt)
    with pytest.raises(PortError,match='CONFLICT'):w2.runtime('Q4')
    assert w2.witness.accepted[rt.key.fingerprint()]==1
    with SessionLocal() as db:
        assert db.scalar(select(t.conflict.c.reason))=='CORE_CONFLICT'
        assert len(list(db.execute(select(t.core))))==1
    assert all(b['settled_tokens']==2432 and b['held_tokens']==5120 and b['calls']==1 and b['state']=='PAUSED_UNKNOWN'
        for b in balances(rt.key))
