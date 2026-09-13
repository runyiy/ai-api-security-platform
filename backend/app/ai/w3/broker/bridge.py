"""Controller-only W2 seam. No credential, TLS socket, J or W descriptor here."""
from hashlib import sha256
import socket
import time
from uuid import uuid4

from dataclasses import dataclass

from app.ai.proposals.adapter import Usage
from app.ai.proposals.codec import canonical
from app.ai.w2.records import make, decode, require, PortError, usage_view, stamp
from .protocol import acknowledgement, digest, peer_identity, receive, send, socket_identity, validate_event, ZERO


class BrokerClient:
    def __init__(self, path, inode, pid, uid):
        self.path, self.inode, self.pid, self.uid = str(path), inode, pid, uid
        self.available = False
        self.view = None
        self.calls, self.observations, self.core_evidence = {}, {}, {}

    def call(self, op, data, timeout=3):
        require(type(timeout) in (int, float) and 0 < timeout <= 30, 'DEADLINE_EXCEEDED')
        try:
            require(socket_identity(self.path) == self.inode, 'AUTHORITY_UNAVAILABLE')
            with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
                connection.settimeout(timeout)
                connection.connect(self.path)
                peer_identity(connection, self.uid, self.pid)
                send(connection, dict(format='ra-broker-control/1', op=op, data=data), packet=True)
                reply = receive(connection, packet=True)
                require(set(reply) == {'ok', 'data'} and reply['ok'] is True, 'OBSERVER_UNAVAILABLE')
                return reply['data']
        except Exception:
            self.available = False
            raise PortError('OBSERVER_UNAVAILABLE') from None

    def refresh(self):
        # A failed decode/page/identity check must not leave a previous snapshot
        # usable as current coverage, even when the IPC request itself succeeded.
        self.available = False
        until = time.monotonic() + 3
        view = self.call('snapshot', {})
        require(view['format'] == 'ra-broker-snapshot/1' and type(view['coverage_complete']) is bool)
        events, acks = view['events'], view['acknowledgements']
        require(view['offset'] == 0 and type(view['total']) is int and 0 <= view['total'] <= 256)
        while view['next'] < view['total']:
            page = self.call('snapshot', dict(offset=view['next'], head=view['head']), max(0, until - time.monotonic()))
            require(all(page[field] == view[field] for field in ('head', 'total', 'epoch', 'generation', 'state', 'closed', 'coverage_complete'))
                    and page['offset'] == view['next'] and page['next'] > page['offset'], 'OBSERVER_UNAVAILABLE')
            events.extend(page['events']); acks.extend(page['acknowledgements'])
            view['next'] = page['next']
        require(view['next'] == view['total'] == len(events), 'OBSERVER_UNAVAILABLE')
        require(type(events) is list and type(acks) is list and len(events) <= 256 and len(acks) <= len(events))
        for index, value in enumerate(events):
            validate_event(value)
            require(value['sequence'] == index + 1 and value['previous'] == (events[index-1]['digest'] if index else ZERO))
            if index < len(acks):
                require(acks[index] == acknowledgement(value, acks[index]['witness']), 'OBSERVER_UNAVAILABLE')
        if view['coverage_complete']: require(len(acks) == len(events), 'OBSERVER_UNAVAILABLE')
        self.view = view
        self.calls, self.observations, self.core_evidence = {}, {}, {}
        binding = next((v['data'] for v in events if v['kind'] == 'BINDING'), None)
        if binding is None:
            self.available = True
            return view
        key = decode('ReservationKey', canonical(binding['key']))
        permit_row = next((v for v in events if v['kind'] == 'PERMIT'), None)
        permit = decode('SendPermit', canonical(permit_row['data']['permit'])) if permit_row else None
        self.core_evidence[key.fingerprint()] = binding
        self.calls[key.fingerprint()] = dict(key=key, closed=view['closed'])
        for value in events:
            kind = value['kind']
            if kind not in ('FINAL_USAGE', 'CLOSED'): continue
            # Unacknowledged tails remain visible evidence but cannot settle.
            if value['sequence'] > len(acks): continue
            data = value['data']
            event = make('Observation', key_digest=key.fingerprint(), call_ref=key.call_ref,
                event_id=sha256((value['stream'] + ':' + value['event_id']).encode()).hexdigest(),
                observer_epoch=permit.observer_epoch if permit else events[0]['data']['authority_epoch'],
                authority_epoch=permit.authority_epoch if permit else events[0]['data']['authority_epoch'],
                acceptance_generation=permit.acceptance_generation if permit else 1, sequence=value['sequence'],
                owner_generation=binding['run']['owner_generation'], kind='FINAL_USAGE' if kind == 'FINAL_USAGE' else 'STREAM_CLOSED',
                permit_id=permit.permit_id if permit else None, body_digest=binding['core']['body_digest'],
                observed_at=data['observed_at'], terminal=data['terminal'] if kind == 'FINAL_USAGE' else 'NONE',
                usage=decode('UsageView', canonical(data['usage'])) if kind == 'FINAL_USAGE' else usage_view(Usage()),
                evidence_digest=value['digest'])
            self.observations[event.event_id] = event
        self.available = True
        return view

    def coverage(self):
        # A verified immutable snapshot, with no IPC inside accounting SQL locks.
        return self.available and self.view is not None and self.view['coverage_complete']

    def events(self, key):
        return tuple(e for e in self.observations.values() if e.key_digest == key.fingerprint())

    def proves_zero(self, key):
        return False  # This increment supplies no automatic no-send refund proof.

    def proves_response(self, key):
        return self.coverage() and key.fingerprint() in self.calls and any(
            value['kind'] == 'PEER_RESPONSE' for value in self.view['events'])

    def proves_final(self, key, event):
        return self.proves_response(key) and event == self.observations.get(event.event_id) and event.kind == 'FINAL_USAGE'

    def observe_v1(self, read, event, timeout=3):
        require(self.coverage() and event == self.observations.get(event.event_id)
                and self.calls[event.key_digest]['key'].scope == read.scope, 'OBSERVER_UNAVAILABLE')
        return make('ObservationAck', event_id=event.event_id, sequence=event.sequence, event_digest=event.fingerprint())

    def register_reserved(self, prepared, run, reservation):
        self.call('bind', dict(key=prepared.key.document(), core=prepared.core.document(), run=run.document(),
            reservation=reservation.document(), manifest_digest=prepared.manifest_digest))
        self.refresh()

    def register_call(self, prepared, run, admission, reservation):
        require(prepared.key == admission.key == reservation.key and run.owner_generation == admission.owner_generation)

    def qualify_admission(self, timeout=3):
        self.refresh()
        require(self.coverage() and self.view['state'] == 'OPEN', 'OBSERVER_UNAVAILABLE')

    def begin_acceptance_v1(self, run, admission, body_digest, timeout=3):
        return decode('AcceptanceTicket', canonical(self.call('ticket', dict(run=run.document(),
            admission=admission.document(), body_digest=body_digest), min(3, timeout))))

    def issue(self, run, admission, ticket, marker_event_id, guard_epoch, timeout=3):
        value = self.call('authorize', dict(format='ra-broker-authorization/1', run=run.document(),
            admission=admission.document(), ticket=ticket.document(), marker=marker_event_id, guard_epoch=guard_epoch), min(3, timeout))
        return decode('SendPermit', canonical(value))

    def qualify_consumption(self, permit, timeout=3):
        self.refresh()
        require(permit is not None and self.proves_response(permit.key) and self.view['state'] == 'OPEN'
                and (permit.authority_epoch, permit.acceptance_generation) == (self.view['epoch'], self.view['generation']),
                'CONTEXT_CHANGED')

    def close_call(self, key, timeout=3):
        self.call('close', dict(key=key.document()), min(3, timeout))
        self.refresh()
        return self.events(key)

    def invalidate_v1(self, context, request, timeout=3):
        try:
            return decode('InvalidationAck', canonical(self.call('invalidate',
                dict(context=context.document(), request=request.document()), min(3, timeout))))
        except PortError: raise PortError('COMMIT_UNKNOWN') from None

    def resolve_invalidation_v1(self, context, resolution, verified_outcome, timeout=3):
        try:
            return decode('InvalidationAck', canonical(self.call('disposition', dict(context=context.document(),
                resolution=resolution.document(), verified_outcome=list(verified_outcome)), min(3, timeout))))
        except PortError: raise PortError('COMMIT_UNKNOWN') from None

    def suspend(self):
        self.available = False


