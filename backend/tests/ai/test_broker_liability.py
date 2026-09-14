"""Independent recovery floors after SQL/J/W omissions, never new spend."""
import json
import os
import struct

import pytest
from sqlalchemy import select, text

from app.db.session import SessionLocal
from app.ai.proposals.codec import canonical
from app.ai.w2 import schema as t
from app.ai.w2.records import MAX_N, PortError, decode
from app.ai.w3.broker.bridge import BrokerClient, audit_inventory, run_synthetic
from app.ai.w3.broker.local import Signal, _broker_main, owned_synthetic_broker
from app.ai.w3.broker.protocol import digest, socket_identity
from tests.ai.broker_fixtures import arm, frames, metadata, response
from tests.ai.test_broker_w2 import connected, balances
from tests.ai.w2_fixtures import (w2, rule_pair, knowledge_pair, subject_pair,
    two_intake_targets, zero_capabilities)  # noqa: F401


def usage_response(i=6000, o=1200, *, cached=0, written=0, reasoning=400):
    value = json.loads(response().partition(b'\r\n\r\n')[2])
    value['usage'] = dict(input_tokens=i, output_tokens=o, total_tokens=i+o,
        input_tokens_details=dict(cached_tokens=cached, cache_write_tokens=written),
        output_tokens_details=dict(reasoning_tokens=reasoning))
    raw = canonical(value)
    return b'HTTP/1.1 200 Synthetic\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(raw)).encode() + b'\r\n\r\n' + raw


def floor(view):
    return view['liability_tokens'], view['liability_microusd']


@pytest.mark.parametrize('missing', ['J', 'SQL'])
def test_recovery_retains_settled_overcap_usage_without_reposting(w2, missing):
    with connected(w2, raw=usage_response()) as (runtime, client, fixture):
        result = run_synthetic(runtime, client)
        assert result.usage.total_tokens == 7200
        settled = runtime.store.current_settlement(runtime.key)
        assert (settled.settled_tokens, settled.settled_microusd) == (7200, 26400)
        witness = (fixture['root']/'w'/'journal').read_bytes()
        assert any(v['kind'] == 'FINAL_USAGE' and v['data']['usage']['total_tokens'] == 7200
                   for v in frames(fixture['root']/'w'/'journal'))
        if missing == 'J':
            (fixture['root']/'b'/'journal').unlink()
            view = client.refresh()
            assert floor(view) == (7200, 26400) and view['unknown_upper_bound']
            assert not view['coverage_complete'] and view['state'] == 'RECOVERY_REQUIRED'
        else:
            with SessionLocal() as db, db.begin():
                db.execute(text('TRUNCATE research_ai_reservation CASCADE'))
        for _ in range(2):
            report = audit_inventory(runtime, client)
            assert not report['complete'] and report['unknown_upper_bound']
            assert (report['retained_tokens'], report['retained_microusd']) == (7200, 26400)
        assert all(b['state'] == 'PAUSED_UNKNOWN' for b in balances())
        if missing == 'J':
            assert runtime.store.current_settlement(runtime.key) == settled
            with SessionLocal() as db:
                assert len(list(db.execute(select(t.posting)))) == 4
                assert {decode('Completion', bytes(raw)).display_state
                        for raw in db.scalars(select(t.completion.c.record))} == {'SUPPRESSED'}
        assert (fixture['root']/'w'/'journal').read_bytes() == witness
        client.refresh()  # Fresh physical observations are not SQL/display qualification.
        with pytest.raises(PortError):
            runtime._current_snapshot(runtime.key.scope.project_ref, runtime.key.scope.context_ref, 'complete')
        with pytest.raises(PortError): client.call('write', dict(permit=runtime.permit.document()))
        assert fixture['counts']['requests'].value == 1


@pytest.mark.parametrize('i,o,written,expected', [
    (5120, 0, 0, (5120, 22528)),
    (5121, 0, 0, (5121, 22528)),
    (1, 1877, 1, (5120, 22528)),  # cost 22526.5 rounds once to 22527
    (2, 1877, 0, (5120, 22528)),  # cost exactly 22528
    (2, 1877, 1, (5120, 22529)),  # cost 22528.5 rounds once to 22529
])
def test_token_and_cost_recovery_boundaries_are_independent(i, o, written, expected):
    data = metadata()
    with owned_synthetic_broker(data.scope.deployment_ref, data.uid, data.run.started_at,
                               usage_response(i, o, written=written, reasoning=0)) as fixture:
        client = fixture['client']; permit = arm(client, data)
        client.call('write', dict(permit=permit.document())); client.call('receive', {})
        assert floor(client.refresh()) == expected
        (fixture['root']/'b'/'journal').unlink()
        for _ in range(2):
            view = client.refresh()
            assert floor(view) == expected and view['unknown_upper_bound'] and not view['coverage_complete']
        assert fixture['counts']['requests'].value == 1


