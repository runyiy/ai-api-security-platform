"""Y1–Y5 real G-session loss, writer commits and sandboxed stale resumption."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from hashlib import sha256
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from app.db.session import SessionLocal, engine
from app.db.models.research_context import ResearchContext
from app.ai.proposals.adapter import Usage
from app.ai.w2 import schema as t
from app.ai.w2.records import PortError, utc
from app.ai.w2.control_ipc import InvalidationServer,InvalidationClient
from app.services import research_context, research_knowledge
from tests.research_rule_fixtures import call, REF
from tests.ai.test_w2_execution import sandbox_run, response, balances
from tests.ai.w2_fixtures import (w2, rule_pair, knowledge_pair, subject_pair,
    two_intake_targets, zero_capabilities, KNOWN)  # noqa: F401


def invalidate(w2, kind):
    if kind == 'context':
        return call(research_context.close_context,w2.g['ctx'],
            dict(expected_version=1,closure_reference=REF),project=w2.g['project'])
    return call(research_knowledge.decide,w2.g['ctx'],
        dict(reference={k:w2.source[k] for k in ('scope','knowledge_id','version','digest')},action='withdraw',
            expected_sequence=3,review=REF,valid_from=None,valid_until=None),project=w2.g['project'])


def kill_guard(runtime):
    pid = runtime.guard_token.backend_pid
    with engine.begin() as db:
        assert db.scalar(text('SELECT pg_terminate_backend(:pid)'),{'pid':pid}) is True
    with engine.begin() as db:
        assert db.scalar(text("SELECT count(*) FROM pg_locks WHERE pid=:pid AND locktype='advisory'"),{'pid':pid}) == 0


@pytest.mark.parametrize('invalidator', ['context','publication'])
@pytest.mark.parametrize('order', ['invalidation_first','acceptance_first','acceptance_first_unknown','lost_ack','not_delivered','unavailable'])
def test_y_orders_real_session_loss_stale_child_no_new_admission(w2,tmp_path,monkeypatch,invalidator,order):
    w2.reopen(); runtime=w2.runtime()
    qualified, resume, accepted, reply = Event(), Event(), Event(), Event()
    admitted=runtime.hook
    def hook(stage):
        admitted(stage)
        if stage=='admitted' and order in ('acceptance_first_unknown','not_delivered'):
            w2.authority.script_final(runtime.key,Usage())
        if stage == 'qualified_write_ready':
            qualified.set()
            assert resume.wait(10)
    runtime.hook=hook
    original_consume=runtime.consume
    def consume(body,timeout):
        ack=original_consume(body,timeout)
        if order.startswith('acceptance_first'):
            # The real sandboxed child sent this IPC request. Hold only its
            # reply, outside A, so the writer can now close and commit.
            accepted.set()
            assert reply.wait(10)
        return ack
    runtime.consume=consume
    before_ops=set(w2.authority.operations)
    control=None
    with ThreadPoolExecutor(max_workers=1) as pool:
        running=pool.submit(sandbox_run,runtime,tmp_path,response(runtime))
        try:
            assert qualified.wait(10)
            permit=runtime.permit
            assert permit.owner_generation == runtime.run.owner_generation == 1
            assert w2.clock.utcnow() < utc(permit.expires_at)
            with SessionLocal() as db:
                original_context=db.get(ResearchContext,w2.g['ctx']).closed_at
                admissions=list(db.execute(select(t.admission)).mappings())
            assert len(admissions) == 1
            kill_guard(runtime)
            if order.startswith('acceptance_first'):
                resume.set()
                assert accepted.wait(10)
            if order in ('lost_ack','not_delivered'):
                # Physically distinct, parent-only control socket. A dropped
                # connection before request delivery is different from a lost
                # reply after the authority journal/witness were fsynced.
                control=InvalidationServer(tmp_path/'control',w2.authority,
                    delivery='drop_after_fsync' if order=='lost_ack' else 'drop_before_request').start()
                client=InvalidationClient(tmp_path/'control')
                monkeypatch.setattr(w2.writer,'authority',SimpleNamespace(invalidate_v1=client.invalidate_v1,
                    resolve_invalidation_v1=w2.authority.resolve_invalidation_v1))
            elif order == 'unavailable':
                # Remove the real authoritative journal descriptor. A and the
                # child socket cannot manufacture a durable acknowledgement or
                # acceptance without it; no Boolean safety assertion stands in
                # for the independently observed zero endpoint events.
                w2.journal.close()
            if order in ('lost_ack','not_delivered','unavailable'):
                with pytest.raises(PortError): invalidate(w2,invalidator)
                with SessionLocal() as db:
                    assert db.get(ResearchContext,w2.g['ctx']).closed_at == original_context
                    assert db.scalar(select(t.invalidation.c.operation_id).where(~t.invalidation.c.operation_id.in_(before_ops))) is None
                assert all(b['state']=='PAUSED_UNKNOWN' for b in balances(runtime.key))
            else:
                invalidate(w2,invalidator)
                with SessionLocal() as db:
                    assert len(list(db.execute(select(t.invalidation).where(~t.invalidation.c.operation_id.in_(before_ops))))) == 1
                assert w2.authority.state == 'CLOSED'
            assert runtime.run.owner_generation == permit.owner_generation == 1
            assert runtime.permit == permit and w2.clock.utcnow() < utc(permit.expires_at)
            resume.set()
            reply.set()
            (completion,result),requests=running.result(timeout=20)
        finally:
            resume.set()
            reply.set()
            if control is not None:control.close()
    expected = 1 if order in ('acceptance_first','acceptance_first_unknown','not_delivered') else 0
    if control is not None:
        assert control.received == (order=='lost_ack')
        assert (control.durable_ack is not None) == (order=='lost_ack')
    assert w2.witness.accepted[runtime.key.fingerprint()] == expected
    assert requests.count('write') == 1
    assert not result.display and completion.display_state == 'SUPPRESSED'
    with SessionLocal() as db:
        assert len(list(db.execute(select(t.admission)))) == 1
        assert db.scalar(select(t.reservation.c.owner_generation)) == 1
    if expected and order not in ('acceptance_first_unknown','not_delivered'):
        assert all(b['settled_tokens']==2432 and b['settled_microusd']==8704 for b in balances(runtime.key))
    elif order in ('unavailable','acceptance_first_unknown','not_delivered'):
        assert all(b['held_tokens']==5120 and b['held_microusd']==22528 for b in balances(runtime.key))
    else:
        assert all(b['settled_tokens']==0 and b['held_tokens']==0 for b in balances(runtime.key))