def initialize_synthetic(runtime, client):
    """Fresh empty TEST deployment only; never a restore/reopen operation."""
    from sqlalchemy import select
    from app.ai.w2 import schema as t
    require(type(client) is BrokerClient and runtime.reservation is None)
    with runtime.guard.read(runtime.deadline):
        with runtime.store.transaction(runtime.deadline) as db:
            rows = list(db.execute(select(t.reservation.c.key_digest)))
            deployment = db.execute(select(t.deployment)).mappings().one()
            require(not rows and deployment['deployment_ref'] == runtime.key.scope.deployment_ref
                    and deployment['state'] == 'FAKE_ACTIVE'
                    and db.scalar(select(t.recovery.c.decision_id).limit(1)) is None, 'CONTEXT_CHANGED')
        client.call('bootstrap', dict(deployment=runtime.key.scope.deployment_ref, sql_empty=digest([])))
    runtime.observer = client


def audit_inventory(runtime, client):
    """Read independent evidence outside SQL; omissions pause, never reopen."""
    from sqlalchemy import select
    from app.ai.w2 import schema as t
    view = client.refresh()
    with runtime.store.transaction() as db:
        rows = list(db.execute(select(t.core.c.key_digest, t.core.c.core_bytes).join(t.reservation)))
        markers = list(db.execute(select(t.marker.c.key_digest, t.marker.c.event_id)))
    expected = {k: canonical(v['core']) for k, v in client.core_evidence.items()}
    matching = {r.key_digest: bytes(r.core_bytes) for r in rows} == expected
    for value in view['events']:
        if value['kind'] == 'PERMIT':
            permit = value['data']['permit']
            matching = matching and any(
                row.key_digest == decode('ReservationKey', canonical(permit['key'])).fingerprint()
                and row.event_id == permit['marker_event_id'] for row in markers)
    if not matching or not client.coverage():
        runtime.store.writer.pause_admission()
        client.available = False
        return dict(complete=False, deficit='SQL_J_W_INVENTORY_UNRESOLVED', unknown_upper_bound=True,
                    retained_tokens=5120, retained_microusd=22528)
    return dict(complete=True, deficit=None, unknown_upper_bound=False)