@pytest.mark.parametrize('missing', ['J_tail', 'J_all', 'local_ack', 'earlier_ack', 'W'])
def test_fenced_restart_retains_available_independent_floor(missing):
    data = metadata()
    with owned_synthetic_broker(data.scope.deployment_ref, data.uid, data.run.started_at, usage_response()) as fixture:
        client = fixture['client']; permit = arm(client, data)
        client.call('write', dict(permit=permit.document())); client.call('receive', {})
        original = client.refresh()
        assert floor(original) == (7200, 26400)
        path = fixture['root']/'b'/'journal'
        retained = frames(path)
        fixture['process'].kill(); fixture['process'].join(3)
        assert not fixture['process'].is_alive()  # fence before restoring any J bytes
        if missing == 'J_all': path.unlink()
        elif missing == 'W': (fixture['root']/'w'/'journal').unlink()
        else:
            prefix = bytearray()
            for row in retained:
                if missing == 'J_tail' and row.get('kind') == 'FINAL_USAGE': break
                raw = canonical(row); prefix.extend(struct.pack('!I', len(raw)) + raw)
                if missing == 'local_ack' and row.get('kind') == 'FINAL_USAGE': break
                if missing == 'earlier_ack' and row.get('kind') == 'PEER_RESPONSE': break
            path.write_bytes(prefix)
        config = {**fixture['config'], 'control':str(fixture['root']/'control'/'restored'), 'faults':{}}
        ready, reached, resume = (Signal(fixture['context']) for _ in range(3))
        process = fixture['context'].Process(target=_broker_main, args=(config, fixture['peers']['provider'],
            fixture['peers']['witness'], ready, fixture['stop'], reached, resume, fixture['broker_pid']))
        process.start(); fixture['broker_pid'].value = process.pid
        try:
            assert ready.wait(3) and process.is_alive()
            restored = BrokerClient(config['control'], socket_identity(config['control']), process.pid, os.getuid())
            for _ in range(2):
                view = restored.refresh()
                assert view['epoch'] != original['epoch'] and view['state'] == 'RECOVERY_REQUIRED'
                assert not view['coverage_complete'] and view['unknown_upper_bound']
                assert floor(view) == (7200, 26400) and view['liability_key_digest'] == data.key.fingerprint()
            with pytest.raises(PortError): restored.call('write', dict(permit=permit.document()))
            with pytest.raises(PortError): restored.qualify_consumption(permit)
            with pytest.raises(PortError): restored.call('bootstrap', dict(deployment=data.scope.deployment_ref, sql_empty=digest([])))
            assert fixture['counts']['requests'].value == 1
        finally:
            process.kill(); process.join(3)
            assert not process.is_alive()
            process.close()


def test_unrepresentable_cost_retains_evidence_and_rejects_numeric_fallback(w2):
    with connected(w2, raw=usage_response(MAX_N, 0, reasoning=0)) as (runtime, client, fixture):
        run_synthetic(runtime, client)
        for path in (fixture['root']/'b'/'journal', fixture['root']/'w'/'journal'):
            assert any(v.get('kind') == 'FINAL_USAGE' and v['data']['usage']['total_tokens'] == MAX_N
                       for v in frames(path))
        with pytest.raises(PortError): client.refresh()
        with pytest.raises(PortError): audit_inventory(runtime, client)
        assert not client.coverage()
        assert all(b['state'] == 'PAUSED_UNKNOWN' and b['held_tokens'] >= 5120 and b['held_microusd'] >= 22528
                   for b in balances())
        with pytest.raises(PortError): client.qualify_consumption(runtime.permit)
        assert fixture['counts']['requests'].value == 1


@pytest.mark.parametrize('altered', ['other_call', 'invalid_number'])
def test_invalid_recovery_report_pauses_without_erasing_known_floor(w2, monkeypatch, altered):
    with connected(w2, raw=usage_response()) as (runtime, client, fixture):
        run_synthetic(runtime, client)
        original = client.call
        known = dict(client.retained_liability)
        settled = runtime.store.current_settlement(runtime.key)
        def tampered(op, data, timeout=3):
            value = original(op, data, timeout)
            if op == 'snapshot':
                if altered == 'other_call': value['liability_key_digest'] = 'f'*64
                else: value['liability_tokens'] = True
            return value
        monkeypatch.setattr(client, 'call', tampered)
        with pytest.raises(PortError): audit_inventory(runtime, client)
        assert not client.coverage() and client.retained_liability == known
        assert (known['tokens'], known['microusd']) == (7200, 26400)
        assert runtime.store.current_settlement(runtime.key) == settled
        assert all(b['state'] == 'PAUSED_UNKNOWN' for b in balances())
        assert fixture['counts']['requests'].value == 1
