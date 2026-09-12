"""N1 child/process acceptance, independently observed at the parent endpoint."""
from dataclasses import asdict
from pathlib import Path
import json
import subprocess

import pytest
from sqlalchemy import select
from app.db.session import SessionLocal
from app.ai.proposals.codec import canonical
from app.ai.w2 import schema as t
from app.ai.w2.execution import child_projection, child_outcome, execute_fake
from app.ai.w2.ipc import CallServer
from tests.ai.w2_fixtures import (w2, rule_pair, knowledge_pair, subject_pair,
    two_intake_targets, zero_capabilities, KNOWN)  # noqa: F401


def response(runtime, *, usage=KNOWN, change=None):
    doc = json.loads(runtime.prepared.prepared.payload)
    output = dict(protocol='ra-ai-proposal-output/1', request_ref=doc['request_ref'], status='suggestions',
        suggestions=[dict(suggestion_ref='s_1', type='REVIEW_CANDIDATE', candidate_ref=doc['candidates'][0]['ref'],
            rule_refs=[doc['rules'][0]['ref']], gap_refs=[], reason_code='CANDIDATE_REVIEW_ONLY', uncertainty_codes=['NOT_EXECUTED'])],
        refusal_code=None)
    u = None if usage.state != 'known' else dict(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens, input_tokens_details=dict(cached_tokens=usage.cached_input_tokens,
        cache_write_tokens=usage.cache_write_tokens), output_tokens_details=dict(reasoning_tokens=usage.reasoning_tokens))
    body = dict(object='response', id='resp_independent', status='completed', model='gpt-5.6-terra', usage=u,
        error=None, incomplete_details=None, output=[dict(type='message', id='msg_independent', role='assistant',
        status='completed', content=[dict(type='output_text', text=canonical(output).decode(), annotations=[], logprobs=[])])])
    if change:
        body.update(change)
    raw = canonical(body)
    return b'HTTP/1.1 200 Synthetic\r\nContent-Type: application/json\r\nContent-Length: '+str(len(raw)).encode()+b'\r\n\r\n'+raw


def sandbox_run(runtime, root, raw):
    sandbox = child_projection(root/'process', runtime, raw)
    server = CallServer(sandbox.call_socket/'endpoint', runtime).start()
    try:
        # Same enforced sandbox command; bounded diagnostic capture is restricted
        # to this independently authored synthetic fixture, with no admin DSN.
        with subprocess.Popen(sandbox.command('child.py'), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, env={'PATH':'/usr/bin:/bin'}) as child:
            stdout, stderr = child.communicate(timeout=30)
        assert len(stdout)+len(stderr) <= 8192
        assert child.returncode == 0, stderr.decode()
        assert not stderr
        result = child_outcome(stdout, runtime)
        return runtime.complete_v1(result), server.requests
    finally:
        server.close()


def balances(key):
    with SessionLocal() as db:
        return list(db.execute(select(t.balance).join(t.policy).where(t.policy.c.account_ref == key.scope.account_ref)).mappings())


def test_x1_y5_real_sandbox_success_and_atomic_postings(w2, tmp_path):
    w2.reopen()
    runtime = w2.runtime()
    (completion, result), requests = sandbox_run(runtime,tmp_path,response(runtime))
    assert result.code is None, (result, requests)
    assert result.usage == KNOWN and len(result.display) == 1
    assert completion.display_state == 'ELIGIBLE_NOW'
    assert w2.authority.witness.accepted[runtime.key.fingerprint()] == 1
    assert w2.authority.coverage()
    assert requests.count('write') == 1
    for b in balances(runtime.key):
        assert (b['settled_tokens'],b['settled_microusd'],b['held_tokens'],b['held_microusd'],b['calls']) == (2432,8704,0,0,1)
    with SessionLocal() as db:
        rows = list(db.execute(select(t.posting)).mappings())
        assert len(rows) == 4
        assert {(p['dimension'],p['settled_delta'],p['held_delta']) for p in rows} == {('tokens',2432,-5120),('microusd',8704,-22528)}
        assert len(list(db.execute(select(t.completion)))) == 1


@pytest.mark.parametrize('drift', [{'store':True},{'model':'unapproved'},{'output':[]}])
def test_terminal_usage_survives_later_profile_or_display_failure(w2,tmp_path,drift):
    w2.reopen(); runtime=w2.runtime()
    (completion,result), _ = sandbox_run(runtime,tmp_path,response(runtime,change=drift))
    assert result.code is not None and not result.display
    assert result.usage == KNOWN and completion.display_state == 'SUPPRESSED'
    assert all(b['settled_tokens'] == 2432 and b['settled_microusd'] == 8704 and b['held_tokens'] == 0 for b in balances(runtime.key))
    assert w2.witness.accepted[runtime.key.fingerprint()] == 1
    with SessionLocal() as db:
        assert db.scalar(select(t.event.c.event_id).where(t.event.c.kind=='TERMINAL_PROJECTION')) is not None


def test_internal_execute_fake_entry_uses_sandbox_and_cleans_projection(w2,tmp_path):
    w2.reopen();rt=w2.runtime()
    completion,result=execute_fake(rt,response(rt),owned_directory=tmp_path)
    assert result.code is None and result.usage==KNOWN and len(result.display)==1
    assert completion.display_state=='ELIGIBLE_NOW' and w2.witness.accepted[rt.key.fingerprint()]==1
    assert not list(tmp_path.glob('w2-call-*'))


def test_independent_usage_disagreement_suppresses_display_without_losing_debit(w2,tmp_path):
    from app.ai.proposals.adapter import Usage
    w2.reopen();rt=w2.runtime()
    rt.hook=lambda stage:w2.authority.script_final(rt.key,Usage('known',1000,100,0,0,0,1100)) if stage=='admitted' else None
    completion,result=execute_fake(rt,response(rt),owned_directory=tmp_path)
    assert result.code=='AUDIT_UNAVAILABLE' and not result.display and result.usage==KNOWN
    assert completion.display_state=='SUPPRESSED' and w2.witness.accepted[rt.key.fingerprint()]==1
    assert all(b['settled_tokens']==1100 and b['settled_microusd']==3200 and b['held_tokens']==0 for b in balances(rt.key))
