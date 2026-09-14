"""Actual PostgreSQL G/marker/lifecycle/accounting integration with B/J/W."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from hashlib import sha256
import time
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update

from app.db.session import SessionLocal, engine
from app.ai.w2 import schema as t
from app.ai.w2.records import PortError, stamp
from app.ai.w3.broker.bridge import initialize_synthetic, run_synthetic, audit_inventory
from app.ai.w3.broker.local import owned_synthetic_broker
from tests.ai.broker_fixtures import response
from tests.ai.w2_fixtures import (w2, rule_pair, knowledge_pair, subject_pair,
    two_intake_targets, zero_capabilities, KNOWN)  # noqa: F401


@contextmanager
def connected(w2, *, faults=None, raw=None):
    # No fake authority is reopened/replaced after it has issued authority.
    # The catalog fixture is qualified, with no calls or recovery history.
    runtime = w2.runtime(reserve=False)
    runtime.hook = lambda stage: None
    with owned_synthetic_broker(w2.scope.deployment_ref, uuid4().hex, stamp(w2.clock.utcnow()),
                               raw or response(), faults=faults) as fixture:
        client = fixture['client']
        initialize_synthetic(runtime, client)
        original = w2.writer.authority
        w2.writer.authority = client
        try: yield runtime, client, fixture
        finally: w2.writer.authority = original


def balances():
    with SessionLocal() as db:
        return list(db.execute(select(t.balance)).mappings())


def prepare_send(runtime, client):
    runtime.reserve_v1()
    runtime.admit(runtime.receipt, runtime.prepared.core.w1_binding_digest, runtime.deadline.remaining())
    client.call('prepare', dict(body=runtime.prepared.body.decode()))


@pytest.mark.parametrize('downstream', ['refusal', 'result_failure', 'accounting_failure', 'cancel'])
def test_final_usage_survives_refusal_cancellation_and_omitted_accounting(w2, downstream):
    with connected(w2, raw=response(refusal=downstream == 'refusal')) as (runtime, client, fixture):
        original_call = client.call
        def checked(op, data, timeout=3):
            if op in ('prepare', 'receive'):
                assert runtime.guard_token is None
            if op in ('prepare', 'write', 'receive', 'invalidate', 'disposition'):
                with engine.connect() as db:
                    active = db.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend' AND pid<>pg_backend_pid() AND xact_start IS NOT NULL"))
                    assert active == 0
            return original_call(op, data, timeout)
        client.call = checked
        def dispatch(reply):
            assert client.proves_response(runtime.key) and any(e.kind == 'FINAL_USAGE' for e in client.events(runtime.key))
            if downstream == 'result_failure': raise RuntimeError('synthetic result failure')
            if downstream == 'accounting_failure':
                runtime.store.hook = lambda stage: (_ for _ in ()).throw(PortError('COMMIT_UNKNOWN')) if stage == 'before_settlement_commit' else None
            if downstream == 'cancel':
                with w2.writer.mutation('CANCELLATION', 'synthetic_cancel', 'a'*64, w2.deadline()) as operation:
                    with SessionLocal() as db, db.begin():
                        operation.bind(db)
                        db.execute(update(t.balance).values(state='CANCELLED'))
                    operation.committed()
        result = run_synthetic(runtime, client, dispatch=dispatch)
        assert result.usage == KNOWN and result.terminal == 'COMPLETED'
        assert result.dispatch_completed == (downstream != 'result_failure')
        assert fixture['counts']['requests'].value == 1
        assert not hasattr(runtime, '_analysis_receipt') or runtime._analysis_receipt is None
        if downstream == 'accounting_failure':
            assert all(b['held_tokens'] >= 5120 for b in balances())
            runtime.store.hook = lambda stage: None
            client.refresh(); runtime.reconcile()
        prior = runtime.store.current_settlement(runtime.key)
        assert prior.state == 'KNOWN' and (prior.settled_tokens, prior.settled_microusd) == (2432, 8704)
        client.refresh(); runtime.reconcile()
        assert runtime.store.current_settlement(runtime.key) == prior
        with SessionLocal() as db:
            assert len(list(db.execute(select(t.posting)))) == 4
        if downstream == 'cancel': assert all(b['state'] == 'CANCELLED' for b in balances())


@pytest.mark.parametrize('pause', ['before_A', 'inside_A'])
def test_actual_lost_G_session_and_lifecycle_commit_order(w2, pause):
    with connected(w2, faults={'stage':pause}) as (runtime, client, fixture):
        prepare_send(runtime, client)
        def sender():
            try:
                runtime.begin_send()
                runtime._current_snapshot(runtime.key.scope.project_ref, runtime.key.scope.context_ref, 'write_ready')
                client.call('write', dict(permit=runtime.permit.document()), 3)
            finally: runtime.end_send()
        with ThreadPoolExecutor(1) as pool:
            old = pool.submit(sender)
            assert fixture['reached'].wait(3)
            pid = runtime.guard_token.backend_pid
            with engine.connect() as db:
                assert db.scalar(text('SELECT pg_terminate_backend(:pid,1000)'), {'pid':pid}) is True
                assert db.scalar(text('SELECT count(*) FROM pg_locks WHERE pid=:pid'), {'pid':pid}) == 0
            operation_id = uuid4().hex
            def mutate():
                with w2.writer.mutation('CANCELLATION', 'synthetic_race', 'b'*64, w2.deadline(), operation_id) as operation:
                    with SessionLocal() as db, db.begin():
                        operation.bind(db)
                        db.execute(update(t.balance).values(state='CANCELLED'))
                    operation.committed()
            if pause == 'before_A':
                mutate()
                assert all(b['state'] == 'CANCELLED' for b in balances())
                fixture['resume'].set()
            else:
                start = time.monotonic()
                with pytest.raises(PortError, match='COMMIT_UNKNOWN'): mutate()
                assert time.monotonic() - start < 5
                with SessionLocal() as db:
                    assert db.scalar(select(t.invalidation.c.operation_id).where(t.invalidation.c.operation_id == operation_id)) is None
                assert all(b['state'] == 'PAUSED_UNKNOWN' for b in balances())
                fixture['process'].kill(); fixture['process'].join(3)
            with pytest.raises(PortError): old.result(timeout=4)
        assert fixture['counts']['bytes'].value == 0
        assert all(b['held_tokens'] >= 5120 and b['held_microusd'] >= 22528 for b in balances())


def test_SQL_omission_is_a_visible_coverage_deficit_with_retained_liability(w2):
    with connected(w2) as (runtime, client, fixture):
        result = run_synthetic(runtime, client)
        assert result.usage == KNOWN
        # Restore assumption: first stop/fence the old B. Here SQL omission is
        # exercised with the live B closed at its completed stream, and there is
        # no reopen API. No lost SQL row grants another permit or display.
        with SessionLocal() as db, db.begin():
            db.execute(text('TRUNCATE research_ai_reservation CASCADE'))
        report = audit_inventory(runtime, client)
        assert not report['complete'] and report['unknown_upper_bound']
        assert (report['retained_tokens'],report['retained_microusd']) == (5120,22528)
        assert all(b['state'] == 'PAUSED_UNKNOWN' for b in balances())
        with pytest.raises(PortError):
            client.call('bootstrap', dict(deployment=runtime.key.scope.deployment_ref, sql_empty=sha256(b'[]').hexdigest()))
        assert fixture['counts']['requests'].value == 1
