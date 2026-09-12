"""Independent adapter deaths leave endpoint evidence outside the child."""
from threading import Event
import subprocess

import pytest
from sqlalchemy import select
from app.db.session import SessionLocal
from app.ai.proposals.adapter import Usage
from app.ai.w2 import schema as t
from app.ai.w2.execution import child_projection
from app.ai.w2.ipc import CallServer
from app.ai.w2.records import PortError
from tests.ai.test_w2_execution import response, balances
from tests.ai.w2_fixtures import (w2, rule_pair, knowledge_pair, subject_pair,
    two_intake_targets, zero_capabilities, KNOWN)  # noqa: F401


@pytest.mark.parametrize('boundary,accepted,known', [
    ('before_input',False,False),('before_marker_commit',False,False),('marker_ack',False,False),
    ('permit_ack',False,False),('after_endpoint_acceptance',True,False),
    ('after_acceptance_journal',True,False),('terminal_observation',True,True),
    ('before_record',True,True),('before_settlement_commit',True,True)])
def test_t7_real_child_kills_across_protocol_boundaries(w2,tmp_path,boundary,accepted,known):
    w2.reopen();rt=w2.runtime()
    paused,release=Event(),Event()
    def stop(stage):
        if stage==boundary:
            paused.set();assert release.wait(10)
    def call_hook(stage):
        if stage=='admitted':w2.authority.script_final(rt.key,KNOWN if known else Usage())
        # The call-only accounting RPC does not accept outcome or display data.
        if boundary=='before_record' and stage=='qualified_before_return':stop(boundary)
        stop(stage)
    rt.hook=call_hook
    w2.store.hook=stop
    w2.authority.hook=stop
    sandbox=child_projection(tmp_path/'process',rt,response(rt,usage=KNOWN if known else Usage()))
    server=CallServer(sandbox.call_socket/'endpoint',rt).start()
    child=subprocess.Popen(sandbox.command('child.py'),stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,env={'PATH':'/usr/bin:/bin'})
    try:
        assert paused.wait(10)
        # An OS kill of the fresh bwrap child, with its namespace/descriptor
        # boundary intact. The parent, journal, witness and DB survive it.
        child.kill();child.wait(timeout=3)
        assert child.returncode != 0
        release.set()
        server.thread.join(5)
        assert not server.thread.is_alive()
        w2.authority.hook=lambda _:None
        w2.store.hook=lambda _:None
        rt.hook=lambda _:None
        rt.close();result=rt.reconcile()
        assert w2.witness.accepted[rt.key.fingerprint()]==int(accepted)
        assert w2.authority.coverage()
        if not accepted:
            assert result.state=='ZERO' and result.refund_tokens==5120 and result.refund_microusd==22528
        elif known:
            assert result.state=='KNOWN' and result.settled_tokens==2432 and result.settled_microusd==8704
        else:
            assert result.state=='UNKNOWN' and result.held_tokens==5120 and result.held_microusd==22528
        with SessionLocal() as db:
            assert len(list(db.execute(select(t.core))))==1
            assert len(list(db.execute(select(t.completion))))==0
        assert all(b['calls']==1 for b in balances(rt.key))
        if rt.permit:
            with pytest.raises(PortError):w2.authority.consume_write_v1(rt.permit,rt.prepared.body)
        assert w2.witness.accepted[rt.key.fingerprint()]==int(accepted)
    finally:
        release.set()
        if child.poll() is None:child.kill()
        child.wait(timeout=3)
        child.stdout.close();child.stderr.close()
        server.close()
