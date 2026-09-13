"""Real local mTLS/Unix sockets, independent process and fsync fault schedules."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import array
import json
import os
from pathlib import Path
import socket
import struct
import time
from unittest.mock import Mock

import pytest

from app.ai.proposals.codec import ProposalRejected, canonical
from app.ai.w2.records import PortError, make
from app.ai.w3.broker import production_admission
from app.ai.w3.broker.authority import Broker
from app.ai.w3.broker.journal import Journal, Observations, Witness
from app.ai.w3.broker.local import owned_synthetic_broker, Signal, _broker_main
from app.ai.w3.broker.bridge import BrokerClient
from app.ai.w3.broker.protocol import ZERO, digest, event, send, receive, socket_identity
from tests.ai.broker_fixtures import arm, frames, invalidation, metadata, response
from tests.ai.n1_sandbox import Sandbox


@pytest.mark.parametrize('evidence', [None, {}, {'measurement_kind': 'synthetic'},
    {'input_limit':4096, 'hidden_overhead':0, 'counting_version':'unverified'}, {'expired': True}])
def test_real_admission_always_stops_before_secret_dns_or_socket(monkeypatch, evidence):
    socket_guard = Mock(side_effect=AssertionError('native I/O prohibited'))
    dns_guard = Mock(side_effect=AssertionError('DNS prohibited'))
    monkeypatch.setattr(socket, 'socket', socket_guard)
    monkeypatch.setattr(socket, 'getaddrinfo', dns_guard)
    with pytest.raises(ProposalRejected, match='PROVIDER_DISABLED'):
        production_admission(evidence=evidence)
    assert socket_guard.call_count == dns_guard.call_count == 0


def test_one_tls_exchange_orders_independent_usage_before_dispatch():
    data = metadata()
    with owned_synthetic_broker(data.scope.deployment_ref, data.uid, data.run.started_at, response()) as fixture:
        client = fixture['client']; permit = arm(client, data)
        local = client.call('write', dict(permit=permit.document()))
        assert local['local_bytes'] > len(data.body) and local['provider_acceptance'] == local['final_usage'] == 'UNKNOWN'
        assert not any(e['kind'] == 'FINAL_USAGE' for e in client.refresh()['events'])
        final = client.call('receive', {})
        assert final['usage']['total_tokens'] == 2432
        view = client.refresh()
        assert view['coverage_complete'] and view['closed']
        kinds = [v['kind'] for v in view['events']]
        assert kinds == ['BOOTSTRAP', 'BINDING', 'PERMIT', 'INTENT', 'LOCAL_WRITE', 'PEER_RESPONSE', 'FINAL_USAGE', 'CLOSED']
        assert len(view['acknowledgements']) == len(kinds)
        assert fixture['counts']['requests'].value == fixture['counts']['connections'].value == 1
        with pytest.raises(PortError): client.call('write', dict(permit=permit.document()))
        assert fixture['counts']['requests'].value == 1
        retained = (fixture['root']/'b'/'journal').read_bytes() + (fixture['root']/'w'/'journal').read_bytes()
        secret = (fixture['root']/'b'/'synthetic-secret').read_bytes()
        assert b'Authorization:' not in retained and secret not in retained
        assert data.body not in retained and b'output_tokens_details' not in retained


def test_revocation_completed_while_old_sender_paused_before_A():
    data = metadata()
    with owned_synthetic_broker(data.scope.deployment_ref, data.uid, data.run.started_at, response(),
                               faults={'stage':'before_A'}) as fixture:
        client = fixture['client']; permit = arm(client, data)
        with ThreadPoolExecutor(1) as pool:
            old = pool.submit(client.call, 'write', dict(permit=permit.document()), 3)
            assert fixture['reached'].wait(2)
            context, request = invalidation(data)
            ack = client.invalidate_v1(context, request)
            resolution = make('InvalidationResolution', operation_id=request.operation_id,
                operation_digest=request.operation_digest, deployment_ref=data.scope.deployment_ref,
                barrier_digest=ack.event_digest, database_outcome='COMMITTED', transaction_evidence_ref='synthetic_tx')
            terminal = client.resolve_invalidation_v1(context, resolution, ('COMMITTED', 'synthetic_tx'))
            assert terminal.disposition == 'COMMITTED'
            fixture['resume'].set()
            with pytest.raises(PortError): old.result(timeout=3)
        assert fixture['counts']['bytes'].value == 0
        assert client.refresh()['state'] == 'CLOSED'


def test_paused_B_holds_A_and_cannot_acknowledge_invalidation():
    data = metadata()
    with owned_synthetic_broker(data.scope.deployment_ref, data.uid, data.run.started_at, response(),
                               faults={'stage':'inside_A'}) as fixture:
        client = fixture['client']; permit = arm(client, data)
        with ThreadPoolExecutor(1) as pool:
            old = pool.submit(client.call, 'write', dict(permit=permit.document()), 3)
            assert fixture['reached'].wait(2)
            context, request = invalidation(data)
            started = time.monotonic()
            with pytest.raises(PortError, match='COMMIT_UNKNOWN'): client.invalidate_v1(context, request, .2)
            assert time.monotonic() - started < 1
            assert not any(v.get('kind') == 'BARRIER' for v in frames(fixture['root']/'w'/'journal'))
            fixture['process'].kill(); fixture['process'].join(3)
            with pytest.raises(PortError): old.result(timeout=3)
        assert fixture['counts']['requests'].value == 0


@pytest.mark.parametrize('fault,limit', [({}, 37), ({'provider_timeout':True}, None),
    ({'drop_w_ack':'INTENT'}, None), ({'drop_control_ack':'write'}, None)])
def test_partial_timeout_or_lost_ack_is_uncertain_without_replay(fault, limit):
    data = metadata()
    with owned_synthetic_broker(data.scope.deployment_ref, data.uid, data.run.started_at, response(),
                               faults=fault, write_limit=limit) as fixture:
        client = fixture['client']; permit = arm(client, data)
        with pytest.raises(PortError):
            client.call('write', dict(permit=permit.document()))
            client.call('receive', {}, 7)
        first = fixture['counts']['connections'].value
        with pytest.raises(PortError): client.call('write', dict(permit=permit.document()))
        assert fixture['counts']['connections'].value == first == 1
        view = client.refresh()
        assert view['liability_tokens'] >= 5120 and view['liability_microusd'] >= 22528
        assert not any(e['kind'] == 'FINAL_USAGE' for e in view['events'])
        if fault.get('drop_w_ack'):
            assert not view['coverage_complete'] and view['unknown_upper_bound']
            assert any(v.get('kind') == 'INTENT' for v in frames(fixture['root']/'w'/'journal'))


@pytest.mark.parametrize('stage,kind,w_has_intent,has_final', [
    ('before_j_fsync','INTENT',False,False), ('after_j_fsync','INTENT',False,False),
    ('after_w_ack','INTENT',True,False), ('after_local_ack','INTENT',True,False),
    ('after_write',None,True,False), ('after_outcome',None,True,True)])
def test_process_crashes_preserve_acknowledged_intent_or_usage(stage, kind, w_has_intent, has_final):
    data = metadata()
    with owned_synthetic_broker(data.scope.deployment_ref, data.uid, data.run.started_at, response(),
                               faults={'stage':stage, 'kind':kind, 'crash':True}) as fixture:
        client = fixture['client']; permit = arm(client, data)
        with pytest.raises(PortError):
            client.call('write', dict(permit=permit.document()))
            client.call('receive', {})
        fixture['process'].join(3)
        assert fixture['process'].exitcode == 71 and fixture['reached'].is_set()
        primary = frames(fixture['root']/'b'/'journal')
        witnessed = frames(fixture['root']/'w'/'journal')
        assert any(v.get('kind') == 'BINDING' for v in primary)
        assert any(v.get('kind') == 'INTENT' for v in witnessed) == w_has_intent
        assert any(v.get('kind') == 'FINAL_USAGE' for v in witnessed) == has_final
        assert fixture['counts']['connections'].value == 1


def test_exact_record_duplicates_are_idempotent_and_conflicts_freeze(tmp_path):
    tmp_path.chmod(0o700)
    with_j = Journal(tmp_path/'w')
    try:
        witness = Witness(with_j, 'a'*64)
        first = event('stream', 1, ZERO, 'event', 'BOOTSTRAP', dict(measurement_kind='synthetic'))
        ack = witness.append(first)
        assert witness.append(first) == ack and len(with_j.rows) == 1
        with pytest.raises(PortError): witness.append(event('stream', 1, ZERO, 'event', 'BOOTSTRAP', dict(changed=True)))
        with pytest.raises(PortError): witness.status()
    finally: with_j.close()
    reopened = Journal(tmp_path/'w')
    try:
        with pytest.raises(PortError): Witness(reopened, 'a'*64).status()
    finally: reopened.close()


@pytest.mark.parametrize('oversized', [True, False])
def test_journal_restart_rejects_oversized_records_or_record_count(tmp_path, oversized):
    tmp_path.chmod(0o700)
    path = tmp_path/'journal'
    if oversized:
        raw = canonical({'synthetic':'x'*49152})
        path.write_bytes(struct.pack('!I', len(raw)) + raw)
    else:
        path.write_bytes((struct.pack('!I', 2) + b'{}') * 513)
    path.chmod(0o600)
    original = path.read_bytes()
    with pytest.raises(PortError): Journal(path)
    assert path.read_bytes() == original


@pytest.mark.parametrize('rollback', ['J','W','missing_ack'])
def test_storage_rollback_or_missing_ack_cannot_reopen(rollback, tmp_path):
    tmp_path.chmod(0o700)
    j, w = Journal(tmp_path/'j'), Journal(tmp_path/'w')
    try:
        witness = Witness(w, 'a'*64)
        observations = Observations(j, witness, 'stream')
        observations.append('first', 'BOOTSTRAP', {'authority_epoch':'old'})
        old_j, old_w = (tmp_path/'j').read_bytes(), (tmp_path/'w').read_bytes()
        observations.append('second', 'CLOSED', {'key_digest':'b'*64})
        if rollback == 'J': (tmp_path/'j').write_bytes(old_j)
        elif rollback == 'W': (tmp_path/'w').write_bytes(old_w)
        else:
            raw = canonical(j.rows[-1])
            with (tmp_path/'j').open('r+b') as out: out.truncate(j.size - len(raw) - 4)
        assert not observations.coverage()
        with pytest.raises(PortError): observations.append('new', 'BOOTSTRAP', {})
    finally: j.close(); w.close()


def test_control_rejects_descriptors_and_altered_authorization():
    data = metadata()
    with owned_synthetic_broker(data.scope.deployment_ref, data.uid, data.run.started_at, response()) as fixture:
        client = fixture['client']; permit = arm(client, data)
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
            connection.settimeout(1); connection.connect(client.path)
            descriptor = os.open('/dev/null', os.O_RDONLY)
            try:
                connection.sendmsg([canonical(dict(format='ra-broker-control/1',op='snapshot',data={}))],
                    [(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[descriptor]))])
                assert connection.recv(1) == b''
            finally: os.close(descriptor)
        tampered = {**permit.document(), 'body_digest':'f'*64}
        with pytest.raises(PortError): client.call('write', dict(permit=tampered))
        assert fixture['counts']['bytes'].value == 0


def test_sandbox_child_cannot_reach_control_credentials_journal_or_witness(tmp_path):
    data = metadata()
    with owned_synthetic_broker(data.scope.deployment_ref, data.uid, data.run.started_at, response()) as fixture:
        arm(fixture['client'], data)
        code, call, empty = (tmp_path/name for name in ('code','call','empty'))
        for path in (code, call, empty): path.mkdir()
        protected = [str(fixture['root']/name) for name in ('b/journal','w/journal','b/synthetic-secret','control/endpoint','tls/broker.key')]
        (code/'protected.json').write_text(json.dumps(protected))
        (code/'child.py').write_text('''import json,os,socket
from pathlib import Path
paths=json.loads(Path('/code/protected.json').read_text())
for p in paths:
    assert not Path(p).exists()
    try: os.open(p,os.O_WRONLY|os.O_CREAT,0o600)
    except OSError: pass
    else: raise AssertionError('protected path writable')
s=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET)
try: s.connect(paths[3])
except OSError: pass
else: raise AssertionError('control exposed')
assert 'DATABASE_URL' not in os.environ
print('isolated')
''')
        assert Sandbox(code, empty, call).run(timeout=5).strip() == b'isolated'
        assert fixture['counts']['requests'].value == 0


def _foreign_controller(path, result):
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
        connection.settimeout(1); connection.connect(path)
        try:
            send(connection, dict(format='ra-broker-control/1', op='snapshot', data={}), packet=True)
            denied = connection.recv(1) == b''
        except OSError:
            denied = True
        result.value = int(denied)


def test_same_uid_foreign_process_is_denied_by_kernel_peer_identity():
    data = metadata()
    with owned_synthetic_broker(data.scope.deployment_ref, data.uid, data.run.started_at, response()) as fixture:
        result = fixture['context'].RawValue('b', 0)
        foreign = fixture['context'].Process(target=_foreign_controller, args=(fixture['client'].path, result))
        foreign.start()
        try:
            foreign.join(3)
            assert not foreign.is_alive() and foreign.exitcode == 0 and result.value == 1
        finally:
            if foreign.is_alive(): foreign.kill(); foreign.join(3)
            foreign.close()


@pytest.mark.parametrize('changed', ['pid', 'inode'])
def test_local_peer_identity_tampering_fails_before_tls_application_write(changed):
    data = metadata()
    with owned_synthetic_broker(data.scope.deployment_ref, data.uid, data.run.started_at, response()) as fixture:
        # The fixture-owner process is not the authorized B client either.
        peer = fixture['peers']['provider']
        changes = {'pid':{'pid':os.getpid()}, 'inode':{'inode':(0,0)}}[changed]
        with pytest.raises((PortError, OSError)):
            replace(peer, **changes).connect()
        assert fixture['counts']['requests'].value == 0


@pytest.mark.parametrize('fault', ['wrong_provider_identity', 'wrong_provider_name'])
def test_broker_rejects_wrong_tls_certificate_pin_or_hostname(fault):
    data = metadata()
    with owned_synthetic_broker(data.scope.deployment_ref, data.uid, data.run.started_at, response(), faults={fault:True}) as fixture:
        with pytest.raises(PortError): arm(fixture['client'], data)
        assert fixture['counts']['requests'].value == 0


def test_complete_snapshot_pages_bind_the_same_journal_head(monkeypatch):
    data = metadata()
    with owned_synthetic_broker(data.scope.deployment_ref, data.uid, data.run.started_at, response()) as fixture:
        client = fixture['client']; permit = arm(client, data)
        client.call('write', dict(permit=permit.document())); client.call('receive', {})
        for _ in range(4):
            context, request = invalidation(data)
            client.invalidate_v1(context, request)
        view = client.refresh()
        assert len(view['events']) == len(view['acknowledgements']) == view['total'] == 12
        assert view['coverage_complete']
        original = client.call
        def changed_page(op, data, timeout=3):
            value = original(op, data, timeout)
            if op == 'snapshot' and data:
                value['state'] = 'RECOVERY_REQUIRED'
            return value
        monkeypatch.setattr(client, 'call', changed_page)
        with pytest.raises(PortError): client.refresh()
        assert not client.coverage() and not client.proves_response(data.key)


@pytest.mark.parametrize('rollback', [False, True])
def test_stopped_broker_restart_never_restores_old_permit_or_display_authority(rollback):
    data = metadata()
    with owned_synthetic_broker(data.scope.deployment_ref, data.uid, data.run.started_at, response()) as fixture:
        client = fixture['client']; permit = arm(client, data)
        checkpoint = (fixture['root']/'b'/'journal').read_bytes()
        client.call('write', dict(permit=permit.document())); client.call('receive', {})
        original = client.refresh()
        fixture['process'].kill(); fixture['process'].join(3)
        assert not fixture['process'].is_alive()
        if rollback: (fixture['root']/'b'/'journal').write_bytes(checkpoint)
        config = {**fixture['config'], 'control':str(fixture['root']/'control'/'restored'), 'faults':{}}
        ready, reached, resume = (Signal(fixture['context']) for _ in range(3))
        process = fixture['context'].Process(target=_broker_main, args=(config, fixture['peers']['provider'],
            fixture['peers']['witness'], ready, fixture['stop'], reached, resume, fixture['broker_pid']))
        process.start(); fixture['broker_pid'].value = process.pid
        try:
            assert ready.wait(3) and process.is_alive()
            restored = BrokerClient(config['control'], socket_identity(config['control']), process.pid, os.getuid())
            view = restored.refresh()
            assert view['state'] == 'RECOVERY_REQUIRED' and view['epoch'] != original['epoch']
            assert view['coverage_complete'] is not rollback
            with pytest.raises(PortError): restored.call('write', dict(permit=permit.document()))
            with pytest.raises(PortError): restored.qualify_consumption(permit)
            with pytest.raises(PortError): restored.call('bootstrap', dict(deployment=data.scope.deployment_ref, sql_empty=digest([])))
            assert fixture['counts']['requests'].value == 1
            if rollback:
                assert view['unknown_upper_bound'] and view['liability_tokens'] >= 5120
            else:
                assert any(e.kind == 'FINAL_USAGE' for e in restored.events(data.key))
        finally:
            process.kill(); process.join(3)
            assert not process.is_alive()
            process.close()


def test_missing_primary_storage_reports_gap_instead_of_zero():
    data = metadata()
    with owned_synthetic_broker(data.scope.deployment_ref, data.uid, data.run.started_at, response()) as fixture:
        client = fixture['client']; arm(client, data)
        (fixture['root']/'b'/'journal').unlink()
        view = client.refresh()
        assert not view['coverage_complete'] and view['unknown_upper_bound']
        assert view['coverage_deficit'] == 'J_W_ACK_COVERAGE_UNRESOLVED'
        assert view['liability_tokens'] >= 5120 and view['liability_microusd'] >= 22528


def test_exact_invalidation_redelivery_is_not_another_mutation_permission():
    data = metadata()
    with owned_synthetic_broker(data.scope.deployment_ref, data.uid, data.run.started_at, response()) as fixture:
        client = fixture['client']; arm(client, data)
        ctx, request = invalidation(data)
        ack = client.invalidate_v1(ctx, request)
        resolution = make('InvalidationResolution', operation_id=request.operation_id, operation_digest=request.operation_digest,
            deployment_ref=data.scope.deployment_ref, barrier_digest=ack.event_digest, database_outcome='COMMITTED', transaction_evidence_ref='tx')
        final = client.resolve_invalidation_v1(ctx, resolution, ('COMMITTED','tx'))
        count = len(client.refresh()['events'])
        assert client.invalidate_v1(ctx, request) == final
        assert client.resolve_invalidation_v1(ctx, resolution, ('COMMITTED','tx')) == final
        assert len(client.refresh()['events']) == count
        changed = make('InvalidationRequest', **{**{k:v for k,v in request.items() if k != 'format'},'reason':'RECOVERY'})
        with pytest.raises(PortError): client.invalidate_v1(ctx, changed)
        assert client.refresh()['state'] == 'RECOVERY_REQUIRED'
