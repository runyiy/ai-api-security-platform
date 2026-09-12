"""N1 parent-owned fake endpoint, durable barrier journal and independent witness.

This is an explicitly constructed test harness, never a live service. Sandboxed
children receive only a call endpoint, never this object's control interface or
its filesystem. Journal agreement alone does not establish endpoint coverage.
"""
from collections import Counter
from datetime import timedelta
from contextlib import contextmanager
from hashlib import sha256
import json
import fcntl
import os
from pathlib import Path
import struct
import threading
from uuid import uuid4

from app.ai.proposals.adapter import Usage, ZERO_USAGE
from app.ai.proposals.codec import canonical, bounded_json
from .records import make, decode, require, PortError, stamp, utc, usage_view


class Journal:
    """Exclusive, no-follow, append-only length frames; fsync before any ack."""
    def __init__(self, path):
        self.path = Path(path)
        require(not self.path.is_symlink(), 'OBSERVER_UNAVAILABLE')
        self.fd = os.open(self.path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        self.entries = []
        self.head = '0'*64
        self.bytes = 0
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            size = os.fstat(self.fd).st_size
            require(size <= 64*1024*1024, 'OBSERVER_UNAVAILABLE')
            os.lseek(self.fd, 0, os.SEEK_SET)
            while self.bytes < size:
                header = os.read(self.fd, 4)
                require(len(header) == 4, 'OBSERVER_UNAVAILABLE')
                length = struct.unpack('!I', header)[0]
                require(0 < length <= 8192, 'OBSERVER_UNAVAILABLE')
                raw = os.read(self.fd, length)
                require(len(raw) == length, 'OBSERVER_UNAVAILABLE')
                row = bounded_json(raw, maximum=8192, depth=8, nodes=2048)
                require(set(row) == {'sequence', 'previous', 'payload', 'digest'}, 'OBSERVER_UNAVAILABLE')
                digest = sha256(canonical({k: v for k, v in row.items() if k != 'digest'})).hexdigest()
                require(row['sequence'] == len(self.entries)+1 and row['previous'] == self.head
                        and row['digest'] == digest, 'OBSERVER_UNAVAILABLE')
                self.entries.append(row)
                self.head = digest
                self.bytes += length+4
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise

    def append(self, payload):
        require(self.fd is not None and len(self.entries) < 65536, 'OBSERVER_UNAVAILABLE')
        require(len(canonical(payload)) <= 4096, 'LIMIT_EXCEEDED')
        row = dict(sequence=len(self.entries)+1, previous=self.head, payload=payload)
        row['digest'] = sha256(canonical(row)).hexdigest()
        raw = canonical(row)
        require(self.bytes + len(raw)+4 <= 64*1024*1024, 'OBSERVER_UNAVAILABLE')
        framed = struct.pack('!I', len(raw)) + raw
        # A partial append deliberately remains a corrupt tail, never truncated
        # into a silently valid older history during restart.
        require(os.write(self.fd, framed) == len(framed), 'OBSERVER_UNAVAILABLE')
        os.fsync(self.fd)
        self.entries.append(row)
        self.head = row['digest']
        self.bytes += len(framed)
        return row

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class EndpointWitness:
    """Independently retained endpoint counts and journal high-water evidence.

Owned by the outer harness, outside authority/result/accounting storage. The
authority is given this narrow observation sink; children never receive it.
"""
    def __init__(self, path):
        self.journal = Journal(path)
        self.accepted = Counter()
        self.acceptance_ids = set()
        self.head_sequence, self.head_digest = 0, '0'*64
        for row in self.journal.entries:
            self._apply(row['payload'])

    def _apply(self, event):
        if event['kind'] == 'ACCEPTED':
            require(event['event_id'] not in self.acceptance_ids, 'OBSERVER_UNAVAILABLE')
            self.acceptance_ids.add(event['event_id'])
            self.accepted[event['key_digest']] += 1
        else:
            require(event['kind'] == 'HEAD' and event['sequence'] > self.head_sequence, 'OBSERVER_UNAVAILABLE')
            self.head_sequence, self.head_digest = event['sequence'], event['digest']

    def accepted_once(self, key_digest, event_id, body_digest):
        event = dict(kind='ACCEPTED', key_digest=key_digest, event_id=event_id, body_digest=body_digest)
        self.journal.append(event)
        self._apply(event)

    def head(self, row):
        event = dict(kind='HEAD', sequence=row['sequence'], digest=row['digest'])
        self.journal.append(event)
        self._apply(event)

    def close(self):
        self.journal.close()


class FakeAuthority:
    def __init__(self, deployment, journal, witness, clock, *, available=True):
        self.deployment, self.journal, self.witness, self.clock = deployment, journal, witness, clock
        self.A = threading.Lock()
        self.available = available
        self.epoch = uuid4().hex
        self.observer_epoch = uuid4().hex
        self.generation = 1
        self.state = 'RECOVERY_REQUIRED'
        self.operations, self.tickets, self.permits, self.calls = {}, {}, {}, {}
        self.observations = {}
        self.core_evidence = {}
        self.endpoint_bodies = {}  # Ephemeral synthetic bodies; never headers.
        self.last_wall = None
        self.hook = lambda stage: None  # Parent fault scheduler only.
        for row in journal.entries:
            payload = row['payload']
            self.generation = max(self.generation, payload.get('acceptance_generation', 1))
            if payload.get('format') == 'ra-w2-invalidation-ack/1':
                ack = decode('InvalidationAck', canonical(payload))
                self.operations[ack.operation_id] = ack
            elif payload.get('format') == 'ra-w2-call-evidence/1':
                self._restore_call(payload)
            elif payload.get('format') == 'ra-w2-admission/1':
                admission = decode('Admission', canonical(payload))
                call = self.calls.get(admission.key.fingerprint())
                require(call is not None and call['admission'] is None, 'OBSERVER_UNAVAILABLE')
                call['admission'] = admission
            elif payload.get('format') == 'ra-w2-send-permit/1':
                permit=decode('SendPermit',canonical(payload))
                self.permits[permit.permit_id]=dict(record=permit,consumed=False,revoked=True)
            elif payload.get('format') == 'ra-w2-observation/1':
                event = decode('Observation', canonical(payload))
                self.observations[event.event_id] = event
                call = self.calls.get(event.key_digest)
                require(call is not None, 'OBSERVER_UNAVAILABLE')
                call['events'] += 1
                if event.kind == 'STREAM_CLOSED':
                    call['closed'] = True
                if event.kind in ('PERMIT_ISSUED', 'WRITE_ACCEPTED'):
                    call['marked'] = True
                if event.kind=='WRITE_ACCEPTED':
                    require(event.permit_id in self.permits,'OBSERVER_UNAVAILABLE')
                    self.permits[event.permit_id]['consumed']=True
        # Even a consistent previous OPEN is never restored as open authority.
        self.coverage_complete = self.coverage()

    @contextmanager
    def locked(self, timeout=3):
        require(type(timeout) in (int, float) and 0 < timeout <= 30, 'DEADLINE_EXCEEDED')
        require(self.available and self.A.acquire(timeout=timeout), 'OBSERVER_UNAVAILABLE')
        try:
            require(self.available, 'OBSERVER_UNAVAILABLE')
            yield
        finally:
            self.A.release()

    def now(self):
        wall = self.clock.utcnow()
        require(self.last_wall is None or wall >= self.last_wall, 'CONTEXT_CHANGED')
        self.last_wall = wall
        return wall

    def coverage(self):
        if self.journal.fd is None or self.witness.journal.fd is None:
            return False
        count = len(self.journal.entries)
        if (count, self.journal.head) != (self.witness.head_sequence, self.witness.head_digest):
            return False
        observed = {e.event_id for e in self.observations.values() if e.kind == 'WRITE_ACCEPTED'}
        logged={r['payload']['event_id']:r['payload'] for r in self.journal.entries
                if r['payload'].get('format')=='ra-w2-observation/1'}
        return (observed == self.witness.acceptance_ids and set(logged)==set(self.observations)
                and all(e.document()==logged[e.event_id] for e in self.observations.values()))

    def _append(self, record):
        try:
            require((len(self.journal.entries),self.journal.head)==
                (self.witness.head_sequence,self.witness.head_digest),'OBSERVER_UNAVAILABLE')
            self.hook('before_journal')
            row = self.journal.append(record.document())
            self.hook('after_fsync')
            self.witness.head(row)
            self.hook('before_ack')
            return row
        except BaseException:
            self.state = 'RECOVERY_REQUIRED'
            raise

    def _open(self):
        if any(self.now()>=call['expiry']+timedelta(days=90) for call in self.calls.values()):
            self.state='RECOVERY_REQUIRED'
        require(self.state == 'OPEN' and self.coverage(), 'OBSERVER_UNAVAILABLE')

    def qualify_admission(self,timeout=3):
        with self.locked(timeout):
            self._open()

    def suspend(self,timeout=3):
        # Parent-only response to uncertain persistence/acknowledgement. A new
        # explicit recovery must close old streams before reopening.
        with self.locked(timeout):
            self.state='RECOVERY_REQUIRED'

    def _restore_call(self, record):
        require(set(record) == {'format', 'key_digest', 'evidence_digest'}, 'OBSERVER_UNAVAILABLE')
        digest = record['key_digest']
        require(type(digest) is str and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest))
        path = self.journal.path.parent/('core_' + digest)
        require(not path.is_symlink(), 'OBSERVER_UNAVAILABLE')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            require(os.fstat(fd).st_size <= 32768, 'OBSERVER_UNAVAILABLE')
            raw = os.read(fd, 32769)
        finally:
            os.close(fd)
        require(sha256(raw).hexdigest() == record['evidence_digest'], 'OBSERVER_UNAVAILABLE')
        value = bounded_json(raw, maximum=32768, depth=8, nodes=4096)
        require(set(value) == {'key', 'core', 'run', 'admission', 'reservation', 'manifest_digest'}, 'OBSERVER_UNAVAILABLE')
        key, core, run, reservation = (decode(name, canonical(value[field])) for name,field in
            (('ReservationKey','key'),('BindingCore','core'),('RunContext','run'),('Reservation','reservation')))
        admission = decode('Admission', canonical(value['admission'])) if value['admission'] is not None else None
        require(key.fingerprint() == digest and core.fingerprint() == key.core_digest and core.scope == key.scope
            and key == reservation.key and run.scope == key.scope and run.call_ref == key.call_ref
            and run.owner_generation == reservation.owner_generation, 'CONFLICT')
        require(admission is None or (admission.key == key and admission.owner_generation == run.owner_generation), 'CONFLICT')
        self.core_evidence[digest] = value
        self.calls[digest] = dict(key=key, body_digest=core.body_digest, owner=run.owner_generation,
            expiry=min(utc(run.deadline_at), utc(core.expires_at)), admission=admission,
            closed=False, cancelled=False, marked=False, events=0, usage=usage_view(Usage()), terminal='UNKNOWN')

    def register_call(self, prepared, run, admission, reservation):
        # This control interface exists only in the parent, after verified DB
        # admission. A call-facing socket never exposes registration/issuance.
        key = prepared.key
        require(key.scope == run.scope and key.call_ref == run.call_ref and admission.key == key)
        require(admission.owner_generation == run.owner_generation)
        with self.locked():
            self._open()
            call = self.calls.get(key.fingerprint())
            require(call is not None and call['admission'] is None and not call['closed']
                and call['owner'] == run.owner_generation, 'CONFLICT')
            self._append(admission)
            call['admission'] = admission

    def register_reserved(self, prepared, run, reservation):
        key = prepared.key
        with self.locked():
            require(self.coverage() and reservation.key == key, 'OBSERVER_UNAVAILABLE')
            if key.fingerprint() in self.calls:
                require(self.core_evidence[key.fingerprint()]['reservation'] == reservation.document(), 'CONFLICT')
                return
            value = dict(key=key.document(), core=prepared.core.document(), run=run.document(),
                admission=None, reservation=reservation.document(), manifest_digest=prepared.manifest_digest)
            raw = canonical(value)
            require(len(raw) <= 32768, 'LIMIT_EXCEEDED')
            path = self.journal.path.parent/('core_'+key.fingerprint())
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                require(os.write(fd, raw) == len(raw), 'OBSERVER_UNAVAILABLE')
                os.fsync(fd)
            finally:
                os.close(fd)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            record = make('CallEvidence',key_digest=key.fingerprint(),evidence_digest=sha256(raw).hexdigest())
            self._append(record)
            self._restore_call(record.document())

    def script_final(self, key, usage, terminal='COMPLETED'):
        # Independently authored endpoint fixture; never derived from W1 result.
        require(terminal in ('COMPLETED', 'INCOMPLETE', 'FAILED', 'CANCELLED', 'UNKNOWN'))
        call = self.calls[key.fingerprint()]
        call['usage'], call['terminal'] = usage_view(usage), terminal

    def begin_acceptance_v1(self, run, admission, body_digest, timeout=3):
        with self.locked(timeout):
            self._open()
            key = admission.key
            call = self.calls.get(key.fingerprint())
            require(call is not None and call['owner'] == run.owner_generation and call['admission'] == admission
                    and body_digest == call['body_digest'] and not call['marked'] and not call['closed']
                    and not call['cancelled'] and self.now() < call['expiry'], 'CONTEXT_CHANGED')
            require(not any(t.key_digest == key.fingerprint() for t in self.tickets.values()), 'CONFLICT')
            ticket = make('AcceptanceTicket', ticket_id=uuid4().hex, key_digest=key.fingerprint(),
                deployment_ref=self.deployment, authority_epoch=self.epoch, acceptance_generation=self.generation,
                owner_generation=run.owner_generation, body_digest=body_digest, expires_at=stamp(call['expiry']))
            self.tickets[ticket.ticket_id] = ticket
            return ticket

    def issue(self, run, admission, ticket, marker_event_id, guard_epoch, timeout=3):
        with self.locked(timeout):
            self._open()
            call = self.calls[ticket.key_digest]
            require(self.tickets.get(ticket.ticket_id) == ticket and not call['marked']
                    and (ticket.authority_epoch, ticket.acceptance_generation) == (self.epoch, self.generation)
                    and ticket.owner_generation == run.owner_generation == call['owner']
                    and self.now() < utc(ticket.expires_at) and not call['closed'], 'CONTEXT_CHANGED')
            call['marked'] = True
            permit = make('SendPermit', key=call['key'], permit_id=uuid4().hex, admission_id=admission.admission_id,
                owner_generation=run.owner_generation, guard_epoch=guard_epoch, authority_epoch=self.epoch,
                acceptance_generation=self.generation, ticket_id=ticket.ticket_id, body_digest=ticket.body_digest,
                marker_event_id=marker_event_id, observer_epoch=self.observer_epoch, expires_at=ticket.expires_at)
            self.permits[permit.permit_id] = dict(record=permit, consumed=False, revoked=False)
            self._append(permit)
            self._event(call, permit, 'PERMIT_ISSUED')
            return permit

    def _event(self, call, permit, kind, usage=None, terminal='NONE', *, event_id=None):
        require(call['events'] < (64 if kind in ('STREAM_CLOSED', 'ZERO_PROVEN', 'CONFLICT', 'COVERAGE_GAP') else 56), 'LIMIT_EXCEEDED')
        event = make('Observation', key_digest=call['key'].fingerprint(), call_ref=call['key'].call_ref,
            event_id=event_id or uuid4().hex, observer_epoch=self.observer_epoch, authority_epoch=self.epoch,
            acceptance_generation=self.generation, sequence=len(self.journal.entries)+1,
            owner_generation=call['owner'], kind=kind, permit_id=permit.permit_id if permit else None,
            body_digest=call['body_digest'], observed_at=stamp(self.now()), terminal=terminal,
            usage=usage or usage_view(Usage()), evidence_digest=self.journal.head)
        self._append(event)
        self.observations[event.event_id] = event
        call['events'] += 1
        return event

    def consume_write_v1(self, permit, body, timeout=3):
        require(type(body) is bytes and len(body) <= 32768, 'LIMIT_EXCEEDED')
        with self.locked(timeout):
            self._open()
            stored = self.permits.get(permit.permit_id)
            call = self.calls.get(permit.key.fingerprint())
            require(stored is not None and stored['record'] == permit and call is not None, 'CONTEXT_CHANGED')
            require(not stored['consumed'] and not stored['revoked'] and not call['closed']
                and not call['cancelled'] and (permit.authority_epoch, permit.acceptance_generation) == (self.epoch, self.generation)
                and permit.owner_generation == call['owner'] and self.now() < min(call['expiry'], utc(permit.expires_at))
                and sha256(body).hexdigest() == permit.body_digest == call['body_digest'], 'CONTEXT_CHANGED')
            # Acceptance and closure share A. Count/body observations are outside
            # both result/accounting ledgers, and precede journal acknowledgement.
            stored['consumed'] = True
            eid = uuid4().hex
            self.endpoint_bodies[permit.key.fingerprint()] = body
            try:
                self.witness.accepted_once(permit.key.fingerprint(), eid, permit.body_digest)
                self.hook('after_endpoint_acceptance')
                event = self._event(call, permit, 'WRITE_ACCEPTED', event_id=eid)
                self.hook('after_acceptance_journal')
                if call['usage'].state == 'known':
                    self._event(call, permit, 'FINAL_USAGE', call['usage'], call['terminal'])
                return make('WriteAck', permit_id=permit.permit_id, event_id=event.event_id,
                            sequence=event.sequence, accepted_at=event.observed_at)
            except BaseException:
                self.state = 'RECOVERY_REQUIRED'
                raise

    def close_call(self, key, timeout=3):
        with self.locked(timeout):
            call = self.calls.get(key.fingerprint())
            require(call is not None and call['key'] == key, 'SOURCE_UNAVAILABLE')
            if call['closed']:
                return self.events(key)
            call['closed'] = True
            permits = [v for v in self.permits.values() if v['record'].key == key]
            for item in permits:
                item['revoked'] = True
            if not self.coverage():
                self.state='RECOVERY_REQUIRED'
                # Locally close the actual endpoint capability, but do not
                # append onto an unverified journal prefix or claim zero proof.
                return self.events(key)
            permit = permits[0]['record'] if permits else None
            self._event(call, permit, 'STREAM_CLOSED')
            # Missing journal callbacks cannot prove zero. Both independent
            # acceptance evidence and complete journal continuity are required.
            if self.coverage() and self.witness.accepted[key.fingerprint()] == 0:
                self._event(call, permit, 'ZERO_PROVEN', usage_view(ZERO_USAGE))
            return self.events(key)

    def late_usage(self, key, usage, terminal='COMPLETED', *, event_id=None):
        with self.locked():
            require(self.coverage() and self.witness.accepted[key.fingerprint()] == 1, 'OBSERVER_UNAVAILABLE')
            call = self.calls[key.fingerprint()]
            permit = next(v['record'] for v in self.permits.values() if v['record'].key == key)
            return self._event(call, permit, 'FINAL_USAGE', usage_view(usage), terminal,event_id=event_id)

    def events(self, key):
        return tuple(e for e in self.observations.values() if e.key_digest == key.fingerprint())

    def observe_v1(self, read, event, timeout=3):
        with self.locked(timeout):
            known=self.observations.get(event.event_id)
            require(known is not None and known==event and self.coverage(),'OBSERVER_UNAVAILABLE')
            call=self.calls.get(event.key_digest)
            require(call is not None and call['key'].scope==read.scope,'SOURCE_UNAVAILABLE')
            return make('ObservationAck',event_id=event.event_id,sequence=event.sequence,event_digest=event.fingerprint())

    def qualify_consumption(self,permit,timeout=3):
        with self.locked(timeout):
            self._open()
            require(permit is not None and (permit.authority_epoch,permit.acceptance_generation)==(self.epoch,self.generation),
                'CONTEXT_CHANGED')
            require(self.witness.accepted[permit.key.fingerprint()]==1,'OBSERVER_UNAVAILABLE')

    def invalidate_v1(self, ctx, request, timeout=3):
        require(request.deployment_ref == ctx.deployment_ref == self.deployment and request.writer_id == ctx.writer_id)
        with self.locked(timeout):
            require(self.now() < min(utc(ctx.deadline_at), utc(request.deadline_at)), 'DEADLINE_EXCEEDED')
            old = self.operations.get(request.operation_id)
            if old:
                if old.operation_digest != request.operation_digest:
                    self.state = 'RECOVERY_REQUIRED'
                    raise PortError('CONFLICT')
                return old
            self.state = 'CLOSED'
            if self.generation >= 2147483647 or sum(a.disposition in ('PENDING', 'UNKNOWN') for a in self.operations.values()) >= 64:
                self.state = 'RECOVERY_REQUIRED'
                raise PortError('LIMIT_EXCEEDED')
            self.generation += 1
            ack = make('InvalidationAck', operation_id=request.operation_id, operation_digest=request.operation_digest,
                deployment_ref=self.deployment, authority_epoch=self.epoch, acceptance_generation=self.generation,
                barrier_sequence=len(self.journal.entries)+1, state='CLOSED', disposition='PENDING', event_digest=self.journal.head)
            self._append(ack)
            self.operations[ack.operation_id] = ack
            return ack

    def lookup_invalidation_v1(self, ctx, operation_id, operation_digest, timeout=3):
        with self.locked(timeout):
            require(ctx.deployment_ref == self.deployment)
            ack = self.operations.get(operation_id)
            if ack is None:
                for row in self.journal.entries:
                    payload = row['payload']
                    if payload.get('format') == 'ra-w2-invalidation-ack/1' and payload.get('operation_id') == operation_id:
                        ack = decode('InvalidationAck', canonical(payload))
                if ack is not None:
                    self.operations[operation_id] = ack
            require(ack is not None and ack.operation_digest == operation_digest, 'COMMIT_UNKNOWN')
            return ack

    def resolve_invalidation_v1(self, ctx, resolution, verified_outcome, timeout=3):
        # verified_outcome is an independently queried transaction receipt from
        # the parent's DB controller, inaccessible on the child-facing endpoint.
        with self.locked(timeout):
            old = self.operations.get(resolution.operation_id)
            require(old is not None and old.operation_digest == resolution.operation_digest
                    and old.deployment_ref == resolution.deployment_ref == ctx.deployment_ref, 'CONFLICT')
            require(resolution.barrier_digest == old.event_digest, 'CONFLICT')
            require(verified_outcome == (resolution.database_outcome, resolution.transaction_evidence_ref), 'COMMIT_UNKNOWN')
            if old.disposition in ('COMMITTED', 'ROLLED_BACK'):
                require(old.disposition == resolution.database_outcome, 'CONFLICT')
                return old
            ack = make('InvalidationAck', **{**{k: v for k, v in old.items() if k != 'format'},
                                            'disposition': resolution.database_outcome})
            self._append(ack)
            self.operations[ack.operation_id] = ack
            return ack

    def reopen_v1(self, ctx, request, evidence, timeout=3):
        # Evidence is produced by explicit bounded parent recovery, never a
        # caller boolean or a child's assertion that an old stream is safe.
        with self.locked(timeout):
            require(self.state in ('CLOSED', 'RECOVERY_REQUIRED') and self.coverage(), 'OBSERVER_UNAVAILABLE')
            require(request.deployment_ref == ctx.deployment_ref == self.deployment
                and request.expected_authority_epoch == self.epoch and request.expected_acceptance_generation == self.generation,
                'CONTEXT_CHANGED')
            require(all(a.disposition in ('COMMITTED', 'ROLLED_BACK') for a in self.operations.values()), 'COMMIT_UNKNOWN')
            require(all(c['closed'] for c in self.calls.values()), 'OBSERVER_UNAVAILABLE')
            require(evidence.request == request and evidence.checked_at <= self.now() < evidence.expires_at,
                    'AUTHORITY_UNAVAILABLE')
            require(evidence.pending_digest == sha256(canonical(sorted((a.operation_id, a.operation_digest, a.disposition)
                    for a in self.operations.values()))).hexdigest(), 'COMMIT_UNKNOWN')
            ack = make('GateAck', deployment_ref=self.deployment, authority_epoch=self.epoch,
                acceptance_generation=self.generation, barrier_sequence=len(self.journal.entries)+1,
                state='OPEN', event_digest=self.journal.head)
            self._append(ack)
            self.state = 'OPEN'
            return ack