@dataclass(frozen=True)
class ObservationResult:
    terminal: str = 'UNKNOWN'
    usage: Usage = Usage()
    dispatch_completed: bool = False
    measurement_kind: str = 'synthetic'


def run_synthetic(runtime, client, *, dispatch=lambda observation: None):
    """One observation/accounting increment, with no new display/cache issuer.

    The existing sandboxed W1 result path remains unchanged. This seam returns
    content-free usage/disposition only. Its downstream failure hook cannot
    supply or replace the independently observed terminal usage.
    """
    require(type(client) is BrokerClient and runtime.observer is client, 'AUTHORITY_UNAVAILABLE')
    outcome = ObservationResult()
    try:
        if runtime.reservation is None: runtime.reserve_v1()
        runtime.admit(runtime.receipt, runtime.prepared.core.w1_binding_digest, runtime.deadline.remaining())
        runtime._current_snapshot(runtime.key.scope.project_ref, runtime.key.scope.context_ref, 'before_secret')
        client.call('prepare', dict(body=runtime.prepared.body.decode('utf-8')), runtime.deadline.remaining(3))
        runtime._current_snapshot(runtime.key.scope.project_ref, runtime.key.scope.context_ref, 'after_tls')
        try:
            runtime.begin_send()
            runtime._current_snapshot(runtime.key.scope.project_ref, runtime.key.scope.context_ref, 'write_ready')
            client.call('write', dict(permit=runtime.permit.document()), runtime.deadline.remaining(3))
        finally:
            runtime.end_send()
        reply = client.call('receive', {}, runtime.deadline.remaining())
        client.refresh()
        usage = decode('UsageView', canonical(reply['usage']))
        outcome = ObservationResult(reply['terminal'], Usage(**{k: v for k, v in usage.items() if k not in ('format', 'mapping')}))
        dispatch(reply)  # Independent final metadata is already J/W durable.
        outcome = ObservationResult(outcome.terminal, outcome.usage, True)
    except Exception:
        pass  # Observed usage survives failed/cancelled downstream consumers.
    finally:
        try:
            client.close_call(runtime.key)
            before = tuple(client.events(runtime.key))
            runtime.reconcile()
            client.refresh()
            require(client.coverage() and tuple(client.events(runtime.key)) == before, 'OBSERVER_UNAVAILABLE')
            runtime.store.finish(runtime.run, runtime.key, client)
            settlement = runtime.store.current_settlement(runtime.key)
            runtime.store.persist_completion(runtime.run, runtime.key, make('Completion',
                key_digest=runtime.key.fingerprint(), completion_id=uuid4().hex,
                outcome_code=None if outcome.dispatch_completed else 'AUDIT_UNAVAILABLE',
                delivery='responded' if outcome.terminal != 'UNKNOWN' else 'unknown',
                usage=usage_view(outcome.usage), settlement_ref=settlement.settlement_id if settlement else None,
                display_state='SUPPRESSED', evaluated_at=stamp(runtime.deadline.clock.utcnow()),
                expires_at=runtime.prepared.core.expires_at))
        except Exception:
            runtime.store.mark_unknown(runtime.run, runtime.key)
    # No complete_v1/display receipt is produced for this new transport seam.
    return outcome
