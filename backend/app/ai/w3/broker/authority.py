"""B owns A, one native exchange, credentials and durable observations.

Only the owned synthetic peer factory constructs this implementation. It has no
Internet resolver/connector, operator credential loader, recovery enable flag,
or result/display authority. A restart is closed even with complete storage.
"""
from contextlib import contextmanager
from datetime import timedelta
from hashlib import sha256
import os
import re
import secrets
import socket
import threading
import time
from uuid import uuid4

from app.ai.proposals.adapter import Usage, project_usage
from app.ai.proposals.bindings import MODEL
from app.ai.proposals.codec import bounded_json, canonical
from app.ai.proposals.transport import ProviderTransport, request_bytes
from app.ai.w2.records import decode, make, require, stamp, utc, usage_view
from .journal import Observations
from .protocol import digest, identifier


def record(name, value):
    return decode(name, canonical(value))


class Broker:
    def __init__(self, deployment, stream, journal, witness, peer, wall, hook=lambda stage: None):
        self.deployment, self.stream = identifier(deployment), identifier(stream)
        self.J = Observations(journal, witness, stream, hook)
        self.peer, self.hook = peer, hook
        self.A = threading.Lock()
        self.epoch, self.observer_epoch = uuid4().hex, uuid4().hex
        self.generation, self.state = 1, 'RECOVERY_REQUIRED'
        self.wall, self.started = utc(wall), time.monotonic()
        self.binding = self.ticket = self.permit = None
        self.consumed, self.closed, self.read_started = False, False, False
        self.socket = self.request = None
        self.operations = {}
        for value in self.J.events:
            if value['kind'] == 'BINDING': self.binding = value['data']
            if value['kind'] == 'PERMIT': self.permit = record('SendPermit', value['data']['permit'])
            if value['kind'] == 'INTENT': self.consumed = True
            if value['kind'] == 'BARRIER':
                ack = record('InvalidationAck', value['data']['ack'])
                self.operations[ack.operation_id] = ack
                self.generation = max(self.generation, ack.acceptance_generation)
            if value['kind'] == 'DISPOSITION':
                ack = record('InvalidationAck', value['data']['ack'])
                self.operations[ack.operation_id] = ack
        # Recovery reconstructs evidence, never socket/permit/display authority.
        if self.J.events: self.closed = True

    def now(self):
        return self.wall + timedelta(seconds=time.monotonic() - self.started)

    @contextmanager
    def locked(self, timeout=3):
        require(type(timeout) in (float, int) and 0 < timeout <= 3, 'DEADLINE_EXCEEDED')
        require(self.A.acquire(timeout=timeout), 'OBSERVER_UNAVAILABLE')
        try: yield
        finally: self.A.release()

    def _open(self):
        require(self.state == 'OPEN' and self.J.coverage(), 'OBSERVER_UNAVAILABLE')

    def _append(self, event_id, kind, data):
        try:
            old = next((v for v in self.J.events if v['event_id'] == event_id), None)
            # Redelivery preserves the original time as well as its exact ID.
            observed_at = old['data']['observed_at'] if old else stamp(self.now())
            return self.J.append(event_id, kind, {**data, 'observed_at': observed_at})
        except BaseException:
            self.state = 'RECOVERY_REQUIRED'
            raise

    def _call(self):
        require(self.binding is not None, 'RESERVATION_UNAVAILABLE')
        return record('ReservationKey', self.binding['key'])

    def _current(self):
        self._open()
        require(not self.closed and self.now() < utc(self.binding['expires_at'])
                and time.monotonic() < self.until, 'DEADLINE_EXCEEDED')

    def bootstrap(self, data):
        require(set(data) == {'deployment', 'sql_empty'} and data['deployment'] == self.deployment
                and data['sql_empty'] == digest([]), 'CONTEXT_CHANGED')
        with self.locked():
            # Only an explicitly constructed empty synthetic fixture can start.
            # No restart or nonempty history has a reopen operation in this increment.
            require(not self.J.events and self.binding is None and self.J.coverage(), 'OBSERVER_UNAVAILABLE')
            self._append('bootstrap', 'BOOTSTRAP', dict(deployment=self.deployment,
                authority_epoch=self.epoch, measurement_kind='synthetic', sql_empty=data['sql_empty']))
            self.state = 'OPEN'
        return {}

    def bind(self, data):
        require(set(data) == {'key', 'core', 'run', 'reservation', 'manifest_digest'}, 'INVALID_RECORD')
        key, core, run, reservation = (record(name, data[field]) for name, field in
            (('ReservationKey', 'key'), ('BindingCore', 'core'), ('RunContext', 'run'), ('Reservation', 'reservation')))
        require(key.scope.deployment_ref == self.deployment and key.scope == core.scope == run.scope
                and key.call_ref == core.call_ref == run.call_ref and key.core_digest == core.fingerprint()
                and reservation.key == key and reservation.owner_generation == run.owner_generation
                and core.measurement_kind == reservation.measurement_kind == 'synthetic', 'CONTEXT_CHANGED')
        require(type(data['manifest_digest']) is str and len(data['manifest_digest']) == 64)
        expires = min(utc(core.expires_at), utc(run.deadline_at), utc(reservation.expires_at))
        value = {**data, 'expires_at': stamp(expires)}
        with self.locked():
            self._open()
            if self.binding is not None:
                require(self.binding == value, 'CONFLICT')
                return {}
            require(self.now() < expires and (expires - self.now()).total_seconds() <= 30, 'DEADLINE_EXCEEDED')
            self.until = time.monotonic() + (expires - self.now()).total_seconds()
            self.binding = value
            self._append('binding', 'BINDING', value)
            threading.Thread(target=self._expire, daemon=True).start()
        return {}

    def _expire(self):
        # B never depends on a surviving controller to close an orphan socket.
        # If A/fsync is stuck, this is unresolved, not a closure acknowledgement.
        time.sleep(max(0, self.until - time.monotonic()))
        try:
            with self.locked():
                self.state = 'CLOSED'
                self.closed = True
                self.ticket = None
                self._close_socket()
                if self.J.coverage():
                    self._append('closed', 'CLOSED', dict(key_digest=self._call().fingerprint()))
        except Exception:
            return

    def prepare(self, data):
        require(set(data) == {'body'}, 'INVALID_RECORD')
        body = data['body'].encode('utf-8') if type(data['body']) is str else b''
        require(0 < len(body) <= 32768 and sha256(body).hexdigest() == self.binding['core']['body_digest'],
                'CONTEXT_CHANGED')
        with self.locked():
            self._current()
            require(self.socket is None and self.request is None and self.permit is None, 'CONFLICT')
            # Mark preparation before leaving A: a second controller request
            # cannot start another TLS connection while the first is preparing.
            self.request = b''
        # No G, A or SQL transaction spans credential resolution or TLS setup.
        wire = None
        try:
            self.hook('before_secret')
            # This fixture credential is generated inside B. No path/reference,
            # environment secret or credential bytes arrive over controller IPC.
            path = self.J.journal.path.parent / 'synthetic-secret'
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                key = b'synthetic_' + secrets.token_hex(32).encode('ascii')
                require(os.write(fd, key) == len(key), 'OBSERVER_UNAVAILABLE')
            finally: os.close(fd)
            request = request_bytes(body, key)
            key = None
            wire = self.peer.connect(min(3, self.until - time.monotonic()))
            self.hook('after_tls')
            with self.locked():
                self._current()
                self.socket, self.request = wire, request
                wire = None
            return {}
        finally:
            if wire is not None: wire.close()

    def ticket_for(self, data):
        require(set(data) == {'run', 'admission', 'body_digest'})
        run, admission = record('RunContext', data['run']), record('Admission', data['admission'])
        with self.locked():
            self._current()
            key = self._call()
            require(run.document() == self.binding['run'] and admission.key == key
                    and admission.owner_generation == run.owner_generation
                    and data['body_digest'] == self.binding['core']['body_digest']
                    and self.ticket is None and self.permit is None and self.socket is not None, 'CONTEXT_CHANGED')
            self.admission = admission
            self.ticket = make('AcceptanceTicket', ticket_id=uuid4().hex, key_digest=key.fingerprint(),
                deployment_ref=self.deployment, authority_epoch=self.epoch, acceptance_generation=self.generation,
                owner_generation=run.owner_generation, body_digest=data['body_digest'], expires_at=self.binding['expires_at'])
            return self.ticket.document()

    def authorize(self, data):
        require(set(data) == {'format', 'run', 'admission', 'ticket', 'marker', 'guard_epoch'}
                and data['format'] == 'ra-broker-authorization/1', 'VERSION_UNSUPPORTED')
        run, admission, ticket = (record(n, data[f]) for n, f in
            (('RunContext', 'run'), ('Admission', 'admission'), ('AcceptanceTicket', 'ticket')))
        identifier(data['marker']); identifier(data['guard_epoch'])
        with self.locked():
            self._current()
            require(self.ticket == ticket and self.permit is None and self.admission == admission
                    and run.document() == self.binding['run']
                    and (ticket.authority_epoch, ticket.acceptance_generation) == (self.epoch, self.generation),
                    'CONTEXT_CHANGED')
            permit = make('SendPermit', key=self._call(), permit_id=uuid4().hex,
                admission_id=admission.admission_id, owner_generation=run.owner_generation,
                guard_epoch=data['guard_epoch'], authority_epoch=self.epoch, acceptance_generation=self.generation,
                ticket_id=ticket.ticket_id, body_digest=ticket.body_digest, marker_event_id=data['marker'],
                observer_epoch=self.observer_epoch, expires_at=ticket.expires_at)
            self._append('permit', 'PERMIT', dict(authorization=data, permit=permit.document()))
            self.permit = permit
            return permit.document()

    def write(self, data):
        require(set(data) == {'permit'})
        permit = record('SendPermit', data['permit'])
        self.hook('before_A')
        with self.locked():
            self._current()
            require(permit == self.permit and not self.consumed and self.socket is not None
                    and (permit.authority_epoch, permit.acceptance_generation) == (self.epoch, self.generation),
                    'CONTEXT_CHANGED')
            self.consumed = True
            self._append('intent', 'INTENT', dict(permit=permit.document(), binding_digest=digest(self.binding),
                measurement_kind='synthetic', liability_tokens=5120, liability_microusd=22528))
            self.hook('inside_A')
            count = None
            try:
                # No detached writer, buffered remainder, automatic reconnect
                # or takeover exists. Invalidation must acquire this same A.
                self._current()
                self.socket.settimeout(min(3, self.until - time.monotonic()))
                self.hook('before_write')
                self._current()
                request = self.request
                limit = self.peer.write_limit  # fixed owned fixture fault, never IPC input
                count = self.socket.send(request if limit is None else request[:limit])
                self.hook('after_write')
                require(time.monotonic() < self.until, 'DEADLINE_EXCEEDED')
            except BaseException:
                self._close_socket()
                self.state = 'RECOVERY_REQUIRED'
                raise
            finally:
                # Even a complete local write is neither provider acceptance
                # nor billable completion. A crash here leaves the durable intent.
                if self.J.coverage():
                    self._append('local_write', 'LOCAL_WRITE', dict(local_bytes=count,
                        requested_bytes=len(self.request), complete=count == len(self.request),
                        provider_acceptance='UNKNOWN', final_usage='UNKNOWN'))
                if count != len(self.request):
                    self._close_socket()
                    self.state = 'RECOVERY_REQUIRED'
            require(count == len(self.request), 'OBSERVER_UNAVAILABLE')
            self.request = None
            return dict(local_bytes=count, provider_acceptance='UNKNOWN', final_usage='UNKNOWN')

    def receive(self, data):
        require(data == {})
        with self.locked():
            require(self.consumed and not self.read_started and self.socket is not None, 'CONTEXT_CHANGED')
            self.read_started = True
            wire = self.socket
        # Response waiting deliberately owns neither A nor G. Late complete
        # usage may be observed after cancellation, without display authority.
        owner = self
        class Reader:
            def read(self, maximum, timeout):
                wire.settimeout(timeout)
                return wire.recv(maximum)
        class Boundary:
            def monotonic(self): return time.monotonic()
            def remaining(self, maximum):
                remaining = min(maximum, owner.until - time.monotonic())
                require(remaining > 0, 'DEADLINE_EXCEEDED')
                return remaining
            def check(self, *args, **kwargs): self.remaining(30)
        try:
            status, raw = ProviderTransport._response(Reader(), Boundary())
            envelope = bounded_json(raw, maximum=65536, depth=16, nodes=8192)
            require(envelope.get('object') == 'response' and envelope.get('model') == MODEL, 'CONTEXT_CHANGED')
            terminal = envelope.get('status')
            require(terminal in ('completed', 'incomplete', 'failed', 'cancelled'), 'INVALID_RECORD')
            usage = project_usage(envelope.get('usage'), True)
            response_id = envelope.get('id')
            # This optional troubleshooting field is not acceptance/usage
            # proof. Its shape cannot erase otherwise correlated terminal usage.
            if type(response_id) is not str or re.fullmatch('[A-Za-z0-9_-]{1,64}', response_id) is None:
                response_id = None
            evidence = dict(exchange_id=self.permit.permit_id, body_digest=self.permit.body_digest,
                status=status, response_id=response_id, terminal=terminal.upper(),
                measurement_kind='synthetic', response_digest=sha256(raw).hexdigest())
            with self.locked():
                self._append('peer_response', 'PEER_RESPONSE', evidence)
                if usage.state == 'known':
                    self._append('final_usage', 'FINAL_USAGE', dict(**evidence, usage=usage_view(usage).document()))
                self.hook('after_outcome')
            return dict(terminal=terminal.upper(), usage=usage_view(usage).document(), status=status)
        finally:
            with self.locked():
                self._close_socket()
                self.closed = True
                if self.J.coverage(): self._append('closed', 'CLOSED', dict(key_digest=self._call().fingerprint()))

    def _close_socket(self):
        if self.socket is not None:
            try: self.socket.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            self.socket.close(); self.socket = None

    def close_call(self, data):
        require(set(data) == {'key'} and record('ReservationKey', data['key']) == self._call(), 'CONTEXT_CHANGED')
        with self.locked():
            self._close_socket()
            self.closed = True
            if self.J.coverage(): self._append('closed', 'CLOSED', dict(key_digest=self._call().fingerprint()))
        return {}

    def invalidate(self, data):
        require(set(data) == {'context', 'request'})
        ctx, request = record('InvalidationContext', data['context']), record('InvalidationRequest', data['request'])
        require(ctx.deployment_ref == request.deployment_ref == self.deployment and ctx.writer_id == request.writer_id)
        until = time.monotonic() + 3
        with self.locked():
            old = self.operations.get(request.operation_id)
            if old is not None:
                prior = next(v['data']['request'] for v in self.J.events
                    if v['kind'] == 'BARRIER' and v['data']['ack']['operation_id'] == request.operation_id)
                if prior != request.document(): self.state = 'RECOVERY_REQUIRED'
                require(prior == request.document() and self.J.coverage(), 'CONFLICT')
                return old.document()
            self.state = 'CLOSED'
            self.generation += 1
            self.ticket = None
            self._close_socket()
            self.closed = self.binding is not None
            require(len(self.operations) < 64 and self.generation <= 2147483647, 'LIMIT_EXCEEDED')
            ack = make('InvalidationAck', operation_id=request.operation_id,
                operation_digest=request.operation_digest, deployment_ref=self.deployment,
                authority_epoch=self.epoch, acceptance_generation=self.generation,
                barrier_sequence=len(self.J.events) + 1, state='CLOSED', disposition='PENDING',
                event_digest=digest(request.document()))
            self._append('barrier_' + sha256(request.operation_id.encode()).hexdigest()[:48], 'BARRIER',
                         dict(ack=ack.document(), request=request.document()))
            self.operations[ack.operation_id] = ack
            require(time.monotonic() < until and self.now() < min(utc(ctx.deadline_at), utc(request.deadline_at)),
                    'DEADLINE_EXCEEDED')
            return ack.document()

    def disposition(self, data):
        require(set(data) == {'context', 'resolution', 'verified_outcome'})
        ctx, resolution = record('InvalidationContext', data['context']), record('InvalidationResolution', data['resolution'])
        with self.locked():
            old = self.operations.get(resolution.operation_id)
            require(old is not None and old.deployment_ref == ctx.deployment_ref == resolution.deployment_ref
                    and old.operation_digest == resolution.operation_digest
                    and old.event_digest == resolution.barrier_digest, 'CONFLICT')
            require(data['verified_outcome'] == [resolution.database_outcome, resolution.transaction_evidence_ref], 'COMMIT_UNKNOWN')
            if old.disposition in ('COMMITTED', 'ROLLED_BACK'):
                require(old.disposition == resolution.database_outcome and self.J.coverage(), 'CONFLICT')
                return old.document()
            ack = record('InvalidationAck', {**old.document(), 'disposition': resolution.database_outcome})
            self._append('disposition_' + digest(resolution.document())[:48], 'DISPOSITION',
                         dict(ack=ack.document(), resolution=resolution.document()))
            self.operations[ack.operation_id] = ack
            return ack.document()

    def snapshot(self, data):
        require(data == {} or set(data) == {'offset', 'head'})
        offset = data.get('offset', 0)
        require(type(offset) is int and 0 <= offset <= len(self.J.events))
        with self.locked():
            complete = self.J.coverage()
            if not complete: self.state = 'RECOVERY_REQUIRED'
            head = self.J.events[-1]['digest'] if self.J.events else '0'*64
            require(not data or data['head'] == head, 'OBSERVER_UNAVAILABLE')
            page, acks = [], []
            for index in range(offset, min(offset + 8, len(self.J.events))):
                proposed = [*page, self.J.events[index]]
                if len(canonical(proposed)) > 60000: break
                page = proposed
                if index < len(self.J.acks): acks.append(self.J.acks[index])
            require(offset == len(self.J.events) or page, 'LIMIT_EXCEEDED')
            return dict(format='ra-broker-snapshot/1', state=self.state, epoch=self.epoch,
                generation=self.generation, closed=self.closed, coverage_complete=complete,
                coverage_deficit=None if complete else 'J_W_ACK_COVERAGE_UNRESOLVED',
                liability_tokens=self.binding['reservation']['reserved_tokens'] if self.binding is not None else 0,
                liability_microusd=self.binding['reservation']['reserved_microusd'] if self.binding is not None else 0,
                unknown_upper_bound=not complete, events=page, acknowledgements=acks,
                head=head, total=len(self.J.events), offset=offset, next=offset + len(page))
